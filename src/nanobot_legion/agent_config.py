#!/usr/bin/env python3
"""Agent Management — 配置中心：Worker Agent 增删。

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/agents.
Provider 列表来自 nanobot 官方 ``providers/registry.py``，自动跟随上游更新。
"""
from __future__ import annotations

import datetime, json, os, shutil
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .squad_config_loader import load_config, _get_config_path

# ── provider registry (from official nanobot) ───────────────────────
try:
    from nanobot.providers.registry import PROVIDERS as _NANOBOT_PROVIDERS, find_by_name
except ImportError:  # pragma: no cover
    _NANOBOT_PROVIDERS = ()
    def find_by_name(name: str):  # noqa: E302
        return None

# ── UX augmentation ──────────────────────────────────────────────────
_PROVIDER_MODELS: dict[str, list[str]] = {
    "deepseek":    ["deepseek-chat", "deepseek-reasoner"],
    "openai":      ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"],
    "siliconflow": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen3-235B-A22B"],
    "zhipu":       ["glm-4-plus", "glm-4-flash", "glm-4-air"],
    "dashscope":   ["qwen3-235b-a22b", "qwen-max", "qwen-plus"],
    "moonshot":    ["kimi-k2.5", "kimi-k2.6"],
    "gemini":      ["gemini-2.5-flash", "gemini-2.5-pro"],
    "mistral":     ["mistral-large-latest", "mistral-small-latest"],
    "anthropic":   ["claude-sonnet-4-20250514", "claude-haiku-3.5"],
    "volcengine":  ["deepseek-v3-250324", "deepseek-r1-250528"],
    "stepfun":     ["step-3"],
    "minimax":     ["minimax-m1"],
    "qianfan":     ["ernie-4.5-8k", "ernie-speed-8k"],
    "novita":      ["deepseek-r1", "deepseek-v3"],
    "openrouter":  ["openai/gpt-4o-mini"],
    "aihubmix":    ["deepseek-chat"],
    "groq":        ["llama-3.3-70b-versatile"],
    "huggingface": ["Qwen/Qwen3-235B-A22B"],
}

_SKIP_PROVIDERS = frozenset({"bedrock", "azure_openai", "ovms", "nvidia",
                               "openai_codex", "github_copilot",
                               "minimax_anthropic",
                               "volcengine_coding_plan", "byteplus_coding_plan"})


def _get_setup_providers() -> list:
    """Return nanobot ProviderSpec list filtered for the agent config form."""
    result = []
    for spec in _NANOBOT_PROVIDERS:
        if spec.is_oauth or spec.is_local:
            continue
        if spec.name in _SKIP_PROVIDERS:
            continue
        result.append(spec)
    return result


def _build_provider_js_data() -> tuple[str, str]:
    """Generate provider <option> HTML and JS presets object.

    Returns (provider_options_html, presets_js).
    """
    select_lines = ['            <option value="">— 选择服务商 —</option>']
    p_entries = []

    for spec in _get_setup_providers():
        select_lines.append(f'            <option value="{spec.name}">{spec.label}</option>')

        models = _PROVIDER_MODELS.get(spec.name, [])
        base = spec.default_api_base or ""
        p_entries.append(
            f'    {spec.name}:{{base:"{base}",ml:{json.dumps(models)}}}'
        )

    # custom provider
    select_lines.append('            <option value="custom">自定义 (OpenAI 兼容)</option>')
    p_entries.append('    custom:{base:"",ml:[]}')

    options_html = "\n".join(select_lines)
    presets_js = "var __PP = {\n" + ",\n".join(p_entries) + "\n};"

    return options_html, presets_js

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
    <label for="provider">模型服务商 / Provider</label>
    <select id="provider" name="provider" required>
{provider_options}
    </select>
    <small style="color:#999">选择 API 服务商，不同 agent 可使用不同 provider</small>
  </div>
  <div class="form-group">
    <label for="model">模型</label>
    <select id="model" name="model" required>
      <option value="">— 先选择服务商 —</option>
    </select>
    <small style="color:#999">也可手动输入模型名（选择「自定义」后在此输入）</small>
  </div>
  <div class="form-group">
    <label for="api_key">API Key</label>
    <input id="api_key" name="api_key" type="password" placeholder="为此 agent 单独设置 API Key（留空则继承 Commander 的 key）">
    <small style="color:#999">留空时 worker agent 使用 Commander (neo) 的 API Key</small>
  </div>
  <button type="submit">添加</button>
  <div id="result"></div>
</form>

<div class="note">
  ⚠️ <strong>添加后需重启空间</strong>（停止 → 启动）使新 agent 生效。<br>
  新 agent 仅通过内部 relay 通信，不绑定对外频道。
</div>

<script>
// -- provider presets (generated from nanobot official registry) --
{provider_presets_js}

var elProvider = document.getElementById('provider');
var elModel = document.getElementById('model');

// -- pre-fill from Commander config --
var __neo_provider = '{neo_provider_id}';
var __neo_api_key = '{neo_api_key}';

// Populate model options when provider changes
elProvider.addEventListener('change', function() {
  var p = __PP[this.value];
  var opts = ['<option value="">— 选择模型 —</option>'];
  if (p) {
    var models = p.ml || [];
    for (var i = 0; i < models.length; i++) {
      opts.push('<option value="' + models[i] + '">' + models[i] + '</option>');
    }
  }
  elModel.innerHTML = opts.join('');

  // Pre-fill api_key if same as neo's provider
  var ak = document.getElementById('api_key');
  if (this.value === __neo_provider && !ak.value) {
    ak.value = __neo_api_key;
    ak.placeholder = '(继承 Commander 的 API Key)';
  } else if (this.value && this.value !== __neo_provider) {
    ak.placeholder = '为此 agent 单独设置 API Key';
  }
});

// On page load: select neo's provider by default
(function init() {
  if (__neo_provider) {
    elProvider.value = __neo_provider;
    elProvider.dispatchEvent(new Event('change'));
  }
})();

// Form submit
document.getElementById('addForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const result = document.getElementById('result');
  result.innerHTML = '';
  const formData = {
    name: document.getElementById('name').value.trim(),
    role: document.getElementById('role').value.trim(),
    provider: document.getElementById('provider').value,
    model: document.getElementById('model').value,
    api_key: document.getElementById('api_key').value.trim()
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

async function removeAgent(name) {
  if (!confirm('确定要删除 Agent "' + name + '" 吗？\n\n其配置目录将被归档，重启空间后生效。')) return;
  const result = document.getElementById('result');
  result.innerHTML = '';
  try {
    const resp = await fetch('/config/agents/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    const data = await resp.json();
    if (data.ok) {
      result.innerHTML = '<div class="ok">✅ ' + data.msg + '</div>';
      setTimeout(() => { window.location.reload(); }, 1500);
    } else {
      result.innerHTML = '<div class="err">❌ ' + data.error + '</div>';
    }
  } catch(err) {
    result.innerHTML = '<div class="err">❌ 提交失败: ' + err.message + '</div>';
  }
}
</script>
</body>
</html>
"""

# ── Logic ──────────────────────────────────────────────────

def _get_neo_config(squad_cfg: dict) -> dict:
    """Load neo's config.json. Returns empty dict on failure."""
    data_root = squad_cfg.get("data_root", "/data")
    neo_config_path = os.path.join(data_root, "instances", "neo", "config.json")
    try:
        with open(neo_config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _get_neo_provider_info(neo_cfg: dict) -> tuple[str, str, str]:
    """Extract (provider_id, api_key, api_base) from neo config."""
    agents = neo_cfg.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    provider_id = defaults.get("provider", "")
    providers = neo_cfg.get("providers", {})
    if isinstance(providers, dict) and provider_id:
        prov = providers.get(provider_id, {})
        if isinstance(prov, dict):
            api_key = prov.get("api_key", "")
            api_base = prov.get("api_base", "")
            return provider_id, api_key, api_base
    return provider_id, "", ""


def _build_worker_providers(provider_id: str, model_id: str, api_key: str,
                            neo_providers: dict) -> dict:
    """Build providers dict for worker agent config.

    Uses official ProviderSpec for default_api_base when provider is
    in the nanobot registry. Falls back to neo's api_base if same provider.
    """
    spec = find_by_name(provider_id)
    api_base = ""
    if spec is not None and spec.default_api_base:
        api_base = spec.default_api_base
    elif provider_id in neo_providers:
        neo_p = neo_providers[provider_id]
        if isinstance(neo_p, dict):
            api_base = neo_p.get("api_base", "")

    # API key: use worker's own key, fallback to neo's
    key = api_key
    if not key and provider_id in neo_providers:
        neo_p = neo_providers[provider_id]
        if isinstance(neo_p, dict):
            key = neo_p.get("api_key", "")

    return {
        provider_id: {
            "api_key": key,
            "api_base": api_base,
        }
    }


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
        if is_cmd:
            action = ""
        else:
            action = f'<button class="del-btn" onclick="removeAgent(\'{name}\')">删除</button>'
        rows.append(f"<tr><td>{name} {tag}</td><td>{gw}/{ws}</td><td>{action}</td></tr>")
    
    return ("<table><tr><th>Agent</th><th>端口 (gw/ws)</th><th>操作</th></tr>"
            + "\n".join(rows) + "</table>")


def _render_model_options(options: list[dict]) -> str:
    """Render <option> tags for model dropdown. (Kept for backward compat.)"""
    if not options:
        return '<option value="">(未找到可用模型)</option>'
    lines = []
    for i, opt in enumerate(options):
        selected = ' selected' if i == 0 else ''
        lines.append(f'        <option value="{opt["id"]}:{opt["model"]}"{selected}>{opt["label"]}</option>')
    return "\n".join(lines)


def _escape_js(s: str) -> str:
    """Escape a string for safe embedding in JS single-quoted string."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


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
        neo_cfg = _get_neo_config(squad_cfg)
        neo_provider_id, neo_api_key, _ = _get_neo_provider_info(neo_cfg)

        agent_table = _render_agent_table(squad_cfg, neo_cfg)
        provider_opts, presets_js = _build_provider_js_data()

        html = (_HTML
                .replace("{agent_table}", agent_table)
                .replace("{provider_options}", provider_opts)
                .replace("{provider_presets_js}", presets_js)
                .replace("{neo_provider_id}", _escape_js(neo_provider_id))
                .replace("{neo_api_key}", _escape_js(neo_api_key)))
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
        provider_id = (body.get("provider", "") or "").strip()
        model_id = (body.get("model", "") or "").strip()
        api_key = (body.get("api_key", "") or "").strip()

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
        if not provider_id:
            return JSONResponse({"ok": False, "error": "请选择服务商"}, status_code=400)
        if not model_id:
            return JSONResponse({"ok": False, "error": "请选择或输入模型"}, status_code=400)

        # Verify provider exists in registry (or is "custom")
        if provider_id != "custom" and find_by_name(provider_id) is None:
            return JSONResponse({"ok": False, "error": f"未知服务商: {provider_id}"}, status_code=400)

        # Load current squad config
        squad_cfg = load_config(force_reload=True)
        peers = squad_cfg.get("peers", {})

        if name in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已存在"}, status_code=409)

        # Read neo config for fallback providers
        neo_cfg = _get_neo_config(squad_cfg)
        if not neo_cfg:
            return JSONResponse({"ok": False, "error": "无法读取 neo 配置，请确保 Commander 已初始化"}, status_code=500)

        neo_providers = neo_cfg.get("providers", {})

        # Allocate ports
        gw, ws = _allocate_ports(peers)

        # Build providers section for this worker
        worker_providers = _build_worker_providers(
            provider_id, model_id, api_key, neo_providers
        )

        # Generate agent config
        instructions_json = json.dumps(role, ensure_ascii=False)
        providers_str = json.dumps(worker_providers, ensure_ascii=False, indent=2)
        agent_config_json = (_AGENT_CONFIG_TEMPLATE
            .replace("$PROVIDER$", provider_id)
            .replace("$MODEL$", model_id)
            .replace("$INSTRUCTIONS$", instructions_json)
            .replace("$PROVIDERS$", providers_str)
            .replace("$GW_PORT$", str(gw))
            .replace("$WS_PORT$", str(ws))
        )

        # Write agent config
        agent_dir = os.path.join(squad_cfg.get("data_root", "/data"), "instances", name)
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

        config_path_sc = _get_config_path()
        try:
            with open(config_path_sc, "w") as f:
                json.dump(squad_cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 配置已创建 (端口 {gw}/{ws})，但 squad_config 更新失败: {e}。请手动添加 peer 或重启后用 /reset-setup 重建。"},
                status_code=201
            )

        return JSONResponse(
            {"ok": True, "msg": f"Agent '{name}' 已添加 (网关: {gw}, WS: {ws}, provider: {provider_id})，重启空间后生效"},
            status_code=201
        )

    async def _remove_agent(request: Request):
        """POST /config/agents/remove — delete a worker agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()

        squad_cfg = load_config(force_reload=True)
        peers = squad_cfg.get("peers", {})

        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不存在"}, status_code=404)

        info = peers[name]
        if info.get("id") == "squad:commander":
            return JSONResponse(
                {"ok": False, "error": f"'{name}' 是 Commander，不可删除"},
                status_code=403,
            )

        # Remove from peers
        del peers[name]
        squad_cfg["peers"] = peers

        # Persist squad_config.json
        config_path_sc = _get_config_path()
        try:
            with open(config_path_sc, "w") as f:
                json.dump(squad_cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            return JSONResponse(
                {"ok": False, "error": f"squad_config 更新失败: {e}"},
                status_code=500,
            )

        # Archive agent directory (rename instead of delete for safety)
        data_root = squad_cfg.get("data_root", "/data")
        agent_dir = os.path.join(data_root, "instances", name)
        if os.path.exists(agent_dir):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_dir = os.path.join(data_root, "instances", f"{name}.removed.{ts}")
            try:
                shutil.move(agent_dir, archived_dir)
            except OSError as e:
                print(f"[agent_config] ⚠️  归档 '{name}' 目录失败: {e}，已移除 peer", flush=True)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已删除。配置已归档，重启空间后生效。",
        })

    # Mount routes
    app.get("/config/agents")(_agent_page)
    app.post("/config/agents/add")(_add_agent)
    app.post("/config/agents/remove")(_remove_agent)
