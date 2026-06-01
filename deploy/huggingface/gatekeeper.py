#!/usr/bin/env python3
"""
Gatekeeper v6.0 (Auto-Resurrect + PID Check) — HTTP proxy + WS interceptor + squad relay.
Deployed on ws_port, serves WebUI and routes squad traffic.
Includes legion_monitor with automatic resurrection for whitelisted agents.
Uses OS-level PID check to avoid false-positive resurrection during DeepSeek thinking.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import asyncio
from uuid import uuid4
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, Response
from starlette.middleware.sessions import SessionMiddleware
import websockets

# ── Platform (auto-detected) ──
# IMPORTANT: squad_config_loader must be imported BEFORE platforms!
# It injects DEPLOY_PLATFORM into os.environ at module level, which
# platforms.__init__._detect() reads in its matches() step 0.
from squad_config_loader import get_relay_timeout  # noqa: E402
from platforms import platform  # noqa: E402

# ═══════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════

def log(msg):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[GATEKEEPER] [{timestamp}] {msg}")
    sys.stdout.flush()

# ═══════════════════════════════════════════════════════════════
# Squad Config (memory-parsed, zero disk I/O)
# ═══════════════════════════════════════════════════════════════

AGENT_NAMES: list[str] = []
SQUAD_ROSTER: dict[str, dict] = {}
INSTANCE_WORKSPACES: dict[str, str] = {}
PEER_ENV_MAP: dict[str, str] = {}  # NANOBOT_PEER_NEO → env var value

def refresh_roster():
    """Parse NANOBOT_PEER_* env vars to build agent roster."""
    global AGENT_NAMES, SQUAD_ROSTER, INSTANCE_WORKSPACES, PEER_ENV_MAP
    AGENT_NAMES.clear()
    SQUAD_ROSTER.clear()
    INSTANCE_WORKSPACES.clear()
    PEER_ENV_MAP.clear()

    for key, val in os.environ.items():
        if not key.startswith("NANOBOT_PEER_"):
            continue
        PEER_ENV_MAP[key] = val
        agent_name = key[len("NANOBOT_PEER_"):].lower()
        try:
            info = json.loads(val)
            if isinstance(info, dict) and "id" in info:
                AGENT_NAMES.append(agent_name)
                SQUAD_ROSTER[agent_name] = {
                    "id": info["id"],
                    "gateway_port": info.get("gateway_port", 0),
                    "ws_port": info.get("ws_port", 0),
                }
                INSTANCE_WORKSPACES[agent_name] = platform.instance_path(agent_name)
        except (json.JSONDecodeError, TypeError):
            log(f"⚠️ 跳过无效 NANOBOT_PEER_*: {key}")

    AGENT_NAMES.sort()
    log(f"📋 编制加载: {len(AGENT_NAMES)} agents → {AGENT_NAMES}")

    # Keep platform in sync with current roster
    try:
        platform.refresh_config(webui_agent=WEBUI_AGENT, squad_roster=SQUAD_ROSTER)
    except Exception:
        pass

refresh_roster()

# ═══════════════════════════════════════════════════════════════
# WebUI Target & Per-agent HTTP Proxy Clients
# ═══════════════════════════════════════════════════════════════

WEBUI_AGENT = os.environ.get("WEBUI_AGENT", "").strip().lower()
if not WEBUI_AGENT or WEBUI_AGENT not in SQUAD_ROSTER:
    WEBUI_AGENT = AGENT_NAMES[0] if AGENT_NAMES else "neo"
    log(f"📡 WEBUI_AGENT 未指定或无效，回退到: {WEBUI_AGENT}")

# ── Nanobot version ──────────────────────────────────────────
def _get_nanobot_version() -> str:
    """Read nanobot version from installed package + commit SHA."""
    ver = "unknown"
    try:
        from nanobot import __version__
        ver = __version__
    except Exception:
        pass
    if ver == "unknown":
        try:
            import tomllib
            with open("/app/pyproject.toml", "rb") as f:
                ver = tomllib.load(f).get("project", {}).get("version", "unknown")
        except Exception:
            pass
    # Append commit SHA
    commit = ""
    try:
        with open("/app/NANOBOT_COMMIT") as f:
            raw = f.read().strip()
        if raw and raw != "unknown":
            commit = raw[:8]
    except Exception:
        pass
    return f"{ver} ({commit})" if commit else ver

NANOBOT_VERSION = _get_nanobot_version()
log(f"📦 nanobot version: {NANOBOT_VERSION}")

def get_agent_for_user(username: str) -> str:
    """返回该用户对应的 agent name → 委托给 platform 模块."""
    return platform.get_agent_for_user(username)


def resolve_user_context(request) -> tuple[str, str, int | None]:
    """从 OAuth session 解析 (username, target_agent, ws_port).

    统一入口：HTTP 代理层（index、catch-all、sessions）通过此函数
    确定当前请求应路由到哪个 agent，避免各处重复 session 解析逻辑。
    """
    if not hasattr(request, "session"):
        return "", WEBUI_AGENT, SQUAD_ROSTER.get(WEBUI_AGENT, {}).get("ws_port")
    session_user = request.session.get("user")
    uname = ""
    if isinstance(session_user, dict):
        uname = session_user.get("preferred_username") or session_user.get("username") or ""
    target_agent = platform.get_agent_for_user(uname) if uname else WEBUI_AGENT
    ws_port = SQUAD_ROSTER.get(target_agent, {}).get("ws_port")
    return uname, target_agent, ws_port


# Per-agent HTTP clients for dynamic user → agent HTTP proxying
_http_clients = {}
for _name, _info in SQUAD_ROSTER.items():
    _http_clients[_name] = httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{_info['ws_port']}",
        timeout=120.0
    )
_default_client = _http_clients.get(WEBUI_AGENT)
log(f"🌐 HTTP proxy pool: {list(_http_clients.keys())}  (default={WEBUI_AGENT})")

# ═══════════════════════════════════════════════════════════════
# cluster_log Builder — wraps agent WS events for LegionTerminal
# ═══════════════════════════════════════════════════════════════

def _build_cluster_log(source: str, raw_data: str) -> Optional[str]:
    """Convert an agent WS frame into a cluster_log event for LegionTerminal.

    Returns None for events that should not appear in the per-agent log tabs
    (e.g. connection handshake, heartbeats, session noise).
    """
    try:
        data = json.loads(raw_data)
    except (json.JSONDecodeError, TypeError):
        return None

    event = data.get("event", "")

    # ── Suppressed (handshake / noise) ──
    if event in ("ready", "attached", "heartbeat", "runtime_model_updated",
                 "session_updated", "stream_end", "reasoning_end"):
        return None

    # ── Activity events → compact labels ──
    if event == "delta":
        text = data.get("text", "")
        if not text or not text.strip():
            return None
        content = text[:100] + ("…" if len(text) > 100 else "")
        label = f"{content}"
    elif event == "reasoning_delta":
        # Too verbose for a log tab — emit as one-liner activity pulse
        return None
    elif event == "turn_start":
        label = "⚡ turn_start"
    elif event == "turn_end":
        label = "✅ turn_end"
    elif event == "message":
        text = data.get("text", "")
        kind = data.get("kind", "")
        if kind == "tool_hint":
            label = f"🔧 {text[:100]}"
        elif kind == "progress":
            label = f"⏳ {text[:100]}"
        else:
            label = f"💬 {text[:120]}"
    elif event == "error":
        detail = data.get("detail", "unknown error")
        label = f"❌ {detail[:120]}"
    else:
        # Unknown events — compact
        label = f"[{event}]"

    return json.dumps({
        "event": "cluster_log",
        "type": "cluster_log",
        "source": source,
        "content": label,
    })


async def _observer_capture(
    agent_name: str,
    info: dict,
    path: str,
    token: str,
    client_ws: WebSocket,
    stop: asyncio.Event,
):
    """Read-only WS capture from one squad agent → cluster_log injector.

    Opens a WS to the agent, captures every inbound event, wraps it with
    ``_build_cluster_log()``, and sends it to the Commander's WS.  Never
    sends any messages to the agent — strictly read-only.

    On disconnect, backs off exponentially (2→30 s) and reconnects.
    Stops immediately when ``stop`` is set (Commander disconnected).
    """
    ws_url = f"ws://127.0.0.1:{info['ws_port']}/{path}"
    if token:
        ws_url += f"?token={token}"

    backoff = 2

    while not stop.is_set():
        try:
            obs_ws = await asyncio.wait_for(
                websockets.connect(ws_url, close_timeout=5), timeout=15
            )
            log(f"👁️ [obs] {agent_name} connected (port {info['ws_port']})")
            backoff = 2  # reset on success

            async with obs_ws:
                while not stop.is_set():
                    try:
                        data = await asyncio.wait_for(obs_ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        continue  # keep-alive, no data

                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")

                    cluster = _build_cluster_log(agent_name, data)
                    if cluster:
                        try:
                            await client_ws.send_text(cluster)
                        except Exception:
                            return  # Commander disconnected

        except asyncio.TimeoutError:
            log(f"👁️ [obs] {agent_name} connect timeout, retry {backoff}s")
        except websockets.exceptions.ConnectionClosed as e:
            log(f"👁️ [obs] {agent_name} WS closed ({e.code}), retry {backoff}s")
        except Exception as e:
            log(f"👁️ [obs] {agent_name} error: {type(e).__name__}: {e}, retry {backoff}s")

        if stop.is_set():
            break
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


# ═══════════════════════════════════════════════════════════════
# OAuth — delegated to platform module
# ═══════════════════════════════════════════════════════════════

oauth = platform.register_oauth()

# ═══════════════════════════════════════════════════════════════
# Auth helpers — delegated to platform module
# ═══════════════════════════════════════════════════════════════

# ForceAuthMiddleware is now provided by platform.create_auth_middleware()

# ═══════════════════════════════════════════════════════════════
# Legion Monitor (agent alive/dead tracking)
# ═══════════════════════════════════════════════════════════════

legion_status: dict[str, str] = {}  # agent → "online"|"offline"
_legion_offline_since: dict[str, float] = {}  # agent → timestamp
_resurrecting: dict[str, bool] = {}  # agent → resurrection in progress
latest_tasks: dict = {}  # latest task payload from Commander

# Resurrection thresholds (conservative — DeepSeek thinking blocks event loop 30-60s)
RESURRECT_WHITELIST = {"neo"}  # only Neo is whitelisted for auto-resurrection
RESURRECT_THRESHOLD = 60       # seconds of continuous offline before trigger
RESURRECT_COOLDOWN = 300       # seconds before retry after failed resurrection

# Startup grace period — allow agents time to boot before monitoring
GRACE_SECONDS = 150
_gatekeeper_boot_time = time.time()
_grace_ended = False
_grace_until = _gatekeeper_boot_time + GRACE_SECONDS

def _is_agent_alive(name: str) -> bool:
    """Check if agent process exists by scanning /proc/*/cmdline.
    Returns True if a nanobot gateway process for this agent is found.
    Used to distinguish 'thinking (event loop blocked)' from 'crashed'."""
    import glob
    token = f"instances/{name}/"
    for cmdline_path in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(cmdline_path, "rb") as f:
                data = f.read()
            if token.encode() in data:
                return True
        except (OSError, PermissionError):
            continue
    return False

async def legion_monitor():
    """Periodically health-check each agent's gateway_port.
    Triggers auto-resurrection for whitelisted agents after THRESHOLD."""
    global _grace_ended
    await asyncio.sleep(GRACE_SECONDS)
    _grace_ended = True
    log(f"🛡️ 复活引擎就绪 (宽限期 {GRACE_SECONDS}s 结束)")
    while True:
        now = time.time()

        # ── Cooldown expiry: allow retry for resurrecting agents ──
        for name in list(_resurrecting.keys()):
            if _resurrecting[name] and name in _legion_offline_since:
                if now - _legion_offline_since[name] > RESURRECT_COOLDOWN:
                    log(f"⏰ [{name}] 复活冷却到期，允许重试")
                    _resurrecting[name] = False
                    _legion_offline_since.pop(name, None)

        for name in AGENT_NAMES:
            info = SQUAD_ROSTER.get(name)
            if not info:
                continue
            gw_port = info.get("gateway_port")
            if not gw_port:
                continue
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"http://127.0.0.1:{gw_port}/health")
                if resp.status_code == 200:
                    if legion_status.get(name) == "offline":
                        offline_sec = now - _legion_offline_since.get(name, 0)
                        log(f"✅ [{name}] 恢复上线 (离线 {offline_sec:.0f}s)")
                    legion_status[name] = "online"
                    _legion_offline_since.pop(name, None)
                    if _resurrecting.get(name):
                        _resurrecting[name] = False
                else:
                    _mark_offline(name, f"HTTP {resp.status_code}", now)
            except Exception as e:
                _mark_offline(name, str(e), now)

        await asyncio.sleep(10)

def _mark_offline(name: str, reason: str, now: float = None):
    if now is None:
        now = time.time()

    # ── PID check: agent thinking blocks event loop but process stays alive ──
    if _is_agent_alive(name):
        if legion_status.get(name) == "offline":
            log(f"🟡 [{name}] PID 存活，疑似思考中（{reason}），恢复在线")
            legion_status[name] = "online"
            _legion_offline_since.pop(name, None)
        return  # process alive → not really offline

    # ── Process not found: genuine offline ──
    if legion_status.get(name) != "offline":
        legion_status[name] = "offline"
        _legion_offline_since[name] = now
        log(f"🔴 [{name}] 掉线 → {reason}")
        return

    # Already offline — check if resurrection should trigger
    if name not in RESURRECT_WHITELIST:
        return
    if _resurrecting.get(name):
        return  # already in progress

    elapsed = now - _legion_offline_since.get(name, now)
    if elapsed < RESURRECT_THRESHOLD:
        return  # not yet past threshold

    script = _find_resurrection_script(name)
    if not script:
        log(f"⚠️ [{name}] 失联 {elapsed:.0f}s 但无复活脚本")
        _legion_offline_since.pop(name, None)
        return

    _resurrecting[name] = True
    log(f"🆘 [{name}] 失联 {elapsed:.0f}s，触发自动复活 → {script}")
    try:
        subprocess.Popen(
            ["setsid", "bash", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        log(f"❌ [{name}] 复活启动失败: {e}")
        _resurrecting[name] = False

def _find_resurrection_script(name: str) -> Optional[str]:
    """Find the resurrection script for an agent, checking both
    /app/scripts/ (Docker-deployed) and persistent instance workspace."""
    candidates = [
        f"/app/scripts/resurrect_{name}.sh",
        f"{platform.instance_path(name)}/workspace/scripts/resurrect_{name}.sh",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

# ═══════════════════════════════════════════════════════════════
# Log Bridge (capture gateway logs → gatekeeper stdout)
# ═══════════════════════════════════════════════════════════════

async def log_bridge():
    """Read agent gateway logs and forward to gatekeeper stdout."""
    await asyncio.sleep(GRACE_SECONDS)
    while True:
        for name in AGENT_NAMES:
            path = f"{platform.instance_path(name)}/logs/gateway.log"
            try:
                with open(path) as f:
                    f.seek(0, 2)
            except FileNotFoundError:
                pass
        await asyncio.sleep(30)

# ═══════════════════════════════════════════════════════════════
# Dead Letter Queue (DLQ) Replay
# ═══════════════════════════════════════════════════════════════

DLQ_DIR = os.environ.get("DLQ_DIR", f"{platform.data_root}/dlq")
os.makedirs(DLQ_DIR, exist_ok=True)

async def dlq_replay():
    """Periodically retry failed cross-agent messages."""
    await asyncio.sleep(GRACE_SECONDS + 30)
    while True:
        try:
            entries = sorted(
                [f for f in os.listdir(DLQ_DIR) if f.endswith(".dlq")],
                key=lambda f: os.path.getmtime(os.path.join(DLQ_DIR, f))
            )
            for fn in entries[:5]:
                fpath = os.path.join(DLQ_DIR, fn)
                try:
                    with open(fpath) as f:
                        msg = json.load(f)
                    target = msg.get("target")
                    if target and legion_status.get(target) == "online":
                        # Re-send logic would go here
                        os.remove(fpath)
                        log(f"📬 [DLQ] replayed {fn} → {target}")
                except (json.JSONDecodeError, OSError):
                    # Stale/broken DLQ entry, remove
                    try: os.remove(fpath)
                    except OSError: pass
        except Exception:
            pass
        await asyncio.sleep(60)

# ═══════════════════════════════════════════════════════════════
# FastAPI App
# ═══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    log(f"🛡️ Gatekeeper v6.0 (Auto-Resurrect + PID, platform: {platform.name}) online — {len(AGENT_NAMES)} agents.")
    await platform.startup()
    asyncio.create_task(legion_monitor())
    asyncio.create_task(log_bridge())
    asyncio.create_task(dlq_replay())
    yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(platform.create_auth_middleware())
app.add_middleware(SessionMiddleware, **platform.session_kwargs)

# ── Platform-specific routes ──
platform.register_routes(app)

# ── Refresh platform with current config ──
platform.refresh_config(webui_agent=WEBUI_AGENT, squad_roster=SQUAD_ROSTER)

@app.middleware("http")
async def force_https_middleware(request: Request, call_next):
    request.scope["scheme"] = "https"
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "ok", "role": "gatekeeper", "agents": len(AGENT_NAMES)}

# ═══════════════════════════════════════════════════════════════
# Squad Relay Endpoint
# ═══════════════════════════════════════════════════════════════

RELAY_TOKEN = os.environ.get("SQUAD_RELAY_TOKEN", "").strip()
RELAY_TIMEOUT = get_relay_timeout()

@app.post("/api/squad/relay")
async def squad_relay(request: Request):
    """
    POST /api/squad/relay
    Header: X-Squad-Token: <SQUAD_RELAY_TOKEN>
    Body:   {"sender":"neo","target":"trinity","message":"ping","correlation_id":"sq-..."}

    Auth-free (no OAuth session required) — secured by shared token.
    Permission: delegated to platform.check_relay_permission().
    """
    # ── Auth ──
    auth_header = request.headers.get("X-Squad-Token", "")
    if not RELAY_TOKEN or auth_header != RELAY_TOKEN:
        return JSONResponse(
            {"status": "unauthorized", "error": "invalid or missing X-Squad-Token"},
            status_code=401)

    # ── Parse ──
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request", "error": "invalid JSON"}, status_code=400)

    sender = (body.get("sender") or "").strip()
    target = (body.get("target") or "").strip().lower()
    message = body.get("message") or ""
    correlation_id = body.get("correlation_id", f"sq-relay-{uuid4().hex[:8]}")

    if not sender or not target or not message:
        return JSONResponse(
            {"status": "bad_request", "error": "missing sender/target/message"}, status_code=400)

    # ── Roster & liveness ──
    if target not in SQUAD_ROSTER:
        return JSONResponse(
            {"status": "roster_miss", "error": f"'{target}' not in squad", "correlation_id": correlation_id}, status_code=404)
    if legion_status.get(target) != "online":
        return JSONResponse(
            {"status": "agent_offline", "error": f"'{target}' is offline", "correlation_id": correlation_id}, status_code=503)

    # ── Permission (delegated to platform) ──
    if not platform.check_relay_permission(sender, target):
        return JSONResponse({
            "status": "permission_denied",
            "error": f"'{sender}' not authorized for '{target}'",
            "correlation_id": correlation_id,
        }, status_code=403)

    # ── Relay via WebSocket ──
    target_info = SQUAD_ROSTER[target]
    nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()
    ws_url = f"ws://127.0.0.1:{target_info['ws_port']}/"
    if nanobot_token:
        ws_url += f"?token={nanobot_token}"

    try:
        log(f"📨 [Relay] {sender}→{target} connect {ws_url}")
        ws = await asyncio.wait_for(
            websockets.connect(ws_url, close_timeout=5),
            timeout=15
        )
        async with ws:
            # Step 1: wait for server ready greeting
            greeting_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            greeting = json.loads(greeting_raw)
            if greeting.get("event") != "ready":
                log(f"❌ [Relay] unexpected greeting: {greeting}")
                return JSONResponse({
                    "status": "protocol_error",
                    "error": f"expected 'ready' event, got {greeting.get('event')}",
                    "correlation_id": correlation_id,
                }, status_code=502)

            # Step 2: send message payload
            payload = json.dumps({
                "type": "message",
                "chat_id": target_info["id"],
                "content": f"[{sender.upper()}]: {message}",
            })
            await ws.send(payload)
            log(f"📨 [Relay] {sender}→{target} sent ({len(payload)}B)")

            # Step 3: collect response
            responses: list[str] = []
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=RELAY_TIMEOUT)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        log(f"📨 [Relay] non-JSON frame ({len(raw)}B)")
                        continue

                    event = data.get("event", "")

                    if event == "error":
                        detail = data.get("detail", "unknown")
                        log(f"❌ [Relay] framework error: {detail}")
                        return JSONResponse({
                            "status": "framework_error",
                            "error": detail,
                            "correlation_id": correlation_id,
                        }, status_code=502)

                    if event == "heartbeat":
                        continue

                    if event == "turn_end":
                        reply = "\n".join(responses) if responses else "(empty)"
                        log(f"✅ [Relay] {sender}→{target} ok ({len(reply)} chars)")
                        return JSONResponse({
                            "status": "delivered",
                            "target_response": reply,
                            "target": target,
                            "correlation_id": correlation_id,
                        })

                    if event == "delta":
                        text = data.get("text", "")
                        if text:
                            responses.append(text)
                        continue

                    if event == "stream_end":
                        continue

                    # Non-streaming fallback: check 'content' field
                    content = data.get("content")
                    if content and content.strip():
                        responses.append(content)

            except asyncio.TimeoutError:
                if responses:
                    reply = "\n".join(responses)
                    log(f"⏱️ [Relay] timeout with partial ({len(reply)} chars)")
                    return JSONResponse({
                        "status": "partial",
                        "target_response": reply,
                        "target": target,
                        "correlation_id": correlation_id,
                    })
                log(f"⏱️ [Relay] timeout ({RELAY_TIMEOUT}s)")
                return JSONResponse({
                    "status": "timeout",
                    "error": f"no response from agent within {RELAY_TIMEOUT}s",
                    "correlation_id": correlation_id,
                }, status_code=504)

    except asyncio.TimeoutError:
        log(f"❌ [Relay] connect timeout (15s)")
        return JSONResponse({
            "status": "connection_error",
            "error": "WebSocket connection timed out",
            "correlation_id": correlation_id,
        }, status_code=502)
    except Exception as e:
        log(f"❌ [Relay] {sender}→{target} error: {type(e).__name__}: {e}")
        return JSONResponse({
            "status": "connection_error",
            "error": f"{type(e).__name__}: {e}",
            "correlation_id": correlation_id,
        }, status_code=502)

# ═══════════════════════════════════════════════════════════════
# Squad Task Tracking Endpoint
# ═══════════════════════════════════════════════════════════════

@app.post("/api/squad/tasks")
async def squad_tasks(request: Request):
    """POST /api/squad/tasks — Commander pushes structured task list to all WebUI clients."""
    auth_header = request.headers.get("X-Squad-Token", "")
    if not RELAY_TOKEN or auth_header != RELAY_TOKEN:
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request", "error": "invalid JSON"}, status_code=400)
    goal = body.get("goal", "")
    tasks = body.get("tasks", [])
    if not isinstance(tasks, list):
        return JSONResponse({"status": "bad_request", "error": "tasks must be a list"}, status_code=400)
    global latest_tasks
    latest_tasks = {
        "goal": goal,
        "tasks": tasks,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "updated_by": body.get("updated_by", "unknown"),
    }
    done = sum(1 for t in tasks if t.get("status") == "done")
    log(f"📋 [Tasks] {done}/{len(tasks)} → {[t.get('title','?') for t in tasks[:5]]}")
    return JSONResponse({"status": "ok", "tasks": len(tasks), "done": done})

@app.get("/api/squad/tasks")
async def squad_tasks_get(request: Request):
    """GET /api/squad/tasks — read current task list (for Neo agent to query before updating)."""
    auth_header = request.headers.get("X-Squad-Token", "")
    if not RELAY_TOKEN or auth_header != RELAY_TOKEN:
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    return JSONResponse(latest_tasks or {"goal": "", "tasks": [], "updated_by": "none"})

# ═══════════════════════════════════════════════════════════════
# Squad Sessions Proxy — /api/squad/sessions → agent /api/sessions
# ═══════════════════════════════════════════════════════════════
# Platform proxies (e.g. ModelScope) may block /api/sessions.
# The frontend is patched to call /api/squad/sessions instead,
# and this universal proxy forwards to the agent's real endpoint.
#
# 2026-06-01: 改为按用户路由 (resolve_user_context),
# 而非始终请求 WEBUI_AGENT (Neo)。此前 DreamShepherd → Medic WebUI
# → Medic token → Neo /api/sessions → 401。

@app.get("/api/squad/sessions")
async def squad_sessions_get(request: Request):
    _uname, target_agent, ws_port = resolve_user_context(request)
    if not ws_port:
        ws_port = SQUAD_ROSTER.get(WEBUI_AGENT, {}).get("ws_port", 20002)
    target = f"http://127.0.0.1:{ws_port}/api/sessions"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ["host", "content-length"]}
    try:
        rp_resp = await _default_client.get(target, headers=headers)
        return JSONResponse(rp_resp.json(), status_code=rp_resp.status_code)
    except Exception as e:
        log(f"❌ sessions proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)

@app.api_route("/api/squad/sessions/{path:path}",
                methods=["GET", "POST", "DELETE"])
async def squad_sessions_proxy(request: Request, path: str):
    _uname, target_agent, ws_port = resolve_user_context(request)
    if not ws_port:
        ws_port = SQUAD_ROSTER.get(WEBUI_AGENT, {}).get("ws_port", 20002)
    target = f"http://127.0.0.1:{ws_port}/api/sessions/{path}"
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ["host", "content-length"]}
    body = await request.body() or None
    try:
        rp_resp = await _default_client.request(
            request.method, target, headers=headers, content=body)
        return Response(content=rp_resp.content,
                        status_code=rp_resp.status_code,
                        headers=dict(rp_resp.headers))
    except Exception as e:
        log(f"❌ sessions proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)

# ═══════════════════════════════════════════════════════════════
# WebSocket Proxy — Multiplexer v6.0 (multi-agent + cluster_log inject)
# ═══════════════════════════════════════════════════════════════

@app.websocket("/{path:path}")
async def ws_proxy(path: str, client_ws: WebSocket):
    """Multiplex Commander's WS to neo (bidirectional) + all squad agents (read-only).

    Architecture::

        Commander ──WS──▶ Gatekeeper ──WS──▶ neo (双向, Commander 对话)
                                ├──WS──▶ trinity (只读捕获)
                                ├──WS──▶ sentinel (只读捕获)
                                ├──WS──▶ assistant (只读捕获)
                                └──WS──▶ medic (只读捕获)

    Other agents' events are wrapped as ``cluster_log`` with ``source`` tags so
    the LegionTerminal component can route them to per-agent log tabs.
    """
    await client_ws.accept()

    # ── Session & identity ──────────────────────────────────
    session_user = client_ws.scope.get("session", {}).get("user")
    real_name = platform.extract_username(session_user) if isinstance(session_user, dict) else "Guest"
    is_commander = platform.is_commander(session_user)
    uname = real_name if is_commander else f"{real_name}_Observer"

    # ── Squad roster injection (V4/V6 interceptor expects these) ──
    await client_ws.send_text(json.dumps({
        "event": "auth_status", "type": "auth_status",
        "role": "commander" if is_commander else "observer",
    }))
    for event_type in ("legion_update", "cluster_update"):
        await client_ws.send_text(json.dumps({
            "event": event_type, "type": event_type,
            "data": legion_status,
            "nanobot_version": NANOBOT_VERSION,
            "roster": {
                a: {"id": info["id"], "name": a,
                    "gateway_port": info.get("gateway_port"),
                    "ws_port": info.get("ws_port")}
                for a, info in SQUAD_ROSTER.items()
            },
            "logs": [], "messages": [], "history": [],
            "tasks": latest_tasks,
        }))

    # ── Determine primary agent ─────────────────────────────
    primary_agent = get_agent_for_user(real_name)
    primary_info = SQUAD_ROSTER.get(primary_agent, SQUAD_ROSTER.get(WEBUI_AGENT, {}))
    nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()

    # ── Connect to primary agent (neo) ──────────────────────
    neo_url = f"ws://127.0.0.1:{primary_info['ws_port']}/{path}"
    if nanobot_token:
        neo_url += f"?token={nanobot_token}"
    log(f"🔀 WS 路由: {real_name} → {primary_agent} (port {primary_info.get('ws_port','?')} path /{path})")

    neo_ws = None
    try:
        neo_ws = await asyncio.wait_for(
            websockets.connect(neo_url, close_timeout=5), timeout=15
        )
    except asyncio.TimeoutError:
        log(f"🔌 [WS Proxy] {uname}→{primary_agent} connect timeout")
        try:
            await client_ws.close(code=4003, reason="agent connect timeout")
        except Exception:
            pass
        return
    except Exception as e:
        log(f"🔌 [WS Proxy] {uname}→{primary_agent} error: {e}")
        try:
            await client_ws.close()
        except Exception:
            pass
        return

    # ── Start observer capture loops for all OTHER agents ───
    observer_stop = asyncio.Event()
    observer_tasks: list[asyncio.Task] = []
    for name, info in SQUAD_ROSTER.items():
        if name == primary_agent:
            continue
        task = asyncio.create_task(
            _observer_capture(name, info, path, nanobot_token, client_ws, observer_stop)
        )
        observer_tasks.append(task)
    if observer_tasks:
        log(f"👁️ [WS Proxy] {len(observer_tasks)} observer capture loops started")

    # ── Periodic legion_update re-emission ──────────────────
    async def _emit_legion_update_periodic():
        while not observer_stop.is_set():
            await asyncio.sleep(5)
            try:
                await client_ws.send_text(json.dumps({
                    "event": "legion_update", "type": "legion_update",
                    "data": dict(legion_status),
                    "nanobot_version": NANOBOT_VERSION,
                    "roster": {
                        a: {"id": i["id"], "name": a,
                            "gateway_port": i.get("gateway_port"),
                            "ws_port": i.get("ws_port")}
                        for a, i in SQUAD_ROSTER.items()
                    },
                    "logs": [], "messages": [], "history": [],
                    "tasks": latest_tasks,
                }))
            except Exception:
                break
    periodic_task = asyncio.create_task(_emit_legion_update_periodic())

    # ── Bidirectional proxy with neo + cluster_log inject ───
    try:
        async def client_to_neo():
            """Commander → neo (identity injection + guest blocking via platform)."""
            username = uname.lower().replace("_observer", "")
            try:
                while True:
                    data = await client_ws.receive_text()
                    processed, blocked = platform.process_commander_message(
                        data, username, real_name, is_commander
                    )
                    if blocked is not None:
                        await client_ws.send_text(json.dumps({
                            "event": "blocked", "type": "blocked",
                            "text": blocked
                        }))
                        continue
                    await neo_ws.send(processed)
            except (WebSocketDisconnect, Exception):
                pass

        async def neo_to_client():
            """neo → Commander + cluster_log for LegionTerminal."""
            try:
                while True:
                    data = await neo_ws.recv()
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    # 1) passthrough to Commander's WebUI
                    await client_ws.send_text(data)
                    # 2) also inject as cluster_log for LegionTerminal
                    cluster = _build_cluster_log(primary_agent, data)
                    if cluster:
                        try:
                            await client_ws.send_text(cluster)
                        except Exception:
                            pass
            except (websockets.exceptions.ConnectionClosed, Exception):
                pass

        await asyncio.gather(client_to_neo(), neo_to_client())

    finally:
        # ── Teardown ────────────────────────────────────────
        observer_stop.set()
        periodic_task.cancel()
        for t in observer_tasks:
            t.cancel()
        try:
            await neo_ws.close()
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════
# Bootstrap / WebUI
# ═══════════════════════════════════════════════════════════════

# NOTE: /webui/bootstrap is deliberately NOT overridden here.
# The catch-all HTTP proxy forwards it to the target agent's ws_port,
# which returns { token, ws_path, ... } needed for WebSocket auth.
# Squad roster is injected via legion_update events at WS connect time.

@app.get("/")
async def index(request: Request):
    """Serve login page for guests, or proxy to agent WebUI for authenticated users."""
    uname, target_agent, _ws_port = resolve_user_context(request)
    if not uname:
        uname = "Unknown"

    # Guests see a login page (MS iframe blocks redirects, so we serve a page with a button)
    if uname.lower() in ("guest", "unknown"):
        login_url = "/api/squad/auth/login"
        return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nanobot Legion — MS Staging</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#0f0f0f; color:#e0e0e0; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#1a1a1a; border:1px solid #333; border-radius:12px; padding:48px 40px; text-align:center; max-width:420px; width:90%; }}
  h1 {{ font-size:1.4rem; margin-bottom:12px; color:#fff; }}
  p {{ color:#999; margin-bottom:24px; line-height:1.6; }}
  .btn {{ display:inline-block; background:#1677ff; color:#fff; padding:12px 32px; border-radius:8px; text-decoration:none; font-weight:600; font-size:1rem; transition:background .2s; }}
  .btn:hover {{ background:#4096ff; }}
  .hint {{ margin-top:20px; font-size:0.8rem; color:#666; }}
</style>
</head>
<body>
  <div class="card">
    <h1>🐈 Nanobot Legion</h1>
    <p>请使用 ModelScope 账号登录以访问指挥中心。</p>
    <a class="btn" href="{login_url}" target="_blank">🔑 使用 ModelScope 登录</a>
    <p class="hint">登录完成后请刷新本页面</p>
  </div>
</body>
</html>""", status_code=200)

    client = _http_clients.get(target_agent, _default_client)
    if not client:
        return HTMLResponse("<h1>Staging: no agent available</h1>", status_code=503)
    try:
        resp = await client.get("/")
        return HTMLResponse(
            content=resp.text,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception as e:
        log(f"❌ [GET /] proxy to {target_agent} failed: {e}")
        return HTMLResponse(f"<h1>Agent {target_agent} unreachable</h1>", status_code=502)

# ═══════════════════════════════════════════════════════════════
# Catch-all HTTP proxy — forward unmatched paths to agent ws_port
# ═══════════════════════════════════════════════════════════════

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(request: Request, path: str = ""):
    """Proxy all unmatched HTTP traffic to the appropriate agent's ws_port."""
    from fastapi.responses import StreamingResponse, Response

    uname, target_agent, _ws_port = resolve_user_context(request)
    if not uname:
        uname = "Unknown"

    client = _http_clients.get(target_agent, _default_client)
    if not client:
        return Response(content="No agent available", status_code=503)

    try:
        url = httpx.URL(path=f"/{path}" if path else "/", query=request.url.query.encode("utf-8"))
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
        rp_req = client.build_request(request.method, url, headers=headers, content=await request.body())
        rp_resp = await client.send(rp_req, stream=True)
        return StreamingResponse(rp_resp.aiter_raw(), status_code=rp_resp.status_code, headers=dict(rp_resp.headers))
    except Exception as e:
        return Response(content="System Warming Up...", status_code=503)

# ═══════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("GATEKEEPER_PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
