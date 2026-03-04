"use client";

import { useEffect, useState } from "react";
import {
  Wrench,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Search,
  ChevronDown,
  ChevronUp,
  Lock,
  AlertTriangle,
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

export default function ToolsPage() {
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "allowed" | "denied">("all");
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

  const filtered = tools.filter((t) => {
    if (search && !t.name.toLowerCase().includes(search.toLowerCase())) {
      return false;
    }
    if (filter === "allowed") return t.allowed && !t.denied;
    if (filter === "denied") return t.denied;
    return true;
  });

  const deniedCount = tools.filter((t) => t.denied).length;
  const ownerOnlyCount = tools.filter((t) => t.owner_only).length;
  const confirmCount = tools.filter((t) => t.requires_confirmation).length;

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Tools</h1>
        <p className="text-sm text-zinc-500 mt-1">
          {tools.length} tools &middot; {deniedCount} denied &middot;{" "}
          {ownerOnlyCount} owner-only &middot; {confirmCount} require
          confirmation
        </p>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-500" />
          <input
            type="text"
            placeholder="Search tools..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input pl-9"
          />
        </div>
        <div className="flex gap-1">
          {(["all", "allowed", "denied"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={clsx(
                "btn text-xs capitalize",
                filter === f
                  ? "bg-zinc-800 text-white"
                  : "text-zinc-500 hover:text-white"
              )}
            >
              {f}
            </button>
          ))}
        </div>
      </div>

      {/* Tool List */}
      {loading ? (
        <div className="card text-center text-zinc-500 text-sm py-12">
          Loading tools...
        </div>
      ) : filtered.length === 0 ? (
        <div className="card text-center py-12 text-zinc-500 text-sm">
          No tools match your filter
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((tool) => (
            <div key={tool.name} className="card-hover">
              <button
                onClick={() =>
                  setExpanded(expanded === tool.name ? null : tool.name)
                }
                className="w-full text-left"
              >
                <div className="flex items-center gap-3">
                  {/* Status icon */}
                  {tool.denied ? (
                    <ShieldAlert className="h-4 w-4 text-red-400 flex-shrink-0" />
                  ) : tool.owner_only ? (
                    <Lock className="h-4 w-4 text-yellow-400 flex-shrink-0" />
                  ) : tool.requires_confirmation ? (
                    <AlertTriangle className="h-4 w-4 text-amber-400 flex-shrink-0" />
                  ) : (
                    <ShieldCheck className="h-4 w-4 text-emerald-400 flex-shrink-0" />
                  )}

                  {/* Name + badges */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-mono font-medium text-white">
                        {tool.name}
                      </span>
                      {tool.denied && (
                        <span className="badge-red text-[10px]">denied</span>
                      )}
                      {tool.owner_only && (
                        <span className="badge-yellow text-[10px]">
                          owner-only
                        </span>
                      )}
                      {tool.requires_confirmation && (
                        <span className="badge-purple text-[10px]">
                          confirm
                        </span>
                      )}
                      {tool.depth_limit != null && (
                        <span className="badge-zinc text-[10px]">
                          depth≤{tool.depth_limit}
                        </span>
                      )}
                    </div>
                  </div>

                  {expanded === tool.name ? (
                    <ChevronUp className="h-4 w-4 text-zinc-500" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-zinc-500" />
                  )}
                </div>
              </button>

              {expanded === tool.name && (
                <div className="mt-3 pt-3 border-t border-zinc-800">
                  <p className="text-sm text-zinc-400 whitespace-pre-wrap leading-relaxed">
                    {tool.description}
                  </p>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
                    <div>
                      <p className="text-zinc-600">Allowed</p>
                      <p
                        className={
                          tool.allowed ? "text-emerald-400" : "text-red-400"
                        }
                      >
                        {tool.allowed ? "Yes" : "No"}
                      </p>
                    </div>
                    <div>
                      <p className="text-zinc-600">Owner Only</p>
                      <p className="text-zinc-400">
                        {tool.owner_only ? "Yes" : "No"}
                      </p>
                    </div>
                    <div>
                      <p className="text-zinc-600">Depth Limit</p>
                      <p className="text-zinc-400">
                        {tool.depth_limit != null
                          ? tool.depth_limit
                          : "None"}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
