#!/usr/bin/env python3
"""Agent Management — 配置中心：Worker Agent 增删。

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/agents.
Provider 列表来自 nanobot 官方 ``providers/registry.py``，自动跟随上游更新。
"""
from __future__ import annotations

import datetime, json, os, shutil, subprocess, time
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .squad_config_loader import load_config, save_config

# ── provider helpers ─────────────────────────────────────────
from .agent_providers import (
    find_by_name, _build_provider_js_data, _get_setup_providers, _PROVIDER_MODELS
)

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

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))

def _load_template(filename: str) -> str:
    """Load an HTML template from the package directory."""
    with open(_os.path.join(_HERE, filename), encoding="utf-8") as f:
        return f.read()

_HTML = _load_template("agent_config.html")

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


# ── Agent ops / views ──────────────────────────────────────
from .agent_ops import (
    _get_listening_ports, _allocate_ports, _patch_agent_config_port,
    _detect_running_agents, _read_agent_metadata, _find_archived_agents,
    _kill_agent_process, _sync_roster
)

from .agent_views import _escape_js, _escape_html, _render_running_table, _render_archived_table

# ── Route Handlers ─────────────────────────────────────────

def create_agent_routes(app, gatekeeper):
    """Mount agent management routes on gatekeeper's FastAPI app."""
    app.state.gatekeeper = gatekeeper

    from .agent_handlers import _read_config_summary
    from .agent_handlers import agent_page as _agent_page
    from .agent_handlers import agent_detail as _agent_detail
    from .agent_handlers import save_agent_detail as _save_agent_detail
    from .agent_handlers import add_agent as _add_agent
    from .agent_handlers import remove_agent as _remove_agent
    from .agent_handlers import restore_agent as _restore_agent
    from .agent_handlers import restore_agent_confirm as _restore_agent_confirm
    from .agent_handlers import delete_permanent_agent as _delete_permanent_agent
    from .agent_handlers import start_agent as _start_agent
    from .agent_handlers import stop_agent as _stop_agent

    # Mount routes
    app.get("/config/agents")(_agent_page)
    app.post("/config/agents/add")(_add_agent)
    app.post("/config/agents/remove")(_remove_agent)
    app.post("/config/agents/restore")(_restore_agent)
    app.post("/config/agents/restore-confirm")(_restore_agent_confirm)
    app.post("/config/agents/delete-permanent")(_delete_permanent_agent)
    app.post("/config/agents/start")(_start_agent)
    app.post("/config/agents/stop")(_stop_agent)
    app.get("/config/agents/{name}")(_agent_detail)
    app.post("/config/agents/{name}/save")(_save_agent_detail)

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 可访问此管理页面</h3>"
