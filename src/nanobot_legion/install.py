#!/usr/bin/env python3
"""nanobot-legion post-install: deploy squad patches & assets.

Usage (in Dockerfile):
    python3 -m nanobot_legion.install

Steps:
  1. Copy patched Python files → site-packages/nanobot/
  2. Copy pre-built patched webui dist → site-packages/nanobot/web/dist/
     (single-agent mode) AND /app/legion_webui/ (Legion mode via launch.sh)
  3. Extract deploy assets (entrypoint.sh, launch.sh, configs, etc.)
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
    """Copy pre-built patched webui dist to two locations:

    1. site-packages/nanobot/web/dist/  — used by single-agent mode
    2. /app/legion_webui/               — symlinked by launch.sh in Legion mode

    The nanobot fork does not ship a built frontend, so the patched dist is
    the only webui available.  Both modes receive the same dist; the Legion
    webui includes LegionTerminal/Roster which are inactive outside squad
    mode, and pinned sidebar keys work identically in both modes.
    """
    src = pkg_dir / "webui_dist"

    if not src.is_dir() or not any(src.iterdir()):
        print("  ⚠️  webui_dist empty — skipping")
        return

    # Target 1: site-packages (single-agent mode)
    dst_site = nanobot_dir / "web" / "dist"
    if dst_site.exists():
        shutil.rmtree(dst_site)
    dst_site.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        d = dst_site / item.name
        if item.is_dir():
            shutil.copytree(item, d)
        else:
            shutil.copy2(item, d)
    n_site = sum(1 for _ in dst_site.rglob("*") if _.is_file())
    print(f"  ✅ webui dist → {dst_site} ({n_site} files)")

    # Target 2: /app/legion_webui/ (Legion mode, launch.sh symlink)
    dst_app = Path("/app/legion_webui")
    if dst_app.exists():
        shutil.rmtree(dst_app)
    dst_app.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        d = dst_app / item.name
        if item.is_dir():
            shutil.copytree(item, d)
        else:
            shutil.copy2(item, d)
    n_app = sum(1 for _ in dst_app.rglob("*") if _.is_file())
    print(f"  ✅ webui dist → {dst_app} ({n_app} files)")


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
