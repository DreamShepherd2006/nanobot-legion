#!/usr/bin/env python3
"""nanobot-legion post-install: deploy squad patches & assets.

Usage (in Dockerfile):
    python3 -m nanobot_legion.install

Steps:
  1. Copy patched Python files → site-packages/nanobot/
  2. Copy pre-built patched webui dist → /app/legion_webui/ (not overwriting
     nanobot's original; launch.sh symlinks at runtime for Legion mode)
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


def _deploy_webui_dist(pkg_dir: Path) -> None:
    """Copy pre-built patched webui dist to /app/legion_webui/.

    Deploys to a neutral location instead of overwriting nanobot's original
    web/dist, so single-agent mode (which doesn't run launch.sh) keeps the
    original webui with correct sidebar pinning.
    """
    src = pkg_dir / "webui_dist"
    dst = Path("/app/legion_webui")

    if not src.is_dir() or not any(src.iterdir()):
        print("  ⚠️  webui_dist empty — skipping")
        return

    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        d = dst / item.name
        if item.is_dir():
            shutil.copytree(item, d)
        else:
            shutil.copy2(item, d)

    n_files = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"  ✅ webui dist deployed → {dst} ({n_files} files)")


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
    _deploy_webui_dist(pkg_dir)

    from nanobot_legion.deploy.extract_assets import extract
    extract()
    print("✅ nanobot-legion install complete")


if __name__ == "__main__":
    main()
