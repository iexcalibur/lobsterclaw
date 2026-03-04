"use client";

import { useEffect, useState } from "react";
import {
  Terminal,
  Globe,
  Brain,
  MessageSquare,
  GitBranch,
  Clock,
  FileText,
  Settings,
  Search,
  ShieldAlert,
  ShieldCheck,
  Lock,
  AlertTriangle,
  HardDrive,
  Chrome,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

interface Tool {
  name: string;
  description: string;
  owner_only: boolean;
  depth_limit: number | null;
  denied: boolean;
  allowed: boolean;
  requires_confirmation: boolean;
}

type CategoryKey =
  | "machine"
  | "browser"
  | "memory"
  | "communication"
  | "agents"
  | "scheduling"
  | "media"
  | "system";

interface Category {
  key: CategoryKey;
  label: string;
  description: string;
  icon: typeof Terminal;
  color: string;
  bgColor: string;
  borderColor: string;
  names: string[];
}

const CATEGORIES: Category[] = [
  {
    key: "machine",
    label: "Local Machine",
    description: "Shell commands, file read/write, directory access",
    icon: HardDrive,
    color: "text-orange-400",
    bgColor: "bg-orange-900/30",
    borderColor: "border-orange-800/40",
    names: [
      "exec",
      "process",
      "read",
      "write",
      "edit",
      "apply_patch",
      "delete",
      "move",
      "list_dir",
      "glob",
    ],
  },
  {
    key: "browser",
    label: "Browser & Web",
    description: "Playwright automation, web fetch, search",
    icon: Chrome,
    color: "text-blue-400",
    bgColor: "bg-blue-900/30",
    borderColor: "border-blue-800/40",
    names: ["browser", "web_fetch", "web_search"],
  },
  {
    key: "memory",
    label: "Memory",
    description: "Long-term memory storage, search, recall",
    icon: Brain,
    color: "text-purple-400",
    bgColor: "bg-purple-900/30",
    borderColor: "border-purple-800/40",
    names: [
      "memory_search",
      "memory_get",
      "memory_write",
      "memory_list",
      "memory_delete",
    ],
  },
  {
    key: "communication",
    label: "Communication",
    description: "Telegram, Discord, Slack, WhatsApp, voice",
    icon: MessageSquare,
    color: "text-emerald-400",
    bgColor: "bg-emerald-900/30",
    borderColor: "border-emerald-800/40",
    names: ["message", "discord", "slack", "whatsapp", "tts"],
  },
  {
    key: "agents",
    label: "Agents & Sessions",
    description: "Sub-agents, session management, multi-agent orchestration",
    icon: GitBranch,
    color: "text-cyan-400",
    bgColor: "bg-cyan-900/30",
    borderColor: "border-cyan-800/40",
    names: [
      "sessions_spawn",
      "sessions_list",
      "sessions_history",
      "sessions_send",
      "session_status",
      "subagents",
      "agents_list",
    ],
  },
  {
    key: "scheduling",
    label: "Scheduling",
    description: "Cron jobs, reminders, timed tasks",
    icon: Clock,
    color: "text-yellow-400",
    bgColor: "bg-yellow-900/30",
    borderColor: "border-yellow-800/40",
    names: ["cron"],
  },
  {
    key: "media",
    label: "Media & Files",
    description: "PDF reading, image analysis",
    icon: FileText,
    color: "text-pink-400",
    bgColor: "bg-pink-900/30",
    borderColor: "border-pink-800/40",
    names: ["pdf", "image"],
  },
  {
    key: "system",
    label: "System & Infra",
    description: "Gateway control, canvas UI, remote nodes",
    icon: Settings,
    color: "text-zinc-400",
    bgColor: "bg-zinc-800/50",
    borderColor: "border-zinc-700/40",
    names: ["gateway", "canvas", "nodes"],
  },
];

function StatusIcon({ tool }: { tool: Tool }) {
  if (tool.denied)
    return <ShieldAlert className="h-3.5 w-3.5 text-red-400" />;
  if (tool.requires_confirmation)
    return <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />;
  if (tool.owner_only)
    return <Lock className="h-3.5 w-3.5 text-yellow-400" />;
  return <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />;
}

function StatusBadge({ tool }: { tool: Tool }) {
  if (tool.denied)
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-950/60 border border-red-800/40 px-2 py-0.5 text-[10px] font-medium text-red-400">
        Blocked
      </span>
    );
  if (tool.requires_confirmation)
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-950/60 border border-amber-800/40 px-2 py-0.5 text-[10px] font-medium text-amber-400">
        Confirm
      </span>
    );
  if (tool.owner_only)
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-yellow-950/60 border border-yellow-800/40 px-2 py-0.5 text-[10px] font-medium text-yellow-400">
        Owner
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-950/60 border border-emerald-800/40 px-2 py-0.5 text-[10px] font-medium text-emerald-400">
      Active
    </span>
  );
}

export default function ToolsPage() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await api<{ tools: Tool[]; total: number }>(
          "/api/gateway/tools"
        );
        setTools(res.tools);
      } catch {
        /* retry */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const toolMap = Object.fromEntries(tools.map((t) => [t.name, t]));
  const categorized = new Set(CATEGORIES.flatMap((c) => c.names));
  const uncategorized = tools.filter((t) => !categorized.has(t.name));

  const matchesSearch = (t: Tool) =>
    !search || t.name.toLowerCase().includes(search.toLowerCase());

  const deniedCount = tools.filter((t) => t.denied).length;
  const activeCount = tools.filter((t) => !t.denied && t.allowed).length;
  const confirmCount = tools.filter((t) => t.requires_confirmation).length;

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Tools</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {tools.length} registered &middot;{" "}
          <span className="text-emerald-400">{activeCount} active</span>{" "}
          &middot;{" "}
          <span className="text-red-400">{deniedCount} blocked</span>{" "}
          &middot;{" "}
          <span className="text-amber-400">{confirmCount} need approval</span>
        </p>
      </div>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
        <input
          type="text"
          placeholder="Search tools..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="input pl-9"
        />
      </div>

      {loading ? (
        <div className="card text-center text-zinc-500 text-sm py-12">
          Loading tools...
        </div>
      ) : (
        <div className="space-y-6">
          {CATEGORIES.map((cat) => {
            const catTools = cat.names
              .map((n) => toolMap[n])
              .filter((t): t is Tool => !!t && matchesSearch(t));

            if (catTools.length === 0) return null;

            const Icon = cat.icon;
            const catActive = catTools.filter((t) => !t.denied && t.allowed).length;
            const catBlocked = catTools.filter((t) => t.denied).length;

            return (
              <div key={cat.key}>
                {/* Category header */}
                <div className="flex items-center gap-3 mb-3">
                  <div
                    className={clsx(
                      "h-9 w-9 rounded-lg flex items-center justify-center",
                      cat.bgColor
                    )}
                  >
                    <Icon className={clsx("h-4.5 w-4.5", cat.color)} />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-semibold text-white">
                        {cat.label}
                      </h2>
                      <span className="text-xs text-zinc-600">
                        {catActive}/{catTools.length} active
                        {catBlocked > 0 && (
                          <span className="text-red-500 ml-1">
                            ({catBlocked} blocked)
                          </span>
                        )}
                      </span>
                    </div>
                    <p className="text-xs text-zinc-500">{cat.description}</p>
                  </div>
                </div>

                {/* Tool cards grid */}
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {catTools.map((tool) => {
                    const isExpanded = expanded === tool.name;
                    return (
                      <button
                        key={tool.name}
                        onClick={() =>
                          setExpanded(isExpanded ? null : tool.name)
                        }
                        className={clsx(
                          "text-left rounded-xl border p-3.5 transition-all duration-200",
                          "hover:border-zinc-600 hover:bg-zinc-800/80",
                          tool.denied
                            ? "border-red-900/30 bg-red-950/10 opacity-70"
                            : clsx("bg-zinc-900/50", cat.borderColor),
                          isExpanded && "ring-1 ring-zinc-600"
                        )}
                      >
                        <div className="flex items-center justify-between mb-1.5">
                          <span className="text-sm font-mono font-medium text-white">
                            {tool.name}
                          </span>
                          <StatusIcon tool={tool} />
                        </div>

                        <div className="flex flex-wrap gap-1 mb-2">
                          <StatusBadge tool={tool} />
                          {tool.depth_limit != null && (
                            <span className="inline-flex items-center rounded-full bg-zinc-800 border border-zinc-700/40 px-2 py-0.5 text-[10px] font-medium text-zinc-400">
                              depth&le;{tool.depth_limit}
                            </span>
                          )}
                        </div>

                        <p
                          className={clsx(
                            "text-[11px] text-zinc-500 leading-relaxed",
                            !isExpanded && "line-clamp-2"
                          )}
                        >
                          {tool.description.split("\n")[0]}
                        </p>

                        {isExpanded && (
                          <div className="mt-3 pt-2.5 border-t border-zinc-800 text-xs text-zinc-400 space-y-1.5">
                            <p className="whitespace-pre-wrap leading-relaxed text-zinc-500">
                              {tool.description}
                            </p>
                            <div className="grid grid-cols-2 gap-x-3 gap-y-1 pt-1.5">
                              <div className="flex justify-between">
                                <span className="text-zinc-600">Allowed</span>
                                <span
                                  className={
                                    tool.allowed
                                      ? "text-emerald-400"
                                      : "text-red-400"
                                  }
                                >
                                  {tool.allowed ? "Yes" : "No"}
                                </span>
                              </div>
                              <div className="flex justify-between">
                                <span className="text-zinc-600">Owner Only</span>
                                <span>{tool.owner_only ? "Yes" : "No"}</span>
                              </div>
                            </div>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {/* Uncategorized tools (if any) */}
          {uncategorized.filter(matchesSearch).length > 0 && (
            <div>
              <div className="flex items-center gap-3 mb-3">
                <div className="h-9 w-9 rounded-lg bg-zinc-800/50 flex items-center justify-center">
                  <Settings className="h-4.5 w-4.5 text-zinc-500" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-white">Other</h2>
                  <p className="text-xs text-zinc-500">
                    Uncategorized tools
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {uncategorized.filter(matchesSearch).map((tool) => {
                  const isExpanded = expanded === tool.name;
                  return (
                    <button
                      key={tool.name}
                      onClick={() =>
                        setExpanded(isExpanded ? null : tool.name)
                      }
                      className={clsx(
                        "text-left rounded-xl border p-3.5 transition-all duration-200",
                        "hover:border-zinc-600 hover:bg-zinc-800/80",
                        tool.denied
                          ? "border-red-900/30 bg-red-950/10 opacity-70"
                          : "border-zinc-800 bg-zinc-900/50",
                        isExpanded && "ring-1 ring-zinc-600"
                      )}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="text-sm font-mono font-medium text-white">
                          {tool.name}
                        </span>
                        <StatusIcon tool={tool} />
                      </div>
                      <div className="flex flex-wrap gap-1 mb-2">
                        <StatusBadge tool={tool} />
                      </div>
                      <p
                        className={clsx(
                          "text-[11px] text-zinc-500 leading-relaxed",
                          !isExpanded && "line-clamp-2"
                        )}
                      >
                        {tool.description.split("\n")[0]}
                      </p>
                      {isExpanded && (
                        <div className="mt-3 pt-2.5 border-t border-zinc-800 text-xs text-zinc-400">
                          <p className="whitespace-pre-wrap leading-relaxed text-zinc-500">
                            {tool.description}
                          </p>
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
