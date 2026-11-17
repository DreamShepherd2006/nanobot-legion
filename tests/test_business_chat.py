"""业务管理置顶会话渲染测试（Legion Module 契约步 3）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from nanobot_legion import gatekeeper_business_chat as gb
from nanobot_legion.module_loader import LoadedModule


# ── 桩 ────────────────────────────────────────────────────────────


@dataclass
class FakeRow:
    label: str
    desc: str = ""
    status: str = ""
    link: str = ""


@dataclass
class FakeSection:
    heading: str
    rows: list = field(default_factory=list)
    desc: str = ""


class FakeModule:
    def __init__(self, sections):
        self._sections = sections
        self.ctx_seen = None

    def sections(self, ctx):
        self.ctx_seen = ctx
        if isinstance(self._sections, Exception):
            raise self._sections
        return self._sections


class FakePlatform:
    def __init__(self, root):
        self.data_root = str(root)
        self.sessions: dict[tuple[str, str], list] = {}
        self.sidebar: dict[str, dict] = {}
        self.transcripts: dict[tuple[str, str], list] = {}

    def instance_path(self, agent):
        return f"{self.data_root}/legion/instances/{agent}"

    def read_session(self, agent, cid):
        return self.sessions.get((agent, cid), [])

    def delete_session(self, agent, cid):
        self.sessions.pop((agent, cid), None)

    def write_session(self, agent, cid, lines):
        self.sessions[(agent, cid)] = lines

    def read_sidebar_state(self, agent):
        state = self.sidebar.get(agent) or {"pinned_keys": []}
        return {"pinned_keys": list(state.get("pinned_keys", []))}

    def write_sidebar_state(self, agent, state):
        self.sidebar[agent] = state

    def write_webui_transcript(self, agent, cid, events):
        self.transcripts[(agent, cid)] = events


class FakeGK:
    def __init__(self, root):
        self._platform = FakePlatform(root)
        self.agent_names = ["neo", "quant"]
        self.logs: list[str] = []

    def _log(self, msg):
        self.logs.append(msg)


def _loaded(name="quant", display="📊 业务管理", sections=None):
    return LoadedModule(name=name, display=display, sdk=1,
                        route_prefix="/config", obj=FakeModule(sections))


# ── 渲染层 ────────────────────────────────────────────────────────


def test_render_sections_status_column_and_links():
    md = gb.render_sections([
        FakeSection("⚙️ 当前状态", [
            FakeRow("交易模式", status="📊 Quant 模式", link="/config/mode"),
            FakeRow("实盘开关", status="⚪ 实盘关闭", link="/config/live"),
        ], desc="开关即时生效"),
    ])
    assert "# ⚙️ 当前状态" in md
    assert "开关即时生效" in md
    assert "| 项 | 状态 | 入口 |" in md
    assert "| 交易模式 | 📊 Quant 模式 | [打开](/config/mode) |" in md


def test_render_sections_desc_column_when_no_status():
    md = gb.render_sections([
        FakeSection("⚙️ 交易控制", [FakeRow("执行参数", "风控上限 / 场景", link="/config/exec")]),
    ])
    assert "| 项 | 说明 | 入口 |" in md
    assert "| 执行参数 | 风控上限 / 场景 | [打开](/config/exec) |" in md


def test_render_sections_mixes_status_and_desc_and_escapes_pipe():
    md = gb.render_sections([
        FakeSection("混合", [
            FakeRow("a", status="ON", desc="备注 | 竖线"),
            FakeRow("b", link=""),
        ]),
    ])
    assert "| a | ON · 备注 \\| 竖线 | [打开]" not in md  # b 无链接时占位
    assert "ON · 备注 \\| 竖线" in md
    assert "| b | — | — |" in md


def test_render_sections_supports_dict_atoms():
    md = gb.render_sections([{"heading": "H", "rows": [{"label": "L", "link": "/x"}]}])
    assert "# H" in md and "| L | — | [打开](/x) |" in md


def test_render_sections_separates_blocks():
    md = gb.render_sections([FakeSection("A"), FakeSection("B")])
    assert "# A" in md and "# B" in md and "\n---\n" in md


# ── 会话内容：模块优先 ────────────────────────────────────────────


def test_module_content_used_when_modules_found(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    mod = _loaded(sections=[FakeSection("⚙️ 当前状态", [FakeRow("交易模式", status="📊 Quant 模式", link="/config/mode")])])
    monkeypatch.setattr(gb, "discover_modules", lambda log=None: [mod])

    gb.ensure_business_management_chat(gk)

    assert len(gk._platform.sessions) == 2  # 两个 agent 各一条
    for (agent, _cid), lines in gk._platform.sessions.items():
        content = lines[1]["content"]
        assert "# 🧭 系统" in content            # 核心区块
        assert "# ⚙️ 当前状态" in content        # 模块 sections
        assert "[打开](/config/mode)" in content
        assert "nanobot-quant" not in content
    assert mod.obj.ctx_seen.module == "quant"
    assert any("由 1 个模块声明生成" in m for m in gk.logs)


def test_multiple_modules_get_display_wrappers(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    mods = [
        _loaded("a", "🅰️ A 系统", [FakeSection("A1", [FakeRow("x", link="/x")])]),
        _loaded("b", "🅱️ B 系统", [FakeSection("B1", [FakeRow("y", link="/y")])]),
    ]
    monkeypatch.setattr(gb, "discover_modules", lambda log=None: mods)
    gb.ensure_business_management_chat(gk)
    content = next(iter(gk._platform.sessions.values()))[1]["content"]
    assert "## 🅰️ A 系统" in content and "## 🅱️ B 系统" in content


def test_module_render_failure_skips_chat(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    mod = _loaded(sections=RuntimeError("boom"))
    monkeypatch.setattr(gb, "discover_modules", lambda log=None: [mod])
    gb.ensure_business_management_chat(gk)
    assert gk._platform.sessions == {}          # 步 4 起没有兜底内容
    assert any("渲染失败" in m for m in gk.logs)


def test_module_without_sections_skips_chat(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    monkeypatch.setattr(gb, "discover_modules", lambda log=None: [_loaded(sections=[])])
    gb.ensure_business_management_chat(gk)
    assert gk._platform.sessions == {}
    assert any("未声明会话内容" in m for m in gk.logs)


# ── 无模块 / 发现异常 ─────────────────────────────────────────────


def test_no_modules_skips_chat(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    monkeypatch.setattr(gb, "discover_modules", lambda log=None: [])
    gb.ensure_business_management_chat(gk)
    assert gk._platform.sessions == {}
    assert any("未发现 Legion Module" in m for m in gk.logs)


def test_legacy_content_builder_is_gone():
    """步 4：直连应用的兜底实现必须已删除（不留两套实现）。"""
    assert not hasattr(gb, "_build_legacy_content")
    src = open(gb.__file__, encoding="utf-8").read()
    assert "nanobot_quant" not in src


def test_discovery_exception_does_not_break(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)

    def boom(log=None):
        raise RuntimeError("discover failed")

    monkeypatch.setattr(gb, "discover_modules", boom)
    gb.ensure_business_management_chat(gk)
    assert gk._platform.sessions == {}
    assert any("模块发现异常" in m for m in gk.logs)


# ── 会话写入行为 ──────────────────────────────────────────────────


def test_old_business_chat_is_cleaned_and_new_one_pinned(monkeypatch, tmp_path):
    gk = FakeGK(tmp_path)
    # 旧同名会话（应被删除）
    gk._platform.sessions[("neo", "old-cid")] = [
        {"_type": "metadata", "metadata": {"title": gb._BUSINESS_CHAT_TITLE}}
    ]
    # 其他置顶项不应被动到
    gk._platform.sessions[("neo", "other-cid")] = [
        {"_type": "metadata", "metadata": {"title": "配置中心"}}
    ]
    gk._platform.sidebar["neo"] = {"pinned_keys": ["websocket:old-cid", "websocket:other-cid"]}

    monkeypatch.setattr(gb, "discover_modules", lambda log=None: [
        _loaded(sections=[FakeSection("S", [FakeRow("x", link="/x")])])
    ])
    gb.ensure_business_management_chat(gk)

    assert ("neo", "old-cid") not in gk._platform.sessions
    assert ("neo", "other-cid") in gk._platform.sessions
    pinned = gk._platform.sidebar["neo"]["pinned_keys"]
    assert "websocket:other-cid" in pinned
    assert len([p for p in pinned if p != "websocket:other-cid"]) == 1
