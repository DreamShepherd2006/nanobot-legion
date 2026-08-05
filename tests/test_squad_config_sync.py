"""squad_config_sync 通道 bool 简写归一化测试。

回归：quant 的 config.json 曾出现 ``"qq": true`` 简写，sync 调
``.get("enabled")`` 崩溃（'bool' object has no attribute 'get'）。
"""

from __future__ import annotations

import json

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
    cfg = {
        "gateway": {"port": 18792},
        "channels": {
            "websocket": {"port": 18793, "enabled": True},
            "qq": True,
            "weixin": False,
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
