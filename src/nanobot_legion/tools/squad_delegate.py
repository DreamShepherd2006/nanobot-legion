"""MCP server: delegate_to_agent + mode enforcement + VT research swarm.

Usage (MCP stdio server):
    python3 -m nanobot_legion.tools.squad_delegate

Tools:
    get_trading_mode() -> str
    delegate_to_agent(target_agent, message, timeout_seconds, enforce_mode) -> str
    run_research_swarm(symbol, max_iterations) -> str

Risk isolation: mode check ALWAYS runs first. OnchainOS pre-fetch is
embedded in the MCP server — no LLM coordination, no deviation.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

import websocket
from mcp.server.fastmcp import FastMCP

from nanobot_legion.squad_config_loader import load_config

# ── onchainos pre-fetch (optional, only needed for run_research_swarm) ──

try:
    from nanobot_quant.onchainos_cli import (
        extract_symbol as _extract_symbol,
        format_risk_level as _format_risk_level,
        get_advanced_info as _get_advanced_info,
        get_holders as _get_holders,
        get_price as _get_price,
        search_token as _search_token,
    )
    _HAS_ONCHAINOS_CLI = True
except ImportError:
    _HAS_ONCHAINOS_CLI = False

mcp = FastMCP("squad-delegate")

# ── mode enforcement ──────────────────────────────────────

# 默认「交易模式 → agent」映射（quand 空间零回归）。
# 可由 squad_config.json 的顶层 ``mode_map`` 覆盖：
#   - 键缺失 → 用本默认值
#   - 显式 ``{}`` → 关闭门控（非交易类空间）
#   - 类型非法 → 回退默认值并告警
DEFAULT_MODE_MAP = {
    "quant": "quant",
    "research": "vt_research",
}


def _warn(msg: str) -> None:
    """诊断输出走 stderr（stdout 是 MCP JSON-RPC 通道，不能污染）。"""
    print(f"[squad-delegate] {msg}", file=sys.stderr, flush=True)


def _mode_path() -> str:
    """Resolve mode.json path from squad_config.json data_root."""
    cfg = load_config()
    data_root = cfg.get("data_root", "/data")
    return os.path.join(data_root, "legion", "mode.json")


def _mode_map() -> dict:
    """生效的「模式 → agent」映射（squad_config.json 的 mode_map）。

    键缺失 = 默认值（兼容现有空间）；空 dict = 显式关闭门控；
    非 dict / 非法项 = 回退默认值并告警（fail-safe，不静默）。
    """
    try:
        raw = load_config().get("mode_map", None)
    except Exception as e:  # noqa: BLE001
        _warn(f"mode_map 读取失败，用默认映射: {type(e).__name__}: {e}")
        return dict(DEFAULT_MODE_MAP)
    if raw is None:
        return dict(DEFAULT_MODE_MAP)
    if not isinstance(raw, dict):
        _warn(f"mode_map 类型非法（{type(raw).__name__}），用默认映射")
        return dict(DEFAULT_MODE_MAP)

    out = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
            out[k.strip()] = v.strip()
        else:
            _warn(f"mode_map 项非法已忽略: {k!r} → {v!r}")
    return out


def _read_mode() -> str | None:
    """读 WebUI 保存的交易模式。

    文件缺失 / 坏 JSON / 空值 → ``None``（不再伪造 "quant"）；
    调用方用 ``_effective_mode()`` 决定缺省行为。
    """
    try:
        with open(_mode_path()) as f:
            mode = (json.load(f).get("mode") or "").strip()
            return mode or None
    except Exception:
        return None


def _effective_mode(mode_map: dict) -> str | None:
    """生效模式 = 已保存的模式；未保存则取 mode_map 首键（默认 map 下即 quant）。"""
    return _read_mode() or (next(iter(mode_map), None) if mode_map else None)


# ── config resolution ─────────────────────────────────────

def _resolve_target(target_alias: str) -> tuple:
    """Return (target_id, ws_port) from squad_config.json peers."""
    cfg = load_config()
    peers = cfg.get("peers", {})
    info = peers.get(target_alias) or peers.get(target_alias.lower())
    if not info or info.get("zone") == "archived":
        return None, None
    return info.get("id"), info.get("ws_port")


# ── ws delivery (shared) ──────────────────────────────────

def _deliver_via_ws(target_agent: str, target_id: str, ws_port: int,
                    message: str, timeout_seconds: int) -> str:
    """Connect to agent WS, send message, collect response until turn_end."""
    uri = f"ws://127.0.0.1:{ws_port}/"
    token = os.environ.get("NANOBOT_TOKEN", "").strip()
    if token:
        uri += f"?token={token}"

    corr_id = uuid.uuid4().hex[:8]

    try:
        ws = websocket.create_connection(uri, timeout=10)
    except Exception as e:
        return json.dumps({
            "status": "connection_error",
            "error": f"could not connect to {target_agent}:{ws_port}: {e}"
        })

    responses: list[str] = []
    start = time.time()

    try:
        greeting_raw = ws.recv()
        greeting = json.loads(greeting_raw)
        if greeting.get("event") != "ready":
            ws.close()
            return json.dumps({
                "status": "protocol_error",
                "error": f"expected 'ready', got {greeting.get('event')}"
            })

        payload = json.dumps({
            "type": "message",
            "chat_id": target_id,
            "content": message,
            "correlation_id": corr_id,
        }, ensure_ascii=False)
        ws.send(payload)

        while time.time() - start < timeout_seconds:
            try:
                ws.settimeout(10)
                raw = ws.recv()
                data = json.loads(raw)
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                return json.dumps({
                    "status": "read_error",
                    "error": str(e),
                    "partial": "\n".join(responses) if responses else None
                })

            event = data.get("event", "")
            if event == "turn_end":
                ws.close()
                reply = "\n".join(responses) if responses else "(empty)"
                return json.dumps({
                    "status": "ok",
                    "response": reply,
                    "target": target_agent,
                })
            elif event == "delta":
                text = data.get("text", "")
                if text:
                    responses.append(text)
            elif event == "error":
                ws.close()
                return json.dumps({
                    "status": "framework_error",
                    "error": data.get("detail", "unknown"),
                    "partial": "\n".join(responses) if responses else None
                })

        ws.close()
        return json.dumps({
            "status": "timeout",
            "response": "\n".join(responses) if responses else None,
            "target": target_agent,
        })

    except Exception as e:
        try:
            ws.close()
        except Exception:
            pass
        return json.dumps({
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "partial": "\n".join(responses) if responses else None
        })


# ── tools ─────────────────────────────────────────────────

@mcp.tool()
async def get_trading_mode() -> str:
    """Read the current trading mode from WebUI settings.

    Returns JSON with the effective mode, the expected agent, whether the
    delegation gate would actually reject, and a ``hint`` when the mode has
    never been saved (Legion no longer fabricates "quant" silently).
    Use this before any trading-related delegation to know which agent to target.
    """
    mode_map = _mode_map()
    saved = _read_mode()
    mode = saved or (next(iter(mode_map), None) if mode_map else None)
    agent = mode_map.get(mode) if mode else None
    payload = {
        "mode": mode,
        "expected_agent": agent,
        "enforced": bool(mode_map) and agent is not None,
        "mode_map_keys": list(mode_map),
        "saved": saved is not None,
    }
    if saved is None:
        payload["hint"] = (
            f"尚未保存交易模式，当前按 mode_map 首键（{mode or '无'}）处理，"
            "请在 /config/mode 选择并保存"
        )
    return json.dumps(payload, ensure_ascii=False)


@mcp.tool()
async def delegate_to_agent(
    target_agent: str,
    message: str,
    timeout_seconds: int = 120,
    enforce_mode: bool = False,
) -> str:
    """Send a message to another squad agent and wait for its response.

    Parameters:
        target_agent: Agent name (e.g. "vt_research", "quant").
        message: The message to send.
        timeout_seconds: Max wait (default 120s). Use 1800+ for swarm debates.
        enforce_mode: When True, read WebUI mode.json and REJECT if
                      target_agent doesn't match. The LLM's suggestion is
                      overruled by the WebUI setting — this is the risk gate.

    Returns:
        JSON with status, response, and enforcement details.
    """
    # ── mode enforcement gate ──
    # mode_map 空 → 关闭门控；模式未知 → 不拦（避免非交易空间被锁死）；
    # 命中且目标不匹配 → 拒绝（风险门保留，行为与改造前一致）。
    enforcement = {}
    if enforce_mode:
        mode_map = _mode_map()
        current_mode = _effective_mode(mode_map)
        expected = mode_map.get(current_mode) if current_mode else None
        if not mode_map:
            enforcement = {"enforced": False, "skipped": "未配置 mode_map"}
            _warn("enforce_mode 已跳过：未配置 mode_map（门控关闭）")
        elif expected is None:
            enforcement = {
                "enforced": False,
                "skipped": f"未知模式 {current_mode}",
                "current_mode": current_mode,
            }
            _warn(f"enforce_mode 已跳过：未知模式 {current_mode!r}")
        elif target_agent.lower() != expected.lower():
            return json.dumps({
                "status": "rejected",
                "error": (
                    f"Mode mismatch: WebUI is set to {current_mode.upper()} mode "
                    f"(expected agent: {expected}), "
                    f"but you specified '{target_agent}'. "
                    f"Please correct your target or contact the administrator."
                ),
                "current_mode": current_mode,
                "expected_agent": expected,
                "requested_agent": target_agent,
            })
        else:
            enforcement = {
                "enforced": True,
                "current_mode": current_mode,
                "expected_agent": expected,
            }

    target_id, ws_port = _resolve_target(target_agent)
    if not target_id or not ws_port:
        return json.dumps({
            "status": "error",
            "error": f"agent '{target_agent}' not found in squad roster"
        })

    result = _deliver_via_ws(target_agent, target_id, ws_port, message, timeout_seconds)
    if enforcement:
        try:
            payload = json.loads(result)
            if isinstance(payload, dict):
                payload["mode_enforcement"] = enforcement
                return json.dumps(payload, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - 非 JSON 返回原样透传
            pass
    return result


# ── run_research_swarm ────────────────────────────────────

# Trading pair suffixes to append when user gives a bare symbol.
_PAIR_SUFFIXES = ("-USDT", "-USD", "-USDC")


def _normalize_pair(raw: str) -> str:
    """BTC → BTC-USDT, ETH-USD → ETH-USD, SPCX → SPCX."""
    stripped = raw.strip().upper()
    if not stripped:
        return raw
    for suffix in _PAIR_SUFFIXES:
        if stripped.endswith(suffix):
            return stripped
    # Bare symbol → assume crypto with -USDT
    if any(stripped.endswith(f".{ex}") for ex in ("US", "HK", "L")):
        return stripped  # stock suffix, keep as-is
    return f"{stripped}-USDT"


def _pre_fetch_onchainos(symbol: str) -> dict:
    """Pre-fetch onchain data for a bare symbol. Returns {ok, errors, data}."""
    errors: list[str] = []
    data: dict[str, object] = {}

    if not _HAS_ONCHAINOS_CLI:
        return {"ok": False, "errors": ["onchainos_cli module not available"], "data": data}

    # ① search token → address
    addr = _search_token(symbol)
    if not addr:
        errors.append(f"token '{symbol}' not found on chain")
        return {"ok": False, "errors": errors, "data": data}
    data["address"] = addr

    # ② market price
    price_val = _get_price(addr)
    if price_val:
        data["price"] = price_val
    else:
        errors.append("price unavailable")

    # ③ advanced-info (risk)
    risk_raw = _get_advanced_info(addr)
    if risk_raw:
        try:
            data["risk"] = _format_risk_level(risk_raw)
        except Exception:
            errors.append("risk parse failed")
    else:
        errors.append("risk unavailable")

    # ④ holders (top 100)
    holders = _get_holders(addr)
    if holders:
        data["holders_count"] = len(holders)
        # Collect top-10 hold percent
        top_hold = sum(
            float(h.get("holdPercent", 0)) for h in holders[:10]
            if isinstance(h, dict)
        )
        data["top10_hold_pct"] = round(top_hold, 1)
    else:
        errors.append("holders unavailable")

    return {
        "ok": len(data) > 0,
        "errors": errors,
        "data": data,
    }


@mcp.tool()
async def run_research_swarm(
    symbol: str,
    max_iterations: int = 50,
    timeout_seconds: int = 300,
) -> str:
    """Run VT swarm investment committee analysis with onchain data pre-fetch.

    THIS is the Research-mode entry point. It:
    1. Checks mode.json → rejects if not 'research' mode
    2. Normalizes symbol (BTC → BTC-USDT)
    3. Pre-fetches onchain data (price, risk, holders) via onchainos CLI
    4. Builds enriched variables and delegates to vt_research

    Parameters:
        symbol: Token symbol, e.g. "BTC", "SPCX", "ETH-USD"
        max_iterations: Max swarm iterations (default 50, range 3-100)
        timeout_seconds: WS delegate timeout (default 300s; swarm runs async
                         after launch, this just waits for the run_id)

    Returns:
        JSON with run_id, pre_fetch result, and status.
    """
    # ━━ ① mode check ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    current_mode = _read_mode()
    if current_mode != "research":
        return json.dumps({
            "status": "rejected",
            "error": (
                f"run_research_swarm requires Research mode, "
                f"but current mode is '{current_mode or '未保存'}'. "
                f"Switch mode in WebUI or use delegate_to_agent directly."
            ),
            "current_mode": current_mode,
        })

    # ━━ ② symbol normalize ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    pair = _normalize_pair(symbol)

    # ━━ ③ onchainos pre-fetch ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    bare = pair.split("-")[0].split(".")[0] if "-" in pair or "." in pair else pair
    pre_fetch = _pre_fetch_onchainos(bare)

    # ━━ ④ build variables for swarm ━━━━━━━━━━━━━━━━━━━━━━━━
    variables: dict[str, object] = {
        "target": pair,
        "market": "crypto" if not any(
            bare.endswith(f".{ex}") for ex in ("US", "HK", "L")
        ) else "stock",
        "max_iterations": str(max_iterations),
    }
    if pre_fetch["ok"]:
        variables["onchainos"] = pre_fetch["data"]

    # ━━ ⑤ WS delegate → vt_research ━━━━━━━━━━━━━━━━━━━━━━━
    target_agent = "vt_research"
    target_id, ws_port = _resolve_target(target_agent)
    if not target_id or not ws_port:
        return json.dumps({
            "status": "error",
            "error": f"agent '{target_agent}' not found in squad roster",
            "pre_fetch": pre_fetch,
        })

    message = (
        f"Run the full research-to-execution chain for {pair}.\n"
        f"Use the signal-structurizer tool **run_research_chain** with "
        f"symbol=\"{pair}\" and max_iterations={max_iterations}.\n"
        f"Do NOT use run_swarm, do NOT call structurize_signal / "
        f"run_td_sequential / execute_signal manually.\n"
        f"run_research_chain starts the investment_committee swarm and, "
        f"once the debate completes, automatically chains structurize -> "
        f"TD check -> execute.\n"
        f"IMPORTANT: Report ONLY the run_id (format: 'run_id: swarm-xxxxx'). "
        f"Do NOT poll for status. Do NOT wait for the swarm to finish."
    )
    result_raw = _deliver_via_ws(target_agent, target_id, ws_port, message, timeout_seconds)

    # ━━ ⑥ parse and return ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        result = json.loads(result_raw)
    except Exception:
        result = {"status": "parse_error", "raw": result_raw[:500]}

    result["pre_fetch"] = pre_fetch
    result["symbol"] = pair
    return json.dumps(result, ensure_ascii=False)


# ── entry ─────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
