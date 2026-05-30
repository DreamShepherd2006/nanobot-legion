FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# ── 1. System dependencies ──────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git bubblewrap openssh-client && \
    \
    # websocat (WebSocket debugging tool for Nightly)
    curl -L https://github.com/vi/websocat/releases/latest/download/websocat.x86_64-unknown-linux-musl -o /usr/local/bin/websocat && \
    chmod +x /usr/local/bin/websocat && \
    \
    # Node.js
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

# ── 4. Legion: kernel patches (sed) ────────────────────────
RUN echo "💉 [Legion] kernel protocol patches..." && \
    sed -i 's/config.gateway.host/"0.0.0.0"/g' nanobot/cli/commands.py && \
    sed -i 's/host = host if host is not None else api_cfg.host/host = "0.0.0.0"/g' nanobot/cli/commands.py && \
    sed -i '/def _authorize_websocket_handshake/a \        return None' nanobot/channels/websocket.py && \
    sed -i 's/return host in _LOCALHOSTS/return True/g' nanobot/channels/websocket.py && \
    grep -rnl "StaticFiles" nanobot/ | xargs -r sed -i 's/StaticFiles(/StaticFiles(html=True, /g' && \
    echo "✅ kernel patches done"

# ── 5. Legion: WebUI patches (BEFORE full install — npm build) ──
COPY deploy/huggingface/patch_legion_v4_client.py /tmp/
COPY deploy/huggingface/patch_legion_v6_sidebar.py /tmp/
COPY deploy/huggingface/patch_webui_squad_sessions.py /tmp/
RUN python3 /tmp/patch_legion_v4_client.py && \
    python3 /tmp/patch_legion_v6_sidebar.py && \
    python3 /tmp/patch_webui_squad_sessions.py && \
    \
    # v0.2.0 sidebar uses radix-ui avatar + scroll-area; ensure deps are present
    cd /app/webui && \
    npm install @radix-ui/react-avatar @radix-ui/react-scroll-area && \
    \
    cd /app && echo "✅ legion webui patches & radix-ui deps applied"

# ── 6. Full install (triggers hatch_build.py → npm build) ─
RUN uv pip install --system --no-cache ".[matrix]"

# ── 7. Legion: Python runtime patches (AFTER full install — site-packages now populated) ──
COPY deploy/huggingface/patch_message_hardening.py /tmp/patch_message_hardening.py
COPY deploy/huggingface/patch_squad_error_events.py /tmp/patch_squad_error_events.py
COPY deploy/huggingface/patch_gatekeeper_identity.py /tmp/patch_gatekeeper_identity.py
RUN python3 /tmp/patch_message_hardening.py && \
    python3 /tmp/patch_squad_error_events.py && \
    python3 /tmp/patch_gatekeeper_identity.py && \
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

# ── 10. Legion: minimal instance seed (storage-first, fallback only) ─
RUN mkdir -p /app/instances/_template /app/instances/neo-workspace/memory && \
    echo '{"gateway":{"host":"0.0.0.0","port":0},"channels":{"websocket":{"port":0}},"tools":{"exec":{"allowed_env_keys":["NANOBOT_TOKEN","NANOBOT_PEER_*","SQUAD_*","COMMANDER_WHITELIST","USER_AGENT_MAP"]}}}' > /app/instances/_template/config.json && \
    printf '📍 当前空间: Nightly (nanobot-multi-agent-nightly)\n→ 部署路径: deploy/huggingface/ | /data: 持久化\n' > /app/instances/neo-workspace/AGENTS.md && \
    echo "✅ minimal seed created (_template + neo-workspace)"

# ── 11. Legion: core scripts ───────────────────────────────
COPY deploy/huggingface/squad_config.json /app/
COPY deploy/huggingface/squad_config.hf-nightly.json /app/
COPY deploy/huggingface/squad_config_loader.py /app/
COPY deploy/huggingface/push_tasks.py /app/
COPY deploy/huggingface/platform_setup.py /app/
COPY deploy/huggingface/platforms/ /app/platforms/
COPY deploy/huggingface/gatekeeper.py ./gatekeeper.py
COPY deploy/huggingface/squad_bridge.py ./squad_bridge.py
COPY deploy/huggingface/squad_config_sync.py ./squad_config_sync.py
COPY deploy/huggingface/scripts/resurrect_neo.sh /app/scripts/resurrect_neo.sh

# ── 12. User & permissions ─────────────────────────────────
RUN useradd -m -u 1000 -s /bin/bash nanobot || true && \
    mkdir -p /home/nanobot/.nanobot && \
    chmod +x /app/gatekeeper.py /app/squad_bridge.py /app/squad_config_sync.py /app/push_tasks.py /app/platform_setup.py /app/scripts/resurrect_neo.sh && \
    chown -R nanobot:nanobot /home/nanobot /app

# ── 13. Entrypoint ──────────────────────────────────────────
COPY deploy/huggingface/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh && chmod +x /usr/local/bin/entrypoint.sh

USER nanobot
ENV HOME=/home/nanobot
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 7860

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
