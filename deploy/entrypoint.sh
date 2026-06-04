#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────
# Legion Entrypoint (nanobot-legion)
#
# Routes either to Squad Legion (multi-agent) or plain cloud
# (single-agent), then runs platform-specific setup via the
# cloud-agent-gateway pip package.
# ────────────────────────────────────────────────────────────
set -euo pipefail

echo "🚀 [Legion] Starting entrypoint..."

# ── 0. Ensure /data/instances exists ──
mkdir -p /data/instances

# ── 1. Platform detection (via cloud-agent-gateway) ────────
eval "$(cloud-gateway-setup)"
echo "📍 [Platform] DEPLOY_PLATFORM=$DEPLOY_PLATFORM"

# ── 2. Route: Squad Legion or plain cloud? ────────────────
if [ -f /app/launch.sh ]; then
    # Squad Legion (multi-agent)
    echo "🛡️ [Squad] Legion mode detected — booting multi-agent command center"
    export INSTANCE_ROOT="${INSTANCE_ROOT:-/data/instances}"
    export PATH="/app/deploy/huggingface:$PATH"
    exec /app/launch.sh
else
    # Plain cloud (single-agent) — via cloud-agent-gateway OAuth proxy
    echo "☁️  [Cloud] Cloud mode — starting cloud-agent-gateway"
    exec /usr/local/bin/cloud-agent-gateway
fi
