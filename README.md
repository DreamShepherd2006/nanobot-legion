# Nanobot Legion

把单智能体 [NanoBot](https://github.com/HKUDS/nanobot) 扩展成多智能体协同系统的**部署层**，运行于 Hugging Face Spaces / ModelScope Studio。

> 🧪 跟踪 [NanoBot v0.2.1](https://github.com/HKUDS/nanobot/releases/tag/v0.2.1)（upstream/nightly [`dbdb146f`](https://github.com/HKUDS/nanobot/commit/dbdb146f)）

## 定位

Legion 有三层价值：

1. **多智能体协同** — 一个空间里跑一支 agent 队伍（指挥官 + 专职 worker），彼此可委派、可互查、可复活。
2. **平台适配** — 把 Hugging Face / ModelScope 的 OAuth、反向代理、持久卷、WebSocket 细节收进底座，上层应用不关心。
3. **业务应用底座（Legion Module）** — 业务界面与能力以「模块」形式挂载，应用自注册路由与会话内容，Legion 只负责发现与渲染，不为任何具体应用改代码。

## 仓库关系

```
cloud-agent-gateway                ← 框架底层（pip 包）：平台探测、OAuth、Relay
  │
  ├── 直接使用 ──→  Cloud Demo（HF / MS）  单智能体快速体验
  │
  └── pip 依赖 ──→  nanobot-legion（本仓库）  ← 应用部署层 + 模块底座
                      │
                      └── 挂载 ──→  Legion Module（如 nanobot-quant 量化系统）
```

- **[cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)** — 框架底层，框架无关，任何 agent 都可复用。
- **nanobot-legion（本仓库）** — 应用部署层：Gatekeeper 网关、多智能体编排、配置同步、复活守护、模块发现与渲染。
- **Legion Module** — 挂在网关上的业务应用（路由 + 会话区块 + 后台服务），独立仓库、独立发版。

## 在线空间

| 空间 | 平台 | 定位 |
|------|------|------|
| [Nightly](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly) | HF Spaces | 🛡️ 生产 |
| [HF Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging) | HF Spaces | 🧪 验证 |
| [MS Staging](https://www.modelscope.cn/studios/Stone2006/nanobot-multi-agent-nightly) | ModelScope | 🧪 验证 + 国内镜像 |
| [HF Cloud Demo](https://huggingface.co/spaces/DreamShepherd2006/nanobot-cloud-demo) | HF Spaces | ☁️ 单智能体体验 |
| [MS Cloud Demo](https://www.modelscope.cn/studios/DreamShepherd/ms-nanobot-cloud-demo) | ModelScope | ☁️ 单智能体体验 |

## 分支模型

| 分支 | 定位 | 部署到 |
|------|------|--------|
| `main` | 🛡️ 稳定生产 | HF Nightly（cherry-pick 已验证变更） |
| `staging` | 🧪 验证前沿 | HF Staging + MS Staging（日常迭代） |

功能开发走 `feat/*` → PR 到 `staging` → 打 tag 发布。Cloud Demo 空间由 cloud-agent-gateway 独立部署，不使用本仓库分支。

## 架构

```
┌───────────────────────────────────────────────┐
│  Gatekeeper —— 统一入口（OAuth · RBAC · Proxy）│
│    ├── WebUI / 消息通道 / 跨空间 Relay         │
│    ├── Legion Module（业务页面与会话区块）      │
│    └── Squad 编排（各 agent 实例 + 健康守护）   │
└───────────────────────────────────────────────┘
              ▲ pip install
┌───────────────────────────────────────────────┐
│  cloud-agent-gateway —— 平台抽象与中转          │
└───────────────────────────────────────────────┘
```

一句话概括运行方式：**一个容器、一个网关、一支 agent 队伍**；网关对外统一鉴权与路由，对内按名单代理到各 agent 的 WebSocket，模块在同一进程里注册业务路由。

## Legion Module（模块底座）

业务应用不再写进 Legion，而是各自以 entry point 声明、由 Legion 发现：

- 模块声明 `name` / `display` / `sdk` / `route_prefix`，实现 `register_routes()` 与 `sections()`
- Legion 负责：发现（含接口不兼容时拒载）、上下文注入（数据目录按模块隔离）、渲染、失败隔离
- 模块负责：自己的页面、自己的状态、自己的后台服务（`/apps/<name>` 前缀；历史应用沿用 `/config/*`）

契约与「如何写一个新模块」见 [docs/legion-module.md](docs/legion-module.md)。现成模块：`nanobot-quant`。

## 核心能力

- **多智能体编排** — 指挥官 + worker 的同空间协作，agent 间消息经 WebSocket 互通，支持模式化委派
- **健康守护** — 按名单自动复活离线 agent（宽限期 / 冷却），复活范围受白名单约束
- **配置同步** — 从模板生成实例、按需刷新端口与权限，只覆盖动态项、不覆盖用户改动
- **通道绑定** — 微信 / QQ / 飞书 / 钉钉 / Telegram / Discord 等自助绑定，凭证热生效
- **不 fork 上游** — 上游 nanobot 以 pip 方式安装，构建期以脚本注入少量补丁，升级只改 pin
- **零漂移部署** — 依赖全部 pin 到具体 commit，部署仓库只放一个 Dockerfile

## 部署

本仓库是 pip 包 + 部署资产，各空间通过 Dockerfile `pip install` 本仓库后由 `launch.sh` 拉起：

```
pip install nanobot-legion  →  解压部署资产  →  播种实例配置  →  启动 Gatekeeper 与各 agent
```

实际部署文件（Dockerfile、README）在各部署仓库与本仓库的 `deploy-*` 目录，不在聊天里手工拼装。

## 文档

| 文档 | 内容 |
|------|------|
| [docs/legion-module.md](docs/legion-module.md) | Legion Module 契约（sdk=1）、发现机制、Section/Row 原子、命名空间与失败隔离 |

## 相关链接

- 上游：[HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- 框架底层：[cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)
- Fork：[DreamShepherd2006/nanobot](https://github.com/DreamShepherd2006/nanobot/tree/nightly)

## 许可

MIT — 继承自[上游](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE)。
