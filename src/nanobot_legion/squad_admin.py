#!/usr/bin/env python3
"""Squad Admin — Commander whitelist & user-agent mapping management.

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/commander
and /config/user-agent-map.
"""
from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .squad_config_loader import load_config, save_config

_COMMANDER_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commander 白名单</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #333; padding: 2rem; max-width: 640px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  .sub { color: #666; font-size: 0.875rem; margin-bottom: 1.5rem; }
  .card { background: #fff; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  ul { list-style: none; }
  ul li { display: flex; align-items: center; justify-content: space-between;
          padding: 0.5rem 0; border-bottom: 1px solid #eee; }
  ul li:last-child { border-bottom: none; }
  .tag { background: #e8f0fe; color: #1a73e8; border-radius: 4px; padding: 2px 8px;
         font-size: 0.85rem; font-family: monospace; }
  .btn { border: none; border-radius: 6px; padding: 0.4rem 0.9rem; cursor: pointer;
         font-size: 0.85rem; transition: background 0.15s; }
  .btn-danger { background: #fce8e6; color: #c5221f; }
  .btn-danger:hover { background: #f8c9c5; }
  .add-row { display: flex; gap: 0.5rem; margin-top: 1rem; }
  .add-row input { flex: 1; border: 1px solid #dadce0; border-radius: 6px;
                   padding: 0.45rem 0.6rem; font-size: 0.9rem; }
  .btn-primary { background: #1a73e8; color: #fff; }
  .btn-primary:hover { background: #1557b0; }
  .empty { color: #999; padding: 1rem 0; text-align: center; }
  .back { display: inline-block; margin-top: 1.5rem; color: #1a73e8; text-decoration: none;
          font-size: 0.9rem; }
  .back:hover { text-decoration: underline; }
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: 0.75rem 1.25rem;
           border-radius: 8px; color: #fff; font-size: 0.9rem; z-index: 999;
           opacity: 0; transition: opacity 0.3s; }
  .toast.ok { background: #1e8e3e; }
  .toast.err { background: #c5221f; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>🛡️ Commander 白名单</h1>
<p class="sub">白名单中的用户可通过 relay 向任意 agent 发送指令。</p>

<div class="card">
<ul id="list">{list_html}</ul>
</div>

<div class="add-row">
  <input id="new-user" placeholder="输入用户名（如 GitHub 用户名）" autocomplete="off">
  <button class="btn btn-primary" onclick="add()">➕ 添加</button>
</div>

<a class="back" href="javascript:history.back()">← 返回</a>

<div id="toast" class="toast"></div>

<script>
async function add() {
  const input = document.getElementById('new-user');
  const name = input.value.trim();
  if (!name) return;
  const resp = await fetch(location.pathname + '/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user: name})
  });
  const data = await resp.json();
  toast(data.ok ? `✅ 已添加 ${name}` : `❌ ${data.error}`, data.ok);
  if (data.ok) { input.value = ''; location.reload(); }
}

async function remove(name) {
  if (!confirm(`移除 ${name}？`)) return;
  const resp = await fetch(location.pathname + '/remove', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user: name})
  });
  const data = await resp.json();
  toast(data.ok ? `✅ 已移除 ${name}` : `❌ ${data.error}`, data.ok);
  if (data.ok) location.reload();
}

function toast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + (ok ? 'ok' : 'err') + ' show';
  setTimeout(() => t.classList.remove('show'), 2500);
}
</script>
</body>
</html>"""

_USER_AGENT_MAP_HTML = """\
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>用户-Agent 映射</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #f5f5f5; color: #333; padding: 2rem; max-width: 640px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
  .sub { color: #666; font-size: 0.875rem; margin-bottom: 1.5rem; }
  .card { background: #fff; border-radius: 8px; padding: 1.25rem; margin-bottom: 1rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.6rem 0.75rem; border-bottom: 1px solid #eee; }
  th { font-size: 0.8rem; color: #666; text-transform: uppercase; }
  .tag { background: #e8f0fe; color: #1a73e8; border-radius: 4px; padding: 2px 8px;
         font-size: 0.85rem; font-family: monospace; }
  .map-input { border: 1px solid #dadce0; border-radius: 6px; padding: 0.4rem 0.6rem;
               font-size: 0.9rem; width: 100%; }
  .map-input:focus { outline: none; border-color: #1a73e8; box-shadow: 0 0 0 2px rgba(26,115,232,0.15); }
  .btn { border: none; border-radius: 6px; padding: 0.45rem 1rem; cursor: pointer;
         font-size: 0.85rem; transition: background 0.15s; }
  .btn-primary { background: #1a73e8; color: #fff; }
  .btn-primary:hover { background: #1557b0; }
  .empty { color: #999; padding: 1rem 0; text-align: center; }
  .back { display: inline-block; margin-top: 1.5rem; color: #1a73e8; text-decoration: none;
          font-size: 0.9rem; }
  .back:hover { text-decoration: underline; }
  .note { font-size: 0.8rem; color: #888; margin-top: 0.75rem; }
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; padding: 0.75rem 1.25rem;
           border-radius: 8px; color: #fff; font-size: 0.9rem; z-index: 999;
           opacity: 0; transition: opacity 0.3s; }
  .toast.ok { background: #1e8e3e; }
  .toast.err { background: #c5221f; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>🔗 用户-Agent 映射</h1>
<p class="sub">将 OAuth 用户绑定到对应的 Worker Agent。未映射的用户自动使用 {webui_agent}。</p>

<div class="card">
<table>
<thead><tr><th>Agent</th><th>OAuth 用户名</th></tr></thead>
<tbody id="rows">{rows_html}</tbody>
</table>
<p class="note">每个用户名绑定一个 Agent。留空即解除绑定。</p>
</div>

<div style="text-align:right; margin-bottom:1rem;">
  <button class="btn btn-primary" onclick="save()">💾 保存</button>
</div>

<a class="back" href="javascript:history.back()">← 返回</a>

<div id="toast" class="toast"></div>

<script>
async function save() {
  const inputs = document.querySelectorAll('.map-input');
  const mapping = {};
  for (const inp of inputs) {
    const agent = inp.dataset.agent;
    const user = inp.value.trim();
    if (user) mapping[user] = agent;
  }
  const resp = await fetch(location.pathname + '/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mapping})
  });
  const data = await resp.json();
  toast(data.ok ? '✅ 已保存' : '❌ ' + data.error, data.ok);
  if (data.ok) setTimeout(() => location.reload(), 800);
}

function toast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + (ok ? 'ok' : 'err') + ' show';
  setTimeout(() => t.classList.remove('show'), 2500);
}
</script>
</body>
</html>"""


def create_squad_admin_routes(app, gatekeeper):
    """Mount commander whitelist & user-agent-map routes on gatekeeper's app."""

    # ── Commander whitelist ──────────────────────────────────────────

    async def _commander_page(request: Request):
        _su = request.session.get("user")
        if not _su:
            return RedirectResponse("/")
        if not gatekeeper._platform.is_commander(_su):
            return HTMLResponse(_DENIED, status_code=403)
        cfg = load_config()
        whitelist = cfg.get("commander_whitelist", [])
        if whitelist:
            items = "".join(
                f'<li><span class="tag">{_esc(u)}</span> '
                f'<button class="btn btn-danger" onclick="remove(&#39;{_esc_js(u)}&#39;)">✕ 移除</button></li>'
                for u in whitelist
            )
        else:
            items = '<li class="empty">尚无白名单条目</li>'
        return HTMLResponse(_COMMANDER_HTML.replace("{list_html}", items))

    async def _commander_add(request: Request):
        _su = request.session.get("user")
        if not _su:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_su):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效请求"}, status_code=400)
        user = body.get("user", "").strip()
        if not user:
            return JSONResponse({"ok": False, "error": "用户名不能为空"}, status_code=400)

        cfg = load_config(force_reload=True)
        whitelist = cfg.setdefault("commander_whitelist", [])
        if user in whitelist:
            return JSONResponse({"ok": False, "error": f"'{user}' 已在白名单中"}, status_code=409)
        whitelist.append(user)
        save_config(cfg)
        gatekeeper._platform._commander_whitelist = whitelist
        return JSONResponse({"ok": True})

    async def _commander_remove(request: Request):
        _su = request.session.get("user")
        if not _su:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_su):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效请求"}, status_code=400)
        user = body.get("user", "").strip()
        if not user:
            return JSONResponse({"ok": False, "error": "用户名不能为空"}, status_code=400)

        cfg = load_config(force_reload=True)
        whitelist = cfg.get("commander_whitelist", [])
        if user not in whitelist:
            return JSONResponse({"ok": False, "error": f"'{user}' 不在白名单中"}, status_code=404)
        whitelist.remove(user)
        save_config(cfg)
        gatekeeper._platform._commander_whitelist = whitelist
        return JSONResponse({"ok": True})

    # ── User-agent mapping ───────────────────────────────────────────

    async def _user_agent_map_page(request: Request):
        _su = request.session.get("user")
        if not _su:
            return RedirectResponse("/")
        if not gatekeeper._platform.is_commander(_su):
            return HTMLResponse(_DENIED, status_code=403)
        cfg = load_config()
        peers = cfg.get("peers", {})
        webui_agent = cfg.get("webui_agent", "neo")
        mapping = cfg.get("user_agent_map", {})
        # Invert: {agent_name: [username, ...]}
        agent_to_users = {}
        for user, agent in mapping.items():
            agent_to_users.setdefault(agent.lower() if isinstance(agent, str) else agent, []).append(user)
        worker_agents = sorted([name for name in peers if name != webui_agent])
        if worker_agents:
            rows = "".join(
                f'<tr><td><span class="tag">{_esc(a)}</span></td>'
                f'<td><input class="map-input" data-agent="{_esc(a)}"'
                f' value="{_esc(", ".join(agent_to_users.get(a, [])))}"'
                f' placeholder="（留空则不绑定）"></td></tr>'
                for a in worker_agents
            )
        else:
            rows = '<tr><td colspan="2" class="empty">尚无 Worker Agent（请先通过 Agent 管理添加）</td></tr>'
        html = _USER_AGENT_MAP_HTML.replace("{rows_html}", rows).replace("{webui_agent}", _esc(webui_agent))
        return HTMLResponse(html)

    async def _user_agent_map_save(request: Request):
        _su = request.session.get("user")
        if not _su:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_su):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效请求"}, status_code=400)
        new_mapping = body.get("mapping", {})
        if not isinstance(new_mapping, dict):
            return JSONResponse({"ok": False, "error": "mapping 须为对象"}, status_code=400)
        clean = {}
        for user, agent in new_mapping.items():
            user = str(user).strip()
            agent = str(agent).strip()
            if user and agent:
                clean[user] = agent
        cfg = load_config(force_reload=True)
        cfg["user_agent_map"] = clean
        save_config(cfg)
        mapping = cfg["user_agent_map"]
        if hasattr(gatekeeper._platform, '_user_agent_map'):
            gatekeeper._platform._user_agent_map = mapping
        return JSONResponse({"ok": True})

    # ── Register routes ──────────────────────────────────────────────

    app.add_route("/config/commander", _commander_page, methods=["GET"])
    app.add_route("/config/commander/add", _commander_add, methods=["POST"])
    app.add_route("/config/commander/remove", _commander_remove, methods=["POST"])
    app.add_route("/config/user-agent-map", _user_agent_map_page, methods=["GET"])
    app.add_route("/config/user-agent-map/save", _user_agent_map_save, methods=["POST"])


def _esc(s: str) -> str:
    """HTML-escape a string."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _esc_js(s: str) -> str:
    """Escape a string for embedding in JS single-quoted string (via HTML entity)."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 可访问此管理页面</h3>"
