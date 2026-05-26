#!/usr/bin/env python3
"""Patch: Legion V6 — agent badges + terminal portal (v0.2.x adapted).

Targets (pre-compile, before npm build):
  - /app/webui/src/components/Sidebar.tsx

Dual-target NOT required: WebUI .tsx patches are compiled into dist/,
only the /app/webui/src/ copy matters before the Vite build step.

Injections:
  1. Add imports (useEffect, useMemo, useState, createPortal, useClient)
  2. LegionRoster component (agent status badges)
  3. LegionTerminal component (log portal via createPortal)
  4. In Sidebar: console state + event capture useEffect
  5. <LegionRoster onToggle /> between logo header and action buttons
  6. <LegionTerminal /> before </nav>
"""
from pathlib import Path

PATCH_LABEL = "legion-v6-sidebar"

SIDEBAR = Path("/app/webui/src/components/Sidebar.tsx")


# ── LegionRoster component (plain string → single braces in output) ──
LEGION_ROSTER = """
/* ── LegionRoster: agent status badges ── */
const STATUS_COLORS: Record<string, string> = {
  online: "bg-emerald-500",
  offline: "bg-red-500",
  executing: "bg-blue-500 animate-pulse",
  blocked: "bg-amber-500",
  disconnected: "bg-red-500 animate-pulse",
};
const STATUS_LABELS: Record<string, string> = {
  online: "在线",
  offline: "离线",
  executing: "执行中",
  blocked: "阻塞",
  disconnected: "断连",
};
type AgentStatus = "online" | "offline" | "executing" | "blocked" | "disconnected";

function LegionRoster(props: {
  peers: Record<string, { id: string; name?: string }>;
  status: Record<string, string>;
  version?: string;
  onToggleConsole?: () => void;
}) {
  const agents = Object.keys(props.peers).sort();
  const t = (s: string) => STATUS_LABELS[s] || s;
  return (
    <div
      className="flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 border-b border-border/40 bg-muted/10 cursor-pointer hover:bg-muted/20 transition-colors select-none"
      onClick={props.onToggleConsole}
      title="点击切换军团指挥中心"
    >
      <span className="text-[11px] text-muted-foreground/60 tracking-wider font-semibold">
        军团{props.version ? ` · v${props.version}` : ""}
      </span>
      {agents.map((key) => {
        const peer = props.peers[key] || { id: key };
        const st: AgentStatus =
          props.status[key] === "online"
            ? "online"
            : props.status[key] === "executing"
              ? "executing"
              : props.status[key] === "blocked"
                ? "blocked"
                : "offline";
        const color = STATUS_COLORS[st] || STATUS_COLORS.offline;
        return (
          <span
            key={key}
            className="flex items-center gap-1 text-[11px] font-semibold text-foreground/80"
            title={`${peer.name || key}: ${t(st)}`}
          >
            <span
              className={`inline-block h-[14px] w-[14px] rounded-full ${color} ring-1 ring-border/30`}
            />
            {peer.name || key}
          </span>
        );
      })}
    </div>
  );
}
"""

# ── LegionTerminal component (right-side panel) (f-string — double braces become single in output) ──
LEGION_TERMINAL = f"""
/* ── LegionTerminal: right-side command center ── */
function LegionTerminal(props: {{
  show: boolean;
  logs: Record<string, string[]>;
  activeTab: string;
  setActiveTab: (t: string) => void;
  tabs: string[];
  version?: string;
  peers: Record<string, {{ id: string; name?: string }}>;
  status: Record<string, string>;
  actions: Record<string, string>;
  tasks?: {{ goal: string; tasks: Array<{{ id: string; title: string; agent?: string; status: string }}> }} | null;
  onClose: () => void;
  onOpen: () => void;
}}) {{
  const {{ tabs, peers, status, actions }} = props;
  const agents = Object.keys(peers).sort();

  /* ── collapsed state: toggle pill on right edge ── */
  if (!props.show) {{
    return createPortal(
      <div
        className="fixed right-0 top-1/2 -translate-y-1/2 z-[100]
                   flex items-center gap-1.5
                   bg-accent/90 text-accent-foreground
                   border border-border/40 border-r-0
                   rounded-l-full shadow-lg
                   pl-3.5 pr-2.5 py-2
                   cursor-pointer hover:bg-accent hover:shadow-xl
                   transition-all duration-200
                   select-none"
        onClick={{props.onOpen}}
        title="展开指挥中心"
      >
        <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
        <span className="text-sm font-semibold tracking-wide">指挥中心</span>
      </div>,
      document.body
    );
  }}

  /* ── expanded state: full right panel ── */
  const visibleLogs = props.logs[props.activeTab] || [];
  const TAB_LABELS: Record<string, string> = {{ all: "全部" }};
  const STATUS_MAP: Record<string, string> = {{
    online: "就绪", executing: "工作中", blocked: "阻塞", disconnected: "离线",
  }};
  const DOT_COLORS: Record<string, string> = {{
    online: "bg-emerald-500",
    executing: "bg-blue-500 animate-pulse",
    blocked: "bg-amber-500",
    disconnected: "bg-red-500 animate-pulse",
  }};
  return createPortal(
    <div className="fixed right-0 top-0 h-full w-[340px]
                    bg-background border-l border-border
                    shadow-2xl z-[100] flex flex-col text-sm">
      {{/* header */}}
      <div className="flex items-center justify-between px-3 py-2.5
                      border-b border-border bg-muted/30 shrink-0">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-semibold text-foreground/85 tracking-wide">
            军团指挥中心{{props.version ? ` · v${{props.version}}` : ""}}
          </span>
        </div>
        <button
          onClick={{props.onClose}}
          className="text-muted-foreground/60 hover:text-foreground px-1.5 py-0.5 rounded text-sm leading-none"
          title="关闭面板">✕</button>
      </div>

      {{/* agent cards */}}
      <div className="px-3 py-2.5 border-b border-border/30 bg-muted/10 shrink-0">
        <div className="text-[10px] text-muted-foreground/45 uppercase tracking-wider mb-2">
          🤖 团队状态
        </div>
        {{agents.map((key: string) => {{
          const peer = peers[key] || {{ id: key }};
          const st = status[key] || "offline";
          const dot = DOT_COLORS[st] || "bg-red-500";
          const label = STATUS_MAP[st] || st;
          const action = actions[key] || "—";
          return (
            <div key={{key}}
              className="flex items-center gap-2 py-1.5 border-b border-border/10 last:border-0">
              <span
                className={{`inline-block h-3 w-3 rounded-full ${{dot}} ring-1 ring-border/30 shrink-0`}}
                title={{label}}
              />
              <span className="text-[11px] font-semibold text-foreground/80 w-16 shrink-0">
                {{peer.name || key}}
              </span>
              <span className="text-[10px] text-muted-foreground/65 truncate leading-relaxed">
                {{action}}
              </span>
            </div>
          );
        }})}}
      </div>

      {{/* tab bar */}}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border/30 bg-muted/10 shrink-0">
        <button
          onClick={{() => props.setActiveTab("tasks")}}
          className={{`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors
            ${{props.activeTab === "tasks"
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground/70 hover:text-foreground hover:bg-accent/30"}}`}}
        >
          📋 任务
        </button>
        <button
          onClick={{() => props.setActiveTab("all")}}
          className={{`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors
            ${{props.activeTab !== "tasks"
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground/70 hover:text-foreground hover:bg-accent/30"}}`}}
        >
          📜 日志
        </button>
        {{props.activeTab !== "tasks" && (
          <div className="flex items-center gap-0.5 ml-1 overflow-x-auto">
            {{tabs.filter((t: string) => t !== "all" && t !== "tasks").map((tab: string) => {{
              const isActive = props.activeTab === tab;
              const count = (props.logs[tab] || []).length;
              const label = TAB_LABELS[tab] || tab;
              return (
                <button key={{tab}}
                  onClick={{() => props.setActiveTab(tab)}}
                  className={{`px-1.5 py-0.5 text-[10px] font-semibold rounded whitespace-nowrap transition-colors
                    ${{isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground/55 hover:text-foreground hover:bg-accent/30"}}`}}
                >
                  {{label}}{{count > 0 && <span className="ml-0.5 opacity-50">{{count}}</span>}}
                </button>
              );
            }})}}
          </div>
        )}}
      </div>

      {{/* content area */}}
      <div className="flex-1 overflow-y-auto text-xs bg-background">
        {{props.activeTab === "tasks" ? (
          props.tasks && props.tasks.tasks?.length > 0 ? (
            <div className="flex flex-col h-full">
              {{/* Goal header */}}
              {{props.tasks.goal && (
                <div className="px-3 py-2 border-b border-border/20 bg-muted/5 shrink-0">
                  <div className="text-[10px] text-muted-foreground/40 uppercase tracking-wider mb-0.5">目标</div>
                  <div className="text-xs font-semibold text-foreground/90">{{props.tasks.goal}}</div>
                </div>
              )}}
              {{/* Progress bar */}}
              <div className="px-3 py-2 border-b border-border/20 shrink-0">
                {{(() => {{
                  const total = props.tasks!.tasks.length;
                  const done = props.tasks!.tasks.filter(t => t.status === "done").length;
                  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
                  return (
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-muted-foreground/50">进度</span>
                        <span className="text-[10px] font-semibold text-foreground/70">{{done}}/{{total}} ({{pct}}%)</span>
                      </div>
                      <div className="h-1.5 bg-border/40 rounded-full overflow-hidden">
                        <div className={{`h-full rounded-full transition-all duration-500 ${{pct === 100 ? 'bg-emerald-500' : 'bg-blue-500'}}`}}
                          style={{{{ width: `${{pct}}%` }}}}
                        />
                      </div>
                    </div>
                  );
                }})()}}
              </div>
              {{/* Task list */}}
              <div className="flex-1 overflow-y-auto px-2 py-1">
                {{props.tasks.tasks.map((task: any, i: number) => {{
                  const statusIcon: Record<string, string> = {{
                    done: "✅", in_progress: "🔄", pending: "⬜", blocked: "🚫",
                  }};
                  const statusColor: Record<string, string> = {{
                    done: "text-emerald-500", in_progress: "text-blue-500",
                    pending: "text-muted-foreground/30", blocked: "text-amber-500",
                  }};
                  const icon = statusIcon[task.status] || "❓";
                  const clr = statusColor[task.status] || "text-muted-foreground";
                  return (
                    <div key={{task.id || i}}
                      className="flex items-start gap-2 py-1.5 border-b border-border/10 last:border-0">
                      <span className={{`text-[14px] ${{clr}} shrink-0 mt-px`}}>{{icon}}</span>
                      <div className="flex-1 min-w-0">
                        <div className={{`text-[11px] leading-tight ${{task.status === 'done' ? 'text-muted-foreground/50 line-through' : 'text-foreground/80'}}`}}>
                          {{task.title}}
                        </div>
                        {{task.agent && (
                          <span className="text-[9px] text-muted-foreground/40">{{task.agent}}</span>
                        )}}
                      </div>
                    </div>
                  );
                }})}}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-muted-foreground/20 gap-3">
              <span className="text-3xl">📋</span>
              <span className="text-[11px]">暂无任务</span>
              <span className="text-[10px] text-muted-foreground/15">通过 Commander 推送任务数据</span>
            </div>
          )
        ) : visibleLogs.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-muted-foreground/20 gap-3">
            <span className="text-3xl">⚔️</span>
            <span className="text-[11px]">
              {{props.activeTab === "all" ? "等待信号…" : `${{TAB_LABELS[props.activeTab] || props.activeTab}} 暂无日志`}}
            </span>
          </div>
        ) : (
          <div className="p-2">
            {{visibleLogs.map((log: string, i: number) => (
              <div key={{i}}
                className="py-0.5 border-b border-border/10 last:border-0 break-all
                           hover:bg-accent/5 transition-colors
                           font-mono text-[11px] leading-relaxed text-muted-foreground/80">
                {{log}}
              </div>
            ))}}
          </div>
        )}}
      </div>
    </div>,
    document.body
  );
}}
"""

# ── State + event capture (injected into Sidebar body) (plain string) ──
SIDEBAR_STATE = """
  /* ── Legion: console state ── */
  const { client } = useClient();
  const [showConsole, setShowConsole] = useState(false);
  const [allLogs, setAllLogs] = useState<string[]>([]);
  const [agentLogs, setAgentLogs] = useState<Record<string, string[]>>({});
  const [activeTab, setActiveTab] = useState("all");
  const [legionPeers, setLegionPeers] = useState<Record<string, { id: string; name?: string }>>({});
  const [legionStatus, setLegionStatus] = useState<Record<string, string>>({});
  const [nanobotVersion, setNanobotVersion] = useState<string>("...");
  const [taskData, setTaskData] = useState<{goal: string; tasks: Array<{id: string; title: string; agent?: string; status: string}>} | null>(null);

  /* derive logs + tabs dynamically */
  const agentIds = Object.keys(legionPeers).sort();
  const allTabs = ["all", ...agentIds];
  const logs: Record<string, string[]> = { all: allLogs, ...agentLogs };

  /* ── derive agent action summaries from latest log per agent ── */
  const agentActions = useMemo(() => {
    const acts: Record<string, string> = {};
    for (const agent of agentIds) {
      const lines = agentLogs[agent] || [];
      if (lines.length === 0) {
        const st = legionStatus[agent];
        acts[agent] = st === "executing" ? "工作中" : st === "blocked" ? "阻塞" : st === "online" ? "就绪" : "离线";
        continue;
      }
      const last = lines[lines.length - 1];
      const match = last.match(/\[[\d:]+\]\s+\S+\s+(.+)/);
      acts[agent] = match ? match[1].slice(0, 60) : last.slice(0, 60);
    }
    return acts;
  }, [agentLogs, agentIds, legionStatus]);

  /* ── helper: push line to a named bin ── */
  function _pushLog(bin: string, line: string, max: number) {
    setAllLogs(prev => [...prev, line].slice(-500));
    if (bin !== "all") {
      setAgentLogs(prev => {
        const cur = prev[bin] || [];
        return { ...prev, [bin]: [...cur, line].slice(-max) };
      });
    }
  }

  /* ── Legion: event capture ── */
  useEffect(() => {
    return client.onAnyEvent((ev: any) => {
      const ts = new Date().toLocaleTimeString();
      const evType = (ev as any).event || (ev as any).type || "?";

      /* ── Handle legion roster/status updates ── */
      if ((evType === "legion_update" || evType === "cluster_update") && (ev as any).roster) {
        const roster = (ev as any).roster as Record<string, { id: string; name?: string }>;
        const data = (ev as any).data as Record<string, string> | undefined;
        setLegionPeers(prev => {
          const next = { ...prev };
          for (const [k, v] of Object.entries(roster)) {
            if (!next[k]) next[k] = v;
          }
          return next;
        });
        if (data) setLegionStatus(data);
        const ver = (ev as any).nanobot_version;
        if (ver && typeof ver === "string") setNanobotVersion(ver);

        /* Capture tasks from Commander */
        const tdata = (ev as any).tasks;
        if (tdata && tdata.tasks?.length > 0) setTaskData(tdata as typeof taskData);

        /* Per-agent status lines */
        if (data) {
          for (const [agent, status] of Object.entries(data)) {
            _pushLog(agent, `[${ts}] 状态  ${agent} = ${status}`, 150);
          }
        }
        return;  /* legion_update done — no generic line needed */
      }

      /* build detail line */
      let detail = "";
      if (typeof (ev as any).text === "string") {
        detail = (ev as any).text.slice(0, 120);
      } else if (typeof (ev as any).content === "string") {
        detail = (ev as any).content.slice(0, 120);
      } else {
        try { detail = JSON.stringify(ev).slice(0, 160); } catch (_) { detail = "?"; }
      }

      const line = `[${ts}] ${evType}  ${detail}`;

      /* route: squad relay events carry sender/target */
      const sender = (ev as any).sender as string | undefined;
      const tgt = (ev as any).target as string | undefined;

      _pushLog("all", line, 500);
      if (sender) _pushLog(sender, line, 150);
      if (tgt && tgt !== sender) _pushLog(tgt, line, 150);
    });
  }, [client]);
"""

# ── Terminal render portal (injected before </nav>) (plain string) ──
TERMINAL_RENDER = """
      {/* ── Legion: right-side command center ── */}
      <LegionTerminal
        show={showConsole}
        logs={logs}
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        tabs={allTabs}
        version={nanobotVersion}
        peers={legionPeers}
        status={legionStatus}
        actions={agentActions}
        tasks={taskData}
        onClose={() => setShowConsole(false)}
        onOpen={() => setShowConsole(true)}
      />
"""


def patch_sidebar():
    """Inject LegionRoster + LegionTerminal into Sidebar.tsx."""
    if not SIDEBAR.is_file():
        print(f"  [{PATCH_LABEL}] {SIDEBAR} not found — skip")
        return False

    content = SIDEBAR.read_text()
    ok = True

    # ── Injection 1: expand react imports (adapted for v0.2.x: useState, type ReactNode) ──
    anchor_import = 'import { useState, type ReactNode } from "react";'
    if anchor_import not in content:
        print(f"  [{PATCH_LABEL}] anchor import not found — skip")
        return False

    expanded_import = 'import { useEffect, useMemo, useState, type ReactNode } from "react";'
    if expanded_import != anchor_import:
        if "useEffect" not in content.split("\n")[0]:
            content = content.replace(anchor_import, expanded_import, 1)
            print(f"  [{PATCH_LABEL}] expanded react imports (useEffect, useMemo)")
        else:
            print(f"  [{PATCH_LABEL}] react imports already expanded")

    # Add createPortal from react-dom (separate import)
    anchor_rd_import = 'import { useTranslation } from "react-i18next";'
    if anchor_rd_import in content and "createPortal" not in content:
        rd_addon = '\nimport { createPortal } from "react-dom";'
        content = content.replace(anchor_rd_import, anchor_rd_import + rd_addon, 1)
        print(f"  [{PATCH_LABEL}] added createPortal import from react-dom")
    elif "createPortal" not in content:
        print(f"  [{PATCH_LABEL}] useTranslation anchor for createPortal not found — skip")

    # ── Injection 2: useClient import ──
    anchor_usec = 'import { useTranslation } from "react-i18next";'
    if anchor_usec in content:
        usec_import = '\nimport { useClient } from "@/providers/ClientProvider";'
        if 'useClient' not in content:
            content = content.replace(anchor_usec, anchor_usec + usec_import, 1)
            print(f"  [{PATCH_LABEL}] added useClient import")
        else:
            print(f"  [{PATCH_LABEL}] useClient import already present")
    else:
        print(f"  [{PATCH_LABEL}] useTranslation anchor not found — skip import")

    # ── Injection 3: LegionRoster component (before Sidebar function) ──
    anchor_side_fn = "export function Sidebar("
    if "function LegionRoster" not in content:
        content = content.replace(anchor_side_fn, LEGION_ROSTER + "\n" + anchor_side_fn, 1)
        print(f"  [{PATCH_LABEL}] added LegionRoster component")
    else:
        print(f"  [{PATCH_LABEL}] LegionRoster already present")

    # ── Injection 4: LegionTerminal component (before Sidebar function) ──
    if "function LegionTerminal" not in content:
        content = content.replace(anchor_side_fn, LEGION_TERMINAL + "\n" + anchor_side_fn, 1)
        print(f"  [{PATCH_LABEL}] added LegionTerminal component")
    else:
        print(f"  [{PATCH_LABEL}] LegionTerminal already present")

    # ── Injection 5: state + event capture (after menuPortalContainer useState, before collapsed) ──
    # v0.2.x: anchor changed from "const [query, setQuery]" to menuPortalContainer
    anchor_state = "    useState<HTMLElement | null>(null);"
    if anchor_state in content and "/* ── Legion: console state ── */" not in content:
        content = content.replace(anchor_state, anchor_state + SIDEBAR_STATE, 1)
        print(f"  [{PATCH_LABEL}] added console state + event capture")
    elif "/* ── Legion: console state ── */" in content:
        print(f"  [{PATCH_LABEL}] console state already present")
    else:
        print(f"  [{PATCH_LABEL}] state anchor (menuPortalContainer) not found — skip state injection")
        ok = False

    # ── Injection 6: <LegionRoster /> between logo header and action buttons ──
    if "<LegionRoster " not in content:
        legion_roster_jsx = (
            '      <LegionRoster peers={legionPeers} status={legionStatus} version={nanobotVersion} onToggleConsole={() => setShowConsole(v => !v)} />'
        )
        # v0.2.x: the action area starts with a multi-line div using cn()
        # Match: <div\n        className={cn(\n          "space-y-1.5 px-2 pb-2",
        anchor_action_div = '      <div\n        className={cn(\n          "space-y-1.5 px-2 pb-2",'
        if anchor_action_div in content:
            content = content.replace(
                anchor_action_div,
                legion_roster_jsx + "\n" + anchor_action_div,
                1
            )
            print(f"  [{PATCH_LABEL}] inserted <LegionRoster /> before action area")
        else:
            # Fallback: try the old single-line anchor
            anchor_old = '      <div className="space-y-1.5 px-2 pb-2">'
            if anchor_old in content:
                content = content.replace(anchor_old, legion_roster_jsx + "\n" + anchor_old, 1)
                print(f"  [{PATCH_LABEL}] inserted <LegionRoster /> (old anchor)")
            else:
                print(f"  [{PATCH_LABEL}] action div anchor not found — skip LegionRoster")
                ok = False
    else:
        print(f"  [{PATCH_LABEL}] <LegionRoster /> already in sidebar")

    # ── Injection 7: <LegionTerminal /> before </nav> ──
    anchor_nav_end = "    </nav>"
    if "<LegionTerminal " not in content:
        if anchor_nav_end in content:
            content = content.replace(anchor_nav_end, TERMINAL_RENDER + "\n" + anchor_nav_end, 1)
            print(f"  [{PATCH_LABEL}] inserted <LegionTerminal />")
        else:
            print(f"  [{PATCH_LABEL}] </nav> anchor not found — skip terminal render")
            ok = False
    else:
        print(f"  [{PATCH_LABEL}] <LegionTerminal /> already in sidebar")

    SIDEBAR.write_text(content)

    if ok:
        print(f"✅ [{PATCH_LABEL}] complete")
    else:
        print(f"⚠ [{PATCH_LABEL}] partial — some injections skipped")
    return ok


def main():
    ok = patch_sidebar()
    if not ok:
        print(f"❌ [{PATCH_LABEL}] failed (some targets missing) — check upstream changes")
        exit(1)


if __name__ == "__main__":
    main()
