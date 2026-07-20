"""Squad gatekeeper — pinned binding chat initialization.

Extracted from gatekeeper.py Batch 3a.
Must be kept in sync with oauth_proxy.py in cloud-agent-gateway.
"""

import os as _os
import time as _time
import uuid as _uuid

from cloud_agent_gateway.package_source import get_package_source, build_source_link

# ── Constants — keep in sync with cloud-agent-gateway oauth_proxy.py ──
_BINDING_CHAT_TITLE = "配置中心"
_LEGACY_BINDING_TITLES = ["社交通道配置提示", "社交通道配置指南"]
_BINDING_PROJECT = "系统配置"


def ensure_pinned_binding_chat(gatekeeper):
    """Create pinned sidebar chat with channel binding links for all roster agents.

    For commander (neo): full admin links (agent management, commander whitelist, etc.).
    For worker agents: channels-only view.
    """
    _bindings = getattr(gatekeeper, "_bindings", [])
    if not _bindings:
        gatekeeper._log("📌 无可用的绑定通道，跳过 pinned chat")
        return

    _agent = gatekeeper.webui_agent
    _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _project_dir = f"{gatekeeper._platform.data_root}/{_BINDING_PROJECT}"

    # Generate binding links (same format as Cloud Demo oauth_proxy.py)
    _rows = "\n".join(
        f"| {_spec.icon} {_spec.display} | [绑定{_spec.display}](/bind/{_spec.name}) |"
        for _spec in _bindings
    )

    # ── dynamic source links (aligned with oauth_proxy.py) ──
    cag_info = get_package_source("cloud-agent-gateway")
    nanobot_info = get_package_source("nanobot-ai")
    nanobot_legion_info = get_package_source("nanobot-legion")

    cag_link = build_source_link(cag_info, "DreamShepherd2006/cloud-agent-gateway")
    nanobot_link = build_source_link(nanobot_info, "DreamShepherd2006/nanobot", "nightly")
    nanobot_legion_link = build_source_link(nanobot_legion_info, "DreamShepherd2006/nanobot-legion")

    # Two content variants: commander gets full admin, workers get channels only.
    _content_commander = f"""\
# 📱 社交通道配置

将 nanobot 连接到社交通道，随时随地对话。

| 通道 | 操作 |
|------|------|
{_rows}

👆 点击上方链接即可操作，无需在此聊天。

---

# 🤖 Agent 管理

Legion 多智能体编制管理。Commander (neo) 由初始化配置生成。

👉 [`配置 Agent`](/config/agents)

添加或管理 Worker Agent（名字、角色、模型），保存后**重启空间**生效。

 ---

 # 🛡️ Squad 管理

 Commander 白名单与 Relay Token 配置：

 👉 [`管理`](/config/commander)

 ---

 # 🔗 用户-Agent 映射

 将特定 OAuth 用户绑定到专属 agent（频道绑定写入该 agent 目录）：

 👉 [`配置映射`](/config/user-agent-map)

 ---

 # 📁 文件管理

 上传、下载、管理你的文件（PPTX、视频、文档等）：

 👉 [`/files`](/files)

 Agent 生成的输出文件存放在此，可随时下载。

 ---

 # ⚙️ 系统重置

 如需重新配置 OAuth 登录凭证（API Key / 模型配置会保留并自动预填），访问：

 👉 [`/reset-setup`](/reset-setup)

  ---

 # 📦 开源代码

 本项目完全开源。

 | 组件 | 源码 |
 |------|------|
 | cloud-agent-gateway（框架层） | {cag_link} |
 | nanobot-legion（部署层） | {nanobot_legion_link} |
 | nanobot（AI 引擎） | {nanobot_link} |

 🧭 点击上方链接浏览完整代码，仓库中的 Dockerfile 可用于部署新空间。
"""

    _content_worker = f"""\
# 📱 社交通道配置

将 nanobot 连接到社交通道，随时随地对话。

| 通道 | 操作 |
|------|------|
{_rows}

👆 点击上方链接即可操作，无需在此聊天。

> 💡 频道绑定后凭证写入当前 agent 目录，重启即生效。
> 其他管理功能请使用 Commander 账号登录。
"""

    # Clean up old binding sessions — matches both current and legacy titles
    _state = gatekeeper._platform.read_sidebar_state(_agent)
    _new_pinned = []
    _changed = False
    for _pk in _state.get("pinned_keys", []):
        if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
            _new_pinned.append(_pk)
            continue
        _cid_old = _pk.split(":", 1)[1]
        _lines = gatekeeper._platform.read_session(_agent, _cid_old)
        if _lines:
            _title = _lines[0].get("metadata", {}).get("title", "")
            if _title == _BINDING_CHAT_TITLE or _title in _LEGACY_BINDING_TITLES:
                gatekeeper._platform.delete_session(_agent, _cid_old)
                _changed = True
                continue
        _new_pinned.append(_pk)
    if _changed:
        _state["pinned_keys"] = _new_pinned
        _state["updated_at"] = _now
        gatekeeper._platform.write_sidebar_state(_agent, _state)

    # Create new binding session
    _cid = str(_uuid.uuid4())
    _key = f"websocket:{_cid}"

    _os.makedirs(_project_dir, exist_ok=True)
    gatekeeper._platform.write_session(_agent, _cid, [
        {
            "_type": "metadata", "key": _key,
            "created_at": _now, "updated_at": _now,
            "metadata": {"title": _BINDING_CHAT_TITLE, "webui": True,
                        "workspace_scope": {"project_path": _project_dir},
                        "_binding_type": "system_config"},
            "last_consolidated": 0,
        },
        {
            "role": "user", "content": _content_commander,
            "timestamp": _now,
        },
    ])

    # WebUI transcript
    gatekeeper._platform.write_webui_transcript(_agent, _cid, [
        {"event": "delta", "text": _content_commander, "chat_id": _cid},
        {"event": "stream_end", "text": _content_commander, "chat_id": _cid},
        {"event": "turn_end", "chat_id": _cid},
    ])

    # Pin to sidebar — commander (neo) first, then replicate to all roster agents
    # Non-commander users (routed via user_agent_map) get a worker-only view.
    for _agent_name in list(gatekeeper.agent_names) + ([_agent] if _agent not in gatekeeper.agent_names else []):
        # Choose content: full admin for neo, channels-only for workers
        _agent_content = _content_commander if _agent_name == _agent else _content_worker
        _sidebar_state = gatekeeper._platform.read_sidebar_state(_agent_name)
        _pinned = _sidebar_state.get("pinned_keys", [])
        if _key not in _pinned:
            _pinned.insert(0, _key)
        _sidebar_state["pinned_keys"] = _pinned
        _sidebar_state["updated_at"] = _now
        _sidebar_state.setdefault("schema_version", 1)
        gatekeeper._platform.write_sidebar_state(_agent_name, _sidebar_state)
        # Write session + transcript for non-commander agents
        if _agent_name != _agent:
            gatekeeper._platform.write_session(_agent_name, _cid, [
                {
                    "_type": "metadata", "key": _key,
                    "created_at": _now, "updated_at": _now,
                    "metadata": {"title": _BINDING_CHAT_TITLE, "webui": True,
                                "workspace_scope": {"project_path": _project_dir},
                                "_binding_type": "system_config"},
                    "last_consolidated": 0,
                },
                {
                    "role": "user", "content": _agent_content,
                    "timestamp": _now,
                },
            ])
            gatekeeper._platform.write_webui_transcript(_agent_name, _cid, [
                {"event": "delta", "text": _agent_content, "chat_id": _cid},
                {"event": "stream_end", "text": _agent_content, "chat_id": _cid},
                {"event": "turn_end", "chat_id": _cid},
            ])

    gatekeeper._binding_chat_cid = _cid
    _agent_count = len(set(list(gatekeeper.agent_names) + [_agent]))
    gatekeeper._log(f"📌 pinned binding chat ({_cid[:12]}...) — {len(_bindings)} channels, {_agent_count} agents")
