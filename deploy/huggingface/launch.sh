#!/bin/bash
set -e

# ============================================================================
# 军团 entrypoint.sh — 统一版本（Nightly / Staging / ModelScope 通用）
# ============================================================================

# ── 0. 平台检测 → 自动选配 ──────────────────────────────────────
_choose_platform_config() {
    local pf=""
    # ModelScope: MODELSCOPE_ENVIRONMENT=studio
    if [ "${MODELSCOPE_ENVIRONMENT:-}" = "studio" ]; then
        pf="ms-staging"
    # HF Staging: SPACE_ID 含 "nanobot-staging"
    elif echo "${SPACE_ID:-}" | grep -qi "nanobot-staging"; then
        pf="hf-staging"
    # HF Nightly: SPACE_ID 含 "multi-agent-nightly"
    elif echo "${SPACE_ID:-}" | grep -qi "multi-agent-nightly"; then
        pf="hf-nightly"
    fi

    if [ -n "$pf" ] && [ -f "/app/squad_config.${pf}.json" ]; then
        cp "/app/squad_config.${pf}.json" "/app/squad_config.json"
        echo "📋 [Config] selected → squad_config.${pf}.json"
    else
        echo "📋 [Config] using default squad_config.json (platform: '${pf:-none}')"
    fi
}
_choose_platform_config

# ── 1. 基础环境配置 ──────────────────────────────────────────────
export HOME="/home/nanobot"
DIR="$HOME/.nanobot"

# 从 seed config 读取 data_root
SEED_CONFIG="/app/squad_config.json"
export MOUNT_PATH=$(python3 -c "import json; print(json.load(open('$SEED_CONFIG')).get('data_root', '/data'))")
echo "📂 [Storage] data_root = $MOUNT_PATH"

# Storage-first: 首次启动时将 seed config 复制到持久化路径
PERSIST_CONFIG="$MOUNT_PATH/squad_config.json"
if [ ! -f "$PERSIST_CONFIG" ]; then
    echo "📋 [Config] 首次启动，种子配置 → 持久化存储 ($PERSIST_CONFIG)"
    cp "$SEED_CONFIG" "$PERSIST_CONFIG"
else
    echo "📋 [Config] 配置已就绪 ($PERSIST_CONFIG)"
fi
export SQUAD_CONFIG_PATH="$PERSIST_CONFIG"

export PATH="/home/nanobot/.local/bin:$PATH"
export PYTHONPATH="/app:${PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export WEBUI_AGENT="neo"

# ── 2. 军团环境变量解冻（从 /proc/1/environ 兜底读取）───────────
echo "🧬 [System] 正在从系统根进程同步军团环境变量..."
if [ -r /proc/1/environ ]; then
    while IFS='=' read -r -d '' name value; do
        if [[ "$name" == NANOBOT_TOKEN ]] || [[ "$name" == NANOBOT_PEER_* ]] || \
           [[ "$name" == SQUAD_LEGION ]] || [[ "$name" == SPACE_ID ]] || \
           [[ "$name" == SQUAD_RELAY_TOKEN_* ]]; then
            export "$name"="$value"
            echo "   >> 已解冻: $name"
        fi
    done < /proc/1/environ
else
    echo "   ℹ️  /proc/1/environ 不可读（tini 隔离），依赖 su 继承环境"
fi

if [ -z "$NANOBOT_PEER_NEO" ]; then
    echo "⚠️ [Warning] 未检测到 NANOBOT_PEER_NEO，请检查环境变量配置"
else
    echo "✅ [System] 环境变量同步完成，已进入内存"
fi

# ── 3. 平台环境初始化 ────────────────────────────────────────────
echo "🧬 [System] 平台环境初始化..."
eval "$(cloud-gateway-setup)"
echo "✅ [System] 平台初始化完成"

# ── 3a. Peer env fallback（squad_config.json → env）──────────────
if [ -z "$NANOBOT_PEER_NEO" ] && [ -f "$SQUAD_CONFIG_PATH" ]; then
    echo "🔧 [Config] 从 squad_config.json 导出编制环境变量..."
    python3 -c "
import json
cfg = json.load(open('$SQUAD_CONFIG_PATH'))
for name, info in cfg.get('peers', {}).items():
    print(f'export NANOBOT_PEER_{name.upper()}=\'' + json.dumps({
        'id': info['id'],
        'gateway_port': info['gateway_port'],
        'ws_port': info['ws_port']
    }) + '\'')
" > /tmp/peers_env.sh
    source /tmp/peers_env.sh
    echo "   ✅ 已从 squad_config.json 导出 $(grep -c 'export' /tmp/peers_env.sh) 个 peer"
elif [ -n "$NANOBOT_PEER_NEO" ]; then
    echo "✅ [System] 环境变量已就绪"
fi

# ── 4. 构建军团花名册 SQUAD_LEGION ─────────────────────────────────
echo "🧑‍🤝‍🧑 [Squad] 构建军团花名册 SQUAD_LEGION..."
if [ -z "$SQUAD_LEGION" ]; then
    export SQUAD_LEGION=$(python3 -c "
import os, json
roster = {}
for key, val in os.environ.items():
    if key.startswith('NANOBOT_PEER_'):
        try:
            data = json.loads(val)
            role = data.get('id', 'squad:' + key[12:].lower())
        except Exception:
            role = 'squad:' + key[12:].lower()
        roster[key] = role
print(json.dumps(roster))
")
    echo "   ✅ SQUAD_LEGION=${SQUAD_LEGION}"
else
    echo "   ℹ️  SQUAD_LEGION 已手动定义，跳过自推导"
fi

# ── 5. 运行时 A2 补丁注入（持久化工作区，不依赖 Dockerfile）─────
echo "💉 [A2] 正在从持久化工作区注入运行时补丁..."
PATCH_DIR="$MOUNT_PATH/instances/neo/workspace/deploy/huggingface"
PATCHES_APPLIED=0
for PATCH_SCRIPT in \
    "patch_message_hardening.py" \
    "patch_squad_error_events.py"; do
    if [ -f "$PATCH_DIR/$PATCH_SCRIPT" ]; then
        python3 "$PATCH_DIR/$PATCH_SCRIPT" && \
            echo "   ✅ $PATCH_SCRIPT 完成" && PATCHES_APPLIED=$((PATCHES_APPLIED + 1)) || \
            echo "   ❌ $PATCH_SCRIPT 失败"
    else
        echo "   ⚠️  $PATCH_DIR/$PATCH_SCRIPT 不存在，跳过"
    fi
done
echo "   📊 共应用 $PATCHES_APPLIED 个运行时补丁"

# ── 6. 存储初始化 ─────────────────────────────────────────────────
echo "🔍 [Storage] 正在检查持久化存储..."
[ -f "$DIR/instances" ] && rm -f "$DIR/instances"
[ -f "$MOUNT_PATH/instances" ] && rm -f "$MOUNT_PATH/instances"
mkdir -p "$DIR"
if [ -d "$MOUNT_PATH" ]; then
    mkdir -p "$MOUNT_PATH/instances"
    ln -sfn "$MOUNT_PATH/instances" "$DIR/instances"
    echo "✅ [Storage] 持久化存储已链接 ($MOUNT_PATH/instances → $DIR/instances)"
fi

# ── 7. 模板恢复（每次启动强制同步）────────────────────────────────
if [ -d "/app/instances/_template" ]; then
    mkdir -p "$MOUNT_PATH/instances"
    rm -rf "$MOUNT_PATH/instances/_template"
    cp -r /app/instances/_template "$MOUNT_PATH/instances/_template"
    echo "🔄 [Template] 模板已从镜像强制同步: $MOUNT_PATH/instances/_template/"
else
    echo "⚠️ [Template] 镜像内无备份 (/app/instances/_template) — agent 将跳过"
fi

# ── 8. 军团配置同步 ────────────────────────────────────────────────
echo "🔧 [System] 执行军团配置同步..."
if [ -f "/app/squad_config_sync.py" ]; then
    python3 /app/squad_config_sync.py
else
    echo "⚠️ [System] 未发现 squad_config_sync.py，跳过"
fi

# ── 9. 日志管道预热 ────────────────────────────────────────────────
echo "📑 [System] 正在初始化日志通道..."
for var in $(env | grep '^NANOBOT_PEER_' | cut -d= -f1); do
    name=$(echo "$var" | sed 's/^NANOBOT_PEER_//' | tr '[:upper:]' '[:lower:]')
    echo "[$(date '+%H:%M:%S')] 🚀 $name 通道初始化完毕" > "$HOME/$name.log"
done
echo "[$(date '+%H:%M:%S')] 🚀 gatekeeper 通道初始化完毕" > "$HOME/gatekeeper.log"

# ── 10. Agent 启动 ─────────────────────────────────────────────────
launch_agent() {
    local name=$1
    local port=$2
    local config="$DIR/instances/$name/config.json"
    local workspace="$DIR/instances/$name/workspace"
    local inst_dir="$DIR/instances/$name"
    local log_dir="$MOUNT_PATH/instances/$name/workspace/logs"

    [ -f "$inst_dir" ] && rm -f "$inst_dir"
    [ -f "$workspace" ] && rm -f "$workspace"
    mkdir -p "$workspace" "$log_dir"

    # 注入军团知识 (neo workspace files)
    if [ "$name" = "neo" ] && [ -d "/app/instances/neo-workspace" ]; then
        cp -r /app/instances/neo-workspace/* "$workspace/"
        echo "🧠 [$name] 军团知识已注入"
    fi

    if [ -f "$config" ]; then
        echo "🚀 [$name] 启动中 (Port: $port)..."
        # Per-agent channel binding path
        _CHANNEL_DIR="$MOUNT_PATH/instances/$name/channels"
        mkdir -p "$_CHANNEL_DIR"

        (
            export NANOBOT_ACCOUNT_BASE="$_CHANNEL_DIR"
            exec stdbuf -oL nanobot gateway \
                --config "$config" \
                --workspace "$workspace" \
                --port "$port" 2>&1 \
            | stdbuf -oL sed "s/^/[$name] /" | tee -a "$log_dir/$name.log"
        ) &
    else
        echo "⚠️ [$name] 跳过启动：$config 不存在"
    fi
}

for var in $(env | grep '^NANOBOT_PEER_' | cut -d= -f1); do
    name=$(echo "$var" | sed 's/^NANOBOT_PEER_//' | tr '[:upper:]' '[:lower:]')
    config="$DIR/instances/$name/config.json"
    if [ -f "$config" ]; then
        gw_port=$(python3 -c "import json; print(json.load(open('$config'))['gateway']['port'])" 2>/dev/null)
        if [ -n "$gw_port" ]; then
            launch_agent "$name" "$gw_port"
        else
            echo "⚠️ [$name] 跳过：无法从 config.json 解析 gateway.port"
        fi
    else
        echo "⚠️ [$name] 跳过：$config 不存在"
    fi
done

# ── 11. Gatekeeper ──────────────────────────────────────────────────
echo "🛡️ 启动 Gatekeeper 调度服务..."
sleep 8

# KeepAlive 守护（防止 HF 免费空间休眠）
KSA_SCRIPT="$MOUNT_PATH/instances/neo/workspace/deploy/huggingface/scripts/keep_staging_alive.py"
if [ -f "$KSA_SCRIPT" ]; then
    echo "🔗 [KeepAlive] 启动 Staging 保活服务..."
    mkdir -p "$MOUNT_PATH/instances/logs"
    nohup python3 "$KSA_SCRIPT" > "$MOUNT_PATH/instances/logs/keep_staging_alive.log" 2>&1 &
    echo "   ✅ keep_staging_alive PID=$!"
else
    echo "⚠️ [KeepAlive] $KSA_SCRIPT 不存在，跳过"
fi

mkdir -p "$MOUNT_PATH/instances/logs"
stdbuf -oL python3 -u gatekeeper.py 2>&1 \
    | stdbuf -oL sed "s/^/[GATEKEEPER] /" \
    | tee -a "$MOUNT_PATH/instances/logs/gatekeeper.log"

trap "kill 0" EXIT
