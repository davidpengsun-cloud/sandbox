"""The question parser: what a question asks (Request), read by rules from its words and the client's domain (parse), and
the calendar, fiscal-quarter, currency and window readings it rests on."""
from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass, field, replace

from kge.compiler.checks import ISO_4217
from kge.pipeline.trace import graph_read
from kge.services.ask.engine.currencies import NAMES as CURRENCY_NAMES


MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
ABBREVIATED = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
MONTH = "(" + "|".join([*MONTHS, *ABBREVIATED]) + ")"
WORDS = "__words__"
CANDIDATE = "__candidate__:"
NAMED_FISCAL_START = 4
UNPLACED_RANGE = "The question asks for a period range or comparison."
RANGE_CUES = r"since|from|between|until|through|to|compared (?:with|to)|vs\.?|versus"


def pair(a: str, b: str, domain) -> str:
    """Two units as a pair's name, <unit>-<unit>, the one the pack lists first first. domain: the Engine, or anything
    that carries the client's domain as it does (UNIT_ORDER here)."""
    a, b = sorted((a, b), key=domain.UNIT_ORDER.index)
    return f"{a}-{b}"


YEAR = re.compile(r"\b(20\d{2})\b")


@dataclass
class Period:
    kind: str
    value: str
    @property
    def plan_kind(self) -> str:
        return "day" if self.kind == "as_of" else self.kind

    def label(self) -> str:
        return f"{self.kind} {self.value}"


@dataclass
class Request:
    """A parsed question. time_mode to counted are what a context's query reads (kge/semantics/query.py), from the pack's
    phrases. restricted holds the restricted columns asked for by name, which no certified join across units answers;
    filtered holds the column each filter's vocabulary names, so a context applies a filter only in that column's
    unit."""
    question: str
    term: str | None = None
    terms_found: list[str] = field(default_factory=list)
    candidate: str | None = None
    units: list[str] = field(default_factory=list)
    period: Period | None = None
    filters: list[tuple[str, str, object]] = field(default_factory=list)
    agg: str = "sum"
    definitional: bool = False
    special: str | None = None
    all_units: bool = False
    time_mode: str = "current"
    latest: bool = False
    group_by: list[str] = field(default_factory=list)
    entity: tuple[str, str] | None = None
    counted: str | None = None
    restricted: list[str] = field(default_factory=list)
    filtered: list[str | None] = field(default_factory=list)
    problem: str | None = None
    period_by: str = "rules"
    model_period_allowed: bool = False


COMMON = set("""a about across all an and any are as at average bank brought by calendar count date day did do does during
enterprise fiscal for from fy give group had has have how in into is it many me month much number of on or over per quarter
show tell than that the their there these they this those to was were what when where which who whose with year""".split())


def question_words(question: str, domain=None, stop_words: set[str] = COMMON) -> list[str]:
    """The question's own words that could name a column, singular, in order, once each. domain: the Engine, or
    anything that carries the client's domain as it does (UNIT_WORDS here: a unit's word names no column)."""
    out: list[str] = []
    for w in re.findall(r"[a-z][a-z_]+", question.lower()):
        if w in stop_words or w in MONTHS or (domain is not None and w in domain.UNIT_WORDS):
            continue
        w = w[:-3] + "y" if w.endswith("ies") else w[:-1] if w.endswith("s") and not w.endswith("ss") and len(w) > 3 else w
        if w not in out:
            out.append(w)
    return out


GROUNDING_STOP = set("""a about across all an and any are as at by did do does during for from give had has have how in into is
it many me much of on or over per show tell than that the their there these they this those to was were what when
where which who whose with be been being can could may might must shall should will would why whom please explain define
definition today tomorrow yesterday latest last next current period week daily weekly monthly quarterly yearly annually
monday tuesday wednesday thursday friday saturday sunday weekend date day month quarter year fy
we us our you your it its them""".split()) | set(ABBREVIATED)


def content_words(text: str, domain=None) -> set[str]:
    """Reuse column-word tokenisation, months, numeric-date stripping and singulars with grammatical stop words.
    Column-search exclusions must not hide content such as calendar standards. Unit ids and labels never ground a question."""
    units = domain.UNIT_LABEL if domain is not None else {}
    unit_words = set(question_words(" ".join([*units, *units.values()]), stop_words=GROUNDING_STOP))
    return set(question_words(text, stop_words=GROUNDING_STOP)) - GROUNDING_STOP - unit_words


def grounded(question: str, passages: list[dict], domain=None) -> bool:
    """At least one supplied passage shares a content word with the question, excluding the loaded pack's unit names."""
    words = content_words(question, domain)
    return any(words & content_words(p.get("excerpt") or "") for p in passages)


def fiscal_quarter(year: int, quarter: int, year_start_month: int) -> tuple[int, int]:
    """Calendar quarter `quarter` of `year` as (fiscal year, fiscal quarter) on a fiscal year that starts in
    year_start_month, the first month of a calendar quarter (1, 4, 7 or 10). A fiscal year is named for the calendar
    year it ends in; one that starts in January is the calendar year."""
    month = 3 * quarter - 2
    start = year if month >= year_start_month else year - 1
    return start + (year_start_month > 1), (month - year_start_month) % 12 // 3 + 1


def fiscal_from_calendar(cq: str, year_start_month: int) -> str:
    """A calendar quarter (2025-Q2) as its fiscal quarter's label (FY26-Q1 on a year from April)."""
    fy, n = fiscal_quarter(int(cq[:4]), int(cq[-1]), year_start_month)
    return f"FY{fy % 100:02d}-Q{n}"


def calendar_from_fiscal(fq: str, year_start_month: int) -> str:
    """A fiscal quarter's label (FY26-Q1) as its calendar quarter (2025-Q2 on a year from April)."""
    fy, n = 2000 + int(fq[2:4]), int(fq[-1])
    month = year_start_month + 3 * (n - 1)
    year = fy - (year_start_month > 1) + (month - 1) // 12
    return f"{year}-Q{(month - 1) % 12 // 3 + 1}"


def quarter_label(cq: str, calendar: dict) -> str:
    """A calendar quarter (2025-Q2) as a calendar labels its quarters: its quarter_label ({yyyy}-Q{n}, FY{yy}-Q{n})
    filled in with the fiscal year and quarter on its year_start_month."""
    fy, n = fiscal_quarter(int(cq[:4]), int(cq[-1]), calendar["year_start_month"])
    return calendar["quarter_label"].format(yyyy=fy, yy=f"{fy % 100:02d}", n=n)


def fiscal_start(calendars: dict[str, dict]) -> int:
    """The month the fiscal year a question's FY label names starts in: the client's calendar whose quarters are labelled
    FY..., the first by id; January if it has none."""
    return next((c["year_start_month"] for _, c in sorted(calendars.items()) if c["quarter_label"].startswith("FY")), 1)


def month_name(n: int) -> str:
    return next(m for m, i in MONTHS.items() if i == n).capitalize()


def spoken(name: str) -> str:
    """A pattern for a name as a question says it: its words (split on spaces, underscores and hyphens) apart or joined
    by any of those, each singular or plural (compound names split into their words)."""
    return r"[\s_-]+".join(re.escape(w) + "s?" for w in re.split(r"[\s_-]+", name.lower()) if w)


def plural_forms(phrase: str) -> tuple[str, str]:
    plural = phrase[:-1] + 'ies' if len(phrase) > 1 and phrase[-1].lower() == 'y' and phrase[-2].lower() not in 'aeiou' else phrase + 's'
    return phrase, plural


def named_as(domain, table: str, column: str) -> list[tuple[str, str]]:
    """A column's names, (table, column): as the warehouse names it, and by its business name where the pack gives one
    (BUSINESS on the engine), so a question that names a column the business way names it too."""
    business = (getattr(domain, "BUSINESS", None) or {}).get(f"{table}.{column}")
    return [(table, column)] + ([tuple(business.split(".", 1))] if business else [])


def restricted_asked(question: str, domain) -> list[str]:
    """The restricted columns a question asks for by name, <unit>.<table>.<column>, in the policy's order. A column's
    name is read from the graph, never from here: its words apart or joined, each singular or plural (a singular name or its plural), its initials where it has three words or more (initials), an acronym
    the pack says spells it, or a phrase its policy gives for it (e-mail; birthdate, birthday, born), each word of it
    the same way. domain: the Engine, or anything that carries RESTRICTED_COLUMNS, ACRONYMS and RESTRICTED_PHRASES as it
    does."""
    ql = question.lower()
    spelled = getattr(domain, "ACRONYMS", None) or {}
    phrases = getattr(domain, "RESTRICTED_PHRASES", None) or {}
    out = []
    for unit, table, column in getattr(domain, "RESTRICTED_COLUMNS", None) or ():
        said = []
        for _, name in named_as(domain, table, column):
            words = [w for w in name.lower().split("_") if w]
            said.append(spoken(name))
            if len(words) >= 3:
                said.append("".join(w[0] for w in words) + "s?")
        said += [re.escape(a.lower()) + "s?" for a, cols in spelled.items() if f"{unit}.{table}.{column}" in cols]
        said += [spoken(p) for p in phrases.get(f"{unit}.{table}.{column}", []) if spoken(p)]
        if re.search(r"\b(?:" + "|".join(said) + r")\b", ql):
            out.append(f"{unit}.{table}.{column}")
    return out


def restricted_named(question: str, domain, unit: str) -> list[str]:
    """The restricted columns of unit a question names by the column's own name, <unit>.<table>.<column>, in the
    policy's order: the column's words where it has two or more (last names, date of birth), its table's word before
    its own (policyholder names, customer email), a qualified column name, its initials where it has three words or more (initials), or an acronym
    the pack says spells it, or a declared denial phrase naming the column. A relative age phrase is not a named
    attribute when it describes another object. Never a one-word column
    on its own (email, name), which a question says in other senses too (complaints received by email): a narrower
    reading than restricted_asked, which the guardrail across units keeps. domain: as for restricted_asked."""
    ql = question.lower()
    spelled = getattr(domain, "ACRONYMS", None) or {}
    phrases = getattr(domain, "RESTRICTED_PHRASES", None) or {}
    out = []
    for u, table, column in getattr(domain, "RESTRICTED_COLUMNS", None) or ():
        if u != unit:
            continue
        said = [re.escape(f'{u}.{table}.{column}'.lower()), re.escape(f'{table}.{column}'.lower())]
        for t, name in named_as(domain, table, column):
            words = [w for w in name.lower().split("_") if w]
            said += [spoken(f"{t} {name}")] + ([spoken(name)] if len(words) >= 2 else [])
            if len(words) >= 3:
                said.append("".join(w[0] for w in words) + "s?")
        said += [re.escape(a.lower()) + "s?" for a, cols in spelled.items() if f"{u}.{table}.{column}" in cols]
        said += [spoken(p) for p in phrases.get(f"{u}.{table}.{column}", ())
                 if len(p.split()) > 1 and not re.search(r"\b(?:older|younger) than\b", p, re.I)]
        if re.search(r"\b(?:" + "|".join(said) + r")\b", ql):
            out.append(f"{u}.{table}.{column}")
    return out


OUTSIDE_PACK = "No glossary term recognised in the question. The question is outside what this pack covers."


def normalise_question(question: str) -> str:
    """Read compatibility characters and every Unicode decimal digit as plain text, dropping invisible separators."""
    text = unicodedata.normalize("NFKC", question)
    text = "".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text if c not in "\u200b\ufeff")
    return " ".join(text.split())


def read_period(q: str, definitional: bool = False, entity_words=()) -> tuple[Period | None, str | None]:
    """The rules' period and diagnostic, independent of whether a model may repair it."""
    period, problem, _ = _read_period(q, definitional, entity_words)
    return period, problem


def _read_period(q: str, definitional: bool, entity_words, vocabulary=(), evidence=None) -> tuple[Period | None, str | None, bool]:
    """Read whole date expressions with one token grammar, never a substring of a date.

    Numeric dates use an unambiguous day/month ordering; no pack declares an ordering
    for dates whose two components could both be months. Halves and relative windows
    marked YTD are refused because the query planner has no such grain. A year needs a date cue.
    The third value permits a model reading only for a wholly unread period. Keep
    scanning after the first error to detect a partial reading or a range elsewhere,
    while retaining the rules' original diagnostic. A wholly unread expression
    needs a single token group and no range cue anywhere in the question.
    """
    q = normalise_question(q).lower()
    if range_cued(q, []):
        return None, UNPLACED_RANGE, False
    tokens = list(re.finditer(r"[\w\u200c\u200d\u00ad]+|[^\w\s]", q))
    words = [t[0] for t in tokens]
    months = MONTHS | ABBREVIATED
    cues = {"in", "for", "during", "at", "of", "fy", "cy"}
    range_words = {"since", "from", "between", "until", "through"}
    readings = []
    spans = []
    unread_spans = []
    token_spans = []
    problem = None
    impossible = False
    saw_token = False
    entities = {w for phrase in entity_words for w in (phrase.lower(), phrase.lower() + "s")}

    def word(i):
        return words[i] if 0 <= i < len(words) else ""

    def year(i):
        value = word(i)
        if value in {"'", "’"}:
            value = word(i + 1)
            if value.isdecimal() and len(value) == 2:
                return 2000 + int(value), i + 2
        if value.isdecimal() and len(value) == 4 and 1900 <= int(value) <= 2099:
            return int(value), i + 1
        raise ValueError

    def separator(i):
        return i + 1 if word(i) in {",", "of", "-"} else i

    def quarter(value):
        if len(value) == 2 and value[0] == "q" and value[1] in "1234":
            return int(value[1])
        if len(value) == 2 and value[1] == "q" and value[0] in "1234":
            return int(value[0])
        return {"first": 1, "second": 2, "third": 3, "fourth": 4}.get(value)

    def date(y, m, d, start, end):
        try:
            value = datetime.date(y, m, d)
        except ValueError:
            text = q[tokens[start].start():tokens[end - 1].end()]
            raise ArithmeticError(f"That date does not exist: {text}.") from None
        if words[max(0, start - 2):start] == ["quarter", "to"]:
            return Period("calendar_quarter", f"{y}-Q{(m - 1) // 3 + 1}")
        return Period("as_of", value.isoformat())

    def month(y, m):
        nonlocal impossible
        try:
            return Period("month", datetime.date(y, m, 1).isoformat())
        except ValueError:
            impossible = True
            raise

    def expression(i):
        value, following = word(i), word(i + 1)
        if value == "quarter":
            period, end = expression(i + 1 + (following == "to"))
            if period and period.kind in {"calendar_quarter", "fiscal_quarter"}:
                return period, end
            raise ValueError
        if value in months:
            j = i + 1 + (following == "." and value in ABBREVIATED)
            if word(j).isdecimal() and len(word(j)) <= 2 and word(j + 1) == ",":
                y, end = year(j + 2)
                return date(y, months[value], int(word(j)), i, end), end
            y, end = year(separator(j))
            return month(y, months[value]), end
        day = value[:-2] if value.endswith(("st", "nd", "rd", "th")) else value
        j = i + 1 + (following == "of")
        if day.isdecimal() and word(j) in months:
            m = months[word(j)]
            bare_full_month = value.isdecimal() and following in MONTHS and word(i - 1) not in {"at", "to"}
            j += 1 + (word(j + 1) == ".")
            y, end = year(separator(j))
            parsed = date(y, m, int(day), i, end)
            return (Period("month", datetime.date(y, m, 1).isoformat()) if bare_full_month else parsed), end
        if value.isdecimal() and following in {"/", "-", "."} and word(i + 2).isdecimal():
            parts, end = [int(value)], i + 1
            while word(end) == following and word(end + 1).isdecimal():
                parts.append(int(word(end + 1)))
                end += 2
            if len(parts) == 3:
                if len(value) == 4:
                    y, m, d = parts
                else:
                    a, b, y = parts
                    if a <= 12 and b <= 12 and a != b:
                        raise ValueError
                    d, m = (a, b) if a > 12 else (b, a)
                if not 1900 <= y <= 2099:
                    raise ValueError
                return date(y, m, d, i, end), end
            if len(parts) == 2:
                y, m = parts if len(value) == 4 else parts[::-1]
                if not 1900 <= y <= 2099:
                    raise ValueError
                return month(y, m), end
            raise ValueError
        if value.startswith("fy"):
            suffix = value[2:]
            if len(suffix) == 2 and suffix.isdecimal() and following == "-" and quarter(word(i + 2)):
                return Period("fiscal_quarter", f"FY{suffix}-Q{quarter(word(i + 2))}"), i + 3
            raise ValueError
        if value.startswith("cy"):
            suffix = value[2:]
            if len(suffix) == 4 and suffix.isdecimal() and 1900 <= int(suffix) <= 2099:
                return Period("year", suffix), i + 1
            if value == "cy":
                y, end = year(i + 1)
                return Period("year", str(y)), end
            raise ValueError
        n = quarter(value)
        if n:
            j = i + 1
            if value in {"first", "second", "third", "fourth"}:
                if following != "quarter":
                    raise ValueError
                j += 1
            y, end = year(separator(j))
            return Period("calendar_quarter", f"{y}-Q{n}"), end
        if len(value) == 4 and value[0] in "1234" and value[1] == "q" and value[2:].isdecimal():
            return Period("calendar_quarter", f"20{value[2:]}-Q{value[0]}"), i + 1
        j = separator(i + 1)
        if value.isdecimal() and len(value) == 4 and quarter(word(j)):
            y, _ = year(i)
            return Period("calendar_quarter", f"{y}-Q{quarter(word(j))}"), j + 1
        y, end = year(i)
        if word(i - 1) in cues | range_words or (word(i - 1) == "to" and readings):
            return Period("year", str(y)), end
        return None, i + 1

    i = 0
    while i < len(words):
        value, following = word(i), word(i + 1)
        if value in entities and following.isdecimal():
            i += 2
            continue
        modal = definitional and i == 0 and value == "may" and not (following.isdecimal() or following in {"of", "'", "’"})
        numeric_form = value.isdecimal() and following in {"/", "-", "."} and word(i + 2).isdecimal()
        ordinal = (value[:-2].isdecimal() and value.endswith(("st", "nd", "rd", "th"))
                   and (following in months or following == "of" or word(i - 1) == "at"))
        marker = value in months or value.rstrip("s") in months or any(value.startswith(m) and value[len(m):len(m) + 1].isdigit() for m in months)
        grain = (value in {"quarter", "half", "ytd", "fy", "cy"} or quarter(value)
                 or (value[:1] in {"q", "h"} and value[1:2].isdigit())
                 or (value.startswith(("fy", "cy")) and value[2:3].isdigit())
                 or (len(value) >= 2 and value[0].isdigit() and value[1] == "q"))
        if (value == 'quarter' and not re.search(r'\d', following) and re.search(
                r'\b(?:by|per|each)(?:\s+(?:(?:fiscal|calendar)\s+)?|\b[^.;?!]*(?:\band|,)\s*)$', q[:tokens[i].start()])):
            i += 1
            continue
        if value in {"first", "second", "third", "fourth"} and following not in {"quarter", "half"}:
            grain = False
        suspect_year = ((value[:2] in {"19", "20"} and len(value) >= 4
                         and (len(value) == 4 or word(i - 1) in cues | {"and", "to"}))
                        or (value.isdecimal() and len(value) == 4 and word(i - 1) in cues))
        unknown_month = (value.isalpha() and value not in COMMON | range_words and (i == 0 or word(i - 1) in cues)
                         and following.isdecimal() and len(following) == 4 and 1900 <= int(following) <= 2099)
        damaged_word = (any(c in value for c in "\u200c\u200d\u00ad")
                        and (following.isdecimal() or value.translate(dict.fromkeys(map(ord, "\u200c\u200d\u00ad"))) in months))
        candidate = (marker or grain or numeric_form or (value.isdecimal() and following in months)
                     or ordinal or suspect_year or unknown_month or damaged_word)
        if modal or not candidate:
            i += 1
            continue
        saw_token = True
        try:
            period, end = expression(i)
            if word(end) in {"/", "-", "."} and word(end + 1).isdecimal():
                raise ValueError
            half_year = period and period.kind == "year" and word(i - 2) == "half" and word(i - 1) == "of"
            if period and not (problem and half_year):
                readings.append(period)
                spans.append((tokens[i].start(), tokens[end - 1].end()))
            token_spans.append((tokens[i].start(), tokens[end - 1].end()))
            i = end
        except ArithmeticError as exc:
            problem = problem or str(exc)
            impossible = True
            unread_spans.append((tokens[i].start(), tokens[i].end()))
            token_spans.append((tokens[i].start(), tokens[i].end()))
            i += 1
        except ValueError:
            tail = q[tokens[i].start():].strip().rstrip(".?!")
            problem = problem or f"Please give a valid period: {tail}."
            unread_spans.append((tokens[i].start(), tokens[i].end()))
            token_spans.append((tokens[i].start(), tokens[i].end()))
            i += 1
    priority = {"as_of": 0, "fiscal_quarter": 1, "calendar_quarter": 2, "month": 3, "year": 4}
    if saw_token and not readings and not definitional:
        known = COMMON | set(re.findall(r"\w+", " ".join(vocabulary).lower()))
        joined = any(m[1] not in known and m[2] not in known
                     and any(start < m.end() and end > m.start() for start, end in token_spans)
                     for m in re.finditer(r"\b(\w+)(?:\s+\d{4})?[\s,]+(?:and(?:/or)?|or|then)\s+(\w+)(?:\s+\d{4})?\b", q))
        if re.search(r"\b(?:" + RANGE_CUES + r")\b", q) or joined:
            return None, problem or UNPLACED_RANGE, False
        groups = []
        for start, end in token_spans:
            if (groups and not re.search(r"\d{4}", q[groups[-1][0]:groups[-1][1]])
                    and re.fullmatch(r"[\s,.'’/-]*(?:of\s+)?", q[groups[-1][1]:start])):
                groups[-1] = (groups[-1][0], end)
            else:
                groups.append((start, end))
        if len(groups) != 1:
            return None, problem or "Please give a single valid period.", False
    if problem:
        repairable = not (definitional or impossible or readings or range_cued(q, sorted(spans + unread_spans)))
        return None, problem, repairable
    if range_cued(q, spans):
        return None, UNPLACED_RANGE, False
    if evidence is not None:
        evidence.extend((start, end, period) for (start, end), period in zip(spans, readings))
    return (min(readings, key=lambda p: priority[p.kind]) if readings else None), None, bool(saw_token and not readings and not definitional)


def range_cued(q: str, spans: list[tuple[int, int]]) -> bool:
    """A range cue attached to a reading cannot promote one endpoint to the requested period.

    Preserve the existing full-month day-to-day reading. Relative windows name
    their own periods; comparison and endpoint cues must adjoin a date reading,
    so comparisons between units and ordinary uses of from/through stay intact.
    """
    if re.search(r"\b(?:ytd|year[ -]to[ -]date|to date|so far this year|over the last \d+ months)\b", q):
        return True
    for start, end in spans:
        before, after = q[:start], q[end:]
        prefix = re.search(r"\b(?:" + RANGE_CUES + r")\s+"
                           r"(?:(?:the )?(?:start|end|beginning|month|quarter|year) of\s+|as (?:at|of)\s+)?$", before)
        if prefix and prefix[0].strip() == "to" and start == spans[0][0]:
            prefix = None
        suffix = re.match(r"\s+(?:until|through|compared (?:with|to)|vs\.?|versus|to (?:now|today|the present))\b", after)
        if re.match(r"\s+(?:compared (?:with|to)|vs\.?|versus)\s+(?:the\s+)?previous\s+(?:period|year)\b", after):
            suffix = None
        if not (prefix or suffix):
            continue
        full_month = re.search(r"\b(?:from\s+)?1 (" + "|".join(MONTHS) + r") (\d{4}) to (\d{2}) \1 \2\b", q)
        if full_month and full_month.start() <= start and end <= full_month.end():
            y, m, d = int(full_month[2]), MONTHS[full_month[1]], int(full_month[3])
            last = datetime.date(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)
            if d == last.day and not re.search(r"\b(?:since|until|through|compared|vs|versus|between)\b", before + after):
                continue
        return True
    return False


def model_period_agreement(q: str, period: Period, vocabulary) -> str | None:
    """A proposed period must preserve every written year and any explicit grain.

    Known question vocabulary before a year distinguishes a bare year from an
    unread date name. Unknown date words remain available to the model.
    """
    if any(year != period.value[:4] for year in re.findall(r"(?<!\d)\d{4}(?!\d)", q)):
        return "The model's period year does not agree with every year in the question."
    grains = [
        (r"\b(?:h\d+\w*|half|halves|semiannual)\b", "half-year"),
        (r"\b(?:weeks?|weekly|w\d+)\b", "week"),
        (r"\b(?:quarters?|quarterly|q\d+\w*|[1-4]q\w*)\b", "calendar_quarter"),
        (r"\b(?:months?|monthly)\b", "month"),
        (r"\b(?:days?|daily)\b", "as_of"),
        (r"\b(?:years?|yearly|annual|(?:fy|cy)\d+\w*)\b", "year"),
    ]
    grain = next((kind for pattern, kind in grains if re.search(pattern, q)), None)
    known = COMMON | {"total"} | set(re.findall(r"\w+", " ".join(vocabulary).lower()))
    year = re.search(r"(?<![\w./-])\d{4}(?![\w/-]|\.\d)", q)
    if grain is None and year:
        before = re.findall(r"\w+", q[:year.start()])
        if not before or before[-1] in known:
            grain = "year"
    if grain and grain != period.kind:
        return "The model's period grain does not agree with the question's " + grain.replace("_", " ") + "."
    return None


def qualified_phrases(question, phrases):
    """A declared certified phrase can qualify its next measure across a period or member name."""
    qualified = []
    for modifier in re.finditer(r'\bcertified\b', question, re.I):
        matches = []
        for phrase in phrases:
            if not phrase.casefold().startswith('certified '):
                continue
            noun = phrase[len('certified '):]
            match = re.search(r'(?<!\w)' + re.escape(noun) + r'(?!\w)', question[modifier.end():], re.I)
            if match:
                matches.append((modifier.end() + match.start(), -len(noun), phrase))
        if matches:
            _, _, phrase = min(matches)
            qualified.append((modifier.start(), modifier.end(), phrase))
    return tuple(qualified)


def parse(question: str, terms: list[str], domain, aliases: bool = True, *, period: Period | None = None) -> Request:
    """terms: the glossary the question is read against ([] for none). domain: the Engine,
    or anything that carries the client's domain as it does: ALIASES, the phrases that
    name a term, and UNIT_WORDS, those that name a unit; and, for a context's query,
    TIME_MODES, NAMED_PERIODS, GROUP_BY, ENTITY_WORDS and ACRONYMS, and the
    RESTRICTED_COLUMNS a question may ask for by name (none if it carries none); and the
    rest of the client's vocabulary, which every run reads, resolving terms or not:
    INTENTS, the patterns that mark a definitional question, a count and an average,
    and FILTERS, the values and phrases that filter a column (kge/semantics/vocabulary.py; none if
    it carries none). aliases: the parser's own vocabulary, ALIASES, those phrases and
    the customer population; off for a run with no term resolution at all. A date may
    abbreviate its month (31 Mar 2025). A definition's bare year selects document
    versions through asked_window, not a warehouse period. period is an already
    validated model reading, used only for a wholly unread period, never an absent
    period, a definition, a range, an impossible date or a partial reading. Reading the rest
    again retains every non-period guard when an unreadable period is repaired."""
    r = Request(question=question)
    q = normalise_question(question)
    ql = q.lower()
    intents = getattr(domain, "INTENTS", None) or {}
    r.definitional = bool(intents.get("definitional") and intents["definitional"].match(q))
    def matched_word(pattern):
        match = re.search(pattern, ql)
        if match:
            graph_read("words", q[match.start():match.end()])
        return match
    found = [t for t in sorted(terms, key=len, reverse=True) if matched_word(r"\b" + re.escape(t.lower()) + r"s?\b")]
    r.terms_found = list(found)
    qualified = {phrase for _, _, phrase in qualified_phrases(ql, domain.ALIASES)} if aliases else set()
    for alias, term in sorted(domain.ALIASES.items() if aliases else (), key=lambda kv: -len(kv[0])):
        if alias in ql or alias in qualified:
            if alias in ql:
                start = ql.index(alias)
                graph_read("words", q[start:start + len(alias)])
            if term.startswith(CANDIDATE):
                r.candidate = term.removeprefix(CANDIDATE)
            elif term not in r.terms_found:
                r.terms_found.append(term)
    if r.candidate and r.candidate in terms:
        r.terms_found = [r.candidate] + [t for t in r.terms_found if t != r.candidate]
        r.candidate = None
    if r.terms_found and not r.candidate:
        r.term = r.terms_found[0]
    r.units = list(dict.fromkeys(u for w, u in domain.UNIT_WORDS.items() if re.search(r"\b" + w + r"\b", ql)))
    sets = getattr(domain, "SOURCE_SETS", {})
    r.all_units = not r.units and any(re.search(r"\b" + re.escape(phrase) + r"\b", ql) for phrase in sets)
    vocabulary = [*terms, *domain.ALIASES, *domain.UNIT_WORDS]
    period_reads = []
    r.period, r.problem, r.model_period_allowed = _read_period(ql, r.definitional, getattr(domain, "ENTITY_WORDS", ()) or (), vocabulary,
                                                            evidence=period_reads)
    for start, end, _ in period_reads:
        graph_read("words", q[start:end])
    if (r.definitional and r.term and r.period and re.match(r"^what (?:is|are)\b", ql)
            and not re.search(r"\b(?:definition|defined|meaning|mean|means|version|calendar|calculated|calculation)\b", ql)
            and "?" not in ql.rstrip("?!.")):
        r.definitional = False
    if r.definitional and r.period and r.period.kind == "year" and not re.search(r"\bin \d{4}\b", ql):
        r.period = None
    if r.period is None and r.problem is None and not r.model_period_allowed:
        r.period_by = "none"
    if r.model_period_allowed and period is not None:
        r.problem = model_period_agreement(ql, period, vocabulary)
        r.period, r.period_by = (None if r.problem else period), "model"
    if aliases:
        longest = lambda d: sorted((getattr(domain, d, None) or {}).items(), key=lambda kv: -len(kv[0]))  # noqa: E731
        said = lambda phrase: re.search(r"\b" + re.escape(phrase) + r"\b", ql)  # noqa: E731
        r.time_mode = next(("as_reported" if mode == "as_reported" else "current" for phrase, mode in longest("TIME_MODES") if said(phrase)),
                           "current")
        r.latest = any(target == "latest" and said(phrase) for phrase, target in longest("NAMED_PERIODS"))
        def group_said(phrase):
            match = said(phrase)
            if match:
                text = q[match.start():match.end()]
                graph_read("words", text[3:] if text.casefold().startswith("by ") else text)
            return match
        r.group_by = list(dict.fromkeys(col for phrase, col in longest("GROUP_BY") if group_said(phrase)))
        by = re.search(r"\bby ([A-Za-z][A-Za-z0-9_]*)\b", q)
        if not r.group_by and by:
            r.group_by = list((getattr(domain, "ACRONYMS", None) or {}).get(by.group(1).upper(), []))
            if r.group_by:
                graph_read("words", by.group(1))
        for phrase, entity in longest("ENTITY_WORDS"):
            pattern = r"\b(?:" + '|'.join(re.escape(p) for p in plural_forms(phrase)) + r")\b"
            named = re.search(pattern + r" (\d+)\b", ql)
            if named and r.entity is None:
                r.entity = (entity, named.group(1))
            elif re.search(pattern, ql) and r.counted is None:
                r.counted = entity
    r.restricted = restricted_asked(q, domain)
    columns = getattr(domain, "FILTER_COLUMNS", None) or ()
    currency_spans = [match.span() for match, _ in currency_names(q)]
    for i, (rx, col, op, group, literal) in enumerate(getattr(domain, "FILTERS", None) or ()):
        mm = next((match for match in rx.finditer(q if group else ql)
                   if not any(start < match.end() and match.start() < end for start, end in currency_spans)), None)
        if mm:
            r.filters.append((col, op, mm.group(group) if group else literal))
            r.filtered.append(columns[i] if i < len(columns) else None)
    if intents.get("count") and intents["count"].search(ql):
        r.agg = "count"
    elif intents.get("avg") and intents["avg"].search(ql):
        r.agg = "avg"
    if r.problem:
        r.special = "invalid_period"
    elif "join" in ql and not_joinable_named(ql, domain):
        r.special = "key_name_join"
    elif currency_asked(q, domain, r.term):
        r.special = "no_conversion_standard"
    elif r.candidate:
        r.special = "candidate_term"
    return r


def definition_request(request: Request) -> Request:
    """Recognise document-only paraphrases for governed Ask without replacing a numeric request or the control baseline."""
    if (request.term is None and request.counted is None and request.entity is None and not request.group_by
            and not request.filters and request.agg == "sum" and request.period is None and request.special is None
            and re.match(r"^(?:explain which|is it acceptable to)\b", normalise_question(request.question), re.I)):
        return replace(request, definitional=True)
    return request


def parse_context(question: str, terms: list[str], domain) -> Request:
    """Retain the default Context package's legacy classifications, outside numeric interpretation."""
    request = parse(question, terms, domain)
    q = normalise_question(question).lower()
    if (request.agg == "count" and re.search(r"\bclients\b", q)
            and not re.search(r"\b(?:customers?|households?|accounts?)\b", q)):
        request.counted = None
    if request.term is None and request.candidate is None and re.search(r"\bcustomers?\b|\bclients\b", q):
        request.term = "__population__"
    if not request.units and re.search(r"across the bank|\bbank\b|\bgroup\b|enterprise", q):
        request.all_units = True
    explicit = [col for phrase, col in sorted((getattr(domain, "GROUP_BY", None) or {}).items(), key=lambda item: -len(item[0]))
                if re.search(r"\b" + re.escape(phrase) + r"\b", q)]
    if explicit:
        request.group_by = explicit[:1]
    if request.special is None:
        if re.search(r"\b(?:email address|email of customer|date[- ]of[- ]birth|dob)\b", q):
            request.special = "restricted_lookup"
        elif re.search(r"\bplus\b", q) and len(request.terms_found) >= 2:
            request.special = "two_measures"
        elif re.search(r"\bwhich .* (?:are )?also\b", q):
            request.special = "cross_unit_entity"
    return request


def not_joinable_named(ql: str, domain) -> list[dict]:
    """The columns the domain declares not joinable (NOT_JOINABLE) whose name the question, in lower case, says. domain: the
    Engine, or anything that carries NOT_JOINABLE as it does (none if it carries none)."""
    return [x for x in getattr(domain, "NOT_JOINABLE", None) or () if re.search(r"(?<!\w)" + re.escape(x["name"].lower()) + r"(?!\w)", ql)]


ASKED_CURRENCY = re.compile(r"\b[Ii]n\s+([A-Z]{3})\b")


CURRENCY_STOP = COMMON | GROUNDING_STOP | set("""all top try cup mop pen bob cop gel mad ron rub sos
    add ago air also am art ask away back bad bar bed big bit box bus but buy call car cat cut dog down due each
    end far few fit get got her here him his hot job key law lay led left let lie lot low man map met more most
    new no nor not now off old one only other our out own pay put ran red rid right run sad saw say see set she
    sit six so son sun ten too two up use van war way web well why win won yes yet zoo""".split())
CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₩": "KRW", "₽": "RUB", "₺": "TRY", "$": "USD"}
CURRENCY_PATTERNS = tuple(
    (re.compile(r"\b" + re.escape(name) + r"s?\b", re.I), code)
    for name, code in sorted(
        [(name, code) for code, variants in CURRENCY_NAMES.items() for name in variants.split("|")]
        + [("pound", "GBP"), ("franc", "CHF")], key=lambda item: -len(item[0])))


def currency_names(question):
    """Natural currency names in longest-first order, with their source spans."""
    consumed = []
    for pattern, code in CURRENCY_PATTERNS:
        for match in pattern.finditer(question):
            if not any(start < match.end() and match.start() < end for start, end in consumed):
                consumed.append(match.span())
                yield match, code


def requested_currencies(question: str) -> list[str]:
    """Read ISO codes, currency names and symbols independently of a pack.

    Uppercase codes are explicit. Lowercase codes require a currency cue and must
    not be ordinary words; the programming language php additionally needs an
    explicit currency noun. Long names take precedence over unqualified names.
    """
    q = normalise_question(question)
    codes = []
    for match in re.finditer(r"\b[a-z]{3}\b", q, re.I):
        value = match[0]
        cue = re.search(r"\b(?:in|to)\s+$", q[:match.start()], re.I)
        context = re.match(r"\s+(?:currency|pesos?)\b", q[match.end():], re.I)
        lower_code = value.lower() not in CURRENCY_STOP and (value.lower() != "php" or context)
        if value.upper() in ISO_4217 and (value.isupper() or (cue and lower_code)):
            codes.append(value.upper())
    codes.extend(code for _, code in currency_names(q))
    for match in re.finditer(r"\b(?:in|to)\s+([A-Z]\s+[A-Z]\s+[A-Z])\b", q):
        code = "".join(match[1].split())
        if code in ISO_4217:
            codes.append(code)
    codes.extend(code for symbol, code in CURRENCY_SYMBOLS.items() if symbol in q)
    return list(dict.fromkeys(codes))


def currency_asked(question: str, domain, term: str | None = None) -> str | None:
    """The currency a question asks a figure in, where no certified measure of the domain is in it or
    converts from it: an ISO 4217 code or English name. None if it asks none of
    those, or the domain declares no currency. domain: the Engine, or anything that carries CURRENCIES and CONVERSIONS as
    it does, and TERM_CURRENCIES, an onboarded unit's currencies by term name: held only for a question whose term
    (the one it resolved to) is that unit's, so onboarding a unit moves no other question's answer."""
    scoped = getattr(domain, "TERM_CURRENCIES", None) or {}
    held = set(getattr(domain, "CURRENCIES", None) or ()) | {c[0] for c in getattr(domain, "CONVERSIONS", None) or ()}
    if not held and not scoped:
        return None
    held |= set(scoped.get(term, ()))
    return next((code for code in requested_currencies(question) if code not in held), None)


def no_conversion(code: str, domain, term: str | None = None) -> str:
    """Why a figure in code is not given, worded from the domain's certified currencies (the term's own, for an onboarded
    unit's term) and its conversions."""
    convs = [f"{a} to {b} ({c})" for a, b, c in domain.CONVERSIONS]
    scoped = (getattr(domain, "TERM_CURRENCIES", None) or {}).get(term, [])
    held = sorted(set(domain.CURRENCIES) | set(scoped)) if scoped else domain.CURRENCIES
    if not held:
        return f"No {code} conversion standard exists: no certified figure is in a currency."
    said = held[0] if len(held) == 1 else ", ".join(held[:-1]) + " and " + held[-1]
    tail = ("none is converted from another currency" if not convs else f"the only conversion is {convs[0]}" if len(convs) == 1
            else "the conversions are " + ", ".join(convs[:-1]) + " and " + convs[-1])
    return f"No {code} conversion standard exists: certified figures are in {said}, and {tail}."


def asked_window(req: Request, domain) -> tuple[str, str] | None:
    """The dates a question asks about, first and last day (ISO), or None for now.
    The parsed period if there is one, else a year the question names ("applies to 2024 reporting").
    domain: the Engine, or anything that carries the client's domain as it does (FISCAL_START, the month
    the fiscal year an FY label names starts in)."""
    p = req.period
    if p is None:
        m = YEAR.search(normalise_question(req.question))
        return (f"{m.group(1)}-01-01", f"{m.group(1)}-12-31") if m else None
    if p.kind == "as_of":
        return p.value, p.value
    if p.kind == "year":
        return f"{p.value}-01-01", f"{p.value}-12-31"
    if p.kind == "month":
        first = datetime.date.fromisoformat(p.value)
        last = datetime.date(first.year + (first.month == 12), first.month % 12 + 1, 1) - datetime.timedelta(days=1)
        return first.isoformat(), last.isoformat()
    cq = calendar_from_fiscal(p.value, domain.FISCAL_START) if p.kind == "fiscal_quarter" else p.value
    y, n = int(cq[:4]), int(cq[-1])
    last = datetime.date(y + (n == 4), (3 * n) % 12 + 1, 1) - datetime.timedelta(days=1)
    return f"{y}-{3 * n - 2:02d}-01", last.isoformat()


def in_force(doc: dict, window: tuple[str, str] | None) -> bool:
    """Whether a document was in force for the window, by the effective window certification
    landed (cert_from, cert_to; '' or None for open). With no window: in force now, so nothing
    has superseded it."""
    if window is None:
        return not doc.get("cert_to")
    return (doc.get("cert_from") or "") <= window[1] and (not doc.get("cert_to") or doc["cert_to"] >= window[0])


CATALOGUE_ABSTENTION = 'Ask answers governed figures and definitions. Use the product page or Search in the catalogue.'
FIGURE_FOLLOWUP = re.compile(
    r"\band\s+(?:what\s+is|what\s+was|what\s+are|how\s+many|how\s+much|count|sum|average|total)\b"
    r"|\b(?:and\s+)?count\s+the\s+matches\b",
    re.I,
)
CATALOGUE_DISCOVERY = re.compile(
    r"\bwhich\s+(?:data\s+)?(?:products?|tables?)\s+(?:holds?|have|has|contains?|stores?|includes?|should\s+i\s+(?:use|query)|are\s+(?:in|available))\b",
    re.I,
)
CATALOGUE_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\bwho\s+(?:owns|looks\s+after|maintains|manages|curates|govern[s]?|stewards|is\s+(?:in\s+charge\s+of|(?:the\s+)?(?:(?:business|technical|data|product)\s+)?(?:owner|steward|custodian|contact|lead|responsible|accountable)\s+(?:of|for)))\b",
    r"^\s*(?:please\s+)?(?:tell\s+me\s+|show\s+(?:me\s+)?)?(?:who|what|which\s+(?:team|person|role|group|steward|owner))\s+(?:owns|looks\s+after|maintains|manages|is\s+(?:in\s+charge\s+of|(?:accountable|responsible)\s+for|(?:the\s+)?(?:(?:business|technical|data|product)\s+)?(?:owner|steward|custodian|contact)\s+(?:of|for)))\b",
    r"\b(?:fields?|columns?|attributes?|schema|primary\s+key|foreign\s+keys?|grain|data\s+types?)\s+(?:of|in|for|does|are\s+in)\b",
    r"\bwhat\s+(?:is|are)\s+the\s+(?:fields?|columns?|attributes?|schema|primary\s+key|foreign\s+keys?|grain|lineage|upstream\s+sources?|downstream\s+\w+|refresh\s+\w+|sla|quality\s+score|owner|steward|custodian)\b",
    r"\bwhat\s+does\s+(?:the\s+)?(?:column|field|attribute)\b.*\bmean\b",
    r"\bwhat\s+(?:does|is\s+in)\b.*\b(?:table|product)\s*(?:contain|have|hold|store|include)?\b",
    r"\b(?:describe|tell\s+me\s+about|explain|summari[sz]e)\b.*\b(?:product|table)\b",
    r"\b(?:give\s+me\s+)?an?\s+overview\s+of\b",
    r"\bwhat\s+(?:data\s+)?(?:products|tables)\s+(?:are\s+(?:in|available)|belong\s+to|exist\s+in|does)\b",
    r"\bwhich\s+(?:domain|unit)\s+(?:does|has|owns|contains|holds)\b.*\b(?:table|product|belong\s+to)\b",
    r"^\s*(?:please\s+)?(?:list|show)(?:\s+me)?\s+(?:all\s+)?(?:the\s+)?(?:available\s+)?(?:data\s+)?(?:products|tables)(?:\s+(?:in|for|across|under|of|belonging\s+to)\b|\s*[?.!]*$)",
    r"\bwhere\s+(?:can\s+i|do\s+i|to)\s+find\b.*\bdata\b",
    r"\bwhere\s+does\b.*\b(?:product|table)\s+come\s+from\b",
    r"\bwhat\s+data\s+do\s+we\s+have\s+(?:about|on|for)\b",
    r"\b(?:refresh\s+(?:cadence|frequency|schedule)|update\s+frequency|last\s+(?:refreshed|updated)|data\s+quality\s+score|certification\s+status|certified\s+status|lineage\s+of|upstream\s+of|downstream\s+of)\b",
    r"\b(?:how\s+often|when)\s+(?:is|was|does)\b.*\b(?:refresh|refreshed|update|updated)\b",
    r"\bis\b.*\b(?:approved\s+for\s+use|production[- ]ready)\b",
    r"\bcan\s+(?:i|we)\s+trust\b.*\b(?:product|table)\b",
    r"\bhow\s+many\s+(?:columns?|fields?|attributes?|tables?|(?:data\s+)?products?)\b",
    r"\bhow\s+(?:do\s+i|can\s+i|to|are)\b.*\b(?:join|joined|link|linked)\b",
))
FIGURE_INTENT = re.compile(
    r"\b(?:highest|lowest|most|least|largest|smallest|top|bottom|how\s+much)\b"
    r"|\bhow\s+many\b(?!\s+(?:columns?|fields?|attributes?|tables?|(?:data\s+)?products?)\b)"
    r"|\b(?:total|average|avg|sum|count)\b(?!\s+of\s+(?:columns?|fields?|attributes?|tables?|(?:data\s+)?products?)\b)(?!\s+[a-z]+\s+(?:product|table)\b)",
    re.I,
)


def catalogue_question(question: str, terms: list[str] | None = None) -> bool:
    """Recognise metadata intents. Certification yes/no questions remain metadata even for named
    measures; certification qualifiers and numeric comparisons keep the figure path."""
    q = normalise_question(question or "")
    if re.search(r"^\s*(?:is|are|was|were)\b.*\b(?:certified|trusted)\b", q, re.I):
        return True
    if not q or read_period(q)[0] is not None or FIGURE_FOLLOWUP.search(q) or FIGURE_INTENT.search(q):
        return False
    if any(p.search(q) for p in CATALOGUE_PATTERNS):
        return True
    if CATALOGUE_DISCOVERY.search(q):
        return not (terms and re.search(r"\b(?:above|below|over|under|greater than|less than|more than|fewer than)\s+\d", q, re.I))
    return not terms and bool(re.search(r"\bis\b.*\b(?:certified|trusted)\b", q, re.I))


def period_evidence(question):
    """Accepted point syntax retains offsets into the original turn, including Unicode forms."""
    chars, offsets = [], []
    for i, char in enumerate(question):
        if char in "\u200b\ufeff":
            continue
        for normalized in unicodedata.normalize('NFKC', char):
            normalized = str(unicodedata.decimal(normalized)) if normalized.isdecimal() else normalized
            if normalized.isspace():
                if chars and chars[-1] != ' ':
                    chars.append(' ')
                    offsets.append(i)
            else:
                chars.append(normalized)
                offsets.append(i)
    text = ''.join(chars).rstrip()
    found = []
    _read_period(text, False, (), evidence=found)
    return tuple((offsets[start], offsets[end - 1] + 1, period) for start, end, period in found)
