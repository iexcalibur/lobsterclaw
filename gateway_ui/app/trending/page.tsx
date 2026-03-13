"use client";

import { useCallback, useEffect, useState } from "react";
import {
  TrendingUp,
  RefreshCw,
  Trash2,
  ExternalLink,
  Clock,
  Flame,
  ToggleLeft,
  ToggleRight,
  MessageCircle,
  ArrowUp,
  Hash,
} from "lucide-react";
import clsx from "clsx";
import { api, postJSON } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────

interface TopicCard {
  id: number;
  topic: string;
  rank: number;
  title: string;
  summary: string;
  sources: string[];
  why_trending: string;
  created_at: number;
}

interface Signal {
  id: number;
  topic: string;
  tier: string;
  source: string;
  title: string;
  url: string;
  summary: string;
  score: number;
  metadata: string;
  found_at: number;
}

interface TrendingStats {
  total_signals: number;
  by_tier: Record<string, number>;
  topic_cards: number;
}

interface TrendingSettings {
  auto_refresh_enabled: boolean;
  auto_top3_enabled: boolean;
  active_topic: string;
  available_topics: string[];
}

// ── Constants ──────────────────────────────────────────────────────────────

const TIER_STYLES = {
  news: {
    badge: "bg-blue-950/60 text-blue-300 border-blue-800/50",
    dot: "bg-blue-400",
    label: "News",
  },
  community: {
    badge: "bg-green-950/60 text-green-300 border-green-800/50",
    dot: "bg-green-400",
    label: "Community",
  },
  social: {
    badge: "bg-purple-950/60 text-purple-300 border-purple-800/50",
    dot: "bg-purple-400",
    label: "Social",
  },
} as const;

const TOPIC_STYLES: Record<string, string> = {
  AI: "border-blue-500/50 bg-blue-500/10 text-blue-300",
  Finance: "border-green-500/50 bg-green-500/10 text-green-300",
  "Finance+AI": "border-cyan-500/50 bg-cyan-500/10 text-cyan-300",
  Marketing: "border-amber-500/50 bg-amber-500/10 text-amber-300",
};

// ── Helpers ────────────────────────────────────────────────────────────────

function timeAgo(unix: number): string {
  const s = Date.now() / 1000 - unix;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function parseMetadata(meta: string | Record<string, unknown>): Record<string, unknown> {
  if (typeof meta === "object") return meta as Record<string, unknown>;
  try {
    return JSON.parse(meta);
  } catch {
    return {};
  }
}

// ── Sub-components ─────────────────────────────────────────────────────────

function TierBadge({ tier }: { tier: string }) {
  const styles = TIER_STYLES[tier as keyof typeof TIER_STYLES] ?? TIER_STYLES.news;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        styles.badge
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", styles.dot)} />
      {styles.label}
    </span>
  );
}

function TopicCardComponent({
  card,
  index,
}: {
  card: TopicCard;
  index: number;
}) {
  const medals = [
    <Flame key="1" className="h-5 w-5 text-orange-400" />,
    <Flame key="2" className="h-5 w-5 text-amber-400" />,
    <Flame key="3" className="h-5 w-5 text-yellow-400" />,
  ];
  return (
    <div className="rounded-xl border border-orange-900/40 bg-orange-950/20 p-5 hover:border-orange-700/50 transition-colors">
      <div className="flex items-start gap-3">
        <div className="flex-shrink-0 mt-0.5">{medals[index] ?? medals[0]}</div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white leading-snug mb-2">
            {card.title}
          </p>
          <p className="text-xs text-zinc-400 leading-relaxed mb-3">
            {card.summary}
          </p>

          {/* Why trending */}
          <div className="flex items-start gap-1.5 mb-2">
            <TrendingUp className="h-3.5 w-3.5 text-orange-400 flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-orange-300 italic">
              {card.why_trending}
            </p>
          </div>

          {/* Sources */}
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            {card.sources.map((src, i) => (
              <span
                key={i}
                className="rounded-full bg-zinc-800/80 px-2 py-0.5 text-[10px] text-zinc-400 border border-zinc-700/50"
              >
                {src}
              </span>
            ))}
            <span className="text-[10px] text-zinc-600 ml-auto">
              {timeAgo(card.created_at)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const meta = parseMetadata(signal.metadata);
  const upvotes = (meta.upvotes as number) ?? 0;
  const comments = (meta.comments as number) ?? 0;
  const points = (meta.points as number) ?? 0;

  return (
    <div className="flex items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-900/50 p-3 hover:border-zinc-700 transition-colors">
      <div className="flex-1 min-w-0">
        <div className="flex items-start justify-between gap-2 mb-1">
          {signal.url ? (
            <a
              href={signal.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-white hover:text-blue-400 transition-colors line-clamp-2 leading-snug flex-1"
            >
              {signal.title}
            </a>
          ) : (
            <p className="text-xs font-medium text-white line-clamp-2 leading-snug flex-1">
              {signal.title}
            </p>
          )}
          {signal.url && (
            <a
              href={signal.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-zinc-700 hover:text-zinc-400 flex-shrink-0 mt-0.5"
            >
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <TierBadge tier={signal.tier} />
          <span className="text-[10px] text-zinc-500 font-mono">
            {signal.source}
          </span>
          {(upvotes > 0 || points > 0) && (
            <span className="text-[10px] text-zinc-500 flex items-center gap-0.5">
              <ArrowUp className="h-2.5 w-2.5" />
              {upvotes || points}
            </span>
          )}
          {comments > 0 && (
            <span className="text-[10px] text-zinc-500 flex items-center gap-0.5">
              <MessageCircle className="h-2.5 w-2.5" />
              {comments}
            </span>
          )}
          <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
            <Clock className="h-2.5 w-2.5" />
            {timeAgo(signal.found_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function TrendingPage() {
  const [cards, setCards] = useState<TopicCard[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [stats, setStats] = useState<TrendingStats>({
    total_signals: 0,
    by_tier: {},
    topic_cards: 0,
  });
  const [settings, setSettings] = useState<TrendingSettings>({
    auto_refresh_enabled: true,
    auto_top3_enabled: true,
    active_topic: "AI",
    available_topics: ["AI", "Finance", "Finance+AI", "Marketing"],
  });
  const [activeTopic, setActiveTopic] = useState("AI");
  const [activeTier, setActiveTier] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchAll = useCallback(
    async (topic?: string) => {
      const t = topic ?? activeTopic;
      try {
        const [trendingRes, signalsRes, settingsRes] = await Promise.all([
          api<{
            cards: TopicCard[];
            stats: TrendingStats;
            topic: string;
            available_topics: string[];
          }>(`/api/gateway/trending?topic=${encodeURIComponent(t)}`),
          api<{ signals: Signal[] }>(
            `/api/gateway/trending/signals?topic=${encodeURIComponent(t)}&limit=30`
          ),
          api<TrendingSettings>("/api/gateway/trending/settings"),
        ]);
        setCards(trendingRes.cards);
        setStats(trendingRes.stats);
        setSignals(signalsRes.signals);
        setSettings(settingsRes);
      } catch {
        // retry next interval
      } finally {
        setLoading(false);
      }
    },
    [activeTopic]
  );

  useEffect(() => {
    fetchAll();
    const id = setInterval(() => fetchAll(), 60_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  const handleTopicSwitch = async (topic: string) => {
    setActiveTopic(topic);
    setLoading(true);
    await postJSON("/api/gateway/trending/settings", {
      active_topic: topic,
    });
    await fetchAll(topic);
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await postJSON("/api/gateway/trending/refresh", {
        topic: activeTopic,
      });
      await fetchAll();
    } finally {
      setRefreshing(false);
    }
  };

  const handleToggleAutoRefresh = async () => {
    const newVal = !settings.auto_refresh_enabled;
    await postJSON("/api/gateway/trending/settings", {
      auto_refresh_enabled: newVal,
    });
    setSettings((s) => ({ ...s, auto_refresh_enabled: newVal }));
  };

  const handleToggleAutoTop3 = async () => {
    const newVal = !settings.auto_top3_enabled;
    await postJSON("/api/gateway/trending/settings", {
      auto_top3_enabled: newVal,
    });
    setSettings((s) => ({ ...s, auto_top3_enabled: newVal }));
  };

  const handleClear = async () => {
    if (!confirm(`Clear all trending data for "${activeTopic}"?`)) return;
    await api(`/api/gateway/trending?topic=${encodeURIComponent(activeTopic)}`, {
      method: "DELETE",
    });
    setCards([]);
    setSignals([]);
    setStats({ total_signals: 0, by_tier: {}, topic_cards: 0 });
  };

  const filteredSignals = activeTier
    ? signals.filter((s) => s.tier === activeTier)
    : signals;

  return (
    <div className="space-y-6 fade-in">
      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-orange-600/20 border border-orange-600/30 flex items-center justify-center">
              <TrendingUp className="h-4 w-4 text-orange-400" />
            </div>
            <h1 className="text-2xl font-semibold">Trending Topics</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-1">
            {stats.total_signals} signals across{" "}
            {Object.keys(stats.by_tier).length} tiers · daily digest at 6pm IST
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Auto-refresh toggle */}
          <button
            onClick={handleToggleAutoRefresh}
            title="Auto-refresh: fetch signals on schedule"
            className={clsx(
              "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors",
              settings.auto_refresh_enabled
                ? "border-emerald-800/50 bg-emerald-950/30 text-emerald-300 hover:border-emerald-700"
                : "border-zinc-700 bg-zinc-800 text-zinc-500 hover:border-zinc-600"
            )}
          >
            {settings.auto_refresh_enabled ? (
              <ToggleRight className="h-3.5 w-3.5" />
            ) : (
              <ToggleLeft className="h-3.5 w-3.5" />
            )}
            Auto
          </button>

          {/* Auto top-3 generation toggle */}
          <button
            onClick={handleToggleAutoTop3}
            title={
              settings.auto_top3_enabled
                ? "Auto top 3: ON — generates top 3 cards on every auto-refresh"
                : "Auto top 3: OFF — only generates top 3 when you press Refresh manually"
            }
            className={clsx(
              "flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors",
              settings.auto_top3_enabled
                ? "border-orange-800/50 bg-orange-950/30 text-orange-300 hover:border-orange-700"
                : "border-zinc-700 bg-zinc-800 text-zinc-500 hover:border-zinc-600"
            )}
          >
            {settings.auto_top3_enabled ? (
              <ToggleRight className="h-3.5 w-3.5" />
            ) : (
              <ToggleLeft className="h-3.5 w-3.5" />
            )}
            Top 3
          </button>

          {/* Manual refresh */}
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-600 hover:text-white transition-colors disabled:opacity-50"
          >
            <RefreshCw
              className={clsx("h-3.5 w-3.5", refreshing && "animate-spin")}
            />
            {refreshing ? "Fetching..." : "Refresh"}
          </button>

          {/* Clear */}
          <button
            onClick={handleClear}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-600 hover:border-red-900/50 hover:text-red-400 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* ── Topic Tabs ── */}
      <div className="flex flex-wrap gap-2">
        {settings.available_topics.map((topic) => (
          <button
            key={topic}
            onClick={() => handleTopicSwitch(topic)}
            className={clsx(
              "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
              activeTopic === topic
                ? TOPIC_STYLES[topic] ??
                    "border-zinc-600 bg-zinc-700/50 text-zinc-200"
                : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300"
            )}
          >
            {topic}
          </button>
        ))}
      </div>

      {/* ── Top 3 Topic Cards ── */}
      {(cards.length > 0 || !loading) && (
        <div>
          <div className="flex items-center gap-2 mb-3">
            <Flame className="h-4 w-4 text-orange-400" />
            <h2 className="text-sm font-semibold text-orange-200">
              Top 3 Trending in {activeTopic}
            </h2>
          </div>

          {cards.length === 0 ? (
            <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 text-center py-12 text-zinc-600">
              <TrendingUp className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
              <p className="text-sm">No trending topics yet</p>
              <p className="text-xs text-zinc-700 mt-1">
                Press Refresh to fetch from all sources
              </p>
              <button
                onClick={handleRefresh}
                disabled={refreshing}
                className="mt-4 rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-xs text-zinc-300 hover:border-zinc-600 disabled:opacity-50"
              >
                {refreshing ? "Fetching..." : "Fetch now"}
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {cards.slice(0, 3).map((card, i) => (
                <TopicCardComponent key={card.id} card={card} index={i} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Tier Filter ── */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => setActiveTier(null)}
          className={clsx(
            "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
            !activeTier
              ? "border-zinc-600 bg-zinc-700/50 text-zinc-200"
              : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700"
          )}
        >
          All
          {stats.total_signals > 0 && (
            <span className="ml-1.5 text-[10px] opacity-60">
              {stats.total_signals}
            </span>
          )}
        </button>
        {Object.entries(TIER_STYLES).map(([tier, style]) => (
          <button
            key={tier}
            onClick={() => setActiveTier(tier)}
            className={clsx(
              "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors flex items-center gap-1.5",
              activeTier === tier
                ? style.badge
                : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700"
            )}
          >
            <span
              className={clsx("h-1.5 w-1.5 rounded-full", style.dot)}
            />
            {style.label}
            {(stats.by_tier[tier] ?? 0) > 0 && (
              <span className="text-[10px] opacity-60">
                {stats.by_tier[tier]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* ── Signal List ── */}
      {loading ? (
        <div className="card text-center py-16 text-zinc-600">
          <TrendingUp className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
          <p className="text-sm">Loading signals...</p>
        </div>
      ) : filteredSignals.length === 0 ? (
        <div className="card text-center py-12 text-zinc-600">
          <Hash className="h-6 w-6 mx-auto mb-2 text-zinc-700" />
          <p className="text-sm">No signals yet</p>
          <p className="text-xs text-zinc-700 mt-1">
            Refresh to fetch from Google News, Reddit, HackerNews, and X
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredSignals.map((signal) => (
            <SignalRow key={signal.id} signal={signal} />
          ))}
        </div>
      )}
    </div>
  );
}
