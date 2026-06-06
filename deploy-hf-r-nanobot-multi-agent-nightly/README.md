# deploy-hf-r-nanobot-multi-agent-nightly

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Nightly 生产空间** |
| 平台 | HuggingFace Spaces |
| 空间 URL | `DreamShepherd2006/nanobot-multi-agent-nightly` |
| nanobot-legion 分支 | `main` |

## 部署代码

生产空间使用 `nanobot-legion` `main` 分支的以下代码：

- `Dockerfile` — 容器构建
- `deploy/huggingface/` — gatekeeper, launch.sh, patches, squad 配置
- `deploy/cloud/` — entrypoint, platform_setup

## 更新流程

1. 修改推送到 `nanobot-legion` `staging` 分支
2. Staging 空间验证通过
3. cherry-pick 到 `main` 分支
4. 推送到本空间
