"""Text extraction with the image's standard library alone, without a model or network."""
from __future__ import annotations

import csv
import io
import re
from html.parser import HTMLParser

from kge.ports import DocumentPage, DocumentReading


def table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(map(len, rows))
    lines = ["| " + " | ".join(str(v).replace("|", "\\|").replace("\n", "<br>") for v in row + [""] * (width - len(row))) + " |"
             for row in rows]
    return "\n".join([lines[0], "| " + " | ".join(["---"] * width) + " |", *lines[1:]])


def expand_spans(rows):
    """Expand HTML cell spans so values remain under their original columns."""
    active = {}
    result = []
    group = None
    for row_group, cells in rows:
        if row_group != group:
            active = {}
            group = row_group
        values = {column: value for column, (remaining, value) in active.items()}
        active = {column: (None if remaining is None else remaining - 1, value)
                  for column, (remaining, value) in active.items() if remaining is None or remaining > 1}
        column = 0
        for value, rowspan, colspan in cells:
            while any(index in values for index in range(column, column + colspan)):
                column += 1
            for index in range(column, column + colspan):
                values[index] = value
                if rowspan != 1:
                    active[index] = (None if rowspan == 0 else rowspan - 1, value)
            column += colspan
        result.append([values.get(index, "") for index in range(max(values, default=-1) + 1)])
    return result


class HTML(HTMLParser):
    """Keep text and table structure, discard executable content, and keep image descriptions."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.rows, self.row, self.cell = [], None, None, None
        self.tables = []
        self.span = (1, 1)
        self.group = 0
        self.hidden = 0

    def finish_cell(self):
        if self.cell is not None:
            if self.row is not None:
                self.row.append(("".join(self.cell).strip(), *self.span))
            self.cell = None

    def finish_row(self):
        self.finish_cell()
        if self.row is not None:
            if self.rows is not None:
                self.rows.append((self.group, self.row))
            self.row = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "table":
            if self.rows is not None:
                self.tables.append((self.rows, self.row, self.cell, self.span, self.group))
            self.rows, self.row, self.cell, self.span, self.group = [], None, None, (1, 1), 0
        elif tag in {"thead", "tbody", "tfoot"}:
            self.finish_row()
            self.group += 1
        elif tag == "tr":
            self.finish_row()
            self.row = []
        elif tag in {"td", "th"}:
            self.finish_cell()
            self.cell = []
            def span(name, maximum):
                value = (attrs.get(name) or "1").strip()
                if not value.isascii() or not value.isdigit():
                    return 1
                number = int(value)
                return min(number, maximum) if number > 0 or name == "rowspan" else 1
            self.span = (span("rowspan", 65534), span("colspan", 1000))
        elif tag == "img":
            self.handle_data("[Picture: " + (attrs.get("alt") or attrs.get("title") or "no description supplied") + "]")
        elif tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "figure", "figcaption", "caption"}:
            self.handle_data("\n\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden:
            return
        if tag in {"td", "th"}:
            self.finish_cell()
        elif tag in {"tr", "thead", "tbody", "tfoot"}:
            self.finish_row()
            if tag != "tr":
                self.group += 1
        elif tag == "table" and self.rows is not None:
            self.finish_row()
            rendered = table(expand_spans(self.rows))
            if self.tables:
                self.rows, self.row, self.cell, self.span, self.group = self.tables.pop()
                self.handle_data("\n" + rendered + "\n")
            else:
                self.out.append("\n\n" + rendered + "\n\n")
                self.rows = None
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "dl", "dt", "dd", "figure", "figcaption", "caption"}:
            self.handle_data("\n\n")

    def handle_data(self, data):
        if not self.hidden:
            parent_cell = self.tables[-1][2] if self.tables else None
            target = self.cell if self.cell is not None else parent_cell if parent_cell is not None else self.out
            target.append(data)


class TextReader:
    name = "text"
    formats = ("md", "txt", "csv", "tsv", "html", "htm")

    def __init__(self, client: str = ""):
        self.client = client

    def identity(self) -> str:
        return "text-v4"

    def read(self, content: bytes, format: str, name: str = "") -> DocumentReading:
        if format not in self.formats:
            raise ValueError(f"{format} isn't supported by {self.name}")
        text = content.decode("utf-8-sig")
        pages = []
        for text in text.split("\f"):
            if format in {"csv", "tsv"}:
                text = table(list(csv.reader(io.StringIO(text), delimiter="\t" if format == "tsv" else ",")))
            elif format in {"html", "htm"}:
                html = HTML()
                html.feed(text)
                html.close()
                text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(html.out))
            pages.append(DocumentPage(text.strip(), "text-layer"))
        return DocumentReading(tuple(pages))
