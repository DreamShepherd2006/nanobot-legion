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

    # ── Read trading mode ────────────────────────────────────
    _mode_path = f"{_platform.data_root}/legion/mode.json"
    try:
        with open(_mode_path) as f:
            _mode_data = _json.load(f)
        _mode = _mode_data.get("mode", "quant")
    except Exception:
        _mode = "quant"
    _mode_labels = {"quant": "📊 Quant 模式（TD Sequential）", "research": "🧠 Research 模式（VT Swarm）"}
    _mode_label = _mode_labels.get(_mode, _mode)

    # ── Read live trading toggle ──────────────────────────────
    _live_path = f"{_platform.data_root}/credentials/live.json"
    try:
        with open(_live_path) as f:
            _live_data = _json.load(f)
        _live = bool(_live_data.get("live", False))
    except Exception:
        _live = False
    _live_label = "🟢 实盘交易已开启" if _live else "⚪ 实盘关闭（dry-run，不成交）"

    # ── Read options inspector status (optional module) ─────
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

    # ── Build business management content ─────────────────────
    _content = f"""\
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
    for _line in _content.splitlines():
        if "钱包管理" in _line and ("打开" in _line or "/config/" in _line):
            gatekeeper._log(f"[DIAG] business chat wallet entry: {_line.strip()}")
