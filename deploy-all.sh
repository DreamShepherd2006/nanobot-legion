#!/bin/bash
# deploy-all.sh — 一键部署框架代码到所有平台
# 用法:
#   bash deploy-all.sh [--dry-run] [--tag MSG] [--rollback TAG]
#   bash deploy-all.sh --skip-verify   # 只推送不验证
#   bash deploy-all.sh --platforms hf-staging,ms  # 仅指定平台
#
# 顺序: 先推 Staging 平台，各自验证，全通过后打 final tag
# 失败自动 git revert + push 回退
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/nanobot-legion"        # 部署代码源头 (staging 分支)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DRY_RUN=false
SKIP_VERIFY=false
NO_SYNC_BACK=false
TAG_MSG=""
ROLLBACK_TAG=""

# ── 日志 ─────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[⚠]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*"; }

# ── 解析参数 ────────────────────────────────────────
PLATFORMS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --skip-verify) SKIP_VERIFY=true; shift ;;
        --no-sync-back) NO_SYNC_BACK=true; shift ;;
        --platforms) PLATFORMS="$2"; shift 2 ;;
        --tag) TAG_MSG="$2"; shift 2 ;;
        --rollback) ROLLBACK_TAG="$2"; shift 2 ;;
        -h|--help) echo "用法: bash deploy-all.sh [--dry-run] [--skip-verify] [--no-sync-back] [--tag MSG] [--platforms hf-staging,ms,nightly]"; exit 0 ;;
        *) err "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -z "$PLATFORMS" ]; then
    PLATFORMS="hf-staging,ms"
fi

# 平台→GitHub 分支映射 (sync-back 用)
declare -A PLATFORM_BRANCH
PLATFORM_BRANCH[hf-staging]="staging"
PLATFORM_BRANCH[ms]="staging"
PLATFORM_BRANCH[nightly]="main"

echo -e "${GREEN}🚀 Legion Deploy All${NC} — $TIMESTAMP"

# ── 1. 验证源头 ─────────────────────────────────────
log "━━━ 1. 验证源头 ━━━"
cd "$SOURCE"
CUR_BRANCH=$(git branch --show-current)
log "源头: $SOURCE (分支: $CUR_BRANCH)"
if [ "$CUR_BRANCH" != "staging" ] && [ "$CUR_BRANCH" != "main" ]; then
    err "请在 staging 或 main 分支运行"
    exit 1
fi

# 检查是否有未提交的变更
if ! git diff-index --quiet HEAD --; then
    warn "源头有未提交的变更，请先 commit"
    git status -s
    exit 1
fi

    # 检查是否已推送到 remote
    LAST_LOCAL=$(git rev-parse HEAD)
    LAST_REMOTE=$(git rev-parse "origin/$CUR_BRANCH" 2>/dev/null || echo "none")
    if [ "$LAST_LOCAL" != "$LAST_REMOTE" ] && [ "$DRY_RUN" = false ]; then
        warn "源头有未推送的 commit，先推送..."
        git push origin "$CUR_BRANCH"
    fi

# ── 2. 打 tag ──────────────────────────────────────
if [ -n "$ROLLBACK_TAG" ]; then
    log "━━━ 回退模式: $ROLLBACK_TAG ━━━"
    cd "$SOURCE"
    git checkout "$ROLLBACK_TAG" 2>/dev/null || { err "tag $ROLLBACK_TAG 不存在"; exit 1; }
    log "已回退到 $ROLLBACK_TAG，下面将重新推送到平台"
fi

PRE_TAG="auto-deploy-${TIMESTAMP}"
if [ "$DRY_RUN" = false ]; then
    git tag "$PRE_TAG" -m "${TAG_MSG:-auto-tag before deploy $TIMESTAMP}"
    log "📌 pre-deploy tag: $PRE_TAG"
fi

# ── 3. 定义平台配置 ────────────────────────────────
# 格式: "平台标识|本地workspace路径|远程push命令|验证url_relay|验证url_health"
declare -A PLATFORM_INFO
PLATFORM_INFO[hf-staging]="/data/instances/neo/workspace/staging-deploy|git push origin master:main|https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging|https://dreamshepherd2006-nanobot-staging.hf.space/health"
PLATFORM_INFO[ms]="/data/instances/neo/workspace/modelscope-deploy|git push origin master|https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly|https://stone2006-nanobot-multi-agent-nightly.ms.show/health"
PLATFORM_INFO[nightly]="/data/instances/neo/workspace/nightly-sync|git push origin main|https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly|https://dreamshepherd2006-nanobot-multi-agent-nightly.hf.space/health"

# ── 4. 同步文件函数 ────────────────────────────────
# ── 4b. GitHub 反向同步 ──────────────────────────────
sync_back_to_github() {
    local clone_dir="$1" platform="$2"
    local github_branch="${PLATFORM_BRANCH[$platform]}"
    local deploy_dir="$SOURCE/deploy/huggingface"
    local clone_deploy_dir="$clone_dir/deploy/huggingface"

    log "  🔄 sync-back → nanobot-legion/$github_branch ..."

    cd "$SOURCE"
    local cur_branch=$(git branch --show-current)
    if [ "$cur_branch" != "$github_branch" ]; then
        log "    切换分支: $cur_branch → $github_branch"
        git checkout "$github_branch" 2>/dev/null || {
            warn "    无法切换到 $github_branch，跳过 sync-back"
            return 0
        }
    fi

    # 同步共享文件 (clone → source)
    for f in "${SHARED_FILES[@]}"; do
        if [ -f "$clone_deploy_dir/$f" ] || [ -d "$clone_deploy_dir/$f" ]; then
            cp -r "$clone_deploy_dir/$f" "$deploy_dir/$f"
        fi
    done

    # Dockerfile
    if [ -f "$clone_dir/Dockerfile" ]; then
        cp "$clone_dir/Dockerfile" "$SOURCE/Dockerfile"
    fi

    # platforms/
    if [ -d "$clone_deploy_dir/platforms" ]; then
        mkdir -p "$deploy_dir/platforms"
        cp -r "$clone_deploy_dir/platforms/"* "$deploy_dir/platforms/"
    fi

    # 补丁
    for patch in "$clone_deploy_dir"/patch_*.py; do
        [ -f "$patch" ] && cp "$patch" "$deploy_dir/"
    done

    # squad_config files（保留所有平台的）
    for cfg in "$clone_deploy_dir"/squad_config*.json; do
        [ -f "$cfg" ] && cp "$cfg" "$deploy_dir/"
    done

    # instances/ 模板
    if [ -d "$clone_deploy_dir/instances" ]; then
        mkdir -p "$deploy_dir/instances"
        cp -rn "$clone_deploy_dir/instances/"* "$deploy_dir/instances/" 2>/dev/null || true
    fi

    # commit & push
    if git diff --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
        log "    无差异，跳过 commit"
        return 0
    fi

    git add -A
    git commit -m "sync: $platform Space changes → nanobot-legion/$github_branch — $(date +%Y-%m-%d)"
    git push origin "$github_branch" 2>&1 || warn "    ⚠ GitHub push 失败 (网络/auth?)"
    log "  ✅ sync-back 完成 → GitHub $github_branch"
}

sync_files() {
    local src="$1" dst="$2" platform="$3"
    local deploy_dir="$src/deploy/huggingface"
    local dst_dir="$dst/deploy/huggingface"

    # 共享文件列表 (所有平台都需要 - deploy/huggingface/)
    SHARED_FILES=(
        "gatekeeper.py"
        "squad_bridge.py"
        "squad_config_sync.py"
        "squad_config_loader.py"
        "push_tasks.py"
        "platform_setup.py"
        "entrypoint.sh"
        "resurrect_agent.sh"
    )

    # 平台相关 config (每个平台保留自己的)
    # squad_config.{platform}.json → 由目标平台自己维护，不覆盖

    log "  同步 $platform 共享文件..."
    for f in "${SHARED_FILES[@]}"; do
        if [ -f "$deploy_dir/$f" ] || [ -d "$deploy_dir/$f" ]; then
            cp -r "$deploy_dir/$f" "$dst_dir/$f"
        fi
    done

    # Dockerfile → 目标仓库根目录 (HF Space 构建需要)
    if [ -f "$src/Dockerfile" ]; then
        cp "$src/Dockerfile" "$dst/Dockerfile"
    fi

    # platforms/ 目录整体同步
    if [ -d "$deploy_dir/platforms" ]; then
        cp -r "$deploy_dir/platforms/"* "$dst_dir/platforms/"
    fi

    # squad_config.json (兜底)
    cp "$deploy_dir/squad_config.json" "$dst_dir/squad_config.json"

    # squad_config.{platform}.json (所有平台的 config 都得有，entrypoint 运行时选)
    if [ "$platform" = "hf-staging" ]; then
        cp "$deploy_dir/squad_config.hf-staging.json" "$dst_dir/squad_config.hf-staging.json" 2>/dev/null || true
        cp "$deploy_dir/squad_config.ms-staging.json" "$dst_dir/" 2>/dev/null || true
        cp "$deploy_dir/squad_config.hf-nightly.json" "$dst_dir/" 2>/dev/null || true
    fi
    if [ "$platform" = "ms" ]; then
        cp "$deploy_dir/squad_config.ms-staging.json" "$dst_dir/squad_config.ms-staging.json" 2>/dev/null || true
        cp "$deploy_dir/squad_config.hf-staging.json" "$dst_dir/" 2>/dev/null || true
        cp "$deploy_dir/squad_config.hf-nightly.json" "$dst_dir/" 2>/dev/null || true
    fi
    if [ "$platform" = "nightly" ]; then
        cp "$deploy_dir/squad_config.hf-nightly.json" "$dst_dir/squad_config.hf-nightly.json" 2>/dev/null || true
        cp "$deploy_dir/squad_config.hf-staging.json" "$dst_dir/" 2>/dev/null || true
        cp "$deploy_dir/squad_config.ms-staging.json" "$dst_dir/" 2>/dev/null || true
    fi

    # 补丁
    for patch in "$deploy_dir"/patch_*.py; do
        [ -f "$patch" ] && cp "$patch" "$dst_dir/"
    done

    # MS 专属补丁
    if [ "$platform" = "ms" ] && [ -f "$deploy_dir/patch_webui_squad_sessions.py" ]; then
        cp "$deploy_dir/patch_webui_squad_sessions.py" "$dst_dir/"
    fi

    # instances/ 模板 (不覆盖已有)
    if [ -d "$deploy_dir/instances" ]; then
        mkdir -p "$dst_dir/instances"
        cp -rn "$deploy_dir/instances/"* "$dst_dir/instances/" 2>/dev/null || true
    fi
}

# ── 5. 验证函数 ─────────────────────────────────────
verify_platform() {
    local platform="$1" health_url="$2" relay_url="$3"

    if [ "$SKIP_VERIFY" = true ]; then
        log "  ⏭ skip verify (--skip-verify)"
        return 0
    fi

    log "  ⏳ 等待容器启动 (90s)..."
    sleep 90

    # Health check
    log "  🔍 health check: $health_url"
    if curl -sf --max-time 10 "$health_url" > /dev/null 2>&1; then
        log "  ✅ health check 通过"
    else
        err "  ❌ health check 失败"
        return 1
    fi

    # Relay test
    log "  🔍 relay test (70s 等待 agent 就绪)..."
    sleep 70
    if bash "$SCRIPT_DIR/skills/squad-http-relay/relay.sh" \
        --url "$relay_url" \
        --message "ping" \
        --timeout 30 2>/dev/null | grep -q "pong"; then
        log "  ✅ relay test 通过"
    else
        warn "  ⚠ relay test 未确认 (agent 可能仍在启动)"
    fi

    return 0
}

# ── 6. 部署单个平台 ─────────────────────────────────
deploy_platform() {
    local platform="$1"
    local info="${PLATFORM_INFO[$platform]}"
    local dst_dir=$(echo "$info" | cut -d'|' -f1)
    local push_cmd=$(echo "$info" | cut -d'|' -f2)
    local relay_url=$(echo "$info" | cut -d'|' -f3)
    local health_url=$(echo "$info" | cut -d'|' -f4)

    log ""
    log "━━━ 部署: $platform ━━━"
    log "  目标: $dst_dir"
    log "  push: $push_cmd"

    if [ ! -d "$dst_dir" ]; then
        err "  目标目录不存在: $dst_dir"
        return 1
    fi

    # 同步文件
    sync_files "$SOURCE" "$dst_dir" "$platform"

    if [ "$DRY_RUN" = true ]; then
        log "  🏜 dry-run: skipping git+push (files synced)"
        return 0
    fi

    # Commit
    cd "$dst_dir"
    local has_changes=false
    if ! git diff-index --quiet HEAD -- 2>/dev/null || [ -n "$(git ls-files --others --exclude-standard)" ]; then
        has_changes=true
        git add -A
        local src_sha=$(cd "$SOURCE" && git rev-parse --short HEAD)
        git commit -m "deploy: sync from nanobot-legion@${src_sha} — $(date +%Y-%m-%d)"
    fi

    # Push
    log "  📤 push $platform..."
    if ! eval "$push_cmd" 2>&1; then
        err "  ❌ push 失败"
        return 1
    fi

    log "  ✅ push 成功"

    # GitHub 反向同步
    if [ "$NO_SYNC_BACK" = false ] && [ "$DRY_RUN" = false ]; then
        sync_back_to_github "$dst_dir" "$platform"
    fi

    # 验证
    if ! verify_platform "$platform" "$health_url" "$relay_url"; then
        # 回退
        warn "  ↩ 回退 $platform..."
        if $has_changes; then
            git revert HEAD --no-edit && eval "$push_cmd" 2>&1
            log "  ↩ 已回退"
        fi
        return 1
    fi

    log "  ✅ $platform 部署完成"
    return 0
}

# ── 7. 主流程 ───────────────────────────────────────
log "━━━ 部署计划 ━━━"
log "   📦 $PLATFORMS"
log "   🌿 $CUR_BRANCH"
log "   🕐 $TIMESTAMP"
log ""

FAILED=()
SUCCESS=()

IFS=',' read -ra PLATFORM_LIST <<< "$PLATFORMS"
for pf in "${PLATFORM_LIST[@]}"; do
    pf=$(echo "$pf" | xargs)  # trim whitespace
    if [ -z "${PLATFORM_INFO[$pf]:-}" ]; then
        err "未知平台: $pf"
        FAILED+=("$pf")
        continue
    fi

    if deploy_platform "$pf"; then
        SUCCESS+=("$pf")
    else
        FAILED+=("$pf")
        if [ -n "$ROLLBACK_TAG" ]; then
            err "回退后部署仍然失败，请手动检查"
            break
        fi
    fi
done

# ── 8. 结果 ─────────────────────────────────────────
log ""
log "━━━ 结果 ━━━"
log "  ✅ 成功: ${#SUCCESS[@]} (${SUCCESS[*]:-无})"
log "  ❌ 失败: ${#FAILED[@]} (${FAILED[*]:-无})"

if [ ${#FAILED[@]} -gt 0 ]; then
    warn "建议: git checkout $PRE_TAG 回退源头，然后逐个修复"
    exit 1
fi

if [ "$DRY_RUN" = false ] && [ ${#SUCCESS[@]} -eq ${#PLATFORM_LIST[@]} ]; then
    FINAL_TAG="deploy-ok-${TIMESTAMP}"
    cd "$SOURCE"
    git tag "$FINAL_TAG" -m "✅ All platforms deployed successfully at $TIMESTAMP"
    log "📌 final tag: $FINAL_TAG"
fi

log "🎉 完成"
