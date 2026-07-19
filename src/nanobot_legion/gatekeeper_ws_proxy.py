"""WebSocket proxy — bidirectional Commander→agent bridge with observer captures.

Extracted from gatekeeper.py Batch 2.  Gets Gatekeeper instance from
``client_ws.app.state.gatekeeper`` (same pattern as HTTP handlers).
"""

import asyncio
import json
import os

import websockets
from fastapi import WebSocket, WebSocketDisconnect


async def handle_ws_proxy(path: str, client_ws: WebSocket) -> None:
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
    gk = client_ws.app.state.gatekeeper

    await client_ws.accept()

    # ── Session & identity ──
    session_user = client_ws.scope.get("session", {}).get("user")
    real_name = (gk._platform.extract_username(session_user)
                 if isinstance(session_user, dict) else "Guest")
    is_commander = gk._platform.is_commander(session_user)
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
        for a, i in gk.squad_roster.items()
    }
    for event_type in ("legion_update", "cluster_update"):
        await client_ws.send_text(json.dumps({
            "event": event_type, "type": event_type,
            "data": gk.legion_status,
            "nanobot_version": gk._nanobot_version,
            "roster": initial_roster,
            "logs": [], "messages": [], "history": [],
            "tasks": gk.latest_tasks,
        }))

    # ── Determine primary agent ──
    primary_agent = gk._get_agent_for_user(real_name)
    if not primary_agent:
        gk._log(f"🚫 WS 拒绝: {real_name} (未授权)")
        await client_ws.close(code=4001, reason="unauthorized")
        return
    primary_info = gk.squad_roster.get(
        primary_agent,
        gk.squad_roster.get(gk.webui_agent, {}))
    nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()

    # ── Connect to primary agent ──
    neo_url = (f"ws://127.0.0.1:{primary_info['ws_port']}/{path}"
               + (f"?token={nanobot_token}" if nanobot_token else ""))
    gk._log(f"🔀 WS 路由: {real_name} → {primary_agent} "
             f"(port {primary_info.get('ws_port', '?')} path /{path})")

    neo_ws = None
    try:
        neo_ws = await asyncio.wait_for(
            websockets.connect(neo_url, close_timeout=5), timeout=15)
    except asyncio.TimeoutError:
        gk._log(f"🔌 [WS Proxy] {uname}→{primary_agent} connect timeout")
        try:
            await client_ws.close(code=4003, reason="agent connect timeout")
        except Exception:
            pass
        return
    except Exception as e:
        gk._log(f"🔌 [WS Proxy] {uname}→{primary_agent} error: {e}")
        try:
            await client_ws.close()
        except Exception:
            pass
        return

    # ── Start observer captures for all OTHER agents ──
    observer_stop = asyncio.Event()
    observer_tasks: list[asyncio.Task] = []
    for name, info in gk.squad_roster.items():
        if name == primary_agent:
            continue
        task = asyncio.create_task(
            gk._observer_capture(
                name, info, path, nanobot_token, client_ws, observer_stop))
        observer_tasks.append(task)
    if observer_tasks:
        gk._log(f"👁️ [WS Proxy] {len(observer_tasks)} observer capture loops started")

    # ── Periodic legion_update re-emission ──
    async def _emit_periodic():
        while not observer_stop.is_set():
            await asyncio.sleep(5)
            try:
                await client_ws.send_text(json.dumps({
                    "event": "legion_update", "type": "legion_update",
                    "data": dict(gk.legion_status),
                    "nanobot_version": gk._nanobot_version,
                    "roster": {
                        a: {"id": i["id"], "name": a,
                            "gateway_port": i.get("gateway_port"),
                            "ws_port": i.get("ws_port")}
                        for a, i in gk.squad_roster.items()
                    },
                    "logs": [], "messages": [], "history": [],
                    "tasks": gk.latest_tasks,
                }))
            except Exception:
                break
    periodic_task = asyncio.create_task(_emit_periodic())

    # ── Session Bridge state ──────────────────────────────
    session_key = real_name.lower() if real_name != "Guest" else ""
    prev_chat_id = gk._ws_sessions.get(session_key, "") if session_key else ""
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
                        gk._log(f"🔗 [Session] {real_name}: ready timeout, skipping attach")
                        session_cid = ""
                    if session_cid:
                        gk._log(f"🔗 [Session] {real_name}: attaching to chat_id={session_cid[:12]}…")
                        try:
                            await neo_ws.send(json.dumps(
                                {"type": "attach", "chat_id": session_cid}))
                            attach_sent = True
                            try:
                                await asyncio.wait_for(attached_confirm.wait(), timeout=5.0)
                                gk._log(f"🔗 [Session] {real_name}: attached ✓")
                                current_chat_id = session_cid
                            except asyncio.TimeoutError:
                                gk._log(f"🔗 [Session] {real_name}: attach resp timeout")
                                session_cid = ""
                        except Exception:
                            gk._log(f"🔗 [Session] {real_name}: attach send failed")
                            session_cid = ""

                # ── Main message loop ──
                while True:
                    data = await client_ws.receive_text()

                    # 🚫 Intercept messages to binding chat (static reply, no LLM)
                    _binding_cid = getattr(gk, '_binding_chat_cid', None)
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
                                    gk._log("🚫 WS: blocked binding chat msg → static reply")
                                    continue
                                if _env.get("type") == "attach":
                                    # Attaching to binding chat → allow (WebUI needs it)
                                    pass
                        except Exception:
                            pass

                    processed, blocked = gk._platform.process_commander_message(
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
                                    gk._ws_sessions[session_key] = cid
                                    current_chat_id = cid
                            ready_chat_event.set()
                        elif ev == "attached" and attach_sent:
                            attached_confirm.set()
                    except Exception:
                        pass

                    # 1) passthrough to Commander's WebUI
                    await client_ws.send_text(data)
                    # 2) also inject as cluster_log for LegionTerminal
                    cluster = gk._build_cluster_log(primary_agent, data)
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
