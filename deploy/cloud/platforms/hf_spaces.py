"""
HF Spaces Cloud Platform.

Handles HuggingFace Spaces OAuth, filesystem paths, and platform-specific
initialisation.  No multi-agent squad logic (gatekeeper / relay / user-agent
mapping) — those are layered on top by ``nanobot-legion``.

OAuth uses manual httpx HTTP calls (mirroring the proven ModelScope pattern)
to avoid authlib cookie/CSRF issues in reverse-proxy chains.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from authlib.integrations.starlette_client import OAuth

from platforms.base import CloudPlatformProtocol


def _log(msg: str) -> None:
    sys.stderr.write(f"[hf-spaces] {msg}\n")
    sys.stderr.flush()


class HFSpacesPlatform(CloudPlatformProtocol):
    """Platform implementation for HuggingFace Spaces."""

    name = "hf-spaces"

    # ── Filesystem ──

    @property
    def data_root(self) -> str:
        return "/data"

    def instance_path(self, name: str) -> str:
        return f"{self.data_root}/instances/{name}"

    # ── OAuth ──

    def register_oauth(self) -> Any:
        oauth = OAuth()
        cid = os.environ.get("OAUTH_CLIENT_ID", "")
        cs = os.environ.get("OAUTH_CLIENT_SECRET")
        _log(f"OAuth CLIENT_ID: {cid[:8]}...  SECRET={'SET' if cs else 'MISSING'}")
        try:
            oauth.register(
                name="huggingface",
                client_id=cid,
                client_secret=cs,
                server_metadata_url="https://huggingface.co/.well-known/openid-configuration",
                client_kwargs={"scope": "openid profile"},
            )
            _log("OAuth registered successfully")
        except Exception as exc:
            _log(f"OAuth register FAILED: {exc}")
        return oauth

    login_route_path = "/login"
    callback_route_path = "/auth/callback"

    async def exchange_token(self, request: Any) -> dict | None:
        """Manual OAuth token exchange — bypasses authlib cookie/CSRF issues."""
        code = request.query_params.get("code")
        if not code:
            _log("No authorisation code in callback")
            return None

        client_id = os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        # Build redirect_uri from the request
        try:
            redirect_uri = str(request.url_for("auth")).replace("http://", "https://")
        except Exception:
            redirect_uri = ""

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
                _log(f"Token exchange failed: {token_resp.text[:200]}")
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return None

            # Step 2 — fetch userinfo
            user_resp = await http.get(
                "https://huggingface.co/oauth/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_resp.status_code != 200:
                return None

            return {"userinfo": user_resp.json(), "access_token": access_token}

    async def fetch_userinfo(self, token: dict) -> dict | None:
        return token.get("userinfo") if token else None

    def extract_username(self, userinfo: dict) -> str:
        return (
            userinfo.get("preferred_username")
            or userinfo.get("username")
            or userinfo.get("name")
            or "Unknown"
        )

    # ── Entrypoint setup ──

    @staticmethod
    def setup() -> str:
        """HF Spaces setup: unfreeze env vars from /proc/1/environ."""
        proc_env = "/proc/1/environ"
        if not os.path.exists(proc_env):
            return ""

        exports: list[str] = []
        try:
            with open(proc_env, "rb") as f:
                raw = f.read().split(b"\0")
            for item in raw:
                if not item:
                    continue
                try:
                    name, value = item.decode("utf-8", errors="replace").split("=", 1)
                except ValueError:
                    continue
                # Unfreeze relevant variables for child processes
                if name.startswith(("NANOBOT_", "SQUAD_", "HF_", "OAUTH_")):
                    exports.append(f"export {name}='{value}'")
                    os.environ[name] = value
        except Exception as exc:
            _log(f"env unfreeze failed: {exc}")

        return "\n".join(exports)
