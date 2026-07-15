import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Archive,
  Menu,
  Search,
  Settings,
  SquarePen,
  Blocks,
} from "lucide-react";

import { useTranslation } from "react-i18next";

import { ChatList } from "@/components/ChatList";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useClient } from "@/providers/ClientProvider";
import { createPortal } from "react-dom";
import type {
  ChatSummary,
  SidebarViewState,
} from "@/lib/types";
import { cn } from "@/lib/utils";

interface SidebarProps {
  sessions: ChatSummary[];
  activeKey: string | null;
  loading: boolean;
  onNewChat: () => void;
  onSelect: (key: string) => void;
  onRequestDelete: (key: string, label: string) => void;
  onTogglePin: (key: string) => void;
  onRequestRename: (key: string, label: string) => void;
  onToggleArchive: (key: string) => void;
  onToggleGroup: (groupId: string) => void;
  onRequestRenameProject: (projectKey: string, label: string) => void;
  onNewChatInProject: (projectPath: string, projectName: string) => void;
  onOpenSettings: () => void;
  onOpenApps: () => void;
  onOpenSearch: () => void;
  activeUtility?: "apps" | null;
  onToggleArchived: () => void;
  onCollapse: () => void;
  onExpand?: () => void;
  containActionMenus?: boolean;
  collapsed?: boolean;
  pinnedKeys?: string[];
  archivedKeys?: string[];
  titleOverrides?: Record<string, string>;
  projectNameOverrides?: Record<string, string>;
  collapsedGroups?: Record<string, boolean>;
  runningChatIds?: string[];
  completedChatIds?: string[];
  viewState?: SidebarViewState;
  showArchived?: boolean;
  archivedCount?: number;
  defaultWorkspacePath?: string | null;
  hostChromeInset?: boolean;
}


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


/* ── LegionTerminal: right-side command center ── */
function LegionTerminal(props: {
  show: boolean;
  logs: Record<string, string[]>;
  activeTab: string;
  setActiveTab: (t: string) => void;
  tabs: string[];
  version?: string;
  peers: Record<string, { id: string; name?: string }>;
  status: Record<string, string>;
  actions: Record<string, string>;
  tasks?: { goal: string; tasks: Array<{ id: string; title: string; agent?: string; status: string }> } | null;
  onClose: () => void;
  onOpen: () => void;
}) {
  const { tabs, peers, status, actions } = props;
  const agents = Object.keys(peers).sort();

  /* ── collapsed state: toggle pill on right edge ── */
  if (!props.show) {
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
        onClick={props.onOpen}
        title="展开指挥中心"
      >
        <svg className="w-4 h-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19l-7-7 7-7" />
        </svg>
        <span className="text-sm font-semibold tracking-wide">指挥中心</span>
      </div>,
      document.body
    );
  }

  /* ── expanded state: full right panel ── */
  const visibleLogs = props.logs[props.activeTab] || [];
  const TAB_LABELS: Record<string, string> = { all: "全部" };
  const STATUS_MAP: Record<string, string> = {
    online: "就绪", executing: "工作中", blocked: "阻塞", disconnected: "离线",
  };
  const DOT_COLORS: Record<string, string> = {
    online: "bg-emerald-500",
    executing: "bg-blue-500 animate-pulse",
    blocked: "bg-amber-500",
    disconnected: "bg-red-500 animate-pulse",
  };
  return createPortal(
    <div className="fixed right-0 top-0 h-full w-[340px]
                    bg-background border-l border-border
                    shadow-2xl z-[100] flex flex-col text-sm">
      {/* header */}
      <div className="flex items-center justify-between px-3 py-2.5
                      border-b border-border bg-muted/30 shrink-0">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
          <span className="text-xs font-semibold text-foreground/85 tracking-wide">
            军团指挥中心{props.version ? ` · v${props.version}` : ""}
          </span>
        </div>
        <button
          onClick={props.onClose}
          className="text-muted-foreground/60 hover:text-foreground px-1.5 py-0.5 rounded text-sm leading-none"
          title="关闭面板">✕</button>
      </div>

      {/* agent cards */}
      <div className="px-3 py-2.5 border-b border-border/30 bg-muted/10 shrink-0">
        <div className="text-[10px] text-muted-foreground/45 uppercase tracking-wider mb-2">
          🤖 团队状态
        </div>
        {agents.map((key: string) => {
          const peer = peers[key] || { id: key };
          const st = status[key] || "offline";
          const dot = DOT_COLORS[st] || "bg-red-500";
          const label = STATUS_MAP[st] || st;
          const action = actions[key] || "—";
          return (
            <div key={key}
              className="flex items-center gap-2 py-1.5 border-b border-border/10 last:border-0">
              <span
                className={`inline-block h-3 w-3 rounded-full ${dot} ring-1 ring-border/30 shrink-0`}
                title={label}
              />
              <span className="text-[11px] font-semibold text-foreground/80 w-16 shrink-0">
                {peer.name || key}
              </span>
              <span className="text-[10px] text-muted-foreground/65 truncate leading-relaxed">
                {action}
              </span>
            </div>
          );
        })}
      </div>

      {/* tab bar */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-border/30 bg-muted/10 shrink-0">
        <button
          onClick={() => props.setActiveTab("tasks")}
          className={`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors
            ${props.activeTab === "tasks"
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground/70 hover:text-foreground hover:bg-accent/30"}`}
        >
          📋 任务
        </button>
        <button
          onClick={() => props.setActiveTab("all")}
          className={`px-2.5 py-1 text-[11px] font-semibold rounded transition-colors
            ${props.activeTab !== "tasks"
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground/70 hover:text-foreground hover:bg-accent/30"}`}
        >
          📜 日志
        </button>
        {props.activeTab !== "tasks" && (
          <div className="flex items-center gap-0.5 ml-1 overflow-x-auto">
            {tabs.filter((t: string) => t !== "all" && t !== "tasks").map((tab: string) => {
              const isActive = props.activeTab === tab;
              const count = (props.logs[tab] || []).length;
              const label = TAB_LABELS[tab] || tab;
              return (
                <button key={tab}
                  onClick={() => props.setActiveTab(tab)}
                  className={`px-1.5 py-0.5 text-[10px] font-semibold rounded whitespace-nowrap transition-colors
                    ${isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-muted-foreground/55 hover:text-foreground hover:bg-accent/30"}`}
                >
                  {label}{count > 0 && <span className="ml-0.5 opacity-50">{count}</span>}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* content area */}
      <div className="flex-1 overflow-y-auto text-xs bg-background">
        {props.activeTab === "tasks" ? (
          props.tasks && props.tasks.tasks?.length > 0 ? (
            <div className="flex flex-col h-full">
              {/* Goal header */}
              {props.tasks.goal && (
                <div className="px-3 py-2 border-b border-border/20 bg-muted/5 shrink-0">
                  <div className="text-[10px] text-muted-foreground/40 uppercase tracking-wider mb-0.5">目标</div>
                  <div className="text-xs font-semibold text-foreground/90">{props.tasks.goal}</div>
                </div>
              )}
              {/* Progress bar */}
              <div className="px-3 py-2 border-b border-border/20 shrink-0">
                {(() => {
                  const total = props.tasks!.tasks.length;
                  const done = props.tasks!.tasks.filter(t => t.status === "done").length;
                  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
                  return (
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-[10px] text-muted-foreground/50">进度</span>
                        <span className="text-[10px] font-semibold text-foreground/70">{done}/{total} ({pct}%)</span>
                      </div>
                      <div className="h-1.5 bg-border/40 rounded-full overflow-hidden">
                        <div className={`h-full rounded-full transition-all duration-500 ${pct === 100 ? 'bg-emerald-500' : 'bg-blue-500'}`}
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </div>
                  );
                })()}
              </div>
              {/* Task list */}
              <div className="flex-1 overflow-y-auto px-2 py-1">
                {props.tasks.tasks.map((task: any, i: number) => {
                  const statusIcon: Record<string, string> = {
                    done: "✅", in_progress: "🔄", pending: "⬜", blocked: "🚫",
                  };
                  const statusColor: Record<string, string> = {
                    done: "text-emerald-500", in_progress: "text-blue-500",
                    pending: "text-muted-foreground/30", blocked: "text-amber-500",
                  };
                  const icon = statusIcon[task.status] || "❓";
                  const clr = statusColor[task.status] || "text-muted-foreground";
                  return (
                    <div key={task.id || i}
                      className="flex items-start gap-2 py-1.5 border-b border-border/10 last:border-0">
                      <span className={`text-[14px] ${clr} shrink-0 mt-px`}>{icon}</span>
                      <div className="flex-1 min-w-0">
                        <div className={`text-[11px] leading-tight ${task.status === 'done' ? 'text-muted-foreground/50 line-through' : 'text-foreground/80'}`}>
                          {task.title}
                        </div>
                        {task.agent && (
                          <span className="text-[9px] text-muted-foreground/40">{task.agent}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
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
              {props.activeTab === "all" ? "等待信号…" : `${TAB_LABELS[props.activeTab] || props.activeTab} 暂无日志`}
            </span>
          </div>
        ) : (
          <div className="p-2">
            {visibleLogs.map((log: string, i: number) => (
              <div key={i}
                className="py-0.5 border-b border-border/10 last:border-0 break-all
                           hover:bg-accent/5 transition-colors
                           font-mono text-[11px] leading-relaxed text-muted-foreground/80">
                {log}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}

export function Sidebar(props: SidebarProps) {
  const { t } = useTranslation();
  const [menuPortalContainer, setMenuPortalContainer] =
    useState<HTMLElement | null>(null);

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
    if (!client) return;
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
        if (tdata && tdata.tasks?.length > 0) setTaskData(tdata);

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

  const collapsed = Boolean(props.collapsed);
  const toggleLabel = t("thread.header.toggleSidebar");

  return (
    <nav
      ref={props.containActionMenus ? setMenuPortalContainer : undefined}
      aria-label={t("sidebar.navigation")}
      className={cn(
        "flex h-full w-full min-w-0 flex-col text-sidebar-foreground",
        props.hostChromeInset ? "bg-transparent" : "bg-sidebar",
        !props.hostChromeInset && "border-r border-sidebar-border/60",
      )}
    >
      <div
        className={cn(
          "flex items-center px-3 pb-2.5",
          props.hostChromeInset ? "pt-[2.85rem]" : "pt-3",
          collapsed ? "w-14 justify-start" : "justify-between",
        )}
      >
        <button
          type="button"
          aria-label={collapsed ? toggleLabel : undefined}
          aria-hidden={collapsed ? undefined : true}
          title={collapsed ? toggleLabel : undefined}
          onClick={collapsed ? props.onExpand : undefined}
          tabIndex={collapsed ? 0 : -1}
          className={cn(
            "flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-xl transition-colors",
            collapsed
              ? "-ml-0.5 hover:bg-sidebar-accent/75"
              : "pointer-events-none -ml-0.5",
          )}
        >
          <img
            src="/brand/nanobot_icon.png"
            alt=""
            className="h-8 w-8 select-none object-contain"
            draggable={false}
          />
        </button>
        {!collapsed && !props.hostChromeInset && (
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("sidebar.collapse")}
            onClick={props.onCollapse}
            className="h-7 w-7 rounded-lg text-muted-foreground/85 hover:bg-sidebar-accent/75 hover:text-sidebar-foreground"
          >
            <Menu className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      <LegionRoster peers={legionPeers} status={legionStatus} version={nanobotVersion} onToggleConsole={() => setShowConsole(v => !v)} />

      <div
        className={cn(
          "space-y-1.5 px-2 pb-2",
          collapsed && "flex w-14 flex-col items-center px-0",
        )}
      >
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.newChat")}
          onClick={props.onNewChat}
          icon={<SquarePen className="h-4 w-4" />}
          shortcut="Cmd/Ctrl+Shift+O"
        />
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.searchAria")}
          onClick={props.onOpenSearch}
          icon={<Search className="h-4 w-4" />}
        />
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.apps")}
          onClick={props.onOpenApps}
          active={props.activeUtility === "apps"}
          icon={<Blocks className="h-4 w-4" />}
        />
        {props.archivedCount ? (
          <SidebarActionButton
            collapsed={collapsed}
            label={props.showArchived ? t("chat.hideArchived") : t("chat.showArchived")}
            onClick={props.onToggleArchived}
            icon={<Archive className="h-4 w-4" />}
          />
        ) : null}
      </div>
      <div
        className={cn(
          "flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden transition-opacity duration-200",
          collapsed && "pointer-events-none opacity-0",
        )}
      >
        {!collapsed && (
          <ChatList
            sessions={props.sessions}
            activeKey={props.activeKey}
            loading={props.loading}
            emptyLabel={t("chat.noSessions")}
            onSelect={props.onSelect}
            onRequestDelete={props.onRequestDelete}
            onTogglePin={props.onTogglePin}
            onRequestRename={props.onRequestRename}
            onToggleArchive={props.onToggleArchive}
            onToggleGroup={props.onToggleGroup}
            onRequestRenameProject={props.onRequestRenameProject}
            onNewChatInProject={props.onNewChatInProject}
            pinnedKeys={props.pinnedKeys}
            archivedKeys={props.archivedKeys}
            titleOverrides={props.titleOverrides}
            projectNameOverrides={props.projectNameOverrides}
            collapsedGroups={props.collapsedGroups}
            runningChatIds={props.runningChatIds}
            completedChatIds={props.completedChatIds}
            density={props.viewState?.density}
            showPreviews={props.viewState?.show_previews}
            showTimestamps={props.viewState?.show_timestamps}
            sort={props.viewState?.sort}
            showArchived={props.showArchived}
            defaultWorkspacePath={props.defaultWorkspacePath}
            actionMenuPortalContainer={
              props.containActionMenus ? menuPortalContainer : undefined
            }
          />
        )}
      </div>
      <Separator className="bg-sidebar-border/50" />
      <div
        className={cn(
          "flex items-center gap-1 px-2.5 py-2.5 text-xs",
          collapsed && "w-14 flex-col px-0",
        )}
      >
        <SidebarActionButton
          collapsed={collapsed}
          label={t("sidebar.settings")}
          onClick={props.onOpenSettings}
          className={collapsed ? undefined : "flex-1"}
          icon={<Settings className="h-4 w-4" />}
        />
        <ConnectionBadge />
      </div>

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

    </nav>
  );
}

function SidebarActionButton({
  collapsed,
  label,
  icon,
  onClick,
  active = false,
  className,
  shortcut,
}: {
  collapsed: boolean;
  label: string;
  icon: ReactNode;
  onClick: () => void;
  active?: boolean;
  className?: string;
  shortcut?: string;
}) {
  const title = shortcut ? `${label} (${shortcut})` : collapsed ? label : undefined;

  return (
    <Button
      type="button"
      variant="ghost"
      aria-label={label}
      aria-current={active ? "page" : undefined}
      title={title}
      onClick={() => onClick()}
      className={cn(
        "group h-8 min-w-0 gap-2 overflow-hidden rounded-full font-medium text-sidebar-foreground/85 hover:bg-sidebar-accent/75 hover:text-sidebar-foreground",
        "transition-[width,padding,border-radius,color,background-color] duration-300 ease-out",
        collapsed
          ? "w-9 justify-center gap-0 rounded-xl px-0"
          : "w-full justify-start gap-2 px-3 text-[12.5px]",
        active && "bg-sidebar-accent text-sidebar-foreground shadow-[inset_0_0_0_1px_hsl(var(--sidebar-border)/0.55)]",
        className,
      )}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center transition-transform duration-300 ease-out",
          collapsed ? "translate-x-0" : "translate-x-0",
        )}
        aria-hidden
      >
        {icon}
      </span>
      <span
        className={cn(
          "min-w-0 overflow-hidden truncate whitespace-nowrap transition-[max-width,opacity,transform] duration-200 ease-out",
          collapsed
            ? "max-w-0 -translate-x-1 opacity-0"
            : "max-w-[12rem] translate-x-0 opacity-100",
        )}
      >
        {label}
      </span>
    </Button>
  );
}
