"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Newspaper,
  RefreshCw,
  Trash2,
  ExternalLink,
  Clock,
  ChevronDown,
  Clapperboard,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────

interface Article {
  id: number;
  title: string;
  url: string;
  summary: string;
  category: "Products" | "Research" | "Tools";
  source: string;
  found_at: number;
  expires_at: number;
}

interface ContentIdea {
  id: number;
  title: string;
  url: string;
  category: string;
  source: string;
  angle: string;
  why: string;
  created_at: number;
}

interface NewsStats {
  total: number;
  by_category: Record<string, number>;
  content_ideas: number;
}

interface NewsResponse {
  articles: Article[];
  total: number;
  stats: NewsStats;
}

interface IdeasResponse {
  ideas: ContentIdea[];
  total: number;
}

// ── Constants ──────────────────────────────────────────────────────────────

const CATEGORIES = ["All", "Products", "Research"] as const;
const CATEGORY_CAPS = { Products: 10, Research: 5 } as const;

const CATEGORY_STYLES = {
  Products: {
    badge: "bg-blue-950/60 text-blue-300 border-blue-800/50",
    dot: "bg-blue-400",
    tab: "border-blue-500/50 bg-blue-500/10 text-blue-300",
  },
  Research: {
    badge: "bg-purple-950/60 text-purple-300 border-purple-800/50",
    dot: "bg-purple-400",
    tab: "border-purple-500/50 bg-purple-500/10 text-purple-300",
  },
} as const;

// ── Helpers ────────────────────────────────────────────────────────────────

function timeAgo(unix: number): string {
  const s = Date.now() / 1000 - unix;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// ── Sub-components ─────────────────────────────────────────────────────────

function CategoryBadge({ category }: { category: string }) {
  const styles = CATEGORY_STYLES[category as keyof typeof CATEGORY_STYLES] ?? CATEGORY_STYLES.Products;
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium", styles.badge)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", styles.dot)} />
      {category}
    </span>
  );
}

function ContentIdeaCard({ idea, index }: { idea: ContentIdea; index: number }) {
  const medals = ["🥇", "🥈", "🥉"];
  return (
    <div className="rounded-xl border border-amber-900/40 bg-amber-950/20 p-4 hover:border-amber-700/50 transition-colors">
      <div className="flex items-start gap-3">
        <span className="text-xl mt-0.5 flex-shrink-0">{medals[index] ?? "🎬"}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2 mb-2">
            <p className="text-sm font-medium text-white leading-snug line-clamp-2">
              {idea.title}
            </p>
            {idea.url && (
              <a href={idea.url} target="_blank" rel="noopener noreferrer"
                className="text-zinc-600 hover:text-zinc-400 flex-shrink-0 mt-0.5">
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>

          {/* Content angle */}
          <div className="flex items-start gap-1.5 mb-1.5">
            <Clapperboard className="h-3.5 w-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
            <p className="text-[12px] text-amber-300 italic">{idea.angle}</p>
          </div>

          {/* Why it performs */}
          <div className="flex items-start gap-1.5">
            <TrendingUp className="h-3.5 w-3.5 text-zinc-500 flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-zinc-500">{idea.why}</p>
          </div>

          {/* Meta */}
          <div className="flex items-center gap-2 mt-2">
            {idea.category && <CategoryBadge category={idea.category} />}
            {idea.source && <span className="text-[10px] text-zinc-600 font-mono">{idea.source}</span>}
            <span className="text-[10px] text-zinc-700">{timeAgo(idea.created_at)}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ArticleCard({ article }: { article: Article }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-3.5 hover:border-zinc-700 transition-colors">
      <div className="flex items-start justify-between gap-2 mb-2">
        <a href={article.url} target="_blank" rel="noopener noreferrer"
          className="text-sm font-medium text-white hover:text-blue-400 transition-colors line-clamp-2 leading-snug flex-1">
          {article.title}
        </a>
        <a href={article.url} target="_blank" rel="noopener noreferrer"
          className="text-zinc-700 hover:text-zinc-400 flex-shrink-0 mt-0.5">
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        <CategoryBadge category={article.category} />
        {article.source && (
          <span className="text-[10px] text-zinc-500 font-mono">{article.source}</span>
        )}
        <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
          <Clock className="h-2.5 w-2.5" />
          {timeAgo(article.found_at)}
        </span>
      </div>

      {article.summary && (
        <div>
          <p className={clsx("text-[11px] text-zinc-500 leading-relaxed", !expanded && "line-clamp-2")}>
            {article.summary}
          </p>
          {article.summary.length > 100 && (
            <button onClick={() => setExpanded(!expanded)}
              className="mt-0.5 text-[10px] text-zinc-700 hover:text-zinc-500 flex items-center gap-0.5">
              {expanded ? "Less" : "More"}
              <ChevronDown className={clsx("h-3 w-3 transition-transform", expanded && "rotate-180")} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Page ────────────────────────────────────────────────────────────────────

export default function AiNewsPage() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [ideas, setIdeas] = useState<ContentIdea[]>([]);
  const [stats, setStats] = useState<NewsStats>({ total: 0, by_category: {}, content_ideas: 0 });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeCategory, setActiveCategory] = useState<string>("All");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(60);

  const fetchAll = useCallback(async () => {
    try {
      const [newsRes, ideasRes] = await Promise.all([
        api<NewsResponse>("/api/gateway/ai-news"),
        api<IdeasResponse>("/api/gateway/ai-news/content-ideas"),
      ]);
      setArticles(newsRes.articles);
      setStats(newsRes.stats);
      setIdeas(ideasRes.ideas);
      setLastUpdated(new Date());
      setCountdown(60);
    } catch {
      // retry on next tick
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 60_000);
    return () => clearInterval(id);
  }, [fetchAll]);

  useEffect(() => {
    const id = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(id);
  }, [lastUpdated]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api("/api/gateway/ai-news/refresh", { method: "POST" });
      await fetchAll();
    } finally {
      setRefreshing(false);
    }
  };

  const handleClear = async () => {
    if (!confirm("Clear all cached AI news and content ideas?")) return;
    await api("/api/gateway/ai-news", { method: "DELETE" });
    setArticles([]);
    setIdeas([]);
    setStats({ total: 0, by_category: {}, content_ideas: 0 });
  };

  const displayed =
    activeCategory === "All" ? articles : articles.filter((a) => a.category === activeCategory);

  return (
    <div className="space-y-6 fade-in">

      {/* ── Header ── */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-blue-600/20 border border-blue-600/30 flex items-center justify-center">
              <Newspaper className="h-4 w-4 text-blue-400" />
            </div>
            <h1 className="text-2xl font-semibold">AI News</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-1">
            {stats.total} articles (max 10 Products + 5 Research) · news refreshes every 4h · 10am–10pm
            {lastUpdated && (
              <span className="ml-1 text-zinc-700">
                · synced {lastUpdated.toLocaleTimeString()} · {countdown}s
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button onClick={handleRefresh} disabled={refreshing}
            title="Fetches latest AI news only — content ideas auto-refresh at 6pm IST"
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-600 hover:text-white transition-colors disabled:opacity-50">
            <RefreshCw className={clsx("h-3.5 w-3.5", refreshing && "animate-spin")} />
            {refreshing ? "Fetching news…" : "Refresh news"}
          </button>
          <button onClick={handleClear}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-600 hover:border-red-900/50 hover:text-red-400 transition-colors">
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>
        </div>
      </div>

      {/* ── Content Ideas Section ── */}
      {(ideas.length > 0 || !loading) && (
        <div className="rounded-xl border border-amber-900/30 bg-amber-950/10 p-4">
          <div className="flex items-center gap-2 mb-3">
            <div className="h-6 w-6 rounded-md bg-amber-500/20 border border-amber-500/30 flex items-center justify-center">
              <Sparkles className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <h2 className="text-sm font-semibold text-amber-200">Content Ideas</h2>
            <span className="text-[10px] text-amber-700 ml-auto">
              AI-scored · auto-refreshes daily at 6pm IST
            </span>
          </div>

          {ideas.length === 0 ? (
            <p className="text-xs text-zinc-600 text-center py-4">
              Content ideas regenerate automatically at 6pm IST daily.
              {stats.total === 0 ? " Fetch news first using the Refresh button." : " Check back at 6pm IST."}
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {ideas.slice(0, 3).map((idea, i) => (
                <ContentIdeaCard key={idea.id} idea={idea} index={i} />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Category Filter ── */}
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map((cat) => {
          const rawCount = cat === "All" ? stats.total : (stats.by_category[cat] ?? 0);
          const cap = cat !== "All" ? (CATEGORY_CAPS[cat as keyof typeof CATEGORY_CAPS] ?? rawCount) : null;
          const count = cap !== null ? Math.min(rawCount, cap) : Math.min(rawCount, 15);
          const catStyles = cat !== "All" ? CATEGORY_STYLES[cat as keyof typeof CATEGORY_STYLES] : null;
          return (
            <button key={cat} onClick={() => setActiveCategory(cat)}
              className={clsx(
                "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                activeCategory === cat
                  ? catStyles
                    ? catStyles.tab
                    : "border-zinc-600 bg-zinc-700/50 text-zinc-200"
                  : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300"
              )}>
              {cat}
              {count > 0 && (
                <span className="ml-1.5 text-[10px] opacity-60">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* ── Article Grid ── */}
      {loading ? (
        <div className="card text-center py-16 text-zinc-600">
          <Newspaper className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
          <p className="text-sm">Loading AI news…</p>
          <p className="text-xs text-zinc-700 mt-1">First run fetches from Perplexity + Gemini</p>
        </div>
      ) : displayed.length === 0 ? (
        <div className="card text-center py-16 text-zinc-600">
          <Newspaper className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
          <p className="text-sm">No {activeCategory !== "All" ? activeCategory : ""} articles yet</p>
          <p className="text-xs text-zinc-700 mt-1">
            News auto-refreshes every 4h (10am–10pm) · content ideas regenerate at 6pm IST
          </p>
          <button onClick={handleRefresh} disabled={refreshing}
            className="mt-4 rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-xs text-zinc-300 hover:border-zinc-600 disabled:opacity-50">
            {refreshing ? "Fetching news…" : "Fetch news now"}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {displayed.map((article) => (
            <ArticleCard key={article.id} article={article} />
          ))}
        </div>
      )}
    </div>
  );
}
