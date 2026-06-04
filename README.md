# Nanobot Legion

将单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展为多智能体协同指挥系统的部署层，运行于 Hugging Face Spaces / ModelScope Studio。

> 🧪 跟踪 [NanoBot v0.2.0](https://github.com/HKUDS/nanobot/releases/tag/v0.2.0)（upstream/nightly [`92f2ff3a`](https://github.com/HKUDS/nanobot/commit/92f2ff3a)）

## 仓库关系

```
                    ┌─ HF Cloud Demo (HF) ──────┐
cloud-agent-gateway ┤                            ├─ 单智能体，纯平台层
(pip 包)            └─ MS Cloud Demo (MS) ───────┘
        │
        │ pip install
        ▼
nanobot-legion                      ← Squad 多智能体部署层
(本仓库)
        │
        │ 部署到
        ▼
  ┌─────────────────────────────────────────────────┐
  │ Nightly (HF)     HF Staging      MS Staging     │
  │ 🛡️ 生产          🧪 验证          🧪 验证+国内    │
  └─────────────────────────────────────────────────┘
```

- **[cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)** — 框架无关的云部署层，提供 OAuth 回调、平台探测、HTTP 中继。Cloud Demo 空间直接使用它；nanobot-legion 通过 `pip install` 依赖它。
- **nanobot-legion (本仓库)** — 在 cloud-agent-gateway 之上叠加 Squad 多智能体层（Gatekeeper、跨智能体 WebSocket 通信网、配置同步、复活守护）。部署到 Nightly + 两个 Staging 空间。
- **上游客制** — 通过构建时 `sed` 补丁 + 运行时 Python 补丁注入，不改上游源码。

## 在线空间

| 空间 | 平台 | 定位 | 链接 |
|------|------|------|------|
| Nightly | HF Spaces | 🛡️ 生产 | [DreamShepherd2006/nanobot-multi-agent-nightly](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly) |
| HF Staging | HF Spaces | 🧪 验证 | [DreamShepherd2006/Nanobot-Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging) |
| MS Staging | ModelScope | 🧪 验证 + 国内镜像 | [Stone2006/nanobot-multi-agent-nightly](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly) |
| HF Cloud Demo | HF Spaces | ☁️ 单智能体快速体验 | [DreamShepherd2006/nanobot-cloud-demo](https://huggingface.co/spaces/DreamShepherd2006/nanobot-cloud-demo) |
| MS Cloud Demo | ModelScope | ☁️ 单智能体快速体验 | [DreamShepherd/ms-nanobot-cloud-demo](https://www.modelscope.cn/studios/DreamShepherd/ms-nanobot-cloud-demo) |

## 分支模型

本仓库两个分支，对应 Squad 空间的部署：

| 分支 | 定位 | 部署到 | 节奏 |
|------|------|--------|------|
| `main` | 🛡️ 稳定生产 | HF Nightly | cherry-pick 已验证变更 |
| `staging` | 🧪 验证前沿 | HF Staging + MS Staging | 跟踪上游 nightly，日常迭代 |

> 同一 `staging` 分支承载多平台，通过 `squad_config.{platform}.json` 区分，启动时自动选配。共享代码（gatekeeper、补丁、bridge）在 `staging` 开发，稳定后 cherry-pick 到 `main`。
>
> Cloud Demo 空间（HF / MS）不使用本仓库分支——它们由 [cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway) 独立部署。

## 架构

```
                         浏览器
                           │
              ┌────────────▼─────────────┐
              │       Gatekeeper          │
              │  OAuth · RBAC · Relay     │
              │  cloud-agent-gateway      │
              └────┬──────┬──────┬────────┘
                   │      │      │
              ┌────▼──┐ ┌─▼──┐ ┌─▼────────┐
              │  Neo   │ │ …… │ │  ……      │ 多智能体 Squad
              │(Commander)│    │           │
              └───┬────┘ └────┘ └──────────┘
                  │
         ┌────────┼────────┐
         ▼        ▼        ▼
    HF Staging  MS Staging  (已通过实战验证)
```

| 层 | 职责 |
|----|------|
| platform（cloud-agent-gateway） | 平台探测、OAuth 回调、HTTP Relay 中继 → 任何 agent 框架都可用 |
| squad（nanobot-legion） | Gatekeeper 多智能体网关、RBAC 三级权限、跨智能体 WebSocket 通信网、配置同步、复活守护 |
| agent（upstream nanobot） | 原生单智能体，零定制 |

## 核心组件

| 组件 | 职责 |
|------|------|
| `gatekeeper.py` | OAuth 网关、三级 RBAC、HTTP/WS 代理、跨空间 relay、保活守护 |
| `squad_bridge.py` | 智能体间 WebSocket 消息通信网 |
| `squad_bridge_cross.py` | 跨空间 relay（Staging → Nightly）、调用方白名单 + 远程 token 认证 |
| `squad_config_sync.py` | 实例配置同步——新 agent 从模板创建，已有 agent 仅更新动态端口与白名单 |
| `push_tasks.py` | 任务进度推送——结构化 JSON `task_update` 事件，前端实时展示 |
| `platform_setup.py` | 启动时平台探测 + 自动选配 |
| `squad_config_loader.py` | 从 JSON 配置注入运行时环境变量 |
| `entrypoint.sh` | 容器入口：平台检测 → 选配 → 实例模板下发 → 补丁注入 → 保活启动 |
| `resurrect_neo.sh` | Neo 离线 > 150s 自动复活（跨平台路径 `$INSTANCE_ROOT`），冷却 300s |

### 多平台配置

```
deploy/huggingface/
├── squad_config.json                  ← 兜底 (HF Nightly)
├── squad_config.hf-staging.json       ← HF Staging
├── squad_config.ms-staging.json       ← ModelScope Staging
└── squad_config.{platform}.json       ← 新平台只需 +1 文件
```

## 补丁

构建时通过 Python 脚本注入，不改上游源码。v0.2.0 共 7 个活跃补丁：

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_legion_v6_sidebar.py` | `webui/src/components/Sidebar.tsx` | 动态编制、状态徽标、LegionTerminal 任务面板 |
| `patch_legion_v4_client.py` | `webui/src/NanobotClient.tsx` | `onAnyEvent` 拦截器（legion_update / task_update 事件路由） |
| `patch_message_hardening.py` | `nanobot/providers/openai_compat_provider.py` | DeepSeek 消息清洗（移除空占位符） |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | 结构化 error 事件（双目标：/app + site-packages） |
| `patch_gatekeeper_identity.py` | `nanobot/channels/manager.py` | OAuth 身份透传（sender_id/name 注入 relay 消息） |
| `patch_webui_squad_sessions.py` | `webui/src/` API 调用 | `/api/sessions` → `/api/squad/sessions?token=` (ModelScope 路由绕过) |
| `patch_package_json_radix.py` | `webui/package.json` | 构建时注入 radix-ui 依赖 |

## 部署

```bash
# 本仓库是部署层，叠加在上游 NanoBot 之上
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
git clone https://github.com/DreamShepherd2006/nanobot-legion.git legion-overlay

# Dockerfile 位于 legion-overlay/Dockerfile
# cloud-agent-gateway 通过 pip 自动安装
```

Docker 多阶段构建流程：

```
上游 nanobot 源码
    │
    ├─ sed 内核补丁 (WebSocket 鉴权、0.0.0.0 绑定)
    ├─ Python WebUI 补丁 (Sidebar、Client、Sessions)
    ├─ pip install cloud-agent-gateway
    ├─ npm build
    ├─ Python 运行时补丁 (message hardening、error events、identity)
    └─ 实例种子 + squad 脚本 → 最终镜像
```

## 相关链接

- 上游：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- 云层：[cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)（pip 包 — 平台抽象、OAuth、中继）
- Fork：[DreamShepherd2006/nanobot](https://github.com/DreamShepherd2006/nanobot/tree/nightly)
- 上游 PR：[#3869](https://github.com/HKUDS/nanobot/pull/3869)（DeepSeek 消息清洗）· [#3908](https://github.com/HKUDS/nanobot/pull/3908)（WS peers_update）· [#4139](https://github.com/HKUDS/nanobot/pull/4139)（WS target_chat_id 会话恢复）· [#4134](https://github.com/HKUDS/nanobot/pull/4134)（WS 权限错误事件 — 已关）
- 讨论：[#3925](https://github.com/HKUDS/nanobot/discussions/3925)（单容器多智能体）

## 许可

MIT — 继承自[上游](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE)。
