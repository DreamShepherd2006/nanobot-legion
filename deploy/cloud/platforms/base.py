"""
Cloud Platform Protocol — abstract interface for cloud-space deployment.

Each platform (HF Spaces, ModelScope, etc.) implements this protocol.
The core entrypoint depends only on this interface, never on platform specifics.

PlatformSpec (dataclass) follows the same data-driven registry pattern as
``nanobot.providers.registry.ProviderSpec`` — detection rules are pure data
so the registry can evaluate matches without importing platform implementations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


# ── PlatformSpec — data-driven registry entry (mirrors ProviderSpec) ──


@dataclass(frozen=True)
class PlatformSpec:
    """Metadata for one deployment platform.

    Like ``ProviderSpec`` for LLM providers, this is pure data — the registry
    evaluates detection rules without importing the platform implementation.
    """

    name: str
    display_name: str = ""
    module: str = ""  # relative import path, e.g. ".hf_spaces"

    # Detection rules — evaluated in priority order by ``matches()``.
    detect_env: str = ""        # env var that must be set (e.g. "HF_SPACE")
    detect_env_value: str = ""  # optional exact value match for detect_env
    detect_env_alt: str = ""    # alternative env var (e.g. "SPACE_ID")
    detect_url_contains: str = ""  # substring in OIDC_CONFIG_URL
    detect_url_env: str = "OIDC_CONFIG_URL"
    detect_empty_url_is_match: bool = False

    priority: int = 50
    is_fallback: bool = False

    @property
    def label(self) -> str:
        return self.display_name or self.name

    def matches(self) -> bool:
        """Evaluate detection rules against the current environment."""
        if self.is_fallback:
            return False

        # 0) Explicit override
        _deploy = os.environ.get("DEPLOY_PLATFORM", "")
        if _deploy:
            return self.name == _deploy

        # 1) Structured env detection (with optional exact-value match)
        if self.detect_env:
            raw = os.environ.get(self.detect_env)
            if raw is not None:
                if self.detect_env_value:
                    return raw == self.detect_env_value
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


# ── CloudPlatformProtocol — minimal cloud platform interface ──


class CloudPlatformProtocol(Protocol):
    """Contract every cloud platform module must fulfil.

    This is the *upstream* interface — it contains only cloud-deployment
    concerns (detection, paths, OAuth, setup).  Multi-agent squad features
    such as gatekeeper routing, relay permissions, and agent-to-user mapping
    are layered on top by ``nanobot-legion`` and are NOT part of this protocol.
    """

    # ── Identity ──
    @property
    def name(self) -> str:
        """Human-readable platform name (e.g. 'hf-spaces', 'modelscope')."""
        ...

    # ── Filesystem ──
    @property
    def data_root(self) -> str:
        """Persistent data root for this platform.

        HF Spaces: ``/data``, ModelScope: ``/mnt/workspace``, local: ``/data``.
        """
        ...

    def instance_path(self, name: str) -> str:
        """Filesystem path for a named instance's persistent workspace."""
        ...

    # ── OAuth ──
    def register_oauth(self) -> Any:
        """Initialise and return an OAuth client for this platform.

        Called once at startup.  Returns an object usable for login/callback.
        """
        ...

    @property
    def login_route_path(self) -> str:
        """URL path for the login endpoint, e.g. '/login'."""
        ...

    @property
    def callback_route_path(self) -> str:
        """URL path for the OAuth callback, e.g. '/auth/callback'."""
        ...

    async def exchange_token(self, request: Any) -> dict | None:
        """Exchange the OAuth authorisation code for an access token.

        Returns a dict with at least ``{'userinfo': {...}}`` on success,
        or ``None`` on failure.
        """
        ...

    async def fetch_userinfo(self, token: dict) -> dict | None:
        """Fetch user profile from the identity provider's userinfo endpoint."""
        ...

    def extract_username(self, userinfo: dict) -> str:
        """Extract the canonical username from a userinfo dict."""
        ...

    # ── Entrypoint setup ──

    @staticmethod
    def setup() -> str:
        """Platform-specific initialisation before agent launch.

        Called by entrypoint.sh via ``platform_setup.py``.  Operates directly
        on the process environment and returns shell variable assignments
        for ``eval`` back into entrypoint.sh.

        Default: no-op.
        """
        return ""
