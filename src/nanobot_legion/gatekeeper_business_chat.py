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

    # ── Build business management content ─────────────────────
    _content = f"""\
# ⚙️ 交易模式

当前：**{_mode_label}**

→ [切换模式](/config/mode)

---

# ⚡ 实盘交易开关

当前：**{_live_label}**

→ [配置实盘开关](/config/live)

---

# 🔑 API 凭证管理

配置交易所、数据源的 API 凭证，保存后即时生效。

{_cred_rows}

---

# 👛 钱包管理

DEX 钱包（OKX Agentic Wallet：登录状态、地址、余额、子钱包、转账）与 CEX 账户（Gate：slot↔子账号映射、余额、主→子划转）分两大类管理。

→ [打开钱包管理](/config/wallets)

---

# 🪙 代币地址管理

管理自定义代币地址（tokens.json）。录入不等于信任——地址有疑问（链不匹配/格式错误）的条目在执行前会被拦截，需确认后才放行；改地址后确认自动重置。

→ [打开代币管理](/config/tokens)

---

# 📐 TD 策略参数

TD Sequential 核心参数（DeMark 算法 + 自研评分权重 + 策略规则）。修改即保存到 td_params.json 并即时生效；默认值 = 旧版硬编码行为，未改动时零变化。权重合计必须 = 1.0。

→ [打开 TD 参数管理](/config/td-params)

---

# 📈 策略选择

选择 Quant 路线使用的确定性信号策略（当前为 TD Sequential 两种口径变体，未来接入更多策略）。路线（mode.json）管流程形态，策略（strategy.json）管用哪个策略；切换即时生效，无需重启。

→ [打开策略选择](/config/strategy)

---

# 🛡️ 执行参数

链上执行环节的系统级策略（单仓上限/回撤/止损/滑点/SOL 缓冲）。WebUI 锁死，MCP/LLM 不可改；修改即时生效，默认值 = 旧版硬编码行为。

→ [打开执行参数](/config/exec)

---

# 📊 TD 序列分析

实时查看当前策略的 setup/countdown/TDST/score 轨迹（随 K 线呈现 1-9 进度），以及历史区间内所有 9 信号的后续表现统计——辅助判断趋势变化，不构成买卖交易。

→ [打开 TD 序列分析](/config/td-table)

---

> 💡 **提示**：凭证文件存储在 `{_platform.data_root}/credentials/` 目录下，容器重启后保留。
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
