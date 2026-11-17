"""C147：「📦 开源代码」表自动列出已安装的 Legion Module。

要求：装了什么模块就显示什么行（不维护第二份组件清单），
失败只跳过该行并打原因，不影响配置中心整体生成。
"""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace

from nanobot_legion import module_loader, pkg_info


def _ep(name="quant", dist_name="nanobot-quant", version="1.0.0", dist=True):
    if not dist:
        return SimpleNamespace(name=name, value="x:y", dist=None)
    return SimpleNamespace(
        name=name,
        value="x:y",
        dist=SimpleNamespace(metadata={"Name": dist_name}, version=version),
    )


def _patch_eps(monkeypatch, eps):
    monkeypatch.setattr(module_loader, "_iter_entry_points", lambda: list(eps))


def _patch_link(monkeypatch, record=None):
    def _build(info, default_repo, default_branch=""):
        if record is not None:
            record.append((info, default_repo))
        repo = (info or {}).get("repo") or default_repo
        return f"[{repo}](https://github.com/{repo})"

    monkeypatch.setattr(pkg_info, "build_source_link", _build)


def test_git_installed_module_row(monkeypatch):
    _patch_eps(monkeypatch, [_ep()])
    seen = []
    monkeypatch.setattr(pkg_info, "get_package_source", lambda n: {
        "repo": "DreamShepherd2006/nanobot-quant", "revision": "5dfde91", "commit": "5dfde918", "url": ""})
    _patch_link(monkeypatch, seen)

    rows = pkg_info.module_source_rows()

    assert rows == [("nanobot-quant业务系统", "[DreamShepherd2006/nanobot-quant](https://github.com/DreamShepherd2006/nanobot-quant)")]
    # 兜底 repo 也传对了（direct_url 缺失时才会用到）
    assert seen and seen[0][1] == "DreamShepherd2006/nanobot-quant"


def test_pypi_module_degrades_to_version(monkeypatch):
    _patch_eps(monkeypatch, [_ep(dist_name="nanobot-extra", version="0.4.2")])
    monkeypatch.setattr(pkg_info, "get_package_source", lambda n: None)
    _patch_link(monkeypatch)

    assert pkg_info.module_source_rows() == [
        ("nanobot-extra业务系统", "v0.4.2（PyPI 安装，无源码链接）"),
    ]


def test_duplicate_distribution_reported_once(monkeypatch):
    _patch_eps(monkeypatch, [_ep(name="quant"), _ep(name="quant_alias")])
    monkeypatch.setattr(pkg_info, "get_package_source", lambda n: {"repo": "a/b", "revision": "", "commit": "", "url": ""})
    _patch_link(monkeypatch)

    assert len(pkg_info.module_source_rows()) == 1


def test_entry_point_without_dist_is_skipped(monkeypatch, capsys):
    _patch_eps(monkeypatch, [_ep(dist=False)])
    _patch_link(monkeypatch)

    assert pkg_info.module_source_rows() == []
    assert "缺少发行包信息" in capsys.readouterr().err


def test_source_read_exception_skips_row_with_reason(monkeypatch, capsys):
    _patch_eps(monkeypatch, [_ep()])

    def _boom(n):
        raise RuntimeError("direct_url 损坏")

    monkeypatch.setattr(pkg_info, "get_package_source", _boom)
    _patch_link(monkeypatch)

    rows = pkg_info.module_source_rows()
    assert rows == [("nanobot-quant业务系统", "v1.0.0（PyPI 安装，无源码链接）")]
    assert "RuntimeError" in capsys.readouterr().err


def test_enumeration_failure_is_reported(monkeypatch, capsys):
    def _boom():
        raise RuntimeError("entry points 不可用")

    monkeypatch.setattr(module_loader, "_iter_entry_points", _boom)
    _patch_link(monkeypatch)

    assert pkg_info.module_source_rows() == []
    assert "枚举 Legion Module 失败" in capsys.readouterr().err


def test_old_cag_support_is_logged(monkeypatch, capsys):
    monkeypatch.setattr(pkg_info, "get_package_source", None)
    monkeypatch.setattr(pkg_info, "build_source_link", None)

    assert pkg_info.module_source_rows() == []
    assert "未提供 package_source" in capsys.readouterr().err


def test_binding_chat_renders_module_rows_without_hardcoded_list():
    """会话模板不得再写死组件清单，必须走 module_source_rows()。"""
    spec = importlib.util.find_spec("nanobot_legion.gatekeeper_binding_chat")
    src = open(spec.origin, encoding="utf-8").read()
    assert "module_source_rows()" in src
    assert "_source_rows" in src
    # 只保留核心三行的固定文案，模块行不得出现硬编码包名
    assert src.count("| nanobot-quant") == 0
