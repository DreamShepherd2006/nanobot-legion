#!/usr/bin/env python3
"""
Gatekeeper v6.1 — squad orchestrator (class-based, zero module globals).

Architecture:
    Gatekeeper (one class, all state in self)
        ├── [Config]        roster, webui_agent, version
        ├── [HTTP Proxy]    sessions, catch-all (per-agent clients, no sharing)
        ├── [Relay]         /api/squad/relay
        ├── [Task Tracking] /api/squad/tasks
        ├── [WS Proxy]      bidirectional + observer captures
        ├── [Resurrection]  legion_monitor + auto-resurrect
        ├── [Log Bridge]    gateway log → stdout forwarder
        ├── [DLQ]           dead letter queue replay
        ├── [Cluster Log]   agent event → cluster_log wrapper
        ├── [Index]         login page / WebUI proxy
        └── create_app()    wires routes to FastAPI

Pattern aligned with upstream nanobot's WebSocketChannel
(nanobot/channels/websocket.py): single class, clear method dispatch,
no module-level mutable globals.

Deployed on ws_port, serves WebUI and routes squad traffic.
Includes legion_monitor with automatic resurrection for whitelisted agents.
Uses OS-level PID check to avoid false-positive resurrection during DeepSeek thinking.
"""

import datetime
import glob as _glob_module
import json
import os
import subprocess
import sys
import time
import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI, Request, WebSocket
import websockets

# ── Platform (auto-detected) ──
# IMPORTANT: squad_config_loader must be imported BEFORE platforms!
# It injects DEPLOY_PLATFORM into os.environ at module level, which
# platforms.__init__._detect() reads in its matches() step 0.
from .squad_config_loader import get_relay_timeout, get_relay_token, get_resurrection_whitelist, load_config  # noqa: E402
from cloud_agent_gateway.platforms import platform  # noqa: E402
from .gatekeeper_monitor import legion_monitor_loop  # noqa: E402
from .gatekeeper_binding_chat import ensure_pinned_binding_chat  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# Gatekeeper — single class owning all state
# ═══════════════════════════════════════════════════════════════

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 或已映射用户可访问</h3>"


class Gatekeeper:
    """Squad gatekeeper: HTTP reverse proxy, WS multiplexer, relay hub.

    All mutable state lives in ``self`` — zero module-level globals
    (except the read-only ``platform`` singleton and constants).
    """

    # ── Class constants (resurrection thresholds) ─────────────
    RESURRECT_THRESHOLD: int = 60       # seconds offline before trigger
    RESURRECT_COOLDOWN: int = 300       # seconds before retry
    GRACE_SECONDS: int = 150            # startup grace period

    def __init__(self):
        # ── Platform (read-only after import — module-level singleton) ──
        self._platform = platform

        # ── Squad roster ───────────────────────────────────────
        self.agent_names: list[str] = []
        self.squad_roster: dict[str, dict] = {}
        self.peer_env_map: dict[str, str] = {}

        # ── WebUI routing ──────────────────────────────────────
        self.webui_agent: str = ""

        # ── Per-agent HTTP proxy pool ──────────────────────────
        # Each agent gets its own httpx.AsyncClient (base_url = ws_port).
        # Sessions proxy uses a FRESH client per request (no sharing).
        self._http_clients: dict[str, httpx.AsyncClient] = {}

        # ── Nanobot version ────────────────────────────────────
        self._nanobot_version: str = "unknown"

        # ── Legion monitor state ───────────────────────────────
        self.legion_status: dict[str, str] = {}       # agent → "online"|"offline"
        self._offline_since: dict[str, float] = {}    # agent → timestamp
        self._resurrecting: dict[str, bool] = {}      # agent → resurrection in progress
        self.latest_tasks: dict = {}                   # latest task payload
        self._boot_time: float = 0.0
        self._grace_ended: bool = False
        self._gateway_ever_healthy: set[str] = set()  # agents whose /health ever returned 200

        # ── Tokens & paths (set in setup) ──────────────────────
        self._relay_token: str = ""
        self._relay_timeout: int = 120
        self._dlq_dir: str = ""

        # ── Session Bridge: WS chat_id persistence ─────────────
        # Maps username → chat_id so reconnecting clients resume the same session.
        self._ws_sessions: dict[str, str] = {}

    # ═══════════════════════════════════════════════════════════
    # [Section 1] Setup — parse env, build state (called once)
    # ═══════════════════════════════════════════════════════════

    def setup(self):
        """Call once before creating the FastAPI app.
        Parses NANOBOT_PEER_* env vars, builds roster + HTTP pool."""
        self._boot_time = time.time()

        # ── Parse roster ──────────────────────────────────────
        self._refresh_roster()

        # ── Detect WebUI agent ────────────────────────────────
        self._detect_webui_agent()

        # ── Init HTTP proxy pool ──────────────────────────────
        self._init_http_pool()

        # ── Read version ──────────────────────────────────────
        self._nanobot_version = self._detect_nanobot_version()

        # ── Tokens ────────────────────────────────────────────
        self._relay_token = get_relay_token()
        self._relay_timeout = get_relay_timeout()
        self._resurrection_whitelist = get_resurrection_whitelist()

        # ── DLQ dir ───────────────────────────────────────────
        self._dlq_dir = os.environ.get("DLQ_DIR",
                                       f"{self._platform.data_root}/dlq")
        os.makedirs(self._dlq_dir, exist_ok=True)

        # ── Sync platform config ──────────────────────────────
        try:
            self._platform.refresh_config(
                webui_agent=self.webui_agent,
                squad_roster=self.squad_roster
            )
        except Exception:
            pass

        self._log(
            f"🛡️ Gatekeeper v6.1 ({self._platform.name}) "
            f"— {len(self.agent_names)} agents | webui={self.webui_agent} | "
            f"nanobot {self._nanobot_version}"
        )

    # ── Logging (instance method, no module-level print) ──────

    def _log(self, msg: str):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[GATEKEEPER] [{timestamp}] {msg}")
        sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════
    # [Section 2] Roster — squad_config.json peers as source of truth
    # ═══════════════════════════════════════════════════════════

    def _refresh_roster(self):
        """Read squad_config.json peers to build agent roster."""
        self.agent_names.clear()
        self.squad_roster.clear()
        self.peer_env_map.clear()

        cfg = load_config()
        peers = cfg.get("peers", {})
        for name, info in peers.items():
            if isinstance(info, dict) and "gateway_port" in info:
                zone = info.get("zone", "(missing)")
                print(f"[ZONE-DEBUG] peer={name!r} zone={zone!r}", file=sys.stderr, flush=True)
                # zone filter: only active agents (missing zone → active)
                if info.get("zone", "active") != "active":
                    continue
                self.agent_names.append(name)
                self.squad_roster[name] = {
                    "id": info.get("id", f"squad:{name}"),
                    "gateway_port": info.get("gateway_port", 0),
                    "ws_port": info.get("ws_port", 0),
                }

        self.agent_names.sort()
        self._log(f"📋 编制加载: {len(self.agent_names)} agents → {self.agent_names}")

    def _detect_webui_agent(self):
        """Resolve WEBUI_AGENT env var, fallback to first agent."""
        webui = os.environ.get("WEBUI_AGENT", "").strip().lower()
        if webui and webui in self.squad_roster:
            self.webui_agent = webui
        elif self.agent_names:
            self.webui_agent = self.agent_names[0]
        else:
            self.webui_agent = "neo"
        self._log(f"📡 WEBUI_AGENT={self.webui_agent}")

    def _init_http_pool(self):
        """Create one httpx.AsyncClient per agent (base_url = ws_port)."""
        self._http_clients.clear()
        for name, info in self.squad_roster.items():
            self._http_clients[name] = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{info['ws_port']}",
                timeout=120.0,
            )
        self._log(f"🌐 HTTP proxy pool: {list(self._http_clients.keys())}")

    def _detect_nanobot_version(self) -> str:
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

    # ═══════════════════════════════════════════════════════════
    # [Section 3] User context resolution (centralized for HTTP proxy)
    # ═══════════════════════════════════════════════════════════

    def _get_agent_for_user(self, username: str) -> str:
        """Return agent name for a user → delegated to platform module."""
        return self._platform.get_agent_for_user(username)

    def _resolve_user_context(self, request: Request) -> tuple[str, str, int | None]:
        """From OAuth session → (username, target_agent, ws_port).

        Unified entry point for HTTP proxy routes (index, catch-all, sessions).
        Avoids duplicating session parsing logic across routes.
        Returns empty target_agent / None ws_port for unauthorised users.
        """
        if not hasattr(request, "session"):
            return "", "", None
        session_user = request.session.get("user")
        uname = ""
        if isinstance(session_user, dict):
            uname = (session_user.get("preferred_username")
                     or session_user.get("username")
                     or session_user.get("name") or "")
        target = self._get_agent_for_user(uname) if uname else ""
        ws_port = self.squad_roster.get(target, {}).get("ws_port") if target else None
        return uname, target, ws_port

    # ═══════════════════════════════════════════════════════════
    # [Section 4] cluster_log builder — agent events → LegionTerminal
    # ═══════════════════════════════════════════════════════════

    def _build_cluster_log(self, source: str, raw_data: str) -> Optional[str]:
        """Convert an agent WS frame into a cluster_log event for LegionTerminal.

        Returns None for events that should not appear in per-agent log tabs
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
            label = text[:100] + ("…" if len(text) > 100 else "")
        elif event == "reasoning_delta":
            return None  # too verbose for log tab
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
            label = f"[{event}]"

        return json.dumps({
            "event": "cluster_log",
            "type": "cluster_log",
            "source": source,
            "content": label,
        })

    # ═══════════════════════════════════════════════════════════
    # [Section 5] WebSocket observer — read-only capture from squad agents
    # ═══════════════════════════════════════════════════════════

    async def _observer_capture(
        self,
        agent_name: str,
        info: dict,
        path: str,
        token: str,
        client_ws: WebSocket,
        stop: asyncio.Event,
    ):
        """Read-only WS capture → cluster_log injector for LegionTerminal.

        Opens WS to agent, captures every inbound event, wraps it with
        ``_build_cluster_log()``, sends to Commander's WS.  Never sends
        any messages to the agent — strictly read-only.

        On disconnect, backs off exponentially (2→30 s) and reconnects.
        Stops when ``stop`` is set (Commander disconnected).
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
                self._log(f"👁️ [obs] {agent_name} connected (port {info['ws_port']})")
                backoff = 2  # reset on success

                async with obs_ws:
                    while not stop.is_set():
                        try:
                            data = await asyncio.wait_for(obs_ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue  # keep-alive

                        if isinstance(data, bytes):
                            data = data.decode("utf-8", errors="replace")

                        cluster = self._build_cluster_log(agent_name, data)
                        if cluster:
                            try:
                                await client_ws.send_text(cluster)
                            except Exception:
                                return  # Commander disconnected

            except asyncio.TimeoutError:
                self._log(f"👁️ [obs] {agent_name} connect timeout, retry {backoff}s")
            except websockets.exceptions.ConnectionClosed as e:
                self._log(f"👁️ [obs] {agent_name} WS closed ({e.code}), retry {backoff}s")
            except Exception as e:
                self._log(f"👁️ [obs] {agent_name} error: {type(e).__name__}: {e}, retry {backoff}s")

            if stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    # ═══════════════════════════════════════════════════════════
    # [Section 6] Legion Monitor — health checks + auto-resurrection
    # ═══════════════════════════════════════════════════════════

    def _is_agent_process_alive(self, name: str) -> bool:
        """Check if agent process exists by scanning /proc/*/cmdline.

        Returns True if a nanobot gateway process for this agent is found.
        Used to distinguish 'thinking (event loop blocked)' from 'crashed'.
        """
        token = f"instances/{name}/"
        for cmdline_path in _glob_module.glob("/proc/[0-9]*/cmdline"):
            try:
                with open(cmdline_path, "rb") as f:
                    if token.encode() in f.read():
                        return True
            except (OSError, PermissionError):
                continue
        return False

    async def _legion_monitor(self):
        """Periodically health-check each agent's gateway_port.
        Triggers auto-resurrection for whitelisted agents after THRESHOLD."""
        await legion_monitor_loop(self)

    def _mark_offline(self, name: str, reason: str, now: float = 0):
        if not now:
            now = time.time()

        # ── PID check: agent thinking blocks event loop but process stays alive ──
        if self._is_agent_process_alive(name):
            # Two cases:
            # A) Gateway was ever healthy → likely thinking, keep online
            # B) Gateway was NEVER healthy → startup failure, treat as offline
            if name in self._gateway_ever_healthy:
                if self.legion_status.get(name) == "offline":
                    self._log(f"🟡 [{name}] PID 存活，疑似思考中（{reason}），恢复在线")
                    self.legion_status[name] = "online"
                    self._offline_since.pop(name, None)
                return
            # Fall through: PID alive but gateway never up → genuine offline

        # ── Process not found (or never-healthy startup failure) ──
        if self.legion_status.get(name) != "offline":
            self.legion_status[name] = "offline"
            self._offline_since[name] = now
            self._log(f"🔴 [{name}] 掉线 → {reason}")
            return

        # Already offline — check if resurrection should trigger
        if name not in self._resurrection_whitelist:
            return
        if self._resurrecting.get(name):
            return

        elapsed = now - self._offline_since.get(name, now)
        if elapsed < self.RESURRECT_THRESHOLD:
            return

        script = self._find_resurrection_script(name)
        if not script:
            self._log(f"⚠️ [{name}] 失联 {elapsed:.0f}s 但无复活脚本")
            self._offline_since.pop(name, None)
            return

        self._resurrecting[name] = True
        self._log(f"🆘 [{name}] 失联 {elapsed:.0f}s，触发自动复活 → {script}")
        # Clear ever-healthy flag so new process gets a fresh chance to prove itself
        self._gateway_ever_healthy.discard(name)
        self._offline_since.pop(name, None)
        try:
            subprocess.Popen(
                ["setsid", "bash", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._log(f"❌ [{name}] 复活启动失败: {e}")
            self._resurrecting[name] = False

    def _find_resurrection_script(self, name: str) -> Optional[str]:
        """Find resurrection script, check /app/scripts/ and persistent workspace."""
        candidates = [
            f"/app/scripts/resurrect_{name}.sh",
            f"{self._platform.instance_path(name)}/workspace/scripts/resurrect_{name}.sh",
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    # ═══════════════════════════════════════════════════════════
    # [Section 7] Log Bridge — gateway log → stdout
    # ═══════════════════════════════════════════════════════════

    async def _log_bridge(self):
        """Read agent gateway logs and forward to gatekeeper stdout."""
        await asyncio.sleep(self.GRACE_SECONDS)
        while True:
            for name in self.agent_names:
                path = f"{self._platform.instance_path(name)}/logs/gateway.log"
                try:
                    with open(path) as f:
                        f.seek(0, 2)
                except FileNotFoundError:
                    pass
            await asyncio.sleep(30)

    # ═══════════════════════════════════════════════════════════
    # [Section 8] Dead Letter Queue (DLQ) Replay
    # ═══════════════════════════════════════════════════════════

    async def _dlq_replay(self):
        """Periodically retry failed cross-agent messages."""
        await asyncio.sleep(self.GRACE_SECONDS + 30)
        while True:
            try:
                entries = sorted(
                    [f for f in os.listdir(self._dlq_dir) if f.endswith(".dlq")],
                    key=lambda f: os.path.getmtime(
                        os.path.join(self._dlq_dir, f))
                )
                for fn in entries[:5]:
                    fpath = os.path.join(self._dlq_dir, fn)
                    try:
                        with open(fpath) as f:
                            msg = json.load(f)
                        target = msg.get("target")
                        if target and self.legion_status.get(target) == "online":
                            os.remove(fpath)
                            self._log(f"📬 [DLQ] replayed {fn} → {target}")
                    except (json.JSONDecodeError, OSError):
                        try:
                            os.remove(fpath)
                        except OSError:
                            pass
            except Exception:
                pass
            await asyncio.sleep(60)

    # ═══════════════════════════════════════════════════════════
    # [Section 9] FastAPI lifespan — background tasks
    # ═══════════════════════════════════════════════════════════

    async def _lifespan(self, _app: FastAPI):
        """FastAPI lifespan: startup → background tasks → yield → shutdown."""
        await self._platform.startup()
        ensure_pinned_binding_chat(self)
        asyncio.create_task(self._legion_monitor())
        asyncio.create_task(self._log_bridge())
        asyncio.create_task(self._dlq_replay())
        yield

    # ═══════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════
    # [Section 10] WebSocket Proxy — Multiplexer v6.0
    # ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from .gatekeeper_routes import create_app
    app = create_app()
    import uvicorn
    port = int(os.environ.get("GATEKEEPER_PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
