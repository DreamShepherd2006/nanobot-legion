#!/usr/bin/env python3
"""Agent management route handlers — extracted from agent_config.py closure.

Uses ``request.app.state.gatekeeper`` instead of closure-captured gatekeeper.
"""
from __future__ import annotations

import json, os

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse

from .agent_ops import _detect_running_agents, _find_archived_agents
from .agent_providers import _build_provider_js_data, _get_setup_providers, _PROVIDER_MODELS
from .agent_views import _escape_js, _escape_html, _render_running_table, _render_archived_table
from .squad_config_loader import load_config


# ── Helpers ─────────────────────────────────────────────────

def _read_config_summary(instances_dir: str, agent_dir_name: str) -> dict:
    """Read config.json from an agent directory and return key fields for comparison.
    Returns {} on failure."""
    cfg_path = os.path.join(instances_dir, agent_dir_name, "config.json")
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        agents = cfg.get("agents", {})
        defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
        channels_cfg = cfg.get("channels", {})
        enabled_channels = sorted(
            [k for k, v in channels_cfg.items() if isinstance(v, dict) and v.get("enabled")]
        ) if isinstance(channels_cfg, dict) else []
        return {
            "provider": defaults.get("provider", "?"),
            "model": defaults.get("model", "?"),
            "gateway_port": cfg.get("gateway", {}).get("port", "?"),
            "ws_port": channels_cfg.get("websocket", {}).get("port", "?") if isinstance(channels_cfg, dict) else "?",
            "channels": enabled_channels,
        }
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


# ── Page Handlers ──────────────────────────────────────────

async def agent_page(request: Request):
    """GET /config/agents — show agent list + add form."""
    from .agent_config import _HTML, _DENIED, _get_neo_config, _get_neo_provider_info

    _user = request.session.get("user")
    if not _user:
        return RedirectResponse("/")
    gk = request.app.state.gatekeeper
    if not gk._platform.is_commander(_user):
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


async def agent_detail(request: Request):
    """GET /config/agents/{name} — detail/edit page for an agent."""
    from .agent_config import _DENIED, _load_template

    _user = request.session.get("user")
    if not _user:
        return HTMLResponse("<h3>请先登录</h3>", status_code=401)
    gk = request.app.state.gatekeeper
    if not gk._platform.is_commander(_user):
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

    detail_html = _load_template("agent_config_detail.html")
    detail_html = (detail_html
                   .replace("{agent_name}", _escape_html(name))
                   .replace("{agent_role}", "Commander" if is_cmd else "Worker")
                   .replace("{provider_options}", provider_options_html)
                   .replace("{api_key_escaped}", _escape_html(cur_api_key))
                   .replace("{model_escaped}", _escape_html(cur_model))
                   .replace("{gw_port}", str(gw))
                   .replace("{ws_port}", str(ws_port))
                   .replace("{name_js_escaped}", _escape_js(name))
                   .replace("{presets_js_data}", presets_js)
                   .replace("{api_key_json}", json.dumps(cur_api_key)))
    return HTMLResponse(detail_html)


async def save_agent_detail(request: Request):
    """POST /config/agents/{name}/save — update agent config.json."""
    from .agent_config import _DENIED

    _user = request.session.get("user")
    if not _user:
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    gk = request.app.state.gatekeeper
    if not gk._platform.is_commander(_user):
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
