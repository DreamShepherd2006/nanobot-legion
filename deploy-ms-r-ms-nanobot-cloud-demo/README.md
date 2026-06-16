# deploy-ms-r-ms-nanobot-cloud-demo

## 空间信息

| 项目 | 内容 |
|------|------|
| 角色 | **Cloud Demo（MS）** |
| 平台 | ModelScope Studio |
| 空间 URL | `DreamShepherd/ms-nanobot-cloud-demo` |
| 部署仓库 | `DreamShepherd/ms-nanobot-cloud-demo`（独立仓库） |
| 框架版本 | cloud-agent-gateway v0.1.5 |
| 构建分支 | `master` |

## 说明

Cloud Demo 无 Squad 覆层代码。

- 运行方式: 框架层 (`cloud-agent-gateway`) + 官方 nanobot 源码
- 部署代码在独立仓库 `DreamShepherd/ms-nanobot-cloud-demo`，**不在** `nanobot-legion` 内
- 本目录仅作为 `nanobot-legion` 的空间索引保留

## 核心能力

| 能力 | 说明 |
|:---|:---|
| OAuth | ModelScope OAuth（`?token=` fallback 绕过 header 剥离） |
| Relay | HTTP Relay（单 agent 模式，token 验证） |
| 通道绑定 | 15 通道自助绑定 |
| 持久化 | `PersistentStorageProtocol` → 数据集 `DreamShepherd/ms-nanobot-cloud-demo-data` |
| Dataset 同步 | 后台 pull timer（60s），自动检测远端变更并 deep-merge 到运行中实例 |

## 同步

cloud-agent-gateway 框架改动 → 同步到本仓库 `repos/cloud-agent-gateway/` 本地副本 → `git push`（master 分支）。
