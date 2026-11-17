"""Squad gatekeeper — business management pinned chat initialization.

置顶会话「业务管理」的内容来源（步 3，Legion Module 契约）：

1. **模块优先**：发现已装的 Legion Module，取其 ``sections()`` 结构化内容，
   由 Legion 统一渲染（模块不返回预拼 Markdown）。
2. **兜底**：未发现任何模块时，走旧路径（直连 ``nanobot_quant`` 读配置拼内容），
   与改造前行为一致；步 4 删除兜底。

会话在网关启动时重建（与现行为一致），模块不直接读写 session / sidebar。
"""

from __future__ import annotations

import json as _json
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
    """核心区块 + 各模块 sections() 渲染；全部模块渲染失败返回 None（走兜底）。"""
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


# ── 兜底路径（旧实现，步 4 删除）──────────────────────────────────


def _build_legacy_content(gatekeeper) -> str | None:
    """旧硬编码业务管理内容；nanobot_quant 未安装或无凭证类型时返回 None。"""
    try:
        from nanobot_quant.credential_registry import discover as _discover_credentials
    except ImportError:
        gatekeeper._log("📊 nanobot_quant 未安装，跳过业务管理 chat")
        return None

    _specs = _discover_credentials()
    if not _specs:
        gatekeeper._log("📊 无凭证类型注册，跳过业务管理 chat")
        return None

    gatekeeper._log(f"📊 发现 {len(_specs)} 种凭证类型，创建业务管理 chat")

    _platform = gatekeeper._platform

    _cred_rows = "\n".join(
        f"| {_spec.icon} {_spec.display} | [配置凭证](/config/credentials/{_name}) |"
        for _name, _spec in _specs.items()
    )

    _mode_path = f"{_platform.data_root}/legion/mode.json"
    try:
        with open(_mode_path) as f:
            _mode_data = _json.load(f)
        _mode = _mode_data.get("mode", "quant")
    except Exception:
        _mode = "quant"
    _mode_labels = {"quant": "📊 Quant 模式（TD Sequential）", "research": "🧠 Research 模式（VT Swarm）"}
    _mode_label = _mode_labels.get(_mode, _mode)

    _live_path = f"{_platform.data_root}/credentials/live.json"
    try:
        with open(_live_path) as f:
            _live_data = _json.load(f)
        _live = bool(_live_data.get("live", False))
    except Exception:
        _live = False
    _live_label = "🟢 实盘交易已开启" if _live else "⚪ 实盘关闭（dry-run，不成交）"

    _opt_path = f"{_platform.data_root}/credentials/okx_options_params.json"
    _opt_on: bool | None = None
    try:
        with open(_opt_path) as f:
            _opt = _json.load(f).get("live") or {}
        _opt_on = bool(_opt.get("enabled", False))
    except Exception:
        _opt_on = None  # 期权模块未装/无配置 → 状态区不显示期权巡检项

    _status_lines = [
        f"| 交易模式 | {_mode_label} | [切换](/config/mode) |",
        f"| 实盘开关 | {_live_label} | [配置](/config/live) |",
    ]
    if _opt_on is not None:
        _opt_label = "🟤 期权巡检 开启" if _opt_on else "🟤 期权巡检 未开启"
        _status_lines.append(f"| 期权巡检 | {_opt_label} | [期权页](/config/okx-options) |")

    return f"""\
# ⚙️ 当前状态

| 项 | 状态 | 入口 |
|---|---|---|
{chr(10).join(_status_lines)}

> 各分类入口见下方分组；状态开关即时生效，无需重启空间。

---

# ⚙️ 交易控制

| 项 | 说明 | 入口 |
|---|---|---|
| 交易模式 | Quant（TD 确定性）/ Research（VT Swarm 辩论）二选一 | [切换](/config/mode) |
| 实盘开关 | WebUI 唯一授权（dry-run 不成交 / live 上链） | [配置](/config/live) |
| 执行参数 | 风控上限 / 执行通道 / 标的池 / 场景 / TD 启停 | [打开](/config/exec) |

---

# 📐 策略与信号

| 项 | 说明 | 入口 |
|---|---|---|
| 策略选择 | 选择 Quant 路线的信号策略（TD 变体等） | [打开](/config/strategy) |
| TD 策略参数 | TD 算法核心参数（权重 / setup / countdown） | [打开](/config/td-params) |
| TD 序列分析 | setup/countdown/TDST/score 轨迹 + 历史信号统计 | [打开](/config/td-table) |

---

# 📊 回测验证

场景回放引擎（与实盘同一策略决策代码）：Gate CEX 历史 K 线 + 模拟撮合（手续费/滑点/分批），输出 ROI/成交明细/每 slot 净值。

→ [打开回测](/config/backtest)

---

# 🔑 账户与资产

{_cred_rows}

| 项 | 说明 | 入口 |
|---|---|---|
| 钱包管理 | DEX（Agentic Wallet 子钱包）与 CEX（Gate 子账号）分两大类 | [打开](/config/wallets) |
| 代币地址 | tokens.json 登记（确认门控，执行前校验） | [打开](/config/tokens) |

---

# 🟤 OKX 期权

U 本位线性期权（USDSⓈ-M，美元现金结算）：期权链 + IV/HV + 卖 put 定价辅助；持仓/台账监控；逐仓担保自动追加；到期巡检自动判定（OTM 作废关账 / ITM 赔付补买）。

→ [打开期权链](/config/okx-options)

---

> 💡 **提示**：凭证文件存储在 {_platform.data_root}/credentials/ 目录下，容器重启后保留。
"""


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
    """重建「业务管理」置顶会话：模块优先，未发现模块时回退旧硬编码。"""
    log = gatekeeper._log
    try:
        modules = discover_modules(log=log)
    except Exception as e:  # noqa: BLE001 - 发现层异常不得阻塞启动
        msg = f"⚠️ 模块发现异常（业务管理 chat 走兜底）: {type(e).__name__}: {e}"
        log(msg)
        _stderr(msg)
        modules = []

    content = None
    if modules:
        content = _build_module_content(gatekeeper, modules, log)
        if content:
            log(f"📊 业务管理 chat 由 {len(modules)} 个模块声明生成（{', '.join(m.name for m in modules)}）")
        else:
            log("⚠️ 模块未提供会话内容，回退旧硬编码渲染")

    if content is None:
        content = _build_legacy_content(gatekeeper)
    if content is None:
        return

    _write_chat(gatekeeper, content)

    for _line in content.splitlines():
        if "钱包管理" in _line and ("打开" in _line or "/config/" in _line):
            log(f"[DIAG] business chat wallet entry: {_line.strip()}")
