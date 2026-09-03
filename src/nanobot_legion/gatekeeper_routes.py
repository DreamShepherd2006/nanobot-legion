"""Squad gatekeeper — FastAPI app factory and route wiring.

Extracted from gatekeeper.py Batch 3b.
"""

import glob as _glob_module
import importlib.metadata
import json
import os
import signal as _signal

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from cloud_agent_gateway.channel_binding import discover as discover_bindings
from cloud_agent_gateway import file_manager

from .agent_config import create_agent_routes
from .gatekeeper import Gatekeeper
from .gatekeeper_handlers import (
    handle_catch_all,
    handle_health,
    handle_index,
    handle_relay,
    handle_reset_setup,
    handle_sessions,
    handle_sessions_sub,
    handle_tasks_get,
    handle_tasks_post,
)
from .gatekeeper_ws_proxy import handle_ws_proxy
from .squad_admin import create_squad_admin_routes

_DENIED_MSG = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 或已映射用户可访问</h3>"


def _installed_rev(pkg_name: str) -> str:
    """Return the pip-installed revision for a distribution (direct_url.json).

    Reads ``requested_revision`` (short hash or tag) so startup logs show
    exactly which commit is running — helps distinguish CD fork validation
    pins from upstream references.
    """
    try:
        dist = importlib.metadata.distribution(pkg_name)
        dj = Path(dist._path) / "direct_url.json"
        if dj.exists():
            data = json.loads(dj.read_text())
            rev = data.get("requested_revision", "?")
            return str(rev)[:7] if rev else "?"
    except Exception:
        pass
    return "?"


def create_app() -> FastAPI:
    """Create and wire the FastAPI application.

    Gatekeeper state is fully encapsulated in a single Gatekeeper instance.
    Module-level globals are limited to the read-only ``platform`` singleton.
    """
    gk = Gatekeeper()
    gk.setup()

    for _pkg in ("nanobot-legion", "nanobot-quant", "cloud-agent-gateway"):
        gk._log(f"[DIAG] installed {_pkg} @{_installed_rev(_pkg)}")

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
            _username = gk._platform.extract_username(_user)
            if not gk._platform.is_commander(_user) and not gk._platform.get_agent_for_user(_username):
                return HTMLResponse(_DENIED_MSG, status_code=403)
            return HTMLResponse(_html)

        async def _bind_submit(request: Request, _ch=_ch):
            _user = request.session.get("user")
            if not _user:
                return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
            _username = gk._platform.extract_username(_user)
            if not gk._platform.is_commander(_user) and not gk._platform.get_agent_for_user(_username):
                return JSONResponse({"ok": False, "error": "仅 Commander 或已映射用户可绑定"}, status_code=403)
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
            _cfg_path = gk._platform._config_path(_agent)
            _ch_cfg_before = _cfg.get("channels", {}).get(_ch, {})
            print(f"🔍 [bind/{_ch}] READ config: {_cfg_path}", flush=True)
            print(f"🔍 [bind/{_ch}] BEFORE: channels.{_ch}.enabled={_ch_cfg_before.get('enabled')}", flush=True)
            _ch_cfg = _ch_cfg_before.copy() if _ch_cfg_before else {}
            _ch_cfg["enabled"] = True
            _ch_cfg.setdefault("allow_from", ["*"])
            _ch_cfg.update(dict(_form))
            _cfg.setdefault("channels", {})[_ch] = _ch_cfg
            print(f"🔍 [bind/{_ch}] AFTER: channels.{_ch}.enabled={_cfg['channels'][_ch]['enabled']}", flush=True)
            gk._platform.write_config(_agent, _cfg)
            print(f"🔍 [bind/{_ch}] WRITE config: {_cfg_path}", flush=True)
            # Verify write persisted
            _verify = gk._platform.read_config(_agent)
            _vch = _verify.get("channels", {}).get(_ch, {})
            print(f"🔍 [bind/{_ch}] VERIFY re-read: channels.{_ch}.enabled={_vch.get('enabled')}", flush=True)

            print(f"✅ /bind/{_ch}: user={_username} → agent={_agent}, creds → channels/{_ch}/")

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
            async def _sub_handler(request: Request, _handler=_handler, _ch=_ch, _suffix=_suffix):
                _user = request.session.get("user")
                if not _user:
                    return JSONResponse({"error": "请先登录"}, status_code=401)
                _username = gk._platform.extract_username(_user)
                if not gk._platform.is_commander(_user) and not gk._platform.get_agent_for_user(_username):
                    return JSONResponse({"error": "仅 Commander 或已映射用户可操作"}, status_code=403)
                _agent = gk._platform.get_agent_for_user(_username) or "default"
                request.state.bound_agent = _agent
                result = await _handler(request)
                # Extract binding status from handler return.
                # Some bindings (wechat) return a Starlette Response wrapping JSON,
                # others return a plain dict.
                _status = None
                if isinstance(result, dict):
                    _status = result.get("status")
                elif hasattr(result, 'body'):
                    try:
                        _body = json.loads(result.body)
                        _status = _body.get("status") if isinstance(_body, dict) else None
                    except Exception:
                        pass
                # When scan-based binding confirms, enable channel in agent's config
                if _status == "confirmed" and _agent != "default":
                    _cfg_key = "weixin" if _ch == "wechat" else _ch
                    _cfg = gk._platform.read_config(_agent)
                    _cfg.setdefault("channels", {}).setdefault(_cfg_key, {"allow_from": ["*"]})
                    _cfg["channels"][_cfg_key]["enabled"] = True
                    gk._platform.write_config(_agent, _cfg)
                    print(f"[GATEKEEPER] 🔓 已启用 {_agent}/{_cfg_key} 通道（{_ch} 扫码绑定确认）", flush=True)
                return result
            _app.add_api_route(f"/bind/{_ch}{_suffix}", _sub_handler, methods=[_method])

    # ── Agent management routes ───────────────────────────────
    create_agent_routes(_app, gk)
    create_squad_admin_routes(_app, gk)

    # ── Business credential routes (nanobot-quant plugin) ───────
    try:
        from nanobot_quant.credential_handlers import register_credential_routes
        register_credential_routes(_app, gk)
        gk._log("📊 已注册 API 凭证管理路由")
    except ImportError:
        pass

    # ── Trading mode routes (nanobot-quant plugin) ───────────────
    try:
        from nanobot_quant.mode_handlers import register_mode_routes
        register_mode_routes(_app, gk)
        gk._log("⚙️ 已注册交易模式管理路由")
    except ImportError:
        pass

    # ── Live trading toggle routes (nanobot-quant plugin) ───────
    try:
        from nanobot_quant.live_handlers import register_live_routes
        register_live_routes(_app, gk)
        gk._log("⚡ 已注册实盘交易开关路由")
    except ImportError:
        pass

    # ── Wallet management routes (nanobot-quant plugin) ─────────
    try:
        from nanobot_quant.wallet_handlers import register_wallet_routes
        register_wallet_routes(_app, gk)
        gk._log("👛 已注册钱包管理路由")
    except ImportError:
        pass

    # ── Gate CEX account routes (nanobot-quant plugin) ─────────
    try:
        from nanobot_quant.gate_handlers import register_gate_routes
        register_gate_routes(_app, gk)
        gk._log("🏦 已注册 Gate CEX 账户路由")
    except ImportError:
        pass

    # ── Token address management routes (nanobot-quant plugin) ──
    try:
        from nanobot_quant.token_handlers import register_token_routes
        register_token_routes(_app, gk)
        gk._log("🪙 已注册代币管理路由")
    except ImportError:
        pass

    # ── TD strategy parameter routes (nanobot-quant plugin) ─────
    try:
        from nanobot_quant.td_params_handlers import register_td_params_routes
        register_td_params_routes(_app, gk)
        gk._log("📐 已注册 TD 参数路由")
    except ImportError:
        pass

    # ── Strategy selection routes (nanobot-quant plugin) ────────
    try:
        from nanobot_quant.strategies_handlers import register_strategy_routes
        register_strategy_routes(_app, gk)
        gk._log("📈 已注册策略选择路由")
    except ImportError:
        pass

    # ── Execution parameter routes (nanobot-quant plugin) ────────
    try:
        from nanobot_quant.exec_params_handlers import register_exec_params_routes
        register_exec_params_routes(_app, gk)
        gk._log("🛡️ 已注册执行参数路由")
    except ImportError:
        pass

    # ── TD sequence table routes (nanobot-quant plugin) ────────
    try:
        from nanobot_quant.td_table_handlers import register_td_table_routes
        register_td_table_routes(_app, gk)
        gk._log("📊 已注册 TD 序列分析路由")
    except ImportError:
        pass

    # ── Backtest page routes (nanobot-quant plugin) ────────────
    try:
        from nanobot_quant.backtest_handlers import register_backtest_routes
        register_backtest_routes(_app, gk)
        gk._log("📈 已注册回测路由")
    except ImportError:
        pass

    # ── OKX options chain page routes (nanobot-quant plugin) ───
    try:
        from nanobot_quant.okx_options_handlers import register_okx_options_routes
        register_okx_options_routes(_app, gk)
        gk._log("🟤 已注册 OKX 期权链路由")
    except ImportError:
        pass

    # ── File manager routes (commander-only) ───────────────────
    async def _fm_list_page(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.list_page(request)

    async def _fm_view_file(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.view_file(request)

    async def _fm_upload_file(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.upload_file(request)

    async def _fm_delete_entry(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.delete_entry(request)

    async def _fm_mkdir(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.mkdir(request)

    async def _fm_touch_file(request: Request):
        _u = request.session.get("user")
        if not _u:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gk._platform.is_commander(_u):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)
        return await file_manager.touch_file(request)

    _app.add_api_route("/files", _fm_list_page, methods=["GET"], response_model=None)
    _app.add_api_route("/files/", _fm_list_page, methods=["GET"], response_model=None)
    _app.add_api_route("/files/view/{path:path}", _fm_view_file, methods=["GET"], response_model=None)
    _app.add_api_route("/files/upload", _fm_upload_file, methods=["POST"], response_model=None)
    _app.add_api_route("/files/delete/{path:path}", _fm_delete_entry, methods=["DELETE"], response_model=None)
    _app.add_api_route("/files/mkdir", _fm_mkdir, methods=["POST"], response_model=None)
    _app.add_api_route("/files/touch", _fm_touch_file, methods=["POST"], response_model=None)

    # ── Wire routes ───────────────────────────────────────────
    _app.state.gatekeeper = gk
    _app.get("/health")(handle_health)
    _app.post("/api/squad/relay")(handle_relay)
    _app.post("/api/squad/tasks")(handle_tasks_post)
    _app.get("/api/squad/tasks")(handle_tasks_get)
    _app.get("/api/squad/sessions")(handle_sessions)
    _app.api_route("/api/squad/sessions/{path:path}",
                   methods=["GET", "POST", "DELETE"])(handle_sessions_sub)
    _app.get("/reset-setup")(handle_reset_setup)
    _app.get("/")(handle_index)
    _app.api_route("/{path:path}",
                   methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])(handle_catch_all)
    _app.websocket("/{path:path}")(handle_ws_proxy)

    return _app
