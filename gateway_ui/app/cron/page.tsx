"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Clock,
  ToggleLeft,
  ToggleRight,
  Play,
  Pause,
  Trash2,
  Calendar,
  RefreshCw,
} from "lucide-react";
import clsx from "clsx";
import { api, postJSON, timeAgo } from "@/lib/api";

interface CronJob {
  id: string;
  description: string;
  schedule: string;
  message: string;
  enabled: boolean;
  created_at: string;
  run_count: number;
  last_run: string | null;
  next_run: string | null;
  session_target: string;
  delivery: string;
  delete_after_run: boolean;
}

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await api<{ jobs: CronJob[]; total: number }>(
        "/api/gateway/cron"
      );
      setJobs(res.jobs);
    } catch {
      /* retry */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
    const id = setInterval(fetchJobs, 10000);
    return () => clearInterval(id);
  }, [fetchJobs]);

  const toggleJob = async (jobId: string) => {
    setToggling(jobId);
    try {
      await postJSON(`/api/gateway/cron/${jobId}/toggle`, {});
      await fetchJobs();
    } catch {
      /* ignore */
    } finally {
      setToggling(null);
    }
  };

  const enabledCount = jobs.filter((j) => j.enabled).length;

  return (
    <div className="space-y-6 fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Cron Jobs</h1>
          <p className="text-sm text-zinc-500 mt-1">
            {jobs.length} jobs &middot; {enabledCount} active
          </p>
        </div>
        <button onClick={fetchJobs} className="btn-ghost">
          <RefreshCw className="h-4 w-4 mr-1.5" />
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="card text-center text-zinc-500 text-sm py-12">
          Loading cron jobs...
        </div>
      ) : jobs.length === 0 ? (
        <div className="card text-center py-16">
          <Clock className="h-10 w-10 text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-500 text-sm">
            No cron jobs scheduled. Ask the agent to set a reminder.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => (
            <div
              key={job.id}
              className={clsx(
                "card-hover",
                !job.enabled && "opacity-60"
              )}
            >
              <div className="flex items-center gap-4">
                {/* Toggle */}
                <button
                  onClick={() => toggleJob(job.id)}
                  disabled={toggling === job.id}
                  className="flex-shrink-0"
                >
                  {job.enabled ? (
                    <ToggleRight
                      className={clsx(
                        "h-6 w-6 text-emerald-400",
                        toggling === job.id && "animate-pulse"
                      )}
                    />
                  ) : (
                    <ToggleLeft
                      className={clsx(
                        "h-6 w-6 text-zinc-600",
                        toggling === job.id && "animate-pulse"
                      )}
                    />
                  )}
                </button>

                {/* Info */}
                <button
                  onClick={() =>
                    setExpanded(expanded === job.id ? null : job.id)
                  }
                  className="flex-1 min-w-0 text-left"
                >
                  <div className="flex items-center gap-2">
                    <p className="text-sm font-medium text-white truncate">
                      {job.description}
                    </p>
                    {job.delete_after_run && (
                      <span className="badge-yellow text-[10px]">once</span>
                    )}
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500">
                    <span className="font-mono bg-zinc-800 px-1.5 py-0.5 rounded">
                      {job.schedule}
                    </span>
                    <span>
                      {job.delivery === "direct" ? "direct" : "agent"} delivery
                    </span>
                    <span>{job.run_count} runs</span>
                  </div>
                </button>

                {/* Timing */}
                <div className="flex-shrink-0 text-right">
                  {job.next_run && (
                    <p className="text-xs text-zinc-400">
                      Next: {new Date(job.next_run).toLocaleString()}
                    </p>
                  )}
                  {job.last_run && (
                    <p className="text-[10px] text-zinc-600">
                      Last: {timeAgo(job.last_run)}
                    </p>
                  )}
                </div>
              </div>

              {/* Expanded detail */}
              {expanded === job.id && (
                <div className="mt-4 pt-4 border-t border-zinc-800 space-y-3">
                  <div>
                    <p className="text-xs text-zinc-500 mb-1">Message</p>
                    <p className="text-sm text-zinc-300 bg-zinc-800 rounded-lg p-3 font-mono whitespace-pre-wrap">
                      {job.message}
                    </p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <p className="text-xs text-zinc-500">Job ID</p>
                      <p className="font-mono text-zinc-400 text-xs">
                        {job.id}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-zinc-500">Created</p>
                      <p className="text-zinc-400 text-xs">
                        {new Date(job.created_at).toLocaleString()}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-zinc-500">Session Target</p>
                      <p className="text-zinc-400 text-xs">
                        {job.session_target}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-zinc-500">Delivery Mode</p>
                      <p className="text-zinc-400 text-xs">{job.delivery}</p>
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
