#!/usr/bin/env python3
"""
Cloud platform detection and setup bridge.

Called by entrypoint.sh to:
1. Detect the current cloud platform
2. Run platform-specific initialisation (env unfreeze, dataset pull, …)
3. Print shell variable assignments for ``eval`` back into entrypoint.sh

All diagnostics go to stderr — stdout MUST contain only shell exports
so that ``eval "$(python3 platform_setup.py)"`` works correctly.
"""

from __future__ import annotations

import os
import sys

# Add deploy/cloud/ to the import path so platforms/ can be found
_cloud_dir = os.path.dirname(os.path.abspath(__file__))
if _cloud_dir not in sys.path:
    sys.path.insert(0, _cloud_dir)

from platforms import platform


def main() -> None:
    sys.stderr.write(f"[platform_setup] detected: {platform.name}\n")
    sys.stderr.write(f"[platform_setup] data_root: {platform.data_root}\n")

    # Run platform-specific setup and print shell exports to stdout
    shell_exports = platform.setup()
    if shell_exports:
        # Also set DEPLOY_PLATFORM so downstream scripts know where we are
        print(f"export DEPLOY_PLATFORM='{platform.name}'")
        print(shell_exports)

    sys.stderr.write("[platform_setup] done\n")
    sys.stderr.flush()


if __name__ == "__main__":
    main()
