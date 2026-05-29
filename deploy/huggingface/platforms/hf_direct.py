"""
HF Direct Platform — Hugging Face Space without platform proxy (Nightly).

- OAuth: authlib + HF OIDC (standard flow)
- Config: environment variables only (HF Secrets)
- Routes: /login /auth /logout
- No HF Bearer token wrapper required for API calls

Used by: DreamShepherd2006/nanobot-multi-agent-nightly
"""

from __future__ import annotations

import datetime
import json
import os
import secrets
import sys

import httpx

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.config import Config as StarletteConfig
from starlette.middleware.base import BaseHTTPMiddleware
from authlib.integrations.starlette_client import OAuth

from platforms.base import PlatformProtocol


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[GATEKEEPER] [{ts}] {msg}", file=sys.stderr)
    sys.stderr.flush()


def _html_page(title: str, body_html: str) -> HTMLResponse:
    """Return a styled debug/success HTML page."""
    return HTMLResponse(
        "<!DOCTYPE html>\n"
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>" + title + "</title>"
        '<style>'
        '  *{margin:0;padding:0;box-sizing:border-box}'
        '  body{display:flex;align-items:center;justify-content:center;'
        '       min-height:100vh;font-family:-apple-system,sans-serif;'
        '       background:#0b0f19;color:#e2e8f0}'
        '  .card{text-align:center;padding:2rem;max-width:420px}'
        '  h1{font-size:1.25rem;margin-bottom:.5rem}'
        '  p{color:#94a3b8;font-size:.875rem;margin-bottom:.5rem;}'
        '  strong{color:#fbbf24}'
        '  details{text-align:left;margin-top:.75rem}'
        '  pre{white-space:pre-wrap;word-break:break-all}'
        '</style></head><body>'
        '<div class="card">'
        + body_html
        + "</div></body></html>"
    )


class HFDirectPlatform(PlatformProtocol):
    """HF Direct (Nightly) platform — no platform proxy."""

    name = "hf-direct"
    _oauth: OAuth | None = None
    _commander_whitelist: list[str] = []
    _user_agent_map: dict[str, str] = {}
    _webui_agent: str = "neo"
    _squad_roster: dict = {}

    def __init__(self) -> None:
        self.refresh_config()

    def refresh_config(self, *, webui_agent: str = "", squad_roster: dict | None = None) -> None:
        """Reload config.  File-first via squad_config_loader, env-var fallback."""
        if webui_agent:
            self._webui_agent = webui_agent
        if squad_roster is not None:
            self._squad_roster = squad_roster
        from squad_config_loader import get_commander_whitelist, get_user_agent_map
        self._commander_whitelist = get_commander_whitelist()
        self._user_agent_map = get_user_agent_map()

    # ── OAuth ──
    def register_oauth(self) -> OAuth:
        starlette_config = StarletteConfig(environ=os.environ)
        self._oauth = OAuth(starlette_config)
        cid = os.environ.get("OAUTH_CLIENT_ID", "MISSING")
        cs = os.environ.get("OAUTH_CLIENT_SECRET")
        _log(f"🔑 OAuth CLIENT_ID prefix: {cid[:4]}... (len={len(cid)}), SECRET={'SET' if cs else 'MISSING'}")
        try:
            self._oauth.register(
                name="huggingface",
                client_id=cid,
                client_secret=cs,
                server_metadata_url="https://huggingface.co/.well-known/openid-configuration",
                client_kwargs={"scope": "openid profile"},
            )
            _log(f"✅ OAuth registered — hasattr huggingface: {hasattr(self._oauth, 'huggingface')}")
        except Exception as e:
            _log(f"❌ OAuth register FAILED: {type(e).__name__}: {e}")
        return self._oauth

    login_route_path = "/login"
    callback_route_path = "/auth/callback"

    async def exchange_token(self, request: Request) -> dict | None:
        """Manual OAuth token exchange — bypasses authlib state-based CSRF.

        authlib's ``authorize_access_token`` relies on a session cookie to
        store/verify the ``state`` parameter.  This cookie is dropped or
        corrupted by the HF reverse-proxy chain, causing 100 % CSRF mismatches.

        Using manual ``httpx`` HTTP calls (mirroring the proven ModelScope
        implementation) eliminates the cookie dependency entirely.
        """
        code = request.query_params.get("code")
        if not code:
            _log("⚠️ No authorisation code in callback")
            return None

        client_id = os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        redirect_uri = str(request.url_for("auth")).replace("http://", "https://")

        async with httpx.AsyncClient(timeout=15) as http:
            # Step 1 — exchange code for access token
            token_resp = await http.post(
                "https://huggingface.co/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json"},
            )
            if token_resp.status_code != 200:
                _log(f"⚠️ Token exchange failed: {token_resp.text[:200]}")
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                _log(f"⚠️ No access_token in response: {token_data}")
                return None

            # Step 2 — fetch userinfo
            user_resp = await http.get(
                "https://huggingface.co/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                _log(f"⚠️ Userinfo failed: {user_resp.text[:200]}")
                return None

            return {"userinfo": user_resp.json(), "access_token": access_token}

    async def fetch_userinfo(self, token: dict) -> dict | None:
        return token.get("userinfo")

    def extract_username(self, userinfo: dict) -> str:
        return (userinfo.get("preferred_username")
                or userinfo.get("username")
                or userinfo.get("name")
                or "Unknown")

    # ── Authorisation ──
    def get_commander_whitelist(self) -> list[str]:
        return self._commander_whitelist

    def get_user_agent_map(self) -> dict[str, str]:
        return self._user_agent_map

    def get_agent_for_user(self, username: str) -> str:
        if not username or username == "Unknown":
            return self._webui_agent
        if username in self._commander_whitelist:
            return self._webui_agent
        peer_key = self._user_agent_map.get(username, "")
        if peer_key and peer_key.startswith("NANOBOT_PEER_"):
            agent_name = peer_key[len("NANOBOT_PEER_"):].lower()
            if agent_name in self._squad_roster:
                return agent_name
        return self._webui_agent

    def is_commander(self, session_user) -> bool:
        if not session_user:
            return False
        username = "Unknown"
        if isinstance(session_user, dict):
            username = self.extract_username(session_user)
        elif isinstance(session_user, str):
            username = session_user
        return username in self._commander_whitelist

    def is_member(self, username: str) -> bool:
        return username.lower() in [k.lower() for k in self._user_agent_map]

    def check_relay_permission(self, sender: str, target: str) -> bool:
        effective = sender.lower()
        agent_to_user: dict[str, str] = {}
        for uname, peer_key in self._user_agent_map.items():
            if isinstance(peer_key, str) and peer_key.upper().startswith("NANOBOT_PEER_"):
                aname = peer_key[len("NANOBOT_PEER_"):].lower()
                agent_to_user[aname] = uname.lower()
        if effective in agent_to_user:
            effective = agent_to_user[effective]
        whitelist_lower = [w.lower() for w in self._commander_whitelist]
        if effective in whitelist_lower:
            return True
        if effective in self._user_agent_map:
            peer_key = self._user_agent_map[effective]
            if isinstance(peer_key, str) and peer_key.upper().startswith("NANOBOT_PEER_"):
                return peer_key[len("NANOBOT_PEER_"):].lower() == target
        return False

    # ── Routing ──
    public_paths = ["/login", "/auth/callback", "/health", "/logout", "/webui/bootstrap"]

    def create_auth_middleware(self) -> BaseHTTPMiddleware:
        platform = self

        class ForceAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                path = request.url.path
                if path.startswith("/api/squad"):
                    return await call_next(request)
                is_public = (
                    any(path == p for p in platform.public_paths)
                    or path.startswith("/assets")
                    or path.startswith("/brand")
                )
                if not is_public and not request.session.get("user"):
                    login_url = str(request.url_for("login")).replace("http://", "https://")
                    return RedirectResponse(url=login_url)
                return await call_next(request)

        return ForceAuthMiddleware

    def register_routes(self, app: FastAPI) -> None:

        @app.get(self.login_route_path)
        async def login(request: Request):
            request.session.clear()
            redirect_uri = str(request.url_for("auth")).replace("http://", "https://")
            client_id = os.environ.get("OAUTH_CLIENT_ID", "")
            state = secrets.token_urlsafe(32)
            request.session["oauth_state"] = state
            auth_url = (
                f"https://huggingface.co/oauth/authorize"
                f"?response_type=code"
                f"&client_id={client_id}"
                f"&state={state}"
                f"&redirect_uri={redirect_uri}"
                f"&scope=openid+profile"
            )
            return _html_page(
                "Sign in to Legion Commander",
                '<style>'
                '  a{display:inline-block;padding:.75rem 2rem;'
                '    background:#3b82f6;color:#fff;text-decoration:none;'
                '    border-radius:8px;font-weight:600;font-size:.875rem}'
                '  a:hover{background:#2563eb}'
                '</style>'
                '<h1>Sign in to Legion Commander</h1>'
                '<p style="margin-bottom:1.5rem">'
                'You will be redirected to Hugging Face in a new tab</p>'
                f'<a href="{auth_url}" target="_blank">'
                'Sign in with Hugging Face \u2197</a>'
            )

        @app.get(self.callback_route_path)
        async def auth(request: Request):
            # state validation: new-tab flow means session cookie may not be
            # available here.  Accept any non-empty state as basic CSRF guard.
            received_state = request.query_params.get("state", "")
            if not received_state:
                _log("OAuth callback missing state param")
                return _html_page(
                    "\u274c Missing State",
                    "<p>OAuth state parameter is missing.</p>"
                    "<p>Please try signing in again.</p>",
                )

            code = request.query_params.get("code")
            error = request.query_params.get("error", "")
            error_desc = request.query_params.get("error_description", "")
            if error:
                _log(f"OAuth callback error: {error} — {error_desc}")
                return _html_page(
                    "\u274c OAuth Error",
                    f'<p>{error}</p><p style="font-size:.75rem">{error_desc}</p>',
                )
            if not code:
                _log("No authorization code in callback")
                return _html_page(
                    "\u26a0\ufe0f No Code",
                    "<p>No authorization code was received.</p>"
                    "<p>Please try signing in again.</p>",
                )
            token = await self.exchange_token(request)
            if not token:
                _log("Token exchange returned None — redirecting to /login")
                return _html_page(
                    "\u26a0\ufe0f Token Exchange Failed",
                    "<p>Unable to exchange authorization code for a token.</p>"
                    "<p>This may be a temporary issue. Please try again.</p>"
                    "<details style='margin-top:1rem;font-size:.75rem;color:#94a3b8'>"
                    f"<summary>Debug</summary><pre>code={code[:8]}...</pre></details>",
                )
            userinfo = await self.fetch_userinfo(token)
            if not userinfo:
                _log("Userinfo fetch returned None")
                return _html_page(
                    "\u26a0\ufe0f User Info Missing",
                    "<p>Failed to retrieve account info.</p>",
                )
            request.session["user"] = userinfo
            name = userinfo.get("preferred_username") or userinfo.get("username") or "user"
            _log(f"✅ OAuth success — signed in as {name}")
            return _html_page(
                "\u2705 Signed In",
                f'<h1>Signed in as <strong>{name}</strong></h1>'
                "<p>You may close this tab and refresh the "
                "Legion Commander page.</p>",
            )

        @app.get("/logout")
        async def logout(request: Request):
            request.session.clear()
            return RedirectResponse(url="/")

    async def startup(self) -> None:
        pass

    # ── WS identity injection ─────────────────────────────────

    def process_commander_message(
        self, data: str, username: str, real_name: str, is_commander: bool
    ) -> tuple[str | None, str | None]:
        """Inject OAuth identity before forwarding to the agent."""
        authorized = is_commander or self.is_member(username)

        if not authorized and username != "guest":
            return (None, "\U0001f512 只读模式: 请登录后再发送消息")

        ident = f"{real_name} (oauth:{username})" if real_name != "Guest" else "Guest"
        sender_id = f"oauth:{username}" if authorized else "guest"

        try:
            envelope = json.loads(data)
        except Exception:
            envelope = {}
        if not isinstance(envelope, dict):
            envelope = {}

        envelope["sender_name"] = ident
        envelope["sender_id"] = sender_id

        if is_commander:
            old_content = envelope.get("content", "")
            prefix = f"[{real_name}]: "
            if isinstance(old_content, str) and not old_content.startswith(prefix):
                envelope["content"] = prefix + old_content

        return (json.dumps(envelope), None)

    # instance_path() inherited from base (reads data_root from squad_config.json)

    @property
    def session_kwargs(self) -> dict:
        return {
            "secret_key": os.environ.get("SESSION_SECRET", "nanobot_commander_secret_123"),
            "https_only": True,
            "same_site": "none",
        }


# ── PlatformSpec registration (data-driven — mirrors ProviderSpec) ──

from platforms.base import PlatformSpec  # PlatformProtocol imported at top

PLATFORM_SPEC = PlatformSpec(
    name="hf-direct",
    display_name="HF Direct",
    module=".hf_direct",
    priority=99,
    is_fallback=True,
)
