"use client";

import { renderMarkdown } from "@/lib/markdown";

interface MarkdownMessageProps {
  content: string;
  className?: string;
}

export default function MarkdownMessage({ content, className = "" }: MarkdownMessageProps) {
  const html = renderMarkdown(content);
  return (
    <div
      className={`md-root ${className}`.trim()}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
