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

# Maps trading mode → squad agent name
MODE_TO_AGENT = {
    "quant": "quant",
    "research": "vt_research",
}


def _mode_path() -> str:
    """Resolve mode.json path from squad_config.json data_root."""
    cfg = load_config()
    data_root = cfg.get("data_root", "/data")
    return os.path.join(data_root, "legion", "mode.json")


def _read_mode() -> str:
    """Read current trading mode, default 'quant'."""
    try:
        with open(_mode_path()) as f:
            return json.load(f).get("mode", "quant")
    except Exception:
        return "quant"


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

    Returns JSON with the current mode and the expected agent.
    Use this before any trading-related delegation to know which agent to target.
    """
    mode = _read_mode()
    agent = MODE_TO_AGENT.get(mode, "quant")
    return json.dumps({
        "mode": mode,
        "expected_agent": agent,
    })


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
    if enforce_mode:
        current_mode = _read_mode()
        expected = MODE_TO_AGENT.get(current_mode, "quant")
        if target_agent.lower() != expected.lower():
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

    target_id, ws_port = _resolve_target(target_agent)
    if not target_id or not ws_port:
        return json.dumps({
            "status": "error",
            "error": f"agent '{target_agent}' not found in squad roster"
        })

    return _deliver_via_ws(target_agent, target_id, ws_port, message, timeout_seconds)


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
                f"but current mode is '{current_mode}'. "
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
        f"Start a swarm analysis for {pair}. "
        f"Use run_swarm with preset=investment_committee, start_only=true, "
        f"and these variables:\n"
        f"{json.dumps(variables, ensure_ascii=False)}\n\n"
        f"IMPORTANT: After starting the swarm, report ONLY the run_id "
        f"(format: 'run_id: swarm-xxxxx'). Do NOT poll for status. "
        f"Do NOT wait for the swarm to finish."
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
