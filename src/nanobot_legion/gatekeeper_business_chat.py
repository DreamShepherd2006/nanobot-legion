"""Squad gatekeeper — business management pinned chat initialization.

Creates a separate pinned chat "业务管理" under "系统配置" sidebar project,
containing links to nanobot-quant credential management pages.

Auto-discovers credential specs from installed packages (nanobot_quant).
"""

from __future__ import annotations

import json as _json
import os as _os
import time as _time
import uuid as _uuid

_BUSINESS_CHAT_TITLE = "业务管理"
_BUSINESS_PROJECT = "系统配置"


def ensure_business_management_chat(gatekeeper) -> None:
    """Create pinned business management chat if nanobot-quant is installed.

    Includes API credential management and future business pages.
    """
    # ── Discover credential specs from nanobot_quant ──────────
    try:
        from nanobot_quant.credential_registry import discover as _discover_credentials
    except ImportError:
        gatekeeper._log("📊 nanobot_quant 未安装，跳过业务管理 chat")
        return

    _specs = _discover_credentials()
    if not _specs:
        gatekeeper._log("📊 无凭证类型注册，跳过业务管理 chat")
        return

    gatekeeper._log(f"📊 发现 {len(_specs)} 种凭证类型，创建业务管理 chat")

    _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _project_dir = f"{gatekeeper._platform.data_root}/{_BUSINESS_PROJECT}"

    # ── Build credential rows ─────────────────────────────────
    _cred_rows = "\n".join(
        f"| {_spec.icon} {_spec.display} | [配置凭证](/config/credentials/{_name}) |"
        for _name, _spec in _specs.items()
    )

    # ── Build business management content ─────────────────────
    _content = f"""\
# 🔑 API 凭证管理

配置交易所、数据源的 API 凭证，保存后即时生效。

{_cred_rows}

---

> 💡 **提示**：凭证文件存储在 `/data/credentials/` 目录下，容器重启后保留。
"""

    _session_id = f"business-mgmt-{_uuid.uuid4().hex[:8]}"
    _session_key = f"{_BUSINESS_PROJECT}/{_BUSINESS_CHAT_TITLE}/{_session_id}"

    # ── Distribute to all roster agents ───────────────────────
    for _agent_name in gatekeeper.agent_names:
        _inst_dir = f"{gatekeeper._platform.data_root}/legion/instances/{_agent_name}"
        _ws_dir = f"{_inst_dir}/workspace"
        _project_dir_agent = f"{_ws_dir}/{_BUSINESS_PROJECT}/{_BUSINESS_CHAT_TITLE}"

        try:
            _os.makedirs(_project_dir_agent, exist_ok=True)
        except OSError:
            continue

        # Clean up any previous business chat sessions for this agent
        _sessions_dir = f"{_ws_dir}/sessions"
        try:
            if _os.path.isdir(_sessions_dir):
                for _fname in _os.listdir(_sessions_dir):
                    if _fname.startswith("business-mgmt-") and _fname.endswith(".jsonl"):
                        _os.remove(f"{_sessions_dir}/{_fname}")
        except OSError:
            pass

        # ── Write session JSONL ───────────────────────────────
        _os.makedirs(_sessions_dir, exist_ok=True)
        _session_path = f"{_sessions_dir}/{_session_id}.jsonl"
        _lines = [
            _json.dumps({"_type": "metadata", "metadata": {"title": _BUSINESS_CHAT_TITLE, "workspace_scope": {"project_path": f"/{_BUSINESS_PROJECT}/{_BUSINESS_CHAT_TITLE}"}}}, ensure_ascii=False),
            _json.dumps({"_type": "message", "role": "user", "content": _content, "timestamp": _now}, ensure_ascii=False),
            _json.dumps({"_type": "message", "role": "assistant", "content": "业务管理页面已就绪。请在侧边栏选择对应页面查看管理选项。", "timestamp": _now}, ensure_ascii=False),
        ]
        try:
            with open(_session_path, "w", encoding="utf-8") as _f:
                _f.write("\n".join(_lines) + "\n")
        except OSError:
            gatekeeper._log(f"⚠️ 无法写入业务管理 session: {_agent_name}")
            continue

        # ── Write webui transcript ────────────────────────────
        _webui_dir = f"{_inst_dir}/webui"
        _os.makedirs(_webui_dir, exist_ok=True)
        _transcript_id = f"websocket_{_uuid.uuid4().hex}"
        _transcript_path = f"{_webui_dir}/{_transcript_id}.jsonl"
        _t_lines = [
            _json.dumps({"_type": "created", "session_id": _session_id, "project_path": f"/{_BUSINESS_PROJECT}/{_BUSINESS_CHAT_TITLE}", "title": _BUSINESS_CHAT_TITLE, "timestamp": _now}, ensure_ascii=False),
            _json.dumps({"_type": "message", "role": "user", "content": _content, "session_id": _session_id, "timestamp": _now}, ensure_ascii=False),
            _json.dumps({"_type": "message", "role": "assistant", "content": "业务管理页面已就绪。", "session_id": _session_id, "timestamp": _now}, ensure_ascii=False),
        ]
        try:
            with open(_transcript_path, "w", encoding="utf-8") as _f:
                _f.write("\n".join(_t_lines) + "\n")
        except OSError:
            pass

    # ── Write sidebar-state.json for each agent ───────────────
    for _agent_name in gatekeeper.agent_names:
        _inst_dir = f"{gatekeeper._platform.data_root}/legion/instances/{_agent_name}"
        _sidebar_path = f"{_inst_dir}/sidebar-state.json"
        try:
            try:
                with open(_sidebar_path, "r", encoding="utf-8") as _f:
                    _sidebar = _json.loads(_f.read() or "{}")
            except (FileNotFoundError, _json.JSONDecodeError):
                _sidebar = {}
        except Exception:
            continue

        if "pinned_projects" not in _sidebar:
            _sidebar["pinned_projects"] = {}
        if _BUSINESS_PROJECT not in _sidebar["pinned_projects"]:
            _sidebar["pinned_projects"][_BUSINESS_PROJECT] = {}

        # Add business management chat to pinned_projects
        _sidebar["pinned_projects"][_BUSINESS_PROJECT][_BUSINESS_CHAT_TITLE] = {
            "session_id": _session_id,
            "project_path": f"/{_BUSINESS_PROJECT}/{_BUSINESS_CHAT_TITLE}",
        }

        try:
            with open(_sidebar_path, "w", encoding="utf-8") as _f:
                _json.dump(_sidebar, _f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    gatekeeper._log(f"📊 业务管理 chat 已创建 (session={_session_id})")
