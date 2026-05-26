# Nanobot Legion

将单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展为多智能体协同指挥系统的部署层，运行于 Hugging Face Spaces。

**在线演示**：[军团指挥中心](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly)（生产）· [Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging)（验证）

> 🧪 跟踪 [NanoBot v0.2.0](https://github.com/HKUDS/nanobot/releases/tag/v0.2.0)（upstream/nightly [`92f2ff3a`](https://github.com/HKUDS/nanobot/commit/92f2ff3a)）

> *"军团" — 一组各司其职的 AI 智能体，在统一指挥下协同作战。*

## 亮点

| 🎖️ | **多智能体协同** — 5 个专业智能体（Neo/Trinity/Sentinel/Assistant/Medic）通过 WebSocket 互联互通。指挥官可跨节点调度任务、查询状态、汇总结果。 |
|-----|-----|
| 🤖 | **自主运维** — Neo（军团指挥官）可自主完成上游版本适配验证：拉取最新代码 → 部署到 Staging → 检查构建/运行日志 → 验证通过后上报，无需人工介入。 |
| 🔄 | **自愈机制** — `resurrect_neo.sh` + Gatekeeper v6.0 健康监控：Neo 离线超过 150 秒自动复活（保守阈值防止 DeepSeek 长时间思考误触发），冷却 300 秒。 |
| 🛡️ | **OAuth + RBAC** — 基于 Hugging Face OAuth 的三级权限：Commander（管理员）、Member（成员，可对话）、Guest（访客，只读）。 |
| 🎨 | **动态 WebUI** — 实时智能体状态徽标（待命/执行中/阻塞/离线）、动态侧边栏编制、跨节点标签切换。全部由运行时环境变量驱动，零硬编码。 |
| 🧪 | **双空间 CI/CD** — Staging 先验证最新上游 nightly，确认通过后再 cherry-pick 到生产 Nightly，避免直接同步风险。 |
| 🐳 | **单 Dockerfile 部署** — 运行于 Hugging Face Spaces 免费套餐。多阶段构建：上游 nanobot + 军团补丁在构建时合并打入。 |

## 架构

```
                     ┌───────────────────────────────────────┐
                     │       Hugging Face Space (Nightly)    │
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
                     │   Neo ──▶ Staging 验证空间 ──▶ 上报   │
                     └───────────────────────────────────────┘
```
> Neo 作为军团指挥官，可自主拉取上游最新代码 → 部署到 Staging 空间 → 检查构建/运行日志 → 验证通过后报告，实现一线式版本适配验证。

## 组件

| 文件 | 功能 |
|------|------|
| `gatekeeper.py` | OAuth 网关（HF + ModelScope），三级权限控制（大小写精确匹配），HTTP/WS 代理，跨智能体中继，保活守护 |
| `squad_bridge.py` | 智能体之间 WebSocket 消息通信网 |
| `squad_config_sync.py` | 实例配置动态同步。**新 agent 从模板创建**，已有 agent 仅同步动态端口与白名单（不触碰 provider/model/ssrf）。**修改前自动备份**（`config.json.backup.{timestamp}`），保留最近 3 份。 |
| `Dockerfile` | 多阶段构建，合并上游 nanobot + 军团部署层 + 补丁打入 |
| `entrypoint.sh` | 运行时初始化：实例模板下发 + neo workspace 知识注入 + 运行时补丁注入 |

### 补丁

**v0.2.0 适配补丁：**

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_legion_v6_sidebar.py` | `webui/src/components/Sidebar.tsx` | 注入 LegionRoster + LegionTerminal 组件（动态编制、状态徽标、任务追踪面板） |
| `patch_legion_v4_client.py` | `webui/src/NanobotClient.tsx` | 注入 onAnyEvent 拦截器（`legion_update` / `cluster_log` / `task_update` 事件路由） |
| `patch_bootstrap_peers.py` | `nanobot/channels/websocket.py` | WS `peers_update` 事件 — 连接认证后推送节点编制 |
| `patch_message_hardening.py` | `nanobot/providers/openai_compat_provider.py` | DeepSeek 消息内容清洗（移除 "(empty)" 占位符、保留 tool_calls 下的文本） |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | squad bridge 权限错误事件发射（双目标：`/app/` + `site-packages/`） |

**旧版补丁（v0.1.x 系列，v0.2.0 中不可用）：**

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_sidebar_ui_v6.py` | `webui/src/` | 旧版 Sidebar 注入（v0.1.x 锚点，v0.2.0 重构后失效） |
| `patch_app_logic_v4.py` | `webui/src/` | 旧版 App 逻辑补丁（同上） |

## 快速开始

本仓库是上游 NanoBot 的 **部署层叠加**。

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
git clone https://github.com/DreamShepherd2006/nanobot-legion.git deploy/huggingface
# 构建与部署参见 Dockerfile
```

## 许可

MIT — 继承自[上游](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE)。

## 相关链接

- 上游项目：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- 上游 PR：[#3854](https://github.com/HKUDS/nanobot/pull/3854)（节点编制，已关闭）· [#3869](https://github.com/HKUDS/nanobot/pull/3869)（消息清洗）· [#3908](https://github.com/HKUDS/nanobot/pull/3908)（WS peers_update 事件）
- ModelScope 验证空间：[Stone2006/nanobot-multi-agent-nightly](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly)

## 最近更新

`44fb9bd` — 2026-05-26

- 上游追踪: `nanobot v0.2.0` (commit `92f2ff3a`)
- gatekeeper: ModelScope OAuth 解析修复、GRACE_SECONDS 150、`import subprocess`
- entrypoint: neo workspace 知识注入 + 运行时补丁注入
- patch_bootstrap_peers: v5.0 peers_update WS 事件
- patch_legion_v6_sidebar: 完整 LegionTerminal 组件
- patch_squad_error_events: 双目标补丁 + 备份
