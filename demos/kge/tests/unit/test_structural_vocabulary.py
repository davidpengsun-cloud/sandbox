"""Structural families share readers and certificates, independent of pack business words."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import GRAIN_WORDS, OPERATOR_FAMILIES, ground_plan, ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import NUMBER_WORDS, ORDINAL_WORDS, parse
from tests.plan_fixtures import model, name, semantics, metric_vocabulary as vocabulary
from tests.unit.test_in_filters import PREDICATES
from tests.unit.test_plan_compiler import connection


@pytest.fixture
def reader(monkeypatch):
    sem = semantics()
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    sem.d.GROUP_BY = {'by category': 'u.events.category'}
    sem.d.CALENDARS['fiscal'] = {'year_start_month': 4, 'quarter_label': 'FY{yy}-Q{n}'}
    sem.d.FISCAL_START = 4
    sem.d.VOCABULARY = [dict(kind='value', params=dict(column='u.events.category', values=list('ABC'))), *PREDICATES]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)

    def read(text, aliases=()):
        active = vocabulary(sem, aliases=[('Metric', 'ctx'), *aliases]) if aliases else vocab
        pieces = ledger(text, 'turn', active)
        assert not [p.text for p in pieces if p.kind == 'unknown'], text
        reply = planrules.read(engine, parse(text, ['Metric'], sem.d), active, SimpleNamespace(graph=True, glossary=True))
        assert reply.kind == 'propose', (text, reply)
        checked = check_plan(reply.plan, active, text)
        assert isinstance(checked, CheckedPlan), (text, checked)
        result = compile_plan(checked)
        assert check(result.answer.sql, result.answer.allow) == []
        return reply.plan, result
    return read


@pytest.mark.parametrize('operation,phrase', [(op, phrase) for op, phrases in OPERATOR_FAMILIES.items() for phrase in phrases])
def test_every_family_member_has_a_meaning(operation, phrase):
    pieces = ledger(phrase, 'turn', vocabulary())
    assert not [p.text for p in pieces if p.kind == 'unknown']
    assert operation in {p.operation for p in pieces}


@pytest.mark.parametrize('connector', [' and ', ', ', ', and ', ', gross ', ' and gross '])
def test_net_is_structural_after_a_list_of_declared_measure_parts(connector):
    sem = semantics()
    sem.d.VOCABULARY = PREDICATES
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    text = 'Metric gross A category' + connector + 'B category and net'
    pieces = ledger(text, 'turn', vocabulary(sem, aliases=[('Metric', 'ctx')]))
    assert not [p.text for p in pieces if p.kind == 'unknown']
    assert next(p for p in pieces if p.text == 'net').operation == 'aggregate'


@pytest.mark.parametrize('word,number', [*NUMBER_WORDS.items(), *ORDINAL_WORDS.items(), *((str(n), n) for n in range(1, 21))])
def test_quantities_and_ordinals_are_numeric_evidence(word, number):
    piece, = ledger(word, 'turn', vocabulary())
    assert (piece.kind, piece.value) == ('literal', number)


@pytest.mark.parametrize('word,grain', list(GRAIN_WORDS.items()))
def test_all_grain_singulars_and_plurals_are_certified(word, grain):
    piece, = ledger(word, 'turn', vocabulary())
    assert piece.operation == grain


@pytest.mark.parametrize('connector', ['over', 'across', 'during', 'in', 'within', 'for'])
def test_period_connectors_preserve_one_period(reader, connector):
    plan, result = reader(f'Metric {connector} April 2025')
    assert plan.time.kind == 'single' and plan.time.period.value == '2025-04'
    with connection() as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 10


@pytest.mark.parametrize('wording', ['over the year 2024', 'over calendar year 2024', 'within 2024', 'across 2024'])
def test_explicit_year_words_disambiguate_periods_from_numeric_thresholds(reader, wording):
    plan, _ = reader('Metric ' + wording)
    assert plan.time.period.kind == 'year' and plan.time.period.value == '2024'


def test_a_spelled_fiscal_year_keeps_its_declared_calendar(reader):
    plan, result = reader('Metric for fiscal year 2025')
    assert (plan.time.period.kind, plan.time.period.value, plan.time.period.calendar.id) == ('fiscal_year', '2025', 'fiscal')
    with connection([(dt.date(2024, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 4, 1), 20, 'B', 2, 'x')]) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 10


@pytest.mark.parametrize('edge', ['first', 'last'])
@pytest.mark.parametrize('grain,maximum', [('months', 12), ('quarters', 4), ('days', 20)])
def test_numbered_windows_are_read_once_for_all_grains(reader, edge, grain, maximum):
    rows = [(dt.date(2024, m, 1), m, 'A', 1, 'x') for m in range(1, 13)]
    for word, n in NUMBER_WORDS.items():
        if n > maximum:
            continue
        plan, result = reader(f'Metric for the {edge} {word} {grain} of 2024')
        assert plan.time.kind == ('single' if n == 1 else 'range')
        months = (range(1, n + 1) if edge == 'first' else range(13 - n, 13)) if grain == 'months' else (
                 (range(1, 3 * n + 1) if edge == 'first' else range(13 - 3 * n, 13)) if grain == 'quarters' else
                 ([1] if edge == 'first' else []))
        with connection(rows) as con:
            assert con.execute(result.answer.sql).fetchone()[0] == (sum(months) if months else None)


@pytest.mark.parametrize('text,first,last,expected', [
    ('nine months through September 2024', '2024-01', '2024-09', 45),
    ('three quarters ending Q4 2024', '2024-Q2', '2024-Q4', 72),
    ('three years through 2024', '2022', '2024', 78),
    ('ten days through 2024-01-10', '2024-01-01', '2024-01-10', 1),
])
def test_anchored_durations_preserve_both_bounds(reader, text, first, last, expected):
    plan, result = reader('Metric for ' + text)
    assert (plan.time.first.value, plan.time.last.value) == (first, last)
    rows = [(dt.date(2024, m, 1), m, 'A', 1, 'x') for m in range(1, 13)]
    with connection(rows) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == expected


@pytest.mark.parametrize('window', ['FIRST NINE MONTHS OF 2024', 'Last Three Quarters of 2024',
                                    'THREE MONTHS THROUGH JUNE 2025', 'Ten Days Through 2025-06-10',
                                    'first nine months of ２０２４', 'last three quarters of ２０２４',
                                    'first nine calendar months of 2024', 'last calendar quarter during 2024'])
def test_numbered_period_evidence_is_case_independent(reader, window):
    upper, _ = reader('Metric for ' + window)
    lower, _ = reader('Metric for ' + window.lower().translate({0xFF10 + digit: str(digit) for digit in range(10)}))
    assert upper.time == lower.time


@pytest.mark.parametrize('connector', ['of', 'in', 'during', 'within', 'over', 'across', 'for'])
def test_anchored_last_period_is_not_a_relative_comparison(reader, connector):
    plan, _ = reader(f'Metric for the last quarter {connector} 2024')
    assert plan.time.kind == 'single' and plan.time.period.value == '2024-Q4'
    assert not plan.comparisons


@pytest.mark.parametrize('ordinal,day', [('first', '2024-01-01'), ('third', '2024-01-03'), ('last', '2024-12-31')])
def test_ordinal_day_reads_the_whole_anchor(reader, ordinal, day):
    plan, _ = reader(f'Metric for the {ordinal} day of 2024')
    assert plan.time.period.kind == 'day' and plan.time.period.value == day


@pytest.mark.parametrize('window,kind,first,last', [
    ('first two fiscal quarters of FY26', 'fiscal_quarter', 'FY26-Q1', 'FY26-Q2'),
    ('last three quarters of FY26', 'fiscal_quarter', 'FY26-Q2', 'FY26-Q4'),
    ('second fiscal quarter during FY26', 'fiscal_quarter', 'FY26-Q2', 'FY26-Q2'),
    ('last fiscal quarter of FY26', 'fiscal_quarter', 'FY26-Q4', 'FY26-Q4'),
    ('three fiscal quarters through FY26-Q2', 'fiscal_quarter', 'FY25-Q4', 'FY26-Q2'),
    ('two fiscal years through FY26', 'fiscal_year', 'FY25', 'FY26'),
])
def test_numbered_fiscal_windows_use_labels_and_the_declared_calendar(reader, window, kind, first, last):
    plan, result = reader('Metric for ' + window)
    endpoints = (plan.time.first, plan.time.last) if plan.time.kind == 'range' else (plan.time.period,) * 2
    assert [(p.kind, p.value, p.calendar.id) for p in endpoints] == [(kind, first, 'fiscal'), (kind, last, 'fiscal')]
    with connection([(dt.date(2025, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 7, 1), 5, 'B', 2, 'x'),
                     (dt.date(2026, 1, 1), 7, 'C', 3, 'x')]) as con:
        expected = 7 if 'last fiscal quarter' in window else 12 if 'last three' in window else 5 if 'second' in window else 22 if kind == 'fiscal_year' else 15
        assert con.execute(result.answer.sql).fetchone()[0] == expected


@pytest.mark.parametrize('word,index', [(w, n) for w, n in ORDINAL_WORDS.items() if n <= 12] + [('last', 12)])
def test_ordinal_months_select_one_month(reader, word, index):
    plan, result = reader(f'Metric for the {word} month of 2024')
    assert plan.time.period.value == f'2024-{index:02d}'
    with connection([(dt.date(2024, m, 1), m, 'A', 1, 'x') for m in range(1, 13)]) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == index


@pytest.mark.parametrize('word,n', list(NUMBER_WORDS.items()))
@pytest.mark.parametrize('direction', ['top', 'bottom', 'highest', 'lowest', 'best', 'worst', 'largest', 'smallest'])
def test_ranking_quantities_share_the_numeric_reader(reader, word, n, direction):
    plan, result = reader(f'{direction} {word} categories by Metric for Q2 2025')
    descending = direction in ('top', 'highest', 'best', 'largest')
    assert plan.limit == n and plan.order[0].direction == ('desc' if descending else 'asc')
    rows = [(dt.date(2025, 4, 1), value, category, i, 'x') for i, (category, value) in enumerate([('A', 10), ('B', 20), ('C', 5)])]
    with connection(rows) as con:
        assert [r[:2] for r in con.execute(result.answer.sql).fetchall()] == sorted([('A', Decimal(10)), ('B', Decimal(20)), ('C', Decimal(5))],
                                                                  key=lambda r: r[1], reverse=descending)[:n]


@pytest.mark.parametrize('word', ['rank', 'ranked', 'ranking'])
def test_ranking_without_a_quantity_keeps_every_result(reader, word):
    plan, result = reader(f'{word} categories by Metric for Q2 2025')
    assert plan.limit is None and plan.order[0].direction == 'desc'
    with connection() as con:
        assert [r[:2] for r in con.execute(result.answer.sql).fetchall()] == [('A', Decimal(15))]


@pytest.mark.parametrize('quantity,n', [('two', 2), ('three', 3), ('10', 10)])
@pytest.mark.parametrize('word,direction', [('highest', 'desc'), ('most', 'desc'), ('lowest', 'asc'), ('least', 'asc')])
def test_ranking_quantity_can_qualify_the_dimension(reader, quantity, n, word, direction):
    plan, _ = reader(f'Which {quantity} categories had the {word} Metric for Q2 2025')
    assert plan.limit == n and plan.order[0].direction == direction


@pytest.mark.parametrize('name', ['Bottom metric', 'Best metric', 'At least metric', 'Smallest metric'])
def test_declared_names_do_not_set_ranking_direction_or_quantity(reader, name):
    plan, _ = reader(f'rank categories by {name} for Q2 2025', aliases=[(name, 'ctx')])
    assert plan.order[0].direction == 'desc' and plan.limit is None


@pytest.mark.parametrize('term', ['Annually', 'Daily', 'Weekly', 'Rest', 'Remainder', 'Portion', 'Proportion',
                                 'Rank', 'Ranked', 'Ranking', 'Best', 'Largest', 'Smallest', 'Mean', 'Median',
                                 'Minimum', 'Maximum', 'Contains', 'Bucket', 'Over'])
@pytest.mark.parametrize('connector', ['', 'for '])
def test_a_declared_measure_keeps_its_meaning_when_its_name_is_also_structural(monkeypatch, term, connector):
    sem = semantics()
    object.__setattr__(sem.contexts['ctx'], 'term', term)
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=[term])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)
    vocab = vocabulary(sem, aliases=[(term, 'ctx')])
    text = term + ' ' + connector + 'April 2025'
    reply = planrules.read(engine, parse(text, [term], sem.d), vocab, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    assert not reply.plan.dimensions and not reply.plan.derived and not reply.plan.order
    sql = compile_plan(checked).answer
    assert check(sql.sql, sql.allow) == []
    with connection() as con:
        assert con.execute(sql.sql).fetchone()[0] == 10


@pytest.mark.parametrize('word', ['rest', 'remainder'])
def test_complement_words_cannot_silently_request_the_original_share(reader, word):
    with pytest.raises(AssertionError, match='remaining share'):
        reader(f'Metric by category {word} of total for Q2 2025')


@pytest.mark.parametrize('word', ['rest', 'remainder'])
def test_model_share_cannot_claim_to_compute_a_complement(word):
    vocab = vocabulary()
    text = f'Metric by category {word} of total'
    plan = model(dimensions=[dict(kind='column', id='d', bindings=[dict(context=name('ctx'), column=name('u.events.category'), path=[], level=None)])],
                 derived=[dict(kind='share', id='s', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])])
    result = check_plan(ground_plan(plan, text, 'turn', vocab), vocab, text)
    assert not isinstance(result, CheckedPlan) and any('complement numerator' in reason for reason in result.missing)


@pytest.mark.parametrize('word', OPERATOR_FAMILIES['previous_period'] + ('previous period', 'month over month', 'month-over-month'))
def test_prior_period_family_builds_one_comparison(reader, word):
    grain = {'dod': 'day', 'wow': 'week', 'qoq': 'calendar_quarter'}.get(word,
        next((GRAIN_WORDS[token] for token in word.replace('-', ' ').split() if token in GRAIN_WORDS), 'month'))
    period, previous, current = {
        'day': ('2025-06-10', dt.date(2025, 6, 9), dt.date(2025, 6, 10)),
        'month': ('June 2025', dt.date(2025, 5, 1), dt.date(2025, 6, 1)),
        'calendar_quarter': ('Q2 2025', dt.date(2025, 1, 1), dt.date(2025, 4, 1)),
        'week': ('2025-W23', dt.date(2025, 5, 26), dt.date(2025, 6, 2)),
    }[grain]
    plan, result = reader(f'Metric for {period} compared with the {word}')
    assert plan.time.period.kind == grain
    assert len(plan.comparisons) == 1 and plan.comparisons[0].kind == 'previous_period'
    with connection([(day, value, 'A', 1, 'x') for day, value in [(previous, 20), (current, 5)]]) as con:
        assert con.execute(result.answer.sql).fetchone()[:2] == (5, 20)


@pytest.mark.parametrize('word', OPERATOR_FAMILIES['previous_year'] + ('previous year', 'year over year'))
def test_prior_year_family_builds_one_comparison(reader, word):
    plan, result = reader(f'Metric for June 2025 against the {word}')
    assert len(plan.comparisons) == 1 and plan.comparisons[0].kind == 'previous_year'
    with connection([(dt.date(y, 6, 1), value, 'A', 1, 'x') for y, value in [(2024, 8), (2025, 5)]]) as con:
        assert con.execute(result.answer.sql).fetchone()[:2] == (5, 8)


@pytest.mark.parametrize('word', ['excluding', *OPERATOR_FAMILIES['not_in']])
def test_exclusion_family_keeps_nulls(reader, word):
    plan, result = reader(f'Metric for Q2 2025 {word} A category')
    assert any(n.kind == 'not' and n.child.kind == 'in' for n in plan.nodes())
    rows = [(dt.date(2025, 4, 1), value, category, i, 'x') for i, (category, value) in enumerate([('A', 10), ('B', 20), (None, 5)])]
    with connection(rows) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 25


@pytest.mark.parametrize('word', ['include', 'includes', 'included', 'any of'])
@pytest.mark.parametrize('values,kind,expected', [('A category', 'compare', 10), ('A or B categories', 'in', 30)])
def test_inclusion_words_certify_the_filter_the_declared_values_build(reader, word, values, kind, expected):
    plan, result = reader(f'Metric for Q2 2025 {word} {values}')
    assert any(n.kind == kind for n in plan.filters)
    with connection([(dt.date(2025, 4, 1), value, category, i, 'x') for i, (category, value) in enumerate([('A', 10), ('B', 20), ('C', 5)])]) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == expected


@pytest.mark.parametrize('word,kind,expected', [('any of', 'any', 35), ('all of', 'all', 10)])
@pytest.mark.parametrize('connector', ['and', 'or', ',', ', and', ', or'])
def test_quantified_cross_column_filter_lists_keep_their_quantifier(reader, word, kind, expected, connector):
    plan, result = reader(f'Metric for Q2 2025 {word} A category {connector} x source')
    assert len(plan.filters) == 1 and plan.filters[0].kind == kind
    rows = [(dt.date(2025, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 4, 2), 20, 'B', 2, 'x'),
            (dt.date(2025, 4, 3), 5, 'A', 3, 'y'), (dt.date(2025, 4, 4), 7, 'B', 4, 'y')]
    with connection(rows) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == expected


def test_a_quantified_list_preserves_its_nested_boolean_group(reader):
    plan, result = reader('Metric for Q2 2025 any of (A category and x source) or B category')
    assert plan.filters[0].kind == 'any' and any(n.kind == 'all' for n in plan.filters[0].children)
    rows = [(dt.date(2025, 4, 1), 10, 'A', 1, 'x'), (dt.date(2025, 4, 2), 20, 'B', 2, 'x'),
            (dt.date(2025, 4, 3), 5, 'A', 3, 'y')]
    with connection(rows) as con:
        assert con.execute(result.answer.sql).fetchone()[0] == 30


@pytest.mark.parametrize('outer', ['any of', 'all of'])
@pytest.mark.parametrize('inner', ['any of', 'all of'])
@pytest.mark.parametrize('connector', ['and', 'or', ','])
def test_nested_quantifiers_cannot_silently_lose_the_outer_operation(monkeypatch, outer, inner, connector):
    from tests.unit.test_filter_grammar import proposed
    text = f'Metric for Q2 2025 {outer} A category {connector} {inner} (x source and B category)'
    reply, vocab = proposed(monkeypatch, text)
    assert not [p.text for p in ledger(text, 'turn', vocab) if p.kind == 'unknown']
    assert reply.kind == 'clarify', reply
    ordinary, _ = proposed(monkeypatch, 'Metric for Q2 2025 A category and (x source and B category)')
    assert ordinary.kind == 'propose', ordinary
    supplied = ground_plan(ordinary.plan.model_copy(update={'obligations': ()}), text, 'turn', vocab)
    rejected = check_plan(supplied, vocab, text)
    assert not isinstance(rejected, CheckedPlan)
    assert 'nested quantified filter lists' in str(rejected)


@pytest.mark.parametrize('word', ['share', 'portion', 'proportion', '% of total', 'percent of total', 'percentage of total'])
def test_share_family_builds_the_same_share(reader, word):
    plan, result = reader(f'Metric by category as a {word} for Q2 2025')
    assert any(d.kind == 'share' for d in plan.derived)
    with connection([(dt.date(2025, 4, 1), 15, 'A', 1, 'x'), (dt.date(2025, 6, 1), 5, 'B', 2, 'x')]) as con:
        assert [(r[0], r[2]) for r in con.execute(result.answer.sql).fetchall()] == [('A', .75), ('B', .25)]


@pytest.mark.parametrize('word', ['running total', 'cumulative', 'to date', *OPERATOR_FAMILIES['running_total']])
def test_running_total_family_orders_the_same_time_dimension(reader, word):
    text = 'Metric to date by months for April through June 2025' if word == 'to date' else f'{word} of Metric by months for April through June 2025'
    plan, result = reader(text)
    assert any(d.kind == 'running_total' for d in plan.derived)
    with connection([(dt.date(2025, m, 1), value, 'A', 1, 'x') for m, value in [(4, 10), (5, 2), (6, 5)]]) as con:
        assert [r[2] for r in con.execute(result.answer.sql).fetchall()] == [10, 12, 17]


@pytest.mark.parametrize('grain', ['day', 'days', 'month', 'months', 'quarter', 'quarters', 'year', 'years'])
@pytest.mark.parametrize('connector', ['and', ','])
def test_coordinated_breakdowns_accept_every_grain_plural(reader, grain, connector):
    plan, _ = reader(f'Metric by {grain} {connector} category for Q2 2025')
    assert len(plan.dimensions) == 2
    assert {d.kind for d in plan.dimensions} == {'time', 'column'}


@pytest.mark.parametrize('text', ['first thirteen months of 2024', 'first zero months of 2024',
                                 'first nine months of 2024 and June 2025', 'three months through Q2 2025'])
def test_partial_or_incompatible_numbered_periods_cannot_be_answered(reader, text):
    with pytest.raises(AssertionError):
        reader('Metric for ' + text)


@pytest.mark.parametrize('phrase', ['three-month moving average', 'ten-day rolling average', 'weekly', 'by week', 'per week',
                                   'top ten categories within each source', 'top two categories in each source',
                                   'Metric containing category in the name', 'Metric beginning with category'])
def test_new_construct_words_are_certified_before_other_workers_add_the_nodes(phrase):
    sem = semantics()
    sem.d.GROUP_BY = {'by category': 'u.events.category'}
    assert not [p.text for p in ledger(phrase, 'turn', vocabulary(sem)) if p.kind == 'unknown']


def test_unknown_business_words_and_extra_quantities_still_refuse(reader):
    with pytest.raises(AssertionError):
        reader('Metric for first nine months of 2024 unapproved')
    with pytest.raises(AssertionError):
        reader('top two and bottom three categories by Metric for Q2 2025')


@pytest.mark.parametrize('word,op', [('greater than', 'gt'), ('more than', 'gt'), ('over', 'gt'),
                                  ('less than', 'lt'), ('under', 'lt'), ('no less than', 'ge'),
                                  ('no more than', 'le'), ('exactly', 'eq'), ('not equal', 'ne')])
def test_numeric_filter_families_build_the_same_comparison(reader, word, op):
    plan, result = reader(f'Metric {word} ten for Q2 2025')
    assert any(n.kind == 'compare' and n.op == op and n.value.value == 10 for n in plan.filters)
    with connection() as con:
        assert bool(con.execute(result.answer.sql).fetchall()) == (op in ('gt', 'ge', 'ne'))


@pytest.mark.parametrize('word,op', [('and over', 'ge'), ('or above', 'ge'), ('and above', 'ge'),
                                  ('and under', 'le'), ('or below', 'le'), ('or less', 'le'), ('and below', 'le')])
def test_postpositive_thresholds_use_the_shared_comparison_reader(reader, word, op):
    plan, _ = reader(f'Metric ten {word} for Q2 2025')
    assert any(n.kind == 'compare' and n.op == op and n.value.value == 10 for n in plan.filters)


@pytest.mark.parametrize('text,ops', [('Metric over 5 or less than 20 for Q2 2025', ['gt', 'any', 'lt']),
                                      ('Metric under 5 or more than 20 for Q2 2025', ['lt', 'any', 'gt'])])
def test_or_less_and_or_more_before_than_join_two_comparisons(text, ops):
    pieces = ledger(text, 'turn', vocabulary())
    assert not [p.text for p in pieces if p.kind == 'unknown']
    assert [p.operation for p in pieces if p.kind == 'operator'] == ops


@pytest.mark.parametrize('word,direction', [('most', 'desc'), ('least', 'asc')])
def test_superlative_breakdown_returns_every_group_leader_first(reader, word, direction):
    """Which category had the most Metric" lists every category, ordered by the measure, the leader first, as a
    certified breakdown orders it; the singular word grounds the order, not a one-row cut."""
    rows = [(dt.date(2025, 4, 3), Decimal('5.00'), 'A', 1, 'x'), (dt.date(2025, 5, 9), Decimal('20.00'), 'B', 2, 'x'),
            (dt.date(2025, 6, 1), Decimal('4.00'), 'B', 3, 'x'), (dt.date(2025, 4, 20), Decimal('12.00'), 'C', 4, 'x'),
            (dt.date(2025, 1, 5), Decimal('99.00'), 'A', 5, 'x')]
    plan, result = reader(f'which category had the {word} Metric for Q2 2025')
    assert plan.limit is None and [o.direction for o in plan.order] == [direction]
    with connection(rows) as con:
        got = [(r[0], r[1]) for r in con.execute(result.answer.sql).fetchall()]
    sums = [('B', Decimal('24.00')), ('C', Decimal('12.00')), ('A', Decimal('5.00'))]
    assert got == (sums if direction == 'desc' else sums[::-1])
