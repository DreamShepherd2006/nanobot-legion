"""
Platform Protocol — abstract interface for space-specific auth & routing.

Each platform (HF Staging, HF Direct, ModelScope, etc.) implements this protocol.
The core gatekeeper.py depends only on this interface, never on platform specifics.

PlatformSpec (dataclass) follows the same data-driven registry pattern as
``nanobot.providers.registry.ProviderSpec`` — detection rules are pure data
so the registry can evaluate matches without importing platform implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware


# ── PlatformSpec — data-driven registry entry (mirrors ProviderSpec) ──


@dataclass(frozen=True)
class PlatformSpec:
    """Metadata for one deployment platform.

    Like ``ProviderSpec`` for LLM providers, this is pure data — the registry
    evaluates detection rules without importing the platform implementation.
    """

    name: str
    display_name: str = ""
    module: str = ""  # relative import path, e.g. ".hf_staging"

    # Detection rules — evaluated in priority order by ``matches()``.
    # These mirror ProviderSpec's detect_by_* fields.
    detect_env: str = ""  # env var that must be set (e.g. "HF_SPACE")
    detect_env_value: str = ""  # optional exact value match for detect_env
    detect_env_alt: str = ""  # alternative env var (e.g. "SPACE_ID")
    detect_url_contains: str = ""  # substring in OIDC_CONFIG_URL
    detect_url_env: str = "OIDC_CONFIG_URL"  # which env var to check
    detect_empty_url_is_match: bool = False  # if True, empty URL env counts as match

    # Priority: lower = checked first (follows ProviderSpec tuple order convention).
    priority: int = 50

    # When True, this spec is only used as a fallback — never matches proactively.
    is_fallback: bool = False

    @property
    def label(self) -> str:
        """Human-readable label (mirrors ProviderSpec.label)."""
        return self.display_name or self.name

    def matches(self) -> bool:
        """Evaluate detection rules against the current environment.

        Returns True if this platform should be selected.  Pure data method —
        does NOT import the platform implementation.
        """
        if self.is_fallback:
            return False

        # 0) Global DEPLOY_PLATFORM override — explicit beats everything
        _deploy = os.environ.get("DEPLOY_PLATFORM", "")
        if _deploy:
            return self.name == _deploy

        # 1) Structured env detection (with optional exact-value match)
        if self.detect_env:
            raw = os.environ.get(self.detect_env)
            if raw is not None:
                if self.detect_env_value:
                    if raw == self.detect_env_value:
                        return True
                    # value mismatch — skip this spec, don't fall through
                    return False
                return True

        # 2) Alternative env detection
        if self.detect_env_alt and os.environ.get(self.detect_env_alt):
            return True

        # 3) URL-based detection
        if self.detect_url_contains:
            url = os.environ.get(self.detect_url_env, "")
            if self.detect_url_contains.lower() in url.lower():
                return True
            if self.detect_empty_url_is_match and not url:
                return True

        return False


class PlatformProtocol(Protocol):
    """Contract every platform module must fulfil."""

    # ── Identity ──
    @property
    def name(self) -> str:
        """Human-readable platform name (e.g. 'hf-staging')."""
        ...

    # ── OAuth ──
    def register_oauth(self) -> Any:
        """Initialise and return an authlib OAuth client (or compatible object).

        Called once at startup.  The returned object is stored on the app
        and used by the login/callback routes.
        """
        ...

    @property
    def login_route_path(self) -> str:
        """URL path for the login endpoint, e.g. '/login' or '/api/squad/auth/login'."""
        ...

    @property
    def callback_route_path(self) -> str:
        """URL path for the OAuth callback, e.g. '/auth' or '/api/squad/auth/callback'."""
        ...

    async def exchange_token(self, request: Request) -> dict | None:
        """Exchange the OAuth authorisation code for an access token.

        Returns a dict with at least {'userinfo': {...}} on success,
        or None on failure.
        """
        ...

    async def fetch_userinfo(self, token: dict) -> dict | None:
        """Fetch user profile from the identity provider's userinfo endpoint.

        Returns a dict (raw provider response) or None.
        """
        ...

    def extract_username(self, userinfo: dict) -> str:
        """Extract the canonical username from a userinfo dict.

        Normalises across providers (preferred_username / username / name / …).
        """
        ...

    # ── Authorisation ──
    def get_commander_whitelist(self) -> list[str]:
        """Return the current list of Commander (admin) usernames.

        Source is transparent to the caller — may be env var, config file, etc.
        """
        ...

    def get_user_agent_map(self) -> dict[str, str]:
        """Return the user→agent mapping dict.

        Keys are usernames, values are agent peer keys (e.g. 'NANOBOT_PEER_NEO').
        """
        ...

    def get_agent_for_user(self, username: str) -> str:
        """Resolve which agent a user should be routed to.

        Default: Commander → WEBUI_AGENT; mapped user → their agent; else WEBUI_AGENT.
        """
        ...

    def is_commander(self, session_user: Any) -> bool:
        """Check whether the given session user has Commander privileges."""
        ...

    def check_relay_permission(self, sender: str, target: str) -> bool:
        """Validate whether *sender* is authorised to relay to *target*."""
        ...

    def is_member(self, username: str) -> bool:
        """Check whether *username* is a registered squad member."""
        ...

    # ── Routing ──
    @property
    def public_paths(self) -> list[str]:
        """Paths that do NOT require authentication (e.g. /login, /health)."""
        ...

    def create_auth_middleware(self) -> BaseHTTPMiddleware:
        """Build and return the force-auth middleware for this platform."""
        ...

    def register_routes(self, app: FastAPI) -> None:
        """Register platform-specific HTTP routes (login, callback, logout).

        Called during app startup.  The implementation adds routes directly
        to the FastAPI *app* instance.
        """
        ...

    # ── Lifecycle ──
    async def startup(self) -> None:
        """Optional async startup hook (e.g. OIDC metadata pre-fetch)."""
        ...

    @property
    def session_kwargs(self) -> dict:
        """Keyword arguments for starlette SessionMiddleware."""
        ...

    # ── Filesystem ──
    @property
    def data_root(self) -> str:
        """Filesystem root for persistent data (instances, DLQ, logs).

        Read from ``squad_config.json`` → ``data_root`` field.
        Default: ``/data``.
        """
        try:
            from squad_config_loader import load_config
            return load_config().get("data_root", "/data")
        except Exception:
            return "/data"

    def instance_path(self, name: str) -> str:
        """Returns the filesystem path for an agent instance's workspace.

        Derived from ``data_root`` in squad_config.json.
        e.g. ``/mnt/workspace/instances/{name}`` or ``/data/instances/{name}``
        """
        return f"{self.data_root}/instances/{name}"

    # ── WebSocket commander message processing ──

    def process_commander_message(
        self, data: str, username: str, real_name: str, is_commander: bool
    ) -> tuple[str | None, str | None]:
        """Process a Commander WS message before forwarding to neo.

        Returns ``(processed_data, blocked_reason)``.
        If *blocked_reason* is not None, the message is blocked and
        *blocked_reason* is sent back to the client.

        Default: pass-through (no identity injection, no blocking).
        """
        return (data, None)

    # ── Entrypoint setup (called by entrypoint.sh via platform_setup.py) ──

    @staticmethod
    def setup() -> str:
        """Platform-specific initialization before agent launch.

        Called by entrypoint.sh via ``platform_setup.py``. Operates directly
        on the filesystem/process environment and returns shell variable
        assignments to ``eval`` back into entrypoint.sh.

        Default: no-op (returns empty string).
        """
        return ""
