import csv
from collections import defaultdict
from decimal import Decimal
from types import SimpleNamespace

import pytest
import yaml

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.pipeline.settings import ROOT
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.planmodel import read
from kge.services.ask.engine.planrules import path_to, vocabulary
from kge.services.ask.engine.planner import Planner, execute
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name

PACK = ROOT / 'clients/meridian'


@pytest.fixture(scope='module')
def vocab(eng):
    return vocabulary(eng, 'analyst_enterprise', 'analysis', SimpleNamespace(products=[]), PRESETS['graph'])


def measure(v, context='nna', identifier='m', filters=(), mode='current'):
    return {'kind': 'measure', 'id': identifier, 'measure': name(v.contexts[context].measure),
            'context': name(context), 'mode': mode, 'filters': list(filters)}


def dimension(v, column, context='nna'):
    path = path_to(v.contexts[context], column.rsplit('.', 1)[0])
    assert path is not None
    return {'kind': 'column', 'id': 'd', 'bindings': [{'context': name(context), 'column': name(column),
            'path': [name(j) for j in path], 'level': None}]}


def predicate(v, column, value, context='nna', identifier='f'):
    path = path_to(v.contexts[context], column.rsplit('.', 1)[0])
    assert path is not None
    return {'kind': 'compare', 'id': identifier, 'left': {'kind': 'column', 'name': name(column),
            'path': [name(j) for j in path]}, 'op': 'eq', 'value': {'kind': 'text', 'value': value}, 'evidence': []}


def run(eng, v, question, context='nna', dimensions=(), filters=(), measures=None, derived=(), assignments=None,
        mode='current', month='2025-06'):
    req = parse(question, eng.term_names, eng)
    v = vocabulary(eng, 'analyst_enterprise', 'analysis', SimpleNamespace(products=[]), PRESETS['graph'], req)
    plan = model(measures=measures or [measure(v, context, mode=mode)], dimensions=list(dimensions), filters=list(filters),
                 derived=list(derived), time={'kind': 'single', 'period': {'kind': 'month', 'value': month, 'calendar': name('calendar')}})
    p = grounded(plan, question, v, assignments or {piece.text: 'time' if piece.kind == 'time' else
        'f' if piece.kind == 'predicate' else 'd' if any(c in v.entries and v.entries[c].kind == 'column' for c in piece.candidates)
        and dimensions else 'f' if any(c in v.entries and v.entries[c].kind == 'value' for c in piece.candidates) else 'm'
        for piece in ledger(question, 'turn', v)})

    class Scripted:
        def json(self, prompt):
            return {'kind': 'propose', 'base_turn': None, 'plan': p.model_dump(mode='json', by_alias=True)}

    reply = read(Scripted(), question, v)
    assert reply.kind == 'propose', reply
    checked = check_plan(reply.plan, v, question, 'turn')
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    if compiled.release is not None:
        check_reference_and_runtime(compiled.release)
        guard, release_rows = execute(eng.w, compiled.release)
        assert guard['passed']
        compiled = compile_plan(checked, release_rows)
    check_reference_and_runtime(compiled.answer)
    result = Planner(v, eng.w, Hidden([], set(), [])).answer(question, reply)
    assert result['kind'] == 'result', result
    assert result['guard']['passed']
    return result


def check_reference_and_runtime(statement):
    assert check(statement.sql, statement.allow) == []
    manager = RuntimeManager(SimpleNamespace(units=['wealth'], curated=[]))
    schema = statement.allow.schema
    manager.ref_table_units = dict.fromkeys((t.lower() for t in schema), 'wealth')
    manager.ref_columns = {(t.lower(), c.lower()): 'VARCHAR' for t, cols in schema.items() for c in cols}
    manager.rt_tables = {(runtime, table.lower()): {'table': physical(table)}
                         for runtime, physical in (('identity', lambda table: table), ('live', lambda table: 'runtime.APP_' + table))
                         for table in schema}
    manager.rt_columns = {(runtime, table.lower()): {column.lower(): {'column': column} for column in columns}
                          for runtime in ('identity', 'live') for table, columns in schema.items()}
    for dialect in ('duckdb', 'bigquery'):
        manager.runtimes = {runtime: {'dialect': dialect} for runtime in ('identity', 'live')}
        for runtime in manager.runtimes:
            sql, allow, _ = manager._rename_sql_for_runtime(statement.sql, 'wealth', runtime, statement.allow)
            native = manager._render_for_dialect(sql, dialect)
            assert check(native, allow, dialect=dialect) == []


def rows(result):
    return {tuple(d['value'] for d in row['dimensions']): tuple(Decimal(c['value']['value']) for c in row['cells'])
            for row in result['result']['rows'] if not row.get('total')}


FILTERS = [
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID4', 'S3200', 'by segment level 4', 'segment level 4 WLTH'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID5', 'S4100', 'by segment level 5', 'segment level 5 AFFLUENT'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', 'S5100', 'by segment level 6', 'segment level 6 CORE'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM4', 'Wealth Management', 'by segment level 4 name', 'segment level 4 name Wealth Management'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM5', 'Affluent', 'by segment level 5 name', 'segment level 5 name Affluent'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6', 'Core Affluent', 'by segment level 6 name', 'segment level 6 name Core Affluent'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID6', 'S5100', 'by client segment', 'client segment CORE'),
    ('CLIENT_CD_ROLLUP_1.SEGMT_LVL_NM6', 'Core Affluent', 'by client segment name', 'client segment name Core Affluent'),
    ('BRCH_DIM_1.BRCH_CD', '5H', 'by branch', 'branch BOS'),
    ('BRCH_DIM_1.BRCH_NM', 'BOSTON BACK BAY', 'by branch name', 'BOSTON BACK BAY'),
    ('BRCH_DIM_1.RGN_CD', 'NER', 'by region', 'region PACIFIC'),
    ('BRCH_DIM_1.RGN_NM', 'PACIFIC REGION', 'by region name', 'region name Pacific'),
    ('TRAN_FACT_1.DW_TRAN_DIRCTN_CD', 'I', 'by flow direction', 'inflows'),
    ('TRAN_FACT_1.DW_TRAN_DIRCTN_CD', 'O', 'by flow direction', 'outflows'),
]


@pytest.mark.parametrize('column,value,group,phrase', FILTERS)
def test_member_filter_reconciles_to_unfiltered_breakdown(eng, vocab, column, value, group, phrase):
    column = 'wealth.' + column
    breakdown = run(eng, vocab, 'NNA for June 2025 ' + group, dimensions=[dimension(vocab, column)])
    filtered = run(eng, vocab, 'NNA for June 2025 ' + phrase, filters=[predicate(vocab, column, value)])
    assert rows(filtered)[()][0] == rows(breakdown)[(value,)][0]


@pytest.mark.parametrize('context,phrase', [('nna_certified', 'Certified NNA'), ('nna_branch_sales', 'Branch Sales NNA')])
def test_new_context_reconciles_branch_filter_and_total(eng, vocab, context, phrase):
    column = 'wealth.BRCH_DIM_1.BRCH_CD'
    breakdown = run(eng, vocab, phrase + ' for June 2025 by branch', context, dimensions=[dimension(vocab, column, context)])
    total = run(eng, vocab, phrase + ' for June 2025', context)
    filtered = run(eng, vocab, phrase + ' for June 2025 branch BOS', context,
                   filters=[predicate(vocab, column, '5H', context)])
    assert rows(filtered)[()][0] == rows(breakdown)[('5H',)][0]
    assert rows(total)[()][0] == sum(v[0] for v in rows(breakdown).values())
    (total_row,) = [row for row in breakdown['result']['rows'] if row.get('total')]
    assert Decimal(total_row['cells'][0]['value']['value']) == rows(total)[()][0]
    assert len(total['sql_run']) == 2


def test_finance_uses_its_golden_signed_amount_without_resigning(eng, vocab):
    """Aligned to the golden SQL: certified reads F_NNA_FIN with no frozen filter."""
    answer = run(eng, vocab, 'Certified NNA for June 2025', 'nna_certified')
    from tests.integration.test_plan_wording_round2 import sql
    expected = sql(eng, "SELECT SUM(NNA_AM) FROM F_NNA_FIN WHERE DW_TRAN_MNTH_END_DT = DATE '2025-06-30'")[0][0]
    assert rows(answer)[()][0] == expected
    assert 'TRAN_FACT_1' not in answer['sql']


def test_branch_sales_as_reported_uses_the_months_release_date(eng, vocab):
    answer = run(eng, vocab, 'Branch Sales NNA for March 2025 as reported', 'nna_branch_sales',
                 mode='as_reported', month='2025-03')
    assert 'TRAN_FACT_EXCL_MFCLR_G.DW_EFF_DT' in answer['sql']
    assert 'MONTH_DIM_1.FIN_MTRC_RLSE_DT' in answer['sql']
    from tests.integration.test_plan_vocabulary_round3 import BRANCH_SALES, SIGNED
    from tests.integration.test_plan_wording_round2 import sql
    expected = sql(eng, 'SELECT SUM(' + SIGNED + ') ' + BRANCH_SALES
                   + " AND f.MNTH_END_DT=DATE '2025-03-31'")[0][0]
    assert rows(answer)[()][0] == expected


def test_inflow_and_outflow_outputs_derive_ratio_and_reconcile_net(eng, vocab):
    measures = [measure(vocab, 'flow_amount_inflows', 'i'), measure(vocab, 'flow_amount_outflows', 'o')]
    question = 'Flow amount ratio of inflows to outflows for June 2025'
    assignments = {'Flow amount ratio of inflows to outflows': ['r', 'i', 'o'], 'June 2025': 'time'}
    answer = run(eng, vocab, question, measures=measures, derived=[{'kind': 'ratio', 'id': 'r', 'numerator': 'i',
        'denominator': 'o', 'declaration': name('flow-amount-inflow-outflow-ratio'), 'evidence': []}], assignments=assignments)
    assert not any(column['kind'] == 'dimension' for column in answer['result']['columns'])
    inflow, outflow, ratio = rows(answer)[()]
    net = run(eng, vocab, 'NNA for June 2025')
    assert inflow > 0 and outflow < 0
    assert inflow + outflow == rows(net)[()][0]
    assert ratio == pytest.approx(inflow / abs(outflow))
    assert len(answer['sql_run']) == len(net['sql_run']) == 2


@pytest.mark.parametrize('context,phrase', [('nna', 'NNA'), ('nna_certified', 'Certified NNA')])
def test_account_relationship_split_with_share_of_total(eng, vocab, context, phrase):
    column = 'wealth.ACCT_DIM2_1.ACCT_RELT_SPS_IN'
    question = phrase + ' for June 2025 by account relationship share of total'
    answer = run(eng, vocab, question, context, dimensions=[dimension(vocab, column, context)],
                 derived=[{'kind': 'share', 'id': 's', 'input': 'm', 'remove_dimensions': ['d'], 'remove_filters': [], 'evidence': []}],
                 assignments={phrase: 'm', 'June 2025': 'time', 'by account relationship': 'd', 'share': 's'})
    total = rows(run(eng, vocab, phrase + ' for June 2025', context))[()][0]
    result = rows(answer)
    assert set(result) == {('Y',), ('N',)}
    assert sum(row[0] for row in result.values()) == total
    assert sum(row[1] for row in result.values()) == pytest.approx(Decimal(1))
    for amount, share in result.values():
        assert share == pytest.approx(amount / total)


@pytest.mark.parametrize('context,phrase', [('nna', 'NNA'), ('nna_certified', 'Certified NNA')])
@pytest.mark.parametrize('column,group', [('DATE_DIM_1.DAY_OF_WEEK', 'by weekday'), ('SRCE_SYS_1.SRCE_SYS_NM', 'by source system name')])
def test_date_and_source_name_joins_reconcile_total(eng, vocab, context, phrase, column, group):
    answer = run(eng, vocab, phrase + ' for June 2025 ' + group, context,
                 dimensions=[dimension(vocab, 'wealth.' + column, context)])
    total = run(eng, vocab, phrase + ' for June 2025', context)
    assert sum(v[0] for v in rows(answer).values()) == rows(total)[()][0]
    assert len(rows(answer)) > 1


def test_declared_member_values_are_only_meridian_fixture_values():
    pack = yaml.safe_load((PACK / 'pack.yaml').read_text())
    for entry in pack['terminology']['value_vocabularies']:
        unit, table, column = entry['column'].split('.')
        if unit != 'wealth' or column == 'RISK_PRFL_CD':
            continue
        with (PACK / 'fixture/data' / unit / (table + '.csv')).open() as f:
            allowed = {r[column] for r in csv.DictReader(f)}
        assert set(entry['values']) <= allowed
    relationship_versions = defaultdict(set)
    historical_counterexamples = []
    with (PACK / 'fixture/data/wealth/ACCT_DIM2_1.csv').open() as f:
        for row in csv.DictReader(f):
            key = row['DW_ACCT_ID'], row['SRCE_SYS_ID']
            relationship_versions[key].add(row['ACCT_RELT_SPS_IN'])
            if key == ('8800001', '100'):
                historical_counterexamples.append((row['DW_EFF_DT'], row['DW_EXPR_DT'], row['CURR_IN'], row['ACCT_RELT_SPS_IN']))
            else:
                assert row['ACCT_RELT_SPS_IN'] == ('Y' if sum(map(int, key)) % 3 == 0 else 'N')
    assert sorted(historical_counterexamples) == [('1900-01-01', '1900-12-31', '0', 'N'),
                                                 ('2000-01-01', '2000-12-31', '0', 'N')]
    assert {value for values in relationship_versions.values() for value in values} == {'Y', 'N'}
    assert all(len(values) == 1 for values in relationship_versions.values())


def test_new_contexts_and_values_do_not_expand_a_retail_callers_scope(eng):
    scoped = vocabulary(eng, 'analyst_retail', 'analysis', SimpleNamespace(products=[]), PRESETS['graph'])
    assert not {'nna_certified', 'nna_branch_sales', 'flow_amount'} & set(scoped.contexts)
    assert not any(e.unit == 'wealth' for e in scoped.entries.values())


@pytest.mark.parametrize('configured', [False, True])
@pytest.mark.parametrize('question,principal,key,hidden', [
    ('Show household names', 'advisor_wealth', 'wealth.HSHLD_DIM_1.HSHLD_DK', 'retail.customer'),
    ('Show Wealth client names', 'advisor_wealth', 'wealth.HSHLD_DIM_1.HSHLD_DK', 'retail.customer'),
    ('Show client names', 'advisor_wealth', 'wealth.HSHLD_DIM_1.HSHLD_DK', 'retail.customer'),
    ('Show client names', 'analyst_retail', 'retail.customer.customer_id', 'wealth.HSHLD_DIM_1'),
    ('Show client names', 'analyst_enterprise', 'wealth.HSHLD_DIM_1.HSHLD_DK', 'corporate.contact'),
])
def test_private_names_offer_only_readable_keys_without_native_data_jobs(monkeypatch, tmp_path, configured, question, principal, key, hidden):
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.wording import template
    from tests.integration.test_planner_request_jobs import pilot, budgets
    class NoCalls:
        name = 'mock'
        def json(self, *args, **kwargs):
            raise AssertionError('A private read must use the refusal template.')
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        for cold in (True, False):
            fake.requests.clear()
            answer = Combined(engine, llm=NoCalls() if configured else None).ask(question, principal)
            assert answer['behaviour'] == 'refuse'
            assert answer.get('figure') is None and not answer.get('figures') and not answer.get('result')
            assert 'POL-DATA-CLASS' in answer['reason'] and key in answer['reason']
            assert key in template(answer, engine) and 'POL-DATA-CLASS' in template(answer, engine)
            assert hidden not in answer['reason'] and hidden not in template(answer, engine)
            jobs = budgets(fake)
            expected_graph = 2 if cold and question == 'Show household names' else 0
            assert jobs == dict(graph=expected_graph, warehouse=0, dry=0, retrieval=0, catalogue=0), (cold, jobs)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
