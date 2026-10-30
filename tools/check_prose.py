#!/usr/bin/env python3
"""House-style validator for the Markdown in this repository.

The conventions in ``CLAUDE.md`` and ``docs/proposals/templates/README.md`` are
mostly editorial judgements a script cannot make. A few are mechanical, and
those are what this checks — so that review time goes on the argument in a
document rather than on its punctuation.

Failures — a clean document has none of these:

* **Markdown hygiene.** ATX headings only (``## Heading``, never an underline),
  no heading level skipped, tables whose every row carries the column count its
  header declares, no stray trailing whitespace, exactly one closing newline.
* **Numeric alignment.** CLAUDE.md: "Right-align numeric table columns." A
  column whose cells are predominantly figures must carry ``---:`` in the
  separator row.
* **Spelling consistency.** CLAUDE.md: "British or American spelling is fine —
  be consistent within a document." Mixed ``-ise``/``-ize``,
  ``-isation``/``-ization``, ``-yse``/``-yze`` and ``-our``/``-or`` within one
  file are reported at the minority spellings. An opening front-matter block's
  values count, so a skill's ``description:`` keeps its body's standard.
* **Currency convention.** CLAUDE.md: currency in full on first use
  (``$247,900``) before any abbreviated form (``$248k``).

Warnings — reported, but exit 0 unless ``--strict``:

* **Planning-estimate labelling.** CLAUDE.md: "Figures are planning estimates
  until measured. Label them as such." A table of volume or cost figures in a
  use case or proposal with no *planning estimate* or *Basis* near it is worth
  a second look. Whether a given table needs the label is an editorial call,
  which is why this warns rather than fails.

Usage::

    python3 tools/check_prose.py                 # default paths
    python3 tools/check_prose.py PATH...         # named files or directories
    python3 tools/check_prose.py --strict        # warnings count as failures
    python3 tools/check_prose.py --list          # what each file was checked for
    python3 tools/check_prose.py --fix           # apply the mechanical repairs

``--fix`` is deliberately narrow. It repairs trailing whitespace, the closing
newline and a numeric column's separator cell — edits with exactly one correct
outcome. It does not touch spelling, currency or table shape: which spelling a
document should settle on, and which figure should be written out in full, are
editorial decisions the script has no grounds to make.

Findings print as ``path:line: message`` so GitHub annotates the offending
line. Exit 0 when clean, 1 otherwise. Standard library only, no network.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Output is pinned to UTF-8 rather than the process locale: these scripts echo
# document content, which carries em dashes and box-drawing characters, and a
# runner under a C locale would otherwise fail on the report rather than on the
# content it is reporting.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

REPO = Path(__file__).resolve().parents[1]

# Scanned by default. The skill tree is the one reviewable dot-directory;
# deliberately broken content in .fixture/ remains outside a real run.
DEFAULT_PATHS = [
    "CLAUDE.md", "README.md", "CONTRIBUTING.md", "docs", "demos", "tools",
    ".claude/skills",
]

# Tables in these trees carry client-facing figures, so they attract the
# planning-estimate warning. Reference and operations material does not. A
# use case's runnable demo sits in demos/ and is held as its use case is.
ESTIMATE_TREES = ("docs/use-cases", "docs/proposals", "demos")

# A header cell that marks a table as quoted commercials rather than an
# estimate: the rate card, the payment schedule, the headline options. These
# are prices the engagement is offered at, and Appendix B is where their basis
# lives, so the planning-estimate label does not apply to them.
COMMERCIAL_HEADERS = {
    "allocation", "day rate", "days", "fee", "fees", "investment",
    "milestone", "option a", "option b", "value",
}

# Words near a table that satisfy the labelling rule.
ESTIMATE_MARKERS = (
    "planning estimate", "basis", "estimate", "estimated", "illustrative",
    "assumption", "sized", "sizing",
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    path: str
    line: int
    message: str
    warning: bool = False

    def render(self) -> str:
        mark = "warning: " if self.warning else ""
        return f"{self.path}:{self.line}: {mark}{self.message}"


# ---------------------------------------------------------------------------
# Markdown primitives
# ---------------------------------------------------------------------------

ATX_RE = re.compile(r"^(#{1,6})\s+\S")
SETEXT_RE = re.compile(r"^(=+|-{2,})\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Table:
    """One Markdown table: its header cells, separator cells and body rows."""

    header: list[str]
    separator: list[str]
    rows: list[list[str]] = field(default_factory=list)
    header_line: int = 0
    separator_line: int = 0
    row_lines: list[int] = field(default_factory=list)


def split_row(line: str) -> list[str]:
    """Split a Markdown table row into stripped cells.

    Pipes escaped as ``\\|`` belong to the cell, not to the structure, so they
    are hidden behind a sentinel for the duration of the split.
    """
    body = line.strip().replace(r"\|", "\x00")
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip().replace("\x00", "|") for cell in body.split("|")]


def fence_map(lines: list[str]) -> list[bool]:
    """Return, per line, whether it sits inside a fenced code block.

    The fence lines themselves count as inside — nothing in them is prose.
    """
    inside: list[bool] = []
    open_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            open_fence = not open_fence
            inside.append(True)
            continue
        inside.append(open_fence)
    return inside


def front_matter_map(lines: list[str]) -> list[bool]:
    """Mark an opening YAML front-matter block as metadata, not prose.

    Only an exact delimiter on the first line opens the block. This narrow
    shape leaves thematic breaks elsewhere subject to the Markdown checks.
    """
    inside = [False] * len(lines)
    if not lines or lines[0] != "---":
        return inside
    closing = next((index for index, line in enumerate(lines[1:], 1) if line == "---"), None)
    if closing is None:
        # An unclosed delimiter is ordinary Markdown rather than a licence to
        # make the rest of the document invisible to every prose check.
        return inside
    content = lines[1:closing]
    if not content or any(
        line and re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*.*$", line) is None
        for line in content
    ):
        # A pair of thematic breaks is ordinary Markdown. Recognise only the
        # flat key/value YAML shape used by this repository's metadata.
        return inside
    for index in range(closing + 1):
        inside[index] = True
    return inside


def blank_lines(text: str, ignored: list[bool]) -> str:
    """Blank ignored line content while preserving offsets and line numbers."""
    parts = text.splitlines(keepends=True)
    return "".join(
        ("".join("\n" if char == "\n" else " " for char in part)
         if ignored[index] else part)
        for index, part in enumerate(parts)
    )


FRONT_MATTER_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?=:)")


def blank_keys(text: str, front_matter: list[bool]) -> str:
    """Blank each front-matter key, keeping its value, offsets and line numbers.

    A key is a field name, while a value such as a skill's ``description:`` is
    read by people, so the spelling rule reads the value with the body.
    """
    parts = text.splitlines(keepends=True)
    return "".join(
        FRONT_MATTER_KEY_RE.sub(lambda key: " " * len(key.group()), part)
        if front_matter[index] else part
        for index, part in enumerate(parts)
    )


def find_tables(lines: list[str], fenced: list[bool]) -> list[Table]:
    """Collect every table whose header is followed by a separator row."""
    tables: list[Table] = []
    i = 0
    while i < len(lines):
        if fenced[i] or not lines[i].strip().startswith("|"):
            i += 1
            continue
        # A table needs a header and a separator made only of -, : and spaces.
        if i + 1 >= len(lines) or fenced[i + 1]:
            i += 1
            continue
        nxt = lines[i + 1].strip()
        if not (nxt.startswith("|") and re.fullmatch(r"[\s|:-]+", nxt) and "-" in nxt):
            i += 1
            continue

        table = Table(
            header=split_row(lines[i]),
            separator=split_row(lines[i + 1]),
            header_line=i + 1,
            separator_line=i + 2,
        )
        j = i + 2
        while j < len(lines) and not fenced[j] and lines[j].strip().startswith("|"):
            table.rows.append(split_row(lines[j]))
            table.row_lines.append(j + 1)
            j += 1
        tables.append(table)
        i = j
    return tables


def strip_inline_code(text: str) -> str:
    """Blank out code spans and link targets, keeping offsets intact.

    Prose checks run over the result, so a path, a URL or a snippet of JSON
    cannot be mistaken for a spelling or a currency figure.
    """
    out = list(text)

    def blank(match: re.Match[str]) -> None:
        for k in range(match.start(), match.end()):
            if out[k] != "\n":
                out[k] = " "

    for match in re.finditer(r"`[^`\n]*`", text):
        blank(match)
    for match in re.finditer(r"\]\([^)\n]*\)", text):
        blank(match)
    for match in re.finditer(r"<[^>\s]+>", text):
        blank(match)
    return "".join(out)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------

# One figure: an optional currency mark, digits with thousands separators, an
# optional decimal, an optional magnitude or percent suffix.
_NUM = r"[$£€]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[$£€]?\d+(?:\.\d+)?"
_SUFFIX = r"(?:\s?(?:k|K|m|M|bn|b|B|%|x|×|pp|bps))?"

# Units a figure may carry and still be a figure: a rate, a duration, a count.
_UNIT = (
    r"(?:\s*(?:/\s*(?:yr|year|day|wk|week|mo|month|FTE|alert|case|hr|hour)"
    r"|min(?:ute)?s?|hrs?|hours?|days?|wks?|weeks?|mths?|months?|yrs?|years?"
    r"|FTE|GB|MB|TB|pp|bps|each))?"
)

FIGURE_RE = re.compile(
    rf"^(?:{_NUM}){_SUFFIX}{_UNIT}"
    rf"(?:\s*[-–—]\s*(?:{_NUM}){_SUFFIX}{_UNIT})?$"
)

# Qualifiers that precede a figure without stopping it being one.
QUALIFIER_RE = re.compile(
    r"^(?:~|≈|<|>|≤|≥|\+|±|c\.|ca\.|approx\.?|about)\s*", re.IGNORECASE
)

# Emphasis and footnote marks a cell may wrap a figure in.
DECORATION_RE = re.compile(r"[*_`]")


def is_figure(cell: str) -> bool:
    """Whether a table cell is a figure rather than prose that mentions one."""
    text = DECORATION_RE.sub("", cell).strip()
    if not text:
        return False
    text = QUALIFIER_RE.sub("", text).strip()
    # A trailing footnote marker or parenthetical is not part of the figure.
    text = re.sub(r"\s*\((?:[^()]*)\)$", "", text).strip()
    if not text or not any(ch.isdigit() for ch in text):
        return False
    return bool(FIGURE_RE.fullmatch(text))


def carries_money_or_volume(cell: str) -> bool:
    """Whether a figure cell is a cost or a volume — the kind CLAUDE.md wants
    labelled as a planning estimate until measured."""
    text = DECORATION_RE.sub("", cell)
    if re.search(r"[$£€]", text):
        return True
    # A bare count is only interesting at scale: four digits or a magnitude.
    return bool(re.search(r"\d{1,3}(?:,\d{3})+|\d{4,}|\d(?:\.\d+)?\s?(?:k|m|bn)\b", text))


# ---------------------------------------------------------------------------
# Spelling
# ---------------------------------------------------------------------------

# -ise words with no -ize variant in either standard. Inflections are derived,
# so only the base form is listed.
FIXED_ISE = {
    "advertise", "advise", "apprise", "arise", "chastise", "circumcise",
    "comprise", "compromise", "concise", "demise", "despise", "devise",
    "disguise", "enterprise", "excise", "exercise", "franchise", "guise",
    "improvise", "incise", "likewise", "merchandise", "noise", "otherwise",
    "paradise", "poise", "practise", "praise", "precise", "premise", "prise",
    "promise", "raise", "reprise", "revise", "rise", "supervise", "surmise",
    "surprise", "televise", "treatise", "wise",
}

# -ize words that are not the American half of a pair.
FIXED_IZE = {"capsize", "maize", "prize", "resize", "seize", "size"}

# -our/-or pairs, by British stem. Suffixes (-s, -ed, -ing, -al, -er, -ous)
# are matched on top of the stem.
OUR_STEMS = {
    "armour", "behaviour", "candour", "clamour", "colour", "demeanour",
    "endeavour", "favour", "flavour", "harbour", "honour", "humour", "labour",
    "neighbour", "odour", "parlour", "rigour", "rumour", "savour", "splendour",
    "valour", "vapour", "vigour",
}
OUR_SUFFIXES = ("", "s", "ed", "ing", "er", "ers", "al", "ally", "ite", "ites", "less")

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]{2,}")

BRITISH, AMERICAN = "British", "American"


def classify_word(word: str) -> tuple[str, str] | None:
    """Return ``(variant, canonical)`` for a word carrying a spelling marker.

    ``canonical`` names the pair so a finding can quote its opposite, e.g.
    ``("British", "vectorise/vectorize")``. Words outside the four families,
    and the fixed lists above, return None.
    """
    low = word.lower().rstrip("'’")

    for suffix in ("isations", "isation", "ising", "ises", "ised", "ise"):
        if low.endswith(suffix):
            stem = low[: -len(suffix)]
            if len(stem) >= 3 and stem + "ise" not in FIXED_ISE:
                return BRITISH, f"{stem}ise/{stem}ize"
    for suffix in ("izations", "ization", "izing", "izes", "ized", "ize"):
        if low.endswith(suffix):
            stem = low[: -len(suffix)]
            if len(stem) >= 3 and stem + "ize" not in FIXED_IZE:
                return AMERICAN, f"{stem}ise/{stem}ize"
    for suffix in ("ysing", "yses", "ysed", "yse"):
        if low.endswith(suffix):
            stem = low[: -len(suffix)]
            if len(stem) >= 3:
                return BRITISH, f"{stem}yse/{stem}yze"
    for suffix in ("yzing", "yzes", "yzed", "yze"):
        if low.endswith(suffix):
            stem = low[: -len(suffix)]
            if len(stem) >= 3:
                return AMERICAN, f"{stem}yse/{stem}yze"

    for stem in OUR_STEMS:
        american_stem = stem[:-3] + "or"
        for suffix in OUR_SUFFIXES:
            if low == stem + suffix:
                return BRITISH, f"{stem}/{american_stem}"
            if low == american_stem + suffix:
                return AMERICAN, f"{stem}/{american_stem}"
    return None


def looks_like_proper_noun(text: str, start: int, word: str) -> bool:
    """Whether a capitalised word is a name rather than a sentence opener.

    Product and vendor names — ``NICE Actimize`` — end in the same letters as
    a spelling variant and must not be counted as one.
    """
    if not word[0].isupper():
        return False
    before = text[:start].rstrip()
    if not before:
        return False
    # A word opening a sentence, a heading, a list item or a table cell is
    # capitalised for position, not because it is a name.
    return before[-1] not in ".!?:|#-—–*" and not before.endswith("\n")


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

# "$247,900" — the full form the convention asks for on first use.
FULL_CURRENCY_RE = re.compile(r"[$£€]\s?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
# "$248k", "$1.4m" — the abbreviated form. Billions are excluded: a balance
# sheet quoted as "$40bn" has no full form anyone would write, so the
# convention does not reach it.
ABBREV_CURRENCY_RE = re.compile(r"[$£€]\s?\d+(?:\.\d+)?\s?[kKmM]\b")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class Document:
    """One Markdown file, parsed once and checked many times."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self.rel = path.relative_to(REPO).as_posix()
        except ValueError:  # a file outside the repository, e.g. a scratch test
            self.rel = path.as_posix()
        self.raw = path.read_text(encoding="utf-8")
        self.lines = self.raw.splitlines()
        fenced = fence_map(self.lines)
        front_matter = front_matter_map(self.lines)
        self.fenced = [code or metadata for code, metadata in zip(fenced, front_matter)]
        self.tables = find_tables(self.lines, self.fenced)
        self.prose = strip_inline_code(blank_lines(self.raw, front_matter))
        # Spelling alone also reads the front matter's values.
        self.spelled = strip_inline_code(blank_keys(self.raw, front_matter))
        self.notes: list[str] = []

    # -- hygiene ----------------------------------------------------------

    def check_headings(self) -> list[Finding]:
        findings: list[Finding] = []
        previous_level = 0
        for index, line in enumerate(self.lines):
            if self.fenced[index]:
                continue
            if SETEXT_RE.match(line) and index > 0 and not self.fenced[index - 1]:
                above = self.lines[index - 1].strip()
                # An underline only makes a heading if a paragraph line sits
                # directly above it; otherwise it is a thematic break.
                if above and not above.startswith(("|", ">", "#", "-", "*", "+")):
                    findings.append(
                        Finding(
                            self.rel,
                            index + 1,
                            f"underline-style heading — use ATX "
                            f"({'#' if line.startswith('=') else '##'} {above[:40]})",
                        )
                    )
                    # Count it as the heading it was meant to be, so the level
                    # check downstream is not thrown off by it.
                    previous_level = 1 if line.startswith("=") else 2
                    continue
            match = ATX_RE.match(line)
            if not match:
                continue
            level = len(match.group(1))
            if previous_level and level > previous_level + 1:
                findings.append(
                    Finding(
                        self.rel,
                        index + 1,
                        f"heading level skips h{previous_level} to h{level}",
                    )
                )
            previous_level = level
        return findings

    def check_whitespace(self) -> list[Finding]:
        """Trailing whitespace and the closing newline.

        ``.editorconfig`` exempts Markdown from trimming because two trailing
        spaces are a hard line break. That exemption is honoured: a run of
        exactly two spaces on a line with content passes, everything else —
        a lone space, three or more, a tab, whitespace on a blank line — is a
        stray and is reported.
        """
        findings: list[Finding] = []
        for index, line in enumerate(self.lines):
            trailing = len(line) - len(line.rstrip(" \t"))
            if not trailing:
                continue
            content = line.rstrip(" \t")
            if content and line.endswith("  ") and not line.endswith("   ") and "\t" not in line[-trailing:]:
                continue  # deliberate hard line break
            what = "whitespace on a blank line" if not content else "trailing whitespace"
            findings.append(Finding(self.rel, index + 1, what))

        if not self.raw:
            findings.append(Finding(self.rel, 1, "file is empty"))
        elif not self.raw.endswith("\n"):
            findings.append(Finding(self.rel, len(self.lines), "no newline at end of file"))
        elif self.raw.endswith("\n\n"):
            findings.append(
                Finding(self.rel, len(self.lines) + 1, "more than one newline at end of file")
            )
        return findings

    def check_table_shape(self) -> list[Finding]:
        findings: list[Finding] = []
        for table in self.tables:
            width = len(table.header)
            if len(table.separator) != width:
                findings.append(
                    Finding(
                        self.rel,
                        table.separator_line,
                        f"separator row has {len(table.separator)} columns, "
                        f"header has {width}",
                    )
                )
            for cells, line_no in zip(table.rows, table.row_lines):
                if len(cells) != width:
                    findings.append(
                        Finding(
                            self.rel,
                            line_no,
                            f"row has {len(cells)} columns, header has {width}",
                        )
                    )
        return findings

    # -- house conventions -------------------------------------------------

    def numeric_columns(self, table: Table) -> list[int]:
        """Column indices whose body cells are predominantly figures.

        "Predominantly" is read as: at least two figures, and at least 70% of
        the cells that have any content at all.
        """
        numeric: list[int] = []
        for column in range(len(table.header)):
            cells = [row[column] for row in table.rows if column < len(row)]
            filled = [cell for cell in cells if DECORATION_RE.sub("", cell).strip()]
            if len(filled) < 2:
                continue
            figures = [cell for cell in filled if is_figure(cell)]
            if len(figures) >= 2 and len(figures) / len(filled) >= 0.7:
                numeric.append(column)
        return numeric

    def check_numeric_alignment(self) -> list[Finding]:
        findings: list[Finding] = []
        for table in self.tables:
            for column in self.numeric_columns(table):
                if column >= len(table.separator):
                    continue
                spec = table.separator[column]
                if spec.endswith(":") and not spec.startswith(":"):
                    continue
                heading = table.header[column] or f"column {column + 1}"
                findings.append(
                    Finding(
                        self.rel,
                        table.separator_line,
                        f"numeric column {column + 1} ({heading!r}) is not "
                        f"right-aligned — separator cell is '{spec}', expected '---:'",
                    )
                )
        return findings

    def check_spelling(self) -> list[Finding]:
        """Report the minority spelling where a document mixes the two.

        Either standard is allowed; only the mixture is a finding, so the
        majority usage decides which occurrences are reported.
        """
        counts = {BRITISH: 0, AMERICAN: 0}
        hits: list[tuple[int, str, str, str]] = []  # line, variant, word, pair
        for match in WORD_RE.finditer(self.spelled):
            word = match.group()
            result = classify_word(word)
            if not result:
                continue
            if looks_like_proper_noun(self.spelled, match.start(), word):
                continue
            variant, canonical = result
            counts[variant] += 1
            line = self.spelled[: match.start()].count("\n") + 1
            hits.append((line, variant, word, canonical))

        if not (counts[BRITISH] and counts[AMERICAN]):
            if hits:
                settled = BRITISH if counts[BRITISH] else AMERICAN
                marked = counts[BRITISH] + counts[AMERICAN]
                self.notes.append(
                    f"spelling: {settled.lower()} throughout "
                    f"({marked} marked word{'' if marked == 1 else 's'})"
                )
            return []

        if counts[BRITISH] == counts[AMERICAN]:
            # No majority to defer to: report every marked word and let the
            # author pick the standard.
            return [
                Finding(
                    self.rel,
                    line,
                    f"mixed spelling — {word!r} is {variant.lower()}, and this "
                    f"document uses both standards equally "
                    f"({counts[BRITISH]} to {counts[AMERICAN]}); pair is {canonical}",
                )
                for line, variant, word, canonical in hits
            ]

        minority = BRITISH if counts[BRITISH] < counts[AMERICAN] else AMERICAN
        majority = AMERICAN if minority == BRITISH else BRITISH
        return [
            Finding(
                self.rel,
                line,
                f"mixed spelling — {word!r} is {variant.lower()} in a document "
                f"that is otherwise {majority.lower()} "
                f"({counts[majority]} to {counts[minority]}); pair is {canonical}",
            )
            for line, variant, word, canonical in hits
            if variant == minority
        ]

    def check_currency(self) -> list[Finding]:
        """Abbreviated currency before the full form is stated."""
        full = FULL_CURRENCY_RE.search(self.prose)
        abbrev = ABBREV_CURRENCY_RE.search(self.prose)
        if not abbrev:
            return []
        if full and full.start() < abbrev.start():
            return []
        line = self.prose[: abbrev.start()].count("\n") + 1
        tail = (
            f"; the full form first appears at line "
            f"{self.prose[: full.start()].count(chr(10)) + 1}"
            if full
            else "; no full form appears in this document"
        )
        return [
            Finding(
                self.rel,
                line,
                f"abbreviated currency {abbrev.group().strip()!r} used before any "
                f"full form{tail}",
            )
        ]

    def check_estimate_labelling(self) -> list[Finding]:
        """Warn where a table of cost or volume figures carries no label.

        The label may be a nearby "planning estimate", a "Basis" column, or a
        stated basis in the table itself — any of the three is enough.
        """
        if not self.rel.startswith(ESTIMATE_TREES):
            return []
        findings: list[Finding] = []
        for table in self.tables:
            headers = {DECORATION_RE.sub("", cell).strip().lower() for cell in table.header}
            if headers & COMMERCIAL_HEADERS:
                continue
            columns = self.numeric_columns(table)
            if not columns:
                continue
            cells = [
                row[column]
                for column in columns
                for row in table.rows
                if column < len(row)
            ]
            if not any(carries_money_or_volume(cell) for cell in cells):
                continue

            window_start = max(0, table.header_line - 9)
            window_end = min(
                len(self.lines),
                table.row_lines[-1] if table.row_lines else table.separator_line,
            )
            context = "\n".join(self.lines[window_start:window_end]).lower()
            if any(token in context for token in ESTIMATE_MARKERS):
                continue
            findings.append(
                Finding(
                    self.rel,
                    table.header_line,
                    "table of cost or volume figures with no 'planning estimate' "
                    "or 'Basis' nearby — CLAUDE.md requires figures to be "
                    "labelled as estimates until measured",
                    warning=True,
                )
            )
        return findings

    # -- driver -----------------------------------------------------------

    def run(self) -> tuple[list[Finding], list[Finding]]:
        failures: list[Finding] = []
        for check in (
            self.check_headings,
            self.check_whitespace,
            self.check_table_shape,
            self.check_numeric_alignment,
            self.check_spelling,
            self.check_currency,
        ):
            failures.extend(check())
        warnings = self.check_estimate_labelling()
        return failures, warnings


# ---------------------------------------------------------------------------
# Repairs
# ---------------------------------------------------------------------------


def fix(document: Document) -> list[str]:
    """Apply the mechanical repairs to one file. Returns what was changed."""
    changed: list[str] = []
    lines = list(document.lines)

    # Numeric alignment: rewrite the separator cell, preserving its width.
    for table in document.tables:
        wrong = [
            column
            for column in document.numeric_columns(table)
            if column < len(table.separator)
            and not (
                table.separator[column].endswith(":")
                and not table.separator[column].startswith(":")
            )
        ]
        if not wrong:
            continue
        cells = list(table.separator)
        for column in wrong:
            dashes = max(3, len(cells[column].strip(": ")))
            cells[column] = "-" * dashes + ":"
        lines[table.separator_line - 1] = "|" + "|".join(cells) + "|"
        changed.append(
            f"line {table.separator_line}: right-aligned column(s) "
            + ", ".join(str(column + 1) for column in wrong)
        )

    # Trailing whitespace, keeping deliberate two-space hard line breaks.
    for index, line in enumerate(lines):
        trailing = len(line) - len(line.rstrip(" \t"))
        if not trailing:
            continue
        content = line.rstrip(" \t")
        if content and line.endswith("  ") and not line.endswith("   ") and "\t" not in line[-trailing:]:
            continue
        lines[index] = content
        changed.append(f"line {index + 1}: removed trailing whitespace")

    text = "\n".join(lines)
    text = text.rstrip("\n") + "\n" if text.strip() else text
    if text != document.raw:
        if not document.raw.endswith("\n"):
            changed.append("added the closing newline")
        elif document.raw.endswith("\n\n"):
            changed.append("removed blank lines at end of file")
        document.path.write_text(text, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# Generated files whose text is not ours to restyle: the image's third-party notices reproduce upstream licences
# verbatim, in whatever spelling and currency each licence uses. Skipped by name when a directory is walked; naming
# one explicitly still checks it.
GENERATED = {"demos/kge/THIRD_PARTY_NOTICES.md", "demos/kge/portal/THIRD_PARTY_NOTICES.md"}
SKIP_DIRS = {"build", ".venv", "node_modules"}


def collect(paths: list[str]) -> list[Path]:
    """Expand arguments into Markdown files, skipping nested dot-directories."""
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = REPO / candidate
        if candidate.is_file():
            found.append(candidate)
        elif candidate.is_dir():
            found.extend(
                path
                for path in candidate.rglob("*.md")
                if not any(part.startswith(".") or part in SKIP_DIRS for part in path.relative_to(candidate).parts)
                and (
                    not path.resolve().is_relative_to(REPO)
                    or path.resolve().relative_to(REPO).as_posix() not in GENERATED
                )
            )
        else:
            print(f"{raw}: no such file or directory", file=sys.stderr)
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="House-style validator for the Markdown in this repository."
    )
    parser.add_argument("paths", nargs="*", default=None, help="files or directories")
    parser.add_argument(
        "--strict", action="store_true", help="treat warnings as failures"
    )
    parser.add_argument(
        "--list", action="store_true", dest="listing",
        help="print what each file was checked for, then the findings",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="apply the mechanical repairs (whitespace, closing newline, "
             "numeric alignment) and report what changed",
    )
    args = parser.parse_args(argv)

    files = collect(args.paths or DEFAULT_PATHS)
    if not files:
        print("no Markdown files found", file=sys.stderr)
        return 1

    if args.fix:
        touched = 0
        for path in files:
            document = Document(path)
            changes = fix(document)
            if changes:
                touched += 1
                print(document.rel)
                for change in changes:
                    print(f"  {change}")
        print(f"\n{touched} file(s) changed of {len(files)} checked.")
        return 0

    failures: list[Finding] = []
    warnings: list[Finding] = []
    for path in files:
        document = Document(path)
        file_failures, file_warnings = document.run()
        failures.extend(file_failures)
        warnings.extend(file_warnings)
        if args.listing:
            tables = len(document.tables)
            numeric = sum(len(document.numeric_columns(t)) for t in document.tables)
            detail = [f"{tables} table(s), {numeric} numeric column(s)"]
            detail.extend(document.notes)
            print(f"{document.rel}: {'; '.join(detail)}")
    if args.listing:
        print()

    for finding in sorted(failures, key=lambda f: (f.path, f.line)):
        print(finding.render())
    if failures and warnings:
        print()
    for finding in sorted(warnings, key=lambda f: (f.path, f.line)):
        print(finding.render())

    print(
        f"\n{len(files)} file(s) checked: "
        f"{len(failures)} failure(s), {len(warnings)} warning(s)."
    )
    if failures:
        return 1
    if warnings and args.strict:
        print("--strict: warnings counted as failures.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
