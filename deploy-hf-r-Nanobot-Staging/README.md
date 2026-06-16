# deploy-hf-r-Nanobot-Staging

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Staging 验证空间（HF）** |
| 平台 | HuggingFace Spaces |
| 空间 URL | `DreamShepherd2006/Nanobot-Staging` |
| nanobot-legion 分支 | `staging` |
| 上游 nanobot | dbdb146f |
| 框架版本 | cloud-agent-gateway v0.1.5 |

## 部署代码

Staging 使用 `nanobot-legion` `staging` 分支：

- `Dockerfile` — 容器构建（sed 补丁 + pip 安装）
- `deploy/huggingface/` — gatekeeper, launch.sh, patches（v0.2.1_ede4c69）, squad 配置
- `entrypoint.sh` — 平台检测 + relay token 映射 + 进程守护（已提升到根目录）
- `config.template.json` — 首次运行 seed 配置

## 核心能力

| 能力 | 说明 |
|:---|:---|
| OAuth | HF OAuth → gatekeeper 身份注入（`sender_id` / `sender_name`） |
| Squad Relay | 多 agent HTTP Relay（commander_whitelist 权限控制） |
| Gatekeeper | 5 智能体调度中枢 + WebSocket 代理 + 复活守护 |
| 通道绑定 | Gatekeeper 自动注册 `/bind/{channel}` 路由，写入 `instances/{agent}/channels/` |
| Sidebar V6 | 军团指挥中心终端 — 勋章阵列 + 实时心跳 |

## 上游 PR

| PR | 标题 | 分支 | 状态 |
|:---|:---|:---|:---|
| [#4271](https://github.com/HKUDS/nanobot/pull/4271) | feat(agent): skip LLM for read_only sessions | `nightly` | 等 review |
| [#4139](https://github.com/HKUDS/nanobot/pull/4139) | feat(ws): accept target_chat_id hint | `nightly` | 等 review |
| [#3908](https://github.com/HKUDS/nanobot/pull/3908) | feat(ws): emit peers_update event | `nightly` | 等 review |
| [#4223](https://github.com/HKUDS/nanobot/pull/4223) | fix(weixin): reload session after pause | `main` | 等 review |
| [#3869](https://github.com/HKUDS/nanobot/pull/3869) | fix(providers): DeepSeek message hardening | `main` | 等 review |

## 更新流程

1. 修改 `nanobot-legion` `staging` 分支
2. 推送到本空间 → HF 自动构建
3. 验证通过后 cherry-pick 到 `main` → Nightly
