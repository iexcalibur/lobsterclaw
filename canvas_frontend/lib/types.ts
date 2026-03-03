// Shared types for Canvas Host ↔ Frontend communication

// ---------------------------------------------------------------
// Content shapes (agent → frontend)
// ---------------------------------------------------------------

export type ContentKind = "html" | "markdown" | "url" | "a2ui" | "json";

export interface HtmlContent {
  kind: "html";
  html: string;
  title?: string;
}

export interface MarkdownContent {
  kind: "markdown";
  markdown: string;
  title?: string;
}

export interface UrlContent {
  kind: "url";
  url: string;
  title?: string;
}

export interface A2UIContent {
  kind: "a2ui";
  a2ui: A2UINode;
  title?: string;
}

export interface JsonContent {
  kind: "json";
  data: unknown;
  title?: string;
}

export type CanvasContent =
  | HtmlContent
  | MarkdownContent
  | UrlContent
  | A2UIContent
  | JsonContent;

// ---------------------------------------------------------------
// WebSocket messages: server → client
// ---------------------------------------------------------------

export type ServerMessage =
  | { type: "present"; content: CanvasContent; title?: string; sessionId: string }
  | { type: "restore"; content: CanvasContent; url?: string; visible: boolean; title?: string; sessionId: string }
  | { type: "navigate"; url: string; sessionId: string }
  | { type: "hide"; sessionId: string }
  | { type: "show"; sessionId: string }
  | { type: "eval"; script: string; id: string; sessionId: string }
  | { type: "update"; patch: Record<string, unknown>; sessionId: string }
  | { type: "close"; sessionId: string }
  | { type: "ping" };

// ---------------------------------------------------------------
// WebSocket messages: client → server
// ---------------------------------------------------------------

export type ClientMessage =
  | { type: "ready"; sessionId: string }
  | { type: "click"; elementId: string; text: string; sessionId: string }
  | { type: "input"; elementId: string; value: string; sessionId: string }
  | { type: "submit"; formId: string; data: Record<string, string>; sessionId: string }
  | { type: "event"; name: string; data: unknown; sessionId: string }
  | { type: "eval_result"; id: string; result: unknown; sessionId: string };

// ---------------------------------------------------------------
// A2UI component tree
// ---------------------------------------------------------------

export type A2UINode =
  | CardNode
  | TextNode
  | HeadingNode
  | TableNode
  | ListNode
  | ButtonNode
  | InputNode
  | FormNode
  | ImageNode
  | CodeNode
  | DividerNode
  | BadgeNode
  | ProgressNode
  | JsonViewNode
  | RowNode
  | ColNode;

export interface CardNode {
  type: "card";
  title?: string;
  subtitle?: string;
  children?: A2UINode[];
}

export interface TextNode {
  type: "text";
  content: string;
  muted?: boolean;
}

export interface HeadingNode {
  type: "heading";
  level?: 1 | 2 | 3 | 4;
  content: string;
}

export interface TableNode {
  type: "table";
  headers: string[];
  rows: (string | number)[][];
}

export interface ListNode {
  type: "list";
  ordered?: boolean;
  items: string[];
}

export interface ButtonNode {
  type: "button";
  label: string;
  action: string;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
}

export interface InputNode {
  type: "input";
  id: string;
  label?: string;
  placeholder?: string;
  inputType?: string;
  defaultValue?: string;
}

export interface FormNode {
  type: "form";
  id: string;
  title?: string;
  children: A2UINode[];
  submitLabel?: string;
}

export interface ImageNode {
  type: "image";
  src: string;
  alt?: string;
  caption?: string;
}

export interface CodeNode {
  type: "code";
  language?: string;
  content: string;
}

export interface DividerNode {
  type: "divider";
}

export interface BadgeNode {
  type: "badge";
  label: string;
  variant?: "default" | "success" | "error" | "warning" | "info";
}

export interface ProgressNode {
  type: "progress";
  value: number;
  max?: number;
  label?: string;
}

export interface JsonViewNode {
  type: "json";
  data: unknown;
  collapsed?: boolean;
}

export interface RowNode {
  type: "row";
  children: A2UINode[];
  gap?: number;
}

export interface ColNode {
  type: "col";
  children: A2UINode[];
  span?: number;
}

// ---------------------------------------------------------------
// Canvas state (UI)
// ---------------------------------------------------------------

export interface CanvasState {
  sessionId: string;
  content: CanvasContent | null;
  visible: boolean;
  title: string;
  connected: boolean;
  lastEventAt: number | null;
}
