"""MCP server: delegate_to_agent + mode enforcement for squad trading.

Usage (MCP stdio server):
    python3 -m nanobot_legion.tools.squad_delegate

Tools:
    get_trading_mode() -> str
    delegate_to_agent(target_agent, message, timeout_seconds, enforce_mode) -> str

Risk isolation: when enforce_mode=True, the tool reads WebUI trading mode
from mode.json and REJECTS any target that doesn't match. Neo's suggestion
is treated as advisory only — the code is the gatekeeper.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import websocket
from mcp.server.fastmcp import FastMCP

from nanobot_legion.squad_config_loader import load_config

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


# ── entry ─────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
