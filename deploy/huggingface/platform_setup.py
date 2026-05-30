#!/usr/bin/env python3
"""Platform-specific runtime setup (called by entrypoint.sh before agent launch).

All platform-specific logic lives in ``platforms/<name>.py`` via
``PlatformProtocol.setup()``. This bridge script instantiates the detected
platform and prints shell variable assignments for entrypoint.sh to ``eval``.
"""
from __future__ import annotations

import sys
import os

# Ensure /app is on sys.path so we can import platforms + squad_config_loader
sys.path.insert(0, "/app")

# MUST import squad_config_loader BEFORE platforms!
# It sets DEPLOY_PLATFORM at module level, which platforms._detect()
# reads in matches() step 0 to override env-based detection.
import squad_config_loader  # noqa: E402,F401

from platforms import platform

print(f"🧬 [Platform Setup] detected → {platform.name}", file=sys.stderr)
sys.stderr.flush()

exports = platform.setup()
if exports:
    print(exports)
    sys.stdout.flush()
