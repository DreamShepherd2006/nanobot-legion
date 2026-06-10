FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# ── 1. System dependencies ──────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git bubblewrap openssh-client && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 2. Metadata-only install (Docker layer cache) ──────────
COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md hatch_build.py ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache ".[matrix]" && \
    rm -rf nanobot bridge

# ── 3. Copy full source ───────────────────────────────────
COPY nanobot/ nanobot/
COPY bridge/ bridge/
COPY webui/ webui/

# ── 4. Legion: kernel patches (sed + Python scripts) ────────
RUN echo "💉 [Legion] kernel protocol patches..." && \
    sed -i '/^def _run_gateway/,/^def /{s/config\.gateway\.host/"0.0.0.0"/g}' nanobot/cli/commands.py && \
    sed -i 's/host = host if host is not None else api_cfg.host/host = "0.0.0.0"/g' nanobot/cli/commands.py && \
    sed -i '/def _authorize_websocket_handshake/a \        return None' nanobot/channels/websocket.py && \
    sed -i 's/return host in _LOCALHOSTS/return True/g' nanobot/channels/websocket.py && \
    grep -rnl "StaticFiles" nanobot/ | xargs -r sed -i 's/StaticFiles(/StaticFiles(html=True, /g' && \
    echo "✅ kernel patches done"

# ── 5. Legion: WebUI patches (BEFORE full install — npm build) ──
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_legion_v4_client.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_legion_v6_sidebar.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_webui_squad_sessions.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_package_json_radix.py /tmp/
RUN python3 /tmp/patch_legion_v4_client.py && \
    python3 /tmp/patch_legion_v6_sidebar.py && \
    python3 /tmp/patch_webui_squad_sessions.py && \
    python3 /tmp/patch_package_json_radix.py && \
    echo "✅ legion webui patches applied"

# ── 6. Full install (triggers hatch_build.py → npm build) ─
RUN uv pip install --system --no-cache ".[matrix]"

# ── 7. Legion: Python runtime patches (AFTER full install — site-packages now populated) ──
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_message_hardening.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_squad_error_events.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_gatekeeper_identity.py /tmp/
COPY deploy/huggingface/patches/v0.2.1_dbdb146f/patch_webui_transcript_user.py /tmp/
RUN python3 /tmp/patch_message_hardening.py && \
    python3 /tmp/patch_squad_error_events.py && \
    python3 /tmp/patch_gatekeeper_identity.py && \
    python3 /tmp/patch_webui_transcript_user.py && \
    echo "✅ runtime patches applied"

# ── 8. Build WhatsApp bridge + version stamp ─────────────────
WORKDIR /app/bridge
RUN git config --global --add url."https://github.com/".insteadOf ssh://git@github.com/ && \
    git config --global --add url."https://github.com/".insteadOf git@github.com: && \
    npm install && npm run build
WORKDIR /app
COPY deploy/huggingface/NANOBOT_COMMIT /app/NANOBOT_COMMIT

# ── 9. Legion: extra Python dependencies ────────────────────
RUN uv pip install --system --no-cache \
    fastapi uvicorn websockets authlib httpx itsdangerous tomli \
    huggingface_hub joserfc websocket-client

# ── 10. Legion: minimal instance seed (storage罐优先，此仅首次兜底) ─
RUN mkdir -p /app/instances/neo-workspace/memory && \
    printf '📍 当前空间: Staging (Nanobot-Staging)\n→ 部署路径: deploy/huggingface/ | /data: 持久化\n' > /app/instances/neo-workspace/AGENTS.md && \
    echo "✅ workspace seed created"
COPY deploy/huggingface/_template_config.json /app/instances/_template/config.json

# ── 10b. Cloud Agent Gateway (pip package) ──────────────────
COPY .framework-version /tmp/.framework-version
COPY cloud-agent-gateway/ /tmp/cloud-agent-gateway/
RUN pip install --break-system-packages /tmp/cloud-agent-gateway/ && \
    EXPECTED=$(cat /tmp/.framework-version) && \
    ACTUAL=$(python3 -c 'import cloud_agent_gateway; print(cloud_agent_gateway.__version__)') && \
    if [ "$EXPECTED" != "$ACTUAL" ]; then \
        echo "❌ Framework version mismatch! Expected: $EXPECTED, Installed: $ACTUAL" >&2; \
        echo "   The cloud-agent-gateway local copy is out of sync." >&2; \
        exit 1; \
    fi && \
    echo "✅ cloud-agent-gateway $ACTUAL (matches .framework-version $EXPECTED)" && \
    rm -rf /tmp/cloud-agent-gateway/

# ── 10c. Channel runtime patches (auto-reload from account.json) ─
RUN python3 -m cloud_agent_gateway.deploy.cloud.patch_qq_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_feishu_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_dingtalk_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_weixin_reload && \
    echo "✅ channel runtime patches applied"

# ── 11. Legion: core scripts ───────────────────────────────
COPY deploy/huggingface/gatekeeper.py ./gatekeeper.py
COPY deploy/huggingface/squad_bridge.py ./squad_bridge.py
COPY deploy/huggingface/squad_bridge_cross.py ./squad_bridge_cross.py
COPY deploy/huggingface/squad_config_sync.py ./squad_config_sync.py
COPY deploy/huggingface/squad_config.json ./squad_config.json
COPY deploy/huggingface/squad_config.hf-staging.json ./squad_config.hf-staging.json
COPY deploy/huggingface/squad_config.hf-nightly.json ./squad_config.hf-nightly.json
COPY deploy/huggingface/squad_config.ms-staging.json ./squad_config.ms-staging.json
COPY deploy/huggingface/squad_config_loader.py ./squad_config_loader.py
COPY deploy/huggingface/push_tasks.py ./push_tasks.py
COPY deploy/huggingface/scripts/resurrect_neo.sh /app/scripts/resurrect_neo.sh

# ── 11b. Legion: cloud platform layer ───────────────────────
COPY deploy/cloud/entrypoint.sh /app/deploy/cloud/entrypoint.sh
RUN sed -i 's/\r$//' /app/deploy/cloud/entrypoint.sh && chmod +x /app/deploy/cloud/entrypoint.sh

# ── 12. User & permissions ─────────────────────────────────
RUN useradd -m -u 1000 -s /bin/bash nanobot && \
    mkdir -p /home/nanobot/.nanobot && \
    chmod +x /app/gatekeeper.py /app/squad_bridge.py /app/squad_bridge_cross.py /app/squad_config_sync.py /app/push_tasks.py /app/scripts/resurrect_neo.sh && \
    chown -R nanobot:nanobot /home/nanobot /app

# ── 13. Entrypoint ──────────────────────────────────────────
# Cloud entrypoint (router) → delegates to squad launch.sh
COPY deploy/huggingface/launch.sh /app/deploy/huggingface/launch.sh
RUN sed -i 's/\r$//' /app/deploy/huggingface/launch.sh && chmod +x /app/deploy/huggingface/launch.sh

USER nanobot
ENV HOME=/home/nanobot \
    SQUAD_LEGION=true

EXPOSE 7860

ENTRYPOINT ["/app/deploy/cloud/entrypoint.sh"]
