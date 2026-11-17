"""Legion Module 发现与加载（契约 sdk=1）。

网关启动时按契约发现业务模块（entry point group ``nanobot_legion.modules``），
模块自己负责「注册页面」与「声明置顶会话内容」，Legion 不认识任何具体应用。

契约文本见 ``docs/legion-module.md``。失败语义（fail-closed）：
任一模块加载/校验失败只记日志并跳过，不影响网关与其他模块。
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass

#: 模块声明的 entry point group
MODULE_ENTRY_GROUP = "nanobot_legion.modules"

#: 当前支持的契约版本（模块 ``sdk`` 不一致即拒载）
SDK_VERSION = 1

#: 兼容兜底：早期安装的模块未声明 entry point 时按约定名加载
LEGACY_ENTRY = "nanobot_quant.legion_plugin:MODULE"

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


def _load_legacy(log) -> LoadedModule | None:
    """按约定名加载模块（老安装方式，无 entry point）。"""
    module_path, _, attr = LEGACY_ENTRY.partition(":")
    try:
        mod = importlib.import_module(module_path)
        obj = getattr(mod, attr)
    except Exception as e:  # noqa: BLE001 - 未安装属正常情况
        log(f"ℹ️ 未发现 Legion Module（entry point 与兜底 {LEGACY_ENTRY} 均不可用: {type(e).__name__}）")
        return None
    loaded = _to_loaded(obj, LEGACY_ENTRY, log)
    if loaded:
        log(f"ℹ️ 通过兜底路径加载模块 {loaded.name}（建议模块声明 entry point {MODULE_ENTRY_GROUP}）")
    return loaded


def discover_modules(log=_default_log) -> list[LoadedModule]:
    """发现并校验全部模块。

    同名模块只保留第一个并告警；全部失败时不回退（由调用方决定是否走旧白名单）。
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
        legacy = _load_legacy(log)
        if legacy:
            found[legacy.name] = legacy

    return [found[name] for name in sorted(found)]
