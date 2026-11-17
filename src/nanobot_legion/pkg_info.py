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
    from cloud_agent_gateway.package_source import get_package_source
except Exception:  # noqa: BLE001 - 旧版 CAG 缺模块，不应阻断启动
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
