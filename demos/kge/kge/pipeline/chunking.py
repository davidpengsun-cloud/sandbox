"""How a document is cut into chunks: by certification, which lands them, and so for the pipeline that reads them.

Paragraph-bounded chunks of about CHUNK_CHARS, tables kept whole, from the
document's body (its title and metadata table stripped), with the title put
back at the head of the first chunk, since it is part of that chunk's meaning.
No graph store here.
"""
from __future__ import annotations

import re

CHUNK_CHARS = 420


def chunk_text(body: str) -> list[str]:
    """Paragraph-bounded chunks of about CHUNK_CHARS. Tables stay whole."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > CHUNK_CHARS:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def strip_header(md: str) -> str:
    """Drop the title and metadata table the generator writes."""
    md = re.sub(r"<!-- page: [0-9]+; read: (?:text-layer|model) -->\n*", "\n\n", md)
    lines = md.splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    if first < len(lines) and lines[first].startswith("# "):
        table = first + 1
        while table < len(lines) and not lines[table].strip():
            table += 1
        keys = ("Shelf", "Document", "Kind", "Business unit", "Owner", "Version", "Published", "Superseded by")
        if lines[table:table + 2] == ["| | |", "|---|---|"]:
            end = table + 2
            for key in keys:
                if (key not in keys[:3] and end + 1 < len(lines)
                        and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[end + 1])):
                    break
                if end < len(lines) and lines[end].startswith(f"| {key} |"):
                    end += 1
                elif key in keys[:3]:
                    break
            if end >= table + 5:
                lines = lines[:table] + lines[end:]
    return "\n".join(line for line in lines if not line.startswith("# ")).strip()


def document_chunks(title: str | None, md: str) -> list[str]:
    """A document's chunks: its body cut by chunk_text, the title at the head of the first."""
    return [f"{title or ''}\n\n{t}" if i == 0 else t for i, t in enumerate(chunk_text(strip_header(md)))]
