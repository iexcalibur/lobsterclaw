"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  Clock,
  Wrench,
  Zap,
  Cpu,
  ArrowUpRight,
  ArrowDownRight,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import StatusCard from "@/components/status-card";
import { api, formatUptime, formatTokens } from "@/lib/api";

interface StatusData {
  agent_id: string;
  provider: string;
  model: string;
  uptime_seconds: number;
  components: Record<string, boolean>;
  counts: Record<string, number>;
  ws_subscribers: number;
}

interface MetricsData {
  main_session: { input_tokens: number; output_tokens: number; total_tokens: number };
  all_sessions: { input_tokens: number; output_tokens: number; total_tokens: number; session_count: number };
}

const COMPONENT_LABELS: Record<string, string> = {
  cron: "Cron Scheduler",
  heartbeat: "Heartbeat",
  memory: "Long-term Memory",
  browser: "Browser Tool",
  canvas: "Canvas Host",
  streaming: "LLM Streaming",
  subagents: "Sub-agents",
  exec: "Shell Execution",
  tts: "Text-to-Speech",
};

const PIE_COLORS = ["#3b82f6", "#22c55e"];

export default function DashboardPage() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const [s, m] = await Promise.all([
        api<StatusData>("/api/gateway/status"),
        api<MetricsData>("/api/gateway/metrics").catch(() => null),
      ]);
      setStatus(s);
      if (m && m.all_sessions) setMetrics(m);
    } catch {
      /* retry next interval */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 8000);
    return () => clearInterval(id);
  }, []);

  if (loading || !status) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-500 text-sm">Connecting to gateway...</div>
      </div>
    );
  }

  const tokenData = metrics
    ? [
        { name: "Input", tokens: metrics.all_sessions.input_tokens },
        { name: "Output", tokens: metrics.all_sessions.output_tokens },
      ]
    : [];

  const pieData = metrics
    ? [
        { name: "Input", value: metrics.all_sessions.input_tokens || 1 },
        { name: "Output", value: metrics.all_sessions.output_tokens || 1 },
      ]
    : [];

  const enabledComponents = Object.entries(status.components).filter(([, v]) => v);
  const disabledComponents = Object.entries(status.components).filter(([, v]) => !v);

  return (
    <div className="space-y-6 fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Mission Control</h1>
          <p className="text-sm text-zinc-500 mt-1">
            {status.provider}/{status.model} &middot; Agent {status.agent_id}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="h-2 w-2 rounded-full bg-emerald-400 pulse-dot" />
          <span className="text-sm text-zinc-400">
            Uptime {formatUptime(status.uptime_seconds)}
          </span>
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatusCard
          title="Active Sessions"
          value={status.counts.active_sessions}
          subtitle={`${status.counts.sessions} total`}
          icon={Bot}
          color="blue"
        />
        <StatusCard
          title="Cron Jobs"
          value={status.counts.cron_active}
          subtitle={`${status.counts.cron_total} total`}
          icon={Clock}
          color="green"
        />
        <StatusCard
          title="Tools"
          value={status.counts.tools}
          subtitle="registered"
          icon={Wrench}
          color="purple"
        />
        <StatusCard
          title="Skills"
          value={status.counts.skills}
          subtitle="loaded"
          icon={Zap}
          color="yellow"
        />
        <StatusCard
          title="Total Tokens"
          value={formatTokens(metrics?.all_sessions.total_tokens ?? 0)}
          subtitle={`${metrics?.all_sessions.session_count ?? 0} sessions`}
          icon={Cpu}
          color="cyan"
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Token Usage Bar Chart */}
        <div className="card">
          <h2 className="text-sm font-medium text-zinc-400 mb-4">Token Usage</h2>
          {metrics && metrics.all_sessions.total_tokens > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={tokenData} barSize={48}>
                <XAxis
                  dataKey="name"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "#a1a1aa", fontSize: 12 }}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: "#71717a", fontSize: 11 }}
                  tickFormatter={(v) => formatTokens(v)}
                />
                <Tooltip
                  contentStyle={{
                    background: "#18181b",
                    border: "1px solid #27272a",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(v: number) => [formatTokens(v), "Tokens"]}
                />
                <Bar dataKey="tokens" radius={[6, 6, 0, 0]}>
                  {tokenData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex flex-col items-center justify-center h-[200px] text-zinc-600 text-sm gap-2">
              <Cpu className="h-8 w-8 text-zinc-700" />
              <p>Send a message to start tracking tokens</p>
              <a href="/chat" className="text-xs text-blue-400 hover:underline">Open Chat</a>
            </div>
          )}
        </div>

        {/* Token Distribution Pie */}
        <div className="card">
          <h2 className="text-sm font-medium text-zinc-400 mb-4">
            Token Distribution
          </h2>
          {metrics && metrics.all_sessions.total_tokens > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width={160} height={160}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={70}
                    paddingAngle={4}
                    dataKey="value"
                  >
                    {pieData.map((_, i) => (
                      <Cell key={i} fill={PIE_COLORS[i]} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <div className="h-3 w-3 rounded-full bg-blue-500" />
                  <span className="text-sm text-zinc-300">
                    Input: {formatTokens(metrics.all_sessions.input_tokens)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="h-3 w-3 rounded-full bg-emerald-500" />
                  <span className="text-sm text-zinc-300">
                    Output: {formatTokens(metrics.all_sessions.output_tokens)}
                  </span>
                </div>
                <div className="mt-2 text-xs text-zinc-500">
                  Main session: {formatTokens(metrics.main_session.total_tokens)} tokens
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-[160px] text-zinc-600 text-sm gap-2">
              <Activity className="h-8 w-8 text-zinc-700" />
              <p>Token distribution appears after first conversation</p>
            </div>
          )}
        </div>
      </div>

      {/* Component Status */}
      <div className="card">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">
          Component Status
        </h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {enabledComponents.map(([key]) => (
            <div
              key={key}
              className="flex items-center gap-2.5 rounded-lg bg-zinc-800/50 px-3 py-2.5"
            >
              <div className="h-2 w-2 rounded-full bg-emerald-400" />
              <span className="text-sm text-zinc-300">
                {COMPONENT_LABELS[key] ?? key}
              </span>
            </div>
          ))}
          {disabledComponents.map(([key]) => (
            <div
              key={key}
              className="flex items-center gap-2.5 rounded-lg bg-zinc-800/30 px-3 py-2.5 opacity-50"
            >
              <div className="h-2 w-2 rounded-full bg-zinc-600" />
              <span className="text-sm text-zinc-500">
                {COMPONENT_LABELS[key] ?? key}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Quick Info */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="text-sm font-medium text-zinc-400 mb-3">
            System Info
          </h2>
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-zinc-500">Provider</span>
              <span className="font-mono text-zinc-300">{status.provider}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">Model</span>
              <span className="font-mono text-zinc-300">{status.model}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">Agent ID</span>
              <span className="font-mono text-zinc-300">{status.agent_id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-zinc-500">WebSocket Clients</span>
              <span className="font-mono text-zinc-300">
                {status.ws_subscribers}
              </span>
            </div>
          </div>
        </div>

        <div className="card">
          <h2 className="text-sm font-medium text-zinc-400 mb-3">
            Quick Actions
          </h2>
          <div className="grid grid-cols-2 gap-2">
            <a
              href="/chat"
              className="flex items-center gap-2 rounded-lg bg-zinc-800/50 px-3 py-2.5 text-sm text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <ArrowUpRight className="h-3.5 w-3.5 text-blue-400" />
              Open Chat
            </a>
            <a
              href="/agents"
              className="flex items-center gap-2 rounded-lg bg-zinc-800/50 px-3 py-2.5 text-sm text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <ArrowUpRight className="h-3.5 w-3.5 text-purple-400" />
              View Agents
            </a>
            <a
              href="/cron"
              className="flex items-center gap-2 rounded-lg bg-zinc-800/50 px-3 py-2.5 text-sm text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />
              Manage Cron
            </a>
            <a
              href="/tools"
              className="flex items-center gap-2 rounded-lg bg-zinc-800/50 px-3 py-2.5 text-sm text-zinc-300 hover:bg-zinc-800 transition-colors"
            >
              <ArrowUpRight className="h-3.5 w-3.5 text-yellow-400" />
              Browse Tools
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
