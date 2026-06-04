"""
Cloud platform auto-detection.

Follows the data-driven registry pattern from ``nanobot.providers.registry``
(ProviderSpec + PROVIDERS tuple) and the auto-discovery pattern from
``nanobot.channels.registry`` (pkgutil.iter_modules).

At import time the registry evaluates ``PlatformSpec.matches()`` for every
entry in priority order; the first match wins.  The winning platform
implementation is lazy-imported only after detection succeeds.

Usage::

    from platforms import platform
    print(platform.name, platform.data_root)
"""

from __future__ import annotations

import sys
from importlib import import_module

from platforms.base import CloudPlatformProtocol, PlatformSpec

# ── Platform registry ──

PLATFORM_SPECS: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        name="modelscope",
        display_name="ModelScope",
        detect_env="MODELSCOPE_ENVIRONMENT",
        detect_env_value="studio",
        detect_url_contains="modelscope",
        module=".modelscope",
        priority=10,
    ),
    PlatformSpec(
        name="hf-spaces",
        display_name="HF Spaces",
        detect_env="HF_SPACE",
        detect_env_alt="SPACE_ID",
        module=".hf_spaces",
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

platform: CloudPlatformProtocol


# ── Detection ──


def _detect() -> CloudPlatformProtocol:
    """Evaluate specs in priority order; first match wins; fallback otherwise."""
    ordered = sorted(PLATFORM_SPECS, key=lambda s: s.priority)

    for spec in ordered:
        if spec.matches():
            _log(spec.name)
            return _load_platform(spec)

    _log(FALLBACK.name)
    return _load_platform(FALLBACK)


def _load_platform(spec: PlatformSpec) -> CloudPlatformProtocol:
    """Lazy-import the platform module and instantiate its implementation."""
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
            and obj.__name__ not in ("CloudPlatformProtocol", "PlatformProtocol")
        ):
            return obj
    raise ImportError(f"No platform class found in {mod.__name__}")


def _log(name: str) -> None:
    sys.stderr.write(f"[PLATFORM] detected → {name}\n")
    sys.stderr.flush()


platform = _detect()
