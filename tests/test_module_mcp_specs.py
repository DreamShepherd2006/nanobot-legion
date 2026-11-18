"""MCP 声明聚合：模块可选钩子 ``mcp_specs(ctx)``（契约 sdk=1 的可选能力）。

覆盖点：
- 有钩子 → 用钩子声明的 spec
- 无钩子 → 跳过；只有「完全没有任何模块声明钩子」时才回退旧 quant import
- 重名 / 返回值非法 / 单模块抛错 → 隔离并可见
- 模块发现只调用一次（sync 会按 agent 多次注入）
"""

import json
import types

import pytest

from nanobot_legion import module_loader
from nanobot_legion import squad_config_sync as sync


class Spec:
    def __init__(self, name, targets=("neo",)):
        self.name = name
        self.command = "python3"
        self.args = ["-m", name]
        self.target_agents = list(targets)
        # 与真实 MCPSpec 同形的可选字段（_resolve_mcp_env 会读 env*）
        self.env = {}
        self.env_provider_keys = {}
        self.env_provider_model_keys = {}
        self.tool_timeout = None


def _mod(name, hook=None):
    obj = types.SimpleNamespace(name=name)
    if hook is not None:
        obj.mcp_specs = hook
    return types.SimpleNamespace(name=name, obj=obj)


@pytest.fixture
def logs(monkeypatch):
    captured: list[str] = []
    monkeypatch.setattr(sync, "_log", captured.append)
    return captured


def test_hook_specs_are_collected(logs):
    mod = _mod("demo", lambda ctx: {"demo-tool": Spec("demo-tool")})
    specs = sync._collect_mcp_specs(modules=[mod], legacy_discover=None)
    assert set(specs) == {"demo-tool"}
    assert any("MCP spec 来源" in line and "demo(1)" in line for line in logs)


def test_module_without_hook_is_skipped_and_logged(logs):
    specs = sync._collect_mcp_specs(modules=[_mod("plain")], legacy_discover=None)
    assert specs == {}
    assert any("跳过 MCP 注入" in line for line in logs)


def test_legacy_fallback_only_when_no_module_declares_hook(logs):
    calls = []

    def legacy():
        calls.append(1)
        return {"old": Spec("old")}

    specs = sync._collect_mcp_specs(modules=[_mod("plain")], legacy_discover=legacy)
    assert set(specs) == {"old"}
    assert calls == [1]
    assert any("兼容路径" in line for line in logs)


def test_hook_present_blocks_legacy_fallback(logs):
    calls = []

    def legacy():
        calls.append(1)
        return {"old": Spec("old")}

    mod = _mod("demo", lambda ctx: {})
    specs = sync._collect_mcp_specs(modules=[mod], legacy_discover=legacy)
    assert specs == {}
    assert calls == []  # 有模块声明了钩子 → 不回退


def test_duplicate_names_first_wins(logs):
    a = _mod("a", lambda ctx: {"dup": Spec("dup")})
    b = _mod("b", lambda ctx: {"dup": Spec("dup")})
    specs = sync._collect_mcp_specs(modules=[a, b], legacy_discover=None)
    assert set(specs) == {"dup"}
    assert any("重名" in line for line in logs)


def test_hook_exception_is_isolated(logs):
    def boom(ctx):
        raise RuntimeError("坏了")

    good = _mod("good", lambda ctx: {"ok": Spec("ok")})
    specs = sync._collect_mcp_specs(modules=[_mod("bad", boom), good], legacy_discover=None)
    assert set(specs) == {"ok"}
    assert any("mcp_specs 失败" in line for line in logs)


def test_non_dict_return_is_ignored(logs):
    mod = _mod("demo", lambda ctx: ["not-a-dict"])
    assert sync._collect_mcp_specs(modules=[mod], legacy_discover=None) == {}
    assert any("不是 dict" in line for line in logs)


def test_key_name_mismatch_is_ignored(logs):
    mod = _mod("demo", lambda ctx: {"alias": Spec("real-name")})
    assert sync._collect_mcp_specs(modules=[mod], legacy_discover=None) == {}
    assert any("不一致" in line for line in logs)


def test_module_discovery_is_cached(monkeypatch, logs):
    calls = []

    def fake_discover(log=None):
        calls.append(1)
        return [_mod("demo", lambda ctx: {"x": Spec("x")})]

    monkeypatch.setattr(module_loader, "discover_modules", fake_discover)
    monkeypatch.setattr(sync, "_SYNC_MODULES", None)

    sync._modules_for_sync()
    sync._modules_for_sync()
    sync._collect_mcp_specs()

    assert calls == [1]


def test_inject_accepts_precollected_specs(logs, monkeypatch, tmp_path):
    """specs 由调用方传入时不再重复采集（避免每实例重复打印来源行）。"""

    def boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("specs 已传入时不应再采集")

    monkeypatch.setattr(sync, "_collect_mcp_specs", boom)

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}")
    changed = sync.inject_mcp_from_specs(str(cfg_path), "neo", specs={"demo": Spec("demo")})

    assert changed is True
    assert not any("MCP spec 来源" in line for line in logs)
    written = json.loads(cfg_path.read_text())
    assert "demo" in written["tools"]["mcp_servers"]


def test_sync_collects_specs_once_per_run(logs, monkeypatch, tmp_path):
    """整轮 sync 只采集一次 spec：N 个实例共用同一批，来源日志只出现一次。"""
    for name in ("neo", "quant", "trinity.removed.20260101"):
        inst = tmp_path / name
        inst.mkdir()
        (inst / "config.json").write_text("{}")

    monkeypatch.setattr(sync, "INSTANCES_ROOT", str(tmp_path))
    monkeypatch.setattr(sync, "TEMPLATE", str(tmp_path / "_missing_template.json"))
    monkeypatch.setattr(
        sync,
        "_parse_squad",
        lambda: {
            "neo": {"gateway_port": 18790, "ws_port": 18791},
            "quant": {"gateway_port": 18810, "ws_port": 18811},
        },
    )

    calls: list[int] = []

    def collect(*a, **k):
        calls.append(1)
        logs.append("     🔌 MCP spec 来源：demo(1)")
        return {"demo": Spec("demo", targets=("neo", "quant"))}

    monkeypatch.setattr(sync, "_collect_mcp_specs", collect)

    sync.sync_configs()

    assert len(calls) == 1, "整轮 sync 应只采集一次 spec"
    assert sum("MCP spec 来源" in line for line in logs) == 1
    for name in ("neo", "quant"):
        cfg = json.loads((tmp_path / name / "config.json").read_text())
        assert "demo" in cfg["tools"]["mcp_servers"], f"{name} 应复用同一批 spec"


def test_real_quant_hook_yields_three_servers():
    """装了 nanobot-quant（新版本实现钩子）时，聚合应得到 3 张 spec。"""
    pytest.importorskip("nanobot_quant.legion_plugin")
    from nanobot_quant.legion_plugin import MODULE

    if not callable(getattr(MODULE, "mcp_specs", None)):
        pytest.skip("当前安装的 nanobot-quant 尚未实现 mcp_specs（旧版本）")

    specs = sync._collect_mcp_specs(modules=[_mod("quant", MODULE.mcp_specs)], legacy_discover=None)
    assert set(specs) == {"vibe-trading", "squad-delegate", "signal-structurizer"}
