#!/usr/bin/env python3
"""
军团端口配置中心 (Squad Config Sync) v4.0
=============================================
模块化设计 — 内存传递替代磁盘中介。

- get_roster() → 返回 dict，供 gatekeeper 直接导入（零磁盘）
- sync_configs() → 写 agent config.json，供 entrypoint.sh 调用
"""

import os, json, glob, sys, shutil
from pathlib import Path
from datetime import datetime
from copy import deepcopy

# 从环境变量/squad_config 读取 data_root（跨平台：HF /data, MS /mnt/workspace）
def _get_data_root():
    """读取 data_root：优先 MOUNT_PATH env，其次 squad_config.json，兜底 /data"""
    mount = os.environ.get('MOUNT_PATH', '')
    if mount:
        return mount
    config_path = os.environ.get('SQUAD_CONFIG_PATH', '/app/squad_config.json')
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg.get('data_root', '/data')
    except Exception:
        return '/data'

DATA_ROOT = _get_data_root()
TEMPLATE = os.path.join(DATA_ROOT, "instances/_template/config.json")
INSTANCES_ROOT = os.path.join(DATA_ROOT, "instances")
print(f"🔍 [sync] DATA_ROOT={DATA_ROOT} INSTANCES_ROOT={INSTANCES_ROOT} MOUNT_PATH={os.environ.get('MOUNT_PATH','')}", flush=True)
MAX_BACKUPS = 5  # keep last N backups per agent

def _log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _backup_config(cfg_path: str, inst_name: str) -> str | None:
    """
    修改前备份: config.json → config.json.backup.{timestamp}
    保留最近 MAX_BACKUPS 份，自动清理旧备份。
    返回备份文件路径，失败返回 None。
    """
    inst_dir = os.path.dirname(cfg_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_name = f"config.json.backup.{ts}"
    bak_path = os.path.join(inst_dir, bak_name)
    try:
        shutil.copy2(cfg_path, bak_path)
        # 清理旧备份：只保留最近 N 份
        all_baks = sorted(
            [f for f in os.listdir(inst_dir) if f.startswith("config.json.backup.")],
            reverse=True,
        )
        for old in all_baks[MAX_BACKUPS:]:
            try:
                os.unlink(os.path.join(inst_dir, old))
            except OSError:
                pass
        return bak_name
    except Exception:
        return None

# ═══ 1. 纯函数：env → squad dict（无副作用） ═══════════════════

def _parse_squad():
    """从 NANOBOT_PEER_* env vars 解析 roster。"""
    squad = {}
    for key in sorted(k for k in os.environ if k.startswith("NANOBOT_PEER_")):
        name = key.replace("NANOBOT_PEER_", "").lower()
        try:
            data = json.loads(os.environ[key])
        except json.JSONDecodeError:
            continue
        gw = data.get("gateway_port")
        ws = data.get("ws_port")
        if not gw or not ws:
            continue
        squad[name] = {
            "id": data.get("id", ""),
            "gateway_port": int(gw),
            "ws_port": int(ws),
        }
    return squad

# ═══ 2. 导出：gatekeeper 内存读取 ═══════════════════════════════

def get_roster():
    """
    供 gatekeeper.py 直接导入 — 全部在内存中，无磁盘 IO。
    返回: (roster: dict, webui_agent: str)
    """
    squad = _parse_squad()
    if not squad:
        return {}, "neo"

    webui_target = os.environ.get("WEBUI_AGENT", "").strip().lower()
    if not webui_target or webui_target not in squad:
        webui_target = "neo"

    return squad, webui_target

# ═══ 2.5 动态 env key 收集 — 运行时注入 allowed_env_keys ═══════

def _build_allowed_env_keys():
    """Collect all dynamic env keys that squad agents should have access to."""
    keys = set()
    for key in os.environ:
        if key.startswith('NANOBOT_PEER_'):
            keys.add(key)
    # Squad operational keys
    # SQUAD_RELAY_TOKEN moved to squad_config.json — gatekeeper + push_tasks read from there.
    keys.update([
        'SQUAD_LEGION',
        'NANOBOT_TOKEN',
        'SQUAD_CONFIG_PATH',
    ])
    return sorted(keys)

# ═══ 3. 导出：entrypoint.sh 调用 — 写 agent config.json ═══════

def sync_configs():
    """
    从 NANOBOT_PEER_* 创建/同步各 agent 的 config.json。
    - 新 agent：从模板 deepcopy → patch 端口 → 写入（模板只在新 agent 时生效）
    - 已有 agent：仅同步 gateway.port + allowed_env_keys（不触碰模板字段）
    无返回值 — 结果直接落盘到 {INSTANCES_ROOT}/{name}/config.json
    """
    _log("🛡️ Squad Config Sync v4.0 — 军团端口配置中心")
    print()

    squad = _parse_squad()
    if not squad:
        _log("❌ 无有效 NANOBOT_PEER_* 变量，退出")
        sys.exit(1)

    # 诊断表
    print(f"  {'Agent':<12} {'gateway':>8} {'ws':>8}")
    print(f"  {'─'*12} {'─'*8} {'─'*8}")
    for name, info in squad.items():
        print(f"  {name:<12} {info['gateway_port']:>8} {info['ws_port']:>8}")
    print()

    # ═══ 动态编制：新 agent 从模板创建 ═══════════════════════
    template = None
    if os.path.isfile(TEMPLATE):
        try:
            template = json.loads(Path(TEMPLATE).read_text())
        except Exception as e:
            _log(f"  ❌ 模板读取失败: {e}")
            template = None

        if template:
            # Load squad_config.json for zone info (NANOBOT_PEER_* env vars don't carry zone)
            squad_cfg_peers = {}
            try:
                sc_path = os.environ.get('SQUAD_CONFIG_PATH', '/app/squad_config.json')
                with open(sc_path) as f:
                    squad_cfg_peers = json.load(f).get("peers", {})
            except Exception:
                pass

            for name, info in squad.items():
                # Skip archived agents — zone=archived means user explicitly removed this agent
                if squad_cfg_peers.get(name, {}).get("zone", "active") != "active":
                    continue

                inst_dir = Path(INSTANCES_ROOT) / name
                cfg_path = inst_dir / "config.json"

                if cfg_path.exists():
                    continue

                _log(f"  🆕 {name}: 新 agent，从模板创建...")
                try:
                    # Clean stale file shadows before mkdir
                    if inst_dir.exists() and not inst_dir.is_dir():
                        _log(f"     ⚠️  清理残留文件: {inst_dir}")
                        inst_dir.unlink()
                    inst_dir.mkdir(parents=True, exist_ok=True)
                    (inst_dir / "workspace").mkdir(exist_ok=True)

                    cfg = deepcopy(template)
                    cfg["gateway"]["port"] = info["gateway_port"]
                    cfg["channels"]["websocket"]["port"] = info["ws_port"]
                    cfg.setdefault("agents", {}).setdefault("defaults", {})["instructions"] = (
                        f"【{name.upper()}】Squad agent {name}. "
                        f"Configure your role via Web UI."
                    )

                    # Inject dynamic allowed_env_keys
                    allowed = _build_allowed_env_keys()
                    cfg.setdefault("tools", {}).setdefault("exec", {})["allowed_env_keys"] = allowed

                    cfg_path.write_text(
                        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8"
                    )
                    _log(f"     ✅ gw={info['gateway_port']} ws={info['ws_port']}")
                except Exception as e:
                    _log(f"     ❌ 创建失败: {e}")
    else:
        _log(f"  ⚠️  模板缺失: {TEMPLATE}")

    # ═══ 同步已有 agent config.json（排除模板自身）══════
    cfg_files = [
        f for f in glob.glob(f"{INSTANCES_ROOT}/*/config.json")
        if os.path.basename(os.path.dirname(f)) != "_template"
    ]

    for cfg_path in sorted(cfg_files):
        inst_name = os.path.basename(os.path.dirname(cfg_path))

        try:
            # Read + backup before any modification
            with open(cfg_path, "r") as f:
                cfg = json.load(f)

            bak = _backup_config(cfg_path, inst_name)

            # --- Squad config sanitization ---
            # Fix corrupted configs that may have root-level keys from hotfix mismatches.
            # Known corruption: "exec" at root level — belongs at tools.exec only.
            _bad_keys_removed = []
            for _bad_key in ["exec", "allowed_env_keys"]:
                if _bad_key in cfg:
                    del cfg[_bad_key]
                    _bad_keys_removed.append(_bad_key)
            if _bad_keys_removed:
                _log(f"   🧹 {inst_name}: cleaned corrupted root keys: {_bad_keys_removed}")
            # --- End sanitization ---

            if inst_name in squad:
                cfg.setdefault("gateway", {})["port"] = squad[inst_name]["gateway_port"]
                cfg.setdefault("channels", {}).setdefault("websocket", {})["port"] = squad[inst_name]["ws_port"]
                cfg["channels"]["websocket"]["enabled"] = True
                # Disable social channels for non-commander agents that lack account.json.
                # Without credentials, their start() autoreload loops block ChannelManager.__init__
                # indefinitely. If account.json exists, the channel is intentionally configured — leave it alone.
                # Read webui_agent from squad_config.json (same file used by gatekeeper).
                try:
                    with open(os.environ.get('SQUAD_CONFIG_PATH', '/app/squad_config.json')) as f:
                        squad_cfg = json.load(f)
                    webui_agent = squad_cfg.get("webui_agent", "neo")
                except Exception:
                    webui_agent = "neo"
                if inst_name != webui_agent:
                    for ch in list(cfg.get("channels", {})):
                        if ch != "websocket":
                            account_file = os.path.join(INSTANCES_ROOT, inst_name, "channels", ch, "account.json")
                            enabled_before = cfg["channels"][ch].get("enabled")
                            if not os.path.exists(account_file):
                                cfg["channels"][ch]["enabled"] = False
                                print(f"🔍 [sync] {inst_name}/{ch}: no account.json → enabled: {enabled_before}→False", flush=True)
                            else:
                                print(f"🔍 [sync] {inst_name}/{ch}: account.json ✓ → enabled: {enabled_before} (unchanged)", flush=True)
                port_mark = f"→ gw={squad[inst_name]['gateway_port']} ws={squad[inst_name]['ws_port']}"
            else:
                port_mark = "(not in roster)"

            # Merge dynamic allowed_env_keys
            allowed = _build_allowed_env_keys()
            tools_cfg = cfg.setdefault("tools", {})
            exec_cfg = tools_cfg.setdefault("exec", {})
            existing = set(exec_cfg.get("allowed_env_keys", []))
            merged = sorted(existing | set(allowed))
            exec_cfg["allowed_env_keys"] = merged

            # ── NOTE: 模板仅用于创建新 agent，不 merge 到已有 agent ──
            # ssrf_whitelist / provider / model 等配置由 Commander 手动管理。
            # 如需批量修改已有 agent，使用专门的运维命令，而非依赖模板自动覆盖。

            tmp_path = cfg_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, cfg_path)

            # Inject MCP configs from installed packages (e.g., nanobot-quant OnchainOS)
            inject_mcp_from_specs(cfg_path)

            _log(f"   ✅ {inst_name}: config 已同步 {port_mark}")
        except Exception as e:
            _log(f"   ❌ {inst_name}: 写入失败 — {e}")

    if not cfg_files:
        _log("   ⚠️  未检测到任何实例 config.json")

    print()
    _log("🏁 完成 — gatekeeper 通过 import 读取 roster（内存）")

# ═══ 4. MCP 配置注入 ══════════════════════════════════════════

def inject_mcp_from_specs(cfg_path: str) -> bool:
    """Inject MCP server configs from nanobot_quant MCP specs."""
    try:
        from nanobot_quant.mcp_spec import discover as _discover_mcp
    except ImportError:
        return False

    _specs = _discover_mcp()
    if not _specs:
        return False

    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False

    _tools = cfg.setdefault("tools", {})
    # Remove stale camelCase key (same reason as CAG's inject_mcp_config)
    _changed = False
    if "mcpServers" in _tools:
        del _tools["mcpServers"]
        _changed = True
    _existing = _tools.setdefault("mcp_servers", {})

    for _name, _spec in _specs.items():
        if _name not in _existing:
            _existing[_name] = {
                "type": "stdio",
                "command": _spec.command,
                "args": _spec.args,
            }
            _changed = True
            _log(f"     🔌 MCP + {_name}")
        else:
            _log(f"     🔌 MCP   {_name} (existing, skipped)")

    if _changed:
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    return _changed


# ═══ CLI 入口（向后兼容） ════════════════════════════════════

if __name__ == "__main__":
    sync_configs()
