"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Newspaper,
  RefreshCw,
  Trash2,
  ExternalLink,
  Clock,
  ChevronDown,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

interface Article {
  id: number;
  title: string;
  url: string;
  summary: string;
  category: string;
  source: string;
  found_at: number;
  expires_at: number;
}

interface NewsStats {
  total: number;
  by_category: Record<string, number>;
}

interface NewsResponse {
  articles: Article[];
  total: number;
  stats: NewsStats;
}

const CATEGORIES = ["All", "General", "Products", "Research", "Industry", "Business"];

const CATEGORY_STYLES: Record<string, { badge: string; dot: string }> = {
  General:  { badge: "bg-zinc-800 text-zinc-300 border-zinc-700",   dot: "bg-zinc-400" },
  Products: { badge: "bg-blue-950/60 text-blue-300 border-blue-800/50",  dot: "bg-blue-400" },
  Research: { badge: "bg-purple-950/60 text-purple-300 border-purple-800/50", dot: "bg-purple-400" },
  Industry: { badge: "bg-emerald-950/60 text-emerald-300 border-emerald-800/50", dot: "bg-emerald-400" },
  Business: { badge: "bg-amber-950/60 text-amber-300 border-amber-800/50", dot: "bg-amber-400" },
};

function timeAgo(unixSecs: number): string {
  const diff = Date.now() / 1000 - unixSecs;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function expiresIn(unixSecs: number): string {
  const diff = unixSecs - Date.now() / 1000;
  if (diff < 0) return "expired";
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

function CategoryBadge({ category }: { category: string }) {
  const styles = CATEGORY_STYLES[category] ?? CATEGORY_STYLES.General;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        styles.badge
      )}
    >
      <span className={clsx("h-1.5 w-1.5 rounded-full", styles.dot)} />
      {category}
    </span>
  );
}

function ArticleCard({ article }: { article: Article }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 hover:border-zinc-700 transition-colors">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex-1 min-w-0">
          <a
            href={article.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-white hover:text-blue-400 transition-colors line-clamp-2 leading-snug"
          >
            {article.title}
          </a>
        </div>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-zinc-600 hover:text-zinc-400 flex-shrink-0 mt-0.5"
        >
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <CategoryBadge category={article.category} />
        {article.source && (
          <span className="text-[11px] text-zinc-500 font-mono">{article.source}</span>
        )}
        <span className="text-[11px] text-zinc-600 flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {timeAgo(article.found_at)}
        </span>
        <span className="text-[11px] text-zinc-700">
          expires in {expiresIn(article.expires_at)}
        </span>
      </div>

      {/* Summary */}
      {article.summary && (
        <div>
          <p
            className={clsx(
              "text-[11px] text-zinc-500 leading-relaxed",
              !expanded && "line-clamp-2"
            )}
          >
            {article.summary}
          </p>
          {article.summary.length > 120 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="mt-1 text-[10px] text-zinc-600 hover:text-zinc-400 flex items-center gap-0.5"
            >
              {expanded ? "Show less" : "Show more"}
              <ChevronDown
                className={clsx("h-3 w-3 transition-transform", expanded && "rotate-180")}
              />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function AiNewsPage() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [stats, setStats] = useState<NewsStats>({ total: 0, by_category: {} });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [activeCategory, setActiveCategory] = useState("All");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [countdown, setCountdown] = useState(60);

  const fetchNews = useCallback(async () => {
    try {
      const res = await api<NewsResponse>("/api/gateway/ai-news?limit=200");
      setArticles(res.articles);
      setStats(res.stats);
      setLastUpdated(new Date());
      setCountdown(60);
    } catch {
      // silently retry on next interval
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch + 60s auto-refresh
  useEffect(() => {
    fetchNews();
    const id = setInterval(fetchNews, 60_000);
    return () => clearInterval(id);
  }, [fetchNews]);

  // Countdown ticker
  useEffect(() => {
    const id = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(id);
  }, [lastUpdated]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api("/api/gateway/ai-news/refresh", { method: "POST" });
      await fetchNews();
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  const handleClear = async () => {
    if (!confirm("Clear all cached AI news articles?")) return;
    try {
      await api("/api/gateway/ai-news", { method: "DELETE" });
      setArticles([]);
      setStats({ total: 0, by_category: {} });
    } catch {
      // ignore
    }
  };

  const displayed =
    activeCategory === "All"
      ? articles
      : articles.filter((a) => a.category === activeCategory);

  return (
    <div className="space-y-6 fade-in">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-blue-600/20 border border-blue-600/30 flex items-center justify-center">
              <Newspaper className="h-4 w-4 text-blue-400" />
            </div>
            <h1 className="text-2xl font-semibold">AI News</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-1">
            {stats.total} articles &middot; refreshes every hour &middot; kept for 3 days
            {lastUpdated && (
              <span className="ml-2 text-zinc-600">
                · updated {lastUpdated.toLocaleTimeString()} · next in {countdown}s
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-300 hover:border-zinc-600 hover:text-white transition-colors disabled:opacity-50"
          >
            <RefreshCw className={clsx("h-3.5 w-3.5", refreshing && "animate-spin")} />
            {refreshing ? "Refreshing…" : "Refresh now"}
          </button>
          <button
            onClick={handleClear}
            className="flex items-center gap-1.5 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-1.5 text-xs text-zinc-500 hover:border-red-900/50 hover:text-red-400 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>
        </div>
      </div>

      {/* Category stats bar */}
      <div className="flex flex-wrap gap-2">
        {CATEGORIES.map((cat) => {
          const count = cat === "All" ? stats.total : (stats.by_category[cat] ?? 0);
          const styles = cat !== "All" ? CATEGORY_STYLES[cat] : null;
          return (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              className={clsx(
                "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
                activeCategory === cat
                  ? "border-blue-600/50 bg-blue-600/10 text-blue-300"
                  : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:border-zinc-700 hover:text-zinc-300"
              )}
            >
              {cat}
              {count > 0 && (
                <span className="ml-1.5 text-[10px] text-zinc-600">{count}</span>
              )}
            </button>
          );
        })}
      </div>

      {/* News grid */}
      {loading ? (
        <div className="card text-center text-zinc-500 text-sm py-16">
          <Newspaper className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
          <p>Loading AI news…</p>
          <p className="text-xs text-zinc-700 mt-1">
            First run searches the web — this may take a moment
          </p>
        </div>
      ) : displayed.length === 0 ? (
        <div className="card text-center text-zinc-500 text-sm py-16">
          <Newspaper className="h-8 w-8 mx-auto mb-3 text-zinc-700" />
          <p>No articles yet</p>
          <p className="text-xs text-zinc-700 mt-1">
            Click &ldquo;Refresh now&rdquo; to fetch the latest AI news
          </p>
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="mt-4 rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-xs text-zinc-300 hover:border-zinc-600 disabled:opacity-50"
          >
            {refreshing ? "Fetching…" : "Fetch news now"}
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
