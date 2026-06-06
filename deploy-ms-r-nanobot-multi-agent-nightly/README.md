# deploy-ms-r-nanobot-multi-agent-nightly

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Staging 验证空间（MS）** |
| 平台 | ModelScope Studio |
| 空间 URL | `Stone2006/nanobot-multi-agent-nightly` |
| nanobot-legion 分支 | `staging` |

## 部署代码

Staging 使用 `nanobot-legion` `staging` 分支：

- `Dockerfile` — 容器构建
- `deploy/huggingface/` — gatekeeper, launch.sh, patches, squad 配置
- `deploy/cloud/` — entrypoint, platform_setup

## 更新流程

1. 修改 `nanobot-legion` `staging` 分支
2. 推送到本空间 → 需手动重启触发重建
3. 验证通过后 cherry-pick 到 `main` → Nightly

## 注意

- 构建分支: `master`
- 重建方式: 空间设置中手动下线→上线
