"""把已发现的 Legion Module 挂到网关上（并提供只读上下文）。

职责边界：网关只做「发现 → 传上下文 → 调用」，业务页面与会话内容归模块自己。
单个模块挂载失败只记日志，不影响其他模块与核心路由。
"""

from __future__ import annotations

import os
import sys

from nanobot_legion.module_loader import LoadedModule, discover_modules


def _stderr(msg: str) -> None:
    """失败类日志必须可见：网关 logger 的 INFO 会被丢弃，故同步打 stderr。"""
    print(f"[MODULE] {msg}", file=sys.stderr, flush=True)


class ModuleContext:
    """传给模块的只读上下文（契约 sdk=1）。

    - ``data_root``：持久化卷根目录（所有路径均由模块自己拼）
    - ``gatekeeper``：过渡期兼容句柄，仅用于既有 ``register_xxx_routes(app, gk)``
      签名；新模块不应依赖。**可能为 ``None``** —— ``squad_config_sync``（独立短进程）
      调用 ``mcp_specs(ctx)`` 时没有网关实例，钩子不得依赖该字段
    - ``log``：写网关日志（stderr / gatekeeper 日志）
    - ``data_dir`` / ``credentials_dir`` / ``files_dir``：按契约分配的模块专属目录
      （访问时按需创建）
    """

    def __init__(self, module: str, data_root: str | None, gatekeeper=None, log=None):
        self.module = module
        self.data_root = data_root or ""
        self.gatekeeper = gatekeeper
        self._log = log

    def log(self, msg: str) -> None:
        if callable(self._log):
            self._log(msg)
        if "⚠️" in msg:
            _stderr(msg)

    def _dir(self, *parts: str) -> str:
        base = self.data_root or "."
        path = os.path.join(base, *parts)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:  # noqa: BLE001 - 目录创建失败不得拖垮挂载
            pass
        return path

    @property
    def data_dir(self) -> str:
        return self._dir("legion", self.module)

    @property
    def credentials_dir(self) -> str:
        return self._dir("legion", "credentials", self.module)

    @property
    def files_dir(self) -> str:
        return self._dir("files", self.module)


def _resolve_data_root(gatekeeper) -> str | None:
    platform = getattr(gatekeeper, "_platform", None)
    root = getattr(platform, "data_root", None)
    if root:
        return str(root)
    for env in ("DATA_ROOT", "NANOBOT_DATA_ROOT"):
        if os.environ.get(env):
            return os.environ[env]
    return None


def register_modules(app, gatekeeper, data_root: str | None = None, log=None) -> list[LoadedModule]:
    """发现并挂载全部模块，返回已注册模块列表（空列表表示没发现任何模块）。"""
    emit = log if callable(log) else (lambda m: None)
    modules = discover_modules(log=emit)
    if not modules:
        emit("ℹ️ 未发现 Legion Module（业务页面走调用方兜底路径）")
        return []

    root = data_root or _resolve_data_root(gatekeeper)
    prefixes: dict[str, str] = {}
    for mod in modules:
        if mod.route_prefix in prefixes:
            msg = f"⚠️ 路由前缀冲突 {mod.route_prefix}: {mod.name} 与 {prefixes[mod.route_prefix]}（两者均继续加载）"
            emit(msg)
            _stderr(msg)
        else:
            prefixes[mod.route_prefix] = mod.name

        ctx = ModuleContext(mod.name, root, gatekeeper=gatekeeper, log=emit)
        try:
            mod.obj.register_routes(app, ctx)
        except Exception as e:  # noqa: BLE001 - 单模块失败不阻塞
            msg = f"⚠️ 模块 {mod.name} 页面挂载失败: {type(e).__name__}: {e}"
            emit(msg)
            _stderr(msg)
            continue
        emit(f"🧩 {mod.display} 模块已挂载（{mod.name} · sdk={mod.sdk} · 前缀 {mod.route_prefix}）")

    return modules
