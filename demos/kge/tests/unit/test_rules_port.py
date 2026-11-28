"""The rules-planner port (question-shapes.md §4.3, #450 requirement 1, #517), on synthetic packs and stub adapters:
the coded adapter gives exactly the coded reading, public Ask reads the rules path from the port alone and never
branches on the adapter, and whatever an adapter proposes passes through the plan checks, the compiler and the SQL
gate as the coded reading did; an adapter's decline is served as a decline."""
import inspect
from types import SimpleNamespace

import pytest

from kge.semantics.coverage import ledger
from kge.semantics.plan import Abstain, Clarify, HistoryEntry, Name, Propose, QueryPlan
from kge.semantics.planvocabulary import Entry
from kge.services.ask.engine import planner as plannermod
from kge.services.ask.engine import planpublic, planrules, rulesport
from kge.services.ask.engine.core import Engine, PRESETS
from kge.services.ask.engine.question import Period, Request, parse
from kge.services.ask.engine.rulesport import RulesPlanner, RulesQuestion, planned
from kge.services.ask.engine.wording import Hidden
from tests.plan_fixtures import grounded, model, name, semantics, metric_vocabulary as vocabulary
from tests.unit.test_plan_ytd_abstention import assert_no_figure
from tests.unit.test_plan_resolution_matrix import NoWarehouse
from tests.unit.test_planner_jobs import jobs, setup
from tests.unit.test_slot_binding import SWEEP, declared, fresh

__all__ = ['declared']

PROPOSE = Propose(kind='propose', base_turn=None, plan=model())


class Stub:
    """An adapter that is not the coded one: it gives its reply to every question and keeps the questions it read."""
    name = 'stub'

    def __init__(self, reply):
        self.reply, self.questions = reply, []

    def plan(self, question):
        self.questions.append(question)
        return self.reply(question) if callable(self.reply) else self.reply


class Recorded(Stub):
    """The coded adapter behind a recorder, so a test sees what the caller asked the port without changing a reply."""
    name = 'recorded'

    def __init__(self):
        super().__init__(planrules.CODED.plan)


def on_turn(plan, turn):
    """A plan grounded on the fixture's turn, its evidence moved to the turn public Ask reads the question on."""
    data = plan.model_dump(mode='json', by_alias=True)
    for obligation in data['obligations']:
        for span in obligation['spans']:
            span['turn'] = turn
    return QueryPlan.model_validate(data)


def proposing(vocab):
    return lambda q: Propose(kind='propose', base_turn=None, plan=on_turn(grounded(model(), q.question, vocab), q.turn))


def clarifying(q):
    """The declared meanings of the question's one ambiguous piece, as a clarification with its evidence."""
    piece = next(p for p in ledger(q.question, q.turn, q.vocabulary) if len(p.candidates) > 1)
    return Clarify(kind='clarify', spans=(piece.span,), candidates=tuple(Name(id=i, evidence=(piece.span,)) for i in piece.candidates))


def asked(engine, text, history=()):
    return RulesQuestion(engine, parse(text, ['Metric', 'Certified Metric'], engine), text, fresh(engine), PRESETS['graph'],
                         'turn', tuple(history))


def test_the_coded_adapter_is_the_one_the_port_gives_and_meets_the_protocol():
    assert rulesport.rules_planner() is planrules.CODED
    assert planrules.CODED.name == 'coded'
    assert isinstance(planrules.CODED, RulesPlanner)
    assert isinstance(Stub(PROPOSE), RulesPlanner)


@pytest.mark.parametrize('text', SWEEP)
def test_the_coded_adapter_gives_exactly_the_coded_reading(declared, text):
    question = asked(declared, text)
    direct = planrules.read(declared, question.request, question.vocabulary, question.cfg, question.turn)
    reply = planned(rulesport.rules_planner(), question)
    assert isinstance(reply, rulesport.REPLIES)
    assert reply.model_dump(mode='json') == direct.model_dump(mode='json')


def test_the_coded_adapter_proposes_on_the_sweep_and_declines_an_unknown_question(declared):
    assert {planned(planrules.CODED, asked(declared, text)).kind for text in SWEEP} >= {'propose'}
    assert planned(planrules.CODED, asked(declared, 'sparkly widgets')).kind in ('abstain', 'clarify')


def test_the_coded_adapter_answers_a_follow_up_before_it_reads_the_question(declared, monkeypatch):
    seen = []
    monkeypatch.setattr(planrules, 'followup', lambda *args: seen.append(args) or PROPOSE)
    monkeypatch.setattr(planrules, 'read', lambda *args, **kw: pytest.fail('read after a follow-up'))
    question = asked(declared, 'Metric for Q1 2025')
    assert planned(planrules.CODED, question) is PROPOSE
    assert seen == [(declared, question.request, question.vocabulary, question.cfg, question.history, question.turn)]


def test_the_coded_adapter_reads_on_the_vocabulary_a_clarification_narrows(declared, monkeypatch):
    entry = HistoryEntry(turn_id='earlier', question='Metric', plan=None, clarification=None, binding=fresh(declared).binding)
    narrowed = object()
    calls = []
    monkeypatch.setattr(planrules, 'clarified_vocabulary', lambda history, vocab, text: calls.append((history, vocab, text)) or narrowed)
    monkeypatch.setattr(planrules, 'read', lambda engine, req, vocab, cfg, turn, cohort=False: calls.append((vocab, turn, cohort)) or PROPOSE)
    question = RulesQuestion(declared, Request('Certified'), 'the words asked', fresh(declared), PRESETS['graph'], 'turn',
                             (entry,), cohort=True)
    assert planned(planrules.CODED, question) is PROPOSE
    assert calls == [((entry,), question.vocabulary, 'the words asked'), (narrowed, 'turn', True)]


@pytest.mark.parametrize('reply', [None, {'kind': 'propose'}, 'abstain'])
def test_the_port_refuses_any_reply_but_propose_clarify_or_abstain(reply):
    with pytest.raises(TypeError, match='stub rules planner'):
        planned(Stub(reply), RulesQuestion(None, Request('Metric'), 'Metric', vocabulary(), PRESETS['graph'], 'turn'))


def public(monkeypatch, v, req, warehouses=None, release_periods=None, history=(), cohort=False):
    """Public Ask's selected_answer on a synthetic engine, rules only, as test_plan_ytd_abstention's helper builds it,
    with the graph's release periods (none by default) for a test that reaches the release offer, the conversation so
    far, and whether the request reads a cohort."""
    sem = v.semantics
    runtime = SimpleNamespace(contexts=v.contexts, mapping_declarations=[], output_metadata={})
    engine = SimpleNamespace(semantics=sem, w=warehouses or NoWarehouse(), UNIT_ORDER=['u'], UNIT_LABEL={'u': 'Unit'},
                             ALIASES={}, PERIODS=sem.d.PERIODS, CALENDARS=sem.d.CALENDARS, ACRONYMS={})
    engine.g = SimpleNamespace(readable_units=lambda principal: {'u'},
                               release_periods=release_periods or (lambda needs, runtime_id: ()))
    engine.request_abstention = lambda request, reason: Engine.request_abstention(engine, request, reason)
    monkeypatch.setattr(planrules, 'vocabulary', lambda *args: v)
    monkeypatch.setattr(planrules, 'snapshot', lambda *args: runtime)
    monkeypatch.setattr(planrules, 'cohort_terms', lambda *args: ['Cohort'] if cohort else [])
    monkeypatch.setattr(Hidden, 'of', lambda *args: Hidden([], set(), []))
    return planpublic.selected_answer(engine, req, 'reader', 'analysis', PRESETS['graph'], SimpleNamespace(products=None),
                                      {'versions': {}, 'trace': []}, tuple(history))


def served_by(monkeypatch, adapter):
    monkeypatch.setattr(rulesport, 'rules_planner', lambda: adapter)
    return adapter


def test_public_ask_serves_an_adapters_abstain_as_a_decline(monkeypatch):
    stub = served_by(monkeypatch, Stub(Abstain(kind='abstain', spans=(), missing=('The stub declines this.',))))
    req = Request('Metric', term='Metric', terms_found=['Metric'])
    answer = public(monkeypatch, vocabulary(), req)
    assert_no_figure(answer)
    assert 'The stub declines this' in answer['reason']
    assert [q.request for q in stub.questions] == [req]
    assert {(q.question, q.history, q.turn != '') for q in stub.questions} == {('Metric', (), True)}


def test_public_ask_serves_an_adapters_clarify_as_a_clarification(monkeypatch):
    v = vocabulary(values=[Entry('one', 'value', 'First grouping', 'u', column='u.events.category', value='A'),
                           Entry('two', 'value', 'Second grouping', 'u', column='u.events.source', value='x')],
                   aliases=[('Office', 'one'), ('Office', 'two')])
    stub = served_by(monkeypatch, Stub(clarifying))
    answer = public(monkeypatch, v, Request('Office'))
    assert_no_figure(answer)
    assert answer['behaviour'] == 'clarify'
    assert {c['id'] for c in answer['clarification']['choices']} == {'one', 'two'}
    assert len(stub.questions) == 1


def test_an_adapters_plan_is_checked_compiled_gated_and_answered(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        v = vocabulary()
        stub = served_by(monkeypatch, Stub(proposing(v)))
        fake.requests.clear()
        answer = public(monkeypatch, v, Request('Metric', term='Metric', terms_found=['Metric']), warehouses=warehouses)
        assert answer['result']['rows'][0]['cells'][0]['value']['value'] == '15.0'
        assert answer['sql_source'] == 'query-plan-v1' and answer['guard']['passed']
        assert jobs(fake) == (1, 1)
        assert len(stub.questions) == 1
    finally:
        fake.con.close()


def test_an_adapters_plan_the_checks_refuse_gives_no_figure_and_no_job(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        v = vocabulary()
        def unreadable(q):
            data = proposing(v)(q).plan.model_dump(mode='json', by_alias=True)
            data['measures'][0]['measure'] = name('missing')
            return Propose(kind='propose', base_turn=None, plan=QueryPlan.model_validate(data))
        served_by(monkeypatch, Stub(unreadable))
        fake.requests.clear()
        answer = public(monkeypatch, v, Request('Metric', term='Metric', terms_found=['Metric']), warehouses=warehouses)
        assert_no_figure(answer)
        assert 'is not one you may read or one the pack declares' in answer['reason']
        assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()


@pytest.mark.parametrize('failure, behaviour, reason', [('compile', 'abstain', 'The compiler refuses this plan.'),
                                                       ('gate', 'refuse', 'did not pass the SQL gate')])
def test_the_compiler_and_the_sql_gate_refuse_an_adapters_plan(monkeypatch, tmp_path, failure, behaviour, reason):
    fake, warehouses = setup(monkeypatch, tmp_path, False)
    try:
        v = vocabulary()
        if failure == 'compile':
            def refuses(*args, **kwargs):
                raise ValueError('The compiler refuses this plan.')
            monkeypatch.setattr(plannermod, 'compile_plan', refuses)
        else:
            guard = {'passed': False, 'failed_at': 'static', 'problems': ['A table outside the allowlist.']}
            monkeypatch.setattr(plannermod, 'execute_on_runtime', lambda *args, **kwargs: (guard, None, {}))
        served_by(monkeypatch, Stub(proposing(v)))
        fake.requests.clear()
        answer = public(monkeypatch, v, Request('Metric', term='Metric', terms_found=['Metric']), warehouses=warehouses)
        assert not answer.get('result')
        assert answer['behaviour'] == behaviour and reason in answer['reason'], answer
        assert jobs(fake) == (0, 0)
    finally:
        fake.con.close()


def earlier(v):
    """A turn before the question, its plan answered."""
    return HistoryEntry(turn_id='earlier', question='Metric', plan=on_turn(grounded(model(), 'Metric', v), 'earlier'), clarification=None,
                        binding=v.binding)


@pytest.mark.parametrize('cohort', [False, True])
def test_public_ask_hands_the_port_the_conversation_and_the_cohort(monkeypatch, cohort):
    """The rules reading is asked with the request, its words, the conversation so far and the cohort reading, on the
    turn public Ask reads the question on."""
    v = vocabulary()
    history = (earlier(v),)
    stub = served_by(monkeypatch, Stub(Abstain(kind='abstain', spans=(), missing=('The stub declines this.',))))
    req = Request('Metric', term='Metric', terms_found=['Metric'])
    public(monkeypatch, v, req, history=history, cohort=cohort)
    assert len(stub.questions) == 1
    question = stub.questions[0]
    assert (question.request, question.question, question.history, question.cohort) == (req, 'Metric', history, cohort)
    assert question.cfg is PRESETS['graph'] and len(question.turn) == 32
    assert question.vocabulary.entries == v.entries


@pytest.mark.parametrize('cohort', [False, True])
def test_the_release_offer_reads_its_plan_from_the_port_with_no_history(monkeypatch, tmp_path, cohort):
    """Where the rules decline a month as not released, the offer of the latest released month reads its plan from the
    port too, as a fresh question: its own words, no history, as planrules.read was asked before the port, and the
    cohort reading of the question."""
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        fake.con.execute("UPDATE u_calendar_rows SET released = FALSE WHERE period >= DATE '2025-06-01'")
        v = vocabulary(semantics(release=True), aliases=[('Metric', 'ctx')])
        req = Request('Metric June 2025', term='Metric', terms_found=['Metric'])
        req.period = Period('month', '2025-06-30')
        june = {'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}}
        def reply(q):
            if len(stub.questions) == 1:
                return Abstain(kind='abstain', spans=(), missing=('June 2025 is not released yet.',))
            return Propose(kind='propose', base_turn=None,
                           plan=on_turn(grounded(model(time=june), q.question, v, {'June 2025': 'time'}), q.turn))
        stub = served_by(monkeypatch, Stub(reply))
        offered = []
        def offer(self, question, plan, turn, release_periods):
            offered.append(plan)
            return 'The latest released month is May 2025. Would you like that month instead?'
        monkeypatch.setattr(plannermod.Planner, 'release_offer', offer)
        history = (earlier(v),)
        answer = public(monkeypatch, v, req, warehouses=warehouses, history=history, cohort=cohort)
        assert_no_figure(answer)
        assert answer['reason'].startswith('June 2025 is not released yet.')
        assert answer['reason'].endswith('The latest released month is May 2025. Would you like that month instead?')
        assert [p.time.period.value for p in offered] == ['2025-06']
        assert len(stub.questions) == 2
        first, offered_on = stub.questions
        assert first.history == history and offered_on.history == ()
        assert all(q.request is req and q.question == req.question and q.cohort is cohort for q in stub.questions)
        assert first.turn == offered_on.turn
    finally:
        fake.con.close()


def test_the_coded_adapter_serves_an_unreleased_month_as_before(monkeypatch, tmp_path):
    fake, warehouses = setup(monkeypatch, tmp_path, True)
    try:
        fake.con.execute("UPDATE u_calendar_rows SET released = FALSE WHERE period >= DATE '2025-06-01'")
        v = vocabulary(semantics(release=True), aliases=[('Metric', 'ctx')])
        req = Request('Metric June 2025', term='Metric', terms_found=['Metric'])
        req.period = Period('month', '2025-06-30')
        recorded = served_by(monkeypatch, Recorded())
        answer = public(monkeypatch, v, req, warehouses=warehouses)
        assert answer['reason'].startswith('June 2025 is not released yet.')
        assert answer['reason'].endswith('Would you like that month instead?')
        assert recorded.questions and all(q.history == () for q in recorded.questions)
    finally:
        fake.con.close()


def test_public_ask_names_no_coded_entry_point_of_the_rules_reading():
    """A lint beside the stub tests, which are the proof of caller independence: public Ask's source names the port and
    no adapter class or coded entry point of the rules reading."""
    source = inspect.getsource(planpublic)
    assert 'rulesport.rules_planner()' in source and 'rulesport.planned(' in source
    for coded in ('planrules.read(', 'planrules.followup(', 'planrules.CODED', 'planrules.Coded', 'clarified_vocabulary'):
        assert coded not in source, coded
