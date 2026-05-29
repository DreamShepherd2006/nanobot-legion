# Nanobot Legion

将单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展为多智能体协同指挥系统的部署层，支持 Hugging Face Spaces 与 ModelScope Studio 双平台运行。

**在线演示**：[军团指挥中心](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly)（生产）· [HF Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging)（验证）· [ModelScope Staging](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly)（国内镜像）

> 🏗️ 基于 [NanoBot v0.2.0](https://github.com/HKUDS/nanobot) (commit [`92f2ff3a`](https://github.com/HKUDS/nanobot/commit/92f2ff3a))

> *"军团" — 一组各司其职的 AI 智能体，在统一指挥下协同作战。*

## 分支模型

| 分支 | 定位 | 部署范围 | 节奏 |
|------|------|---------|------|
| **`main`** | 🛡️ 稳定生产 | HF Nightly Space | 落后 staging 数日，仅 cherry-pick 已验证的变更 |
| **`staging`** | 🧪 验证前沿 | HF Staging + ModelScope Staging + 未来平台 | 跟踪上游 nightly，日常迭代 |

> 共享代码在 `staging` 上开发 → 验证通过 → cherry-pick 到 `main`。多平台差异通过 `squad_config.{platform}.json` 配置分离。

## 多平台支持

同一套部署代码，通过平台配置 + 抽象层适配不同运行环境：

| 平台 | 类型 | 数据根 | 检测方式 | 仓库 |
|------|------|--------|---------|------|
| `hf-direct` | HF Space (Nightly 生产) | `/data` | 默认 | 本仓库 `main` 分支 |
| `hf-staging` | HF Space (验证) | `/data` | `SPACE_ID` 含 `NanobotStaging` | 本仓库 `staging` 分支 |
| `modelscope` | ModelScope Studio (验证) | `/mnt/workspace` | `MODELSCOPE_ENVIRONMENT=studio` | 本仓库 `staging` 分支 |

> 扩展新平台：在 `staging` 分支添加 `platforms/{name}.py` + `squad_config.{name}.json`，共享代码零改动。

## 亮点

| 🎖️ | **多智能体协同** — 5 个专业智能体（Neo/Trinity/Sentinel/Assistant/Medic）通过 WebSocket 互联互通。指挥官可跨节点调度任务、查询状态、汇总结果。 |
|-----|-----|
| 🤖 | **自主运维** — Neo（军团指挥官）可自主完成上游版本适配验证：拉取最新代码 → 部署到 Staging → 检查构建/运行日志 → 验证通过后上报。 |
| 🔄 | **自愈机制** — Gatekeeper v6.0 健康监控：Neo 离线超过 150 秒自动复活（保守阈值防止 DeepSeek 长时间思考误触发），冷却 300 秒。 |
| 🛡️ | **OAuth + RBAC** — Hugging Face / ModelScope OAuth，三级权限：Commander（管理员）、Member（成员，可对话）、Guest（访客，只读）。 |
| 🎨 | **动态 WebUI** — 实时智能体状态徽标（待命/执行中/阻塞/离线）、动态侧边栏编制、跨节点标签切换。 |
| 🧪 | **多平台 CI/CD** — Staging 先验证最新上游 nightly，确认通过后再 cherry-pick 到生产 Nightly。同一套代码适配 HF Spaces + ModelScope Studio。 |
| 🐳 | **单 Dockerfile 部署** — 多阶段构建，合并上游 nanobot + 军团补丁，运行于 HF Spaces 免费套餐。 |

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
                      │     Neo▲   Trinity  Sentinel  ...     │
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

## 组件

| 文件 | 功能 | 分支 |
|------|------|------|
| `gatekeeper.py` | OAuth 网关（HF + ModelScope），三级权限控制，HTTP/WS 代理，跨智能体中继，保活守护 | 共用 |
| `squad_bridge.py` | 智能体之间 WebSocket 消息通信网 | 共用 |
| `squad_config_sync.py` | 实例配置动态同步。新 agent 从模板创建，已有 agent 仅同步动态端口与白名单。修改前自动备份，保留最近 3 份。 | 共用 |
| `platforms/` | 平台抽象层：`base.py`（Protocol）+ `hf_staging.py` + `hf_direct.py` + `modelscope.py`，零平台分支的主代码 | `staging` |
| `squad_config_loader.py` | 配置加载器：从 `squad_config.json` 读取并注入 `DEPLOY_PLATFORM`、`data_root` 等环境变量 | `staging` |
| `platform_setup.py` | 平台探测入口：导入 `platforms/` → 调用 `setup()` → 运行时环境配置 | `staging` |
| `squad_config.{platform}.json` | 平台专属配置（数据根、端口、权限）。`main` 用单一 `squad_config.json`。 | `staging` |
| `Dockerfile` | 多阶段构建，合并上游 nanobot + 军团部署层 + 补丁打入 | 共用 |
| `entrypoint.sh` | 运行时初始化：平台检测 → 自动选配 → 实例模板下发 → workspace 知识注入 → 补丁注入 → 保活 | 共用 |

### 补丁

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_legion_v6_sidebar.py` | `webui/src/components/Sidebar.tsx` | 注入 LegionTerminal + 动态编制 + 状态徽标 + 任务追踪面板 |
| `patch_legion_v4_client.py` | `webui/src/NanobotClient.tsx` | 注入 onAnyEvent 拦截器（`legion_update` / `cluster_log` / `task_update` 事件路由） |
| `patch_message_hardening.py` | `nanobot/providers/openai_compat_provider.py` | DeepSeek 消息内容清洗（移除 "(empty)" 占位符，保留 tool_calls 下的文本） |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | 为 squad bridge 提供结构化 error 事件（双目标：`/app/` + `site-packages/`） |

## 快速开始

本仓库是上游 NanoBot 的 **部署层叠加**。`Dockerfile` 位于根目录，squad 组件位于 `deploy/huggingface/`。

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
git clone -b main https://github.com/DreamShepherd2006/nanobot-legion.git deploy/huggingface
# Dockerfile 在 deploy/huggingface/Dockerfile — 复制到项目根目录或挂载为构建上下文
```

> ☝️ 默认 `main` 分支 = 稳定生产版。如需尝鲜，使用 `-b staging` 获取最新实验特性。

## 许可

MIT — 继承自[上游](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE)。

## 相关链接

- 上游项目：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- 上游 PR：[#3869](https://github.com/HKUDS/nanobot/pull/3869)（DeepSeek 消息清洗）· [#3908](https://github.com/HKUDS/nanobot/pull/3908)（WS peers_update 事件）
- 上游 Discussion：[#3925](https://github.com/HKUDS/nanobot/discussions/3925)（单容器多智能体系统）
- ModelScope 镜像：[Stone2006/nanobot-multi-agent-nightly](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly)
