#!/usr/bin/env python3
"""nanobot-legion post-install: deploy squad patches & assets.

Usage (in Dockerfile):
    python3 -m nanobot_legion.install

Steps:
  1. Copy patched Python files → site-packages/nanobot/
  2. Copy vanilla webui dist → /app/vanilla_webui/
      (single-agent mode — entrypoint.sh uses NANOBOT_WEBUI_DIST)
  3. Copy Legion webui source → /app/legion_webui_src/
      (Dockerfile builds it → /app/legion_webui/;
       Legion mode — launch.sh uses NANOBOT_WEBUI_DIST)
  4. Extract deploy assets (entrypoint.sh, launch.sh, configs, etc.)
"""

import os
import shutil
import sys
from pathlib import Path


def _find_nanobot_dir() -> Path:
    import nanobot
    return Path(nanobot.__file__).parent


def _deploy_patched_python(nanobot_dir: Path, pkg_dir: Path) -> None:
    """Copy pre-patched Python files over the installed nanobot."""
    src_dir = pkg_dir / "patched_files" / "nanobot"
    if not src_dir.is_dir():
        print("  ⚠️  patched_files/ not found — skipping Python patches")
        return

    for root, _, files in os.walk(src_dir):
        for fname in files:
            rel = os.path.relpath(os.path.join(root, fname), src_dir)
            src = Path(root) / fname
            dst = nanobot_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  ✅ {rel}")
    print("  ✅ patched Python files deployed")


def _deploy_webui_dist(nanobot_dir: Path, pkg_dir: Path) -> None:
    """Copy webui dists to /app/ only.

    Runtime activation uses the NANOBOT_WEBUI_DIST env var (patched into
    nanobot's _default_webui_dist()).  No symlinks or chmod needed.

    1. Vanilla nanobot webui → /app/vanilla_webui/
        Used by single-agent mode (entrypoint.sh exports NANOBOT_WEBUI_DIST).

    2. Legion-patched webui  → /app/legion_webui/
        Used by Legion mode (launch.sh exports NANOBOT_WEBUI_DIST).
    """
    # Target 1: vanilla webui → /app/vanilla_webui/
    vanilla_src = pkg_dir / "vanilla_webui_dist"
    dst_vanilla = Path("/app/vanilla_webui")

    if vanilla_src.is_dir() and any(vanilla_src.iterdir()):
        if dst_vanilla.exists():
            shutil.rmtree(dst_vanilla)
        dst_vanilla.mkdir(parents=True, exist_ok=True)
        for item in vanilla_src.iterdir():
            d = dst_vanilla / item.name
            if item.is_dir():
                shutil.copytree(item, d)
            else:
                shutil.copy2(item, d)
        n = sum(1 for _ in dst_vanilla.rglob("*") if _.is_file())
        print(f"  ✅ vanilla webui → {dst_vanilla} ({n} files)")
    else:
        print("  ⚠️  vanilla_webui_dist empty — single-agent webui not available")

    # Target 2: Legion webui source → /app/legion_webui_src/
    #   (built by Dockerfile after install — not pre-built dist)
    legion_src = pkg_dir / "deploy" / "webui_src"
    dst_legion = Path("/app/legion_webui_src")

    if legion_src.is_dir() and any(legion_src.iterdir()):
        if dst_legion.exists():
            shutil.rmtree(dst_legion)
        dst_legion.mkdir(parents=True, exist_ok=True)
        for item in legion_src.iterdir():
            d = dst_legion / item.name
            if item.is_dir():
                shutil.copytree(item, d)
            else:
                shutil.copy2(item, d)
        n = sum(1 for _ in dst_legion.rglob("*") if _.is_file())
        print(f"  ✅ legion webui source → {dst_legion} ({n} files)")
    else:
        print("  ⚠️  webui_src empty — Legion webui not available")


def main() -> None:
    print("🔧 nanobot-legion install")

    try:
        nanobot_dir = _find_nanobot_dir()
        print(f"  📍 nanobot @ {nanobot_dir}")
    except ImportError:
        print("  ❌ nanobot not installed — install it before nanobot-legion")
        sys.exit(1)

    import nanobot_legion
    pkg_dir = Path(nanobot_legion.__file__).parent

    _deploy_patched_python(nanobot_dir, pkg_dir)
    _deploy_webui_dist(nanobot_dir, pkg_dir)

    from nanobot_legion.deploy.extract_assets import extract
    extract()
    print("✅ nanobot-legion install complete")


if __name__ == "__main__":
    main()
