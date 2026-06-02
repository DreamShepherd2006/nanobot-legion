"""
ModelScope Studio Cloud Platform.

Handles ModelScope OAuth, filesystem paths, dataset-backed configuration,
and platform-specific initialisation.  Uses manual httpx OAuth flow to
bypass authlib nonce-validation failures on ModelScope.

No multi-agent squad logic (gatekeeper / relay / user-agent mapping) —
those are layered on top by ``nanobot-legion``.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import httpx
from authlib.integrations.starlette_client import OAuth

from platforms.base import CloudPlatformProtocol

logger = logging.getLogger("cloud.modelscope")

_MODELSCOPE_OIDC_CONFIG = "https://modelscope.cn/.well-known/openid-configuration"


def _log(msg: str) -> None:
    sys.stderr.write(f"[modelscope] {msg}\n")
    sys.stderr.flush()


def _get_oauth_client() -> OAuth:
    """Create a minimal OAuth object for ModelScope.

    Registers the MS provider by hand because authlib's automatic OIDC
    discovery causes nonce-validation failures on ModelScope.
    """
    oauth = OAuth()
    oauth.register(
        name="modelscope",
        client_id=os.environ.get("OAUTH_CLIENT_ID", ""),
        client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
        server_metadata_url=_MODELSCOPE_OIDC_CONFIG,
        client_kwargs={
            "scope": "profile",  # avoid 'openid' → no nonce
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    return oauth


class ModelScopePlatform(CloudPlatformProtocol):
    """Platform implementation for ModelScope Studio."""

    name = "modelscope"

    # ── Filesystem ──

    @property
    def data_root(self) -> str:
        return "/mnt/workspace"

    def instance_path(self, name: str) -> str:
        return f"{self.data_root}/instances/{name}"

    # ── OAuth ──

    def register_oauth(self) -> Any:
        return _get_oauth_client()

    login_route_path = "/login"
    callback_route_path = "/auth/callback"

    async def exchange_token(self, request: Any) -> dict | None:
        """Manual OAuth token exchange — bypasses authlib nonce issues on MS."""
        code = request.query_params.get("code")
        if not code:
            return None

        client_id = os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        redirect_uri = str(request.url).split("?")[0]

        async with httpx.AsyncClient(timeout=15) as http:
            token_resp = await http.post(
                f"{_MODELSCOPE_OIDC_CONFIG.replace('.well-known/openid-configuration', '')}oauth/token",
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
                logger.warning(f"Token exchange failed: {token_resp.text[:200]}")
                return None

            token_data = token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return None

            user_resp = await http.get(
                f"{_MODELSCOPE_OIDC_CONFIG.replace('.well-known/openid-configuration', '')}oauth/userinfo",
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
        """MS setup: unfreeze env vars from /proc/1/environ, set DATA_ROOT."""
        exports: list[str] = []
        proc_env = "/proc/1/environ"

        if os.path.exists(proc_env):
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
                    if name.startswith(("NANOBOT_", "OAUTH_", "DEEPSEEK_")):
                        exports.append(f"export {name}='{value}'")
                        os.environ[name] = value
            except Exception as exc:
                _log(f"env unfreeze failed: {exc}")

        # Ensure correct data root for ModelScope
        exports.append("export DATA_ROOT='/mnt/workspace'")

        return "\n".join(exports)

    @staticmethod
    async def fetch_userinfo(token_data: dict) -> dict | None:
        """Fetch userinfo from ModelScope OAuth endpoint."""
        import httpx

        access_token = token_data.get("access_token", "")
        if not access_token:
            return None
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://modelscope.cn/api/v1/oauth2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as exc:
            import sys

            sys.stderr.write(f"[modelscope] fetch_userinfo error: {exc}\n")
        return None
