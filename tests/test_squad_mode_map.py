"""squad-delegate 的交易模式门控：mode_map 配置化 + 缺省不再伪造 quant。

覆盖点：
- squad_config.json 无 mode_map → 用默认映射（quant 空间零回归）
- mode_map = {} → 门控关闭（放行并说明跳过原因）
- mode_map 非 dict / 非法项 → 回退默认并告警
- 未知模式 → 不拦（跳过并说明），不静默
- 命中且目标不匹配 → rejected（风险门保留）
- mode.json 缺失 → 生效模式取 mode_map 首键，且 get_trading_mode 给出 hint
"""

import asyncio
import importlib
import json
import sys
import types

import pytest


# ── 允许在未安装 websocket / mcp 的测试环境导入模块 ──────────────
def _ensure_stub(name: str, attrs: dict) -> None:
    try:
        importlib.import_module(name)
        return
    except Exception:
        pass
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    if "." in name:
        parent, _, child = name.rpartition(".")
        pmod = sys.modules.get(parent)
        if pmod is not None:
            setattr(pmod, child, mod)


class _FakeMCP:
    def __init__(self, *a, **k):
        pass

    def tool(self, *a, **k):
        def deco(fn):
            return fn

        return deco


_ensure_stub("websocket", {"create_connection": None})
_ensure_stub("mcp", {})
_ensure_stub("mcp.server", {})
_ensure_stub("mcp.server.fastmcp", {"FastMCP": _FakeMCP})

sd = importlib.import_module("nanobot_legion.tools.squad_delegate")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 squad_config 与 mode.json 路径。"""
    mode_file = tmp_path / "mode.json"
    monkeypatch.setattr(sd, "_mode_path", lambda: str(mode_file))
    return mode_file


def _cfg(monkeypatch, **extra):
    monkeypatch.setattr(sd, "load_config", lambda: {"data_root": "/data", **extra})


def test_missing_mode_map_uses_default(env, monkeypatch):
    _cfg(monkeypatch)
    assert sd._mode_map() == sd.DEFAULT_MODE_MAP


def test_empty_mode_map_disables_gate(env, monkeypatch):
    _cfg(monkeypatch, mode_map={})
    assert sd._mode_map() == {}


def test_non_dict_mode_map_falls_back_with_warning(env, monkeypatch, capsys):
    _cfg(monkeypatch, mode_map=["quant"])
    assert sd._mode_map() == sd.DEFAULT_MODE_MAP
    assert "mode_map 类型非法" in capsys.readouterr().err


def test_invalid_entries_are_dropped(env, monkeypatch, capsys):
    _cfg(monkeypatch, mode_map={"quant": "quant", 3: "x", "bad": ""})
    assert sd._mode_map() == {"quant": "quant"}
    assert "mode_map 项非法已忽略" in capsys.readouterr().err


def test_read_mode_returns_none_when_missing(env):
    assert sd._read_mode() is None


def test_effective_mode_falls_back_to_first_map_key(env, monkeypatch):
    _cfg(monkeypatch)
    assert sd._effective_mode(sd._mode_map()) == "quant"
    _cfg(monkeypatch, mode_map={})
    assert sd._effective_mode(sd._mode_map()) is None


def test_get_trading_mode_reports_hint_when_unsaved(env, monkeypatch):
    _cfg(monkeypatch)
    out = json.loads(asyncio.run(sd.get_trading_mode()))
    assert out["mode"] == "quant"
    assert out["expected_agent"] == "quant"
    assert out["enforced"] is True
    assert out["saved"] is False
    assert "请在 /config/mode" in out["hint"]


def test_get_trading_mode_saved_has_no_hint(env, monkeypatch):
    _cfg(monkeypatch)
    env.write_text(json.dumps({"mode": "research"}))
    out = json.loads(asyncio.run(sd.get_trading_mode()))
    assert out["mode"] == "research"
    assert out["expected_agent"] == "vt_research"
    assert out["saved"] is True
    assert "hint" not in out


def test_enforce_rejects_mismatch(env, monkeypatch):
    _cfg(monkeypatch)
    env.write_text(json.dumps({"mode": "quant"}))
    out = json.loads(
        asyncio.run(sd.delegate_to_agent("vt_research", "hi", enforce_mode=True))
    )
    assert out["status"] == "rejected"
    assert out["expected_agent"] == "quant"


def test_enforce_skipped_when_mode_map_empty(env, monkeypatch, capsys):
    _cfg(monkeypatch, mode_map={})
    out = json.loads(
        asyncio.run(sd.delegate_to_agent("vt_research", "hi", enforce_mode=True))
    )
    assert out["status"] != "rejected"  # 门控关闭 → 不会以模式不匹配拒绝
    assert "未配置 mode_map" in capsys.readouterr().err


def test_enforce_skipped_on_unknown_mode(env, monkeypatch, capsys):
    _cfg(monkeypatch)
    env.write_text(json.dumps({"mode": "weird"}))
    out = json.loads(
        asyncio.run(sd.delegate_to_agent("anyone", "hi", enforce_mode=True))
    )
    assert out["status"] != "rejected"
    assert "未知模式" in capsys.readouterr().err


def test_run_research_swarm_rejects_non_research(env, monkeypatch):
    _cfg(monkeypatch)
    env.write_text(json.dumps({"mode": "quant"}))
    out = json.loads(asyncio.run(sd.run_research_swarm("BTC")))
    assert out["status"] == "rejected"


def test_run_research_swarm_accepts_research(env, monkeypatch):
    _cfg(monkeypatch)
    env.write_text(json.dumps({"mode": "research"}))
    # 只验证模式门通过：后续需要 WS，mock 掉重活
    monkeypatch.setattr(sd, "_deliver_via_ws", lambda *a, **k: json.dumps({"status": "ok"}))
    out = json.loads(asyncio.run(sd.run_research_swarm("BTC")))
    assert out.get("status") != "rejected"
