# Nanobot Legion

将单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展为多智能体协同指挥系统的部署层，运行于 Hugging Face Spaces。

**在线演示**：[军团指挥中心](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly)（生产）· [Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging)（验证）

> *"军团" — 一组各司其职的 AI 智能体，在统一指挥下协同作战。*

## 亮点

| 🎖️ | **多智能体协同** — 5 个专业智能体（Neo/Trinity/Sentinel/Assistant/Medic）通过 WebSocket 互联互通。指挥官可跨节点调度任务、查询状态、汇总结果。 |
|-----|-----|
| 🤖 | **自主运维** — Neo（军团指挥官）可自主完成上游版本适配验证：拉取最新代码 → 部署到 Staging → 检查构建/运行日志 → 验证通过后上报，无需人工介入。 |
| 🔄 | **自愈机制** — `resurrect_agent.sh` + gatekeeper 健康监控：智能体离线超过 10 秒自动复活，无需人工干预。 |
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
| `gatekeeper.py` | OAuth 网关，三级权限控制，HTTP/WS 代理，跨智能体中继 |
| `squad_bridge.py` | 智能体之间 WebSocket 消息通信网 |
| `squad_config_sync.py` | 实例配置动态同步 |
| `Dockerfile` | 多阶段构建，合并上游 nanobot + 军团部署层 |
| `entrypoint.sh` | 运行时初始化，实例模板下发 |

### 补丁

| 补丁 | 目标 | 作用 |
|------|------|------|
| `patch_sidebar_ui_v6.py` | `webui/src/` | 动态智能体编制侧边栏 + 状态徽标 |
| `patch_app_logic_v4.py` | `webui/src/` | 军团消息拦截器 |
| `patch_bootstrap_peers.py` | `nanobot/channels/websocket.py` | 通过 `/webui/bootstrap` 暴露节点编制 |
| `patch_message_hardening.py` | `nanobot/providers/` | DeepSeek 消息内容清洗 |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | 为 squad bridge 提供结构化错误事件 |

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
- 上游 PR：[#3854](https://github.com/HKUDS/nanobot/pull/3854)（节点编制）· [#3869](https://github.com/HKUDS/nanobot/pull/3869)（消息清洗）· [#3891](https://github.com/HKUDS/nanobot/pull/3891)（远程启动访问）
