"""Legion Module 发现/校验/挂载测试（契约 sdk=1）。"""

from __future__ import annotations

import types

import pytest

from nanobot_legion import module_loader as ml
from nanobot_legion import module_registry as mr


# ── 构造工具 ──────────────────────────────────────────────────────


class FakeModule:
    name = "demo"
    display = "🧪 Demo"
    sdk = 1
    route_prefix = "/config"

    def __init__(self, log=None):
        self.registered: list[tuple] = []
        self.sections_calls: list = []
        self.log = log

    def register_routes(self, app, ctx):
        self.registered.append((app, ctx))

    def sections(self, ctx):
        self.sections_calls.append(ctx)
        return []


class FakeEP:
    def __init__(self, name, obj=None, exc=None):
        self.name = name
        self._obj = obj
        self._exc = exc

    def load(self):
        if self._exc:
            raise self._exc
        return self._obj


def logs():
    out: list[str] = []
    return out, out.append


# ── validate_module ───────────────────────────────────────────────


def test_validate_accepts_valid_module():
    assert ml.validate_module(FakeModule()) is None


@pytest.mark.parametrize(
    "attr,value",
    [
        ("name", ""),
        ("display", ""),
        ("sdk", 2),
        ("sdk", "1"),
        ("route_prefix", "config"),
        ("register_routes", None),
        ("sections", None),
    ],
)
def test_validate_rejects_bad_fields(attr, value):
    mod = FakeModule()
    setattr(mod, attr, value)
    assert ml.validate_module(mod) is not None


def test_validate_reports_missing_fields():
    class Empty:
        pass

    err = ml.validate_module(Empty())
    assert err and "缺少契约字段" in err


# ── discover_modules ─────────────────────────────────────────────


def test_discover_loads_modules(monkeypatch):
    mod = FakeModule()
    monkeypatch.setattr(ml, "_iter_entry_points", lambda: [FakeEP("demo", mod)])
    _, log = logs()
    found = ml.discover_modules(log=log)
    assert [m.name for m in found] == ["demo"]
    assert found[0].obj is mod
    assert found[0].route_prefix == "/config"


def test_discover_skips_broken_entry_point(monkeypatch):
    monkeypatch.setattr(
        ml,
        "_iter_entry_points",
        lambda: [FakeEP("bad", exc=RuntimeError("boom")), FakeEP("demo", FakeModule())],
    )
    out, log = logs()
    found = ml.discover_modules(log=log)
    assert [m.name for m in found] == ["demo"]
    assert any("加载失败" in m for m in out)


def test_discover_rejects_incompatible_sdk(monkeypatch):
    mod = FakeModule()
    mod.sdk = 99
    monkeypatch.setattr(ml, "_iter_entry_points", lambda: [FakeEP("demo", mod)])
    out, log = logs()
    assert ml.discover_modules(log=log) == []
    assert any("拒载" in m and "sdk" in m for m in out)


def test_discover_dedupes_same_name(monkeypatch):
    monkeypatch.setattr(
        ml,
        "_iter_entry_points",
        lambda: [FakeEP("a", FakeModule()), FakeEP("b", FakeModule())],
    )
    out, log = logs()
    found = ml.discover_modules(log=log)
    assert len(found) == 1
    assert any("重名" in m for m in out)


def test_discover_falls_back_to_legacy_import(monkeypatch):
    mod = FakeModule()
    monkeypatch.setattr(ml, "_iter_entry_points", lambda: [])

    fake_pkg = types.ModuleType("nanobot_quant.legion_plugin")
    fake_pkg.MODULE = mod
    monkeypatch.setattr(ml.importlib, "import_module", lambda name: fake_pkg)

    out, log = logs()
    found = ml.discover_modules(log=log)
    assert [m.name for m in found] == ["demo"]
    assert any("兜底" in m for m in out)


def test_discover_returns_empty_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(ml, "_iter_entry_points", lambda: [])

    def _boom(name):
        raise ImportError("no module")

    monkeypatch.setattr(ml.importlib, "import_module", _boom)
    out, log = logs()
    assert ml.discover_modules(log=log) == []
    assert any("未发现 Legion Module" in m for m in out)


# ── register_modules ─────────────────────────────────────────────


class FakeGK:
    def __init__(self, data_root):
        self._platform = types.SimpleNamespace(data_root=data_root)


def test_register_modules_passes_context(monkeypatch, tmp_path):
    mod = FakeModule()
    monkeypatch.setattr(mr, "discover_modules", lambda log=None: [ml.LoadedModule(
        name="demo", display="🧪 Demo", sdk=1, route_prefix="/config", obj=mod)])
    out, log = logs()
    app = object()
    result = mr.register_modules(app, FakeGK(str(tmp_path)), log=log)

    assert len(result) == 1
    (app_arg, ctx), = mod.registered
    assert app_arg is app
    assert ctx.module == "demo"
    assert ctx.data_root == str(tmp_path)
    assert ctx.data_dir.endswith("legion/demo")
    assert ctx.credentials_dir.endswith("legion/credentials/demo")
    assert ctx.files_dir.endswith("files/demo")
    import os

    assert os.path.isdir(ctx.data_dir)
    assert any("已挂载" in m for m in out)


def test_register_modules_isolates_failure(monkeypatch, tmp_path):
    class Boom(FakeModule):
        name = "boom"

        def register_routes(self, app, ctx):
            raise RuntimeError("mount fail")

    good = FakeModule()
    monkeypatch.setattr(mr, "discover_modules", lambda log=None: [
        ml.LoadedModule("boom", "💥 Boom", 1, "/apps/boom", Boom()),
        ml.LoadedModule("demo", "🧪 Demo", 1, "/config", good),
    ])
    out, log = logs()
    result = mr.register_modules(object(), FakeGK(str(tmp_path)), log=log)
    assert [m.name for m in result] == ["boom", "demo"]
    assert len(good.registered) == 1
    assert any("页面挂载失败" in m for m in out)


def test_register_modules_warns_on_prefix_conflict(monkeypatch, tmp_path):
    monkeypatch.setattr(mr, "discover_modules", lambda log=None: [
        ml.LoadedModule("a", "A", 1, "/config", FakeModule()),
        ml.LoadedModule("b", "B", 1, "/config", FakeModule()),
    ])
    out, log = logs()
    mr.register_modules(object(), FakeGK(str(tmp_path)), log=log)
    assert any("路由前缀冲突" in m for m in out)


def test_register_modules_empty_when_none(monkeypatch):
    monkeypatch.setattr(mr, "discover_modules", lambda log=None: [])
    out, log = logs()
    assert mr.register_modules(object(), FakeGK("/tmp"), log=log) == []
    assert any("未发现 Legion Module" in m for m in out)


def test_legacy_whitelist_removed():
    """步 4：gatekeeper 不得再直连具体应用的 handler（零硬编码耦合）。"""
    import importlib.util

    spec = importlib.util.find_spec("nanobot_legion.gatekeeper_routes")
    src = open(spec.origin, encoding="utf-8").read()
    assert "_register_legacy_quant_routes" not in src
    assert "from nanobot_quant" not in src
    assert "register_modules(" in src
