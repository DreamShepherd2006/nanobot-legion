"""C152：启动 DIAG 的版本行不再硬编码业务包名。

要求：框架固定两件（nanobot-legion / cloud-agent-gateway），业务系统按
entry point 声明自动追加——纯框架部署没有任何模块行、也不产生
「包未安装」告警噪音；装了模块才多一行。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import nanobot_legion
from nanobot_legion import module_loader, pkg_info

# 不 import gatekeeper_routes：它依赖 cloud_agent_gateway.file_manager（新版 CAG 才有），
# 单测容器里的 CAG 版本可能偏旧——结构断言只需读源码文本。
_ROUTES_SRC = pathlib.Path(nanobot_legion.__file__).parent / "gatekeeper_routes.py"


def _ep(name="quant", dist_name="nanobot-quant", version="1.0.0"):
    return SimpleNamespace(
        name=name,
        value="x:y",
        dist=SimpleNamespace(metadata={"Name": dist_name}, version=version),
    )


def _patch_eps(monkeypatch, eps):
    monkeypatch.setattr(module_loader, "_iter_entry_points", lambda: list(eps))


def test_module_packages_empty_without_modules(monkeypatch, capsys):
    _patch_eps(monkeypatch, [])

    assert pkg_info.module_packages() == []
    # 纯框架部署是正常状态：不枚举、不告警
    assert capsys.readouterr().err.strip() == ""


def test_module_packages_dedup_and_order(monkeypatch):
    _patch_eps(
        monkeypatch,
        [
            _ep(name="quant", dist_name="nanobot-quant"),
            _ep(name="quant-2", dist_name="Nanobot-Quant"),  # 大小写不同即同包
            _ep(name="demo", dist_name="nanobot-demo"),
        ],
    )

    assert pkg_info.module_packages() == ["nanobot-quant", "nanobot-demo"]


def test_module_packages_skips_entry_point_without_dist(monkeypatch, capsys):
    _patch_eps(
        monkeypatch,
        [SimpleNamespace(name="ghost", value="x:y", dist=None), _ep(dist_name="nanobot-demo")],
    )

    assert pkg_info.module_packages() == ["nanobot-demo"]
    # 缺发行包信息要留痕，但不中断其它行
    assert "ghost" in capsys.readouterr().err


def test_gatekeeper_routes_has_no_hardcoded_business_package():
    src = _ROUTES_SRC.read_text(encoding="utf-8")

    assert "nanobot-quant" not in src, "启动 DIAG 不得再写死业务包名"
    assert "nanobot_quant" not in src
    assert "module_packages()" in src, "业务系统行必须来自 entry point 发现"
    # 框架两件仍在（否则纯框架部署看不到自己的 pin）
    assert '"nanobot-legion"' in src and '"cloud-agent-gateway"' in src
