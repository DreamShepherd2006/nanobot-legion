# deploy-hf-r-nanobot-multi-agent-nightly

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Nightly 生产空间** |
| 平台 | HuggingFace Spaces |
| 空间 URL | `DreamShepherd2006/nanobot-multi-agent-nightly` |
| nanobot-legion 分支 | `main` |

## 部署代码

生产空间使用 `nanobot-legion` `main` 分支的全部部署代码：

- `Dockerfile` — 容器构建
- `deploy/huggingface/` — gatekeeper, launch.sh, patches, squad 配置
- `deploy/cloud/` — entrypoint, platform_setup

## 升级策略

- `main` 分支版本落后于 `staging`
- Staging 验证可靠后，cherry-pick 升版
- 不直接从 staging force push，保持生产稳定

## 关联

- 同仓库 staging 分支 → 各 Staging 空间验证
- 验证通过 → cherry-pick → main → 本空间
