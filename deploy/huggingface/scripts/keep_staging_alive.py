#!/usr/bin/env python3
"""
keep_staging_alive.py — Persistent WebSocket to Staging HF Space Neo
Prevents free-tier sleep by maintaining a long-lived WS connection.

Mechanism:
  1. GET Staging /webui/bootstrap → fetch WS token
  2. Connect via wss:// (this external HTTP/WS traffic resets HF sleep timer)
  3. Auto ping/pong + reconnect on disconnect

Nightly Neo → WS → Staging Neo: Staging HF detects external activity → no sleep.
"""

import json
import logging
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
import ssl
from urllib.parse import urlparse

import websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [KSA] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("keep_staging_alive")

# ── Configuration ──────────────────────────────────────────────
STAGING_URL = os.environ.get(
    "STAGING_URL",
    "https://DreamShepherd2006-Nanobot-Staging.hf.space",
)
BOOTSTRAP_TIMEOUT = 60       # staging may be asleep → long timeout
BOOTSTRAP_RETRIES = 20       # try for ~10 min (20 × 30s)
BOOTSTRAP_DELAY = 30
RECONNECT_DELAY = 45
PING_INTERVAL = 55           # websocket-client auto ping
PING_TIMEOUT = 20


# ── Bootstrap ───────────────────────────────────────────────────
def get_token() -> tuple[str | None, str]:
    """GET /webui/bootstrap → extract token + ws_path.

    When Staging is asleep, HF returns 503 or hangs until the
    container wakes up. We retry with generous timeouts.
    """
    url = f"{STAGING_URL}/webui/bootstrap"
    for attempt in range(1, BOOTSTRAP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, method="GET")
            # Allow HF's internal TLS cert
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(
                req, timeout=BOOTSTRAP_TIMEOUT, context=ctx
            ) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                token = data.get("token")
                ws_path = data.get("ws_path", "/")
                if token:
                    log.info(
                        f"✓ Bootstrap OK (attempt {attempt}): "
                        f"ws_path={ws_path}"
                    )
                    return token, ws_path
        except urllib.error.HTTPError as e:
            status = e.code
            log.warning(
                f"Bootstrap {attempt}/{BOOTSTRAP_RETRIES}: "
                f"HTTP {status} (staging may be waking up)"
            )
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning(
                f"Bootstrap {attempt}/{BOOTSTRAP_RETRIES}: "
                f"{type(e).__name__}: {e}"
            )
        except Exception:
            log.warning(
                f"Bootstrap {attempt}/{BOOTSTRAP_RETRIES}: "
                f"{traceback.format_exc()}"
            )

        if attempt < BOOTSTRAP_RETRIES:
            time.sleep(BOOTSTRAP_DELAY)

    log.error("Bootstrap exhausted all retries")
    return None, "/"


# ── WebSocket handlers ──────────────────────────────────────────
def on_open(ws: websocket.WebSocketApp) -> None:
    log.info("✓ WebSocket connected to Staging Neo")


def on_message(ws: websocket.WebSocketApp, raw: str) -> None:
    try:
        data = json.loads(raw)
        event = data.get("event", "")
        if event == "ready":
            log.info("  ← Staging Neo ready — keep-alive active")
        elif event == "heartbeat":
            ws.send(json.dumps({"type": "pong"}))
    except json.JSONDecodeError:
        pass


def on_error(ws: websocket.WebSocketApp, error: Exception) -> None:
    log.warning(f"WS error: {error}")


def on_close(
    ws: websocket.WebSocketApp, status: int | None, msg: str | None
) -> None:
    log.info(f"WS closed: {status} {msg}")


# ── Main loop ───────────────────────────────────────────────────
def run() -> None:
    log.info(f"Keep Staging Alive — target: {STAGING_URL}")

    while True:
        try:
            token, ws_path = get_token()
            if not token:
                log.warning(f"No token → retrying in {RECONNECT_DELAY}s")
                time.sleep(RECONNECT_DELAY)
                continue

            parsed = urlparse(STAGING_URL)
            ws_url = f"wss://{parsed.netloc}{ws_path}?token={token}"
            log.info(f"Connecting: {ws_url}")

            ws = websocket.WebSocketApp(
                ws_url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            ws.run_forever(
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
                sslopt={"cert_reqs": ssl.CERT_NONE},
            )

        except Exception:
            log.error(f"Fatal: {traceback.format_exc()}")

        log.info(f"Reconnecting in {RECONNECT_DELAY}s …")
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    run()
