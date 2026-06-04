# deploy/cloud/ → cloud-agent-gateway

本目录的云部署层代码已独立为 pip 包：

👉 **[cloud-agent-gateway](https://github.com/DreamShepherd2006/cloud-agent-gateway)**

## 职责

- 平台探测（HF Spaces / ModelScope Studio）
- OAuth 认证回调
- HTTP Relay 中继
- 环境变量注入

## 使用

```dockerfile
RUN uv pip install git+https://github.com/DreamShepherd2006/cloud-agent-gateway.git
```

```bash
eval "$(cloud-gateway-setup)"   # 平台探测 → shell exports
cloud-agent-gateway             # 启动 OAuth 代理 + nanobot gateway
```

## 为什么独立

cloud-agent-gateway 是框架无关的平台层，不包含任何 agent 逻辑。nanobot-legion 和单 agent 云空间都通过 pip 依赖它，避免代码重复。
