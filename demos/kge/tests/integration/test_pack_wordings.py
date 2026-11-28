"""Pack-declared wordings and groups retain independently checked sample figures."""
from decimal import Decimal

import pytest
import yaml

from kge.pipeline.settings import ROOT
from kge.pipeline.sqlguard import DataAllowlist, schema_allowlist
from kge.ports import AUTHORED
from kge.semantics.coverage import ledger
from kge.semantics.query import Asked
from kge.services.ask.engine.question import Period
from tests.integration.test_context_scope import vocabulary
from tests.unit.test_wording_concepts import without_evidence


PACK = yaml.safe_load((ROOT / 'clients/meridian/pack.yaml').read_text())
CONCEPTS = PACK['terminology']['concepts']
WHO = 'analyst_enterprise'


def reference(eng, statement):
    """Reference SQL is independently authored and admitted through the ordinary data gate."""
    allow = DataAllowlist(**{**vars(schema_allowlist(eng.g, 'wealth', eng.RESTRICTED_COLUMNS)), 'checked_cte_joins': True})
    for identifier in ('nna', 'nna_certified', 'nna_client_directed', 'nna_branch_sales'):
        scoped = eng.semantics.allowlist(eng.semantics.plan(eng.semantics.contexts[identifier],
            Asked(period=Period('month', '2025-03-01'), time_mode='as_reported')), data_read=True)
        for field in ('joins', 'predicates', 'declared', 'links'):
            getattr(allow, field).update(getattr(scoped, field))
        allow.composite_joins.update(scoped.composite_joins)
    guard = eng.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return eng.w.rows('wealth', statement, AUTHORED)


def figure(answer):
    assert answer['behaviour'] == 'figure', answer.get('reason')
    assert answer['guard']['passed'], answer['guard']
    return Decimal(answer['result']['rows'][0]['cells'][0]['value']['value'])


def march_reference(eng, mode):
    version = ("f.DW_EFF_DT <= m.FIN_MTRC_RLSE_DT AND (f.DW_EXPR_DT IS NULL OR f.DW_EXPR_DT >= m.FIN_MTRC_RLSE_DT)"
               if mode == 'as_reported' else 'f.CURR_IN = 1')
    sql = """SELECT ROUND(SUM(f.TRAN_AM),2)
FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE f.MNTH_END_DT=DATE '2025-03-31' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND """ + version
    return Decimal(str(reference(eng, sql)[0][0]))


def certified_reference(eng):
    expected = Decimal(str(reference(eng, """SELECT SUM(f.NNA_AM) FROM F_NNA_FIN f
JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-03-31' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0""")[0][0]))
    assert expected == Decimal('5929714.83')
    return expected


@pytest.mark.parametrize('concept', CONCEPTS, ids=lambda c: c['id'])
def test_every_declared_concept_wording_has_one_plan_and_one_figure(eng, concept):
    """The test cases come from the pack, including any later added wording. A concept that qualifies NNA names its
    qualified metric wherever its wording sits, after NNA as before it: certified NNA reads F_NNA_FIN (requirement 19)."""
    assert 'time_mode' in concept, 'add the shared question for another declared concept target'
    qualified = [q['term'] for q in concept.get('qualified_terms', ()) if q['noun'] == 'NNA']
    if qualified:
        assert qualified == ['certified-nna'], 'add the shared question for another qualified term'
        expected = certified_reference(eng)
    else:
        expected = march_reference(eng, concept['time_mode'])
        assert expected == Decimal('9187481.28' if concept['time_mode'] == 'as_reported' else '9479714.83')
    answers = []
    for wording in concept['wordings']:
        for text in ('NNA for March 2025 ' + wording, 'NNA, ' + wording + ', for March 2025'):
            answer = eng.answer(text, WHO)
            assert figure(answer) == expected, text
            plan = without_evidence(answer['history'][-1]['plan'])
            answers.append((plan, answer['sql'], figure(answer)))
    if qualified:
        canonical = eng.answer('Certified NNA for March 2025', WHO)
        answers.append((without_evidence(canonical['history'][-1]['plan']), canonical['sql'], figure(canonical)))
    assert all(answer == answers[0] for answer in answers)
    if concept['time_mode'] == 'restated':
        assert figure(eng.answer('NNA for March 2025', WHO)) == expected


@pytest.mark.parametrize('concept', [c for c in CONCEPTS if c.get('time_mode') == 'as_reported'], ids=lambda c: c['id'])
@pytest.mark.parametrize('metric', ['finance NNA', 'client-directed NNA'])
def test_reporting_basis_wordings_share_the_golden_finance_plan_and_figure(eng, concept, metric):
    """Aligned to the golden SQL: certified reads F_NNA_FIN with no frozen filter."""
    directed = metric.startswith('client-directed')
    source = """FROM F_NNA_FIN f JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-03-31' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0"""
    if directed:
        source = """FROM F_NNA_FIN f
JOIN MONTH_DIM_1 m ON f.DW_TRAN_MNTH_END_DT=m.CLDR_EOM_DT
JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID
AND f.DW_TRAN_MNTH_END_DT BETWEEN a.DW_EFF_DT AND a.DW_EXPR_DT
JOIN CLIENT_CD_ROLLUP_1 r ON a.CLNT_CD=r.CLNT_CD
AND f.DW_TRAN_MNTH_END_DT BETWEEN r.DW_EFF_DT AND r.DW_EXPR_DT
WHERE f.DW_TRAN_MNTH_END_DT=DATE '2025-03-31' AND m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0
AND f.CLIENT_DIRECTED_NNA_IN='Y' AND r.SEGMT_LVL_ID5='S4100'"""
    expected = Decimal(str(reference(eng, 'SELECT SUM(f.NNA_AM) ' + source)[0][0]))
    if directed:
        assert expected == Decimal('3308619.19')
    answers = []
    for wording in concept['wordings']:
        question = metric + (' for AFFLUENT March 2025 ' if directed else ' for March 2025 ') + wording
        answer = eng.answer(question, WHO)
        assert figure(answer) == expected, question
        assert 'FRZN_IN' not in answer['sql'], answer['sql']
        assert 'F_NNA_FIN' in answer['sql'], answer['sql']
        plan = without_evidence(answer['history'][-1]['plan'])
        answers.append((plan, answer['sql'], figure(answer)))
    assert all(answer == answers[0] for answer in answers)


@pytest.mark.parametrize('concept', [c for c in CONCEPTS if c.get('qualified_terms')], ids=lambda c: c['id'])
@pytest.mark.parametrize('template', ['{wording} {noun} for March 2025', '{wording} March 2025 {noun}',
                                      '{noun} for March 2025 {wording}', '{noun}, {wording}, for March 2025'])
def test_every_qualified_wording_names_the_same_declared_metric(eng, concept, template):
    """A generated prefix name cannot silently select another metric's population, and its wording said apart from
    the noun, after it, names the same metric."""
    expected = certified_reference(eng)
    for qualified in concept['qualified_terms']:
        answers = []
        for wording in concept['wordings']:
            text = template.format(wording=wording, noun=qualified['noun'])
            answer = eng.answer(text, WHO)
            assert figure(answer) == expected, text
            plan = without_evidence(answer['history'][-1]['plan'])
            assert {m['context']['id'] for m in plan['measures']} == {'nna_certified'}
            assert {m['mode'] for m in plan['measures']} == {'current'}
            assert 'FRZN_IN' not in answer['sql']
            answers.append((plan, answer['sql'], figure(answer)))
        assert all(answer == answers[0] for answer in answers)


def test_golden_business_wordings_are_onboarded_without_unknown_words(eng):
    from kge.eval import sets
    items = [i for i in sets.items(ROOT / 'build/sage_v1.json') if i.get('category', '').startswith('Golden 20')]
    unknown = {i['id']: [p.text for p in ledger(i['question'], 'audit', vocabulary(eng, i['question'])) if p.kind == 'unknown']
               for i in items}
    assert len(items) == 112
    assert not {identifier: words for identifier, words in unknown.items() if words}
    ranking = next(i for i in items if i['id'] == 'S21_alt3')
    assert any(p.operation == 'desc' for p in ledger(ranking['question'], 'audit', vocabulary(eng, ranking['question'])))


def test_inflow_verbs_do_not_select_another_metrics_population(eng):
    answers = []
    for wording in ('flowed in', 'came in', 'brought in'):
        question = 'NNA for March 2025 ' + wording
        answer = eng.answer(question, WHO)
        assert figure(answer)
        plan = without_evidence(answer['history'][-1]['plan'])
        assert {m['context']['id'] for m in plan['measures']} == {'nna'}
        answers.append((plan, answer['sql'], figure(answer)))
    assert all(answer == answers[0] for answer in answers)


def test_a_reference_wording_does_not_rename_the_finance_amount_column(eng):
    answer = eng.answer('Client-directed NNA for June 2025', WHO)
    assert figure(answer) == Decimal('5232475.35')
    assert answer['column'] == 'F_NNA_FIN.NNA_AM'
