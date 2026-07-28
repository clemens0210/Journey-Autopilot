/* Minimal, safe Markdown -> HTML for assistant replies, plus the agent trace.
 *
 * Everything here is XSS-critical: model output reaches innerHTML through these
 * functions. The rule is that raw text passes through `escapeHtml` (or
 * `renderInlineMd`, which escapes first) before any token replacement runs.
 */

import { escapeHtml } from "./dom.js";

export function renderTrace(trace) {
  const lines = trace.map((t) => {
    if (t.kind === "call") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> → calls <b>${escapeHtml(t.name)}()</b></div>`;
    if (t.kind === "result") return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span> ← result of <b>${escapeHtml(t.name)}</b></div>`;
    // Sub-agent actions: nested one level under the Orchestrator's call to them.
    if (t.kind === "subcall") return `<div class="trace-line trace-sub"><span class="ag">${escapeHtml(t.author)}</span> → calls <b>${escapeHtml(t.name)}()</b></div>`;
    if (t.kind === "subresult") return `<div class="trace-line trace-sub"><span class="ag">${escapeHtml(t.author)}</span> ← result of <b>${escapeHtml(t.name)}</b></div>`;
    return `<div class="trace-line"><span class="ag">${escapeHtml(t.author)}</span>: ${escapeHtml(t.text)}</div>`;
  }).join("");
  return `<details class="chat-trace"><summary>Agent trace (${trace.length})</summary>${lines}</details>`;
}

// Inline Markdown (bold, italic, inline code, links) for one span of text.
// Escapes first so nothing the model emits can inject HTML, THEN applies the
// token replacements — the escaped `*`, `` ` ``, `[` … survive escaping.
// Only *…* / **…** are treated as emphasis (not `_`), because agent prose is
// full of snake_case identifiers like `mock_hotels` that `_`-italic would mangle.
export function renderInlineMd(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${t}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s;
}

// A small line-based block grammar (headings, ordered/unordered lists, tables,
// blockquotes, fenced code, rules, paragraphs) wrapping renderInlineMd. Not
// full CommonMark — just the subset the agents actually emit.
export function renderMarkdown(src) {
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  // A table starts at line idx when it contains pipes and the NEXT line is a
  // |---|---| separator row. Checked by index (not per-line) because both the
  // block dispatcher and the paragraph accumulator must stop there — models
  // often emit "Here are your options:" directly followed by the table, and
  // without this check the whole table was swallowed into the paragraph.
  const isTableStart = (idx) =>
    lines[idx].includes("|") && idx + 1 < lines.length &&
    /^\s*\|?[\s:|-]*-[\s:|-]*$/.test(lines[idx + 1]) && lines[idx + 1].includes("|");
  const isBlockStart = (l) =>
    !l.trim() || /^```/.test(l.trim()) || /^#{1,6}\s/.test(l) || /^\s*>/.test(l) ||
    /^\s*[-*+]\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l);
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    if (/^```/.test(line.trim())) {
      const buf = [];
      for (i++; i < lines.length && !/^```/.test(lines[i].trim()); i++) buf.push(lines[i]);
      i++;
      out.push(`<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length, 6);
      out.push(`<h${level}>${renderInlineMd(h[2].trim())}</h${level}>`);
      i++; continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push("<hr>"); i++; continue; }

    // Table: a row with pipes followed by a |---|---| separator row.
    if (isTableStart(i)) {
      const cells = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
      const head = cells(line);
      i += 2;
      const rows = [];
      for (; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      const thead = `<thead><tr>${head.map((c) => `<th>${renderInlineMd(c)}</th>`).join("")}</tr></thead>`;
      const tbody = `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${renderInlineMd(c)}</td>`).join("")}</tr>`).join("")}</tbody>`;
      out.push(`<div class="md-tablewrap"><table class="md-table">${thead}${tbody}</table></div>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*>\s?/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${renderInlineMd(buf.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*[-*+]\s+/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
      out.push(`<ul>${buf.map((it) => `<li>${renderInlineMd(it)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf = [];
      for (; i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i]); i++) buf.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
      out.push(`<ol>${buf.map((it) => `<li>${renderInlineMd(it)}</li>`).join("")}</ol>`);
      continue;
    }
    const buf = [];
    for (; i < lines.length && lines[i].trim() && !isBlockStart(lines[i]) && !isTableStart(i); i++) buf.push(lines[i]);
    out.push(`<p>${renderInlineMd(buf.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }
  return out.join("");
}
