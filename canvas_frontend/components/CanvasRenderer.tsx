"use client";

import React, { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { CanvasContent, ClientMessage } from "@/lib/types";
import { A2UIRenderer } from "./A2UIRenderer";

interface Props {
  content: CanvasContent;
  sessionId: string;
  sendEvent: (msg: Omit<ClientMessage, "sessionId">) => void;
}

export function CanvasRenderer({ content, sessionId, sendEvent }: Props) {
  switch (content.kind) {
    case "html":
      return <HtmlRenderer html={content.html} />;

    case "markdown":
      return <MarkdownRenderer markdown={content.markdown} />;

    case "url":
      return <UrlRenderer url={content.url} />;

    case "a2ui":
      return (
        <A2UIRenderer
          node={content.a2ui}
          onClick={(elementId, text) => {
            sendEvent({ type: "click", elementId, text });
          }}
          onInput={(elementId, value) => {
            sendEvent({ type: "input", elementId, value });
          }}
          onSubmit={(formId, data) => {
            sendEvent({ type: "submit", formId, data });
          }}
          onEvent={(name, data) => {
            sendEvent({ type: "event", name, data });
          }}
        />
      );

    case "json":
      return <JsonRenderer data={content.data} />;

    default:
      return (
        <div className="text-canvas-muted text-sm italic">
          Unknown content kind.
        </div>
      );
  }
}

// ------------------------------------------------------------------
// HTML renderer — uses a sandboxed iframe for isolation
// ------------------------------------------------------------------
function HtmlRenderer({ html }: { html: string }) {
  // Wrap in a styled shell so it looks right in the dark canvas
  const wrapped = useMemo(
    () => `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<style>
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f0f11; color: #e2e8f0;
    margin: 0; padding: 16px; line-height: 1.6;
  }
  a { color: #a78bfa; }
  table { border-collapse: collapse; width: 100%; }
  th { background: #1a1a1f; color: #a78bfa; padding: 8px 12px; text-align: left; }
  td { padding: 8px 12px; border-bottom: 1px solid #2d2d3e; }
  pre, code { background: #1a1a1f; border-radius: 4px; padding: 2px 6px;
               font-family: monospace; font-size: 0.875em; }
  pre { padding: 12px; overflow: auto; }
  img { max-width: 100%; border-radius: 6px; }
  h1,h2,h3,h4 { color: #f8fafc; }
  hr { border: none; border-top: 1px solid #2d2d3e; margin: 16px 0; }
</style>
</head>
<body>${html}</body>
</html>`,
    [html]
  );

  return (
    <iframe
      srcDoc={wrapped}
      sandbox="allow-scripts allow-same-origin allow-forms"
      className="w-full min-h-[400px] rounded-lg border border-canvas-border bg-canvas-bg"
      style={{ height: "600px" }}
      title="canvas-html"
    />
  );
}

// ------------------------------------------------------------------
// Markdown renderer
// ------------------------------------------------------------------
function MarkdownRenderer({ markdown }: { markdown: string }) {
  return (
    <div className="prose prose-invert prose-sm max-w-none
                    prose-headings:text-canvas-text
                    prose-p:text-canvas-text
                    prose-a:text-canvas-accent
                    prose-code:text-canvas-accent prose-code:bg-canvas-bg
                    prose-pre:bg-canvas-bg prose-pre:border prose-pre:border-canvas-border
                    prose-th:text-canvas-accent prose-th:bg-canvas-bg
                    prose-td:text-canvas-text prose-td:border-canvas-border
                    prose-blockquote:border-canvas-accent prose-blockquote:text-canvas-muted">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}

// ------------------------------------------------------------------
// URL renderer — iframe navigating to an external URL
// ------------------------------------------------------------------
function UrlRenderer({ url }: { url: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-canvas-bg border border-canvas-border rounded-lg">
        <svg className="w-3.5 h-3.5 text-canvas-muted flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
        </svg>
        <span className="text-xs text-canvas-muted truncate">{url}</span>
        <a href={url} target="_blank" rel="noopener noreferrer" className="ml-auto text-canvas-accent hover:underline text-xs flex-shrink-0">
          open ↗
        </a>
      </div>
      <iframe
        src={url}
        className="w-full rounded-lg border border-canvas-border bg-canvas-bg"
        style={{ height: "600px" }}
        title="canvas-url"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
      />
    </div>
  );
}

// ------------------------------------------------------------------
// JSON renderer
// ------------------------------------------------------------------
function JsonRenderer({ data }: { data: unknown }) {
  return (
    <pre className="bg-canvas-bg border border-canvas-border rounded-lg p-4 text-xs font-mono text-canvas-text overflow-x-auto max-h-[600px] overflow-y-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
