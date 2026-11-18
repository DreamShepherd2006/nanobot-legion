"""Squad gatekeeper — business management pinned chat initialization.

置顶会话「业务管理」的内容来源（步 3，Legion Module 契约）：

1. **模块优先**：发现已装的 Legion Module，取其 ``sections()`` 结构化内容，
   由 Legion 统一渲染（模块不返回预拼 Markdown）。
2. **无模块则不建会话**：未发现任何 Legion Module（或模块未声明内容）时跳过，
   不再有直连具体应用的兜底实现。

会话在网关启动时重建（与现行为一致），模块不直接读写 session / sidebar。
"""

from __future__ import annotations

import os as _os
import time as _time
import uuid as _uuid

from nanobot_legion.module_loader import discover_modules
from nanobot_legion.module_registry import ModuleContext, _resolve_data_root, _stderr

_BUSINESS_CHAT_TITLE = "业务管理"
_BUSINESS_PROJECT = "系统配置"


# ── 渲染层（模块化路径）────────────────────────────────────────────


def _attr(obj, key, default=None):
    """同时支持 dataclass/对象属性与 dict（模块可自带原子实现）。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cell(text) -> str:
    """表格单元格：竖线转义，空值占位。"""
    s = str(text or "").strip().replace("|", "\\|")
    return s or "—"


def render_sections(sections) -> str:
    """把 ``Section`` / ``Row`` 原子渲染为统一 Markdown。

    中列：优先 status；同时有 status 与 desc 时拼成「status · desc」；
    列名按本节是否出现 status 自动选择「状态」/「说明」。
    """
    blocks: list[str] = []
    for sec in sections or []:
        heading = _attr(sec, "heading", "") or ""
        rows = list(_attr(sec, "rows", []) or [])
        desc = str(_attr(sec, "desc", "") or "").strip()

        lines = [f"# {heading}".rstrip(), ""]
        if desc:
            lines += [desc, ""]
        if rows:
            has_status = any(str(_attr(r, "status", "") or "").strip() for r in rows)
            mid = "状态" if has_status else "说明"
            lines += [f"| 项 | {mid} | 入口 |", "|---|---|---|"]
            for row in rows:
                status = str(_attr(row, "status", "") or "").strip()
                detail = str(_attr(row, "desc", "") or "").strip()
                middle = status if status else detail
                if status and detail:
                    middle = f"{status} · {detail}"
                link = str(_attr(row, "link", "") or "").strip()
                entry = f"[打开]({link})" if link else "—"
                lines.append(f"| {_cell(_attr(row, 'label', ''))} | {_cell(middle)} | {entry} |")
        blocks.append("\n".join(lines).strip() + "\n")
    return "\n---\n\n".join(blocks).strip()


def _core_block(data_root: str) -> str:
    """核心区块（Legion 固定提供，与具体模块无关）。"""
    return f"""\
# 🧭 系统

| 项 | 说明 | 入口 |
|---|---|---|
| 文件管理 | 持久卷文件浏览 / 上传 / 下载 | [打开](/files/) |
| 系统配置 | 社交通道绑定与「📦 开源代码」信息 | 见「系统配置」项目 |
| 存储位置 | 配置与凭证位于 `{data_root}/credentials/`，容器重启后保留 | — |
"""


def _build_module_content(gatekeeper, modules, log) -> str | None:
    """核心区块 + 各模块 sections() 渲染；全部模块未声明内容时返回 None。"""
    root = _resolve_data_root(gatekeeper) or ""
    parts = [_core_block(root)]
    rendered_any = False

    for mod in modules:
        ctx = ModuleContext(mod.name, root, gatekeeper=gatekeeper, log=log)
        try:
            sections = mod.obj.sections(ctx)
        except Exception as e:  # noqa: BLE001 - 单模块渲染失败不阻塞
            msg = f"⚠️ 模块 {mod.name} 会话内容渲染失败: {type(e).__name__}: {e}"
            log(msg)
            _stderr(msg)
            continue
        body = render_sections(sections)
        if not body:
            log(f"ℹ️ 模块 {mod.name} 未声明会话内容")
            continue
        title = f"## {mod.display}\n\n" if len(modules) > 1 else ""
        parts.append(title + body)
        rendered_any = True

    if not rendered_any:
        return None
    return "\n\n---\n\n".join(p.strip() for p in parts) + "\n"


# ── 会话写入（两条路径共用）───────────────────────────────────────


def _write_chat(gatekeeper, content: str) -> str:
    """重建「业务管理」置顶会话（清理旧同名会话 → 写 session/transcript → pin）。"""
    _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    _platform = gatekeeper._platform
    _project_dir = f"{_platform.data_root}/{_BUSINESS_PROJECT}"
    _os.makedirs(_project_dir, exist_ok=True)

    _cid = str(_uuid.uuid4())
    _key = f"websocket:{_cid}"

    for _agent_name in gatekeeper.agent_names:
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
                "role": "user", "content": content,
                "timestamp": _now,
            },
        ])

        # Write webui transcript
        _platform.write_webui_transcript(_agent_name, _cid, [
            {"event": "delta", "text": content, "chat_id": _cid},
            {"event": "stream_end", "text": content, "chat_id": _cid},
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
    return _cid


# ── 入口 ────────────────────────────────────────────────────────


def ensure_business_management_chat(gatekeeper) -> None:
    """重建「业务管理」置顶会话：内容全部来自模块 sections()，无模块则不建。"""
    log = gatekeeper._log
    try:
        modules = discover_modules(log=log)
    except Exception as e:  # noqa: BLE001 - 发现层异常不得阻塞启动
        msg = f"⚠️ 模块发现异常（本次不建业务管理 chat）: {type(e).__name__}: {e}"
        log(msg)
        _stderr(msg)
        modules = []

    if not modules:
        log("ℹ️ 未发现 Legion Module，跳过业务管理 chat")
        return

    content = _build_module_content(gatekeeper, modules, log)
    if content is None:
        log("ℹ️ 模块未声明业务管理会话内容，跳过该 chat")
        return
    log(f"📊 业务管理 chat 由 {len(modules)} 个模块声明生成（{', '.join(m.name for m in modules)}）")

    _write_chat(gatekeeper, content)

    for _line in content.splitlines():
        if "钱包管理" in _line and ("打开" in _line or "/config/" in _line):
            log(f"[DIAG] business chat wallet entry: {_line.strip()}")
