/**
 * Standalone markdown renderer for the gateway UI.
 * Zero dependencies. Escapes all input before inserting HTML.
 *
 * Supported: **bold** __bold__ *italic* _italic_ ~~strikethrough~~
 * `inline code` ```fenced code``` # headings
 * - bullet lists  1. ordered lists  > blockquotes
 */

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function applyInline(s: string): string {
  // Inline code first (before other transforms)
  s = s.replace(/`([^`\n]+?)`/g, (_, m) => `<code class="md-ic">${esc(m)}</code>`);
  // Bold — must come before italic; use [\s\S] instead of . with s flag for ES5 compat
  s = s.replace(/\*\*([\s\S]+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([\s\S]+?)__/g, "<strong>$1</strong>");
  // Italic — single star/underscore (avoid lookbehind for ES5 compat)
  s = s.replace(/\*([^*\n]+?)\*/g, "<em>$1</em>");
  s = s.replace(/_([^_\n]+?)_/g, "<em>$1</em>");
  // Strikethrough
  s = s.replace(/~~([\s\S]+?)~~/g, "<del>$1</del>");
  // Trailing double-space → line break
  s = s.replace(/  \n/g, "<br />");
  return s;
}

export function renderMarkdown(raw: string): string {
  if (!raw) return "";

  const lines = raw.split("\n");
  const out: string[] = [];
  let i = 0;
  let inUl = false;
  let inOl = false;
  let inBlockquote = false;
  let paraLines: string[] = [];

  const flushPara = () => {
    if (paraLines.length > 0) {
      out.push(`<p class="md-p">${paraLines.join("<br />")}</p>`);
      paraLines = [];
    }
  };
  const closeList = () => {
    flushPara();
    if (inUl) {
      out.push("</ul>");
      inUl = false;
    }
    if (inOl) {
      out.push("</ol>");
      inOl = false;
    }
  };
  const closeBq = () => {
    flushPara();
    if (inBlockquote) {
      out.push("</blockquote>");
      inBlockquote = false;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fenceMatch = line.match(/^```([A-Za-z0-9_.+-]*)$/);
    if (fenceMatch) {
      closeList();
      closeBq();
      flushPara();
      const lang = fenceMatch[1] || "";
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(esc(lines[i]));
        i++;
      }
      const langAttr = lang ? ` data-lang="${esc(lang)}"` : "";
      out.push(
        `<pre class="md-pre"${langAttr}><code class="md-code">${codeLines.join("\n")}</code></pre>`
      );
      i++;
      continue;
    }

    // Horizontal rule
    if (/^(---+|\*\*\*+|___+)\s*$/.test(line)) {
      closeList();
      closeBq();
      flushPara();
      out.push(`<hr class="md-hr" />`);
      i++;
      continue;
    }

    // Headings
    const headMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headMatch) {
      closeList();
      closeBq();
      flushPara();
      const lvl = headMatch[1].length;
      const text = applyInline(esc(headMatch[2]));
      out.push(`<h${lvl} class="md-h${lvl}">${text}</h${lvl}>`);
      i++;
      continue;
    }

    // Blockquote
    const bqMatch = line.match(/^>\s?(.*)$/);
    if (bqMatch) {
      closeList();
      flushPara();
      if (!inBlockquote) {
        out.push(`<blockquote class="md-bq">`);
        inBlockquote = true;
      }
      out.push(`<p>${applyInline(esc(bqMatch[1]))}</p>`);
      i++;
      continue;
    }
    if (inBlockquote && line.trim() === "") {
      closeBq();
      i++;
      continue;
    }

    // Unordered list
    const ulMatch = line.match(/^(\s*)[*\-+]\s+(.+)$/);
    if (ulMatch) {
      closeBq();
      flushPara();
      if (!inUl) {
        closeList();
        out.push(`<ul class="md-ul">`);
        inUl = true;
      }
      out.push(`<li>${applyInline(esc(ulMatch[2]))}</li>`);
      i++;
      continue;
    }

    // Ordered list
    const olMatch = line.match(/^\d+\.\s+(.+)$/);
    if (olMatch) {
      closeBq();
      flushPara();
      if (!inOl) {
        closeList();
        out.push(`<ol class="md-ol">`);
        inOl = true;
      }
      out.push(`<li>${applyInline(esc(olMatch[1]))}</li>`);
      i++;
      continue;
    }

    // Blank line — paragraph break
    if (line.trim() === "") {
      closeList();
      closeBq();
      flushPara();
      i++;
      continue;
    }

    // Regular text line — collect into paragraph
    closeList();
    closeBq();
    paraLines.push(applyInline(esc(line)));
    i++;
  }

  closeList();
  closeBq();
  flushPara();
  return out.join("\n");
}
