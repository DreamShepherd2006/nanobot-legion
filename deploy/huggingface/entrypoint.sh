#!/bin/bash
set -e

# ── 1. 基础环境配置 ──────────────────────────────────────────────
export HOME="/home/nanobot"
DIR="$HOME/.nanobot"

# 从 seed config 读取 data_root，然后将配置迁移到持久化目录
SEED_CONFIG="/app/squad_config.json"
MOUNT_PATH=$(python3 -c "import json; print(json.load(open('$SEED_CONFIG')).get('data_root', '/data'))")
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

# ── 2. 平台相关环境准备 (delegates to platforms/<name>.py) ─────────
echo "🧬 [System] 平台环境初始化..."
eval "$(python3 /app/platform_setup.py)"
echo "✅ [System] 平台初始化完成"

# ── 2a. Peer env fallback (squad_config.json → env, when vars missing) ──
if [ -z "$NANOBOT_PEER_NEO" ] && [ -f "$SQUAD_CONFIG_PATH" ]; then
    echo "🔧 [Config] 从 squad_config.json 导出编制环境变量..."
    python3 -c "
import json
cfg = json.load(open('$SQUAD_CONFIG_PATH'))
for name, info in cfg.get('peers', {}).items():
    print(f'export NANOBOT_PEER_{name.upper()}=\\'' + json.dumps({
        'id': info['id'],
        'gateway_port': info['gateway_port'],
        'ws_port': info['ws_port']
    }) + '\\'')
" > /tmp/peers_env.sh
    source /tmp/peers_env.sh
    echo "   ✅ 已从 squad_config.json 导出 $(grep -c 'export' /tmp/peers_env.sh) 个 peer"
elif [ -n "$NANOBOT_PEER_NEO" ]; then
    echo "✅ [System] 环境变量已就绪"
fi

# ── 3. 构建军团花名册 SQUAD_LEGION ─────────────────────────────────
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

# ── 4. 存储初始化 ─────────────────────────────────────────────────
echo "🔍 [Storage] 正在检查持久化存储..."
[ -f "$DIR/instances" ] && rm -f "$DIR/instances"
[ -f "$MOUNT_PATH/instances" ] && rm -f "$MOUNT_PATH/instances"
mkdir -p "$DIR"
if [ -d "$MOUNT_PATH" ]; then
    mkdir -p "$MOUNT_PATH/instances"
    ln -sfn "$MOUNT_PATH/instances" "$DIR/instances"
    echo "✅ [Storage] 持久化存储已链接 ($MOUNT_PATH/instances → $DIR/instances)"
fi

# ── 5. 军团配置同步 ────────────────────────────────────────────────
echo "🔧 [System] 执行军团配置同步..."
if [ -f "/app/squad_config_sync.py" ]; then
    python3 /app/squad_config_sync.py
else
    echo "⚠️ [System] 未发现 squad_config_sync.py，跳过"
fi

# ── 6. 日志管道预热 ────────────────────────────────────────────────
echo "📑 [System] 正在初始化日志通道..."
for var in $(env | grep '^NANOBOT_PEER_' | cut -d= -f1); do
    name=$(echo "$var" | sed 's/^NANOBOT_PEER_//' | tr '[:upper:]' '[:lower:]')
    echo "[$(date '+%H:%M:%S')] 🚀 $name 通道初始化完毕" > "$HOME/$name.log"
done
echo "[$(date '+%H:%M:%S')] 🚀 gatekeeper 通道初始化完毕" > "$HOME/gatekeeper.log"

# ── 7. Agent 启动 ─────────────────────────────────────────────────
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

    if [ -f "$config" ]; then
        echo "🚀 [$name] 启动中 (Port: $port)..."
        (
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

# ── 8. Gatekeeper ──────────────────────────────────────────────────
echo "🛡️ 启动 Gatekeeper 调度服务..."
sleep 8
mkdir -p "$MOUNT_PATH/instances/logs"
stdbuf -oL python3 -u gatekeeper.py 2>&1 \
    | stdbuf -oL sed "s/^/[GATEKEEPER] /" \
    | tee -a "$MOUNT_PATH/instances/logs/gatekeeper.log"

trap "kill 0" EXIT
