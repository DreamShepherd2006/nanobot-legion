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

# ── 0a. Phase 1: setup wizard (no oauth.json) ────────────────────
if [ ! -f /data/oauth.json ] && [ ! -f /mnt/workspace/oauth.json ]; then
    echo "🛠️  Phase 1 — starting setup wizard..."
    exec python3 -m cloud_agent_gateway.setup
fi

# ── 0b. OAuth: export credentials from oauth.json (Phase 1→2 bridge) ──
for oauth_path in /data/oauth.json /mnt/workspace/oauth.json; do
    if [ -f "$oauth_path" ]; then
        eval $(python3 -c "
import json, shlex
with open('$oauth_path') as f:
    o = json.load(f)
cid = o.get('client_id', '')
secret = o.get('client_secret', '')
if cid and secret and 'OAUTH_CLIENT_ID' not in __import__('os').environ:
    print(f'export OAUTH_CLIENT_ID={shlex.quote(cid)}')
    print(f'export OAUTH_CLIENT_SECRET={shlex.quote(secret)}')
")
        echo "✅ OAuth: loaded from $oauth_path"
        break
    fi
done

# ── 0c. Detect DATA_ROOT (needed for Legion vs single choice) ──────
if [ -d /mnt/workspace ]; then
    DATA_ROOT=/mnt/workspace
elif [ -d /data ]; then
    DATA_ROOT=/data
else
    DATA_ROOT=/tmp
fi

# ── 1. Route to squad if user chose Legion at setup ────────────────
#     setup writes squad_config.json to persistent volume only for Legion mode.
if [ -f "${DATA_ROOT}/legion/squad_config.json" ] && [ -x /app/deploy/huggingface/launch.sh ]; then
    echo "🦁 Squad Legion mode (squad_config.json found) — delegating to launch.sh"
    exec /app/deploy/huggingface/launch.sh
fi

# ── 2. Single-agent Phase 2 → template_launch ─────────────────────
#     Handles platform detection, storage seeding, gateway + oauth_proxy internally.
#     Export DATA_ROOT so template_launch uses correct path (not from baked-in squad_config).
echo "☁️  Single-agent mode — launching via template_launch"

# 通过 NANOBOT_WEBUI_DIST 环境变量指定原生 webui 路径
export NANOBOT_WEBUI_DIST=/app/vanilla_webui

export DATA_ROOT
exec python3 -m cloud_agent_gateway.template_launch
