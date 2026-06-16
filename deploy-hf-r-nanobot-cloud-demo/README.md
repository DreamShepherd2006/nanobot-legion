# deploy-hf-r-nanobot-cloud-demo

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Cloud Demo（HF）** |
| 平台 | HuggingFace Spaces |
| 空间 URL | `DreamShepherd2006/nanobot-cloud-demo` |
| 部署仓库 | `DreamShepherd2006/nanobot-cloud-demo`（独立仓库） |
| 框架版本 | cloud-agent-gateway v0.1.5 |

## 说明

Cloud Demo 无 Squad 覆层代码。

- 运行方式: 框架层 (`cloud-agent-gateway`) + 官方 nanobot 源码
- 部署代码在独立仓库 `DreamShepherd2006/nanobot-cloud-demo`，**不在** `nanobot-legion` 内
- 本目录仅作为 `nanobot-legion` 的空间索引保留

## 核心能力

| 能力 | 说明 |
|:---|:---|
| OAuth | HF OAuth（`hf_oauth: true` 自动注入） |
| Relay | HTTP Relay（单 agent 模式，token 验证） |
| 通道绑定 | 15 通道自助绑定（微信/QQ/飞书/钉钉/Telegram/Discord/Slack + 8 手动配置） |
| 持久化 | `PersistentStorageProtocol` 统一 11 方法读写 |

## 同步

cloud-agent-gateway 框架改动 → 同步到本仓库 `repos/cloud-agent-gateway/` 本地副本 → `git push space`。
