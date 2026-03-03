"use client";

import { use } from "react";
import Link from "next/link";
import { useCanvas } from "@/lib/useCanvas";
import { CanvasRenderer } from "@/components/CanvasRenderer";

interface Props {
  params: Promise<{ sessionId: string }>;
}

export default function CanvasPage({ params }: Props) {
  const { sessionId } = use(params);
  const { state, sendEvent } = useCanvas(sessionId);

  return (
    <div className="min-h-screen bg-canvas-bg flex flex-col">
      {/* Topbar */}
      <header className="flex items-center gap-3 px-4 py-2.5 border-b border-canvas-border bg-canvas-surface/50 backdrop-blur">
        <Link
          href="/"
          className="text-canvas-muted hover:text-canvas-text transition-colors text-xs"
        >
          ← all
        </Link>
        <div className="w-px h-4 bg-canvas-border" />
        <span className="text-sm font-medium text-canvas-text truncate">
          {state.title || sessionId}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {/* Connection indicator */}
          <span
            className={`flex items-center gap-1.5 text-xs ${
              state.connected ? "text-green-400" : "text-canvas-muted"
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                state.connected ? "bg-green-400 animate-pulse" : "bg-canvas-border"
              }`}
            />
            {state.connected ? "live" : "disconnected"}
          </span>
          {/* Session ID chip */}
          <span className="text-xs font-mono text-canvas-muted/50 hidden sm:block">
            {sessionId}
          </span>
        </div>
      </header>

      {/* Canvas body */}
      <main className="flex-1 p-5 overflow-auto">
        {!state.visible && (
          <div className="flex items-center justify-center h-32 text-canvas-muted text-sm">
            Canvas hidden by agent.
          </div>
        )}

        {state.visible && !state.content && (
          <div className="flex flex-col items-center justify-center h-64 text-canvas-muted space-y-3">
            <svg
              className="w-10 h-10 text-canvas-border"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"
              />
            </svg>
            <span className="text-sm">Waiting for content from agent…</span>
            {!state.connected && (
              <span className="text-xs text-canvas-muted/60">
                Connecting to canvas host…
              </span>
            )}
          </div>
        )}

        {state.visible && state.content && (
          <CanvasRenderer
            content={state.content}
            sessionId={sessionId}
            sendEvent={sendEvent}
          />
        )}
      </main>

      {/* Event log (debug footer — shows last interaction) */}
      {state.lastEventAt && (
        <footer className="px-4 py-2 border-t border-canvas-border text-xs text-canvas-muted/50 text-right">
          Last update:{" "}
          {new Date(state.lastEventAt).toLocaleTimeString()}
        </footer>
      )}
    </div>
  );
}
