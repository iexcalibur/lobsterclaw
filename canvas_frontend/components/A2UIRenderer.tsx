"use client";

import React, { useRef } from "react";
import clsx from "clsx";
import type {
  A2UINode,
  ButtonNode,
  InputNode,
  FormNode,
  TableNode,
  CardNode,
  ListNode,
  CodeNode,
  BadgeNode,
  ProgressNode,
  JsonViewNode,
  RowNode,
  ColNode,
  HeadingNode,
} from "@/lib/types";

interface Props {
  node: A2UINode;
  onEvent?: (name: string, data: unknown) => void;
  onSubmit?: (formId: string, data: Record<string, string>) => void;
  onClick?: (elementId: string, text: string) => void;
  onInput?: (elementId: string, value: string) => void;
}

export function A2UIRenderer({ node, onEvent, onSubmit, onClick, onInput }: Props) {
  return <Node node={node} onEvent={onEvent} onSubmit={onSubmit} onClick={onClick} onInput={onInput} />;
}

function Node({ node, onEvent, onSubmit, onClick, onInput }: Props) {
  switch (node.type) {
    case "card":     return <CardComp node={node} {...{onEvent, onSubmit, onClick, onInput}} />;
    case "text":     return <p className={clsx("text-sm leading-relaxed", node.muted ? "text-canvas-muted" : "text-canvas-text")}>{node.content}</p>;
    case "heading":  return <HeadingComp node={node} />;
    case "table":    return <TableComp node={node} />;
    case "list":     return <ListComp node={node} />;
    case "button":   return <ButtonComp node={node} onClick={onClick} onEvent={onEvent} />;
    case "input":    return <InputComp node={node} onInput={onInput} />;
    case "form":     return <FormComp node={node} {...{onEvent, onSubmit, onClick, onInput}} />;
    case "image":    return <img src={node.src} alt={node.alt ?? ""} className="rounded-lg max-w-full" />;
    case "code":     return <CodeComp node={node} />;
    case "divider":  return <hr className="border-canvas-border my-4" />;
    case "badge":    return <BadgeComp node={node} />;
    case "progress": return <ProgressComp node={node} />;
    case "json":     return <JsonComp node={node} />;
    case "row":      return <RowComp node={node} {...{onEvent, onSubmit, onClick, onInput}} />;
    case "col":      return <ColComp node={node} {...{onEvent, onSubmit, onClick, onInput}} />;
    default:         return null;
  }
}

function Children({ nodes, ...handlers }: { nodes?: A2UINode[] } & Omit<Props, "node">) {
  if (!nodes?.length) return null;
  return (
    <div className="space-y-3">
      {nodes.map((n, i) => (
        <Node key={i} node={n} {...handlers} />
      ))}
    </div>
  );
}

// ------------------------------------------------------------------
// Card
// ------------------------------------------------------------------
function CardComp({ node, ...handlers }: { node: CardNode } & Omit<Props, "node">) {
  return (
    <div className="rounded-xl border border-canvas-border bg-canvas-surface p-5 space-y-3">
      {node.title && (
        <div>
          <h3 className="text-canvas-accent font-semibold text-base">{node.title}</h3>
          {node.subtitle && <p className="text-canvas-muted text-xs mt-0.5">{node.subtitle}</p>}
        </div>
      )}
      <Children nodes={node.children} {...handlers} />
    </div>
  );
}

// ------------------------------------------------------------------
// Heading
// ------------------------------------------------------------------
function HeadingComp({ node }: { node: HeadingNode }) {
  const level = node.level ?? 2;
  const cls = clsx("font-bold text-canvas-text", {
    "text-2xl": level === 1,
    "text-xl":  level === 2,
    "text-lg":  level === 3,
    "text-base": level === 4,
  });
  const Tag = `h${level}` as keyof JSX.IntrinsicElements;
  return <Tag className={cls}>{node.content}</Tag>;
}

// ------------------------------------------------------------------
// Table
// ------------------------------------------------------------------
function TableComp({ node }: { node: TableNode }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-canvas-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-canvas-border bg-canvas-bg">
            {node.headers.map((h, i) => (
              <th key={i} className="px-4 py-2.5 text-left font-medium text-canvas-accent">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {node.rows.map((row, ri) => (
            <tr key={ri} className="border-b border-canvas-border last:border-0 hover:bg-canvas-bg/50 transition-colors">
              {row.map((cell, ci) => (
                <td key={ci} className="px-4 py-2.5 text-canvas-text">
                  {String(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------
// List
// ------------------------------------------------------------------
function ListComp({ node }: { node: ListNode }) {
  const Tag = node.ordered ? "ol" : "ul";
  return (
    <Tag className={clsx("space-y-1.5 text-sm text-canvas-text pl-5", node.ordered ? "list-decimal" : "list-disc")}>
      {node.items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </Tag>
  );
}

// ------------------------------------------------------------------
// Button
// ------------------------------------------------------------------
function ButtonComp({
  node,
  onClick,
  onEvent,
}: {
  node: ButtonNode;
  onClick?: Props["onClick"];
  onEvent?: Props["onEvent"];
}) {
  const variantCls = clsx({
    "bg-canvas-accent hover:bg-canvas-accent-dim text-white": node.variant === "primary" || !node.variant,
    "border border-canvas-border hover:border-canvas-accent text-canvas-text bg-transparent": node.variant === "secondary",
    "bg-red-700 hover:bg-red-600 text-white": node.variant === "danger",
    "opacity-50 cursor-not-allowed": node.disabled,
  });
  return (
    <button
      disabled={node.disabled}
      className={clsx("px-4 py-2 rounded-lg text-sm font-medium transition-colors", variantCls)}
      onClick={() => {
        if (node.disabled) return;
        onClick?.(node.action, node.label);
        onEvent?.(node.action, { label: node.label });
      }}
    >
      {node.label}
    </button>
  );
}

// ------------------------------------------------------------------
// Input
// ------------------------------------------------------------------
function InputComp({
  node,
  onInput,
}: {
  node: InputNode;
  onInput?: Props["onInput"];
}) {
  return (
    <div className="space-y-1">
      {node.label && (
        <label className="text-xs font-medium text-canvas-muted" htmlFor={node.id}>
          {node.label}
        </label>
      )}
      <input
        id={node.id}
        type={node.inputType || "text"}
        defaultValue={node.defaultValue}
        placeholder={node.placeholder}
        className="w-full px-3 py-2 rounded-lg bg-canvas-bg border border-canvas-border
                   text-canvas-text text-sm placeholder:text-canvas-muted
                   focus:outline-none focus:ring-2 focus:ring-canvas-accent/50"
        onChange={(e) => onInput?.(node.id, e.target.value)}
      />
    </div>
  );
}

// ------------------------------------------------------------------
// Form
// ------------------------------------------------------------------
function FormComp({ node, onSubmit, ...handlers }: { node: FormNode } & Omit<Props, "node">) {
  const formRef = useRef<HTMLFormElement>(null);
  return (
    <form
      ref={formRef}
      id={node.id}
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        const form = formRef.current;
        if (!form) return;
        const entries = new FormData(form);
        const data: Record<string, string> = {};
        entries.forEach((v, k) => { data[k] = String(v); });
        onSubmit?.(node.id, data);
      }}
    >
      {node.title && (
        <h4 className="font-semibold text-canvas-text">{node.title}</h4>
      )}
      <Children nodes={node.children} {...handlers} onSubmit={onSubmit} />
      <button
        type="submit"
        className="px-4 py-2 bg-canvas-accent hover:bg-canvas-accent-dim text-white rounded-lg text-sm font-medium transition-colors"
      >
        {node.submitLabel ?? "Submit"}
      </button>
    </form>
  );
}

// ------------------------------------------------------------------
// Code
// ------------------------------------------------------------------
function CodeComp({ node }: { node: CodeNode }) {
  return (
    <div className="rounded-lg overflow-hidden">
      {node.language && (
        <div className="bg-canvas-bg px-4 py-1 text-xs text-canvas-muted border-b border-canvas-border">
          {node.language}
        </div>
      )}
      <pre className="bg-canvas-bg p-4 overflow-x-auto text-sm font-mono text-canvas-text">
        <code>{node.content}</code>
      </pre>
    </div>
  );
}

// ------------------------------------------------------------------
// Badge
// ------------------------------------------------------------------
function BadgeComp({ node }: { node: BadgeNode }) {
  const variantCls = {
    default: "bg-canvas-border text-canvas-muted",
    success: "bg-green-900/40 text-green-400 border border-green-800",
    error:   "bg-red-900/40 text-red-400 border border-red-800",
    warning: "bg-yellow-900/40 text-yellow-400 border border-yellow-800",
    info:    "bg-blue-900/40 text-blue-400 border border-blue-800",
  }[node.variant ?? "default"];
  return (
    <span className={clsx("inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium", variantCls)}>
      {node.label}
    </span>
  );
}

// ------------------------------------------------------------------
// Progress
// ------------------------------------------------------------------
function ProgressComp({ node }: { node: ProgressNode }) {
  const max = node.max ?? 100;
  const pct = Math.min(100, Math.round((node.value / max) * 100));
  return (
    <div className="space-y-1">
      {node.label && (
        <div className="flex justify-between text-xs text-canvas-muted">
          <span>{node.label}</span>
          <span>{pct}%</span>
        </div>
      )}
      <div className="h-2 bg-canvas-border rounded-full overflow-hidden">
        <div
          className="h-full bg-canvas-accent rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------
// JSON viewer
// ------------------------------------------------------------------
function JsonComp({ node }: { node: JsonViewNode }) {
  return (
    <pre className="bg-canvas-bg border border-canvas-border rounded-lg p-4 text-xs font-mono text-canvas-text overflow-x-auto">
      {JSON.stringify(node.data, null, 2)}
    </pre>
  );
}

// ------------------------------------------------------------------
// Row / Col layout
// ------------------------------------------------------------------
function RowComp({ node, ...handlers }: { node: RowNode } & Omit<Props, "node">) {
  return (
    <div className={clsx("flex flex-wrap", node.gap !== undefined ? `gap-${node.gap}` : "gap-4")}>
      {node.children.map((n, i) => <Node key={i} node={n} {...handlers} />)}
    </div>
  );
}

function ColComp({ node, ...handlers }: { node: ColNode } & Omit<Props, "node">) {
  const spanCls = node.span ? `flex-[${node.span}]` : "flex-1";
  return (
    <div className={clsx("flex flex-col space-y-3", spanCls)}>
      {node.children.map((n, i) => <Node key={i} node={n} {...handlers} />)}
    </div>
  );
}
