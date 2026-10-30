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
    lines = md.splitlines()
    out, in_meta = [], False
    for ln in lines:
        if ln.startswith("# "):
            continue
        if ln.startswith("| | |") or ln.startswith("|---|---|"):
            in_meta = True
            continue
        if in_meta and ln.startswith("| "):
            continue
        in_meta = False
        out.append(ln)
    return "\n".join(out).strip()


def document_chunks(title: str | None, md: str) -> list[str]:
    """A document's chunks: its body cut by chunk_text, the title at the head of the first."""
    return [f"{title or ''}\n\n{t}" if i == 0 else t for i, t in enumerate(chunk_text(strip_header(md)))]
