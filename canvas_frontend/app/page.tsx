"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

interface Session {
  sessionId: string;
  title: string;
  visible: boolean;
  connected: number;
  contentKind: string | null;
  updatedAt: string;
}

const CANVAS_HOST_URL =
  process.env.NEXT_PUBLIC_CANVAS_HOST_URL || "http://localhost:7681";

export default function HomePage() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function fetchSessions() {
    try {
      const res = await fetch(`${CANVAS_HOST_URL}/api/canvas/sessions`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSessions(data.sessions ?? []);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    fetchSessions();
    const t = setInterval(fetchSessions, 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <main className="min-h-screen bg-canvas-bg p-8">
      {/* Header */}
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-8 h-8 rounded-lg bg-canvas-accent/20 flex items-center justify-center">
            <svg className="w-4 h-4 text-canvas-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <div>
            <h1 className="text-xl font-bold text-canvas-text">PyGate Canvas</h1>
            <p className="text-xs text-canvas-muted">Agent-driven interactive UI</p>
          </div>
          <button
            onClick={fetchSessions}
            className="ml-auto text-xs text-canvas-muted hover:text-canvas-accent transition-colors"
          >
            ↻ refresh
          </button>
        </div>

        {loading && (
          <div className="text-center py-16 text-canvas-muted text-sm">
            Connecting to canvas host…
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-800 bg-red-900/20 p-4 text-sm text-red-400 mb-6">
            <strong>Canvas host unreachable:</strong> {error}
            <br />
            <span className="text-xs mt-1 block text-red-400/70">
              Start PyGate with <code className="bg-red-900/30 px-1 rounded">CANVAS_HOST_ENABLED=true</code>
            </span>
          </div>
        )}

        {!loading && !error && sessions.length === 0 && (
          <div className="text-center py-16">
            <div className="text-canvas-muted text-sm">No active canvas sessions</div>
            <div className="text-canvas-muted/50 text-xs mt-2">
              Sessions appear when the agent calls the canvas tool
            </div>
          </div>
        )}

        {sessions.length > 0 && (
          <div className="space-y-3">
            <div className="text-xs text-canvas-muted uppercase tracking-wider mb-4">
              Active sessions ({sessions.length})
            </div>
            {sessions.map((s) => (
              <Link
                key={s.sessionId}
                href={`/canvas/${s.sessionId}`}
                className="block rounded-xl border border-canvas-border bg-canvas-surface
                           hover:border-canvas-accent/50 transition-colors p-4 group"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        s.connected > 0 ? "bg-green-400" : "bg-canvas-border"
                      }`} />
                      <span className="font-medium text-canvas-text group-hover:text-canvas-accent transition-colors truncate">
                        {s.title || s.sessionId}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1.5 text-xs text-canvas-muted">
                      <span className="font-mono text-canvas-muted/60">{s.sessionId}</span>
                      {s.contentKind && (
                        <span className="px-1.5 py-0.5 rounded bg-canvas-bg border border-canvas-border">
                          {s.contentKind}
                        </span>
                      )}
                      {s.connected > 0 && (
                        <span className="text-green-400">{s.connected} connected</span>
                      )}
                    </div>
                  </div>
                  <div className="text-xs text-canvas-muted/60 flex-shrink-0">
                    {s.updatedAt ? new Date(s.updatedAt).toLocaleTimeString() : ""}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}

        <div className="mt-8 pt-6 border-t border-canvas-border text-xs text-canvas-muted/50 text-center">
          Canvas host at{" "}
          <a href={CANVAS_HOST_URL} className="text-canvas-accent/60 hover:text-canvas-accent">
            {CANVAS_HOST_URL}
          </a>
        </div>
      </div>
    </main>
  );
}
