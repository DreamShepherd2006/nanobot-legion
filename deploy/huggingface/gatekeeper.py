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
import re
import signal as _signal
import subprocess
import sys
import time
import asyncio
from uuid import uuid4
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse, Response, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware
import websockets
from cloud_agent_gateway.channel_binding import discover as discover_bindings

# ── Platform (auto-detected) ──
# IMPORTANT: squad_config_loader must be imported BEFORE platforms!
# It injects DEPLOY_PLATFORM into os.environ at module level, which
# platforms.__init__._detect() reads in its matches() step 0.
from squad_config_loader import get_relay_timeout  # noqa: E402
from cloud_agent_gateway.platforms import platform  # noqa: E402
from agent_config import create_agent_routes  # noqa: E402


# ═══════════════════════════════════════════════════════════════
# Gatekeeper — single class owning all state
# ═══════════════════════════════════════════════════════════════

class Gatekeeper:
    """Squad gatekeeper: HTTP reverse proxy, WS multiplexer, relay hub.

    All mutable state lives in ``self`` — zero module-level globals
    (except the read-only ``platform`` singleton and constants).
    """

    # ── Class constants (resurrection thresholds) ─────────────
    RESURRECT_WHITELIST: set = {"neo"}
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
        self._relay_token = os.environ.get("SQUAD_RELAY_TOKEN", "").strip()
        self._relay_timeout = get_relay_timeout()

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

    def _ensure_pinned_binding_chat(self):
        """Create pinned sidebar chat with channel binding links."""
        _bindings = getattr(self, "_bindings", [])
        if not _bindings:
            self._log("📌 无可用的绑定通道，跳过 pinned chat")
            return

        import uuid as _uuid, time as _time

        # Match oauth_proxy.py constants — keep in sync with cloud-agent-gateway
        _BINDING_CHAT_TITLE = "社交通道配置指南"
        _LEGACY_BINDING_TITLES = ["社交通道配置提示"]
        _BINDING_PROJECT = "系统配置"
        _agent = self.webui_agent
        _now = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        _project_dir = f"{self._platform.data_root}/{_BINDING_PROJECT}"

        # Generate binding links (same format as Cloud Demo oauth_proxy.py)
        _rows = "\n".join(
            f"| {_spec.icon} {_spec.display} | [绑定{_spec.display}](/bind/{_spec.name}) |"
            for _spec in _bindings
        )

        # Build content matching oauth_proxy.py BINDING_CHAT_CONTENT structure
        _content = f"""\
# 📱 社交通道配置

将 nanobot 连接到社交通道，随时随地对话。

| 通道 | 操作 |
|------|------|
{_rows}

👆 点击上方链接即可操作，无需在此聊天。

---

# 🤖 Agent 管理

Legion 多智能体编制管理。Commander (neo) 由初始化配置生成。

👉 [`配置 Agent`](/config/agents)

添加或管理 Worker Agent（名字、角色、模型），保存后**重启空间**生效。

---

# ⚙️ 系统重置

如需重新配置 OAuth 登录凭证（API Key / 模型配置会保留并自动预填），访问：

👉 [`/reset-setup`](/reset-setup)

---

# 📦 开源代码

本项目基于以下开源组件构建：

- **cloud-agent-gateway**: [DreamShepherd2006/cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)
- **nanobot**: [HKUDS/nanobot](https://github.com/HKUDS/nanobot)

部署方式：\n1. 将本空间的 Dockerfile 上传到你的 HuggingFace Space 或 ModelScope Studio\n2. 空间自动构建并启动，访问 Setup 页面完成初始化"""

        # Clean up old binding sessions — matches both current and legacy titles
        _state = self._platform.read_sidebar_state(_agent)
        _new_pinned = []
        _changed = False
        for _pk in _state.get("pinned_keys", []):
            if not isinstance(_pk, str) or not _pk.startswith("websocket:"):
                _new_pinned.append(_pk)
                continue
            _cid = _pk.split(":", 1)[1]
            _lines = self._platform.read_session(_agent, _cid)
            if _lines:
                _title = _lines[0].get("metadata", {}).get("title", "")
                if _title == _BINDING_CHAT_TITLE or _title in _LEGACY_BINDING_TITLES:
                    self._platform.delete_session(_agent, _cid)
                    _changed = True
                    continue
            _new_pinned.append(_pk)
        if _changed:
            _state["pinned_keys"] = _new_pinned
            _state["updated_at"] = _now
            self._platform.write_sidebar_state(_agent, _state)

        # Create new binding session
        _cid = str(_uuid.uuid4())
        _key = f"websocket:{_cid}"

        import os as _os
        _os.makedirs(_project_dir, exist_ok=True)
        self._platform.write_session(_agent, _cid, [
            {
                "_type": "metadata", "key": _key,
                "created_at": _now, "updated_at": _now,
                "metadata": {"title": _BINDING_CHAT_TITLE, "webui": True,
                            "workspace_scope": {"project_path": _project_dir},
                            "_binding_type": "system_config"},
                "last_consolidated": 0,
            },
            {
                "role": "user", "content": _content,
                "timestamp": _now,
            },
        ])

        # WebUI transcript
        self._platform.write_webui_transcript(_agent, _cid, [
            {"event": "delta", "text": _content, "chat_id": _cid},
            {"event": "stream_end", "text": _content, "chat_id": _cid},
            {"event": "turn_end", "chat_id": _cid},
        ])

        # Pin to sidebar
        _sidebar_state = self._platform.read_sidebar_state(_agent)
        _sidebar_state.setdefault("pinned_keys", []).insert(0, _key)
        _sidebar_state["updated_at"] = _now
        _sidebar_state.setdefault("schema_version", 1)
        self._platform.write_sidebar_state(_agent, _sidebar_state)

        self._binding_chat_cid = _cid
        self._log(f"📌 pinned binding chat ({_cid[:12]}...) — {len(_bindings)} channels")

    # ═══════════════════════════════════════════════════════════
    # [Section 2] Roster parsing — NANOBOT_PEER_* → squad_roster
    # ═══════════════════════════════════════════════════════════

    def _refresh_roster(self):
        """Parse NANOBOT_PEER_* env vars to build agent roster."""
        self.agent_names.clear()
        self.squad_roster.clear()
        self.peer_env_map.clear()

        for key, val in os.environ.items():
            if not key.startswith("NANOBOT_PEER_"):
                continue
            self.peer_env_map[key] = val
            agent_name = key[len("NANOBOT_PEER_"):].lower()
            try:
                info = json.loads(val)
                if isinstance(info, dict) and "id" in info:
                    self.agent_names.append(agent_name)
                    self.squad_roster[agent_name] = {
                        "id": info["id"],
                        "gateway_port": info.get("gateway_port", 0),
                        "ws_port": info.get("ws_port", 0),
                    }
            except (json.JSONDecodeError, TypeError):
                self._log(f"⚠️ 跳过无效 NANOBOT_PEER_*: {key}")

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
        """
        if not hasattr(request, "session"):
            return "", self.webui_agent, self.squad_roster.get(
                self.webui_agent, {}).get("ws_port")
        session_user = request.session.get("user")
        uname = ""
        if isinstance(session_user, dict):
            uname = (session_user.get("preferred_username")
                     or session_user.get("username")
                     or session_user.get("name") or "")
        target = self._get_agent_for_user(uname) if uname else self.webui_agent
        ws_port = self.squad_roster.get(target, {}).get("ws_port")
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
        grace_until = self._boot_time + self.GRACE_SECONDS
        await asyncio.sleep(self.GRACE_SECONDS)
        self._grace_ended = True
        self._log(f"🛡️ 复活引擎就绪 (宽限期 {self.GRACE_SECONDS}s 结束)")

        while True:
            now = time.time()

            # ── Cooldown expiry ──
            for name in list(self._resurrecting.keys()):
                if self._resurrecting[name] and name in self._offline_since:
                    if now - self._offline_since[name] > self.RESURRECT_COOLDOWN:
                        self._log(f"⏰ [{name}] 复活冷却到期，允许重试")
                        self._resurrecting[name] = False
                        self._offline_since.pop(name, None)

            for name in self.agent_names:
                info = self.squad_roster.get(name)
                if not info:
                    continue
                gw_port = info.get("gateway_port")
                if not gw_port:
                    continue
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        resp = await client.get(
                            f"http://127.0.0.1:{gw_port}/health")
                    if resp.status_code == 200:
                        if self.legion_status.get(name) == "offline":
                            offline_sec = now - self._offline_since.get(name, 0)
                            self._log(f"✅ [{name}] 恢复上线 (离线 {offline_sec:.0f}s)")
                        self.legion_status[name] = "online"
                        self._offline_since.pop(name, None)
                        if self._resurrecting.get(name):
                            self._resurrecting[name] = False
                    else:
                        self._mark_offline(name, f"HTTP {resp.status_code}", now)
                except Exception as e:
                    self._mark_offline(name, str(e), now)

            await asyncio.sleep(10)

    def _mark_offline(self, name: str, reason: str, now: float = 0):
        if not now:
            now = time.time()

        # ── PID check: agent thinking blocks event loop but process stays alive ──
        if self._is_agent_process_alive(name):
            if self.legion_status.get(name) == "offline":
                self._log(f"🟡 [{name}] PID 存活，疑似思考中（{reason}），恢复在线")
                self.legion_status[name] = "online"
                self._offline_since.pop(name, None)
            return

        # ── Process not found: genuine offline ──
        if self.legion_status.get(name) != "offline":
            self.legion_status[name] = "offline"
            self._offline_since[name] = now
            self._log(f"🔴 [{name}] 掉线 → {reason}")
            return

        # Already offline — check if resurrection should trigger
        if name not in self.RESURRECT_WHITELIST:
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
        self._ensure_pinned_binding_chat()
        asyncio.create_task(self._legion_monitor())
        asyncio.create_task(self._log_bridge())
        asyncio.create_task(self._dlq_replay())
        yield

    # ═══════════════════════════════════════════════════════════
    # [Section 10] HTTP Route Handlers
    # ═══════════════════════════════════════════════════════════

    # ── Health ─────────────────────────────────────────────────

    async def _handle_health(self) -> dict:
        return {"status": "ok", "role": "gatekeeper",
                "agents": len(self.agent_names)}

    # ── Relay ──────────────────────────────────────────────────

    async def _handle_relay(self, request: Request):
        """POST /api/squad/relay — cross-agent message relay via WS."""
        # Auth
        auth_header = request.headers.get("X-Squad-Token", "")
        if not self._relay_token or auth_header != self._relay_token:
            return JSONResponse(
                {"status": "unauthorized",
                 "error": "invalid or missing X-Squad-Token"}, status_code=401)

        # Parse
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "bad_request", "error": "invalid JSON"}, status_code=400)

        sender = (body.get("sender") or "").strip()
        commander = (body.get("commander") or "").strip()
        target = (body.get("target") or "").strip().lower()
        message = body.get("message") or ""
        corr_id = body.get("correlation_id", f"sq-relay-{uuid4().hex[:8]}")

        if not sender or not target or not message:
            return JSONResponse(
                {"status": "bad_request",
                 "error": "missing sender/target/message"}, status_code=400)

        # Roster & liveness
        if target not in self.squad_roster:
            return JSONResponse(
                {"status": "roster_miss",
                 "error": f"'{target}' not in squad",
                 "correlation_id": corr_id}, status_code=404)
        if self.legion_status.get(target) != "online":
            return JSONResponse(
                {"status": "agent_offline",
                 "error": f"'{target}' is offline",
                 "correlation_id": corr_id}, status_code=503)

        # Permission: check commander (OAuth identity) if provided,
        # otherwise fall back to sender (backward compat).
        auth_identity = commander or sender
        if not self._platform.check_relay_permission(auth_identity, target):
            return JSONResponse({
                "status": "permission_denied",
                "error": f"'{auth_identity}' not authorized for '{target}'",
                "correlation_id": corr_id,
            }, status_code=403)

        # Relay via WebSocket
        target_info = self.squad_roster[target]
        nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()
        ws_url = f"ws://127.0.0.1:{target_info['ws_port']}/"
        if nanobot_token:
            ws_url += f"?token={nanobot_token}"

        try:
            self._log(f"📨 [Relay] {sender}→{target} connect {ws_url}")
            ws = await asyncio.wait_for(
                websockets.connect(ws_url, close_timeout=5), timeout=15)
            async with ws:
                greeting_raw = await asyncio.wait_for(ws.recv(), timeout=10)
                greeting = json.loads(greeting_raw)
                if greeting.get("event") != "ready":
                    self._log(f"❌ [Relay] unexpected greeting: {greeting}")
                    return JSONResponse({
                        "status": "protocol_error",
                        "error": f"expected 'ready' event, got {greeting.get('event')}",
                        "correlation_id": corr_id,
                    }, status_code=502)

                # Inject relay identity so neo sees:
                #   - which agent relayed (sender_id: agent:<agent>)
                #   - which Commander authorised it (commander_id: oauth:<user>)
                # This mirrors the WebUI path where process_commander_message
                # injects sender_id=oauth:<username>.
                envelope = {
                    "type": "message",
                    "chat_id": target_info["id"],
                    "content": message,
                    "sender_id": f"agent:{sender}",
                    "sender_name": sender,
                }
                if commander:
                    envelope["commander_id"] = f"oauth:{commander}"
                    envelope["commander_name"] = commander
                payload = json.dumps(envelope)
                await ws.send(payload)
                self._log(f"📨 [Relay] {sender}→{target} sent ({len(payload)}B)")

                responses: list[str] = []
                try:
                    while True:
                        raw = await asyncio.wait_for(ws.recv(),
                                                     timeout=self._relay_timeout)
                        try:
                            data = json.loads(raw)
                        except json.JSONDecodeError:
                            self._log(f"📨 [Relay] non-JSON frame ({len(raw)}B)")
                            continue

                        event = data.get("event", "")

                        if event == "error":
                            detail = data.get("detail", "unknown")
                            self._log(f"❌ [Relay] framework error: {detail}")
                            return JSONResponse({
                                "status": "framework_error",
                                "error": detail,
                                "correlation_id": corr_id,
                            }, status_code=502)

                        if event == "heartbeat":
                            continue

                        if event == "turn_end":
                            reply = "\n".join(responses) if responses else "(empty)"
                            self._log(f"✅ [Relay] {sender}→{target} ok ({len(reply)} chars)")
                            return JSONResponse({
                                "status": "delivered",
                                "target_response": reply,
                                "target": target,
                                "correlation_id": corr_id,
                            })

                        if event == "delta":
                            text = data.get("text", "")
                            if text:
                                responses.append(text)
                            continue

                        if event == "stream_end":
                            continue

                        content = data.get("content")
                        if content and content.strip():
                            responses.append(content)

                except asyncio.TimeoutError:
                    if responses:
                        reply = "\n".join(responses)
                        self._log(f"⏱️ [Relay] timeout with partial ({len(reply)} chars)")
                        return JSONResponse({
                            "status": "partial",
                            "target_response": reply,
                            "target": target,
                            "correlation_id": corr_id,
                        })
                    self._log(f"⏱️ [Relay] timeout ({self._relay_timeout}s)")
                    return JSONResponse({
                        "status": "timeout",
                        "error": f"no response from agent within {self._relay_timeout}s",
                        "correlation_id": corr_id,
                    }, status_code=504)

        except asyncio.TimeoutError:
            self._log("❌ [Relay] connect timeout (15s)")
            return JSONResponse({
                "status": "connection_error",
                "error": "WebSocket connection timed out",
                "correlation_id": corr_id,
            }, status_code=502)
        except Exception as e:
            self._log(f"❌ [Relay] {sender}→{target} error: {type(e).__name__}: {e}")
            return JSONResponse({
                "status": "connection_error",
                "error": f"{type(e).__name__}: {e}",
                "correlation_id": corr_id,
            }, status_code=502)

    # ── Task Tracking ──────────────────────────────────────────

    async def _handle_tasks_post(self, request: Request):
        """POST /api/squad/tasks — Commander pushes structured task list."""
        auth_header = request.headers.get("X-Squad-Token", "")
        if not self._relay_token or auth_header != self._relay_token:
            return JSONResponse({"status": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"status": "bad_request", "error": "invalid JSON"}, status_code=400)
        goal = body.get("goal", "")
        tasks = body.get("tasks", [])
        if not isinstance(tasks, list):
            return JSONResponse(
                {"status": "bad_request", "error": "tasks must be a list"},
                status_code=400)
        self.latest_tasks = {
            "goal": goal,
            "tasks": tasks,
            "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "updated_by": body.get("updated_by", "unknown"),
        }
        done = sum(1 for t in tasks if t.get("status") == "done")
        self._log(f"📋 [Tasks] {done}/{len(tasks)} → "
                  f"{[t.get('title', '?') for t in tasks[:5]]}")
        return JSONResponse({"status": "ok", "tasks": len(tasks), "done": done})

    async def _handle_tasks_get(self, request: Request):
        """GET /api/squad/tasks — read current task list."""
        auth_header = request.headers.get("X-Squad-Token", "")
        if not self._relay_token or auth_header != self._relay_token:
            return JSONResponse({"status": "unauthorized"}, status_code=401)
        return JSONResponse(
            self.latest_tasks or {"goal": "", "tasks": [], "updated_by": "none"})

    # ── Sessions Proxy ─────────────────────────────────────────
    # ⚠️  Uses a FRESH httpx.AsyncClient per request (no shared state with
    #    catch-all proxy's per-agent clients).  This avoids the 502 bug
    #    (2026-06-01) where _default_client's base_url polluted sessions routing.

    async def _handle_sessions(self, request: Request):
        _uname, target_agent, ws_port = self._resolve_user_context(request)
        if not ws_port:
            ws_port = self.squad_roster.get(self.webui_agent, {}).get("ws_port", 20002)

        token = request.query_params.get("token", "")
        target = f"http://127.0.0.1:{ws_port}/api/sessions"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(target, headers=headers)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except Exception as e:
            self._log(f"❌ sessions proxy error: {e}")
            return JSONResponse({"error": str(e)}, status_code=502)

    async def _handle_sessions_sub(self, request: Request, path: str):
        _uname, target_agent, ws_port = self._resolve_user_context(request)
        if not ws_port:
            ws_port = self.squad_roster.get(self.webui_agent, {}).get("ws_port", 20002)

        token = request.query_params.get("token", "")
        target = f"http://127.0.0.1:{ws_port}/api/sessions/{path}"
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = await request.body() or None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(
                    request.method, target, headers=headers, content=body)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except Exception as e:
            self._log(f"❌ sessions proxy error: {e}")
            return JSONResponse({"error": str(e)}, status_code=502)

    # ── Index (login page / WebUI proxy) ───────────────────────

    async def _handle_index(self, request: Request):
        """Serve login page for guests, or proxy to agent WebUI for auth'd users."""
        uname, target_agent, _ws_port = self._resolve_user_context(request)
        if not uname:
            uname = "Unknown"

        # Guests → login page
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

        # Authenticated → proxy to agent
        client = self._http_clients.get(target_agent)
        if not client:
            client = self._http_clients.get(self.webui_agent)
        if not client:
            return HTMLResponse("<h1>Staging: no agent available</h1>",
                                status_code=503)
        try:
            resp = await client.get("/")
            return HTMLResponse(
                content=resp.text,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
        except Exception as e:
            self._log(f"❌ [GET /] proxy to {target_agent} failed: {e}")
            return HTMLResponse(f"<h1>Agent {target_agent} unreachable</h1>",
                                status_code=502)

    # ── Catch-all HTTP proxy ───────────────────────────────────

    async def _handle_catch_all(self, request: Request, path: str):
        """Proxy unmatched HTTP traffic to the user's assigned agent ws_port."""
        uname, target_agent, _ws_port = self._resolve_user_context(request)

        client = self._http_clients.get(target_agent)
        if not client:
            client = self._http_clients.get(self.webui_agent)
        if not client:
            return Response(content="No agent available", status_code=503)

        try:
            url = httpx.URL(
                path=f"/{path}" if path else "/",
                query=request.url.query.encode("utf-8"))
            blacklist = set(h.lower() for h in self._platform.proxy_header_blacklist)
            headers = {k: v for k, v in request.headers.items()
                       if k.lower() not in blacklist}
            # 🔧 On platforms that strip Authorization header (ModelScope),
            # fall back to ?token= query parameter for API calls.
            if "authorization" not in {k.lower() for k in headers}:
                _q = request.url.query.decode("utf-8") if isinstance(request.url.query, bytes) else request.url.query
                if "token=" in _q:
                    for _p in _q.split("&"):
                        if _p.startswith("token="):
                            _tok = _p.split("=", 1)[1]
                            headers["authorization"] = f"Bearer {_tok}"
                            break
            rp_req = client.build_request(
                request.method, url, headers=headers,
                content=await request.body())
            rp_resp = await client.send(rp_req, stream=True)
            return StreamingResponse(
                rp_resp.aiter_raw(),
                status_code=rp_resp.status_code,
                headers=dict(rp_resp.headers))
        except Exception:
            return Response(content="System Warming Up...", status_code=503)

    # ═══════════════════════════════════════════════════════════
    # [Section 11] WebSocket Proxy — Multiplexer v6.0
    # ═══════════════════════════════════════════════════════════

    async def _handle_ws_proxy(self, path: str, client_ws: WebSocket):
        """Multiplex Commander's WS: primary agent (bidirectional)
        + all other squad agents (read-only observer captures).

        Architecture:
            Commander ──WS──▶ Gatekeeper ──WS──▶ primary (bidirectional)
                                     ├──WS──▶ agent2 (read-only)
                                     ├──WS──▶ agent3 (read-only)
                                     └──WS──▶ agent4 (read-only)

        Observer events are wrapped as ``cluster_log`` with ``source`` tags
        so LegionTerminal can route them to per-agent log tabs.
        """
        await client_ws.accept()

        # ── Session & identity ──
        session_user = client_ws.scope.get("session", {}).get("user")
        real_name = (self._platform.extract_username(session_user)
                     if isinstance(session_user, dict) else "Guest")
        is_commander = self._platform.is_commander(session_user)
        uname = real_name if is_commander else f"{real_name}_Observer"

        # ── Squad roster injection (V4/V6 interceptor expects these) ──
        await client_ws.send_text(json.dumps({
            "event": "auth_status", "type": "auth_status",
            "role": "commander" if is_commander else "observer",
        }))
        initial_roster = {
            a: {"id": i["id"], "name": a,
                "gateway_port": i.get("gateway_port"),
                "ws_port": i.get("ws_port")}
            for a, i in self.squad_roster.items()
        }
        for event_type in ("legion_update", "cluster_update"):
            await client_ws.send_text(json.dumps({
                "event": event_type, "type": event_type,
                "data": self.legion_status,
                "nanobot_version": self._nanobot_version,
                "roster": initial_roster,
                "logs": [], "messages": [], "history": [],
                "tasks": self.latest_tasks,
            }))

        # ── Determine primary agent ──
        primary_agent = self._get_agent_for_user(real_name)
        primary_info = self.squad_roster.get(
            primary_agent,
            self.squad_roster.get(self.webui_agent, {}))
        nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()

        # ── Connect to primary agent ──
        neo_url = (f"ws://127.0.0.1:{primary_info['ws_port']}/{path}"
                   + (f"?token={nanobot_token}" if nanobot_token else ""))
        self._log(f"🔀 WS 路由: {real_name} → {primary_agent} "
                  f"(port {primary_info.get('ws_port', '?')} path /{path})")

        neo_ws = None
        try:
            neo_ws = await asyncio.wait_for(
                websockets.connect(neo_url, close_timeout=5), timeout=15)
        except asyncio.TimeoutError:
            self._log(f"🔌 [WS Proxy] {uname}→{primary_agent} connect timeout")
            try:
                await client_ws.close(code=4003, reason="agent connect timeout")
            except Exception:
                pass
            return
        except Exception as e:
            self._log(f"🔌 [WS Proxy] {uname}→{primary_agent} error: {e}")
            try:
                await client_ws.close()
            except Exception:
                pass
            return

        # ── Start observer captures for all OTHER agents ──
        observer_stop = asyncio.Event()
        observer_tasks: list[asyncio.Task] = []
        for name, info in self.squad_roster.items():
            if name == primary_agent:
                continue
            task = asyncio.create_task(
                self._observer_capture(
                    name, info, path, nanobot_token, client_ws, observer_stop))
            observer_tasks.append(task)
        if observer_tasks:
            self._log(f"👁️ [WS Proxy] {len(observer_tasks)} observer capture loops started")

        # ── Periodic legion_update re-emission ──
        async def _emit_periodic():
            while not observer_stop.is_set():
                await asyncio.sleep(5)
                try:
                    await client_ws.send_text(json.dumps({
                        "event": "legion_update", "type": "legion_update",
                        "data": dict(self.legion_status),
                        "nanobot_version": self._nanobot_version,
                        "roster": {
                            a: {"id": i["id"], "name": a,
                                "gateway_port": i.get("gateway_port"),
                                "ws_port": i.get("ws_port")}
                            for a, i in self.squad_roster.items()
                        },
                        "logs": [], "messages": [], "history": [],
                        "tasks": self.latest_tasks,
                    }))
                except Exception:
                    break
        periodic_task = asyncio.create_task(_emit_periodic())

        # ── Session Bridge state ──────────────────────────────
        session_key = real_name.lower() if real_name != "Guest" else ""
        prev_chat_id = self._ws_sessions.get(session_key, "") if session_key else ""
        current_chat_id: str = ""  # set by neo_to_client on "ready"
        ready_chat_event = asyncio.Event()
        attached_confirm = asyncio.Event()
        attach_sent = False

        # ── Bidirectional proxy + cluster_log inject ──
        try:
            async def client_to_neo():
                """Commander → neo (identity injection + session bridge)."""
                nonlocal attach_sent, current_chat_id
                username = uname.lower().replace("_observer", "")
                session_cid = prev_chat_id  # snapshot before loop
                try:
                    # ── Session Bridge: attach to previous chat ──
                    if session_cid and is_commander:
                        # Wait for neo's "ready" (so neo is ready to handle attach)
                        try:
                            await asyncio.wait_for(ready_chat_event.wait(), timeout=8.0)
                        except asyncio.TimeoutError:
                            self._log(f"🔗 [Session] {real_name}: ready timeout, skipping attach")
                            session_cid = ""
                        if session_cid:
                            self._log(f"🔗 [Session] {real_name}: attaching to chat_id={session_cid[:12]}…")
                            try:
                                await neo_ws.send(json.dumps(
                                    {"type": "attach", "chat_id": session_cid}))
                                attach_sent = True
                                try:
                                    await asyncio.wait_for(attached_confirm.wait(), timeout=5.0)
                                    self._log(f"🔗 [Session] {real_name}: attached ✓")
                                    current_chat_id = session_cid
                                except asyncio.TimeoutError:
                                    self._log(f"🔗 [Session] {real_name}: attach resp timeout")
                                    session_cid = ""
                            except Exception:
                                self._log(f"🔗 [Session] {real_name}: attach send failed")
                                session_cid = ""

                    # ── Main message loop ──
                    while True:
                        data = await client_ws.receive_text()

                        # 🚫 Intercept messages to binding chat (static reply, no LLM)
                        _binding_cid = getattr(self, '_binding_chat_cid', None)
                        if _binding_cid:
                            try:
                                _env = json.loads(data)
                                if isinstance(_env, dict) and _env.get("chat_id") == _binding_cid:
                                    if _env.get("type") == "message":
                                        _reply = "👆 请点击上方链接操作（社交通道绑定、Agent 管理、系统重置），无需在此聊天。"
                                        await client_ws.send_text(json.dumps({
                                            "event": "delta", "data": _reply,
                                            "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                            "sender_name": username,
                                        }))
                                        await client_ws.send_text(json.dumps({
                                            "event": "stream_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        await client_ws.send_text(json.dumps({
                                            "event": "turn_end", "chat_id": _binding_cid,
                                            "sender_id": f"oauth:{username}",
                                        }))
                                        self._log("🚫 WS: blocked binding chat msg → static reply")
                                        continue
                                    if _env.get("type") == "attach":
                                        # Attaching to binding chat → allow (WebUI needs it)
                                        pass
                            except Exception:
                                pass

                        processed, blocked = self._platform.process_commander_message(
                            data, username, real_name, is_commander)
                        if blocked is not None:
                            await client_ws.send_text(json.dumps({
                                "event": "blocked", "type": "blocked",
                                "text": blocked}))
                            continue
                        # Session Bridge: force persisted session for attach,
                        # let other envelope types (message, new_chat, command, etc.)
                        # flow with the WebUI's chat_id — Neo auto-attaches on first
                        # message so each chat keeps its own conversation.
                        cid = current_chat_id or session_cid
                        if cid:
                            try:
                                env = json.loads(processed)
                                if isinstance(env, dict) and env.get("type") == "attach":
                                    env["chat_id"] = cid
                                    processed = json.dumps(env)
                            except Exception:
                                pass
                        await neo_ws.send(processed)
                except (WebSocketDisconnect, Exception):
                    pass

            async def neo_to_client():
                """neo → Commander + cluster_log + session tracking."""
                nonlocal current_chat_id
                try:
                    while True:
                        data = await neo_ws.recv()
                        if isinstance(data, bytes):
                            data = data.decode("utf-8", errors="replace")

                        # ── Session tracking ──
                        try:
                            env = json.loads(data)
                            ev = env.get("event", "") if isinstance(env, dict) else ""
                            if ev == "ready":
                                cid = env.get("chat_id", "")
                                if cid and session_key:
                                    if not prev_chat_id:
                                        # First connection: store session
                                        self._ws_sessions[session_key] = cid
                                        current_chat_id = cid
                                ready_chat_event.set()
                            elif ev == "attached" and attach_sent:
                                attached_confirm.set()
                        except Exception:
                            pass

                        # 1) passthrough to Commander's WebUI
                        await client_ws.send_text(data)
                        # 2) also inject as cluster_log for LegionTerminal
                        cluster = self._build_cluster_log(primary_agent, data)
                        if cluster:
                            try:
                                await client_ws.send_text(cluster)
                            except Exception:
                                pass
                except (websockets.exceptions.ConnectionClosed, Exception):
                    pass

            await asyncio.gather(client_to_neo(), neo_to_client())

        finally:
            # Teardown
            observer_stop.set()
            periodic_task.cancel()
            for t in observer_tasks:
                t.cancel()
            try:
                await neo_ws.close()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# create_app() — instantiate Gatekeeper, wire routes, return FastAPI
# ═══════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    """Create and wire the FastAPI application.

    Gatekeeper state is fully encapsulated in a single Gatekeeper instance.
    Module-level globals are limited to the read-only ``platform`` singleton.
    """
    gk = Gatekeeper()
    gk.setup()

    # ── FastAPI app ──
    _app = FastAPI(lifespan=gk._lifespan)
    _auth_mw = gk._platform.create_auth_middleware()
    if _auth_mw is not None:
        _app.add_middleware(_auth_mw)
    _app.add_middleware(SessionMiddleware, **gk._platform.session_kwargs)

    # ── Platform-specific routes (OAuth, etc.) ──
    gk._platform.register_routes(_app)

    # ── Force HTTPS scheme (proxy awareness) ──
    @_app.middleware("http")
    async def _force_https(request: Request, call_next):
        request.scope["scheme"] = "https"
        return await call_next(request)

    # ── Channel bind routes (from cloud-agent-gateway) ─────────
    try:
        _bindings = discover_bindings()
    except Exception as _be:
        print(f"⚠️ bindings discover failed: {_be}")
        _bindings = []
    gk._bindings = _bindings

    for _spec in _bindings:
        _ch = _spec.name
        _html = _spec.bind_page_html

        async def _bind_page(request: Request, _ch=_ch, _html=_html):
            _user = request.session.get("user")
            if not _user:
                return RedirectResponse("/")
            return HTMLResponse(_html)

        async def _bind_submit(request: Request, _ch=_ch):
            _user = request.session.get("user")
            if not _user:
                return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
            _username = gk._platform.extract_username(_user)
            _agent = gk._platform.get_agent_for_user(_username)
            if not _agent:
                return JSONResponse(
                    {"ok": False, "error": f"用户 {_username} 没有关联的 agent"},
                    status_code=403,
                )
            _form = {}
            try:
                ct = request.headers.get("content-type", "")
                if "application/json" in ct:
                    _body_data = await request.json()
                    for _k, _v in _body_data.items():
                        _form[_k] = str(_v).strip()
                else:
                    _raw = await request.form()
                    for _k in _raw.keys():
                        _form[_k] = str(_raw.get(_k, "")).strip()
            except Exception:
                pass
            # Write credential via platform (auto-syncs for ModelScope)
            gk._platform.write_credential(_agent, _ch, dict(_form))

            # Also update config.json so the channel is enabled
            _cfg = gk._platform.read_config(_agent)
            _ch_cfg = _cfg.get("channels", {}).get(_ch, {})
            _ch_cfg["enabled"] = True
            _ch_cfg.setdefault("allow_from", ["*"])
            _ch_cfg.update(dict(_form))
            _cfg.setdefault("channels", {})[_ch] = _ch_cfg
            gk._platform.write_config(_agent, _cfg)

            print(f"✅ /bind/{_ch}: user={_username} → agent={_agent}, creds → {_instance_dir}/channels/{_ch}/")

            # ── Restart agent so it loads fresh config.json with new channel ──
            _token = f"instances/{_agent}/"
            _killed = 0
            for _cmdline_path in _glob_module.glob("/proc/[0-9]*/cmdline"):
                try:
                    with open(_cmdline_path, "rb") as _f:
                        _raw = _f.read()
                    if _token.encode() in _raw and b"nanobot gateway" in _raw:
                        _pid = int(os.path.basename(os.path.dirname(_cmdline_path)))
                        os.kill(_pid, _signal.SIGTERM)
                        _killed += 1
                        print(f"🔄 /bind/{_ch}: sent SIGTERM to {_agent} (PID {_pid})")
                except (OSError, ProcessLookupError):
                    continue
            if _killed:
                print(f"🔁 /bind/{_ch}: killed {_killed} process(es) — gatekeeper will auto-resurrect")
            else:
                print(f"⚠️ /bind/{_ch}: no gateway process found for {_agent} — manual restart may be needed")

            return JSONResponse({"ok": True, "channel": _ch, "agent": _agent, "message": f"{_ch} 已绑定"})

        _app.get(f"/bind/{_ch}")(_bind_page)
        _app.post(f"/bind/{_ch}/submit")(_bind_submit)

        # Register public sub-routes from BindingSpec (e.g., /bind/wechat/qr, /bind/wechat/status)
        # /submit is already handled by gatekeeper's _bind_submit above
        for _suffix, _method, _handler in _spec.public_routes:
            if _suffix == "/submit":
                continue
            async def _sub_handler(request: Request, _handler=_handler):
                if not request.session.get("user"):
                    return JSONResponse({"error": "请先登录"}, status_code=401)
                _username = gk._platform.extract_username(request.session.get("user"))
                request.state.bound_agent = gk._platform.get_agent_for_user(_username) or "default"
                return await _handler(request)
            _app.add_api_route(f"/bind/{_ch}{_suffix}", _sub_handler, methods=[_method])

    # ── Agent management routes ───────────────────────────────
    create_agent_routes(_app, gk)

    # ── Wire routes ───────────────────────────────────────────
    _app.get("/health")(gk._handle_health)
    _app.post("/api/squad/relay")(gk._handle_relay)
    _app.post("/api/squad/tasks")(gk._handle_tasks_post)
    _app.get("/api/squad/tasks")(gk._handle_tasks_get)
    _app.get("/api/squad/sessions")(gk._handle_sessions)
    _app.api_route("/api/squad/sessions/{path:path}",
                   methods=["GET", "POST", "DELETE"])(gk._handle_sessions_sub)
    _app.get("/")(gk._handle_index)
    _app.api_route("/{path:path}",
                   methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])(gk._handle_catch_all)
    _app.websocket("/{path:path}")(gk._handle_ws_proxy)

    return _app


# ═══════════════════════════════════════════════════════════════
# Entrypoint
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = create_app()
    import uvicorn
    port = int(os.environ.get("GATEKEEPER_PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
