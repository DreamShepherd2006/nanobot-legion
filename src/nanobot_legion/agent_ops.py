#!/usr/bin/env python3
"""Agent operations — port allocation, process detection, kill, archive finding.

Extracted from agent_config.py. Pure logic functions with no web framework dependencies.
"""
from __future__ import annotations

import json, os, signal

from .squad_config_loader import save_config


def _get_listening_ports() -> set[int]:
    """Return ports currently in LISTEN state from /proc/net/tcp and /proc/net/tcp6."""
    used = set()
    for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_path) as f:
                for line_no, line in enumerate(f):
                    if line_no == 0:
                        continue  # Skip header
                    parts = line.split()
                    if len(parts) >= 4 and parts[3] == "0A":  # LISTEN
                        local = parts[1].split(":")
                        if len(local) == 2:
                            port = int(local[1], 16)
                            if port > 0:
                                used.add(port)
        except Exception:
            pass
    return used


def _allocate_ports(peers: dict) -> tuple[int, int]:
    """Find next available gateway_port and ws_port.

    Returns (gw, ws) — checks both squad_config.json peers AND
    ports currently in LISTEN state to avoid collisions.
    """
    all_ports = set()
    for info in peers.values():
        gp = info.get("gateway_port", 0)
        wp = info.get("ws_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            all_ports.add(int(gp))
        if isinstance(wp, (int, float)) and wp > 0:
            all_ports.add(int(wp))

    # Merge with ports actually in use
    all_ports |= _get_listening_ports()

    if not all_ports:
        return 18795, 18895

    candidate = max(all_ports) + 1
    for _ in range(1000):  # Safety limit
        gw = candidate
        ws = candidate + 1
        if gw not in all_ports and ws not in all_ports:
            return gw, ws
        candidate += 2

    raise RuntimeError("No available ports found")


def _patch_agent_config_port(instance_dir: str, gw: int, ws: int) -> None:
    """Update gateway.port and channels.websocket.port in agent's config.json."""
    config_path = os.path.join(instance_dir, "config.json")
    if not os.path.exists(config_path):
        return
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        cfg.setdefault("gateway", {})["port"] = gw
        cfg.setdefault("channels", {}).setdefault("websocket", {})["port"] = ws
        with open(config_path, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"[agent_config] 🔧 {os.path.basename(instance_dir)} config.json 端口已更新 → gw={gw} ws={ws}", flush=True)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[agent_config] ⚠️ 无法更新 {os.path.basename(instance_dir)} config.json 端口: {e}", flush=True)


# ── Agent Detection ────────────────────────────────────────

def _detect_running_agents(squad_cfg: dict) -> dict[str, bool]:
    """Check which peers have a running process. Returns {name: True/False}."""
    peers = squad_cfg.get("peers", {})
    if not peers:
        return {}

    # Build port→name map from peers
    port_map: dict[int, str] = {}
    for name, info in peers.items():
        gp = info.get("gateway_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            port_map[int(gp)] = name

    # Scan /proc for processes matching gateway ports
    found_ports: set[int] = set()
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read()
                    for port in port_map:
                        needle = str(port).encode()
                        if needle in cmdline:
                            found_ports.add(port)
            except (OSError, PermissionError):
                pass
    except OSError:
        pass

    running: dict[str, bool] = {}
    for name, info in peers.items():
        gp = info.get("gateway_port", 0)
        if isinstance(gp, (int, float)) and gp > 0:
            running[name] = int(gp) in found_ports
        else:
            running[name] = False

    return running


def _read_agent_metadata(config_path: str) -> tuple[str, str]:
    """Read provider and model from an agent's config.json. Returns ('', '') on failure."""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        agents = cfg.get("agents", {})
        defaults = agents.get("defaults", {}) if isinstance(agents, dict) else {}
        return defaults.get("provider", ""), defaults.get("model", "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return "", ""


def _find_archived_agents(squad_cfg: dict) -> list[dict]:
    """Find archived agents from peers (zone=archived) and orphan .removed.* dirs.

    Tier 1: peers with zone="archived" → matched .removed.* directory.
    Tier 2: orphan .removed.* directories not in peers → write back zone="archived".
    """
    data_root = squad_cfg.get("data_root", "/data")
    peers = squad_cfg.get("peers", {})
    instances_dir = os.path.join(data_root, "instances")

    archived = []
    active_names: set[str] = set()
    archived_peer_names: set[str] = set()

    for name, info in peers.items():
        zone = info.get("zone", "active") if isinstance(info, dict) else "active"
        if zone == "archived":
            archived_peer_names.add(name)
        else:
            active_names.add(name)

    # Scan directory: build .removed.* → name map
    dir_map: dict[str, str] = {}  # agent_name → dir_name
    try:
        for entry in os.listdir(instances_dir):
            if ".removed." not in entry:
                continue
            if not os.path.isdir(os.path.join(instances_dir, entry)):
                continue
            parts = entry.split(".removed.", 1)
            if len(parts) == 2:
                dir_map[parts[0]] = entry
    except OSError:
        pass

    def _build_entry(name: str, entry: str, zone: str = "archived") -> dict:
        config_path = os.path.join(instances_dir, entry, "config.json")
        provider, model = _read_agent_metadata(config_path)
        ts = entry.split(".removed.", 1)[1] if ".removed." in entry else ""
        return {"name": name, "timestamp": ts, "dir_name": entry,
                "provider": provider, "model": model, "zone": zone}

    # Tier 1: peers with zone="archived"
    for name in sorted(archived_peer_names):
        entry = dir_map.get(name)
        if entry:
            archived.append(_build_entry(name, entry, "archived"))
        else:
            # No matching .removed.* dir (e.g. from previous tests) — show with empty metadata
            archived.append({"name": name, "timestamp": "", "dir_name": "",
                            "provider": "", "model": "", "zone": "archived"})

    # Tier 2: orphan .removed.* dirs not in peers → write back zone="archived"
    needs_save = False
    for name in sorted(dir_map):
        if name in archived_peer_names or name in active_names:
            continue
        entry = dir_map[name]
        archived.append(_build_entry(name, entry, "legacy"))
        # Write back to peers
        info = peers.get(name)
        if isinstance(info, dict):
            info["zone"] = "archived"
        else:
            peers[name] = {"id": f"squad:{name}", "gateway_port": 0, "zone": "archived"}
        needs_save = True

    if needs_save:
        squad_cfg["peers"] = peers
        save_config(squad_cfg)

    archived.sort(key=lambda x: x.get("name", ""))
    return archived


def _kill_agent_process(gw_port: int) -> list[int]:
    """Kill all processes listening on a gateway port by scanning /proc/{pid}/cmdline.
    Returns list of killed PIDs."""
    killed = []
    try:
        for pid_dir in os.listdir("/proc"):
            if not pid_dir.isdigit():
                continue
            try:
                with open(f"/proc/{pid_dir}/cmdline", "rb") as f:
                    cmdline = f.read()
                # Match --port N (the port must appear as a standalone arg after --port)
                port_bytes = str(gw_port).encode()
                if b"--port" in cmdline and port_bytes in cmdline:
                    pid = int(pid_dir)
                    os.kill(pid, signal.SIGTERM)
                    killed.append(pid)
            except (OSError, PermissionError, ValueError):
                pass
    except OSError:
        pass
    if killed:
        print(f"[agent_config] 🔫 已 kill (SIGTERM) PIDs: {killed} (port {gw_port})", flush=True)
    return killed


def _sync_roster(gatekeeper, squad_cfg: dict):
    """Refresh gatekeeper roster from squad_config.json peers after config change."""
    gatekeeper._refresh_roster()
    gatekeeper._init_http_pool()
