"""The schema arm retains its period refusal after currency candidate narrowing."""
import pytest

from kge.eval.sets import items
from tests.scratch import BUILD
from tests.test_agent_loop import Agent, asked, llm, questions


def test_control_currency_request_keeps_the_missing_period_binding_refusal(eng, monkeypatch):
    """The careful model's refusal, asked under ASK_ORDER=model_first, A1's documented order (#464 requirement 2)."""
    monkeypatch.setenv('ASK_ORDER', 'model_first')
    question, principal, arm = next((question, principal, arm) for key, question, principal, arm in questions()
                                    if key == 'golden:G013:control')
    answer = asked(Agent(eng, llm=llm('careful')), question, principal, arm)
    assert answer['behaviour'] == 'abstain'
    assert answer['reason'] == 'This figure is not recorded by period, so it cannot be given for one. No figure is given.'
    assert answer.get('figure') is None and not answer.get('figures')
    assert 'clarification' not in answer


@pytest.mark.parametrize('identifier,behaviour,reason', [
    *((identifier, 'abstain', f'A predicate on wealth.CLIENT_CD_ROLLUP_1.{column} has no declared role for every selected context. No figure is given.')
      for identifier, column in (('S12', 'SEGMT_LVL_ID5'), ('S12_alt1', 'SEGMT_LVL_ID5'),
                                 ('S12_alt2', 'SEGMT_LVL_NM5'), ('S12_alt3', 'SEGMT_LVL_NM5'))),
    ('S19', 'abstain', 'A predicate on wealth.CLIENT_CD_ROLLUP_1.SEGMT_LVL_ID4 has no declared role for every selected context. No figure is given.'),
    ('S24_alt1', 'abstain', 'A predicate on wealth.BRCH_DIM_1.RGN_CD has no declared role for every selected context. No figure is given.'),
])
def test_control_retains_its_raw_filters_and_nearest_count_ownership(eng, identifier, behaviour, reason):
    item = next(item for item in items(BUILD / 'sage_v1.json') if item['id'] == identifier)
    question = item['question']
    answer = asked(Agent(eng, llm=None), question, item.get('principal') or eng.ASKER, 'control')
    assert answer['behaviour'] == behaviour
    assert answer['reason'] == reason
    assert answer.get('figure') is None and not answer.get('figures')
