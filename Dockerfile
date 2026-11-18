FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# ══════════════════════════════════════════════════════════════════
# nanobot-legion 基线镜像（Core）
#
# 只装框架三件套：nanobot + cloud-agent-gateway + nanobot-legion。
# **不装任何业务系统（Legion Module）**——量化、Vibe-Trading、
# onchainos CLI 等一律属于业务层，由业务空间自己的 Dockerfile 叠加，
# 例：DreamShepherd2006/nanobot-quant 的 Dockerfile。
#
# pin 规则：一律固定 commit / tag（零漂移）；升级 = 改 pin + 递增 bust。
# ══════════════════════════════════════════════════════════════════

# ── 1. System: Node 20 + git ──────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git bubblewrap openssh-client && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── 2. nanobot（Python 本体；webui 由 legion 构建）─────────────────
ENV NANOBOT_SKIP_WEBUI_BUILD=1
RUN pip install --break-system-packages \
        git+https://github.com/DreamShepherd2006/nanobot.git@dbdb146f && \
    echo "✅ nanobot @dbdb146f"

# ── 3. CAG + 通道热重载补丁 ───────────────────────────────────────
RUN pip install --break-system-packages \
        git+https://github.com/DreamShepherd2006/cloud-agent-gateway.git@v0.2.0 && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_qq_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_feishu_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_dingtalk_reload && \
    python3 -m cloud_agent_gateway.deploy.cloud.patch_weixin_reload && \
    echo "✅ CAG v0.2.0 + channel patches"

# ── 4. nanobot-legion：内核补丁 + webui 源码 + 部署资产 ─────────────
RUN echo "[bust=1]" && pip install --break-system-packages \
        git+https://github.com/DreamShepherd2006/nanobot-legion.git@0b687794 && \
    python3 -m nanobot_legion.install && \
    echo "✅ nanobot-legion @0b687794 (v0.2.1)"

# ── 4b. 构建 Legion webui（源码编译）───────────────────────────────
RUN cd /app/legion_webui_src && \
    npm install && \
    npm run build && \
    mkdir -p /app/legion_webui && \
    cp -r /app/nanobot/web/dist/* /app/legion_webui/ && \
    rm -rf /app/nanobot/web/dist && \
    rm -rf /app/legion_webui_src && \
    echo "✅ legion webui built"

# ── 5. WhatsApp bridge ───────────────────────────────────────────
RUN NANOBOT_DIR=$(python3 -c "import nanobot, os; print(os.path.dirname(nanobot.__file__))") && \
    cp -r "$NANOBOT_DIR/bridge" /app/bridge && \
    cd /app/bridge && \
    git config --global --add url."https://github.com/".insteadOf ssh://git@github.com/ && \
    git config --global --add url."https://github.com/".insteadOf git@github.com: && \
    npm install && npm run build && \
    cd /app && rm -rf /app/bridge/node_modules && \
    echo "✅ whatsapp bridge"

# ── 6. Reset 标记 ────────────────────────────────────────────────
RUN echo "PURGE_OAUTH=0" > /app/reset-setup.ini

# ── 7. 用户与权限 ────────────────────────────────────────────────
RUN useradd -m -u 1000 -s /bin/bash nanobot && \
    mkdir -p /home/nanobot/.nanobot && \
    chown -R nanobot:nanobot /home/nanobot /app

USER nanobot
ENV HOME=/home/nanobot \
    SQUAD_LEGION=true

EXPOSE 7860
ENTRYPOINT ["/app/entrypoint.sh"]
