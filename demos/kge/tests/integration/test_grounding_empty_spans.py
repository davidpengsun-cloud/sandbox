import pytest

from kge.eval.grounding import Replay, load, outcome
from kge.pipeline.settings import ROOT
from kge.services.ask.engine.combined import Combined


CASES = [item for item in load(ROOT / 'tests/fixtures/grounding_eval/citations.json')
         if item['id'].endswith('-missing')]


@pytest.mark.parametrize('item', CASES, ids=lambda item: item['id'])
def test_missing_recorded_citation_is_grounded_without_refusal_or_rules_fallback(eng, item):
    caller = Combined(eng, llm=Replay(item['reply']))
    caller.plan_grounding = True
    config = caller.default_config('graph').with_stages(sql_source='planner+model', renderer='template')
    with eng.g.as_roles():
        answer = caller.ask(item['question'], item['principal'], config=config)
    assert outcome(item, answer) == 'correct', answer
    assert not any(step['step'] == 'model_plan_refused' for step in answer['trace'])
    assert next(step for step in answer['trace'] if step['step'] == 'plan')['interpretation'].startswith('model:')
