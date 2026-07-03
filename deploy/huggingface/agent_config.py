#!/usr/bin/env python3
"""Agent Management — 配置中心：Worker Agent 增删。

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/agents.
"""
from __future__ import annotations

import json, os
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from squad_config_loader import load_config, _get_config_path

_AGENT_CONFIG_TEMPLATE = """\
{
  "agents": {
    "defaults": {
      "provider": "$PROVIDER$",
      "model": "$MODEL$",
      "instructions": $INSTRUCTIONS$
    }
  },
  "providers": $PROVIDERS$,
  "gateway": {"host": "127.0.0.1", "port": $GW_PORT$},
  "channels": {"websocket": {"enabled": true, "port": $WS_PORT$}},
  "tools": {
    "exec": {
      "allowed_env_keys": ["PATH", "HOME", "USER", "LANG", "PYTHONPATH", "VIRTUAL_ENV", "NANOBOT_ACCOUNT_BASE"],
      "restrict_to_workspace": true,
      "deny_patterns": ["git push", "git commit", "git rebase", "gh pr create", "gh pr merge"]
    }
  }
}
"""

# ── HTML ───────────────────────────────────────────────────

_HTML = r"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 管理 — 系统配置</title>
<style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family: -apple-system, sans-serif; background:#f5f5f5; color:#333; padding:20px; max-width:720px; margin:0 auto; }
  h1 { font-size:1.4em; margin-bottom:16px; }
  h2 { font-size:1.2em; margin:24px 0 12px; }
  table { width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; }
  th,td { padding:10px 14px; text-align:left; border-bottom:1px solid #eee; font-size:.92em; }
  th { background:#f0f0f0; font-weight:600; }
  .tag { display:inline-block; padding:2px 8px; border-radius:4px; font-size:.78em; font-weight:600; }
  .tag-cmd { background:#e3f2fd; color:#1565c0; }
  .tag-work { background:#fff3e0; color:#e65100; }
  .empty { color:#999; text-align:center; padding:24px; }
  .form-group { margin-bottom:14px; }
  label { display:block; margin-bottom:4px; font-weight:600; font-size:.9em; }
  input,select,textarea { width:100%; padding:10px 12px; border:1px solid #ddd; border-radius:6px; font-size:.92em; }
  textarea { resize:vertical; min-height:60px; }
  button { padding:10px 24px; background:#1565c0; color:#fff; border:none; border-radius:6px; font-size:.95em; cursor:pointer; }
  button:hover { background:#0d47a1; }
  .note { background:#fff3cd; border:1px solid #ffc107; border-radius:6px; padding:12px; margin:16px 0; font-size:.88em; }
  .err { background:#fce4ec; border:1px solid #ef9a9a; color:#c62828; border-radius:6px; padding:12px; margin:16px 0; font-size:.88em; }
  .ok { background:#e8f5e9; border:1px solid #a5d6a7; color:#2e7d32; border-radius:6px; padding:12px; margin:16px 0; font-size:.88em; }
  .del-btn { background:none; border:1px solid #ccc; color:#999; padding:4px 10px; font-size:.8em; cursor:pointer; border-radius:4px; margin-left:8px; }
  .del-btn:hover { border-color:#e53935; color:#e53935; }
</style>
</head>
<body>
<h1>🤖 Agent 管理</h1>

<p style="margin-bottom:16px;color:#666;font-size:.9em">
  当前 Legion 编制。Commander (neo) 由初始化配置生成，不可编辑。<br>
  Worker agent 通过下方表单添加，重启后生效。
</p>

<h2>📋 当前 Agent 列表</h2>
{agent_table}

<h2>➕ 添加 Worker Agent</h2>
<form id="addForm">
  <div class="form-group">
    <label for="name">名字</label>
    <input id="name" name="name" type="text" placeholder="例如: trinity" pattern="[a-z][a-z0-9_]*" required>
    <small style="color:#999">英文小写字母开头，可用数字和下划线</small>
  </div>
  <div class="form-group">
    <label for="role">角色说明</label>
    <textarea id="role" name="role" placeholder="例如: 写代码、提交 PR、审查代码" required></textarea>
    <small style="color:#999">描述这个 agent 的职责，将写入 AGENTS.md 作为系统提示词</small>
  </div>
  <div class="form-group">
    <label for="model">模型</label>
    <select id="model" name="model" required>
{model_options}
    </select>
  </div>
  <button type="submit">添加</button>
  <div id="result"></div>
</form>

<div class="note">
  ⚠️ <strong>添加后需重启空间</strong>（停止 → 启动）使新 agent 生效。<br>
  新 agent 仅通过内部 relay 通信，不绑定对外频道。
</div>

<script>
document.getElementById('addForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const result = document.getElementById('result');
  result.innerHTML = '';
  const formData = {
    name: document.getElementById('name').value.trim(),
    role: document.getElementById('role').value.trim(),
    model: document.getElementById('model').value
  };
  try {
    const resp = await fetch('/config/agents/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(formData)
    });
    const data = await resp.json();
    if (data.ok) {
      result.innerHTML = '<div class="ok">✅ ' + data.msg + ' — 请重启空间生效</div>';
      // Refresh agent list after short delay
      setTimeout(() => { window.location.reload(); }, 1500);
    } else {
      result.innerHTML = '<div class="err">❌ ' + data.error + '</div>';
    }
  } catch(err) {
    result.innerHTML = '<div class="err">❌ 提交失败: ' + err.message + '</div>';
  }
});
</script>
</body>
</html>
"""

# ── Logic ──────────────────────────────────────────────────

def _parse_providers(neo_config: dict) -> tuple[str, str, list[dict]]:
    """Extract default provider_id and model from neo config, plus all model options."""
    agents = neo_config.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    provider_id = defaults.get("provider", "")
    default_model = defaults.get("model", "")
    providers = neo_config.get("providers", {})
    if isinstance(providers, dict):
        prov = providers.get(provider_id, {})
    else:
        prov = {}
    models = prov.get("models", []) if isinstance(prov, dict) else []
    if not models:
        models = [default_model] if default_model else ["gpt-4o"]
    options = []
    for m in models:
        if isinstance(m, str):
            options.append({"id": provider_id, "model": m, "label": f"{provider_id} / {m}"})
        elif isinstance(m, dict):
            mid = m.get("id", m.get("model", str(m)))
            options.append({"id": provider_id, "model": mid, "label": f"{provider_id} / {mid}"})
    return provider_id, default_model, options


def _render_agent_table(squad_config: dict, neo_config: dict) -> str:
    """Render HTML table of current agents."""
    peers = squad_config.get("peers", {})
    if not peers:
        return '<p class="empty">暂无 agent（异常状态）</p>'
    
    rows = []
    for name, info in sorted(peers.items()):
        gw = info.get("gateway_port", "?")
        ws = info.get("ws_port", "?")
        is_cmd = info.get("id") == "squad:commander"
        tag = '<span class="tag tag-cmd">Commander</span>' if is_cmd else '<span class="tag tag-work">Worker</span>'
        rows.append(f"<tr><td>{name} {tag}</td><td>{gw}/{ws}</td></tr>")
    
    return ("<table><tr><th>Agent</th><th>端口 (gw/ws)</th></tr>"
            + "\n".join(rows) + "</table>")


def _render_model_options(options: list[dict]) -> str:
    """Render <option> tags for model dropdown."""
    if not options:
        return '<option value="">(未找到可用模型)</option>'
    lines = []
    for i, opt in enumerate(options):
        selected = ' selected' if i == 0 else ''
        lines.append(f'        <option value="{opt["id"]}:{opt["model"]}"{selected}>{opt["label"]}</option>')
    return "\n".join(lines)


def _allocate_ports(peers: dict) -> tuple[int, int]:
    """Find next available gateway_port and ws_port.
    
    Returns (gw, ws) where both are > max existing ports and ws > gw.
    """
    all_ports = set()
    for info in peers.values():
        gp = info.get("gateway_port", 0)
        wp = info.get("ws_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            all_ports.add(int(gp))
        if isinstance(wp, (int, float)) and wp > 0:
            all_ports.add(int(wp))
    
    if not all_ports:
        return 18795, 18895  # Default for first worker after neo
    
    max_port = max(all_ports)
    gw = max_port + 1
    ws = gw + 1
    return gw, ws


# ── Route Handlers ─────────────────────────────────────────

def create_agent_routes(app, gatekeeper):
    """Mount agent management routes on gatekeeper's FastAPI app."""

    async def _agent_page(request: Request):
        """GET /config/agents — show agent list + add form."""
        _user = request.session.get("user")
        if not _user:
            from starlette.responses import RedirectResponse
            return RedirectResponse("/")
        
        squad_cfg = load_config(force_reload=True)
        
        # Read neo's config to get provider/model info
        data_root = squad_cfg.get("data_root", "/data")
        neo_config_path = os.path.join(data_root, "instances", "neo", "config.json")
        neo_cfg = {}
        try:
            with open(neo_config_path) as f:
                neo_cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        
        agent_table = _render_agent_table(squad_cfg, neo_cfg)
        _, _, options = _parse_providers(neo_cfg)
        model_options = _render_model_options(options)
        
        html = _HTML.replace("{agent_table}", agent_table).replace("{model_options}", model_options)
        return HTMLResponse(html)

    async def _add_agent(request: Request):
        """POST /config/agents/add — create a new worker agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)
        
        name = (body.get("name", "") or "").strip().lower()
        role = (body.get("role", "") or "").strip()
        model_sel = (body.get("model", "") or "").strip()
        
        # Validate
        if not name:
            return JSONResponse({"ok": False, "error": "名字不能为空"}, status_code=400)
        if not name[0].isalpha():
            return JSONResponse({"ok": False, "error": "名字必须以字母开头"}, status_code=400)
        if not all(c.isalnum() or c in '_-' for c in name):
            return JSONResponse({"ok": False, "error": "名字只能包含字母、数字、下划线和连字符"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "neo 是保留的 Commander 名字，不可用作 Worker"}, status_code=400)
        if not role:
            return JSONResponse({"ok": False, "error": "角色说明不能为空"}, status_code=400)
        
        # Parse model selection: "provider_id:model_id"
        provider_id = ""
        model_id = ""
        if ":" in model_sel:
            provider_id, model_id = model_sel.split(":", 1)
        else:
            model_id = model_sel
        
        # Load current squad config
        squad_cfg = load_config(force_reload=True)
        peers = squad_cfg.get("peers", {})
        
        if name in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已存在"}, status_code=409)
        
        # Read neo config for providers
        data_root = squad_cfg.get("data_root", "/data")
        neo_config_path = os.path.join(data_root, "instances", "neo", "config.json")
        neo_cfg = {}
        try:
            with open(neo_config_path) as f:
                neo_cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return JSONResponse({"ok": False, "error": "无法读取 neo 配置，请确保 Commander 已初始化"}, status_code=500)
        
        providers = neo_cfg.get("providers", {})
        if not provider_id and providers:
            provider_id = next(iter(providers.keys()))
        if not model_id and providers:
            prov = providers.get(provider_id, {})
            models = prov.get("models", []) if isinstance(prov, dict) else []
            model_id = models[0] if models else "gpt-4o"
        
        # Allocate ports
        gw, ws = _allocate_ports(peers)
        
        # Generate agent config (use str.replace to avoid .format() brace conflicts)
        instructions_json = json.dumps(role, ensure_ascii=False)
        providers_str = json.dumps(providers, ensure_ascii=False, indent=2)
        agent_config_json = (_AGENT_CONFIG_TEMPLATE
            .replace("$PROVIDER$", provider_id)
            .replace("$MODEL$", model_id)
            .replace("$INSTRUCTIONS$", instructions_json)
            .replace("$PROVIDERS$", providers_str)
            .replace("$GW_PORT$", str(gw))
            .replace("$WS_PORT$", str(ws))
        )
        
        # Write agent config
        agent_dir = os.path.join(data_root, "instances", name)
        os.makedirs(agent_dir, exist_ok=True)
        config_path = os.path.join(agent_dir, "config.json")
        try:
            with open(config_path, "w") as f:
                f.write(agent_config_json)
            os.chmod(config_path, 0o600)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"无法写入配置: {e}"}, status_code=500)
        
        # Write AGENTS.md
        agents_md = f"# {name}\n\n{role}\n"
        try:
            with open(os.path.join(agent_dir, "AGENTS.md"), "w") as f:
                f.write(agents_md)
        except OSError:
            pass
        
        # Write MEMORY.md placeholder
        try:
            memory_dir = os.path.join(agent_dir, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            with open(os.path.join(memory_dir, "MEMORY.md"), "w") as f:
                f.write(f"# {name} Memory\n\n*Work in progress.*\n")
        except OSError:
            pass
        
        # Update squad_config.json (persistent copy)
        peers[name] = {"id": f"squad:{name}", "gateway_port": gw, "ws_port": ws}
        squad_cfg["peers"] = peers
        
        config_path = _get_config_path()
        try:
            with open(config_path, "w") as f:
                json.dump(squad_cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            # Config written, but squad_config update failed — partial success
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 配置已创建 (端口 {gw}/{ws})，但 squad_config 更新失败: {e}。请手动添加 peer 或重启后用 /reset-setup 重建。"},
                status_code=201
            )
        
        return JSONResponse(
            {"ok": True, "msg": f"Agent '{name}' 已添加 (网关: {gw}, WS: {ws})，重启空间后生效"},
            status_code=201
        )

    # Mount routes
    app.get("/config/agents")(_agent_page)
    app.post("/config/agents/add")(_add_agent)
