import React from "react";

/* Tiny, dependency-free Markdown renderer for the assistant's answers.
   Covers the subset the model actually emits: **bold**, *italic*, `code`,
   ## headings, "-"/"*" bullet lists, "1." numbered lists, and paragraphs.
   It builds React elements (no dangerouslySetInnerHTML), so it is safe.
   Non-string children (e.g. the JSX greeting) pass straight through. */

// Inline spans: bold, then code, then italic. Underscores are left literal so
// identifiers like FUND_MASTER are never mangled into emphasis.
const INLINE_RE = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g;

const renderInline = (text, key) => {
  const out = [];
  let last = 0;
  let m;
  let i = 0;
  INLINE_RE.lastIndex = 0;
  while ((m = INLINE_RE.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={`${key}-b${i}`}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={`${key}-c${i}`} className="md-code">{tok.slice(1, -1)}</code>);
    else out.push(<em key={`${key}-i${i}`}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
};

const parseBlocks = (src) => {
  const lines = String(src).replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let para = [];
  let list = null; // { ordered, items: [] }

  const flushPara = () => {
    if (para.length) {
      blocks.push({ type: "p", text: para.join(" ") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push({ type: "list", ordered: list.ordered, items: list.items });
      list = null;
    }
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");
    if (line.trim() === "") {
      flushPara();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+\.\s+(.*)$/);

    if (heading) {
      flushPara();
      flushList();
      blocks.push({ type: "h", level: heading[1].length, text: heading[2] });
    } else if (bullet) {
      flushPara();
      if (!list || list.ordered) {
        flushList();
        list = { ordered: false, items: [] };
      }
      list.items.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (!list || !list.ordered) {
        flushList();
        list = { ordered: true, items: [] };
      }
      list.items.push(numbered[1]);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
};

export default function Markdown({ children }) {
  // Only strings are Markdown; anything else (the JSX greeting) renders as-is.
  if (typeof children !== "string") return children || null;

  const blocks = parseBlocks(children);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.type === "h") {
          const lvl = Math.min(b.level, 3);
          return (
            <div key={i} className={`md-h md-h${lvl}`}>
              {renderInline(b.text, `h${i}`)}
            </div>
          );
        }
        if (b.type === "list") {
          const Tag = b.ordered ? "ol" : "ul";
          return (
            <Tag key={i} className="md-list">
              {b.items.map((it, j) => (
                <li key={j}>{renderInline(it, `l${i}-${j}`)}</li>
              ))}
            </Tag>
          );
        }
        return (
          <p key={i} className="md-p">
            {renderInline(b.text, `p${i}`)}
          </p>
        );
      })}
    </div>
  );
}
