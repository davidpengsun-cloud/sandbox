"""Owner-preserved answers and the seven step-six regression boundaries."""
import json

import pytest

from kge.eval.run_golden import score
from kge.services.ask.engine import planrules
from kge.services.ask.engine.combined import Combined
from kge.services.ask.engine.core import PRESETS, parse
from kge.services.ask.engine.config import AGENT_ARMS
from kge.services.ask.engine.scope import ProductScope
from tests.scratch import DEMO


PRESERVED = {'G004', 'G005', 'G006', 'G007', 'G010', 'G011', 'G012', 'G015', 'G016', 'G019', 'H006', 'W18', 'XB3', 'S01', 'S02'}


def cases():
    for filename in ('golden', 'heldout', 'golden_wealth_v1', 'sage_v1'):
        data = json.loads((DEMO / 'clients/meridian/fixture' / (filename + '.json')).read_text())
        for item in data['items'] if isinstance(data, dict) else data:
            if item['id'] in PRESERVED:
                yield pytest.param(item, id=item['id'])


@pytest.mark.parametrize('item', list(cases()))
@pytest.mark.parametrize('arm', ['graph', 'treatment', 'ask'])
def test_owner_preserved_answers(eng, item, arm):
    answer = (Combined(eng, llm=None).ask(item['question'], 'analyst_enterprise') if arm == 'ask'
              else eng.answer(item['question'], 'analyst_enterprise', arm_name=arm) if arm == 'graph'
              else eng.answer(item['question'], 'analyst_enterprise', config=AGENT_ARMS['treatment']))
    ok, why = score(item, answer)
    assert ok, why


@pytest.mark.parametrize('arm', ['schema', 'catalog', 'control'])
def test_baseline_arms_do_not_gain_declared_dimension_paths(eng, arm):
    cfg = PRESETS.get(arm) or AGENT_ARMS[arm]
    question = 'Retail revenue for June 2025 in region NW'
    req = parse(question, eng.term_names if cfg.glossary else [], eng, aliases=cfg.resolution != 'words')
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), cfg, req)
    assert all(not c.joins for c in vocab.contexts.values())
    assert not vocab.no_join
    answer = eng.answer(question, 'analyst_enterprise', config=cfg)
    assert answer['behaviour'] == 'abstain' and not answer.get('figure')


def test_null_parts_have_reason_status(eng):
    answer = eng.answer('Retail and SME revenue for June 2090', 'analyst_enterprise')
    assert answer['behaviour'] == 'parts_with_reason'
    assert all(p['status'] == 'abstain' and p['reason'] for p in answer['plan'])


@pytest.mark.parametrize('arm', ['schema', 'catalog', 'control'])
def test_baseline_resolution_trace_names_only_readable_implementations(eng, arm):
    cfg = PRESETS.get(arm) or AGENT_ARMS[arm]
    answer = eng.answer('Retail revenue for June 2025', 'analyst_retail', config=cfg)
    implementations = [i for step in answer['trace'] if step['step'] == 'map' for i in step['implementations']]
    assert all(i.startswith('retail.') for i in implementations)


def test_cohort_correlation_requires_declared_relationships_despite_harvested_keys(eng):
    import copy
    question = "How many active customers in Retail in June 2025 raised a complaint that month, and what were their average balance and fee income?"
    req = parse(question, eng.term_names, eng)
    sem = planrules.snapshot(eng)
    assert planrules.cohort_snapshot(eng, sem, req) is not sem
    undeclared = copy.copy(sem)
    undeclared.declared = {key: join for key, join in sem.declared.items()
                          if 'retail.complaint' not in (join.source, join.target)}
    assert planrules.cohort_snapshot(eng, undeclared, req) is undeclared


def test_unapplied_declared_filter_keeps_the_existing_context_reason(eng):
    answer = eng.answer('How many accounts were excluded from NNA at 31 Mar 2025?', 'analyst_enterprise')
    assert answer['behaviour'] == 'abstain' and answer.get('figure') is None
    assert 'applies no condition on' in answer['reason']


@pytest.mark.parametrize('question', ['Show birthdate', 'NNA in FY26-Q1 from retail clients older than 60'])
def test_ask_vocabulary_refuses_restricted_phrases_without_core_ordering(eng, question):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    req = parse(question, eng.term_names, eng)
    req.restricted = []
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    assert vocab.denial(question)
    result = Planner(vocab, eng.w, Hidden([], set(), [])).answer(question,
        lambda *args: pytest.fail('Restricted phrase reached interpretation'))
    assert result['kind'] == 'refuse'


@pytest.mark.parametrize('question', [
    'Number of complaints in June 2025 older than 30 days',
    'Credit exposure for June 2025 on facilities older than 5 years',
    'Days past due older than 90 days in June 2025',
    'Fee income for June 2025 by age band',
    'NNA for March 2025 as reported, and the age of the data',
])
def test_ask_vocabulary_does_not_refuse_policy_phrases_in_another_sense(eng, question):
    req = parse(question, eng.term_names, eng)
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    assert not vocab.denial(question)


@pytest.mark.parametrize('question', ['Show email address', 'Show DOB'])
def test_planner_refuses_a_parsed_restricted_lookup_without_core(eng, question):
    from kge.services.ask.engine.planner import Planner
    from kge.services.ask.engine.wording import Hidden
    req = parse(question, eng.term_names, eng)
    assert req.restricted
    vocab = planrules.vocabulary(eng, 'analyst_enterprise', 'analysis', ProductScope(eng, None), PRESETS['graph'], req)
    out = Planner(vocab, eng.w, Hidden([], set(), [])).answer(question,
        lambda *args: pytest.fail('Restricted lookup reached interpretation'))
    assert out['kind'] == 'refuse'


@pytest.mark.parametrize('arm', ['catalog', 'graph'])
def test_generated_context_labels_do_not_deny_a_readable_measure(eng, arm):
    question = 'Retail fee income for June 2025'
    expected = eng.answer(question, 'analyst_enterprise', arm_name=arm)
    actual = eng.answer(question, 'analyst_retail', arm_name=arm)
    assert expected.get('figure') is not None
    assert actual.get('figure') == expected['figure'], actual
