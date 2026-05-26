#!/bin/bash

# 1. 基础环境配置
export HOME="/home/nanobot"
DIR="$HOME/.nanobot"
MOUNT_PATH="/data" 

# 确保 PYTHONPATH 包含当前应用目录
export PATH="/home/nanobot/.local/bin:$PATH"
export PYTHONPATH="/app:${PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export WEBUI_AGENT="neo"  # 军团指挥中心默认前端入口 agent

# ---------------------------------------------------------
# 💡 步骤 A：军团环境变量解冻 (阻塞式执行)
# ---------------------------------------------------------
echo "🧬 [System] 正在从系统根进程同步军团环境变量..."

# 注意：从 /proc/1/environ 读取才能确保拿到容器启动时注入的原始变量
while IFS='=' read -r -d '' name value; do
    if [[ "$name" == NANOBOT_TOKEN ]] || [[ "$name" == NANOBOT_PEER_* ]] || [[ "$name" == SQUAD_LEGION ]]; then
        export "$name"="$value"
        echo "   >> 已解冻: $name"
    fi
done < /proc/1/environ

# 阻塞校验：确保关键变量已经进入当前 Shell 内存
if [ -z "$NANOBOT_PEER_NEO" ]; then
    echo "⚠️ [Warning] 未检测到 NANOBOT_PEER_NEO，请检查环境变量配置"
else
    echo "✅ [System] 环境变量同步完成，已进入内存"
fi

# ---------------------------------------------------------
# 💡 步骤：构建军团花名册 SQUAD_LEGION（从 NANOBOT_PEER_* 派生）
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 💡 步骤 A2：持久化工作区补丁注入 (runtime, 不依赖 Dockerfile)
# ---------------------------------------------------------
echo "💉 [A2] 正在从持久化工作区注入运行时补丁..."
PATCH_DIR="/data/instances/neo/workspace/deploy/huggingface"
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

# 2. 存储初始化逻辑
echo "🔍 [Storage] 正在检查持久化存储..."
# 清理残留文件（防止 NotADirectoryError）
[ -f "$DIR/instances" ] && rm -f "$DIR/instances"
[ -f "$MOUNT_PATH/instances" ] && rm -f "$MOUNT_PATH/instances"
mkdir -p "$DIR"
if [ -d "$MOUNT_PATH" ]; then
    mkdir -p "$MOUNT_PATH/instances"
    ln -sfn "$MOUNT_PATH/instances" "$DIR/instances"
    echo "✅ [Storage] 持久化存储已链接"
fi

# 模板恢复: 存储优先，仅首次启动从镜像落种子
if [ ! -d "/data/instances/_template" ]; then
    if [ -d "/app/instances/_template" ]; then
        mkdir -p "/data/instances"
        cp -r /app/instances/_template /data/instances/_template
        echo "🌱 [Template] 首次启动，从镜像落种子: /data/instances/_template/"
    else
        echo "⚠️ [Template] 镜像内无种子 (/app/instances/_template) — agent 将跳过"
    fi
else
    echo "✅ [Template] 存储罐已有模板，跳过镜像覆盖"
fi

# ---------------------------------------------------------
# 💡 步骤 B：执行军团配置自动化同步 (确保在 Agent 启动前)
# ---------------------------------------------------------
echo "🔧 [System] 正在执行军团授权白名单自动注入..."
if [ -f "/app/squad_config_sync.py" ]; then
    # 这里会读取上面 export 的变量并修补 config.json
    python3 /app/squad_config_sync.py
else
    echo "⚠️ [System] 未发现 squad_config_sync.py，跳过配置修补"
fi

# 3. 日志管道预热（动态派生，无硬编码）
echo "📑 [System] 正在初始化日志通道..."
for var in $(env | grep '^NANOBOT_PEER_' | cut -d= -f1); do
    name=$(echo "$var" | sed 's/^NANOBOT_PEER_//' | tr '[:upper:]' '[:lower:]')
    echo "[$(date '+%H:%M:%S')] 🚀 $name 通道初始化完毕" > "$HOME/$name.log"
done
echo "[$(date '+%H:%M:%S')] 🚀 gatekeeper 通道初始化完毕" > "$HOME/gatekeeper.log"

# 4. Agent 启动函数
launch_agent() {
    local name=$1
    local port=$2
    local config="$DIR/instances/$name/config.json"
    local workspace="$DIR/instances/$name/workspace"
    local inst_dir="$DIR/instances/$name"

    # 清理残留文件 block（防止 NotADirectoryError）
    [ -f "$inst_dir" ] && rm -f "$inst_dir"
    [ -f "$workspace" ] && rm -f "$workspace"

    local log_dir="/data/instances/$name/workspace/logs"
    mkdir -p "$workspace" "$log_dir"

    # 注入军团知识 (仅首次/空工作区，尊重存储罐已有内容)
    if [ "$name" = "neo" ] && [ -d "/app/instances/neo-workspace" ]; then
        if [ ! -f "$workspace/AGENTS.md" ]; then
            cp -r /app/instances/neo-workspace/* "$workspace/"
            echo "🧠 [$name] 军团知识已注入 (首次)"
        else
            echo "🧠 [$name] 工作区已存在，跳过注入"
        fi
    fi

    if [ -f "$config" ]; then
        echo "🚀 [$name] 启动中 (Port: $port)..."
        # 这里的 Agent 进程将 100% 继承上面 export 的所有变量
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

# --- 动态启动：从 NANOBOT_PEER_* 派生 name，从 config.json 读取 gateway port ---
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

# 5. 启动 Gatekeeper (监控中枢)
echo "🛡️ 启动 Gatekeeper 调度服务..."
sleep 8

# 5.5. Staging 保活守护进程 (防止 HF 免费空间休眠)
# 从持久化 workspace 加载（不依赖 Dockerfile COPY，避免构建失败）
KSA_SCRIPT="/data/instances/neo/workspace/deploy/huggingface/scripts/keep_staging_alive.py"
if [ -f "$KSA_SCRIPT" ]; then
    echo "🔗 [KeepAlive] 启动 Staging 保活服务..."
    mkdir -p /data/instances/logs
    nohup python3 "$KSA_SCRIPT" > /data/instances/logs/keep_staging_alive.log 2>&1 &
    echo "   ✅ keep_staging_alive PID=$!"
else
    echo "⚠️ [KeepAlive] $KSA_SCRIPT 不存在，跳过"
fi

mkdir -p /data/instances/logs
stdbuf -oL python3 -u gatekeeper.py 2>&1 \
    | stdbuf -oL sed "s/^/[GATEKEEPER] /" \
    | tee -a "/data/instances/logs/gatekeeper.log"

trap "kill 0" EXIT
