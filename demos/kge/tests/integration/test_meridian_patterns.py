"""The Meridian pattern self-check beside the golden-20 gate (issue #474): does each golden item's pattern hold for other
values, or only for the one its question names?

For each golden item S06 to S25 (read from sage_v1.json at run time, its question and reference SQL as the set holds
them now), variants are made by substituting, in the question and its reference SQL alike, one kind of value at a time:

  period      the month, quarter, quarter range, month range or year-to-date end the question names, moved to other
              months of the sample's released range (the months the pack's released filter passes, the last unbroken
              run of them the fact holds); every date of the SQL moves with it (a comparison month keeps its
              distance from the period's end, so year over year stays year over year)
  segment     a client segment named in the question, swapped for another of the same level (CLIENT_CD_ROLLUP_1)
  region      the regions named in the question, swapped for others (RGN_DIM_1)
  branch      a branch named in the question, swapped for another (BRCH_DIM_1)
  threshold   a dollar threshold, as 0.4, 2 and 4 times its value
  top N       the N of a top-N or bottom-N, as 3, 5 and 20

A value is substituted only where the question names it: a filter the question does not name is part of the measure's
definition, and stays. Only values the sample holds are used, and a variant whose reference SQL gives no figure on the
sample is passed over. The set is fixed: the candidates of each kind in a fixed order, spread evenly, at most three of
a kind; the report lists every variant asked.

Each variant is asked on the rules path, as the golden-20 gate asks it, and its figures are compared with its
substituted reference SQL's rows on the sample by the same scorer (kge/eval/run_golden.py). A pattern holds when every
variant of its item passes. An item whose main question fails the golden-20 gate today is a strict expected failure
here (MAIN_QUESTION_FAILS, with its reason): one whose variants all pass fails the check until it leaves the list. An
item whose main question passes must hold every variant, but for a variant of a known generic defect (KNOWN_DEFECTS,
each a rule on the variant, not an item), which must fail, strictly, until the defect is fixed. The module prints "N of 20 patterns hold across their
Meridian variants" and, per item, the variants passing out of the total.
"""
from __future__ import annotations

import calendar
import contextlib
import itertools
import re
from dataclasses import dataclass
from functools import cache
from types import SimpleNamespace

import pytest
import yaml

from kge.estate import golden20
from kge.estate.golden import warehouses
from kge.estate.golden_wealth import read
from kge.eval.run_golden import score
from kge.pipeline.settings import ROOT
from tests.integration.test_golden20 import ITEMS, WHO, came_back, layer
from tests.scratch import BUILD

PER_KIND = 3
MAIN_QUESTION_FAILS: dict[str, str] = {}
PACK = ROOT / 'clients/meridian/pack.yaml'
RESULTS: dict[str, list[dict]] = {}

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November',
          'December']
MONTH = '(' + '|'.join(MONTHS) + ')'
QUARTER_RANGE = re.compile(r'\bQ([1-4]) (through|to) Q([1-4]) (\d{4})\b')
MONTH_RANGE = re.compile(rf'\b{MONTH} (through|to) {MONTH} (\d{{4}})\b')
TO_DATE = re.compile(rf'\b(?:YTD|year to date)\b.*?\bthrough ({MONTH} (\d{{4}}))\b', re.IGNORECASE)
ONE_QUARTER = re.compile(r'\bQ([1-4]) (\d{4})\b')
ONE_MONTH = re.compile(rf'\b{MONTH} (\d{{4}})\b')
DATE = re.compile(r"CAST\('(\d{4})-(\d{2})-(\d{2})' AS DATE\)")

NAMED = {
    r'SEGMT_LVL_ID(\d)': ('segment', 'SELECT DISTINCT SEGMT_LVL_ID{0}, SEGMT_LVL_NM{0} FROM CLIENT_CD_ROLLUP_1'),
    r'RGN_CD': ('region', 'SELECT DISTINCT RGN_CD, RGN_NM FROM RGN_DIM_1'),
    r'BRCH_CD': ('branch', 'SELECT DISTINCT BRCH_CD, BRCH_CD FROM BRCH_DIM_1'),
}
FILTER = re.compile(r"\b\w+\.(SEGMT_LVL_ID\d|RGN_CD|BRCH_CD)\s*(?:IN\s*\(([^)]*)\)|=\s*('[^']*'))")
MONEY = re.compile(r'\$(\d+(?:\.\d+)?)([KM]?)\b')
TOP_N = re.compile(r'\b(top|bottom) (\d+)\b', re.IGNORECASE)
TOP_N_TIMES = (3, 5, 20)
THRESHOLD_TIMES = (0.4, 2, 4)


@dataclass(frozen=True)
class Variant:
    kind: str
    question: str
    sql: str


def month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def month_end(index: int) -> str:
    year, month = divmod(index, 12)
    return f'{year:04d}-{month + 1:02d}-{calendar.monthrange(year, month + 1)[1]:02d}'


def spread(candidates: list, k: int = PER_KIND) -> list:
    """At most k of an ordered list, evenly spaced from its first to its last."""
    if len(candidates) <= k:
        return list(candidates)
    return [candidates[round(i * (len(candidates) - 1) / (k - 1))] for i in range(k)]


class Sample:
    """Meridian's sample, read for the values a variant may take and for a reference SQL's rows."""

    def __init__(self):
        self.session = warehouses(BUILD, 'meridian').open(golden20.UNIT)
        rule = next(f for f in yaml.safe_load(PACK.read_text())['filters'] if f['id'] == 'released')
        table = rule['table'].split('.')[-1]
        released_months = {month_index(d.year, d.month) for (d,) in
                           self.session.execute(f"SELECT CLDR_EOM_DT FROM {table} WHERE {rule['predicate']}").fetchall()}
        months = sorted({month_index(d.year, d.month) for (d,) in
                         self.session.execute('SELECT DISTINCT DW_TRAN_MNTH_END_DT FROM F_NNA_FIN').fetchall()}
                        & released_months)
        released = [months[-1]]
        for m in reversed(months[:-1]):
            if m != released[-1] - 1:
                break
            released.append(m)
        self.released = sorted(released)

    def names(self, column: str) -> list[tuple[str, str]]:
        for pattern, (_, sql) in NAMED.items():
            m = re.fullmatch(pattern, column)
            if m:
                return sorted({(str(c), str(n)) for c, n in self.session.execute(sql.format(*m.groups())).fetchall()
                               if c is not None and n is not None})
        return []

    def table(self, item_id: str, sql: str, measures: list[str], zero_is_figure: bool = False) -> dict | None:
        """A reference SQL's rows as the golden-20 baseline holds them, or None where it gives no figure: no rows, no
        measure value, or (unless zero_is_figure) only zeros."""
        got = read(self.session, SimpleNamespace(id=item_id, reads='table'), sql)['table']
        rows = [[golden20.value(v) for v in row] for row in got['rows']]
        at = [got['columns'].index(m) for m in measures if m in got['columns']]
        empty = (None,) if zero_is_figure else (None, '0.00', 0)
        if not rows or not any(row[i] not in empty for row in rows for i in at):
            return None
        return golden20.table({'columns': got['columns'], 'rows': rows})

    def close(self):
        self.session.close()


def period(question: str) -> tuple[str, int, int, re.Match] | None:
    """The question's period: its form, first and last month, and where it is named."""
    m = QUARTER_RANGE.search(question)
    if m:
        year = int(m.group(4))
        return 'quarters', month_index(year, 3 * int(m.group(1)) - 2), month_index(year, 3 * int(m.group(3))), m
    m = MONTH_RANGE.search(question)
    if m:
        year = int(m.group(4))
        return 'months', month_index(year, MONTHS.index(m.group(1)) + 1), month_index(year, MONTHS.index(m.group(3)) + 1), m
    m = TO_DATE.search(question)
    if m:
        year = int(m.group(3))
        return 'to date', month_index(year, 1), month_index(year, MONTHS.index(m.group(2).capitalize()) + 1), m
    m = ONE_QUARTER.search(question)
    if m:
        year = int(m.group(2))
        return 'quarter', month_index(year, 3 * int(m.group(1)) - 2), month_index(year, 3 * int(m.group(1))), m
    m = ONE_MONTH.search(question)
    if m:
        index = month_index(int(m.group(2)), MONTHS.index(m.group(1)) + 1)
        return 'month', index, index, m
    return None


def period_candidates(form: str, released: list[int]) -> list[tuple[int, int]]:
    first, last = released[0], released[-1]
    inside = range(first, last + 1)
    if form == 'month':
        return [(m, m) for m in inside]
    if form == 'quarter':
        return [(m, m + 2) for m in inside if m % 3 == 0 and m + 2 <= last]
    if form == 'quarters':
        return [(a, b) for a in inside for b in inside if a % 3 == 0 and b % 3 == 2 and b - a >= 5 and a // 12 == b // 12]
    if form == 'months':
        return [(a, b) for a in inside for b in inside if a < b and a // 12 == b // 12 and a % 12 == 0]
    return [(a - a % 12, a) for a in inside if a - a % 12 >= first and a % 12 > 0]


def said(form: str, match: re.Match, a: int, b: int) -> str:
    """A period said the way the question said its own."""
    year = b // 12
    if form == 'month':
        return f'{MONTHS[b % 12]} {year}'
    if form == 'quarter':
        return f'Q{b % 12 // 3 + 1} {year}'
    if form == 'quarters':
        return f'Q{a % 12 // 3 + 1} {match.group(2)} Q{b % 12 // 3 + 1} {year}'
    if form == 'months':
        return f'{MONTHS[a % 12]} {match.group(2)} {MONTHS[b % 12]} {year}'
    return f'{MONTHS[b % 12]} {year}'


def period_variants(question: str, sql: str, released: list[int]) -> list[Variant]:
    found = period(question)
    if not found:
        return []
    form, start, end, match = found
    if not DATE.search(sql):
        return []
    span = match.span(1) if form == 'to date' else match.span()
    out = []
    for a, b in period_candidates(form, released):
        if (a, b) == (start, end):
            continue

        def moved(d: re.Match) -> str:
            index = month_index(int(d.group(1)), int(d.group(2)))
            new = a if index == start else b if index == end else index + (b - end)
            return f"CAST('{month_end(new)}' AS DATE)"
        dates = [month_index(int(d.group(1)), int(d.group(2))) for d in DATE.finditer(sql)]
        if any(d not in (start, end) and not released[0] <= d + (b - end) <= released[-1] for d in dates):
            continue
        out.append(Variant('period', question[:span[0]] + said(form, match, a, b) + question[span[1]:], DATE.sub(moved, sql)))
    return out


def named_variants(question: str, sql: str, sample: Sample) -> list[Variant]:
    """Each filter of the SQL on a segment, region or branch whose every value the question names: the values swapped
    for another set of the sample's, as many as the question names, in the question and in every filter of the SQL
    on that column with those values (each part of a comparison alike)."""
    out = []
    seen = set()
    for f in FILTER.finditer(sql):
        column = f.group(1)
        codes = tuple(re.findall(r"'([^']*)'", f.group(2) if f.group(2) is not None else f.group(3)))
        if not codes or (column, codes) in seen:
            continue
        seen.add((column, codes))
        pairs = sample.names(column)
        name = dict(pairs)
        spans = [re.search(rf'\b{re.escape(name.get(code, code))}\b', question, re.IGNORECASE) for code in codes]
        if not all(spans):
            continue
        kind = next(k for p, (k, _) in NAMED.items() if re.fullmatch(p, column))
        for chosen in itertools.combinations([c for c, _ in pairs], len(codes)):
            if set(chosen) == set(codes):
                continue
            q = question
            for hit, code in sorted(zip(spans, chosen), key=lambda x: -x[0].start()):
                word = name[code]
                q = q[:hit.start()] + word + ("'" + q[hit.end() + 2:] if word.endswith('s') and q[hit.end():hit.end() + 2] == "'s" else q[hit.end():])

            def swapped(g: re.Match) -> str:
                listed = g.group(2) if g.group(2) is not None else g.group(3)
                if g.group(1) != column or tuple(re.findall(r"'([^']*)'", listed)) != codes:
                    return g.group(0)
                return g.group(0).replace(listed, ', '.join(f"'{c}'" for c in chosen))
            out.append(Variant(kind, q, FILTER.sub(swapped, sql)))
    return out


def money(value: float) -> str:
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f'${int(value // 1_000_000)}M'
    return f'${value / 1000:g}K'


def threshold_variants(question: str, sql: str) -> list[Variant]:
    m = MONEY.search(question)
    if not m:
        return []
    value = float(m.group(1)) * {'': 1, 'K': 1_000, 'M': 1_000_000}[m.group(2)]
    bound = re.compile(rf'(>=?|<=?)\s*{int(value)}\b')
    if not bound.search(sql):
        return []
    out = []
    for times in THRESHOLD_TIMES:
        new = value * times
        out.append(Variant('threshold', question[:m.start()] + money(new) + question[m.end():],
                           bound.sub(lambda b: f'{b.group(1)} {int(new)}', sql)))
    return out


def top_n_variants(question: str, sql: str) -> list[Variant]:
    hits = list(TOP_N.finditer(question))
    if not hits or len({h.group(2) for h in hits}) != 1:
        return []
    n = hits[0].group(2)
    bound = re.compile(rf'<=\s*{n}\b')
    if not bound.search(sql):
        return []
    return [Variant('top N', TOP_N.sub(lambda h: f'{h.group(1)} {k}', question), bound.sub(f'<= {k}', sql))
            for k in TOP_N_TIMES if str(k) != n]


KNOWN_DEFECTS: list = []


def known_defect(question: str, table: dict) -> str:
    """The reason a variant is expected to fail for a known generic defect, or none."""
    return next((reason for rule, reason in KNOWN_DEFECTS if rule(question, table)), '')


def marked(ids):
    return [pytest.param(i, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason=MAIN_QUESTION_FAILS[i]))
            if i in MAIN_QUESTION_FAILS else i for i in ids]


@cache
def variants(item_id: str) -> tuple[list[Variant], list[dict]]:
    """An item's variants and, for each, its substituted reference SQL's table on the sample."""
    item = ITEMS[item_id]
    question, sql, measures = item['question'], item['reference_sql'], item['expect']['measures']
    sample = Sample()
    try:
        by_kind = [period_variants(question, sql, sample.released), named_variants(question, sql, sample),
                   threshold_variants(question, sql), top_n_variants(question, sql)]
        chosen, tables = [], []
        for group in by_kind:
            for kind in sorted({v.kind for v in group}):
                kept = [(v, t) for v in group if v.kind == kind for t in [sample.table(item_id, v.sql, measures)] if t]
                for v, t in spread(kept):
                    chosen.append(v)
                    tables.append(t)
        return chosen, tables
    finally:
        sample.close()


@pytest.fixture(scope='module', autouse=True)
def report(request):
    """The self-check's score and its failures, printed when the module ends."""
    yield
    terminal = request.config.pluginmanager.get_plugin('terminalreporter')
    capture = request.config.pluginmanager.get_plugin('capturemanager')
    with capture.global_and_fixture_disabled() if capture else contextlib.nullcontext():
        summary(terminal.write_line if terminal else print)


def summary(write) -> None:
    if not RESULTS:
        return
    held = [i for i, r in RESULTS.items() if r and all(x['ok'] for x in r)]
    write('')
    write(f'patterns: {len(held)} of {len(ITEMS)} patterns hold across their Meridian variants')
    for item_id in sorted(RESULTS):
        runs = RESULTS[item_id]
        state = 'main question fails the golden gate' if item_id in MAIN_QUESTION_FAILS else 'main question passes'
        write(f"  {item_id}: {sum(x['ok'] for x in runs)} of {len(runs)} variants pass ({state})")
        for x in runs:
            known = f" (known defect: {x['expected']})" if x['expected'] else ''
            write(f"    [{'ok' if x['ok'] else 'FAIL'}] {x['kind']}: {x['question']!r}{known}")
            if not x['ok']:
                write(f"      came back {x['what']}\n      layer {x['layer']}; SQL {x['difference']}")


@pytest.mark.parametrize('item_id', marked(sorted(ITEMS)))
def test_each_golden_pattern_holds_across_its_meridian_variants(eng, item_id):
    found, tables = variants(item_id)
    item = ITEMS[item_id]
    runs = []
    for v, table in zip(found, tables):
        asked = {**item, 'question': v.question, 'reference_sql': v.sql, 'expect': {**item['expect'], 'table': table}}
        ans = eng.answer(v.question, WHO)
        ok, _ = score(asked, ans)
        runs.append({'kind': v.kind, 'question': v.question, 'ok': ok, 'what': came_back(ans),
                     'expected': '' if item_id in MAIN_QUESTION_FAILS else known_defect(v.question, table),
                     'layer': '' if ok else layer(ans),
                     'difference': '' if ok else golden20.difference(ans.get('sql'), v.sql)})
    RESULTS[item_id] = runs
    assert runs, f'{item_id}: no variant could be made'
    assert all(x['ok'] for x in runs if not x['expected']), '\n'.join(
        f"{x['question']!r}: {x['what']} ({x['layer']}; {x['difference']})" for x in runs if not x['ok'] and not x['expected'])
    assert not any(x['ok'] for x in runs if x['expected']), 'a known defect is fixed: take it out of KNOWN_DEFECTS: ' + \
        ', '.join(repr(x['question']) for x in runs if x['ok'] and x['expected'])


@pytest.mark.parametrize('item_id', sorted(ITEMS))
def test_each_item_has_at_least_three_variants_each_a_real_substitution(item_id):
    found, _ = variants(item_id)
    item = ITEMS[item_id]
    assert len(found) >= 3, [v.question for v in found]
    for v in found:
        assert v.question != item['question'] and v.sql != item['reference_sql'], v
    assert len({v.question for v in found}) == len(found)


def test_every_item_the_main_question_list_names_is_a_golden_item():
    assert set(MAIN_QUESTION_FAILS) <= set(ITEMS)


def test_the_unsubstituted_reference_reads_as_the_committed_baseline():
    """The reading a variant is compared with is the golden-20 baseline's: each item's own SQL gives its baseline."""
    sample = Sample()
    try:
        for item_id, item in ITEMS.items():
            assert sample.table(item_id, item['reference_sql'], item['expect']['measures']) == item['expect']['table'], item_id
    finally:
        sample.close()


@pytest.mark.parametrize('question', ['Show me certified Core Affluent NNA by sub-segment and quarter for Q1 through Q3 2024.',
                                      'Certified Core Affluent NNA by sub-segment by quarter, Q1 to Q3 2024'])
def test_a_segment_name_holding_its_parent_segment_s_name_reads_both_and_computes(eng, question):
    """Core Affluent sits under Affluent, and its name holds its parent's: the two filters agree, so the figure is the
    child segment's, as S07 reads it under its other name."""
    item = ITEMS['S07']
    ans = eng.answer(question, WHO)
    assert score({**item, 'question': question}, ans)[0], came_back(ans)


@pytest.mark.parametrize('position', ['Retail Investor {} NNA for September 2024', 'Retail Investor NNA, {}, for September 2024',
                                      'Retail Investor NNAxFDIC {} for September 2024'])
def test_the_model_prompt_reads_each_reporting_basis_wording_as_the_rules_do(eng, monkeypatch, position):
    """Every declared wording of a reporting basis carries, in the model planner's obligations, the mode the rules
    planner's plan gives the measure: the two paths read a question's basis from one ledger."""
    from kge.services.ask.engine import planmodel, planrules
    read, seen = planrules.read, {}

    def recorded(engine, req, vocab, cfg, turn='turn', *args, **kwargs):
        prompt = planmodel.payload(req.question, vocab, (), turn)
        plan = read(engine, req, vocab, cfg, turn, *args, **kwargs)
        seen[req.question] = ({o['operation'] for o in prompt['obligations'] if o['operation'] in ('as_reported', 'current')},
                              {m.mode for m in plan.plan.measures} if getattr(plan, 'plan', None) else None)
        return plan
    monkeypatch.setattr(planrules, 'read', recorded)
    wordings = sorted(eng.TIME_MODES)
    assert wordings
    for wording in wordings:
        question = position.format(wording)
        eng.answer(question, WHO)
        operations, modes = seen[question]
        assert modes, question
        assert operations <= {'as_reported', 'current'} and modes == (operations or {'current'}), (question, operations, modes)


def test_current_versus_certified_reads_two_different_sources(eng):
    ans = eng.answer('Compare current and certified Retail Investor NNA for September 2024', WHO)
    assert ans.get('behaviour') in ('figure', 'combined'), came_back(ans)
    sources = set(re.findall(r'\bFROM\s+"?(\w+)"?', ans['sql']))
    assert {'F_NNA_FIN', 'TRAN_FACT_1'} <= sources, sources
