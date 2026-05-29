"""
Platform auto-detection.

Follows the data-driven registry pattern from ``nanobot.providers.registry``
(ProviderSpec + PROVIDERS tuple) and the auto-discovery pattern from
``nanobot.channels.registry`` (pkgutil.iter_modules).

At import time the registry evaluates ``PlatformSpec.matches()`` for every
entry in priority order; the first match wins.  The winning platform
implementation is lazy-imported only after detection succeeds.

Usage in gatekeeper.py::
    from platforms import platform
    oauth = platform.register_oauth()
"""

from __future__ import annotations

import sys
from importlib import import_module

from platforms.base import PlatformProtocol, PlatformSpec

# ── Platform registry (data-driven — mirrors PROVIDERS tuple) ──

PLATFORM_SPECS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="modelscope",
        display_name="ModelScope",
        detect_env_alt="MODELSCOPE_ENVIRONMENT",  # future compat: MS may inject this
        detect_url_contains="modelscope",  # legacy: OIDC_CONFIG_URL substring
        module=".modelscope",
        priority=10,
    ),
    PlatformSpec(
        name="hf-staging",
        display_name="HF Staging",
        detect_env="HF_SPACE",
        detect_env_alt="SPACE_ID",
        module=".hf_staging",
        priority=20,
    ),
)

FALLBACK = PlatformSpec(
    name="hf-direct",
    display_name="HF Direct",
    module=".hf_direct",
    priority=99,
    is_fallback=True,
)

platform: PlatformProtocol


# ── Detection ──


def _detect() -> PlatformProtocol:
    """Evaluate specs in priority order; first match wins; fallback otherwise."""
    ordered = sorted(PLATFORM_SPECS, key=lambda s: s.priority)

    for spec in ordered:
        if spec.matches():
            _log(spec.name)
            return _load_platform(spec)

    _log(FALLBACK.name)
    return _load_platform(FALLBACK)


def _load_platform(spec: PlatformSpec) -> PlatformProtocol:
    """Lazy-import the platform module and instantiate its implementation.

    Follows the same pattern as ``nanobot.channels.registry.load_channel_class``:
    import the module, then find the class that implements the protocol.
    """
    mod = import_module(spec.module, __package__)
    cls = _find_platform_class(mod)
    return cls()


def _find_platform_class(mod):
    """Find the first non-Protocol class in *mod* that has a ``name`` attribute."""
    for attr_name in dir(mod):
        obj = getattr(mod, attr_name)
        if (
            isinstance(obj, type)
            and hasattr(obj, "name")
            and obj.__name__ != "PlatformProtocol"
        ):
            return obj
    raise ImportError(f"No platform class found in {mod.__name__}")


def _log(name: str) -> None:
    sys.stderr.write(f"[PLATFORM] detected → {name}\n")
    sys.stderr.flush()


platform = _detect()
