"""Window lengths come only from the selected measure's pack declaration."""
from dataclasses import replace

import pytest

from kge.compiler.vocabulary import entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import moving_average_lengths
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.core import PRESETS
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import vocabulary
from tests.unit.test_plan_compiler import connection
from tests.unit.test_rules_derived_shapes import ROWS, SuppliedPlan, declared, execute


@pytest.mark.parametrize('length', [2, 3, 6, 12])
@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
def test_no_window_declaration_refuses_every_previous_implicit_length(monkeypatch, length, modeled):
    eng, vocab = declared(monkeypatch)
    assert moving_average_lengths(vocab, 'u.metric') == ()
    text = f'{length}-month moving average of Metric by month for Q2 2025'
    proposal = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    if modeled:
        proposal = planmodel.read(SuppliedPlan(ground_plan(proposal.plan, text, 'turn', vocab)), text, vocab)
        assert proposal.kind == 'propose', proposal
    refusal = check_plan(proposal.plan, vocab, text)
    assert not isinstance(refusal, CheckedPlan)
    assert 'moving average length is not declared for this measure' in ' '.join(refusal.missing)
    with pytest.raises(TypeError):
        compile_plan(proposal.plan)


def test_another_measure_cannot_grant_a_window_length(monkeypatch):
    eng, _ = declared(monkeypatch)
    sem = eng.semantics
    sem.contexts['other'] = replace(sem.contexts['ctx'], id='other', measure='u.other', grain='day')
    sem.d.VOCABULARY += entries({}, [{'id': 'u.other', 'moving_average_lengths': [3]}])
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    assert moving_average_lengths(vocab, 'u.other') == (3,)
    assert moving_average_lengths(vocab, 'u.metric') == ()
    text = '3-month moving average of Metric by month for Q2 2025'
    proposal = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    refusal = check_plan(proposal.plan, vocab, text)
    assert not isinstance(refusal, CheckedPlan)
    assert 'moving average length is not declared for this measure' in ' '.join(refusal.missing)


@pytest.mark.parametrize('modeled', [False, True], ids=['rules', 'model-plan'])
def test_a_new_pack_length_runs_with_hand_computed_figures(monkeypatch, modeled):
    eng, _ = declared(monkeypatch)
    sem = eng.semantics
    sem.d.VOCABULARY += entries({}, [{'id': 'u.metric', 'moving_average_lengths': [4]}])
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx'), *sem.d.GROUP_BY.items()])
    assert moving_average_lengths(vocab, 'u.metric') == (4,)
    text = '4-month moving average of Metric by month for Q2 2025'
    proposal = planrules.read(eng, parse(text, eng.term_names, eng), vocab, PRESETS['graph'])
    assert proposal.kind == 'propose', proposal
    if modeled:
        proposal = planmodel.read(SuppliedPlan(ground_plan(proposal.plan, text, 'turn', vocab)), text, vocab)
        assert proposal.kind == 'propose', proposal
    checked = check_plan(proposal.plan, vocab, text)
    assert isinstance(checked, CheckedPlan), checked
    compiled = compile_plan(checked)
    assert check(compiled.answer.sql, compiled.answer.allow) == []
    with connection(ROWS) as con:
        rows = execute(checked, con)
    assert [float(r[checked.plan.derived[0].id]) for r in rows] == [120, 135, 150]
