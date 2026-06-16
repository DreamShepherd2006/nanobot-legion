# deploy-ms-r-nanobot-multi-agent-nightly

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Staging 验证空间（MS）** |
| 平台 | ModelScope Studio |
| 空间 URL | `Stone2006/nanobot-multi-agent-nightly` |
| nanobot-legion 分支 | `staging` |
| 上游 nanobot | dbdb146f |
| 框架版本 | cloud-agent-gateway v0.1.5 |
| 构建分支 | `master` |

## 部署代码

Staging 使用 `nanobot-legion` `staging` 分支：

- `Dockerfile` — 容器构建（sed 补丁 + pip 安装）
- `deploy/huggingface/` — gatekeeper, launch.sh, patches（v0.2.1_ede4c69）, squad 配置
- `entrypoint.sh` — 平台检测 + relay token 映射 + 进程守护（已提升到根目录）
- `squad_config.ms-staging.json` — 从数据集 `Stone2006/nanobot-multi-agent-nightly-data` 拉取

## 核心能力

| 能力 | 说明 |
|:---|:---|
| OAuth | ModelScope OAuth → gatekeeper 身份注入（`?token=` fallback） |
| Squad Relay | 多 agent HTTP Relay（commander_whitelist 权限控制） |
| Gatekeeper | 5 智能体调度中枢 + WebSocket 代理 + 复活守护 |
| 通道绑定 | Gatekeeper 自动注册 `/bind/{channel}` 路由 |
| Dataset 同步 | 后台 pull timer（60s），自动检测远端变更并 deep-merge 到运行中实例 |
| 持久化 | `PersistentStorageProtocol` → 数据集 `Stone2006/nanobot-multi-agent-nightly-data` |

## 更新流程

1. 修改 `nanobot-legion` `staging` 分支
2. 推送到本空间 → 需手动重启（下线→上线）
3. 验证通过后 cherry-pick 到 `main` → Nightly

## 平台差异

- 构建分支: `master`（非 `main`）
- 重建方式: 空间设置中手动下线→上线
- Route 绕过: `/api/squad/` 命名空间下的代理路由 + `?token=` query parameter fallback
- 平台检测: `deploy_platform` 字段在 `squad_config.json` 中设为 `modelscope-squad`
