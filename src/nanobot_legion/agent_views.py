#!/usr/bin/env python3
"""Agent views — escape helpers and HTML table renderers.

Extracted from agent_config.py. Pure string-generation functions with no
web framework or I/O dependencies.
"""
from __future__ import annotations


def _escape_js(s: str) -> str:
    """Escape a string for safe embedding in JS single-quoted string."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _escape_html(s: str) -> str:
    """Escape a string for safe embedding in HTML."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _render_running_table(squad_cfg: dict, running: dict[str, bool]) -> str:
    """Render HTML table of running agent peers with status icons."""
    peers = squad_cfg.get("peers", {})
    if not peers:
        return '<p class="empty">暂无 agent（异常状态）</p>'

    rows = []
    for name, info in sorted(peers.items()):
        if not isinstance(info, dict):
            continue
        if info.get("zone", "active") != "active":
            continue
        gw = info.get("gateway_port", "?")
        ws = info.get("ws_port", "?")
        is_cmd = info.get("id") == "squad:commander"
        is_up = running.get(name, False)
        status_icon = "🟢" if is_up else "⚪"
        tag = '<span class="tag tag-cmd">Commander</span>' if is_cmd else '<span class="tag tag-work">Worker</span>'

        # Printable name with link to detail page
        name_link = f'<a class="agent-link" href="/config/agents/{name}">{name}</a>'

        # Resurrection toggle
        if is_cmd:
            rez = "🔒"
        elif is_up:
            rez = f'<button class="rez-on" onclick="stopAgent(\'{name}\')" title="运行中 — 点击停止">🟢 停止</button>'
        else:
            rez = f'<button class="rez-off" onclick="startAgent(\'{name}\')" title="已停止 — 点击启动">▶ 启动</button>'

        if is_cmd:
            action = ""
        else:
            action = f'<button class="del-btn" onclick="removeAgent(\'{name}\')">删除</button>'
        rows.append(f"<tr><td>{status_icon} {name_link} {tag}</td><td>{gw}/{ws}</td><td>{rez}</td><td>{action}</td></tr>")

    return ("<table><tr><th>Agent</th><th>端口 (gw/ws)</th><th>启停</th><th>操作</th></tr>"
            + "\n".join(rows) + "</table>")


def _render_archived_table(archived: list[dict]) -> str:
    """Render HTML table of archived (removed) agents."""
    if not archived:
        return '<p class="empty">无已归档的 agent</p>'

    rows = []
    for a in archived:
        name = a["name"]
        provider = a.get("provider") or "—"
        model = a.get("model") or "—"
        ts = a.get("timestamp", "")
        ts_display = ts[:8] if len(ts) >= 8 else ts
        dir_name_esc = _escape_js(a["dir_name"])
        name_esc = _escape_js(name)
        rows.append(
            f"<tr class=\"archived-row\"><td>📦 {name}</td>"
            f"<td>{provider}</td><td style='max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap'>{model}</td>"
            f"<td style='font-size:.8em;color:#999'>{ts_display}</td>"
            f"<td>"
            f"<button class=\"restore-btn\" onclick=\"restoreAgent('{name_esc}','{dir_name_esc}')\">恢复</button>"
            f"<button class=\"del-btn\" onclick=\"deletePermanent('{name_esc}','{dir_name_esc}')\">🗑️</button>"
            f"</td></tr>"
        )

    return ("<table><tr><th>Agent</th><th>Provider</th><th>Model</th><th>归档时间</th><th>操作</th></tr>"
            + "\n".join(rows) + "</table>")
