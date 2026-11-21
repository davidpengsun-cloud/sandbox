"""Optional measure declarations reconcile both Ask paths with independent, gated SQL.

Certified references are aligned to the golden SQL: certified reads F_NNA_FIN with no frozen filter.
"""
from copy import copy, deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kge.compiler.checks import derived_measures, measures
from kge.compiler.pack import read
from kge.compiler.vocabulary import AGGREGATION_CHOICES, measure_entries
from kge.pipeline.sqlguard import DataAllowlist, schema_allowlist
from kge.ports import AUTHORED
from kge.semantics.coverage import ground_plan
from kge.semantics.plan import Propose, QueryPlan
from kge.semantics.query import Asked, Plan
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine.wording import Hidden
from tests.integration.test_context_scope import vocabulary
from tests.integration.test_plan_wording_round2 import Recording
from kge.services.ask.engine import planmodel


WHO = 'analyst_enterprise'
DECLARATIONS = {
    'ratio': ('flow-quotient', 'Flow quotient'),
    'difference': ('flow-spread', 'Flow spread'),
    'percent_change': ('flow-growth', 'Flow growth'),
}
WORDS = {
    'sum': ('sum',), 'count': ('count',), 'avg': ('average', 'mean'),
    'min': ('minimum', 'lowest', 'smallest'), 'max': ('maximum', 'largest', 'highest'), 'median': ('median',),
}
FLOW = "f.TRAN_AM"
CURRENT = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.DW_TRAN_DT>=DATE '2025-06-01' AND f.DW_TRAN_DT<DATE '2025-07-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1"""
CERTIFIED = """FROM F_NNA_FIN f JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.DW_TRAN_MNTH_END_DT>=DATE '2025-06-01' AND f.DW_TRAN_MNTH_END_DT<DATE '2025-07-01'"""


def declared_engine(eng):
    """Use a pack copy and independent vocabulary cache; leave the served engine and pack untouched."""
    pack = deepcopy(read(eng.profile.clients, eng.profile.client))
    pack.data['contexts'] = [c for c in pack.data['contexts'] if c['id'] != 'nna_client_directed']
    current = eng.semantics.contexts['nna'].measure
    certified = eng.semantics.contexts['nna_certified'].measure
    for measure in pack.data['measures']:
        if measure['id'] == current:
            measure['aggregations'] = list(AGGREGATION_CHOICES)
    pack.data['derived_measures'] = []
    for kind, (identifier, label) in DECLARATIONS.items():
        glossary = {**deepcopy(pack.data['glossary'][0]), 'id': identifier, 'name': label,
                    'definition': 'Arithmetic over the two declared flow measures.'}
        pack.data['glossary'].append(glossary)
        pack.data['derived_measures'].append(dict(id=identifier, term=identifier,
            formula=dict(kind=kind, numerator=current, denominator=certified)))
    assert measures(pack) == [] and derived_measures(pack) == []
    scratch = copy(eng)
    scratch.term_names = [*eng.term_names, *(label for _, label in DECLARATIONS.values())]
    scratch.g = copy(eng.g)
    scratch.g._scoped_vocabularies = {}
    scratch.VOCABULARY = [*eng.VOCABULARY, *measure_entries(pack.data)]
    scratch._semantics = copy(eng.semantics)
    scratch._semantics.d = SimpleNamespace(**{k: v for k, v in vars(scratch).items() if k.isupper()})
    scratch._semantics.contexts = {k: v for k, v in eng.semantics.contexts.items() if k != 'nna_client_directed'}
    scratch._semantics.plan_snapshot = scratch._semantics.baseline_snapshot = None
    return scratch


def independent(eng, statement):
    """Reference queries obtain table access from the ordinary schema gate before execution."""
    allow = DataAllowlist(**{**vars(schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS)), 'checked_cte_joins': True})
    for identifier in ('nna', 'nna_certified'):
        context = eng.semantics.contexts[identifier]
        scoped = eng.semantics.allowlist(Plan(eng.semantics, context, Asked()), data_read=True)
        for field in ('joins', 'predicates', 'declared', 'links'):
            getattr(allow, field).update(getattr(scoped, field))
        allow.composite_joins.update(scoped.composite_joins)
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


def decimals(result):
    return {c['id']: Decimal(str(cell['value']['value']))
            for c, cell in zip([c for c in result['columns'] if c['kind'] != 'dimension'], result['rows'][0]['cells'])}


def values_by_dimension(result):
    outputs = [c['id'] for c in result['columns'] if c['kind'] != 'dimension']
    return {tuple(d.get('value') for d in row['dimensions']):
            {identifier: Decimal(str(cell['value']['value'])) if cell['value']['kind'] != 'null' else None
             for identifier, cell in zip(outputs, row['cells'])}
            for row in result['rows'] if not row.get('total')}


def rules_and_replay(eng, question, grouped=False):
    """The model path replays the interpreted plan with newly grounded question evidence."""
    answer = eng.answer(question, WHO)
    assert answer['behaviour'] in ('figure', 'combined'), answer.get('reason')
    assert answer['guard']['passed'], answer['guard']
    raw = answer['history'][-1]['plan']
    plan = QueryPlan.model_validate(raw)
    vocab = vocabulary(eng, question)
    clean = plan.model_dump(mode='json', by_alias=True)
    clean['obligations'] = []
    def strip(value):
        if isinstance(value, dict):
            return {k: [] if k == 'evidence' else strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    grounded = ground_plan(QueryPlan.model_validate(strip(clean)), question, 'turn', vocab)
    reply = planmodel.read(Recording(Propose(kind='propose', base_turn=None, plan=grounded)), question, vocab)
    assert reply.kind == 'propose', reply
    replay = Planner(vocab, eng.w, Hidden([], set(), [])).answer(question, reply)
    assert replay['kind'] == 'result' and replay['guard']['passed'], replay
    assert values_by_dimension(answer['result']) == values_by_dimension(replay['result'])
    return plan, values_by_dimension(answer['result']) if grouped else decimals(answer['result'])


@pytest.mark.parametrize('aggregation,word', [(aggregation, word) for aggregation, words in WORDS.items() for word in words])
def test_declared_aggregation_words_reach_both_paths_and_match_independent_sql(eng, aggregation, word):
    scratch = declared_engine(eng)
    plan, values = rules_and_replay(scratch, f'{word} NNA for June 2025')
    output, = plan.measures
    assert (output.aggregation or scratch.semantics.contexts[output.context.id].aggregation) == aggregation
    operand = f'cast({FLOW} AS DOUBLE)' if aggregation == 'median' else FLOW
    expected, = independent(scratch, f'SELECT {aggregation}({operand}) {CURRENT}')[0]
    assert values[output.id] == pytest.approx(Decimal(str(expected)))


@pytest.mark.parametrize('kind', DECLARATIONS)
def test_declared_formula_reaches_both_paths_and_matches_independent_sql(eng, kind):
    scratch = declared_engine(eng)
    identifier, label = DECLARATIONS[kind]
    plan, values = rules_and_replay(scratch, f'{label} for June 2025')
    node, = plan.derived
    assert node.kind == kind and node.declaration.id == identifier
    answer = scratch.answer(f'{label} for June 2025', WHO)
    column, = [column for column in answer['result']['columns'] if column['id'] == node.id]
    assert column['label'] == label
    assert identifier in column['lineage']
    expressions = {'ratio': 'n / nullif(abs(d),0)', 'difference': 'n-d',
                   'percent_change': '(n-d) / nullif(abs(d),0)'}
    expected, = independent(scratch, f'''WITH operands AS (
SELECT sum({FLOW}) AS n, NULL AS d {CURRENT}
UNION ALL SELECT NULL AS n, sum(f.NNA_AM) AS d {CERTIFIED}),
totals AS (SELECT sum(n) AS n, sum(d) AS d FROM operands)
SELECT {expressions[kind]} FROM totals''')[0]
    assert values[node.id] == pytest.approx(Decimal(str(expected)))


def test_undeclared_aggregation_is_refused_and_original_pack_stays_unchanged(eng):
    vocabulary_before = deepcopy(eng.VOCABULARY)
    pack_path = eng.profile.clients / eng.profile.client / 'pack.yaml'
    pack_before = pack_path.read_bytes()
    scratch = declared_engine(eng)
    assert scratch.answer('average NNA for June 2025', WHO)['behaviour'] == 'figure'
    answer = eng.answer('average NNA for June 2025', WHO)
    assert answer['behaviour'] == 'abstain'
    assert 'average' in answer.get('reason', '').casefold() or 'aggregation' in answer.get('reason', '').casefold()
    assert eng.VOCABULARY == vocabulary_before
    assert pack_path.read_bytes() == pack_before


@pytest.mark.parametrize('kind', DECLARATIONS)
def test_declared_formula_applies_the_breakdown_and_filter_to_both_operands(eng, kind):
    scratch = declared_engine(eng)
    identifier, label = DECLARATIONS[kind]
    question = f'{label} by branch for Northeast region for June 2025'
    plan, values = rules_and_replay(scratch, question, grouped=True)
    node, = plan.derived
    assert node.declaration.id == identifier
    raw_join = '''JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0'''
    frozen_join = '''JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0'''
    current = CURRENT.replace('WHERE m.', raw_join + '\nWHERE m.', 1)
    certified = CERTIFIED.replace('WHERE m.', frozen_join + '\nWHERE m.', 1)
    expression = {'ratio': 'n / nullif(abs(d),0)', 'difference': 'n-d',
                  'percent_change': '(n-d) / nullif(abs(d),0)'}[kind]
    expected = independent(scratch, f'''WITH operands AS (
SELECT b.BRCH_CD AS branch, sum({FLOW}) AS n, NULL AS d {current}
AND b.RGN_CD='CEO' GROUP BY b.BRCH_CD
UNION ALL SELECT b.BRCH_CD AS branch, NULL AS n, sum(f.NNA_AM) AS d {certified}
AND b.RGN_CD='CEO' GROUP BY b.BRCH_CD),
totals AS (SELECT branch, sum(n) AS n, sum(d) AS d FROM operands GROUP BY branch)
SELECT branch,n,d,{expression} FROM totals''')
    assert expected and len(values) == len(expected)
    for branch, numerator, denominator, calculated in expected:
        row = values[(branch,)]
        for output, amount in ((node.numerator, numerator), (node.denominator, denominator), (node.id, calculated)):
            assert row[output] == pytest.approx(Decimal(str(amount))) if amount is not None else row[output] is None
