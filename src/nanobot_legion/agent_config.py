#!/usr/bin/env python3
"""Agent Management — 配置中心：Worker Agent 增删。

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/agents.
Provider 列表来自 nanobot 官方 ``providers/registry.py``，自动跟随上游更新。
"""
from __future__ import annotations

import datetime, json, os, shutil, signal, subprocess, time
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .squad_config_loader import load_config, save_config

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
  .restore-btn { background:none; border:1px solid #4caf50; color:#4caf50; padding:4px 10px; font-size:.8em; cursor:pointer; border-radius:4px; }
  .restore-btn:hover { background:#e8f5e9; }
  .rez-on { background:#e8f5e9; border:1px solid #4caf50; color:#2e7d32; padding:4px 10px; font-size:.75em; cursor:pointer; border-radius:4px; }
  .rez-on:hover { background:#ffebee; border-color:#e53935; color:#c62828; }
  .rez-off { background:#f5f5f5; border:1px solid #ccc; color:#555; padding:4px 10px; font-size:.75em; cursor:pointer; border-radius:4px; }
  .rez-off:hover { background:#e8f5e9; border-color:#4caf50; color:#2e7d32; }
  .agent-link { color: #1a56db; text-decoration: none; }
  .agent-link:hover { text-decoration: underline; }
  .archived-row td { color:#888; }
</style>
</head>
<body>
<h1>🤖 Agent 管理</h1>

<p style="margin-bottom:16px;color:#666;font-size:.9em">
  当前 Legion 编制。Commander (neo) 由初始化配置生成，不可编辑。<br>
  Worker agent 通过下方表单添加，重启后生效。
</p>

<h2>✅ 运行中</h2>
<p style="color:#888;font-size:.82em;margin-bottom:8px">「删除」= 配置归档到下方，可从📦已归档恢复。重启后生效。</p>
{running_table}

<h2>📦 已归档</h2>
<p style="color:#888;font-size:.82em;margin-bottom:8px">「恢复」= 还原目录并加入复活白名单。「🗑️」= 彻底删除，不可恢复。</p>
{archived_table}

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
  if (!confirm('确定要删除 Agent "' + name + '" 吗？\n\n其配置目录将被归档，侧边栏即时生效。')) return;
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
async function restoreAgent(name, dirName) {
  if (!confirm('确定要恢复 Agent "' + name + '" 吗？\n\n目录将恢复，并加入复活白名单。重启空间后生效。')) return;
  const result = document.getElementById('result');
  result.innerHTML = '';
  try {
    const resp = await fetch('/config/agents/restore', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, dir_name: dirName})
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

async function deletePermanent(name, dirName) {
  if (!confirm('⚠️ 永久删除 Agent "' + name + '"？\n\n此操作不可撤销，配置目录将被彻底删除。')) return;
  const result = document.getElementById('result');
  result.innerHTML = '';
  try {
    const resp = await fetch('/config/agents/delete-permanent', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, dir_name: dirName})
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
async function startAgent(name) {
  const result = document.getElementById('result');
  result.innerHTML = '';
  try {
    const resp = await fetch('/config/agents/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    const data = await resp.json();
    if (data.ok) {
      result.innerHTML = '<div class="ok">✅ ' + data.msg + '</div>';
      setTimeout(() => { window.location.reload(); }, 2000);
    } else {
      result.innerHTML = '<div class="err">❌ ' + data.error + '</div>';
    }
  } catch(err) {
    result.innerHTML = '<div class="err">❌ 提交失败: ' + err.message + '</div>';
  }
}

async function stopAgent(name) {
  if (!confirm('确定要停止 Agent "' + name + '" 吗？\n\n进程将被终止，并从复活白名单移除。')) return;
  const result = document.getElementById('result');
  result.innerHTML = '';
  try {
    const resp = await fetch('/config/agents/stop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name})
    });
    const data = await resp.json();
    if (data.ok) {
      result.innerHTML = '<div class="ok">✅ ' + data.msg + '</div>';
      setTimeout(() => { window.location.reload(); }, 2000);
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


def _escape_js(s: str) -> str:
    """Escape a string for safe embedding in JS single-quoted string."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _escape_html(s: str) -> str:
    """Escape a string for safe embedding in HTML."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _get_listening_ports() -> set[int]:
    """Return ports currently in LISTEN state from /proc/net/tcp and /proc/net/tcp6."""
    used = set()
    for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_path) as f:
                for line_no, line in enumerate(f):
                    if line_no == 0:
                        continue  # Skip header
                    parts = line.split()
                    if len(parts) >= 4 and parts[3] == "0A":  # LISTEN
                        local = parts[1].split(":")
                        if len(local) == 2:
                            port = int(local[1], 16)
                            if port > 0:
                                used.add(port)
        except Exception:
            pass
    return used


def _allocate_ports(peers: dict) -> tuple[int, int]:
    """Find next available gateway_port and ws_port.

    Returns (gw, ws) — checks both squad_config.json peers AND
    ports currently in LISTEN state to avoid collisions.
    """
    all_ports = set()
    for info in peers.values():
        gp = info.get("gateway_port", 0)
        wp = info.get("ws_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            all_ports.add(int(gp))
        if isinstance(wp, (int, float)) and wp > 0:
            all_ports.add(int(wp))

    # Merge with ports actually in use
    all_ports |= _get_listening_ports()

    if not all_ports:
        return 18795, 18895

    candidate = max(all_ports) + 1
    for _ in range(1000):  # Safety limit
        gw = candidate
        ws = candidate + 1
        if gw not in all_ports and ws not in all_ports:
            return gw, ws
        candidate += 2

    raise RuntimeError("No available ports found")


def _patch_agent_config_port(instance_dir: str, gw: int, ws: int) -> None:
    """Update gateway.port and channels.websocket.port in agent's config.json."""
    config_path = os.path.join(instance_dir, "config.json")
    if not os.path.exists(config_path):
        return
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        cfg.setdefault("gateway", {})["port"] = gw
        cfg.setdefault("channels", {}).setdefault("websocket", {})["port"] = ws
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"[agent_config] 🔧 {os.path.basename(instance_dir)} config.json 端口已更新 → gw={gw} ws={ws}", flush=True)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[agent_config] ⚠️ 无法更新 {os.path.basename(instance_dir)} config.json 端口: {e}", flush=True)


# ── Agent Detection ────────────────────────────────────────

def _detect_running_agents(squad_cfg: dict) -> dict[str, bool]:
    """Check which peers have a running process. Returns {name: True/False}."""
    peers = squad_cfg.get("peers", {})
    if not peers:
        return {}

    # Build port→name map from peers
    port_map: dict[int, str] = {}
    for name, info in peers.items():
        gp = info.get("gateway_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            port_map[int(gp)] = name

    # Scan /proc for processes matching gateway ports
    found_ports: set[int] = set()
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read()
                    for port in port_map:
                        needle = str(port).encode()
                        if needle in cmdline:
                            found_ports.add(port)
            except (OSError, PermissionError):
                pass
    except OSError:
        pass

    running: dict[str, bool] = {}
    for name, info in peers.items():
        gp = info.get("gateway_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            running[name] = int(gp) in found_ports
        else:
            running[name] = False

    return running


def _find_archived_agents(squad_cfg: dict) -> list[dict]:
    """Find archived agent directories (.removed.*) and return metadata list.

    Filters out agents that are already in the active peers list.
    """
    data_root = squad_cfg.get("data_root", "/data")
    peers = squad_cfg.get("peers", {})
    active_names = set(peers.keys())
    instances_dir = os.path.join(data_root, "instances")
    archived = []
    try:
        for entry in os.listdir(instances_dir):
            if ".removed." not in entry:
                continue
            dir_path = os.path.join(instances_dir, entry)
            if not os.path.isdir(dir_path):
                continue
            # Parse: name.removed.timestamp → name
            parts = entry.split(".removed.", 1)
            if len(parts) != 2:
                continue
            orig_name = parts[0]
            ts_str = parts[1]

            # Try to read config.json for metadata
            provider = ""
            model = ""
            config_path = os.path.join(dir_path, "config.json")
            try:
                with open(config_path) as f:
                    cfg = json.load(f)
                agents = cfg.get("agents", {})
                defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
                provider = defaults.get("provider", "")
                model = defaults.get("model", "")
            except (FileNotFoundError, json.JSONDecodeError):
                pass

            archived.append({
                "name": orig_name,
                "timestamp": ts_str,
                "dir_name": entry,
                "provider": provider,
                "model": model,
            })
    except OSError:
        pass

    # Filter out agents already in active peers
    archived = [a for a in archived if a["name"] not in active_names]

    archived.sort(key=lambda x: x.get("name", ""))
    return archived


# ── Render ─────────────────────────────────────────────────

def _render_running_table(squad_cfg: dict, running: dict[str, bool]) -> str:
    """Render HTML table of running agent peers with status icons."""
    peers = squad_cfg.get("peers", {})
    if not peers:
        return '<p class="empty">暂无 agent（异常状态）</p>'

    rows = []
    for name, info in sorted(peers.items()):
        gw = info.get("gateway_port", "?")
        ws = info.get("ws_port", "?")
        is_cmd = info.get("id") == "squad:commander"
        is_up = running.get(name, False)
        status_icon = "🟢" if is_up else "⚪"
        tag = '<span class="tag tag-cmd">Commander</span>' if is_cmd else '<span class="tag tag-work">Worker</span>'

        # Printable name with link to detail page
        name_link = f'<a class="agent-link" href="/config/agents/{name}">{name}</a>'

        # Resurrection toggle
        if is_cmd:
            rez = "🔒"
        elif is_up:
            rez = f'<button class="rez-on" onclick="stopAgent(\'{name}\')" title="运行中 — 点击停止">🟢 停止</button>'
        else:
            rez = f'<button class="rez-off" onclick="startAgent(\'{name}\')" title="已停止 — 点击启动">▶ 启动</button>'

        if is_cmd:
            action = ""
        else:
            action = f'<button class="del-btn" onclick="removeAgent(\'{name}\')">删除</button>'
        rows.append(f"<tr><td>{status_icon} {name_link} {tag}</td><td>{gw}/{ws}</td><td>{rez}</td><td>{action}</td></tr>")

    return ("<table><tr><th>Agent</th><th>端口 (gw/ws)</th><th>启停</th><th>操作</th></tr>"
            + "\n".join(rows) + "</table>")


def _render_archived_table(archived: list[dict]) -> str:
    """Render HTML table of archived (removed) agents."""
    if not archived:
        return '<p class="empty">无已归档的 agent</p>'

    rows = []
    for a in archived:
        name = a["name"]
        provider = a.get("provider") or "—"
        model = a.get("model") or "—"
        ts = a.get("timestamp", "")
        ts_display = ts[:8] if len(ts) >= 8 else ts
        dir_name_esc = _escape_js(a["dir_name"])
        name_esc = _escape_js(name)
        rows.append(
            f"<tr class=\"archived-row\"><td>📦 {name}</td>"
            f"<td>{provider}</td><td style='max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{model}</td>"
            f"<td style='font-size:.8em;color:#999'>{ts_display}</td>"
            f"<td>"
            f"<button class=\"restore-btn\" onclick=\"restoreAgent('{name_esc}','{dir_name_esc}')\">恢复</button>"
            f"<button class=\"del-btn\" onclick=\"deletePermanent('{name_esc}','{dir_name_esc}')\">🗑️</button>"
            f"</td></tr>"
        )

    return ("<table><tr><th>Agent</th><th>Provider</th><th>Model</th><th>归档时间</th><th>操作</th></tr>"
            + "\n".join(rows) + "</table>")


def _kill_agent_process(gw_port: int) -> list[int]:
    """Kill all processes listening on a gateway port by scanning /proc/{pid}/cmdline.
    Returns list of killed PIDs."""
    killed = []
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read()
                # Match --port N (the port must appear as a standalone arg after --port)
                port_bytes = str(gw_port).encode()
                if b"--port" in cmdline and port_bytes in cmdline:
                    pid = int(pid_dir)
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
            except (OSError, PermissionError, ValueError):
                pass
    except OSError:
        pass
    if killed:
        print(f"[agent_config] 🔫 已 kill (SIGTERM) PIDs: {killed} (port {gw_port})", flush=True)
    return killed


def _sync_roster(gatekeeper, squad_cfg: dict):
    """Refresh gatekeeper roster from squad_config.json peers after config change."""
    gatekeeper._refresh_roster()
    gatekeeper._init_http_pool()


# ── Route Handlers ─────────────────────────────────────────

def create_agent_routes(app, gatekeeper):
    """Mount agent management routes on gatekeeper's FastAPI app."""

    async def _agent_page(request: Request):
        """GET /config/agents — show agent list + add form."""
        _user = request.session.get("user")
        if not _user:
            from starlette.responses import RedirectResponse
            return RedirectResponse("/")
        if not gatekeeper._platform.is_commander(_user):
            return HTMLResponse(_DENIED, status_code=403)

        squad_cfg = load_config()
        neo_cfg = _get_neo_config(squad_cfg)
        neo_provider_id, neo_api_key, _ = _get_neo_provider_info(neo_cfg)

        running = _detect_running_agents(squad_cfg)
        archived = _find_archived_agents(squad_cfg)

        running_table = _render_running_table(squad_cfg, running)
        archived_table = _render_archived_table(archived)
        provider_opts, presets_js = _build_provider_js_data()

        html = (_HTML
                .replace("{running_table}", running_table)
                .replace("{archived_table}", archived_table)
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
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

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
        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})

        if name in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已存在"}, status_code=409)

        # Check if archived copy exists — reject, guide user to restore
        data_root = squad_cfg.get("data_root", "/data")
        archived_dir = os.path.join(data_root, "instances", f"{name}.removed.")
        try:
            for entry in os.listdir(os.path.join(data_root, "instances")):
                if entry.startswith(f"{name}.removed.") and os.path.isdir(os.path.join(data_root, "instances", entry)):
                    return JSONResponse(
                        {"ok": False, "error": f"Agent '{name}' 的归档目录已存在（{entry}），请在📦已归档中使用「恢复」。"},
                        status_code=409,
                    )
        except OSError:
            pass

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

        # Sync roster first (gatekeeper reads from disk, so save first)
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 配置已创建 (端口 {gw}/{ws})，但 squad_config 更新失败: {e}。请手动添加 peer 或重启后用 /reset-setup 重建。"},
                status_code=201
            )

        try:
            _sync_roster(gatekeeper, squad_cfg)
        except Exception as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 已保存但侧边栏同步失败: {e}，重启空间后生效"},
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
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()

        squad_cfg = load_config()
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
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": False, "error": f"squad_config 更新失败: {e}"},
                status_code=500,
            )

        # Archive agent directory — kill process first to release file handles
        data_root = squad_cfg.get("data_root", "/data")
        agent_dir = os.path.join(data_root, "instances", name)

        gw_port = info.get("gateway_port", 0)
        if gw_port:
            _kill_agent_process(gw_port)
            # Wait up to 5s for process to exit (scan /proc directly by port)
            port_bytes = str(gw_port).encode()
            for attempt in range(50):
                alive = False
                try:
                    for pid_dir in os.listdir("/proc"):
                        if not pid_dir.isdigit():
                            continue
                        try:
                            with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                                if port_bytes in f.read():
                                    alive = True
                                    break
                        except (OSError, PermissionError):
                            pass
                except OSError:
                    pass
                if not alive:
                    break
                time.sleep(0.1)

        if os.path.exists(agent_dir):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_dir = os.path.join(data_root, "instances", f"{name}.removed.{ts}")
            moved = False
            try:
                shutil.move(agent_dir, archived_dir)
                moved = True
            except OSError:
                # If move fails (e.g. cross-device), try copy + delete
                try:
                    shutil.copytree(agent_dir, archived_dir)
                    shutil.rmtree(agent_dir)
                    moved = True
                except OSError as e2:
                    print(f"[agent_config] ⚠️  归档 '{name}' 目录失败: {e2}，已移除 peer", flush=True)
            if moved:
                print(f"[agent_config] 📦 Agent '{name}' 已归档 → {archived_dir}", flush=True)
        else:
            print(f"[agent_config] ⚠️  Agent '{name}' 目录不存在: {agent_dir}", flush=True)

        _sync_roster(gatekeeper, squad_cfg)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已删除。配置已归档，WebUI 侧边栏已更新。",
        })

    async def _restore_agent(request: Request):
        """POST /config/agents/restore — restore an archived agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        dir_name = (body.get("dir_name", "") or "").strip()

        if not name or not dir_name:
            return JSONResponse({"ok": False, "error": "缺少 name 或 dir_name"}, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        instances_dir = os.path.join(data_root, "instances")
        src_dir = os.path.join(instances_dir, dir_name)
        dst_dir = os.path.join(instances_dir, name)

        # Security: ensure dir_name matches the pattern name.removed.*
        if not dir_name.startswith(name + ".removed."):
            return JSONResponse({"ok": False, "error": f"dir_name '{dir_name}' 与 name '{name}' 不匹配"}, status_code=400)

        if not os.path.isdir(src_dir):
            return JSONResponse({"ok": False, "error": f"归档目录 '{dir_name}' 不存在"}, status_code=404)

        if os.path.exists(dst_dir):
            # Empty shell (no config.json) — some startup process may recreate
            # bare directories.  Clean it up and proceed rather than refusing.
            cfg_path = os.path.join(dst_dir, "config.json")
            if os.path.isdir(dst_dir) and not os.path.isfile(cfg_path):
                shutil.rmtree(dst_dir)
            else:
                return JSONResponse({"ok": False, "error": f"Agent '{name}' 目录已存在（可能已恢复或正在运行）"}, status_code=409)

        peers = squad_cfg.get("peers", {})
        if name in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已在 peers 列表中"}, status_code=409)

        # Read archived config for port info
        gw = 0
        ws = 0
        config_path = os.path.join(src_dir, "config.json")
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            gateway = cfg.get("gateway", {})
            if isinstance(gateway, dict):
                gw = gateway.get("port", 0)
            channels = cfg.get("channels", {})
            ws_cfg = channels.get("websocket", {}) if isinstance(channels, dict) else {}
            ws = ws_cfg.get("port", 0) if isinstance(ws_cfg, dict) else 0
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # Rename directory back
        try:
            shutil.move(src_dir, dst_dir)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"恢复目录失败: {e}"}, status_code=500)

        # Add back to peers — check archived ports for conflicts
        if isinstance(gw, (int, float)) and gw > 0 and isinstance(ws, (int, float)) and ws > 0:
            gw_int, ws_int = int(gw), int(ws)
            # Collect all ports currently claimed by peers + actually listening
            used_ports = set()
            for info in peers.values():
                for k in ("gateway_port", "ws_port"):
                    v = info.get(k, 0)
                    if isinstance(v, (int, float)) and v > 0:
                        used_ports.add(int(v))
            used_ports |= _get_listening_ports()
            if gw_int in used_ports or ws_int in used_ports:
                gw_int, ws_int = _allocate_ports(peers)
                # Also update the agent's own config.json with re-allocated ports
                _patch_agent_config_port(dst_dir, gw_int, ws_int)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw_int, "ws_port": ws_int}
        else:
            gw, ws = _allocate_ports(peers)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw, "ws_port": ws}

        # Add to resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name not in whitelist:
            whitelist.append(name)
        squad_cfg["resurrection_whitelist"] = whitelist
        squad_cfg["peers"] = peers

        # Persist
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 目录已恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），但 squad_config 更新失败: {e}。"},
                status_code=201
            )

        _sync_roster(gatekeeper, squad_cfg)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），已加入复活白名单。WebUI 侧边栏已更新。",
        })

    async def _delete_permanent_agent(request: Request):
        """POST /config/agents/delete-permanent — permanently delete an archived agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        dir_name = (body.get("dir_name", "") or "").strip()

        if not name or not dir_name:
            return JSONResponse({"ok": False, "error": "缺少 name 或 dir_name"}, status_code=400)

        # Security: ensure dir_name matches the pattern name.removed.*
        if not dir_name.startswith(name + ".removed."):
            return JSONResponse({"ok": False, "error": f"dir_name '{dir_name}' 与 name '{name}' 不匹配"}, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        dir_path = os.path.join(data_root, "instances", dir_name)

        if not os.path.isdir(dir_path):
            return JSONResponse({"ok": False, "error": f"归档目录 '{dir_name}' 不存在"}, status_code=404)

        # Remove from resurrection whitelist if present
        whitelist = list(squad_cfg.get("resurrection_whitelist", []))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else []
        if name in whitelist:
            whitelist.remove(name)
            squad_cfg["resurrection_whitelist"] = whitelist
            try:
                save_config(squad_cfg)
            except OSError:
                pass

        # Permanently delete
        try:
            shutil.rmtree(dir_path)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"删除目录失败: {e}"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已永久删除。",
        })

    async def _start_agent(request: Request):
        """POST /config/agents/start — add to whitelist + spawn agent process."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "缺少 name"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "Commander (neo) 只能由 gatekeeper 管理"}, status_code=403)

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})
        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不在 peers 列表中"}, status_code=404)

        data_root = squad_cfg.get("data_root", "/data")
        mount_path = data_root  # In Staging, data_root == /data (/data → /mnt/workspace for MS)

        config_path = os.path.join(mount_path, "instances", name, "config.json")
        workspace_path = os.path.join(mount_path, "instances", name, "workspace")
        channel_dir = os.path.join(mount_path, "instances", name, "channels")
        log_dir = os.path.join(mount_path, "instances", name, "workspace", "logs")

        if not os.path.exists(config_path):
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 的 config.json 不存在"}, status_code=404)

        # Parse port from config
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            gw_port = cfg.get("gateway", {}).get("port", 0)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return JSONResponse({"ok": False, "error": f"无法读取配置: {e}"}, status_code=500)
        if not gw_port:
            return JSONResponse({"ok": False, "error": "无法从配置中解析 gateway.port"}, status_code=500)

        # Check if already running
        running = _detect_running_agents(squad_cfg)
        if running.get(name):
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已在运行 (port {gw_port})"}, status_code=409)

        # Prepare directories
        os.makedirs(workspace_path, exist_ok=True)
        os.makedirs(channel_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

        # Spawn agent process (detached)
        env = os.environ.copy()
        env["NANOBOT_ACCOUNT_BASE"] = channel_dir
        log_path = os.path.join(log_dir, f"{name}.log")
        try:
            log_fh = open(log_path, "a")
        except OSError:
            log_fh = subprocess.DEVNULL

        proc = subprocess.Popen(
            [
                "nanobot", "gateway",
                "--config", config_path,
                "--workspace", workspace_path,
                "--port", str(gw_port),
            ],
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,  # Detach from gatekeeper process group
        )

        # Add to resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name not in whitelist:
            whitelist.append(name)
        squad_cfg["resurrection_whitelist"] = whitelist

        try:
            save_config(squad_cfg)
        except OSError:
            pass  # Non-fatal; agent is already running

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已启动 (PID: {proc.pid}, port: {gw_port})，已加入复活白名单。",
            "pid": proc.pid,
        })

    async def _stop_agent(request: Request):
        """POST /config/agents/stop — kill agent process + remove from whitelist."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "缺少 name"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "Commander (neo) 不可停止"}, status_code=403)

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})
        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不在 peers 列表中"}, status_code=404)

        gw_port = peers[name].get("gateway_port", 0)
        if not gw_port:
            return JSONResponse({"ok": False, "error": "无法确定 agent 端口"}, status_code=500)

        killed = _kill_agent_process(gw_port)

        # Remove from resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name in whitelist:
            whitelist.remove(name)
        squad_cfg["resurrection_whitelist"] = whitelist

        try:
            save_config(squad_cfg)
        except OSError:
            pass

        if killed:
            return JSONResponse({
                "ok": True,
                "msg": f"Agent '{name}' 已停止 (PID: {killed})，已移出复活白名单。",
            })
        else:
            return JSONResponse({
                "ok": True,
                "msg": f"未找到 Agent '{name}' (port {gw_port}) 的运行进程。已移出复活白名单。",
            })

    async def _agent_detail(request: Request):
        """GET /config/agents/{name} — detail/edit page for an agent."""
        _user = request.session.get("user")
        if not _user:
            return HTMLResponse("<h3>请先登录</h3>", status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return HTMLResponse(_DENIED, status_code=403)

        name = request.path_params.get("name", "")
        if not name or name in ("add", "remove", "restore", "delete-permanent", "start", "stop"):
            return HTMLResponse("<h3>Agent 不存在</h3>", status_code=404)

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})
        if name not in peers:
            return HTMLResponse(f"<h3>Agent '{name}' 不在编制中</h3>", status_code=404)

        data_root = squad_cfg.get("data_root", "/data")
        mount_path = data_root
        config_path = os.path.join(mount_path, "instances", name, "config.json")

        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return HTMLResponse(f"<h3>无法读取 {name} 的配置</h3>", status_code=500)

        # Extract current values
        agents = cfg.get("agents", {})
        defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
        cur_provider = defaults.get("provider", "") or ""
        cur_model = defaults.get("model", "") or ""
        cur_api_key = ""
        providers = cfg.get("providers", {})
        if isinstance(providers, dict) and cur_provider in providers:
            cur_api_key = providers[cur_provider].get("api_key", "") or ""

        gw = cfg.get("gateway", {}).get("port", "?") if isinstance(cfg.get("gateway"), dict) else "?"
        ws_port = "?"
        channels = cfg.get("channels", {})
        if isinstance(channels, dict):
            ws_cfg = channels.get("websocket", {})
            if isinstance(ws_cfg, dict):
                ws_port = ws_cfg.get("port", "?")

        # Build provider options
        provider_options = ['<option value="">— 选择服务商 —</option>']
        for spec in _get_setup_providers():
            sel = ' selected' if spec.name == cur_provider else ''
            provider_options.append(f'<option value="{spec.name}"{sel}>{spec.label}</option>')

        # Build preset JS data
        p_entries = []
        for spec in _get_setup_providers():
            models = _PROVIDER_MODELS.get(spec.name, [])
            models_json = json.dumps(models)
            p_entries.append(f'"{spec.name}": {{models: {models_json}}}')
        presets_js = "{" + ", ".join(p_entries) + "}"

        provider_options_html = "\n".join(provider_options)

        is_cmd = peers.get(name, {}).get("id") == "squad:commander"

        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — 配置</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width:600px; margin:20px auto; padding:0 16px; color:#222; }}
  h2 {{ margin-bottom:4px; font-size:1.3em; }}
  .subtle {{ color:#888; font-size:.85em; margin-bottom:20px; }}
  label {{ display:block; font-weight:600; margin-top:14px; margin-bottom:4px; font-size:.9em; }}
  input, select {{ width:100%; box-sizing:border-box; padding:8px 10px; border:1px solid #ccc; border-radius:6px; font-size:.95em; }}
  input:focus, select:focus {{ outline:none; border-color:#4f46e5; box-shadow:0 0 0 3px rgba(79,70,229,.15); }}
  .row {{ display:flex; gap:16px; }}
  .row > div {{ flex:1; }}
  .ro {{ background:#f9fafb; color:#666; }}
  .btn {{ background:#4f46e5; color:#fff; border:none; padding:10px 24px; font-size:.95em; border-radius:6px; cursor:pointer; margin-top:18px; }}
  .btn:hover {{ background:#4338ca; }}
  .back {{ color:#888; font-size:.85em; }}
</style>
</head>
<body>
<h2>⚙️ {name} {('Commander' if is_cmd else 'Worker')}</h2>
<p class="subtle">修改后需在 Agent 管理页手动启停生效。</p>

<label>服务商</label>
<select id="provider">{provider_options_html}</select>

<label>API Key</label>
<input type="password" id="api_key" value="{_escape_html(cur_api_key)}" placeholder="sk-...">

<div class="row">
  <div><label>模型</label><input type="text" id="model" value="{_escape_html(cur_model)}" placeholder="模型标识"></div>
  <div><label>gateway 端口</label><input class="ro" type="text" value="{gw}" readonly></div>
  <div><label>ws 端口</label><input class="ro" type="text" value="{ws_port}" readonly></div>
</div>

<button class="btn" onclick="saveConfig('{_escape_js(name)}')">保存</button>
<div id="result"></div>

<p><a class="back" href="/config/agents">← 返回 Agent 管理</a></p>

<script>
var _presets = {presets_js};
var _curApiKey = {json.dumps(cur_api_key)};

// Fill model on provider change
(function() {{
  var sel = document.getElementById('provider');
  var modelInp = document.getElementById('model');
  function updateModel() {{
    var p = sel.value;
    var pk = document.getElementById('api_key');
    if (p && _presets[p] && _presets[p].models.length > 0) {{
      modelInp.value = _presets[p].models[0];
    }}
    // Keep current key unless provider changed from original
  }}
  sel.addEventListener('change', updateModel);
  // On first load, ensure model matches provider
  if (!modelInp.value && sel.value && _presets[sel.value]) {{
    modelInp.value = _presets[sel.value].models[0] || '';
  }}
}})();

async function saveConfig(name) {{
  var result = document.getElementById('result');
  result.innerHTML = '';
  var provider = document.getElementById('provider').value;
  var api_key = document.getElementById('api_key').value;
  var model = document.getElementById('model').value;
  if (!provider) {{ result.innerHTML = '<div style="color:#c62828;margin-top:12px">❌ 请选择服务商</div>'; return; }}
  if (!api_key) {{ result.innerHTML = '<div style="color:#c62828;margin-top:12px">❌ 请输入 API Key</div>'; return; }}
  try {{
    var resp = await fetch('/config/agents/' + name + '/save', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{provider: provider, api_key: api_key, model: model}})
    }});
    var data = await resp.json();
    if (data.ok) {{
      result.innerHTML = '<div style="color:#2e7d32;margin-top:12px">✅ ' + data.msg + '</div>';
    }} else {{
      result.innerHTML = '<div style="color:#c62828;margin-top:12px">❌ ' + data.error + '</div>';
    }}
  }} catch(err) {{
    result.innerHTML = '<div style="color:#c62828;margin-top:12px">❌ 保存失败: ' + err.message + '</div>';
  }}
}}
</script>
</body>
</html>""")

    async def _save_agent_detail(request: Request):
        """POST /config/agents/{name}/save — update agent config.json."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        name = request.path_params.get("name", "")
        if not name:
            return JSONResponse({"ok": False, "error": "无效路径"}, status_code=400)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        provider = (body.get("provider", "") or "").strip()
        api_key = (body.get("api_key", "") or "").strip()
        model = (body.get("model", "") or "").strip()

        if not provider:
            return JSONResponse({"ok": False, "error": "缺少 provider"}, status_code=400)
        if not api_key:
            return JSONResponse({"ok": False, "error": "缺少 api_key"}, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        mount_path = data_root
        config_path = os.path.join(mount_path, "instances", name, "config.json")

        try:
            with open(config_path) as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 的 config.json 不存在"}, status_code=404)
        except json.JSONDecodeError:
            return JSONResponse({"ok": False, "error": "config.json 格式错误"}, status_code=500)

        # Update agents.defaults
        agents = cfg.setdefault("agents", {})
        agents.setdefault("defaults", {})
        agents["defaults"]["provider"] = provider
        agents["defaults"]["model"] = model or provider

        # Update provider api_key
        providers = cfg.setdefault("providers", {})
        providers.setdefault(provider, {})
        if isinstance(providers[provider], dict):
            providers[provider]["api_key"] = api_key
        else:
            providers[provider] = {"api_key": api_key}

        # Write back
        try:
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"写入失败: {e}"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 配置已更新。返回管理页后可用「停止→启动」使其生效。",
        })

    # Mount routes
    app.get("/config/agents")(_agent_page)
    app.post("/config/agents/add")(_add_agent)
    app.post("/config/agents/remove")(_remove_agent)
    app.post("/config/agents/restore")(_restore_agent)
    app.post("/config/agents/delete-permanent")(_delete_permanent_agent)
    app.post("/config/agents/start")(_start_agent)
    app.post("/config/agents/stop")(_stop_agent)
    app.get("/config/agents/{name}")(_agent_detail)
    app.post("/config/agents/{name}/save")(_save_agent_detail)

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 可访问此管理页面</h3>"
