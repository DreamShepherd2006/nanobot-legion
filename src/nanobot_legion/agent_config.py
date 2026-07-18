#!/usr/bin/env python3
"""Agent Management — 配置中心：Worker Agent 增删。

Mounted by gatekeeper.py at Phase 2; serves GET/POST under /config/agents.
Provider 列表来自 nanobot 官方 ``providers/registry.py``，自动跟随上游更新。
"""
from __future__ import annotations

import datetime, json, os, shutil, subprocess, time
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from .squad_config_loader import load_config, save_config

# ── provider helpers ─────────────────────────────────────────
from .agent_providers import (
    find_by_name, _build_provider_js_data, _get_setup_providers, _PROVIDER_MODELS
)

_AGENT_CONFIG_TEMPLATE = """\
{
  "agents": {
    "defaults": {
      "provider": "$PROVIDER$",
      "model": "$MODEL$",
      "instructions": $INSTRUCTIONS$
    }
  },
  "providers": $PROVIDERS$,
  "gateway": {"host": "127.0.0.1", "port": $GW_PORT$},
  "channels": {"websocket": {"enabled": true, "port": $WS_PORT$}},
  "tools": {
    "exec": {
      "allowed_env_keys": ["PATH", "HOME", "USER", "LANG", "PYTHONPATH", "VIRTUAL_ENV", "NANOBOT_ACCOUNT_BASE"],
      "restrict_to_workspace": true,
      "deny_patterns": ["git push", "git commit", "git rebase", "gh pr create", "gh pr merge"]
    }
  }
}
"""

# ── HTML ───────────────────────────────────────────────────

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))

def _load_template(filename: str) -> str:
    """Load an HTML template from the package directory."""
    with open(_os.path.join(_HERE, filename), encoding="utf-8") as f:
        return f.read()

_HTML = _load_template("agent_config.html")

# ── Logic ──────────────────────────────────────────────────

def _get_neo_config(squad_cfg: dict) -> dict:
    """Load neo's config.json. Returns empty dict on failure."""
    data_root = squad_cfg.get("data_root", "/data")
    neo_config_path = os.path.join(data_root, "instances", "neo", "config.json")
    try:
        with open(neo_config_path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _get_neo_provider_info(neo_cfg: dict) -> tuple[str, str, str]:
    """Extract (provider_id, api_key, api_base) from neo config."""
    agents = neo_cfg.get("agents", {})
    defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
    provider_id = defaults.get("provider", "")
    providers = neo_cfg.get("providers", {})
    if isinstance(providers, dict) and provider_id:
        prov = providers.get(provider_id, {})
        if isinstance(prov, dict):
            api_key = prov.get("api_key", "")
            api_base = prov.get("api_base", "")
            return provider_id, api_key, api_base
    return provider_id, "", ""


def _build_worker_providers(provider_id: str, model_id: str, api_key: str,
                            neo_providers: dict) -> dict:
    """Build providers dict for worker agent config.

    Uses official ProviderSpec for default_api_base when provider is
    in the nanobot registry. Falls back to neo's api_base if same provider.
    """
    spec = find_by_name(provider_id)
    api_base = ""
    if spec is not None and spec.default_api_base:
        api_base = spec.default_api_base
    elif provider_id in neo_providers:
        neo_p = neo_providers[provider_id]
        if isinstance(neo_p, dict):
            api_base = neo_p.get("api_base", "")

    # API key: use worker's own key, fallback to neo's
    key = api_key
    if not key and provider_id in neo_providers:
        neo_p = neo_providers[provider_id]
        if isinstance(neo_p, dict):
            key = neo_p.get("api_key", "")

    return {
        provider_id: {
            "api_key": key,
            "api_base": api_base,
        }
    }


# ── Agent ops / views ──────────────────────────────────────
from .agent_ops import (
    _get_listening_ports, _allocate_ports, _patch_agent_config_port,
    _detect_running_agents, _read_agent_metadata, _find_archived_agents,
    _kill_agent_process, _sync_roster
)

from .agent_views import _escape_js, _escape_html, _render_running_table, _render_archived_table

# ── Route Handlers ─────────────────────────────────────────

def create_agent_routes(app, gatekeeper):
    """Mount agent management routes on gatekeeper's FastAPI app."""
    app.state.gatekeeper = gatekeeper

    from .agent_handlers import _read_config_summary
    from .agent_handlers import agent_page as _agent_page

    async def _add_agent(request: Request):
        """POST /config/agents/add — create a new worker agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip().lower()
        role = (body.get("role", "") or "").strip()
        provider_id = (body.get("provider", "") or "").strip()
        model_id = (body.get("model", "") or "").strip()
        api_key = (body.get("api_key", "") or "").strip()

        # Validate
        if not name:
            return JSONResponse({"ok": False, "error": "名字不能为空"}, status_code=400)
        if not name[0].isalpha():
            return JSONResponse({"ok": False, "error": "名字必须以字母开头"}, status_code=400)
        if not all(c.isalnum() or c in '_-' for c in name):
            return JSONResponse({"ok": False, "error": "名字只能包含字母、数字、下划线和连字符"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "neo 是保留的 Commander 名字，不可用作 Worker"}, status_code=400)
        if not role:
            return JSONResponse({"ok": False, "error": "角色说明不能为空"}, status_code=400)
        if not provider_id:
            return JSONResponse({"ok": False, "error": "请选择服务商"}, status_code=400)
        if not model_id:
            return JSONResponse({"ok": False, "error": "请选择或输入模型"}, status_code=400)

        # Verify provider exists in registry (or is "custom")
        if provider_id != "custom" and find_by_name(provider_id) is None:
            return JSONResponse({"ok": False, "error": f"未知服务商: {provider_id}"}, status_code=400)

        # Load current squad config
        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})

        if name in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已存在"}, status_code=409)

        # Check if archived copy exists — reject, guide user to restore
        data_root = squad_cfg.get("data_root", "/data")
        archived_dir = os.path.join(data_root, "instances", f"{name}.removed.")
        try:
            for entry in os.listdir(os.path.join(data_root, "instances")):
                if entry.startswith(f"{name}.removed.") and os.path.isdir(os.path.join(data_root, "instances", entry)):
                    return JSONResponse(
                        {"ok": False, "error": f"Agent '{name}' 的归档目录已存在（{entry}），请在📦已归档中使用「恢复」。"},
                        status_code=409,
                    )
        except OSError:
            pass

        # Read neo config for fallback providers
        neo_cfg = _get_neo_config(squad_cfg)
        if not neo_cfg:
            return JSONResponse({"ok": False, "error": "无法读取 neo 配置，请确保 Commander 已初始化"}, status_code=500)

        neo_providers = neo_cfg.get("providers", {})

        # Allocate ports
        gw, ws = _allocate_ports(peers)

        # Build providers section for this worker
        worker_providers = _build_worker_providers(
            provider_id, model_id, api_key, neo_providers
        )

        # Generate agent config
        instructions_json = json.dumps(role, ensure_ascii=False)
        providers_str = json.dumps(worker_providers, ensure_ascii=False, indent=2)
        agent_config_json = (_AGENT_CONFIG_TEMPLATE
            .replace("$PROVIDER$", provider_id)
            .replace("$MODEL$", model_id)
            .replace("$INSTRUCTIONS$", instructions_json)
            .replace("$PROVIDERS$", providers_str)
            .replace("$GW_PORT$", str(gw))
            .replace("$WS_PORT$", str(ws))
        )

        # Write agent config
        agent_dir = os.path.join(squad_cfg.get("data_root", "/data"), "instances", name)
        os.makedirs(agent_dir, exist_ok=True)
        config_path = os.path.join(agent_dir, "config.json")
        try:
            with open(config_path, "w") as f:
                f.write(agent_config_json)
            os.chmod(config_path, 0o600)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"无法写入配置: {e}"}, status_code=500)

        # Write AGENTS.md
        agents_md = f"# {name}\n\n{role}\n"
        try:
            with open(os.path.join(agent_dir, "AGENTS.md"), "w") as f:
                f.write(agents_md)
        except OSError:
            pass

        # Write MEMORY.md placeholder
        try:
            memory_dir = os.path.join(agent_dir, "memory")
            os.makedirs(memory_dir, exist_ok=True)
            with open(os.path.join(memory_dir, "MEMORY.md"), "w") as f:
                f.write(f"# {name} Memory\n\n*Work in progress.*\n")
        except OSError:
            pass

        # Update squad_config.json (persistent copy)
        peers[name] = {"id": f"squad:{name}", "gateway_port": gw, "ws_port": ws}
        squad_cfg["peers"] = peers

        # Sync roster first (gatekeeper reads from disk, so save first)
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 配置已创建 (端口 {gw}/{ws})，但 squad_config 更新失败: {e}。请手动添加 peer 或重启后用 /reset-setup 重建。"},
                status_code=201
            )

        try:
            _sync_roster(gatekeeper, squad_cfg)
        except Exception as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 已保存但侧边栏同步失败: {e}，重启空间后生效"},
                status_code=201
            )

        return JSONResponse(
            {"ok": True, "msg": f"Agent '{name}' 已添加 (网关: {gw}, WS: {ws}, provider: {provider_id})，重启空间后生效"},
            status_code=201
        )

    async def _remove_agent(request: Request):
        """POST /config/agents/remove — delete a worker agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})

        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不存在"}, status_code=404)

        info = peers[name]
        if info.get("id") == "squad:commander":
            return JSONResponse(
                {"ok": False, "error": f"'{name}' 是 Commander，不可删除"},
                status_code=403,
            )

        # Set zone=archived instead of deleting from peers
        info["zone"] = "archived"
        squad_cfg["peers"] = peers

        # Remove from resurrection whitelist — archived agents shouldn't auto-resurrect
        whitelist = squad_cfg.get("resurrection_whitelist", [])
        if name in whitelist:
            whitelist.remove(name)
            squad_cfg["resurrection_whitelist"] = whitelist
            if name != "neo":
                print(f"[agent_config] 🧹 '{name}' 已从复活白名单移除", flush=True)

        # Persist squad_config.json
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": False, "error": f"squad_config 更新失败: {e}"},
                status_code=500,
            )

        # Archive agent directory — kill process first to release file handles
        data_root = squad_cfg.get("data_root", "/data")
        agent_dir = os.path.join(data_root, "instances", name)

        gw_port = info.get("gateway_port", 0)
        if gw_port:
            _kill_agent_process(gw_port)
            # Wait up to 5s for process to exit (scan /proc directly by port)
            port_bytes = str(gw_port).encode()
            for attempt in range(50):
                alive = False
                try:
                    for pid_dir in os.listdir("/proc"):
                        if not pid_dir.isdigit():
                            continue
                        try:
                            with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                                if port_bytes in f.read():
                                    alive = True
                                    break
                        except (OSError, PermissionError):
                            pass
                except OSError:
                    pass
                if not alive:
                    break
                time.sleep(0.1)

        if os.path.exists(agent_dir):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_dir = os.path.join(data_root, "instances", f"{name}.removed.{ts}")
            moved = False
            try:
                shutil.move(agent_dir, archived_dir)
                moved = True
            except OSError:
                # If move fails (e.g. cross-device), try copy + delete
                try:
                    shutil.copytree(agent_dir, archived_dir)
                    shutil.rmtree(agent_dir)
                    moved = True
                except OSError as e2:
                    print(f"[agent_config] ⚠️  归档 '{name}' 目录失败: {e2}，已设置 zone=archived", flush=True)
            if moved:
                print(f"[agent_config] 📦 Agent '{name}' 已归档 → {archived_dir}", flush=True)
        else:
            print(f"[agent_config] ⚠️  Agent '{name}' 目录不存在: {agent_dir}", flush=True)

        _sync_roster(gatekeeper, squad_cfg)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已删除。配置已归档，WebUI 侧边栏已更新。",
        })

    async def _restore_agent(request: Request):
        """POST /config/agents/restore — restore an archived agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        dir_name = (body.get("dir_name", "") or "").strip()

        if not name or not dir_name:
            return JSONResponse({"ok": False, "error": "缺少 name 或 dir_name"}, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        instances_dir = os.path.join(data_root, "instances")
        src_dir = os.path.join(instances_dir, dir_name)
        dst_dir = os.path.join(instances_dir, name)

        # Security: ensure dir_name matches the pattern name.removed.*
        if not dir_name.startswith(name + ".removed."):
            return JSONResponse({"ok": False, "error": f"dir_name '{dir_name}' 与 name '{name}' 不匹配"}, status_code=400)

        if not os.path.isdir(src_dir):
            return JSONResponse({"ok": False, "error": f"归档目录 '{dir_name}' 不存在"}, status_code=404)

        if os.path.exists(dst_dir):
            # Empty shell (no config.json) — some startup process may recreate
            # bare directories.  Clean it up and proceed rather than refusing.
            cfg_path = os.path.join(dst_dir, "config.json")
            if os.path.isdir(dst_dir) and not os.path.isfile(cfg_path):
                shutil.rmtree(dst_dir)
            else:
                peers_check = squad_cfg.get("peers", {})
                if name not in peers_check:
                    # Orphan directory (not in roster) — auto-discard and proceed.
                    # The agent was removed from peers but its directory was left behind.
                    discarded_base = os.path.join(instances_dir, ".discarded")
                    os.makedirs(discarded_base, exist_ok=True)
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    try:
                        shutil.move(dst_dir, os.path.join(discarded_base, f"{name}.orphan.{ts}"))
                    except OSError:
                        pass  # best-effort, fall through to restore
                else:
                    # ── genuine conflict: agent IS in roster ─────────────
                    existing = _read_config_summary(instances_dir, name)
                    archived = _read_config_summary(instances_dir, dir_name)
                    if not existing:
                        return JSONResponse({
                            "ok": False,
                            "error": f"Agent '{name}' 目录已存在但无法读取配置，请手动检查 {dst_dir}"
                        }, status_code=500)
                    if not archived:
                        return JSONResponse({
                            "ok": False,
                            "error": f"归档目录 '{dir_name}' 中无有效 config.json"
                        }, status_code=500)
                    return JSONResponse({
                        "ok": False,
                        "conflict": True,
                        "existing": existing,
                        "archived": archived,
                    })

        peers = squad_cfg.get("peers", {})
        if name in peers and peers[name].get("zone", "active") != "archived":
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已在活跃列表中"}, status_code=409)

        # Read archived config for port info
        gw = 0
        ws = 0
        config_path = os.path.join(src_dir, "config.json")
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            gateway = cfg.get("gateway", {})
            if isinstance(gateway, dict):
                gw = gateway.get("port", 0)
            channels = cfg.get("channels", {})
            ws_cfg = channels.get("websocket", {}) if isinstance(channels, dict) else {}
            ws = ws_cfg.get("port", 0) if isinstance(ws_cfg, dict) else 0
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # Rename directory back
        try:
            shutil.move(src_dir, dst_dir)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"恢复目录失败: {e}"}, status_code=500)

        # Add back to peers — check archived ports for conflicts
        if isinstance(gw, (int, float)) and gw > 0 and isinstance(ws, (int, float)) and ws > 0:
            gw_int, ws_int = int(gw), int(ws)
            # Collect all ports currently claimed by peers + actually listening
            used_ports = set()
            for info in peers.values():
                for k in ("gateway_port", "ws_port"):
                    v = info.get(k, 0)
                    if isinstance(v, (int, float)) and v > 0:
                        used_ports.add(int(v))
            used_ports |= _get_listening_ports()
            if gw_int in used_ports or ws_int in used_ports:
                gw_int, ws_int = _allocate_ports(peers)
                # Also update the agent's own config.json with re-allocated ports
                _patch_agent_config_port(dst_dir, gw_int, ws_int)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw_int, "ws_port": ws_int, "zone": "active"}
        else:
            gw, ws = _allocate_ports(peers)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw, "ws_port": ws, "zone": "active"}

        # Add to resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name not in whitelist:
            whitelist.append(name)
        squad_cfg["resurrection_whitelist"] = whitelist
        squad_cfg["peers"] = peers

        # Persist
        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse(
                {"ok": True, "msg": f"Agent '{name}' 目录已恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），但 squad_config 更新失败: {e}。"},
                status_code=201
            )

        _sync_roster(gatekeeper, squad_cfg)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），已加入复活白名单。WebUI 侧边栏已更新。",
        })

    async def _restore_agent_confirm(request: Request):
        """POST /config/agents/restore-confirm — resolve a restore conflict."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        dir_name = (body.get("dir_name", "") or "").strip()
        choice = (body.get("choice", "") or "").strip()

        if not name or not dir_name or choice not in ("keep", "take"):
            return JSONResponse({
                "ok": False, "error": "缺少 name/dir_name/choice（choice 须为 keep 或 take）"
            }, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        instances_dir = os.path.join(data_root, "instances")
        src_dir = os.path.join(instances_dir, dir_name)
        dst_dir = os.path.join(instances_dir, name)

        if not dir_name.startswith(name + ".removed."):
            return JSONResponse({"ok": False, "error": f"dir_name 与 name 不匹配"}, status_code=400)
        if not os.path.isdir(src_dir):
            return JSONResponse({"ok": False, "error": f"归档目录 '{dir_name}' 不存在"}, status_code=404)

        discarded_base = os.path.join(instances_dir, ".discarded")
        os.makedirs(discarded_base, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        if choice == "keep":
            dst = os.path.join(discarded_base, f"{dir_name}")
            try:
                shutil.move(src_dir, dst)
                return JSONResponse({
                    "ok": True,
                    "msg": f"已保留现有 Agent '{name}'，归档 '{dir_name}' 已移至 .discarded/"
                })
            except OSError as e:
                return JSONResponse({
                    "ok": False, "error": f"移动归档失败: {e}"
                }, status_code=500)

        discarded_dst = os.path.join(discarded_base, f"{name}.{ts}")
        try:
            shutil.move(dst_dir, discarded_dst)
        except OSError as e:
            return JSONResponse({
                "ok": False, "error": f"备份现有目录失败: {e}"
            }, status_code=500)

        try:
            shutil.move(src_dir, dst_dir)
        except OSError as e:
            try:
                shutil.move(discarded_dst, dst_dir)
            except OSError:
                pass
            return JSONResponse({
                "ok": False, "error": f"恢复归档失败: {e}"
            }, status_code=500)

        gw = 0
        ws = 0
        config_path = os.path.join(dst_dir, "config.json")
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            gateway = cfg.get("gateway", {})
            if isinstance(gateway, dict):
                gw = gateway.get("port", 0)
            channels = cfg.get("channels", {})
            ws_cfg = channels.get("websocket", {}) if isinstance(channels, dict) else {}
            ws = ws_cfg.get("port", 0) if isinstance(ws_cfg, dict) else 0
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        peers = squad_cfg.get("peers", {})
        if name in peers and peers[name].get("zone", "active") != "archived":
            return JSONResponse({
                "ok": False,
                "error": f"Agent '{name}' 已在活跃列表中（可能已恢复）"
            }, status_code=409)

        if isinstance(gw, (int, float)) and gw > 0 and isinstance(ws, (int, float)) and ws > 0:
            gw_int, ws_int = int(gw), int(ws)
            used_ports = set()
            for info in peers.values():
                for k in ("gateway_port", "ws_port"):
                    v = info.get(k, 0)
                    if isinstance(v, (int, float)) and v > 0:
                        used_ports.add(int(v))
            used_ports |= _get_listening_ports()
            if gw_int in used_ports or ws_int in used_ports:
                gw_int, ws_int = _allocate_ports(peers)
                _patch_agent_config_port(dst_dir, gw_int, ws_int)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw_int, "ws_port": ws_int, "zone": "active"}
        else:
            gw, ws = _allocate_ports(peers)
            peers[name] = {"id": f"squad:{name}", "gateway_port": gw, "ws_port": ws, "zone": "active"}

        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name not in whitelist:
            whitelist.append(name)
        squad_cfg["resurrection_whitelist"] = whitelist
        squad_cfg["peers"] = peers

        try:
            save_config(squad_cfg)
        except OSError as e:
            return JSONResponse({
                "ok": True,
                "msg": f"Agent '{name}' 已恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），现有版本已移至 .discarded/。squad_config 更新失败: {e}"
            }, status_code=201)

        _sync_roster(gatekeeper, squad_cfg)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已从归档恢复（端口 {peers[name]['gateway_port']}/{peers[name]['ws_port']}），已加入复活白名单。现有版本已移至 .discarded/{name}.{ts}。"
        })

    async def _delete_permanent_agent(request: Request):
        """POST /config/agents/delete-permanent — permanently delete an archived agent."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        dir_name = (body.get("dir_name", "") or "").strip()

        if not name or not dir_name:
            return JSONResponse({"ok": False, "error": "缺少 name 或 dir_name"}, status_code=400)

        # Security: ensure dir_name matches the pattern name.removed.*
        if not dir_name.startswith(name + ".removed."):
            return JSONResponse({"ok": False, "error": f"dir_name '{dir_name}' 与 name '{name}' 不匹配"}, status_code=400)

        squad_cfg = load_config()
        data_root = squad_cfg.get("data_root", "/data")
        dir_path = os.path.join(data_root, "instances", dir_name)

        if not os.path.isdir(dir_path):
            return JSONResponse({"ok": False, "error": f"归档目录 '{dir_name}' 不存在"}, status_code=404)

        # Remove from resurrection whitelist if present
        whitelist = list(squad_cfg.get("resurrection_whitelist", []))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else []
        if name in whitelist:
            whitelist.remove(name)
            squad_cfg["resurrection_whitelist"] = whitelist
            try:
                save_config(squad_cfg)
            except OSError:
                pass

        # Permanently delete
        try:
            shutil.rmtree(dir_path)
        except OSError as e:
            return JSONResponse({"ok": False, "error": f"删除目录失败: {e}"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已永久删除。",
        })

    async def _start_agent(request: Request):
        """POST /config/agents/start — add to whitelist + spawn agent process."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "缺少 name"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "Commander (neo) 只能由 gatekeeper 管理"}, status_code=403)

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})
        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不在 peers 列表中"}, status_code=404)

        data_root = squad_cfg.get("data_root", "/data")
        mount_path = data_root  # In Staging, data_root == /data (/data → /mnt/workspace for MS)

        config_path = os.path.join(mount_path, "instances", name, "config.json")
        print(f"🔍 [agent_config/start] data_root={data_root} config_path={config_path}", flush=True)
        # Diagnostic: show social channel state
        try:
            with open(config_path) as _df:
                _dc = json.load(_df)
            for _ch_name in ("qq", "weixin", "feishu", "dingtalk"):
                _ch = _dc.get("channels", {}).get(_ch_name, {})
                _acct = os.path.join(mount_path, "instances", name, "channels", _ch_name, "account.json")
                print(f"🔍 [agent_config/start] {_ch_name}: enabled={_ch.get('enabled')} account.json={'✓' if os.path.exists(_acct) else '✗'}", flush=True)
        except Exception:
            pass
        workspace_path = os.path.join(mount_path, "instances", name, "workspace")
        channel_dir = os.path.join(mount_path, "instances", name, "channels")
        log_dir = os.path.join(mount_path, "instances", name, "workspace", "logs")

        if not os.path.exists(config_path):
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 的 config.json 不存在"}, status_code=404)

        # Parse port from config
        try:
            with open(config_path) as f:
                cfg = json.load(f)
            gw_port = cfg.get("gateway", {}).get("port", 0)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return JSONResponse({"ok": False, "error": f"无法读取配置: {e}"}, status_code=500)
        if not gw_port:
            return JSONResponse({"ok": False, "error": "无法从配置中解析 gateway.port"}, status_code=500)

        # Check if already running
        running = _detect_running_agents(squad_cfg)
        if running.get(name):
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 已在运行 (port {gw_port})"}, status_code=409)

        # Prepare directories
        os.makedirs(workspace_path, exist_ok=True)
        os.makedirs(channel_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

        # Spawn agent process (detached)
        env = os.environ.copy()
        env["NANOBOT_ACCOUNT_BASE"] = channel_dir
        log_path = os.path.join(log_dir, f"{name}.log")
        try:
            log_fh = open(log_path, "a")
        except OSError:
            log_fh = subprocess.DEVNULL

        proc = subprocess.Popen(
            [
                "nanobot", "gateway",
                "--config", config_path,
                "--workspace", workspace_path,
                "--port", str(gw_port),
            ],
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,  # Detach from gatekeeper process group
        )

        # Add to resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name not in whitelist:
            whitelist.append(name)
        squad_cfg["resurrection_whitelist"] = whitelist

        try:
            save_config(squad_cfg)
        except OSError:
            pass  # Non-fatal; agent is already running

        return JSONResponse({
            "ok": True,
            "msg": f"Agent '{name}' 已启动 (PID: {proc.pid}, port: {gw_port})，已加入复活白名单。",
            "pid": proc.pid,
        })

    async def _stop_agent(request: Request):
        """POST /config/agents/stop — kill agent process + remove from whitelist."""
        _user = request.session.get("user")
        if not _user:
            return JSONResponse({"ok": False, "error": "请先登录"}, status_code=401)
        if not gatekeeper._platform.is_commander(_user):
            return JSONResponse({"ok": False, "error": "仅 Commander 可操作"}, status_code=403)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效的请求格式"}, status_code=400)

        name = (body.get("name", "") or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "缺少 name"}, status_code=400)
        if name == "neo":
            return JSONResponse({"ok": False, "error": "Commander (neo) 不可停止"}, status_code=403)

        squad_cfg = load_config()
        peers = squad_cfg.get("peers", {})
        if name not in peers:
            return JSONResponse({"ok": False, "error": f"Agent '{name}' 不在 peers 列表中"}, status_code=404)

        gw_port = peers[name].get("gateway_port", 0)
        if not gw_port:
            return JSONResponse({"ok": False, "error": "无法确定 agent 端口"}, status_code=500)

        killed = _kill_agent_process(gw_port)

        # Remove from resurrection whitelist
        whitelist = list(squad_cfg.get("resurrection_whitelist", ["neo"]))
        if not isinstance(whitelist, list):
            whitelist = list(whitelist) if hasattr(whitelist, '__iter__') else ["neo"]
        if name in whitelist:
            whitelist.remove(name)
        squad_cfg["resurrection_whitelist"] = whitelist

        try:
            save_config(squad_cfg)
        except OSError:
            pass

        if killed:
            return JSONResponse({
                "ok": True,
                "msg": f"Agent '{name}' 已停止 (PID: {killed})，已移出复活白名单。",
            })
        else:
            return JSONResponse({
                "ok": True,
                "msg": f"未找到 Agent '{name}' (port {gw_port}) 的运行进程。已移出复活白名单。",
            })

    from .agent_handlers import agent_detail as _agent_detail

    from .agent_handlers import save_agent_detail as _save_agent_detail

    # Mount routes
    app.get("/config/agents")(_agent_page)
    app.post("/config/agents/add")(_add_agent)
    app.post("/config/agents/remove")(_remove_agent)
    app.post("/config/agents/restore")(_restore_agent)
    app.post("/config/agents/restore-confirm")(_restore_agent_confirm)
    app.post("/config/agents/delete-permanent")(_delete_permanent_agent)
    app.post("/config/agents/start")(_start_agent)
    app.post("/config/agents/stop")(_stop_agent)
    app.get("/config/agents/{name}")(_agent_detail)
    app.post("/config/agents/{name}/save")(_save_agent_detail)

_DENIED = "<h3 style='text-align:center;margin-top:60px;color:#e74c3c;'>🔒 仅 Commander 可访问此管理页面</h3>"
