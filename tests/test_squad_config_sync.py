"""squad_config_sync 通道 bool 简写归一化测试。

回归：quant 的 config.json 曾出现 ``"qq": true`` 简写，sync 调
``.get("enabled")`` 崩溃（'bool' object has no attribute 'get'）。
"""

from __future__ import annotations

import json
import sys
import types

from nanobot_legion.squad_config_sync import _normalise_channel_entry


def test_normalise_channel_entry():
    assert _normalise_channel_entry(True) == {"enabled": True}
    assert _normalise_channel_entry(False) == {"enabled": False}
    assert _normalise_channel_entry({"enabled": True}) == {"enabled": True}
    assert _normalise_channel_entry({"enabled": False, "allow_from": ["*"]}) == {
        "enabled": False, "allow_from": ["*"],
    }


def test_sync_handles_bool_channel_shorthand(tmp_path, monkeypatch):
    """已有 agent 的 config.json 含 bool 简写通道 → 归一化写盘，不崩溃。"""
    import nanobot_legion.squad_config_sync as s

    root = tmp_path / "data"
    inst_root = root / "instances"
    (inst_root / "quant").mkdir(parents=True)
    (inst_root / "_template").mkdir(parents=True)

    # 模拟 quant config.json：websocket dict + qq/weixin bool 简写
    # + channels 级行为开关（sendProgress 等，非通道，sync 不得触碰）
    cfg = {
        "gateway": {"port": 18792},
        "channels": {
            "websocket": {"port": 18793, "enabled": True},
            "qq": True,
            "weixin": False,
            "sendProgress": True,
            "showReasoning": 1,
            "extractDocumentText": {"enabled": True},
        },
    }
    (inst_root / "quant" / "config.json").write_text(json.dumps(cfg))

    # squad_config：webui_agent=neo（quant 是 worker，走通道禁用逻辑）
    (tmp_path / "squad_config.json").write_text(json.dumps({
        "webui_agent": "neo",
        "peers": {"quant": {"zone": "active", "gateway_port": 18792, "ws_port": 18793}},
    }))

    monkeypatch.setenv("SQUAD_CONFIG_PATH", str(tmp_path / "squad_config.json"))
    monkeypatch.setenv("MOUNT_PATH", str(root))
    monkeypatch.setenv("NANOBOT_PEER_QUANT",
                       '{"id":"quant","gateway_port":18792,"ws_port":18793}')
    monkeypatch.setattr(s, "DATA_ROOT", str(root))
    monkeypatch.setattr(s, "TEMPLATE", str(inst_root / "_template" / "config.json"))
    monkeypatch.setattr(s, "INSTANCES_ROOT", str(inst_root))

    s.sync_configs()

    out = json.loads((inst_root / "quant" / "config.json").read_text())
    # bool 简写被归一化为 dict；无 account.json → enabled=False
    assert out["channels"]["qq"] == {"enabled": False}
    assert out["channels"]["weixin"] == {"enabled": False}
    assert out["channels"]["websocket"]["port"] == 18793
    # 行为开关/非通道键保持原样（不得被 sync 改写或禁用）
    assert out["channels"]["sendProgress"] is True
    assert out["channels"]["showReasoning"] == 1
    assert out["channels"]["extractDocumentText"] == {"enabled": True}


# ── MCP tool_timeout 注入（2026-09-23）─────────────────────────

def _fake_spec(**kw):
    """minimal MCPSpec duck-type（legion 侧只用属性，不 import quant）。"""
    defaults = dict(name="signal-structurizer", display="Signal Structurizer",
                    command="python3", args=["-m", "x"],
                    target_agents=["quant"], tool_timeout=60,
                    # _resolve_mcp_env 会读这几个字段（ducks-type 要完整）
                    env=None, env_provider_keys=None,
                    env_provider_model_keys=None, env_from_credential=None)
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


def _install_fake_discover(monkeypatch, specs: dict):
    """把 nanobot_quant.mcp_spec.discover 换成假注册表（测试环境未装 quant）。"""
    pkg = types.ModuleType("nanobot_quant")
    pkg.__path__ = []
    mod = types.ModuleType("nanobot_quant.mcp_spec")
    mod.discover = lambda: specs
    monkeypatch.setitem(sys.modules, "nanobot_quant", pkg)
    monkeypatch.setitem(sys.modules, "nanobot_quant.mcp_spec", mod)


def test_inject_mcp_writes_tool_timeout(tmp_path, monkeypatch):
    """新建 entry 要带上 tool_timeout（否则吃上游默认 30s、长任务工具被掐断）。"""
    import nanobot_legion.squad_config_sync as s

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"agents": {"defaults": {}}}))
    _install_fake_discover(monkeypatch, {"signal-structurizer": _fake_spec()})

    assert s.inject_mcp_from_specs(str(cfg_path), "quant") is True
    entry = json.loads(cfg_path.read_text())["tools"]["mcp_servers"]["signal-structurizer"]
    assert entry["tool_timeout"] == 60
    assert entry["type"] == "stdio" and entry["command"] == "python3"


def test_inject_mcp_updates_existing_tool_timeout(tmp_path, monkeypatch):
    """存量 entry（已是默认 30）→ 同步为 spec 值，且返回 True（需重生效）。"""
    import nanobot_legion.squad_config_sync as s

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "agents": {"defaults": {}},
        "tools": {"mcp_servers": {"signal-structurizer": {
            "type": "stdio", "command": "python3", "args": ["-m", "x"],
            "tool_timeout": 30,
        }}},
    }))
    _install_fake_discover(monkeypatch, {"signal-structurizer": _fake_spec()})

    assert s.inject_mcp_from_specs(str(cfg_path), "quant") is True
    entry = json.loads(cfg_path.read_text())["tools"]["mcp_servers"]["signal-structurizer"]
    assert entry["tool_timeout"] == 60


def test_inject_mcp_without_tool_timeout_is_noop(tmp_path, monkeypatch):
    """旧版 spec（无该属性）→ 不写字段、不报错、不算变更（向后兼容）。"""
    import nanobot_legion.squad_config_sync as s

    spec = _fake_spec()
    del spec.tool_timeout
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "agents": {"defaults": {}},
        "tools": {"mcp_servers": {"signal-structurizer": {
            "type": "stdio", "command": "python3", "args": ["-m", "x"],
        }}},
    }))
    _install_fake_discover(monkeypatch, {"signal-structurizer": spec})

    assert s.inject_mcp_from_specs(str(cfg_path), "quant") is False
    entry = json.loads(cfg_path.read_text())["tools"]["mcp_servers"]["signal-structurizer"]
    assert "tool_timeout" not in entry
