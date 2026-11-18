"""Legion Module 发现与加载（契约 sdk=1）。

网关启动时按契约发现业务模块（entry point group ``nanobot_legion.modules``），
模块自己负责「注册页面」与「声明置顶会话内容」，Legion 不认识任何具体应用。

契约文本见 ``docs/legion-module.md``。失败语义（fail-closed）：
任一模块加载/校验失败只记日志并跳过，不影响网关与其他模块。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

#: 模块声明的 entry point group
MODULE_ENTRY_GROUP = "nanobot_legion.modules"

#: 当前支持的契约版本（模块 ``sdk`` 不一致即拒载）
SDK_VERSION = 1

REQUIRED_ATTRS = ("name", "display", "sdk", "route_prefix", "register_routes", "sections")


def _default_log(msg: str) -> None:
    """默认日志：写 stderr（网关 stdout 会被 launch.sh 捕获）。"""
    print(f"[MODULE] {msg}", file=sys.stderr, flush=True)


@dataclass
class LoadedModule:
    """已加载并通过契约校验的模块。"""

    name: str
    display: str
    sdk: int
    route_prefix: str
    obj: object

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.name}（{self.display}）"


def _iter_entry_points():
    """返回声明了模块 group 的 entry points（兼容旧 importlib API）。"""
    try:
        from importlib.metadata import entry_points
    except Exception:  # pragma: no cover - 极旧 Python
        return []
    try:
        eps = entry_points(group=MODULE_ENTRY_GROUP)
    except TypeError:  # pragma: no cover - py<3.10
        eps = entry_points().get(MODULE_ENTRY_GROUP, [])
    try:
        return list(eps)
    except Exception:  # pragma: no cover
        return []


def validate_module(obj) -> str | None:
    """按契约校验模块对象；返回错误信息（None 表示通过）。"""
    missing = [a for a in REQUIRED_ATTRS if not hasattr(obj, a)]
    if missing:
        return f"缺少契约字段 {', '.join(missing)}"
    for callable_attr in ("register_routes", "sections"):
        if not callable(getattr(obj, callable_attr)):
            return f"{callable_attr} 不是可调用对象"
    if not isinstance(getattr(obj, "sdk"), int):
        return f"sdk 必须是 int（实际 {type(obj.sdk).__name__}）"
    if obj.sdk != SDK_VERSION:
        return f"契约版本不兼容（模块 sdk={obj.sdk}，网关支持 sdk={SDK_VERSION}）"
    name = str(getattr(obj, "name", "") or "").strip()
    if not name:
        return "name 不能为空"
    prefix = str(getattr(obj, "route_prefix", "") or "")
    if not prefix.startswith("/"):
        return f"route_prefix 必须是以 / 开头的绝对路径（实际 {prefix!r}）"
    if not str(getattr(obj, "display", "") or "").strip():
        return "display 不能为空"
    return None


def _to_loaded(obj, source: str, log) -> LoadedModule | None:
    err = validate_module(obj)
    if err:
        log(f"⚠️ 模块拒载（来源 {source}）: {err}")
        return None
    return LoadedModule(
        name=str(obj.name).strip(),
        display=str(obj.display).strip(),
        sdk=int(obj.sdk),
        route_prefix=str(obj.route_prefix).rstrip("/") or "/",
        obj=obj,
    )


def discover_modules(log=_default_log) -> list[LoadedModule]:
    """发现并校验全部模块。

    同名模块只保留第一个并告警；一个都没装时返回空列表（网关照常启动，
    仅无业务页面）——**不做任何按约定名的隐式加载**，避免新部署被旧业务
    系统牵连。
    """
    found: dict[str, LoadedModule] = {}
    for ep in _iter_entry_points():
        try:
            obj = ep.load()
        except Exception as e:  # noqa: BLE001 - 单模块损坏不阻塞
            log(f"⚠️ 模块 {getattr(ep, 'name', '?')} 加载失败: {type(e).__name__}: {e}")
            continue
        loaded = _to_loaded(obj, f"entry point {getattr(ep, 'name', '?')}", log)
        if not loaded:
            continue
        if loaded.name in found:
            log(f"⚠️ 模块重名 {loaded.name}（entry point {getattr(ep, 'name', '?')}），已忽略后一个")
            continue
        found[loaded.name] = loaded

    if not found:
        log(f"ℹ️ 未发现 Legion Module（entry point {MODULE_ENTRY_GROUP} 下没有任何可用模块）")

    return [found[name] for name in sorted(found)]
