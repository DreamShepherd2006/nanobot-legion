#!/bin/bash
# ============================================================================
# nanobot cloud entrypoint.sh
# Single entrypoint — routes to squad or plain cloud at startup.
#
#   ☁️  Plain cloud  → detect platform, init storage, launch nanobot
#   🦁 Squad Legion → delegate to deploy/huggingface/launch.sh
#
# Set SQUAD_LEGION=true in Dockerfile for squad deployments.
# ============================================================================
set -e

echo "🚀 nanobot cloud entrypoint starting..."

# ── 0. Basic environment ──────────────────────────────────────────
export HOME="/home/nanobot"
export PATH="/home/nanobot/.local/bin:$PATH"
export PYTHONPATH="/app:${PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

# ── 1. Route to squad if Legion mode ──────────────────────────────
if [ "${SQUAD_LEGION:-}" = "true" ]; then
    echo "🦁 Squad Legion mode — delegating to launch.sh"
    exec /app/deploy/huggingface/launch.sh
fi

# ── 2. Platform detection ─────────────────────────────────────────
echo "🔍 Detecting cloud platform..."
eval "$(python3 /app/deploy/cloud/platform_setup.py)"
echo "✅ Platform: ${DEPLOY_PLATFORM:-unknown}"

# ── 3. Storage-first: seed → persistent ───────────────────────────
DATA_ROOT="${DATA_ROOT:-/data}"
echo "📂 data_root = $DATA_ROOT"

PERSIST="$DATA_ROOT/instances"
SEED="/app/seed/instances"
if [ -d "$SEED" ] && [ ! -d "$PERSIST/_template" ]; then
    echo "📋 First run — seeding instances"
    mkdir -p "$PERSIST"
    cp -r "$SEED"/* "$PERSIST/"
fi

mkdir -p "$HOME/.nanobot"
ln -sfn "$DATA_ROOT/instances" "$HOME/.nanobot/instances" 2>/dev/null || true
echo "✅ Storage linked"

# ── 4. Launch ─────────────────────────────────────────────────────
echo "☁️  Starting nanobot..."
exec nanobot run
