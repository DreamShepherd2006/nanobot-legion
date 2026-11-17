"""启动日志里的安装版本号必须显示真实 pin（原先恒为 `@?`）。

根因：pip 把 git 信息写进 ``direct_url.json`` 的 ``vcs_info`` 子对象，
旧实现读的是顶层 ``requested_revision``（永远取不到），且 ``except: pass``
把原因也吞了。现在统一走 ``cloud_agent_gateway.package_source``。
"""

from __future__ import annotations

import importlib
import importlib.metadata

from nanobot_legion import pkg_info


def _fake_source(monkeypatch, info):
    monkeypatch.setattr(pkg_info, "get_package_source", lambda name: info)


def test_tag_revision_returned_as_is(monkeypatch):
    _fake_source(monkeypatch, {"repo": "a/b", "revision": "v0.2.0", "commit": "14b7489138", "url": ""})
    assert pkg_info.installed_rev("nanobot-legion") == "v0.2.0"


def test_short_hash_kept_long_hash_truncated(monkeypatch):
    _fake_source(monkeypatch, {"revision": "f4dde8ac", "commit": "f4dde8ac11", "url": ""})
    assert pkg_info.installed_rev("nanobot-legion") == "f4dde8ac"

    _fake_source(monkeypatch, {"revision": "f4dde8ac1122334455", "commit": "", "url": ""})
    assert pkg_info.installed_rev("nanobot-legion") == "f4dde8a"


def test_falls_back_to_commit_id(monkeypatch):
    _fake_source(monkeypatch, {"revision": "", "commit": "14b7489138aabbccddee", "url": ""})
    assert pkg_info.installed_rev("nanobot-legion") == "14b7489"


def test_pypi_install_reports_version(monkeypatch):
    _fake_source(monkeypatch, None)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    assert pkg_info.installed_rev("nanobot-legion") == "v0.2.0"


def test_missing_package_logs_reason(monkeypatch, capsys):
    _fake_source(monkeypatch, None)

    def _boom(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _boom)
    assert pkg_info.installed_rev("ghost-pkg") == "?"
    err = capsys.readouterr().err
    assert "ghost-pkg" in err and "未安装" in err


def test_source_reader_exception_is_reported(monkeypatch, capsys):
    def _boom(name):
        raise RuntimeError("direct_url 损坏")

    monkeypatch.setattr(pkg_info, "get_package_source", _boom)
    assert pkg_info.installed_rev("nanobot-quant") == "?"
    err = capsys.readouterr().err
    assert "nanobot-quant" in err and "RuntimeError" in err


def test_direct_url_without_revision_logs_reason(monkeypatch, capsys):
    _fake_source(monkeypatch, {"revision": "", "commit": "", "url": "https://example.com/x"})
    assert pkg_info.installed_rev("nanobot-legion") == "?"
    err = capsys.readouterr().err
    assert "缺少 revision/commit" in err


def test_old_cag_without_package_source_falls_back_to_version(monkeypatch, capsys):
    """旧版 CAG 没有 package_source 模块时不能崩，退回包版本并说明原因。"""
    monkeypatch.setattr(pkg_info, "get_package_source", None)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.1.9")
    assert pkg_info.installed_rev("nanobot-legion") == "v0.1.9"
    assert "未提供 package_source" in capsys.readouterr().err


def test_no_private_path_or_silent_except():
    """回归锁：不得再用 ``dist._path`` 手拼路径 + ``except: pass`` 静默吞错。"""
    spec = importlib.util.find_spec("nanobot_legion.pkg_info")
    src = open(spec.origin, encoding="utf-8").read()
    assert "dist._path" not in src
    assert "except Exception:\n        pass" not in src
    assert "get_package_source" in src

    routes = importlib.util.find_spec("nanobot_legion.gatekeeper_routes")
    routes_src = open(routes.origin, encoding="utf-8").read()
    assert "installed_rev" in routes_src
    assert "dist._path" not in routes_src
