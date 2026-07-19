"""Gatekeeper HTTP route handlers (extracted from gatekeeper.py).

Each handler reads the Gatekeeper instance from ``request.app.state.gatekeeper``,
which is set during ``create_app``.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import websockets
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 或已映射用户可访问</h3>"


def _gk(request: Request):
    """Convenience: get Gatekeeper from app.state."""
    return request.app.state.gatekeeper


# ── Health ─────────────────────────────────────────────────────

async def handle_health(request: Request) -> dict:
    gk = _gk(request)
    return {"status": "ok", "role": "gatekeeper",
            "agents": len(gk.agent_names)}


# ── Reset ─────────────────────────────────────────────────────

async def handle_reset_setup(request: Request) -> JSONResponse:
    """GET /reset-setup — delete oauth.json to re-enter Phase 1 setup."""
    gk = _gk(request)
    _user = request.session.get("user")
    if not _user:
        return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
    if not gk._platform.is_commander(_user):
        return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
    deleted = []
    candidates = [
        Path(gk._platform.data_root, "oauth.json"),
        Path(gk._platform.data_root, "instances", "oauth.json"),
        Path(gk._platform.data_root).parent / "oauth.json",
        Path("/data", "instances", "oauth.json"),
        Path("/data", "oauth.json"),
        Path("/mnt/workspace", "oauth.json"),
    ]
    for p in candidates:
        try:
            p.unlink()
            deleted.append(str(p))
        except FileNotFoundError:
            pass

    if deleted:
        msg = f"已删除: {', '.join(deleted)}。请重启空间进入 Setup 页面。"
    else:
        msg = "未找到 oauth.json（可能已被删除）。如需重新配置，请重启空间。"
    return JSONResponse({"ok": True, "message": msg, "deleted": deleted})


# ── Relay ──────────────────────────────────────────────────────

async def handle_relay(request: Request):
    """POST /api/squad/relay — cross-agent message relay via WS."""
    gk = _gk(request)

    # Auth
    auth_header = request.headers.get("X-Squad-Token", "")
    if not gk._relay_token or auth_header != gk._relay_token:
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
    if target not in gk.squad_roster:
        return JSONResponse(
            {"status": "roster_miss",
             "error": f"'{target}' not in squad",
             "correlation_id": corr_id}, status_code=404)
    if gk.legion_status.get(target) != "online":
        return JSONResponse(
            {"status": "agent_offline",
             "error": f"'{target}' is offline",
             "correlation_id": corr_id}, status_code=503)

    # Permission
    auth_identity = commander or sender
    if not gk._platform.check_relay_permission(auth_identity, target):
        return JSONResponse({
            "status": "permission_denied",
            "error": f"'{auth_identity}' not authorized for '{target}'",
            "correlation_id": corr_id,
        }, status_code=403)

    # Relay via WebSocket
    target_info = gk.squad_roster[target]
    nanobot_token = os.environ.get("NANOBOT_TOKEN", "").strip()
    ws_url = f"ws://127.0.0.1:{target_info['ws_port']}/"
    if nanobot_token:
        ws_url += f"?token={nanobot_token}"

    try:
        gk._log(f"📨 [Relay] {sender}→{target} connect {ws_url}")
        ws = await asyncio.wait_for(
            websockets.connect(ws_url, close_timeout=5), timeout=15)
        async with ws:
            greeting_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            greeting = json.loads(greeting_raw)
            if greeting.get("event") != "ready":
                gk._log(f"❌ [Relay] unexpected greeting: {greeting}")
                return JSONResponse({
                    "status": "protocol_error",
                    "error": f"expected 'ready' event, got {greeting.get('event')}",
                    "correlation_id": corr_id,
                }, status_code=502)

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
            gk._log(f"📨 [Relay] {sender}→{target} sent ({len(payload)}B)")

            responses: list[str] = []
            try:
                while True:
                    raw = await asyncio.wait_for(ws.recv(),
                                                 timeout=gk._relay_timeout)
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        gk._log(f"📨 [Relay] non-JSON frame ({len(raw)}B)")
                        continue

                    event = data.get("event", "")

                    if event == "error":
                        detail = data.get("detail", "unknown")
                        gk._log(f"❌ [Relay] framework error: {detail}")
                        return JSONResponse({
                            "status": "framework_error",
                            "error": detail,
                            "correlation_id": corr_id,
                        }, status_code=502)

                    if event == "heartbeat":
                        continue

                    if event == "turn_end":
                        reply = "\n".join(responses) if responses else "(empty)"
                        gk._log(f"✅ [Relay] {sender}→{target} ok ({len(reply)} chars)")
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
                    gk._log(f"⏱️ [Relay] timeout with partial ({len(reply)} chars)")
                    return JSONResponse({
                        "status": "partial",
                        "target_response": reply,
                        "target": target,
                        "correlation_id": corr_id,
                    })
                gk._log(f"⏱️ [Relay] timeout ({gk._relay_timeout}s)")
                return JSONResponse({
                    "status": "timeout",
                    "error": f"no response from agent within {gk._relay_timeout}s",
                    "correlation_id": corr_id,
                }, status_code=504)

    except asyncio.TimeoutError:
        gk._log("❌ [Relay] connect timeout (15s)")
        return JSONResponse({
            "status": "connection_error",
            "error": "WebSocket connection timed out",
            "correlation_id": corr_id,
        }, status_code=502)
    except Exception as e:
        gk._log(f"❌ [Relay] {sender}→{target} error: {type(e).__name__}: {e}")
        return JSONResponse({
            "status": "connection_error",
            "error": f"{type(e).__name__}: {e}",
            "correlation_id": corr_id,
        }, status_code=502)


# ── Task Tracking ──────────────────────────────────────────────

async def handle_tasks_post(request: Request):
    """POST /api/squad/tasks — Commander pushes structured task list."""
    gk = _gk(request)
    auth_header = request.headers.get("X-Squad-Token", "")
    if not gk._relay_token or auth_header != gk._relay_token:
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
    gk.latest_tasks = {
        "goal": goal,
        "tasks": tasks,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "updated_by": body.get("updated_by", "unknown"),
    }
    done = sum(1 for t in tasks if t.get("status") == "done")
    gk._log(f"📋 [Tasks] {done}/{len(tasks)} → "
            f"{[t.get('title', '?') for t in tasks[:5]]}")
    return JSONResponse({"status": "ok", "tasks": len(tasks), "done": done})


async def handle_tasks_get(request: Request):
    """GET /api/squad/tasks — read current task list."""
    gk = _gk(request)
    auth_header = request.headers.get("X-Squad-Token", "")
    if not gk._relay_token or auth_header != gk._relay_token:
        return JSONResponse({"status": "unauthorized"}, status_code=401)
    return JSONResponse(
        gk.latest_tasks or {"goal": "", "tasks": [], "updated_by": "none"})


# ── Sessions Proxy ─────────────────────────────────────────────

async def handle_sessions(request: Request):
    gk = _gk(request)
    _uname, target_agent, ws_port = gk._resolve_user_context(request)
    if not target_agent:
        return JSONResponse({"error": "未授权访问"}, status_code=403)
    if not ws_port:
        ws_port = gk.squad_roster.get(gk.webui_agent, {}).get("ws_port", 20002)

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
        gk._log(f"❌ sessions proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


async def handle_sessions_sub(request: Request, path: str):
    gk = _gk(request)
    _uname, target_agent, ws_port = gk._resolve_user_context(request)
    if not target_agent:
        return JSONResponse({"error": "未授权访问"}, status_code=403)
    if not ws_port:
        ws_port = gk.squad_roster.get(gk.webui_agent, {}).get("ws_port", 20002)

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
        gk._log(f"❌ sessions proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


# ── Index (login page / WebUI proxy) ───────────────────────────

async def handle_index(request: Request):
    """Serve login page for guests, or proxy to agent WebUI for auth'd users."""
    gk = _gk(request)
    uname, target_agent, _ws_port = gk._resolve_user_context(request)
    if not uname:
        uname = "Unknown"

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

    if not target_agent:
        return HTMLResponse(_DENIED, status_code=403)

    client = gk._http_clients.get(target_agent)
    if not client:
        client = gk._http_clients.get(gk.webui_agent)
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
        gk._log(f"❌ [GET /] proxy to {target_agent} failed: {e}")
        return HTMLResponse(f"<h1>Agent {target_agent} unreachable</h1>",
                            status_code=502)


# ── Catch-all HTTP proxy ───────────────────────────────────────

async def handle_catch_all(request: Request, path: str):
    """Proxy unmatched HTTP traffic to the user's assigned agent ws_port."""
    gk = _gk(request)
    uname, target_agent, _ws_port = gk._resolve_user_context(request)

    if not target_agent:
        return Response(content="Unauthorized", status_code=403)

    client = gk._http_clients.get(target_agent)
    if not client:
        client = gk._http_clients.get(gk.webui_agent)
    if not client:
        return Response(content="No agent available", status_code=503)

    try:
        url = httpx.URL(
            path=f"/{path}" if path else "/",
            query=request.url.query.encode("utf-8"))
        blacklist = set(h.lower() for h in gk._platform.proxy_header_blacklist)
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in blacklist}
        if "authorization" not in {k.lower() for k in headers}:
            _q = (request.url.query.decode("utf-8")
                  if isinstance(request.url.query, bytes)
                  else request.url.query)
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

        if path == "webui/bootstrap" and rp_resp.status_code == 200:
            body = await rp_resp.aread()
            try:
                data = json.loads(body)
                ws_path = data.get("ws_path", "")
                if ws_path:
                    data["ws_url"] = ws_path
                    body = json.dumps(data).encode("utf-8")
                    gk._log("bootstrap ws_url → ws_path (Host header fix)")
            except Exception as exc:
                gk._log(f"bootstrap ws_url fix skipped: {exc}")
            resp_headers = {k: v for k, v in rp_resp.headers.items()
                             if k.lower() != "content-length"}
            return Response(
                content=body,
                status_code=rp_resp.status_code,
                headers=resp_headers,
                media_type=rp_resp.headers.get("content-type", "application/json"))

        return StreamingResponse(
            rp_resp.aiter_raw(),
            status_code=rp_resp.status_code,
            headers=dict(rp_resp.headers))
    except Exception:
        return Response(content="System Warming Up...", status_code=503)
