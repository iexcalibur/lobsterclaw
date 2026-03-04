"use client";

import { useEffect, useState } from "react";
import {
  Settings,
  Cpu,
  MessageSquare,
  ToggleLeft,
  ToggleRight,
  Gauge,
  Fingerprint,
  FileText,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";

interface ConfigData {
  llm: Record<string, any>;
  telegram: Record<string, any>;
  features: Record<string, boolean>;
  limits: Record<string, number>;
  identity: Record<string, string>;
}

interface WorkspaceFile {
  name: string;
  size: number;
  preview: string;
}

const SECTION_ICONS: Record<string, typeof Cpu> = {
  llm: Cpu,
  telegram: MessageSquare,
  features: ToggleRight,
  limits: Gauge,
  identity: Fingerprint,
};

const SECTION_LABELS: Record<string, string> = {
  llm: "LLM Configuration",
  telegram: "Telegram Settings",
  features: "Feature Flags",
  limits: "Limits & Constraints",
  identity: "Agent Identity",
};

const FEATURE_LABELS: Record<string, string> = {
  cron: "Cron Scheduler",
  heartbeat: "Heartbeat",
  memory: "Long-term Memory",
  browser: "Browser Automation",
  canvas: "Canvas Host",
  subagents: "Sub-agent Spawning",
  exec: "Shell Execution",
  tts: "Text-to-Speech",
};

export default function SettingsPage() {
  const [config, setConfig] = useState<ConfigData | null>(null);
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkspaceFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedFile, setExpandedFile] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [cfg, ws] = await Promise.all([
          api<ConfigData>("/api/gateway/config"),
          api<{ files: WorkspaceFile[] }>("/api/gateway/workspace"),
        ]);
        setConfig(cfg);
        setWorkspaceFiles(ws.files);
      } catch {
        /* retry */
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading || !config) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-zinc-500 text-sm">Loading configuration...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6 fade-in">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-sm text-zinc-500 mt-1">
          Current system configuration (read-only)
        </p>
      </div>

      {/* Config sections */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {(["llm", "telegram", "limits", "identity"] as const).map(
          (section) => {
            const Icon = SECTION_ICONS[section] || Settings;
            const data = config[section] as Record<string, any>;
            return (
              <div key={section} className="card">
                <div className="flex items-center gap-2 mb-4">
                  <Icon className="h-4 w-4 text-zinc-400" />
                  <h2 className="text-sm font-medium text-zinc-400">
                    {SECTION_LABELS[section] ?? section}
                  </h2>
                </div>
                <div className="space-y-2.5">
                  {Object.entries(data).map(([key, value]) => (
                    <div key={key} className="flex justify-between text-sm">
                      <span className="text-zinc-500">
                        {key.replace(/_/g, " ")}
                      </span>
                      <span className="font-mono text-zinc-300 text-right max-w-[60%] truncate">
                        {String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            );
          }
        )}
      </div>

      {/* Feature Flags */}
      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <ToggleRight className="h-4 w-4 text-zinc-400" />
          <h2 className="text-sm font-medium text-zinc-400">Feature Flags</h2>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {Object.entries(config.features).map(([key, enabled]) => (
            <div
              key={key}
              className={clsx(
                "flex items-center gap-2.5 rounded-lg px-3 py-2.5",
                enabled ? "bg-zinc-800/60" : "bg-zinc-800/30"
              )}
            >
              {enabled ? (
                <ToggleRight className="h-5 w-5 text-emerald-400 flex-shrink-0" />
              ) : (
                <ToggleLeft className="h-5 w-5 text-zinc-600 flex-shrink-0" />
              )}
              <span
                className={clsx(
                  "text-sm",
                  enabled ? "text-zinc-300" : "text-zinc-500"
                )}
              >
                {FEATURE_LABELS[key] ?? key}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Workspace Files */}
      <div className="card">
        <div className="flex items-center gap-2 mb-4">
          <FileText className="h-4 w-4 text-zinc-400" />
          <h2 className="text-sm font-medium text-zinc-400">
            Workspace Files ({workspaceFiles.length})
          </h2>
        </div>
        <div className="space-y-2">
          {workspaceFiles.map((file) => (
            <div key={file.name}>
              <button
                onClick={() =>
                  setExpandedFile(
                    expandedFile === file.name ? null : file.name
                  )
                }
                className="w-full flex items-center justify-between rounded-lg bg-zinc-800/50 px-3 py-2.5 hover:bg-zinc-800 transition-colors text-left"
              >
                <div className="flex items-center gap-2">
                  <FileText className="h-3.5 w-3.5 text-zinc-500" />
                  <span className="text-sm font-mono text-zinc-300">
                    {file.name}
                  </span>
                </div>
                <span className="text-xs text-zinc-600">
                  {(file.size / 1024).toFixed(1)} KB
                </span>
              </button>
              {expandedFile === file.name && (
                <div className="mt-1 rounded-lg bg-zinc-800/30 p-3">
                  <pre className="text-xs text-zinc-400 whitespace-pre-wrap font-mono leading-relaxed max-h-64 overflow-y-auto">
                    {file.preview}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
