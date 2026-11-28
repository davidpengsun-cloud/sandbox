"""The #474 capstone: the golden-20 patterns, held out, on a second test client.

The 20 golden questions are patterns. This module asks each of them on the capstone client (tests/fixtures/capstone/
build.py: Meridian's nine views with another seed, more regions, branches and leaf segments, and a pack that differs
from Meridian's only in its values, the Heartland region among them), with value variants and every phrasing crossed
with them:

  base        the item's question and each of its phrasings as they are, the values the capstone pack gives their
              phrases (the Heartland region is another group of regions there)
  named       a pack value phrase the question names (a branch, a region or a group of regions, a segment) swapped for
              another phrase of the pack on the same column and operator, its values swapped in the reference SQL
  period      the period moved to other released months (as the Meridian self-check moves it)
  threshold   a dollar threshold as 0.4, 2 and 4 times its value
  top N       the N of a top-N or bottom-N as 3, 5 and 20
  year        every period the question names a year earlier (2024 to 2023, 2023 to 2022): the year the capstone
              client's second year of history holds
  new level 4, new business line, new leaf segment
              a segment phrase swapped for the phrase of each segment at that level the capstone client adds (one its
              pack declares and Meridian's does not), every one of them kept

Each value set is made on the item's question, at most three of a kind spread evenly, and then crossed with every
phrasing that names the same value the same way (wording x value). Expected figures are the item's golden reference SQL
with the same values swapped in, run on the capstone warehouse; a value set whose SQL gives no figure is passed over.
Every variant is asked on the rules path alone (MODEL_PROVIDER=none), as the golden-20 gate asks it, and scored by the
same scorer (kge/eval/run_golden.py). No model path is asked. An item passes when every variant does.

Run only on a capstone build: KGE_CAPSTONE=1 KGE_TEST_BUILD=<folder>/build (the folder build.py wrote). CAPSTONE_REPORT
names a JSON file the per-variant results are written to.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest
import yaml

from kge.estate import golden20
from kge.eval.run_golden import score
from kge.pipeline.settings import ROOT
from tests.integration.test_golden20 import ITEMS, WHO, came_back, layer
from tests.integration.test_meridian_patterns import (DATE, ONE_MONTH, ONE_QUARTER, Sample, known_defect, month_end,
                                                      month_index, period_variants, spread, threshold_variants,
                                                      top_n_variants)
from tests.scratch import BUILD

pytestmark = pytest.mark.skipif(not os.environ.get('KGE_CAPSTONE'), reason='the capstone runs on a capstone build only')

PER_KIND = 3
NEW_KINDS = {'SEGMT_LVL_ID4': 'new level 4', 'SEGMT_LVL_ID5': 'new business line', 'SEGMT_LVL_ID6': 'new leaf segment'}
YEAR = re.compile(r'\b(20\d\d)\b')
FILTER = re.compile(r"\b\w+\.(SEGMT_LVL_ID\d|RGN_CD|BRCH_CD)\s*(?:IN\s*\(([^)]*)\)|=\s*('[^']*'))")
RESULTS: dict[str, list[dict]] = {}


@dataclass(frozen=True)
class Variant:
    kind: str
    question: str
    sql: str


@cache
def predicates() -> list[dict]:
    """The capstone pack's value phrases on a segment, region or branch column: (phrases, column, op, codes)."""
    pack = yaml.safe_load((BUILD.parent / 'clients/meridian/pack.yaml').read_text())
    out = []
    for p in pack['terminology']['predicates']:
        column = p.get('column', '').split('.')[-1]
        if not re.fullmatch(r'SEGMT_LVL_ID\d|RGN_CD|BRCH_CD', column) or p.get('op') not in ('eq', 'in'):
            continue
        codes = tuple(p['values']) if p['op'] == 'in' else (str(p['value']),)
        out.append({'phrases': p['phrases'], 'column': column, 'op': p['op'], 'codes': codes})
    return out


@cache
def new_members() -> dict[str, set[str]]:
    """The segment codes the capstone pack lists and Meridian's does not, by column."""
    def listed(pack: Path) -> dict[str, set[str]]:
        out = {}
        for v in yaml.safe_load(pack.read_text())['terminology']['value_vocabularies']:
            column = v['column'].split('.')[-1]
            if column in NEW_KINDS and 'values' in v:
                out.setdefault(column, set()).update(map(str, v['values']))
        return out
    mine, meridian = listed(BUILD.parent / 'clients/meridian/pack.yaml'), listed(ROOT / 'clients/meridian/pack.yaml')
    return {column: codes - meridian.get(column, set()) for column, codes in mine.items()}


def natural(phrases: list[str], like: str) -> str | None:
    """The phrase of a predicate that reads as `like` does: ending in the same word where that is region or branch,
    never a code-style phrase (segment level, client segment, region CODE)."""
    tail = like.split()[-1].lower()
    fits = [p for p in phrases if not re.search(r'level|client segment|^region |^branch ', p, re.IGNORECASE)
            and not re.fullmatch(r'[A-Z]+ region', p)]
    if tail in ('region', 'branch'):
        fits = [p for p in fits if p.split()[-1].lower() == tail]
    else:
        fits = [p for p in fits if p.split()[-1].lower() not in ('region', 'branch')]
    return min(fits, key=len) if fits else None


def named(question: str):
    """The pack phrases the question names on a filter column: (match, predicate), the longest phrase at a place."""
    hits = []
    for p in predicates():
        for phrase in p['phrases']:
            m = re.search(rf'(?<!\w){re.escape(phrase)}(?!\w)', question, re.IGNORECASE)
            if m:
                hits.append((m, p))
    hits.sort(key=lambda h: (h[0].start(), -(h[0].end() - h[0].start())))
    out, last = [], -1
    for m, p in hits:
        if m.start() >= last:
            out.append((m, p))
            last = m.end()
    return out


def with_codes(sql: str, column: str, old: tuple, new: tuple) -> str:
    def swapped(g: re.Match) -> str:
        listed = g.group(2) if g.group(2) is not None else g.group(3)
        if g.group(1) != column or set(re.findall(r"'([^']*)'", listed)) != set(old):
            return g.group(0)
        if g.group(2) is not None:
            return g.group(0).replace(listed, ', '.join(f"'{c}'" for c in new))
        if len(new) == 1:
            return g.group(0).replace(listed, f"'{new[0]}'")
        return g.group(0)[:g.group(0).index('=')] + 'IN (' + ', '.join(f"'{c}'" for c in new) + ')'
    return FILTER.sub(swapped, sql)


def capstone_sql(question: str, sql: str) -> str:
    """The reference SQL with each named phrase's codes the capstone pack's (the Heartland region's, there)."""
    for m, p in named(question):
        for f in FILTER.finditer(sql):
            listed = f.group(2) if f.group(2) is not None else f.group(3)
            codes = tuple(re.findall(r"'([^']*)'", listed))
            if f.group(1) == p['column'] and set(codes) != set(p['codes']) and len(codes) == len(p['codes']) > 1:
                sql = with_codes(sql, p['column'], codes, p['codes'])
    return sql


def named_variants(question: str, sql: str) -> list[Variant]:
    out = []
    for m, p in named(question):
        if not any(f.group(1) == p['column'] and set(re.findall(r"'([^']*)'", f.group(2) or f.group(3))) == set(p['codes'])
                   for f in FILTER.finditer(sql)):
            continue
        for other in predicates():
            if other['column'] != p['column'] or other['op'] != p['op'] or set(other['codes']) == set(p['codes']):
                continue
            phrase = natural(other['phrases'], m.group(0))
            if not phrase:
                continue
            q = question[:m.start()] + phrase + question[m.end():]
            new = set(other['codes']) & new_members().get(p['column'], set())
            kind = NEW_KINDS[p['column']] if new else 'named'
            out.append(Variant(kind, q, with_codes(sql, p['column'], p['codes'], other['codes'])))
    seen, unique = set(), []
    for v in out:
        if v.sql not in seen:
            seen.add(v.sql)
            unique.append(v)
    return unique


def year_variants(question: str, sql: str, released: list[int]) -> list[Variant]:
    """Every period the question names a year earlier, in its words and in every date of its SQL, where each moved
    month is released."""
    dates = [month_index(int(d.group(1)), int(d.group(2))) - 12 for d in DATE.finditer(sql)]
    if not YEAR.search(question) or not dates or not all(released[0] <= d <= released[-1] for d in dates):
        return []
    moved = DATE.sub(lambda d: f"CAST('{month_end(month_index(int(d.group(1)), int(d.group(2))) - 12)}' AS DATE)", sql)
    return [Variant('year', YEAR.sub(lambda y: str(int(y.group(1)) - 1), question), moved)]


def periods_named(question: str) -> int:
    return len(ONE_MONTH.findall(question)) + len(ONE_QUARTER.findall(question))


def all_variants(question: str, sql: str, sample: Sample) -> list[Variant]:
    """A phrasing that names two periods (September 2024 with September 2023) takes no period variant: only the first
    would move in its words, while its reference SQL moves both."""
    periods = period_variants(question, sql, sample.released) if periods_named(question) <= 1 else []
    return [*periods, *year_variants(question, sql, sample.released), *named_variants(question, sql),
            *threshold_variants(question, sql), *top_n_variants(question, sql)]


def expected(sample: Sample, item_id: str, sql: str, measures: list[str]) -> dict | None:
    """A reference SQL's table, or None where any of its figure columns holds no figure in any row: a reference with
    no figure is passed over, and a zero is a figure."""
    table = sample.table(item_id, sql, measures, zero_is_figure=True)
    if table is None:
        return None
    at = [table['columns'].index(m) for m in measures if m in table['columns']]
    return None if any(all(row[i] is None for row in table['rows']) for i in at) else table


@cache
def cases(item_id: str) -> list[tuple[Variant, dict | None]]:
    """The item's variants, each with its expected table (None where its reference SQL gives no rows, or a figure
    column with no figure in any row, on the capstone sample, a variant the check passes over; a zero is a figure,
    asked and compared): every phrasing as it is, then each value set chosen on the question, crossed with every
    phrasing that names it."""
    item = ITEMS[item_id]
    measures = item['expect']['measures']
    sample = Sample()
    try:
        base_sql = capstone_sql(item['question'], item['reference_sql'])
        out = []
        for q in item['phrasings']:
            sql = capstone_sql(q, item['reference_sql'])
            out.append((Variant('base', q, sql), expected(sample, item_id, sql, measures)))
        main = all_variants(item['question'], base_sql, sample)
        chosen = []
        for kind in sorted({v.kind for v in main}):
            whole = kind == 'year' or kind in NEW_KINDS.values()
            kept = [(v, t) for v in main if v.kind == kind
                    for t in [expected(sample, item_id, v.sql, measures) if whole else sample.table(item_id, v.sql, measures)] if t]
            chosen += kept if whole else spread(kept, PER_KIND)
        tables = {v.sql: t for v, t in chosen}
        for q in item['phrasings']:
            for v in all_variants(q, capstone_sql(q, item['reference_sql']), sample):
                if v.sql in tables:
                    out.append((v, tables[v.sql]))
        return out
    finally:
        sample.close()


@pytest.fixture(scope='module', autouse=True)
def report(request):
    yield
    terminal = request.config.pluginmanager.get_plugin('terminalreporter')
    capture = request.config.pluginmanager.get_plugin('capturemanager')
    with capture.global_and_fixture_disabled() if capture else contextlib.nullcontext():
        summary(terminal.write_line if terminal else print)
    if os.environ.get('CAPSTONE_REPORT') and RESULTS:
        Path(os.environ['CAPSTONE_REPORT']).write_text(json.dumps(RESULTS, indent=1, default=str))


def summary(write) -> None:
    if not RESULTS:
        return
    held = [i for i, r in RESULTS.items() if r and all(x['ok'] for x in r)]
    total = [x for r in RESULTS.values() for x in r if not x.get('skipped')]
    passed_over = sum(bool(x.get('skipped')) for r in RESULTS.values() for x in r)
    write('')
    write(f'capstone: {len(held)} of {len(RESULTS)} patterns hold on the capstone client '
          f'({sum(x["ok"] for x in total)} of {len(total)} variants, rules path; {passed_over} passed over, no reference figure)')
    for kind in sorted({x['kind'] for x in total}):
        runs = [x for x in total if x['kind'] == kind]
        write(f"  {kind}: {sum(x['ok'] for x in runs)} of {len(runs)} variants pass")
    for item_id in sorted(RESULTS):
        runs = [x for x in RESULTS[item_id] if not x.get('skipped')]
        write(f"  {item_id}: {sum(x['ok'] for x in runs)} of {len(runs)} variants pass")
        for x in runs:
            if not x['ok']:
                known = f" (known defect: {x['known']})" if x.get('known') else ''
                write(f"    [FAIL] {x['kind']}: {x['question']!r}{known}\n      came back {x['what']}\n"
                      f"      layer {x['layer']}; SQL {x['difference']}")


@pytest.mark.parametrize('item_id', sorted(ITEMS))
def test_each_golden_pattern_holds_on_the_capstone_client(eng, item_id):
    item = ITEMS[item_id]
    runs = []
    for v, table in cases(item_id):
        if table is None:
            runs.append({'kind': v.kind, 'question': v.question, 'ok': True, 'skipped': True,
                         'what': 'reference SQL gives no figure: passed over', 'sql': v.sql})
            continue
        asked = {**item, 'question': v.question, 'reference_sql': v.sql, 'expect': {**item['expect'], 'table': table}}
        ans = eng.answer(v.question, WHO)
        ok, _ = score(asked, ans)
        runs.append({'kind': v.kind, 'question': v.question, 'ok': ok, 'what': came_back(ans),
                     'known': known_defect(v.question, table),
                     'layer': '' if ok else layer(ans),
                     'difference': '' if ok else golden20.difference(ans.get('sql'), v.sql),
                     'sql': v.sql, 'answer_sql': None if ok else ans.get('sql'),
                     'expected': None if ok else table['rows'][:5], 'got': None if ok else (ans.get('rows') or ans.get('figure'))})
    RESULTS[item_id] = runs
    assert any(not x.get('skipped') for x in runs), f'{item_id}: no variant could be made'
    assert all(x['ok'] for x in runs), '\n'.join(f"{x['question']!r}: {x['what']} ({x['layer']}; {x['difference']})"
                                                  for x in runs if not x['ok'])
