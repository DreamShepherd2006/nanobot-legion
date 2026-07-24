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
    """Create pinned business management chat if nanobot-quant is installed."""
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
    _platform = gatekeeper._platform
    _project_dir = f"{_platform.data_root}/{_BUSINESS_PROJECT}"
    _os.makedirs(_project_dir, exist_ok=True)

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

    _cid = str(_uuid.uuid4())
    _key = f"websocket:{_cid}"

    # ── Write session + transcript + pin for each roster agent ─
    for _agent_name in gatekeeper.agent_names:
        _inst_dir = _platform.instance_path(_agent_name)

        # Clean up any previous business chat sessions for this agent
        _state = _platform.read_sidebar_state(_agent_name)
        _new_pinned = []
        _changed = False
        for _pk in _state.get("pinned_keys", []):
            if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
                _new_pinned.append(_pk)
                continue
            _cid_old = _pk.split(":", 1)[1]
            _lines = _platform.read_session(_agent_name, _cid_old)
            if _lines:
                _title = _lines[0].get("metadata", {}).get("title", "")
                if _title == _BUSINESS_CHAT_TITLE:
                    _platform.delete_session(_agent_name, _cid_old)
                    _changed = True
                    continue
            _new_pinned.append(_pk)
        if _changed:
            _state["pinned_keys"] = _new_pinned
            _state["updated_at"] = _now
            _platform.write_sidebar_state(_agent_name, _state)

        # Write session JSONL
        _platform.write_session(_agent_name, _cid, [
            {
                "_type": "metadata", "key": _key,
                "created_at": _now, "updated_at": _now,
                "metadata": {"title": _BUSINESS_CHAT_TITLE, "webui": True,
                            "workspace_scope": {"project_path": _project_dir},
                            "_binding_type": "business_mgmt"},
                "last_consolidated": 0,
            },
            {
                "role": "user", "content": _content,
                "timestamp": _now,
            },
        ])

        # Write webui transcript
        _platform.write_webui_transcript(_agent_name, _cid, [
            {"event": "delta", "text": _content, "chat_id": _cid},
            {"event": "stream_end", "text": _content, "chat_id": _cid},
            {"event": "turn_end", "chat_id": _cid},
        ])

        # Pin to sidebar via pinned_keys
        _sidebar_state = _platform.read_sidebar_state(_agent_name)
        _pinned = _sidebar_state.get("pinned_keys", [])
        if _key not in _pinned:
            _pinned.insert(0, _key)
        _sidebar_state["pinned_keys"] = _pinned
        _sidebar_state["updated_at"] = _now
        _sidebar_state.setdefault("schema_version", 1)
        _platform.write_sidebar_state(_agent_name, _sidebar_state)

    gatekeeper._log(f"📊 业务管理 chat 已创建 (session={_cid})")
