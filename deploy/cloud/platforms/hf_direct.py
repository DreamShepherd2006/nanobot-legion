"""
HF Direct / Local Fallback Platform.

Used when no cloud platform is detected (HF_Direct on HuggingFace,
local Docker deployment, or any non-cloud environment).

No OAuth — uses native nanobot authentication.
"""

from __future__ import annotations

from typing import Any

from platforms.base import CloudPlatformProtocol


class HFDirectPlatform(CloudPlatformProtocol):
    """Fallback platform for local / HF Direct deployments."""

    name = "hf-direct"

    # ── Filesystem ──

    @property
    def data_root(self) -> str:
        return "/data"

    def instance_path(self, name: str) -> str:
        return f"{self.data_root}/instances/{name}"

    # ── OAuth (not supported on local/direct) ──

    def register_oauth(self) -> Any:
        return None

    login_route_path = "/login"
    callback_route_path = "/auth/callback"

    async def exchange_token(self, request: Any) -> dict | None:
        return None

    async def fetch_userinfo(self, token: dict) -> dict | None:
        return None

    def extract_username(self, userinfo: dict) -> str:
        return "Unknown"

    # ── Entrypoint setup ──

    @staticmethod
    def setup() -> str:
        return ""
