#!/usr/bin/env python3
"""
Squad Config Loader — 统一配置入口。

优先级：SQUAD_CONFIG_PATH env → {data_root}/squad_config.json → /app/squad_config.json (seed)
Legion 模式下 data_root = {platform}/legion，与单 agent 的 instances/ 完全隔离。
"""
import json, os

_SEED_PATH = "/app/squad_config.json"
_config_cache: dict | None = None


def _get_config_path() -> str:
    """确定 squad_config.json 路径。
    
    优先级：SQUAD_CONFIG_PATH env → {data_root}/squad_config.json → seed (/app/)
    data_root 从 seed 文件读取（仅用于探测路径，不加载完整配置）。
    """
    env_path = os.environ.get("SQUAD_CONFIG_PATH", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    # 从 seed 文件读取 data_root，构造持久化路径
    data_root = "/data"
    if os.path.exists(_SEED_PATH):
        try:
            with open(_SEED_PATH) as f:
                seed = json.load(f)
            data_root = seed.get("data_root", "/data")
        except (json.JSONDecodeError, IOError):
            pass
    # 也允许 env 覆盖 data_root
    data_root = os.environ.get("DATA_ROOT", data_root)
    # legion isolation: persistent file moved to {data_root}/legion/
    legion_path = os.path.join(data_root, "legion", "squad_config.json")
    if os.path.exists(legion_path):
        return legion_path
    # fallback: pre-isolation root-level path
    persistent_path = os.path.join(data_root, "squad_config.json")
    if os.path.exists(persistent_path):
        return persistent_path
    return _SEED_PATH

# ── 默认值（文件不存在时兜底） ────────────────────────────

_DEFAULTS: dict = {
    "webui_agent": "neo",
    "owner": "",
    "commander_whitelist": [],
    "user_agent_map": {},
    "squad_relay_token": "",
    "data_root": "/data",
    "relay_timeout": 60,
    "gatekeeper_port": 7860,
    "dlq_dir": "/data/dlq",
    "deploy_platform": "",
    "peers": {},
}


def load_config(force_reload: bool = False) -> dict:
    """加载 squad_config.json → 返回完整配置 dict。缓存结果。"""
    global _config_cache
    if _config_cache is not None and not force_reload:
        return _config_cache

    config = dict(_DEFAULTS)

    # 1. 文件优先（动态路径：持久化 > seed）
    config_path = _get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
            config.update(file_cfg)
        except (json.JSONDecodeError, IOError):
            pass

    # 2. env fallback（仅当文件中未定义时）
    _env_override(config)

    _config_cache = config
    return config


def _env_override(config: dict):
    """用环境变量覆盖 config 中缺失的项。"""
    v = os.environ.get("WEBUI_AGENT", "").strip().lower()
    if v:
        config["webui_agent"] = v

    v = os.environ.get("RELAY_TIMEOUT", "")
    if v:
        try:
            config["relay_timeout"] = int(v)
        except ValueError:
            pass

    v = os.environ.get("DLQ_DIR", "")
    if v:
        config["dlq_dir"] = v

    # 密钥类 — env fallback，但如果 json 已有值则保留 json（WebUI 可覆盖）
    for secret_key in ["SQUAD_RELAY_TOKEN", "SESSION_SECRET"]:
        field = secret_key.lower()
        if config.get(field):
            continue  # json 已有值，不覆盖
        v = os.environ.get(secret_key, "")
        if v:
            config[field] = v

    # Peers — 只从 squad_config.json 读取，不再从 NANOBOT_PEER_* env 合并
    # （env 合并会丢失 zone 等字段，导致 zone 过滤失效）


def save_config(config: dict | None = None) -> None:
    """原子写入 squad_config.json。先写临时文件，再 os.replace（POSIX 原子 rename）。

    不传 config 时默认写当前 _config_cache。
    """
    global _config_cache
    if config is None:
        config = _config_cache
    if config is None:
        config = load_config()
    path = _get_config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    _config_cache = config


# ── 便捷存取 ────────────────────────────────────────────

def get_peers() -> dict:
    """返回 peers dict: {name: {id, gateway_port, ws_port}}"""
    return load_config().get("peers", {})


def get_webui_agent() -> str:
    return load_config().get("webui_agent", "neo")


def get_commander_whitelist() -> list[str]:
    return load_config().get("commander_whitelist", [])


def get_user_agent_map() -> dict:
    return load_config().get("user_agent_map", {})


def get_relay_timeout() -> int:
    return load_config().get("relay_timeout", 60)


def get_relay_token() -> str:
    """获取 squad relay token。优先 squad_config.json，其次 env SQUAD_RELAY_TOKEN。"""
    return str(load_config().get("squad_relay_token", "") or "")


def get_resurrection_whitelist() -> set:
    """Return the set of agent names that are allowed auto-resurrection."""
    whitelist = load_config().get("resurrection_whitelist", ["neo"])
    return set(whitelist) if isinstance(whitelist, list) else {"neo"}


def get_deploy_platform() -> str:
    return (load_config().get("deploy_platform") or os.environ.get("DEPLOY_PLATFORM") or "").strip()


def get_dlq_dir() -> str:
    cfg = load_config()
    data_root = cfg.get("data_root", "/data")
    d = cfg.get("dlq_dir", f"{data_root}/dlq")
    os.makedirs(d, exist_ok=True)
    return d


# ── 模块导入时自动设置 DEPLOY_PLATFORM ─────────────────────
# 放在文件末尾，确保 load_config 等函数已定义。
# gatekeeper / entrypoint 在 import squad_config_loader 之后，
# platforms.matches() 会读取 os.environ["DEPLOY_PLATFORM"]。
_cfg = load_config()
_dp = (_cfg.get("deploy_platform") or "").strip()
if _dp:
    os.environ["DEPLOY_PLATFORM"] = _dp
