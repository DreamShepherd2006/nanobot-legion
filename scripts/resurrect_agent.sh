#!/bin/bash
# Nanobot Legion — Agent Auto-Resurrection Script (V5)
# ====================================================
# Watched by gatekeeper.py legion_monitor: if an agent's gateway_port stops
# responding to health checks for >10s, the monitor spawns this script to
# revive it. Only whitelisted agents (Neo by default) are eligible.
#
# Usage: setsid bash resurrect_agent.sh <agent-name> &
#
# Security: zero hardcoded credentials. All secrets are injected from
# /proc/1/environ at runtime — standard Docker container practice.

set -e

AGENT="${1:?Usage: $0 <agent-name>}"
BASE="/data/instances/${AGENT}"
LOG_DIR="${BASE}/workspace/logs"
mkdir -p "$LOG_DIR"
LOG="${LOG_DIR}/${AGENT}_resurrect.log"
AGENT_LOG="${LOG_DIR}/${AGENT}.log"

# Triple output: resurrection log, agent log, container stdout
exec > >(tee -a "$LOG" /proc/1/fd/1) 2>&1

mark() {
    echo "[$(date '+%H:%M:%S')] [resurrect:${AGENT}] $*" | tee -a "$AGENT_LOG"
}

# ── 0. PATH ──
export PATH="/home/nanobot/.local/bin:/usr/local/bin:/usr/bin:/bin"
mark "⚡ ${AGENT} 复活脚本启动 (PID: $$)"

# ── 1. Delay for clean exit ──
mark "等待 3 秒，确保原进程安全退出..."
sleep 3

# ── 2. Environment ──
export HOME="/home/nanobot"
export PYTHONPATH="/app:${PYTHONPATH:-}"
export PYTHONDONTWRITEBYTECODE=1

# ── 3. Env injection from PID 1 ──
mark "正在注入 /proc/1/environ..."
while IFS='=' read -r -d '' name value; do
    export "$name"="$value"
done < /proc/1/environ
mark "环境变量注入完成"

# ── 4. SQUAD_LEGION — dynamic roster from NANOBOT_PEER_* ──
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
fi

# ── 5. Config sync ──
mark "执行 squad_config_sync.py..."
python3 /app/squad_config_sync.py 2>&1 | tee -a "$AGENT_LOG" || mark "⚠️ config sync 失败，继续..."

# ── 6. Kill stale processes ──
mark "扫描旧 ${AGENT} 进程..."
OLD_PIDS=""
for pid_dir in /proc/[0-9]*; do
    pid=$(basename "$pid_dir")
    cmdline=$(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null)
    case "$cmdline" in
        *nanobot*gateway*instances/${AGENT}*)
            OLD_PIDS="$OLD_PIDS $pid"
            ;;
    esac
done

if [ -n "$OLD_PIDS" ]; then
    mark "终止旧进程:${OLD_PIDS}"
    for pid in $OLD_PIDS; do
        kill "$pid" 2>/dev/null && mark "   SIGTERM → PID $pid"
    done
    sleep 2
    for pid in $OLD_PIDS; do
        kill -9 "$pid" 2>/dev/null && mark "   SIGKILL → PID $pid"
    done
else
    mark "未发现旧 ${AGENT} 进程"
fi

# ── 7. Spawn new agent ──
CONFIG="$HOME/.nanobot/instances/${AGENT}/config.json"
WORKSPACE="$HOME/.nanobot/instances/${AGENT}/workspace"
GW_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG'))['gateway']['port'])" 2>/dev/null)

if [ -z "$GW_PORT" ]; then
    mark "❌ 无法解析 gateway.port，终止"
    exit 1
fi

mkdir -p "$WORKSPACE"
mark "🚀 启动 ${AGENT} (Port: $GW_PORT)..."

(
    exec stdbuf -oL nanobot gateway \
        --config "$CONFIG" \
        --workspace "$WORKSPACE" \
        --port "$GW_PORT" 2>&1 \
    | stdbuf -oL sed "s/^/[${AGENT}] /" | tee -a "$AGENT_LOG"
) &

mark "✅ ${AGENT} 已启动 (PID: $!)"
mark "🎯 复活完成"
