"""Rules first (#464, J4): a full match, the rules plan with no unknown piece that passes check_plan, is served certified
with no model call, and the compiler's or the SQL gate's refusal of it is a certified decline; anything else goes to
model_fallback, the seam J5 fills."""
import pytest

from kge.semantics.plan import Abstain, Propose
from kge.semantics.plancheck import CheckedPlan
from kge.services.ask.engine import planmodel, planpublic
from kge.services.ask.engine import planner as plannermod
from kge.services.ask.engine.planner import Planner
from kge.services.ask.engine import wording
from kge.services.ask.engine.wording import CERTIFIED_LABEL, Hidden, worded
from tests.plan_fixtures import grounded, model, metric_vocabulary as vocabulary
from tests.unit.test_planner_jobs import jobs, setup


class Untouched:
    name = 'mock:untouched'

    def __init__(self):
        self.prompts = []

    def json(self, prompt):
        self.prompts.append(prompt)
        raise AssertionError('the model was asked')


def rules_of(vocab, text='Metric'):
    def rules(question, vocabulary):
        return Propose(kind='propose', base_turn=None, plan=grounded(model(), text, vocab))
    return rules


def served(planner, question, rules, llm):
    """selected_answer's order on a bare planner: rules first, else the seam with the model's plan first."""
    result, _ = planpublic.rules_first(planner, question, rules, 'turn')
    if result is not None:
        return 'certified', result
    def interpret(text, vocabulary):
        return planmodel.read(llm, text, vocabulary)
    return 'fallback', planpublic.model_fallback(planner, question, 'turn', (), interpret, rules, True, None)


@pytest.fixture
def bench(monkeypatch, tmp_path):
    vocab = vocabulary()
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    yield vocab, fake, Planner(vocab, warehouses, Hidden([], set(), []))
    fake.con.close()


def test_a_full_match_is_answered_certified_and_the_mock_model_is_untouched(bench):
    vocab, fake, planner = bench
    llm = Untouched()
    path, result = served(planner, 'Metric', rules_of(vocab), llm)
    assert path == 'certified'
    assert result['kind'] == 'result'
    assert result['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
    assert 'model_refusal' not in result
    assert llm.prompts == []
    assert jobs(fake) == (1, 1)


def test_a_compiler_refusal_of_the_rules_plan_is_a_certified_decline_with_no_model_call(bench, monkeypatch):
    vocab, fake, planner = bench
    def refuses(*args, **kwargs):
        raise ValueError('The compiler refuses this plan.')
    monkeypatch.setattr(plannermod, 'compile_plan', refuses)
    llm = Untouched()
    path, result = served(planner, 'Metric', rules_of(vocab), llm)
    assert path == 'certified'
    assert (result['kind'], result['phase'], result['missing']) == ('abstain', 'compile', ['The compiler refuses this plan.'])
    assert llm.prompts == []
    assert jobs(fake) == (0, 0)


def test_a_sql_gate_refusal_of_the_rules_plan_is_a_certified_decline_with_no_model_call(bench, monkeypatch):
    vocab, fake, planner = bench
    guard = {'passed': False, 'failed_at': 'static', 'problems': ['A table outside the allowlist.']}
    monkeypatch.setattr(plannermod, 'execute_on_runtime', lambda *args, **kwargs: (guard, None, {}))
    llm = Untouched()
    path, result = served(planner, 'Metric', rules_of(vocab), llm)
    assert path == 'certified'
    assert result['kind'] == 'refuse' and result['guard']['failed_at'] == 'static'
    assert llm.prompts == []


def test_an_unknown_piece_is_not_a_full_match_and_goes_to_the_seam(bench):
    vocab, _, planner = bench
    result, reply = planpublic.rules_first(planner, 'Metric sparkly', rules_of(vocab, 'Metric sparkly'), 'turn')
    assert result is None and reply.kind == 'propose'


def test_a_rules_decline_before_check_plan_goes_to_the_seam(bench):
    _, _, planner = bench
    def abstains(question, vocabulary):
        return Abstain(kind='abstain', spans=[], missing=['No certified measure.'])
    result, reply = planpublic.rules_first(planner, 'Metric', abstains, 'turn')
    assert result is None and reply.kind == 'abstain'


def test_the_seam_keeps_todays_path_the_models_plan_first_with_the_rules_as_its_floor(bench):
    vocab, _, planner = bench
    llm = Untouched()
    path, result = served(planner, 'Metric sparkly', rules_of(vocab, 'Metric sparkly'), llm)
    assert path == 'fallback'
    assert len(llm.prompts) >= 1
    assert result['model_refusal']['fallback'] in ('rules', None)


@pytest.mark.parametrize('mode,label', [('certified', True), ('not_sure', False), (None, False)])
def test_answer_text_begins_with_the_certified_label_for_a_certified_answer_only(monkeypatch, mode, label):
    monkeypatch.setattr(wording, '_worded', lambda a, e: dict(a))
    answer = {'answer_text': 'Metric: 15.', 'phrased_by': 'template', **({'answer_mode': mode} if mode else {})}
    text = worded(answer, None)['answer_text']
    assert text.startswith(CERTIFIED_LABEL) is label
    assert text.removeprefix(CERTIFIED_LABEL) == 'Metric: 15.'
    assert worded({**answer, 'answer_text': text}, None)['answer_text'] == text


def test_a_follow_up_is_not_a_full_match_where_a_model_may_have_planned_the_turn_before(bench):
    vocab, _, planner = bench
    def follows(question, vocabulary):
        return Propose(kind='propose', base_turn='earlier', plan=grounded(model(), 'Metric', vocab))
    calls = []
    def prepared(question, proposal, turn='turn', history=(), grounding=False, *, rules_floor=False):
        calls.append(proposal(question, vocab))
        return CheckedPlan.__new__(CheckedPlan)
    planner.prepare = prepared
    assert planpublic.rules_first(planner, 'Metric', follows, 'turn', follow_ups=False)[0] is None
    assert calls and calls[0].base_turn == 'earlier'
