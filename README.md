# Nanobot Legion

将单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展为多智能体协同指挥系统的部署层，运行于 Hugging Face Spaces。

**在线演示**：[军团指挥中心](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly)（生产）· [HF Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging)（验证）· [ModelScope Staging](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly)（国内镜像）

> 🧪 跟踪 [NanoBot v0.2.0](https://github.com/HKUDS/nanobot/releases/tag/v0.2.0)（upstream/nightly [`92f2ff3a`](https://github.com/HKUDS/nanobot/commit/92f2ff3a)）

> *"军团" — 一组各司其职的 AI 智能体，在统一指挥下协同作战。*

## 亮点

| 🎖️ | **多智能体协同** — 5 个专业智能体（Neo/Trinity/Sentinel/Assistant/Medic）通过 WebSocket 互联互通。指挥官可跨节点调度任务、查询状态、汇总结果。 |
|-----|-----|
| 🤖 | **自主运维** — Neo（军团指挥官）可自主完成上游版本适配验证：拉取最新代码 → 部署到 Staging → 检查构建/运行日志 → 验证通过后上报，无需人工介入。 |
| 🔄 | **自愈机制** — `resurrect_neo.sh` V6 + Gatekeeper 健康监控：Neo 离线 > 150s 自动复活（跨平台路径 `$INSTANCE_ROOT`），冷却 300s。已通过 MS / HF Staging / HF Nightly 三平台实战验证。 |
| 🛡️ | **OAuth + RBAC** — Hugging Face / ModelScope OAuth，三级权限：Commander（管理员）、Member（成员，可对话）、Guest（访客，只读）。 |
| 🎨 | **动态 WebUI** — 实时智能体状态徽标（待命/执行中/阻塞/离线）、动态侧边栏编制、跨节点标签切换。全部由运行时环境变量驱动，零硬编码。 |
| 🧪 | **多平台 CI/CD** — 双分支模型（`main` 稳定生产，`staging` 验证前沿），配置分离支持多平台（HF Staging / ModelScope Staging），新平台只需 +1 个 JSON 配置文件。 |
| 🐳 | **单 Dockerfile 部署** — 运行于 Hugging Face Spaces 免费套餐。多阶段构建：上游 nanobot + 军团补丁在构建时合并打入。 |

## 分支模型

| 分支 | 定位 | 平台 | 节奏 |
|------|------|------|------|
| `main` | 🛡️ 稳定生产（Nightly） | HF Space | 落后 staging，cherry-pick 已验证的变更 |
| `staging` | 🧪 验证前沿 | HF Staging + MS Staging + 未来平台 | 跟踪上游 nightly，日常迭代 |

> 配置分离：同一 `staging` 分支承载多个平台，通过 `squad_config.{platform}.json` 区分，`entrypoint.sh` 启动时自动选配。

### 多平台配置

```
deploy/huggingface/
├── squad_config.json                  ← 兜底
├── squad_config.hf-staging.json       ← HF Staging: /data, hf-staging
├── squad_config.ms-staging.json       ← MS Staging: /mnt/workspace, modelscope
└── squad_config.{new-platform}.json   ← 扩展新平台只需创建此文件
```

| 平台 | 检测条件 | 配置 | 部署目标 |
|------|---------|------|---------|
| HF Staging | `SPACE_ID` 含 `NanobotStaging` | `squad_config.hf-staging.json` | `DreamShepherd2006/Nanobot-Staging` |
| ModelScope | `MODELSCOPE_ENVIRONMENT=studio` | `squad_config.ms-staging.json` | `Stone2006/nanobot-multi-agent-nightly` |
| HF Nightly (main 分支) | `SPACE_ID` 含 `multi-agent-nightly` | `squad_config.json` | `DreamShepherd2006/nanobot-multi-agent-nightly` |

> 共享代码（gatekeeper / squad_bridge / platforms / 补丁）在 `staging` 上开发，稳定后 cherry-pick 到 `main`。平台差异仅存在于 `squad_config.{platform}.json`。

## 架构

```
                      ┌───────────────────────────────────────┐
                      │       Nightly (HF Space 生产)         │
                      │                                       │
                      │  Gatekeeper (FastAPI/WS 代理)         │
                      │   ├─ OAuth / 权限控制                 │
                      │   ├─ HTTP Relay (跨智能体中继)        │
   浏览器 ──── WebUI ─▶│   └─ WebSocket 代理                  │
                      │        │        │        │            │
                      │     Neo(A)  Trinity  Sentinel  ...    │
                      │   ┌──────────────────────────────┐    │
                      │   │  squad_bridge (WS 通信网)    │    │
                      │   │  squad_config_sync (配置)    │    │
                      │   └──────────────────────────────┘    │
                      │                                       │
                      │  Neo ──▶ HF Staging ──▶ MS Staging    │
                      │          (多平台编排验证)              │
                      └───────────────────────────────────────┘

             nanobot-legion (部署仓库)
             ├── main     → Nightly 稳定生产
             └── staging  → 多平台验证 (HF + MS + …)
```
> Neo 作为军团指挥官，可自主拉取上游最新代码 → 部署到 Staging 空间 → 检查构建/运行日志 → 验证通过后报告，实现一线式版本适配验证。

## 组件

| 文件 | 功能 |
|------|------|
| `gatekeeper.py` | OAuth 网关（HF + ModelScope），三级权限控制（大小写精确匹配），HTTP/WS 代理，跨智能体中继，保活守护 |
| `squad_bridge.py` | 智能体之间 WebSocket 消息通信网 |
| `squad_config_sync.py` | 实例配置动态同步。**新 agent 从模板创建**，已有 agent 仅同步动态端口与白名单（不触碰 provider/model/ssrf）。**修改前自动备份**（`config.json.backup.{timestamp}`），保留最近 3 份。**自动清理根级污染**（`exec`、`allowed_env_keys`）。 |
| `squad_config.json` | 兜底配置（当前 = Nightly）；平台专属 → `squad_config.{platform}.json` |
| `squad_config_loader.py` | 配置加载器：从 `squad_config.json` 读取并注入 `DEPLOY_PLATFORM` 等环境变量 |
| `platform_setup.py` | 平台探测入口：导入 `platforms/` → 调用对应 `setup()` → 输出 shell exports |
| `platforms/` | 平台抽象层：`base.py`（Protocol）、`hf_staging.py`、`hf_direct.py`、`modelscope.py` |
| `Dockerfile` | 多阶段构建，合并上游 nanobot + 军团部署层 + 补丁打入 |
| `entrypoint.sh` | 运行时初始化：平台检测 → 自动选配 → 实例模板下发 → neo workspace 知识注入 → 补丁注入 |

### 补丁

**v0.2.0 适配补丁：**

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_legion_v6_sidebar.py` | `webui/src/components/Sidebar.tsx` | 注入 LegionRoster + LegionTerminal 组件（动态编制、状态徽标、任务追踪面板） |
| `patch_legion_v4_client.py` | `webui/src/NanobotClient.tsx` | 注入 onAnyEvent 拦截器（`legion_update` / `cluster_log` / `task_update` 事件路由） |
| `patch_message_hardening.py` | `nanobot/providers/openai_compat_provider.py` | DeepSeek 消息内容清洗（移除 "(empty)" 占位符、保留 tool_calls 下的文本） |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | squad bridge 权限错误事件发射（双目标：`/app/` + `site-packages/`） |

> ⚠️ `patch_bootstrap_peers.py` 已移除（上游 v0.2.0 已原生支持 `_read_peers`，该补丁无消费者，属于死代码）。

## 快速开始

本仓库是上游 NanoBot 的 **部署层叠加**。`Dockerfile` 位于根目录，squad 组件位于 `deploy/huggingface/`。

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
git clone https://github.com/DreamShepherd2006/nanobot-legion.git deploy/huggingface
# Dockerfile 在 deploy/huggingface/Dockerfile — 复制到项目根目录或挂载为构建上下文
```

## 许可

MIT — 继承自[上游](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE)。

## 相关链接

- 上游项目：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- 上游 PR：[#3869](https://github.com/HKUDS/nanobot/pull/3869)（DeepSeek 消息清洗）· [#3908](https://github.com/HKUDS/nanobot/pull/3908)（WS peers_update 事件）
- 上游 Discussion：[#3925](https://github.com/HKUDS/nanobot/discussions/3925)（单容器多智能体系统）
- ModelScope 验证空间：[Stone2006/nanobot-multi-agent-nightly](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly)

## 最近更新

`dd22e46` — 2026-05-31

- 🩹 **复活机制全面修复**：`squad_config_sync` 根级 `allowed_env_keys` 清理（5/30 Pydantic 复活失败根因）
- 🔄 `resurrect_neo.sh` V5→V6：跨平台路径 `$INSTANCE_ROOT`（原 `$HOME/.nanobot` 在 MS 不存在）
- 🔧 `gatekeeper.py`：复活脚本路径改用 `platform.instance_path()` 动态解析
- 🧪 **三平台复活实战验证通过**（MS / HF Staging / HF Nightly）
- 🌐 `squad_bridge_cross.py` v2：跨空间 relay + 调用方白名单

`80d2329` — 2026-05-29

- 🏗️ 双分支 + 配置分离：`staging` 分支承载 HF Staging / MS Staging 双平台，通过 `squad_config.{platform}.json` 区分
- 📋 entrypoint 启动时自动检测平台 → 选配正确的 `squad_config.json`
- 📦 platforms/ 抽象层 + squad_config_loader 补全至 Dockerfile
- 🧹 同步 MS 与 HF Staging 部署代码（manual OAuth, stderr logging）

`0f449cc` — 2026-05-26

- 🧹 移除 `patch_bootstrap_peers.py`（死代码，上游已原生支持）
- 🐛 修复 TS6133：v6 sidebar 补丁移除未使用的 `useCallback`/`useRef` import
- 📝 补丁表精简至 4 个活跃补丁

`44fb9bd` — 2026-05-26

- 上游追踪: `nanobot v0.2.0` (commit `92f2ff3a`)
- gatekeeper: ModelScope OAuth 解析修复、GRACE_SECONDS 150、`import subprocess`
- entrypoint: neo workspace 知识注入 + 运行时补丁注入
- patch_legion_v6_sidebar: 完整 LegionTerminal 组件
- patch_squad_error_events: 双目标补丁 + 备份
