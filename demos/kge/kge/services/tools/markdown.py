"""A deliberately small Markdown renderer for the business-facing onboarding guide.

The source is ours, but escaping remains the boundary: unsupported Markdown is
shown as text rather than passed through as HTML or quietly discarded.
"""
from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    held: list[str] = []

    def code(match: re.Match) -> str:
        held.append(f"<code>{html.escape(match.group(1), quote=True)}</code>")
        return f"\x00{len(held) - 1}\x00"

    safe = re.sub(r"`([^`]+)`", code, text)
    safe = html.escape(safe, quote=True)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    def restore(match: re.Match) -> str:
        index = int(match.group(1))
        return held[index] if index < len(held) else match.group(0)
    return re.sub(r"\x00(\d+)\x00", restore, safe)


def render_markdown(source: str) -> str:
    """Render headings, paragraphs, lists, bold, code and pipe tables only."""
    lines = source.splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    listing: str | None = None
    i = 0

    def flush() -> None:
        nonlocal paragraph, listing
        if paragraph:
            out.append(f"<p>{_inline(' '.join(part.strip() for part in paragraph))}</p>")
            paragraph = []
        if listing:
            out.append(f"</{listing}>")
            listing = None

    while i < len(lines):
        line = lines[i]
        if not line.strip():
            flush(); i += 1; continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        if heading:
            flush(); level = len(heading.group(1)); out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1; continue
        item = re.fullmatch(r"\s*(?:([-+*])|(\d+)\.)\s+(.+)", line)
        if item:
            paragraph and flush()
            wanted = "ol" if item.group(2) else "ul"
            if listing != wanted:
                if listing: out.append(f"</{listing}>")
                out.append(f"<{wanted}>"); listing = wanted
            out.append(f"<li>{_inline(item.group(3))}</li>")
            i += 1; continue
        if line.startswith("|") and i + 1 < len(lines) and re.fullmatch(r"\s*\|?(?:\s*:?-+:?\s*\|)+\s*", lines[i + 1]):
            flush()
            rows: list[list[str]] = []
            raw_rows = [line, lines[i + 1]]
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                raw_rows.append(lines[i])
                rows.append([cell.strip() for cell in lines[i].strip().strip("|").split("|")]); i += 1
            width = len(rows[0])
            if width and all(len(row) == width for row in rows):
                out.append("<table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0]) + "</tr></thead><tbody>")
                out.extend("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>" for row in rows[1:])
                out.append("</tbody></table>")
            else:
                out.extend(f"<p>{_inline(row)}</p>" for row in raw_rows)
            continue
        if listing:
            flush()
        paragraph.append(line)
        i += 1
    flush()
    return "\n".join(out)
