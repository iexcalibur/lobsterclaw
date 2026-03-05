"use client";

import { useEffect, useState } from "react";
import {
  Bot,
  ChevronRight,
  Circle,
  GitBranch,
  AlertCircle,
  CheckCircle,
  X,
} from "lucide-react";
import clsx from "clsx";
import { api, formatTokens, timeAgo } from "@/lib/api";
import MarkdownMessage from "@/components/MarkdownMessage";

interface Session {
  id: string;
  label: string;
  parent_id: string | null;
  status: string;
  model: string;
  depth: number;
  token_usage: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  updated_at: string;
  error: string | null;
}

interface SessionDetail {
  session: Session;
  messages: { role: string; content: string }[];
  children: { id: string; label: string; status: string; depth: number }[];
}

const STATUS_STYLES: Record<string, string> = {
  active: "badge-green",
  completed: "badge-blue",
  error: "badge-red",
  cancelled: "badge-zinc",
};

const STATUS_ICONS: Record<string, typeof Circle> = {
  active: Circle,
  completed: CheckCircle,
  error: AlertCircle,
  cancelled: X,
};

export default function AgentsPage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<SessionDetail | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchSessions = async () => {
    try {
      const res = await api<{ sessions: Session[] }>("/api/gateway/sessions");
      setSessions(res.sessions);
    } catch {
      /* retry */
    } finally {
      setLoading(false);
    }
  };

  const selectSession = async (id: string) => {
    try {
      const detail = await api<SessionDetail>(
        `/api/gateway/sessions/${id}`
      );
      setSelected(detail);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    fetchSessions();
    const id = setInterval(fetchSessions, 10000);
    return () => clearInterval(id);
  }, []);

  const mainSessions = sessions.filter((s) => !s.parent_id);
  const subSessions = sessions.filter((s) => s.parent_id);

  // Build tree: parent_id -> children
  const childrenMap = new Map<string, Session[]>();
  for (const s of subSessions) {
    const list = childrenMap.get(s.parent_id!) || [];
    list.push(s);
    childrenMap.set(s.parent_id!, list);
  }

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Agent Sessions</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {sessions.length} sessions &middot;{" "}
          {sessions.filter((s) => s.status === "active").length} active
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        {/* Session List */}
        <div className="lg:col-span-2 space-y-2">
          {loading ? (
            <div className="card text-center text-zinc-500 text-sm py-12">
              Loading sessions...
            </div>
          ) : sessions.length === 0 ? (
            <div className="card text-center text-zinc-500 text-sm py-12">
              No sessions found
            </div>
          ) : (
            <>
              {/* Main sessions */}
              {mainSessions.map((s) => (
                <div key={s.id}>
                  <SessionRow
                    session={s}
                    selected={selected?.session.id === s.id}
                    onClick={() => selectSession(s.id)}
                  />
                  {/* Sub-agents */}
                  {childrenMap.get(s.id)?.map((child) => (
                    <div key={child.id} className="ml-6 mt-1">
                      <SessionRow
                        session={child}
                        selected={selected?.session.id === child.id}
                        onClick={() => selectSession(child.id)}
                        isChild
                      />
                    </div>
                  ))}
                </div>
              ))}
              {/* Orphan sub-sessions (parent not in list) */}
              {subSessions
                .filter((s) => !mainSessions.some((m) => m.id === s.parent_id))
                .map((s) => (
                  <div key={s.id} className="ml-6">
                    <SessionRow
                      session={s}
                      selected={selected?.session.id === s.id}
                      onClick={() => selectSession(s.id)}
                      isChild
                    />
                  </div>
                ))}
            </>
          )}
        </div>

        {/* Session Detail */}
        <div className="lg:col-span-3">
          {selected ? (
            <div className="card space-y-5">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold">
                    {selected.session.label}
                  </h2>
                  <p className="text-xs text-zinc-500 font-mono">
                    {selected.session.id}
                  </p>
                </div>
                <span className={STATUS_STYLES[selected.session.status] ?? "badge-zinc"}>
                  {selected.session.status}
                </span>
              </div>

              {/* Meta grid */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <p className="text-zinc-500">Model</p>
                  <p className="font-mono text-zinc-300">
                    {selected.session.model || "default"}
                  </p>
                </div>
                <div>
                  <p className="text-zinc-500">Depth</p>
                  <p className="text-zinc-300">{selected.session.depth}</p>
                </div>
                <div>
                  <p className="text-zinc-500">Tokens</p>
                  <p className="text-zinc-300">
                    {formatTokens(selected.session.token_usage)} total
                  </p>
                </div>
                <div>
                  <p className="text-zinc-500">Updated</p>
                  <p className="text-zinc-300">
                    {timeAgo(selected.session.updated_at)}
                  </p>
                </div>
              </div>

              {/* Token breakdown */}
              <div>
                <p className="text-xs text-zinc-500 mb-2">Token Breakdown</p>
                <div className="flex gap-2 h-3 rounded-full overflow-hidden bg-zinc-800">
                  {selected.session.token_usage > 0 && (
                    <>
                      <div
                        className="bg-blue-500 rounded-full"
                        style={{
                          width: `${(selected.session.input_tokens / selected.session.token_usage) * 100}%`,
                        }}
                      />
                      <div
                        className="bg-emerald-500 rounded-full"
                        style={{
                          width: `${(selected.session.output_tokens / selected.session.token_usage) * 100}%`,
                        }}
                      />
                    </>
                  )}
                </div>
                <div className="flex justify-between mt-1 text-xs text-zinc-500">
                  <span>In: {formatTokens(selected.session.input_tokens)}</span>
                  <span>Out: {formatTokens(selected.session.output_tokens)}</span>
                </div>
              </div>

              {/* Children */}
              {selected.children.length > 0 && (
                <div>
                  <p className="text-xs text-zinc-500 mb-2 flex items-center gap-1">
                    <GitBranch className="h-3 w-3" />
                    Sub-agents ({selected.children.length})
                  </p>
                  <div className="space-y-1">
                    {selected.children.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => selectSession(c.id)}
                        className="flex w-full items-center justify-between rounded-lg bg-zinc-800/50 px-3 py-2 text-sm hover:bg-zinc-800 transition-colors"
                      >
                        <span className="text-zinc-300">{c.label}</span>
                        <span className={STATUS_STYLES[c.status] ?? "badge-zinc"}>
                          {c.status}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Recent Messages */}
              <div>
                <p className="text-xs text-zinc-500 mb-2">
                  Recent Messages ({selected.messages.length})
                </p>
                <div className="max-h-80 overflow-y-auto space-y-2 pr-1">
                  {selected.messages.length === 0 ? (
                    <p className="text-xs text-zinc-600 text-center py-4">
                      No messages
                    </p>
                  ) : (
                    selected.messages.map((msg, i) => (
                      <div
                        key={i}
                        className={clsx(
                          "rounded-lg px-3 py-2 text-sm",
                          msg.role === "user"
                            ? "bg-blue-500/10 border border-blue-500/20"
                            : msg.role === "assistant"
                            ? "bg-zinc-800"
                            : "bg-zinc-800/50 text-zinc-500"
                        )}
                      >
                        <p className="text-[10px] font-medium text-zinc-500 uppercase mb-1">
                          {msg.role}
                        </p>
                        <MarkdownMessage content={msg.content} className="text-zinc-300 line-clamp-6" />
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* Error */}
              {selected.session.error && (
                <div className="rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2">
                  <p className="text-xs font-medium text-red-400 mb-1">Error</p>
                  <p className="text-sm text-red-300">{selected.session.error}</p>
                </div>
              )}
            </div>
          ) : (
            <div className="card flex items-center justify-center py-20 text-zinc-600 text-sm">
              Select a session to view details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SessionRow({
  session,
  selected,
  onClick,
  isChild,
}: {
  session: Session;
  selected: boolean;
  onClick: () => void;
  isChild?: boolean;
}) {
  const StatusIcon = STATUS_ICONS[session.status] ?? Circle;
  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full flex items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
        selected
          ? "bg-zinc-800 ring-1 ring-zinc-700"
          : "hover:bg-zinc-800/60"
      )}
    >
      {isChild && (
        <GitBranch className="h-3.5 w-3.5 text-zinc-600 flex-shrink-0" />
      )}
      <StatusIcon
        className={clsx(
          "h-3.5 w-3.5 flex-shrink-0",
          session.status === "active"
            ? "text-emerald-400"
            : session.status === "error"
            ? "text-red-400"
            : "text-zinc-500"
        )}
      />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-zinc-300 truncate">
          {session.label}
        </p>
        <p className="text-[10px] text-zinc-600 font-mono">{session.id}</p>
      </div>
      <div className="text-right flex-shrink-0">
        <p className="text-xs text-zinc-500">
          {formatTokens(session.token_usage)}
        </p>
        <p className="text-[10px] text-zinc-600">
          {timeAgo(session.updated_at)}
        </p>
      </div>
      <ChevronRight className="h-3.5 w-3.5 text-zinc-600 flex-shrink-0" />
    </button>
  );
}
