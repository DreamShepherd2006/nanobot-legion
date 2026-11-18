"""启动诊断用的包信息读取（不依赖 logging 配置、不依赖 FastAPI）。

gatekeeper 进程没有给 ``logging`` 挂 handler，INFO 级日志会被 Python 的
lastResort 静默丢弃 —— 所以诊断输出一律写 stderr。

安装来源统一由 ``cloud_agent_gateway.package_source`` 解析，与「📦 开源代码」
表共用一份实现：pip 把 git 信息写在 ``direct_url.json`` 的 ``vcs_info`` 子对象里
（``{"url": ..., "vcs_info": {"requested_revision": ..., "commit_id": ...}}``），
早先直接读顶层 ``requested_revision`` 才导致启动日志恒为 ``@?``。
"""

from __future__ import annotations

import importlib.metadata
import sys

try:  # CAG >= v0.2.0；更老的安装缺这个模块，此时退回读包版本
    from cloud_agent_gateway.package_source import build_source_link, get_package_source
except Exception:  # noqa: BLE001 - 旧版 CAG 缺模块，不应阻断启动
    build_source_link = None
    get_package_source = None


def diag(msg: str) -> None:
    """写一行诊断到 stderr（不经过 logging）。"""
    print(f"[DIAG] {msg}", file=sys.stderr, flush=True)


def _version_of(pkg_name: str) -> str:
    try:
        return f"v{importlib.metadata.version(pkg_name)}"
    except Exception as exc:  # noqa: BLE001
        diag(f"{pkg_name}: 未安装或包元数据不可读 — {type(exc).__name__}: {exc}")
        return "?"


def installed_rev(pkg_name: str) -> str:
    """返回 pip 安装的实际 pin：tag / 短哈希 → commit → 包版本 → ``?``。

    显示顺序有意为之：``requested_revision`` 是「我们要求装的那个 ref」
    （tag 或短哈希），最能区分 CD fork 验证 pin 与上游发布；没有时退到
    ``commit_id`` 短哈希；PyPI 安装没有 direct_url.json，退到包版本。
    读不到时打一行原因，绝不静默返回 ``?``。
    """
    if get_package_source is None:
        diag(f"{pkg_name}: cloud-agent-gateway 未提供 package_source（版本过旧），退回读包版本")
        return _version_of(pkg_name)

    try:
        info = get_package_source(pkg_name)
    except Exception as exc:  # noqa: BLE001 - 包元数据坏了也不能影响启动
        diag(f"{pkg_name}: 读取安装来源失败 — {type(exc).__name__}: {exc}")
        return "?"

    if info:
        rev = str(info.get("revision") or "").strip()
        if rev.startswith("v"):
            return rev
        if rev:
            return rev if len(rev) <= 8 else rev[:7]
        commit = str(info.get("commit") or "").strip()
        if commit:
            return commit[:7]
        diag(f"{pkg_name}: direct_url.json 缺少 revision/commit（url={info.get('url') or '—'}）")
        return "?"

    return _version_of(pkg_name)


# 模块行的展示后缀（C147 定稿文案：nanobot-quant →「nanobot-quant业务系统」）
MODULE_LABEL_SUFFIX = "业务系统"


def _module_dists():
    """枚举 Legion Module 的发行包，yield ``(ep_name, pkg_name, version, dist)``。

    发现机制与 ``module_loader`` 同源（同一个 entry point group），不维护第二份
    组件清单：装了什么模块就遍历出什么。单项失败只打原因并跳过，不影响调用方
    （启动 DIAG / 配置中心表格）继续工作。
    """
    try:
        from .module_loader import _iter_entry_points

        eps = _iter_entry_points()
    except Exception as exc:  # noqa: BLE001 - 发现失败不能影响启动
        diag(f"枚举 Legion Module 失败 — {type(exc).__name__}: {exc}")
        return

    for ep in eps:
        ep_name = str(getattr(ep, "name", "?") or "?")
        dist = getattr(ep, "dist", None)
        if dist is None:
            diag(f"模块 {ep_name}: entry point 缺少发行包信息，跳过")
            continue
        try:
            pkg_name = str(dist.metadata["Name"] or "").strip()
            version = str(getattr(dist, "version", "") or "")
        except Exception as exc:  # noqa: BLE001
            diag(f"模块 {ep_name}: 读取发行包元数据失败 — {type(exc).__name__}: {exc}")
            continue
        if not pkg_name:
            continue
        yield ep_name, pkg_name, version, dist


def module_packages() -> list[str]:
    """已安装 Legion Module 的发行包名（去重保序）。

    启动 DIAG 的版本行用它追加：装了模块就多报一行，没装就什么都不报——
    不再硬编码任何业务包名（C152）。空表 = 纯框架部署，属正常状态、不告警。
    """
    names: list[str] = []
    seen: set[str] = set()
    for _ep_name, pkg_name, _version, _dist in _module_dists():
        if pkg_name.lower() in seen:
            continue
        seen.add(pkg_name.lower())
        names.append(pkg_name)
    return names


def module_source_rows() -> list[tuple[str, str]]:
    """枚举已安装的 Legion Module，返回「📦 开源代码」表里的 (组件名, 源码列)。

    发现机制与 ``module_loader`` 同源（同一个 entry point group），不另维护
    组件清单：装了什么模块就显示什么行。git 安装走 ``build_source_link``
    （与核心组件同款、带 pin）；PyPI 安装没有 ``direct_url.json``，退化为版本号
    文本；任一步失败只跳过该行并打原因，不影响配置中心整体生成。
    """
    if get_package_source is None or build_source_link is None:
        diag("跳过模块源码行：cloud-agent-gateway 未提供 package_source/build_source_link")
        return []

    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _ep_name, pkg_name, version, _dist in _module_dists():
        if pkg_name.lower() in seen:
            continue
        seen.add(pkg_name.lower())

        try:
            info = get_package_source(pkg_name)
        except Exception as exc:  # noqa: BLE001
            diag(f"{pkg_name}: 读取安装来源失败 — {type(exc).__name__}: {exc}")
            info = None

        if info:
            link = build_source_link(info, f"DreamShepherd2006/{pkg_name}")
        elif version:
            link = f"v{version}（PyPI 安装，无源码链接）"
        else:
            link = "—"
        rows.append((f"{pkg_name}{MODULE_LABEL_SUFFIX}", link))

    return rows
