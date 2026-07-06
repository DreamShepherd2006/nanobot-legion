#!/bin/bash
# Neo 自我复活脚本 V5 — 三路输出（文件 + 容器屏幕）
# 调用方式: setsid bash /data/instances/neo/workspace/scripts/resurrect_neo.sh &

LOG_DIR="/data/instances/neo/workspace/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/neo_resurrect.log"
NEO_LOG="$LOG_DIR/neo.log"
exec > >(tee -a "$LOG" /proc/1/fd/1) 2>&1

# 辅助函数：同时写入 resurrection log 和 neo.log
mark() { echo "[$(date '+%H:%M:%S')] [resurrect] $*" | tee -a "$NEO_LOG"; }

# ── 0. PATH ──
export PATH="/home/nanobot/.local/bin:/usr/local/bin:/usr/bin:/bin"
mark "⚡ Neo 复活脚本启动 (PID: $$)"

# ── 1. 延迟 ──
mark "等待 3 秒，确保原 Neo 安全退出..."
sleep 3

# ── 2. 基础环境 ──
export HOME="/home/nanobot"
DIR="$HOME/.nanobot"
export PYTHONPATH="/app:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1

# ── 3. 环境变量注入 ──
mark "正在注入 /proc/1/environ..."
while IFS='=' read -r -d '' name value; do
    export "$name"="$value"
done < /proc/1/environ
mark "环境变量注入完成"

# ── 4. SQUAD_LEGION ──
if [ -z "${SQUAD_LEGION:-}" ]; then
    mark "推导 SQUAD_LEGION..."
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
    mark "SQUAD_LEGION=${SQUAD_LEGION}"
fi

# ── 5. 配置同步 ──
mark "执行 squad_config_sync.py..."
python3 -m nanobot_legion.squad_config_sync 2>&1 | tee -a "$NEO_LOG" || mark "⚠️ config sync 失败，继续..."

# ── 6. 杀旧进程 ──
mark "扫描旧 Neo 进程..."
OLD_PIDS=""
for pid_dir in /proc/[0-9]*; do
    pid=$(basename "$pid_dir")
    cmdline=$(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null)
    case "$cmdline" in
        *nanobot*gateway*instances/neo*)
            OLD_PIDS="$OLD_PIDS $pid"
            ;;
    esac
done

if [ -n "$OLD_PIDS" ]; then
    mark "终止旧 Neo 进程:$OLD_PIDS"
    for pid in $OLD_PIDS; do
        kill "$pid" 2>/dev/null && mark "   SIGTERM → PID $pid"
    done
    sleep 2
    for pid in $OLD_PIDS; do
        kill -9 "$pid" 2>/dev/null && mark "   SIGKILL → PID $pid"
    done
    mark "旧进程已终止"
else
    mark "未发现旧 Neo 进程"
fi

# ── 7. 启动新 Neo ──
CONFIG="$DIR/instances/neo/config.json"
WORKSPACE="$DIR/instances/neo/workspace"
GW_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG'))['gateway']['port'])" 2>/dev/null)

if [ -z "$GW_PORT" ]; then
    mark "❌ 无法解析 gateway.port，终止"
    exit 1
fi

mkdir -p "$WORKSPACE"
mark "🚀 启动 Neo (Port: $GW_PORT)..."

(
    exec stdbuf -oL nanobot gateway \
        --config "$CONFIG" \
        --workspace "$WORKSPACE" \
        --port "$GW_PORT" 2>&1 \
    | stdbuf -oL sed "s/^/[neo] /" | tee -a "$NEO_LOG"
) &

NEO_PID=$!
mark "✅ Neo 已启动 (PID: $NEO_PID)"
mark "🎯 复活完成"
