#!/bin/bash
set -e

# ============================================================================
# 军团 entrypoint.sh — 统一版本（Nightly / Staging / ModelScope 通用）
# ============================================================================

# ── 0. 平台检测 → 自动选配 ──────────────────────────────────────
# 绝大多数 Squad 空间统一用默认 squad_config.json。
# Nightly 是唯一例外，需要独立的 relay token 映射。
_choose_platform_config() {
    if echo "${SPACE_ID:-}" | grep -qi "multi-agent-nightly" \
       && [ -f "/app/squad_config.hf-nightly.json" ]; then
        cp "/app/squad_config.hf-nightly.json" "/app/squad_config.json"
        echo "📋 [Config] selected → squad_config.hf-nightly.json"
    else
        echo "📋 [Config] using default squad_config.json"
    fi
}
_choose_platform_config

# ── 0a. 自动判定 data_root ──────────────────────────────────────
if [ "${MODELSCOPE_ENVIRONMENT:-}" = "studio" ]; then
    export MOUNT_PATH="/mnt/workspace"
elif [ "${HF_SPACE:-}" = "1" ]; then
    export MOUNT_PATH="/data"
else
    # 未来平台：回退到 squad_config.json 预设值
    export MOUNT_PATH=$(python3 -c "import json; print(json.load(open('/app/squad_config.json')).get('data_root', '/data'))")
fi
echo "📂 [Storage] data_root = $MOUNT_PATH"

# ── 0b. Legion 工作区隔离 ────────────────────────────────────────
# 多 agent 实例放在 DATA_ROOT/legion/ 子目录下，
# 与单 agent 的 DATA_ROOT/instances/default/ 完全隔离。
LEGACY_DATA_ROOT="$MOUNT_PATH"
MOUNT_PATH="$MOUNT_PATH/legion"
[ ! -d "$MOUNT_PATH" ] && mkdir -p "$MOUNT_PATH"
echo "📂 [Legion]  data_root = $MOUNT_PATH"

# 迁移旧路径（首次升级）
if [ -f "$LEGACY_DATA_ROOT/squad_config.json" ] && [ ! -f "$MOUNT_PATH/squad_config.json" ]; then
    echo "🔄 [Migrate] 检测到旧 squad_config.json，迁移到 legion/ ..."
    cp "$LEGACY_DATA_ROOT/squad_config.json" "$MOUNT_PATH/squad_config.json"
    if [ -d "$LEGACY_DATA_ROOT/instances" ] && [ ! -d "$MOUNT_PATH/instances" ]; then
        cp -r "$LEGACY_DATA_ROOT/instances" "$MOUNT_PATH/instances"
    fi
    echo "✅ [Migrate] 迁移完成"
fi

# 导出 DATA_ROOT 供 squad_config_loader 等 Python 模块读取
export DATA_ROOT="$MOUNT_PATH"

# ── 1. 基础环境配置 ──────────────────────────────────────────────
export HOME="/home/nanobot"
DIR="$HOME/.nanobot"
SEED_CONFIG="/app/squad_config.json"

# Storage-first: 首次启动时将 seed config 复制到持久化路径
PERSIST_CONFIG="$MOUNT_PATH/squad_config.json"
if [ ! -f "$PERSIST_CONFIG" ]; then
    echo "📋 [Config] 首次启动，种子配置 → 持久化存储 ($PERSIST_CONFIG)"
    cp "$SEED_CONFIG" "$PERSIST_CONFIG"
else
    echo "📋 [Config] 配置已就绪 ($PERSIST_CONFIG)"
fi
# 始终同步 data_root / dlq_dir（新平台 + 已有配置均生效）
python3 -c "
import json
cfg = json.load(open('$PERSIST_CONFIG'))
cfg['data_root'] = '$MOUNT_PATH'
cfg['dlq_dir'] = '$MOUNT_PATH/dlq'
json.dump(cfg, open('$PERSIST_CONFIG', 'w'), indent=2, ensure_ascii=False)
print('   ✅ paths synced → data_root=$MOUNT_PATH')
"
export SQUAD_CONFIG_PATH="$PERSIST_CONFIG"

export PATH="/home/nanobot/.local/bin:$PATH"
export PYTHONPATH="/app:${PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
export WEBUI_AGENT="neo"

# ── 2. 军团环境变量解冻（从 /proc/1/environ 兜底读取）───────────
# 注意：NANOBOT_PEER_* 不再从面板环境变量导入。
# Agent 编制唯一来源是 squad_config.json（step 3a）。
echo "🧬 [System] 正在从系统根进程同步军团环境变量..."
if [ -r /proc/1/environ ]; then
    while IFS='=' read -r -d '' name value; do
        if [[ "$name" == NANOBOT_TOKEN ]] || \
           [[ "$name" == SPACE_ID ]] || \
           [[ "$name" == SQUAD_RELAY_TOKEN_* ]]; then
            export "$name"="$value"
            echo "   >> 已解冻: $name"
        fi
    done < /proc/1/environ
else
    echo "   ℹ️  /proc/1/environ 不可读（tini 隔离），依赖 su 继承环境"
fi

# ── 3. 平台环境初始化 ────────────────────────────────────────────
echo "🧬 [System] 平台环境初始化..."
eval "$(cloud-gateway-setup)"

# ── Reset check: platform_setup signals oauth.json deleted ──
if [ -n "${RESET_DONE:-}" ]; then
    echo "🔄 [Reset] oauth.json deleted — restarting into Phase 1 setup..."
    exit 0
fi

echo "✅ [System] 平台初始化完成"

# ── 3a. Peer env fallback（squad_config.json → env）──────────────
# Always export peers from squad_config.json to pick up dynamically added agents.
# Existing NANOBOT_PEER_* env vars (from /proc/1/environ) take priority.
if [ -f "$SQUAD_CONFIG_PATH" ]; then
    python3 -c "
import json, os, shlex
cfg = json.load(open('$SQUAD_CONFIG_PATH'))
count = 0
for name, info in cfg.get('peers', {}).items():
    # Skip archived agents — only active agents get started
    if info.get('zone', 'active') != 'active':
        continue
    env_key = f'NANOBOT_PEER_{name.upper()}'
    if env_key not in os.environ:
        val = json.dumps({'id': info['id'], 'gateway_port': info['gateway_port'], 'ws_port': info['ws_port']})
        print(f'export {env_key}={shlex.quote(val)}')
        count += 1
if count:
    print(f'# {count} new peer(s) from squad_config.json', end='')
" > /tmp/peers_env.sh
    if grep -q 'export' /tmp/peers_env.sh 2>/dev/null; then
        source /tmp/peers_env.sh
        echo "   ✅ 已从 squad_config.json 导出新 peer"
    fi
fi

# 验证：Neo 必须存在
if [ -z "$NANOBOT_PEER_NEO" ]; then
    echo "❌ [Fatal] 未检测到 NANOBOT_PEER_NEO — squad_config.json 必须包含 neo peer"
    exit 1
fi
echo "✅ [System] Agent 编制已从 squad_config.json 加载"

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

# WeChat 状态目录桥接 — 绑定写入 ~/.nanobot/weixin，nanobot 读取实例目录
mkdir -p "$MOUNT_PATH/instances/neo/channels/weixin"
rm -rf "$HOME/.nanobot/weixin" 2>/dev/null || true
ln -sfn "$MOUNT_PATH/instances/neo/channels/weixin" "$HOME/.nanobot/weixin"
echo "🔗 [WeChat] 微信状态目录已桥接 ($HOME/.nanobot/weixin → $MOUNT_PATH/instances/neo/channels/weixin)"

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
echo "🔧 [System] 执行军团配置同步 (via nanobot-legion pip package)..."
python3 -m nanobot_legion.squad_config_sync

# ── 9. 日志管道预热 ────────────────────────────────────────────────
echo "📑 [System] 正在初始化日志通道..."
for var in $(env | grep '^NANOBOT_PEER_' | cut -d= -f1); do
    name=$(echo "$var" | sed 's/^NANOBOT_PEER_//' | tr '[:upper:]' '[:lower:]')
    echo "[$(date '+%H:%M:%S')] 🚀 $name 通道初始化完毕" > "$HOME/$name.log"
done
echo "[$(date '+%H:%M:%S')] 🚀 gatekeeper 通道初始化完毕" > "$HOME/gatekeeper.log"

# ── 10. 激活 Legion WebUI ──────────────────────────────────────────
# 通过 NANOBOT_WEBUI_DIST 环境变量直接指定路径（nanobot-legion 补丁提供支持）。
# 单 agent 模式不执行 launch.sh，因此保留原版 webui（sidebar pin ✅）。
if [ -d /app/legion_webui ]; then
    export NANOBOT_WEBUI_DIST=/app/legion_webui
    echo "🎨 [Legion] WebUI path → $NANOBOT_WEBUI_DIST"
else
    echo "⚠️  [Legion] /app/legion_webui not found — using nanobot default webui"
fi

# ── 11. Agent 启动 ─────────────────────────────────────────────────
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

# ── 12. Gatekeeper ──────────────────────────────────────────────────
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
stdbuf -oL python3 -u -m nanobot_legion.gatekeeper 2>&1 \
    | stdbuf -oL sed "s/^/[GATEKEEPER] /" \
    | tee -a "$MOUNT_PATH/instances/logs/gatekeeper.log"

trap "kill 0" EXIT
