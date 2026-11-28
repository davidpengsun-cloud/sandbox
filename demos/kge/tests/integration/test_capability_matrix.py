"""Recorded reachability on the sample vocabulary and explicit test pack additions, with no model calls.

The table is a baseline, not an assertion that every compiler construct is
reachable in English. Model means a supplied plan passes the model path's word
and vocabulary checks; it does not mean an LLM generated or executed that plan.
The refusing layer describes the rules failure, or the supplied plan's blocker
when neither path reaches the construct. A dash means both paths reached it.
Aggregation and Declared rows use the scratch measure declarations in
test_measure_capabilities; Join rows use isolated scratch join declarations.
The served sample pack is not changed.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
from types import SimpleNamespace

import sqlglot
from sqlglot import exp

from kge.compiler.vocabulary import join_entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plan import Binding, Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Join
from kge.semantics.vocabulary import compiled as lexical_vocabulary
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.question import parse
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_measure_capabilities import DECLARATIONS, declared_engine
from tests.integration.test_plan_wording_round2 import Recording
from tests.plan_fixtures import model, name, semantics


@dataclass(frozen=True)
class Capability:
    construct: str
    question: str
    rules: str
    model: str
    refusing_layer: str


MATRIX = (
    Capability('Compare text eq', 'NNA for the NORTHEAST region for June 2025', 'yes', 'yes', '—'),
    Capability('Compare text ne', 'NNA where office.region_code is not equal to CEO for June 2025', 'yes', 'yes', '—'),
    Capability('Compare text lt', 'NNA where office.region_code is below CEO for June 2025', 'yes', 'yes', '—'),
    Capability('Compare text le', 'NNA where office.region_code is at most CEO for June 2025', 'yes', 'yes', '—'),
    Capability('Compare text gt', 'NNA where office.region_code is above CEO for June 2025', 'yes', 'yes', '—'),
    Capability('Compare text ge', 'NNA where office.region_code is at least CEO for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number eq', 'NNA equal to $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number ne', 'NNA not equal to $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number lt', 'NNA below $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number le', 'NNA at most $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number gt', 'NNA above $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('Compare number ge', 'NNA at least $100,000 for June 2025', 'yes', 'yes', '—'),
    Capability('In', 'NNA for NORTHEAST or MIDWEST regions for June 2025', 'yes', 'yes', '—'),
    Capability('Not over In', 'NNA excluding NORTHEAST or MIDWEST regions for June 2025', 'yes', 'yes', '—'),
    Capability('IsNull true', 'How many retail complaints were still unresolved at 30 June 2025?', 'yes', 'yes', '—'),
    Capability('IsNull false', 'NNA where flow.counterparty_account is not null for June 2025', 'yes', 'yes', '—'),
    Capability('Boolean flag', 'Number of SME facilities in covenant breach', 'yes', 'yes', '—'),
    Capability('Junction all', 'NNA for the NORTHEAST region and segment level 5 AFFLUENT for June 2025', 'yes', 'yes', '—'),
    Capability('Junction any', 'NNA for NORTHEAST region or SPS accounts for June 2025', 'yes', 'yes', '—'),
    Capability('Membership', 'NNA for accounts that also have AUM for June 2025', 'no', 'no', 'pack vocabulary'),
    Capability('Single day', 'NNA for 2025-06-10', 'yes', 'yes', '—'),
    Capability('Single week', 'NNA for 2025-W23', 'yes', 'yes', '—'),
    Capability('Single month', 'NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Single calendar_quarter', 'NNA for Q2 2025', 'yes', 'yes', '—'),
    Capability('Single fiscal_quarter', 'NNA for FY26-Q1', 'yes', 'yes', '—'),
    Capability('Single year', 'NNA for 2024', 'yes', 'yes', '—'),
    Capability('Single fiscal_year', 'NNA for FY25', 'yes', 'yes', '—'),
    Capability('Range', 'certified NNA for January through March 2025', 'yes', 'yes', '—'),
    Capability('Range abbreviated months', 'NNA for Jan-Sep 2024', 'yes', 'yes', '—'),
    Capability('Range month connectors', 'NNA from January to September 2024', 'yes', 'yes', '—'),
    Capability('Range between months', 'NNA between January and September 2024', 'yes', 'yes', '—'),
    Capability('Range implied year', 'NNA from January to June', 'yes', 'yes', '—'),
    Capability('Range quarters', 'NNA for Q1 through Q3 2024', 'yes', 'yes', '—'),
    Capability('Range weeks', 'NNA for 2025-W22 through 2025-W23', 'yes', 'yes', '—'),
    Capability('Range numbered months', 'NNA for the first nine months of 2024', 'yes', 'yes', '—'),
    Capability('Range numbered quarters', 'NNA for the first three quarters of 2024', 'yes', 'yes', '—'),
    Capability('Range fiscal quarters', 'NNA for FY25-Q1 through FY25-Q2', 'yes', 'yes', '—'),
    Capability('Range fiscal months', 'NNA for the first six fiscal months of FY25', 'yes', 'yes', '—'),
    Capability('Range with month breakdown', 'NNA by month for Jan-Sep 2024', 'yes', 'yes', '—'),
    Capability('Ytd', 'NNA year to date through June 2025', 'yes', 'yes', '—'),
    Capability('Ytd fiscal', 'NNA fiscal year to date through June 2025', 'yes', 'yes', '—'),
    Capability('Ytd calendar fiscal breakdown', 'NNA by fiscal quarter for calendar year to date through June 2025', 'yes', 'yes', '—'),
    Capability('Mtd', 'NNA month to date through 2025-06-15', 'yes', 'yes', '—'),
    Capability('Qtd', 'NNA quarter to date through 2025-06-15', 'yes', 'yes', '—'),
    Capability('Qtd fiscal', 'NNA fiscal quarter to date through 2025-06-15', 'yes', 'yes', '—'),
    Capability('Current Ytd', 'NNA year to date', 'yes', 'yes', '—'),
    Capability('Current Mtd', 'NNA month to date', 'yes', 'yes', '—'),
    Capability('Current Qtd', 'NNA quarter to date', 'yes', 'yes', '—'),
    Capability('latest_released', 'NNA for the latest month', 'yes', 'yes', '—'),
    Capability('all_available', 'NNA for all available', 'yes', 'yes', '—'),
    Capability('Dimension column', 'NNA by household for June 2025', 'yes', 'yes', '—'),
    Capability('Dimension hierarchy', 'NNA by region for June 2025', 'yes', 'yes', '—'),
    Capability('Dimension time day', 'NNA by day for June 2025', 'yes', 'yes', '—'),
    Capability('Dimension time week', 'NNA by week for June 2025', 'yes', 'yes', '—'),
    Capability('Dimension time month', 'certified NNA for Q1 2025 by month', 'yes', 'yes', '—'),
    Capability('Dimension time calendar_quarter', 'NNA by quarter for 2024', 'yes', 'yes', '—'),
    Capability('Dimension time fiscal_quarter', 'NNA by fiscal quarter for FY26-Q1', 'yes', 'yes', '—'),
    Capability('Dimension time year', 'NNA by year for all available', 'yes', 'yes', '—'),
    Capability('Dimension time fiscal_year', 'NNA by fiscal year for FY26-Q1', 'yes', 'yes', '—'),
    Capability('PlanMeasure', 'NNA for June 2025', 'yes', 'yes', '—'),
    Capability('DistinctCount', 'NNA with number of households for June 2025', 'yes', 'yes', '—'),
    Capability('Mode current', 'current NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Mode as_reported', 'NNA as reported for June 2025', 'yes', 'yes', '—'),
    Capability('TimeModeComparison', 'NNA current vs as reported for June 2025', 'yes', 'yes', '—'),
    Capability('previous_period', 'NNA for Q2 2025 vs the previous period', 'yes', 'yes', '—'),
    Capability('Week over week', 'NNA by week for June 2025 week over week', 'yes', 'yes', '—'),
    Capability('previous_year', 'year over year change in NNA for Q2 2025', 'yes', 'yes', '—'),
    Capability('explicit_period', 'NNA for Q2 2025 vs Q1 2025', 'yes', 'yes', '—'),
    Capability('contexts', 'current NNA vs certified NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('ratio', 'ratio of client-directed NNA to NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('percent_change', 'percent change in NNA from Q1 to Q2 2025', 'yes', 'yes', '—'),
    Capability('difference', 'change in NNA from March to April 2025', 'yes', 'yes', '—'),
    Capability('share', 'NNA by region as a share of total for June 2025', 'yes', 'yes', '—'),
    Capability('Share split', 'How much NNA was from SPS accounts and how much from the rest for June 2025?', 'yes', 'yes', '—'),
    Capability('running_total', 'NNA by month for January through June 2025 with cumulative NNA', 'yes', 'yes', '—'),
    Capability('moving_average', '3-month moving average of NNA by month for January through June 2025', 'yes', 'yes', '—'),
    Capability('Order asc', 'bottom 3 branches by NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Order desc', 'top 3 branches by NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Order both', 'top and bottom 3 branches by NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Order partitioned', 'top 3 branches by NNA in each region for Q1 2025', 'yes', 'yes', '—'),
    Capability('Limit', 'top 3 branches by NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Superlative over a breakdown', 'which region had the most NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Layout separate', 'NNA and certified NNA separate for June 2025', 'yes', 'yes', '—'),
    Capability('Part inflow', 'gross inflows of NNA for Q1 2025', 'yes', 'yes', '—'),
    Capability('Part outflow', 'NNA inflows and outflows for Q1 2025', 'yes', 'yes', '—'),
    Capability('Part net', 'NNA inflows, outflows and net for Q1 2025', 'yes', 'yes', '—'),
    Capability('Counts', 'NNA with number of transactions for June 2025', 'yes', 'yes', '—'),
    Capability('Per-entity counts', 'NNA with number of transactions by household for June 2025', 'yes', 'yes', '—'),
    Capability('sum + count in one answer', 'NNA with account counts for June 2025', 'yes', 'yes', '—'),
    Capability('Ranked sum + count', 'top 3 branches by NNA for January through June 2025 with account counts', 'yes', 'yes', '—'),
    Capability('Repeated measure aggregations', 'sum NNA and count NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation sum', 'sum NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation count', 'count NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation avg', 'average NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation min', 'minimum NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation max', 'maximum NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Aggregation median', 'median NNA for June 2025', 'yes', 'yes', '—'),
    Capability('Declared ratio', 'Flow quotient for June 2025', 'yes', 'yes', '—'),
    Capability('Declared difference', 'Flow spread for June 2025', 'yes', 'yes', '—'),
    Capability('Declared percent_change', 'Flow growth for June 2025', 'yes', 'yes', '—'),
)


MATRIX += tuple(Capability(f'Offset {kind} {grain}', question, 'yes', 'yes', '—')
                for kind, phrase in (('previous_period', 'previous period'), ('previous_year', 'same period a year earlier'))
                for grain, period, grouping in (('day', '2025-06-10', ''), ('week', '2025-W24', ''),
                    ('month', 'June 2025', ''), ('calendar_quarter', 'Q2 2025', ''), ('year', 'September 2024', ' by year'))
                for question in (f'NNA{grouping} for {period} versus {phrase}, with difference and percent change',))
MATRIX += (
    Capability('Version comparison', 'current NNA against certified NNA by month for Q3 2024, with difference and percent change', 'yes', 'yes', '—'),
    Capability('Calendar attribute month_of_year', 'NNA for every June from July 2023 through June 2025', 'yes', 'yes', '—'),
    Capability('Calendar attribute week_of_month', 'NNA for week 2 of each month for January through June 2025', 'yes', 'yes', '—'),
    Capability('Magnitude threshold', 'How many transactions of $250K or more did the Fort Collins branch have in January 2024, and what was their branch sales NNA?', 'yes', 'yes', '—'),
)


JOIN_MATRIX = (
    Capability('Join inner', 'Metric by grouping', 'yes', 'yes', '—'),
    Capability('Join left', 'Metric by grouping', 'yes', 'yes', '—'),
    Capability('Join right', 'Metric by grouping', 'yes', 'yes', '—'),
    Capability('Join full', 'Metric by grouping', 'yes', 'yes', '—'),
    Capability('Join validity_window', 'Metric by grouping', 'yes', 'yes', '—'),
)


TEXTBAND_MATRIX = (
    Capability('Compare text contains', 'Metric for matching labels', 'yes', 'yes', '—'),
    Capability('Compare text starts_with', 'Metric for initial labels', 'yes', 'yes', '—'),
    Capability('Dimension band', 'Metric by size band', 'yes', 'yes', '—'),
)


def test_declared_text_and_band_capability_matrix(monkeypatch):
    """Observe declarations, both planner paths, compilation and gate admission on an isolated pack."""
    from tests.unit.test_textband_figures import SuppliedPlan, band_dimension, declarations, text_filter
    observed = []
    for case in TEXTBAND_MATRIX:
        engine, vocab = declarations(monkeypatch)
        rules = planrules.read(engine, parse(case.question, engine.term_names, engine), vocab, PRESETS['graph'])
        supplied = model(dimensions=[band_dimension()]) if case.construct == 'Dimension band' else model(
            filters=[text_filter(case.construct.rsplit(' ', 1)[-1])])
        proposed = ground_plan(supplied, case.question, 'turn', vocab)
        replay = planmodel.read(SuppliedPlan(proposed), case.question, vocab)
        for path, reply in (('rules', rules), ('model', replay)):
            assert reply.kind == 'propose', (case.construct, path, reply)
            if case.construct == 'Dimension band':
                assert any(d.kind == 'band' for d in reply.plan.dimensions)
            else:
                assert any(n.kind == 'compare' and n.op == case.construct.rsplit(' ', 1)[-1] for n in reply.plan.nodes())
            checked = check_plan(reply.plan, vocab, case.question)
            assert isinstance(checked, CheckedPlan), (case.construct, path, checked)
            result = compile_plan(checked)
            assert result.answer is not None, (case.construct, path, result)
            assert check(result.answer.sql, result.answer.allow) == []
        observed.append((case.construct, case.question, 'yes', 'yes', '—'))
    print('\n| construct | question | rules | model | refusing layer |')
    print('| --- | --- | --- | --- | --- |')
    for row in observed:
        print('| ' + ' | '.join(row) + ' |')
    assert [row[2:] for row in observed] == [(case.rules, case.model, case.refusing_layer) for case in TEXTBAND_MATRIX]


def join_declarations(join_type, window, monkeypatch):
    """A scratch pack supplies each join contract without loading a graph or changing sample metadata."""
    sem = semantics(joined=True)
    sem.tables['u.members'].update(effective='DATE', expiry='DATE')
    sem.d.GROUP_BY = {'by grouping': 'u.members.grouping'}
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.INTENTS = {'metric': 'Metric'}, {}, {}
    declaration = {'id': 'member_join', 'type': join_type, 'cardinality': 'one-to-one'}
    if window:
        declaration['validity_window'] = {'date': 'u.events.occurred', 'effective': 'u.members.effective',
                                          'expiry': 'u.members.expiry'}
    sem.d.VOCABULARY = join_entries({'joins': [declaration]})
    options = sem.d.VOCABULARY[0]['params']
    original = sem.contexts['ctx'].joins[0]
    join = Join(original.id, original.kind, original.condition, None, None, original.source, original.target, True, options)
    sem.contexts['ctx'] = replace(sem.contexts['ctx'], grain='day', filters=(), joins=(join,))
    sem.declared = {join.id: join}
    sem.types = {table + '.' + column: dtype for table, columns in sem.tables.items() for column, dtype in columns.items()}
    words = lexical_vocabulary(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    spec = MeasureSpec('u.metric', 'USD', 'entities', True)
    sem.measure_specs, sem.measure_declarations, sem.output_metadata = {spec.id: spec}, {spec.id: {}}, {}
    vocab = build_vocabulary(sem, Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                             ['u'], ['u'], [spec], aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    engine = SimpleNamespace(semantics=sem, term_names=['Metric'], FISCAL_START=4, **vars(sem.d))
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **kw: sem)
    return engine, vocab


def test_declared_join_capability_matrix(monkeypatch):
    """Observe the rules and supplied-model paths through compilation and the SQL gate for every join option."""
    observed = []
    for case in JOIN_MATRIX:
        window = case.construct == 'Join validity_window'
        join_type = 'left' if window else case.construct.split()[1]
        engine, vocab = join_declarations(join_type, window, monkeypatch)
        rules = planrules.read(engine, parse(case.question, engine.term_names, engine), vocab, PRESETS['graph'])
        dimension = {'kind': 'column', 'id': 'group', 'bindings': [
            {'context': name('ctx'), 'column': name('u.members.grouping'), 'path': [name('member_join')], 'level': None}]}
        supplied = ground_plan(model(dimensions=[dimension]), case.question, 'turn', vocab)
        replay = planmodel.read(Recording(Propose(kind='propose', base_turn=None, plan=supplied)), case.question, vocab)
        for path, reply in (('rules', rules), ('model', replay)):
            assert reply.kind == 'propose', (case.construct, path, reply)
            checked = check_plan(reply.plan, vocab, case.question)
            assert isinstance(checked, CheckedPlan), (case.construct, path, checked)
            result = compile_plan(checked)
            assert result.answer is not None, (case.construct, path, result)
            assert check(result.answer.sql, result.answer.allow) == [], (case.construct, path, result.answer.sql)
            tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
            rendered, = [j for j in tree.find_all(exp.Join) if isinstance(j.this, exp.Table) and j.this.name == 'members']
            assert (rendered.side.casefold() or 'inner') == join_type
            assert bool(list(rendered.find_all(exp.Between))) == window
        observed.append((case.construct, case.question, 'yes', 'yes', '—'))
    print('\n| construct | question | rules | model | refusing layer |')
    print('| --- | --- | --- | --- | --- |')
    for row in observed:
        print('| ' + ' | '.join(row) + ' |')
    assert [row[2:] for row in observed] == [(case.rules, case.model, case.refusing_layer) for case in JOIN_MATRIX]


def has_construct(plan, construct, vocab):
    """Match the actual shape, including scalar types, roles and local parts."""
    if plan is None:
        return False
    nodes = tuple(plan.nodes())
    if construct.startswith('Offset '):
        _, kind, grain = construct.split()
        return any(c.kind == kind for c in plan.comparisons) and (any(
            d.kind == 'time' and d.grain == grain for d in plan.dimensions) or
            plan.time.kind == 'single' and plan.time.period.kind == grain)
    if construct == 'Version comparison':
        return any(c.kind == 'contexts' for c in plan.comparisons) and any(
            d.kind == 'time' and d.grain == 'month' for d in plan.dimensions)
    if construct.startswith('Calendar attribute '):
        attribute = construct.split()[2]
        columns = {calendar.get('spine', {}).get('attributes', {}).get(attribute) for calendar in vocab.calendars.values()}
        return any(n.kind == 'compare' and n.left.kind == 'column' and n.left.name.id in columns for n in nodes)
    if construct == 'Repeated measure aggregations':
        return any(left.kind == right.kind == 'measure' and left.measure.id == right.measure.id
                   and left.context.id == right.context.id
                   and (left.aggregation or vocab.contexts[left.context.id].aggregation) == 'sum'
                   and (right.aggregation or vocab.contexts[right.context.id].aggregation) == 'count'
                   for left in plan.measures for right in plan.measures)
    if construct == 'Ranked sum + count':
        ranked = {m.id: m for m in plan.measures}.get(plan.order[0].output) if plan.order else None
        return (plan.time.kind == 'range' and plan.limit == 3 and ranked is not None and ranked.kind == 'measure'
                and any(m.kind == 'distinct_count' and m.entity.id == 'wealth_account' for m in plan.measures))
    if construct == 'sum + count in one answer':
        return (any(m.kind == 'measure' and (m.aggregation or vocab.contexts[m.context.id].aggregation) == 'sum' for m in plan.measures)
                and any(m.kind == 'distinct_count' for m in plan.measures))
    if construct.startswith('Aggregation '):
        return any(m.kind == 'measure' and (m.aggregation or vocab.contexts[m.context.id].aggregation) == construct.split()[1]
                   for m in plan.measures)
    if construct.startswith('Declared '):
        return any(n.kind == construct.split()[1] and n.declaration is not None for n in plan.derived)
    if construct == 'Magnitude threshold':
        return any(n.kind == 'compare' and n.magnitude for n in nodes)
    if construct.startswith('Compare '):
        _, scalar, op = construct.split()
        kinds = ('integer', 'decimal') if scalar == 'number' else ('text',)
        return any(n.kind == 'compare' and n.op == op and n.value.kind in kinds for n in nodes)
    if construct.startswith('Single '):
        return plan.time.kind == 'single' and plan.time.period.kind == construct.split()[1]
    if construct.startswith('Range '):
        return plan.time.kind == 'range' and (not construct.endswith('breakdown') or any(d.kind == 'time' for d in plan.dimensions))
    if construct.startswith(('Ytd ', 'Current ')) or construct in ('Mtd', 'Qtd', 'Qtd fiscal'):
        return plan.time.kind == ('ytd' if 'Ytd' in construct else 'range')
    if construct == 'Week over week':
        return any(c.kind == 'previous_period' for c in plan.comparisons) and any(d.kind == 'time' and d.grain == 'week' for d in plan.dimensions)
    if construct == 'Share split':
        return any(n.kind == 'share' for n in plan.derived) and not plan.filters and any(
            b.column.id == 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN' for d in plan.dimensions for b in getattr(d, 'bindings', ()))
    if construct.startswith('Dimension time '):
        return any(d.kind == 'time' and d.grain == construct.split()[2] for d in plan.dimensions)
    if construct.startswith('Dimension '):
        return any(d.kind == construct.split()[1] for d in plan.dimensions)
    if construct.startswith('Mode '):
        return any(m.mode == construct.split()[1] for m in plan.measures)
    if construct == 'Order partitioned':
        return any(o.partition_by for o in plan.order)
    if construct == 'Superlative over a breakdown':
        return plan.limit is None and [o.direction for o in plan.order] in (['asc'], ['desc']) and bool(plan.dimensions)
    if construct.startswith('Order '):
        return any(o.direction == construct.split()[1] for o in plan.order)
    if construct.startswith('Part '):
        def direction(measure, value):
            return any(f.kind == 'compare' and f.op == 'eq' and f.left.kind == 'column'
                       and f.left.name.id.endswith('.DW_TRAN_DIRCTN_CD') and f.value.kind == 'text'
                       and f.value.value == value for f in (*measure.filters, *plan.filters))
        if construct == 'Part net':
            return any(not m.filters and any(direction(s, 'I') and s.context.id == m.context.id for s in plan.measures)
                       and any(direction(s, 'O') and s.context.id == m.context.id for s in plan.measures) for m in plan.measures)
        return any(direction(m, 'I' if construct == 'Part inflow' else 'O') for m in plan.measures)
    if construct in ('Counts', 'Per-entity counts'):
        counts = any(m.kind == 'distinct_count' or vocab.contexts[m.context.id].aggregation == 'count' for m in plan.measures)
        if construct == 'Counts':
            return counts
        entity = vocab.entities['wealth_account_household']
        keys = {entity['table'] + '.' + key for key in entity['key']}
        grouped = {b.column.id for d in plan.dimensions if d.kind in ('column', 'hierarchy') for b in d.bindings}
        return any(m.kind == 'distinct_count' and m.entity.id == 'wealth_flow' for m in plan.measures) and keys <= grouped
    if construct == 'Not over In':
        return any(n.kind == 'not' and n.child.kind == 'in' for n in nodes)
    if construct.startswith('IsNull '):
        return any(n.kind == 'null' and n.is_null == (construct == 'IsNull true') for n in nodes)
    if construct == 'Boolean flag':
        return any(n.kind == 'compare' and n.value.kind == 'boolean' for n in nodes)
    if construct == 'Limit':
        return plan.limit is not None
    if construct == 'Layout separate':
        return plan.layout == 'separate'
    kinds = {'In': 'in', 'Junction all': 'all', 'Junction any': 'any', 'Membership': 'membership',
             'PlanMeasure': 'measure', 'DistinctCount': 'distinct_count', 'TimeModeComparison': 'time_mode'}
    kind = kinds.get(construct, construct.lower())
    return plan.time.kind == kind or any(n.kind == kind for n in nodes)


def supplied_plan(construct, vocab):
    """Hand-author unsupported rules shapes using real sample declarations."""
    ctx = vocab.contexts['nna']
    def measure(context=ctx, identifier='m'):
        return dict(kind='measure', id=identifier, measure=name(context.measure), context=name(context.id), mode='current', filters=[])

    def operand(column):
        path = planrules.path_to(ctx, column.rsplit('.', 1)[0])
        assert path is not None, column
        return dict(kind='column', name=name(column), path=[name(p) for p in path])

    def compare(column, value, identifier='f', op='eq', scalar='text'):
        return dict(kind='compare', id=identifier, left=operand(column), op=op,
                    value=dict(kind=scalar, value=value), evidence=[])

    def dimension(column, kind='column'):
        path = operand(column)['path']
        level = sum(j.kind == 'hierarchy' for j in ctx.joins if j.id in {p['id'] for p in path}) if kind == 'hierarchy' else None
        return dict(kind=kind, id='d', bindings=[dict(context=name(ctx.id), column=name(column), path=path, level=level)])

    def period(grain='month', value='2025-06'):
        return dict(kind=grain, value=value, calendar=name('fiscal_april' if grain.startswith('fiscal_') else 'calendar'))

    data = dict(schema='query-plan/v1', layout='aligned', measures=[measure()], dimensions=[], filters=[],
                time=dict(kind='single', period=period()), comparisons=[], derived=[], order=[], limit=None, obligations=[])
    region = 'wealth.BRCH_DIM_1.RGN_CD'
    if construct.startswith('Current '):
        from kge.semantics.coverage import to_date_selection
        question = next(c.question for c in MATRIX if c.construct == construct)
        data['time'] = to_date_selection(ledger(question, 'turn', vocab), vocab).model_dump(mode='json')
    elif construct == 'Repeated measure aggregations':
        data['measures'][0]['aggregation'] = 'sum'
        data['measures'].append({**measure(identifier='count'), 'aggregation': 'count'})
    elif construct.startswith('Aggregation '):
        data['measures'][0]['aggregation'] = construct.split()[1]
    elif construct.startswith('Declared '):
        kind = construct.split()[1]
        data['measures'].append(measure(vocab.contexts['nna_certified'], 'certified'))
        data['derived'] = [dict(kind=kind, id='formula', numerator='m', denominator='certified',
                               declaration=name(DECLARATIONS[kind][0]), evidence=[])]
    elif construct.startswith('Compare '):
        _, scalar, op = construct.split()
        if scalar == 'number':
            data['filters'] = [dict(kind='compare', id='f', left=dict(kind='output', id='m'),
                                   op=op, value=dict(kind='integer', value=100000), evidence=[])]
        else:
            data['filters'] = [compare(region, 'CEO', op=op)]
    elif construct in ('In', 'Not over In'):
        pred = dict(kind='in', id='f', left=operand(region),
                    values=[dict(kind='text', value=v) for v in ('CEO', 'MIDWEST')], evidence=[])
        data['filters'] = [dict(kind='any', id='exclusion', children=[dict(kind='not', id='negated', child=pred, evidence=[]),
                            dict(kind='null', id='empty', left=operand(region), is_null=True, evidence=[])], evidence=[])] if construct == 'Not over In' else [pred]
    elif construct.startswith('IsNull '):
        data['filters'] = [dict(kind='null', id='f', left=operand('wealth.TRAN_FACT_1.OFSET_ACCT_ID'),
                               is_null=construct == 'IsNull true', evidence=[])]
    elif construct == 'Boolean flag':
        data['filters'] = [compare('wealth.TRAN_FACT_1.NNA_IN', True, scalar='boolean')]
    elif construct.startswith('Junction '):
        other = (compare('wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'S4100', 'g') if construct.endswith('all')
                 else compare('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN', 'Y', 'g'))
        data['filters'] = [dict(kind=construct.split()[1], id='junction',
                               children=[compare(region, 'CEO'), other], evidence=[])]
    elif construct == 'Membership':
        data['filters'] = [dict(kind='membership', id='f', entity=name('wealth_account'),
                               relation=name('flow_account'), context=name('aum'), filters=[], evidence=[])]
    elif construct.startswith('Single '):
        grain = construct.split()[1]
        values = dict(day='2025-06-10', month='2025-06', calendar_quarter='2025-Q2',
                      fiscal_quarter='FY26-Q1', year='2024', fiscal_year='FY25')
        data['time'] = dict(kind='single', period=period(grain, values[grain]))
    elif construct in ('latest_released', 'all_available'):
        data['time'] = dict(kind=construct, calendar=name(vocab.periods[ctx.fact]['calendar']))
    elif construct.startswith('Dimension time '):
        grain = construct.split()[2]
        data['dimensions'] = [dict(kind='time', id='d', grain=grain, calendar=period(grain)['calendar'])]
        data['time'] = dict(kind='all_available', calendar=period(grain)['calendar'])
        if grain in ('fiscal_quarter', 'fiscal_year'):
            data['time'] = dict(kind='single', period=period('fiscal_quarter', 'FY26-Q1'))
    elif construct == 'Dimension hierarchy':
        data['dimensions'] = [dimension(region, 'hierarchy')]
    elif construct == 'share':
        data['dimensions'] = [dimension(region, 'hierarchy')]
        data['derived'] = [dict(kind='share', id='share', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])]
    elif construct == 'Share split':
        data['dimensions'] = [dimension('wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN')]
        data['derived'] = [dict(kind='share', id='share', input='m', remove_dimensions=['d'], remove_filters=[], evidence=[])]
    elif construct == 'running_total':
        data['time'] = dict(kind='range', first=period(value='2025-01'), last=period())
        data['dimensions'] = [dict(kind='time', id='d', grain='month', calendar=name('calendar'))]
        data['derived'] = [dict(kind='running_total', id='running', input='m', partition_by=[], order_by='d', evidence=[])]
    elif construct == 'Order both':
        data['time'] = dict(kind='single', period=period('calendar_quarter', '2025-Q1'))
        data['dimensions'] = [dimension('wealth.BRCH_DIM_1.BRCH_CD', 'hierarchy')]
        data['order'] = [dict(output='m', direction='both', nulls='last', evidence=[])]
        data['limit'] = 3
    elif construct == 'Ranked sum + count':
        data['time'] = dict(kind='range', first=period(value='2025-01'), last=period())
        data['dimensions'] = [dimension('wealth.BRCH_DIM_1.BRCH_CD', 'hierarchy')]
        data['measures'].append(dict(kind='distinct_count', id='n', entity=name('wealth_account'),
                                     context=name(ctx.id), mode='current', filters=[]))
        data['order'] = [dict(output='m', direction='desc', nulls='last', evidence=[])]
        data['limit'] = 3
    elif construct == 'Order partitioned':
        data['time'] = dict(kind='single', period=period('calendar_quarter', '2025-Q1'))
        data['dimensions'] = [dimension('wealth.BRCH_DIM_1.BRCH_CD', 'hierarchy'), dimension(region, 'hierarchy') | {'id': 'region'}]
        data['order'] = [dict(output='m', direction='desc', nulls='last', evidence=[], partition_by=['region'])]
        data['limit'] = 3
    elif construct == 'moving_average':
        data['time'] = dict(kind='range', first=period(value='2025-01'), last=period())
        data['dimensions'] = [dict(kind='time', id='d', grain='month', calendar=name('calendar'))]
        data['derived'] = [dict(kind='moving_average', id='moving', input='m', partition_by=[], order_by='d', length=3, evidence=[])]
    elif construct == 'Layout separate':
        data['layout'] = 'separate'
        data['measures'].append(measure(vocab.contexts['nna_certified'], 'certified'))
    elif construct == 'TimeModeComparison':
        data['comparisons'] = [dict(kind='time_mode', id='c', inputs=['m'], base='current', other='as_reported', evidence=[])]
    elif construct.startswith('Part '):
        data['time'] = dict(kind='single', period=period('calendar_quarter', '2025-Q1'))
        data['measures'] = [measure()] if construct == 'Part net' else []
        parts = (('inflows', 'I'),) if construct == 'Part inflow' else (('inflows', 'I'), ('outflows', 'O'))
        for identifier, value in parts:
            part = measure(identifier=identifier)
            part['filters'] = [compare('wealth.TRAN_FACT_1.DW_TRAN_DIRCTN_CD', value, identifier + '_filter')]
            data['measures'].append(part)
    elif construct in ('DistinctCount', 'Counts', 'Per-entity counts', 'sum + count in one answer'):
        entity = ('wealth_account_household' if construct == 'DistinctCount' else
                  'wealth_account' if construct == 'sum + count in one answer' else 'wealth_flow')
        data['measures'].append(dict(kind='distinct_count', id='n', entity=name(entity),
                                     context=name(ctx.id), mode='current', filters=[]))
        if construct == 'Per-entity counts':
            data['dimensions'] = [dimension('wealth.ACCT_DIM2_1.LAST_RCV_HSHLD_DK')]
    else:
        raise AssertionError('Add a supplied plan for ' + construct)
    return QueryPlan.model_validate(data)


def refusing_layer(answer, decision, rules, model, planning, reply):
    """Use gate evidence and known declaration diagnostics before word failures.

    Coverage often returns the uncovered words themselves. Only the explicit
    declaration/type errors below are vocabulary failures; an arbitrary unfamiliar
    word or an unfulfilled obligation belongs to the word check.
    """
    if rules and model:
        return '—'
    guard = planning.get('guard') or answer.get('guard') or {}
    if guard.get('failed_at') in ('static', 'partition', 'dry run', 'cost gate'):
        return 'SQL gate'
    assert not guard.get('failed_at'), ('Unexpected warehouse failure', guard)
    if planning.get('phase') in ('compile', 'release'):
        return 'compiler'
    declarations = ('absent from the readable vocabulary', 'steward certification', 'missing declared',
                    'missing readable', 'missing complete entity', 'missing release/freeze',
                    'missing compatible period binding', 'undeclared member value', 'literal on non', 'membership entity',
                    'entity has no declared path', 'role does not reach', 'no declared',
                    'outside access or product scope', 'certified relationship')
    if not model or (reply is not None and reply.kind == 'propose' and planning.get('kind') != 'result'):
        missing = ' '.join(decision.missing if not model else planning.get('missing', ())).casefold()
        return 'pack vocabulary' if any(p in missing for p in declarations) else 'word check'
    return 'rules planner'


def without_evidence(value):
    """Replay selections with fresh evidence instead of another turn's spans."""
    if isinstance(value, dict):
        return {k: [] if k in ('evidence', 'obligations') else without_evidence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_evidence(v) for v in value]
    return value


def test_capability_matrix(eng, monkeypatch):
    """One engine and one cached answer per question keep this inventory cheap."""
    for tier in ('', '_HIGH', '_MEDIUM', '_LOW'):
        monkeypatch.setenv('MODEL' + tier + '_PROVIDER', 'none')
    planning, replies = {}, {}
    original_answer, original_read = Planner.answer, planrules.read

    def record_answer(self, question, *args, **kwargs):
        result = original_answer(self, question, *args, **kwargs)
        planning[question] = result
        return result

    def record_read(engine, req, *args, **kwargs):
        result = original_read(engine, req, *args, **kwargs)
        replies.setdefault(req.question, result)
        return result

    monkeypatch.setattr(Planner, 'answer', record_answer)
    monkeypatch.setattr(planrules, 'read', record_read)

    scratch = declared_engine(eng)

    @lru_cache(maxsize=None)
    def answer(question, declared=False):
        return (scratch if declared else eng).answer(question, 'analyst_enterprise')

    observed, failures = [], []
    for case in MATRIX:
        declared = case.construct.startswith(('Aggregation ', 'Declared ')) or case.construct == 'Repeated measure aggregations'
        result = answer(case.question, declared)
        vocab = vocabulary(scratch if declared else eng, case.question)
        history = result.get('history') or []
        raw = history[-1].get('plan') if history else None
        rules_plan = QueryPlan.model_validate(raw) if raw else None
        rules = has_construct(rules_plan, case.construct, vocab)
        plan = rules_plan if rules else supplied_plan(case.construct, vocab)
        plan = QueryPlan.model_validate(without_evidence(plan.model_dump(mode='json', by_alias=True)))
        plan = ground_plan(plan, case.question, 'turn', vocab)
        if plan.layout == 'separate':
            separate = {p.span for p in ledger(case.question, 'turn', vocab) if p.operation == 'separate'}
            plan = plan.model_copy(update={'obligations': tuple(
                o.model_copy(update={'targets': (plan.measures[0].id,), 'state': 'applied'})
                if separate.intersection(o.spans) else o for o in plan.obligations)})
        assert has_construct(plan, case.construct, vocab), case.construct
        checked = check_plan(plan, vocab, case.question)
        model = isinstance(checked, CheckedPlan)
        layer = refusing_layer(result, checked, rules, model, planning.get(case.question, {}), replies.get(case.question))
        actual = ('yes' if rules else 'no', 'yes' if model else 'no', layer)
        observed.append((case.construct, case.question, *actual))
        if actual != (case.rules, case.model, case.refusing_layer):
            failures.append((case.construct, actual, (case.rules, case.model, case.refusing_layer),
                             result.get('reason'), getattr(checked, 'missing', ())))

    print('\n| construct | question | rules | model | refusing layer |')
    print('| --- | --- | --- | --- | --- |')
    for row in observed:
        print('| ' + ' | '.join(row) + ' |')
    for index, label in ((2, 'rules'), (3, 'model')):
        print(f'Reached by {label}: {sum(row[index] == "yes" for row in observed)} of {len(MATRIX)}')
    for failure in failures:
        print(failure)
    assert not failures, failures
