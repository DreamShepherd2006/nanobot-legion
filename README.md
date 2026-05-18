# Nanobot Legion

Multi-agent deployment overlay for [NanoBot](https://github.com/HKUDS/nanobot) — transforms a single-agent chatbot into a coordinated multi-agent system on Hugging Face Spaces.

**Live Demo**: [Legion Command Center](https://huggingface.co/spaces/DreamShepherd2006/nanobot-multi-agent-nightly) (Nightly) · [Staging](https://huggingface.co/spaces/DreamShepherd2006/Nanobot-Staging)

> *"Legion" — a coordinated group of AI agents, each with specialized roles, working together under a unified command structure.*

## Highlights

| 🎖️ | **Multi-Agent Mesh** — 5 specialized agents (Neo/Trinity/Sentinel/Assistant/Medic) communicate via WebSocket relay. Commander can dispatch tasks, query status, and aggregate results across the legion. |
|-----|-----|
| 🔄 | **Self-Healing** — `resurrect_agent.sh` + gatekeeper health monitor: any agent offline for >10s is automatically revived. Zero manual intervention. |
| 🛡️ | **OAuth + RBAC** — Hugging Face OAuth with three-tier permissions: Commander (admin), Business (agent-bound), Guest (read-only). |
| 🎨 | **Dynamic WebUI** — Real-time agent status badges (standby/executing/blocked/disconnected), dynamic sidebar roster, cross-agent tab switching. |
| 🧪 | **Dual-Space CI/CD** — Staging validates latest upstream nightly before cherry-picking to production. No risky direct sync. |
| 🐳 | **Single Dockerfile** — Runs on Hugging Face Spaces free tier. Multi-stage build patches and merges upstream + legion overlay at build time. |

## Architecture

```
                    ┌──────────────────────────────────────┐
                    │           Hugging Face Space          │
                    │                                       │
  Browser ──────────▶  Gatekeeper (FastAPI/WS proxy)       │
  (WebUI)            │    ├─ OAuth / RBAC                   │
                      │    ├─ HTTP Relay (cross-agent)       │
                      │    └─ WebSocket proxy                │
                      │         │         │         │        │
                      │      Neo(A)  Trinity  Sentinel  ...  │
                      │    ┌─────────────────────────────┐   │
                      │    │  squad_bridge.py (WS mesh)  │   │
                      │    │  squad_config_sync.py       │   │
                      │    └─────────────────────────────┘   │
                      └──────────────────────────────────────┘
```

## Components

| File | Role |
|------|------|
| `gatekeeper.py` | OAuth gateway, RBAC, HTTP/WS proxy, cross-agent relay |
| `squad_bridge.py` | Agent-to-agent WebSocket messaging mesh |
| `squad_config_sync.py` | Dynamic config propagation across instances |
| `Dockerfile` | Multi-stage build merging upstream nanobot + legion overlay |
| `entrypoint.sh` | Runtime initialization, instance template seeding |

### Patches

| Patch | Target | Purpose |
|-------|--------|---------|
| `patch_sidebar_ui_v6.py` | `webui/src/` | Dynamic agent roster sidebar with status badges |
| `patch_app_logic_v4.py` | `webui/src/` | Legion message interceptor |
| `patch_bootstrap_peers.py` | `nanobot/channels/websocket.py` | Expose peer roster via `/webui/bootstrap` |
| `patch_message_hardening.py` | `nanobot/providers/` | DeepSeek message sanitization |
| `patch_squad_error_events.py` | `nanobot/channels/websocket.py` | Structured error events for squad bridge |

## Quick Start

This repo is a **deployment overlay** on top of upstream NanoBot.

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
git clone https://github.com/DreamShepherd2006/nanobot-legion.git deploy/huggingface
# Build & deploy (see Dockerfile)
```

## License

MIT — inherits from [upstream](https://github.com/HKUDS/nanobot/blob/nightly/LICENSE).

## Related

- Upstream: [HKUDS/nanobot](https://github.com/HKUDS/nanobot)
- Upstream PRs: [#3854](https://github.com/HKUDS/nanobot/pull/3854) (peer bootstrap) · [#3869](https://github.com/HKUDS/nanobot/pull/3869) (message hardening) · [#3891](https://github.com/HKUDS/nanobot/pull/3891) (remote bootstrap access)
