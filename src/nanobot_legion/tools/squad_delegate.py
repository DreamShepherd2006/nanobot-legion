"""MCP server: delegate_to_agent tool for squad internal communication.

Usage (MCP stdio server):
    python3 -m nanobot_legion.tools.squad_delegate

Tools:
    delegate_to_agent(target_agent, message, timeout_seconds) -> str

The server connects directly to the target agent's WebSocket, sends a message,
and waits for the full response (turn_end). No relay token needed — same container,
direct WS connection.
"""

from __future__ import annotations

import json
import sys
import time
import uuid

from mcp.server.fastmcp import FastMCP
from nanobot_legion.squad_config_loader import load_config

import os
import websocket

mcp = FastMCP("squad-delegate")

# ── config resolution ─────────────────────────────────────

def _resolve_target(target_alias: str) -> tuple:
    """Return (target_id, ws_port) from squad_config.json peers."""
    cfg = load_config()
    peers = cfg.get("peers", {})
    info = peers.get(target_alias) or peers.get(target_alias.lower())
    if not info or info.get("zone") == "archived":
        return None, None
    return info.get("id"), info.get("ws_port")


# ── tools ─────────────────────────────────────────────────

@mcp.tool()
async def delegate_to_agent(
    target_agent: str,
    message: str,
    timeout_seconds: int = 120,
) -> str:
    """Send a message to another squad agent and wait for its response.

    Parameters:
        target_agent: Agent name as listed in squad roster (e.g. "vt_research", "quant").
        message: The message to send to the target agent.
        timeout_seconds: Maximum wait for response (default 120s).
                         Set higher for long-running tasks like swarm debates (1800+).

    Returns:
        The target agent's full response text, or an error message.
    """
    target_id, ws_port = _resolve_target(target_agent)
    if not target_id or not ws_port:
        return json.dumps({
            "status": "error",
            "error": f"agent '{target_agent}' not found in squad roster"
        })

    uri = f"ws://127.0.0.1:{ws_port}/"
    token = os.environ.get("NANOBOT_TOKEN", "").strip()
    if token:
        uri += f"?token={token}"

    corr_id = uuid.uuid4().hex[:8]

    # Connect
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
        # Handshake
        greeting_raw = ws.recv()
        greeting = json.loads(greeting_raw)
        if greeting.get("event") != "ready":
            ws.close()
            return json.dumps({
                "status": "protocol_error",
                "error": f"expected 'ready', got {greeting.get('event')}"
            })

        # Send message
        payload = json.dumps({
            "type": "message",
            "chat_id": target_id,
            "content": message,
            "correlation_id": corr_id,
        }, ensure_ascii=False)
        ws.send(payload)

        # Read response
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

        # Timeout — return partial
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


# ── entry ─────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
