# deploy-hf-r-Nanobot-Staging

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Staging 验证空间（HF）** |
| 平台 | HuggingFace Spaces |
| 空间 URL | `DreamShepherd2006/Nanobot-Staging` |
| nanobot-legion 分支 | `staging` |

## 部署代码

Staging 使用 `nanobot-legion` `staging` 分支：

- `Dockerfile` — 容器构建
- `deploy/huggingface/` — gatekeeper, launch.sh, patches, squad 配置
- `deploy/cloud/` — entrypoint, platform_setup

## 更新流程

1. 修改 `nanobot-legion` `staging` 分支
2. 推送到本空间 → HF 自动构建
3. 验证通过后 cherry-pick 到 `main` → Nightly
