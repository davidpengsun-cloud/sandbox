"""Source-set meanings are declared vocabulary, independent of English organization nouns."""
import json

import pytest

from kge.compiler.pack import read
from kge.pipeline.settings import ROOT
from kge.semantics.coverage import ledger
from kge.semantics.plan import Binding
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import Entry, MeasureSpec, build_vocabulary
from tests.plan_fixtures import grounded, model, name, semantics
from tests.unit.test_plan_relational_matrix import replace_context


def bundle(phrase='organization', readable=None, scoped=None, values=()):
    pack = read(ROOT / 'clients', 'meridian').data
    members = next(a['sources'] for a in pack['terminology']['aliases'] if a['phrase'] == 'bank')
    sem = semantics()
    ctx = sem.contexts['ctx']
    sem.tables = {u + '.events': {'value': 'DECIMAL'} for u in members}
    sem.contexts = {u: replace_context(ctx, id=u, label=u + ' context', measure=u + '.metric', fact=u + '.events',
                              expression='sum(events.value)', values=(u + '.events.value',), filters=()) for u in members}
    sem.entities = {}
    sem.types = {t + '.value': 'DECIMAL' for t in sem.tables}
    sem.d.RESTRICTED_COLUMNS = []
    sem.d.PERIODS = {}
    binding = Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis')
    v = build_vocabulary(sem, binding, readable if readable is not None else members,
                         scoped if scoped is not None else members,
                         [MeasureSpec(u + '.metric', 'USD', 'entities', True) for u in members],
                         aliases=[(phrase, members)], values=values)
    measures = [{'kind': 'measure', 'id': 'm' + str(i), 'measure': name(u + '.metric'), 'context': name(u),
                 'mode': 'current', 'filters': []} for i, u in enumerate(members)]
    return v, model(measures=measures), members


@pytest.mark.parametrize('phrase', ['bank', 'group', 'enterprise', 'organization', 'whole division'])
def test_source_set_is_one_meaning_and_plancheck_requires_every_member(phrase):
    v, p, members = bundle(phrase)
    [entry] = v.candidates(phrase)
    assert entry.kind == 'source_set' and set(entry.value) == set(members)
    checked = check_plan(grounded(p, phrase, v, {phrase: [m.id for m in p.measures]}), v, phrase)
    assert isinstance(checked, CheckedPlan) and set(checked.units) == set(members)
    incomplete = model(measures=p.measures[:-1])
    assert check_plan(grounded(incomplete, phrase, v, {phrase: [m.id for m in incomplete.measures]}), v, phrase).kind == 'abstain'


def test_g021_source_phrase_resolves_through_plancheck_without_an_english_rule():
    question = next(q['question'] for q in json.loads((ROOT / 'clients/meridian/fixture/golden.json').read_text())['items']
                    if q['id'] == 'G021')
    v, p, members = bundle('bank')
    [scope] = [piece for piece in ledger(question, 'turn', v) if any(v.entries[i].kind == 'source_set' for i in piece.candidates)]
    assert question[scope.span.start:scope.span.end] == 'bank'
    source_question = question[scope.span.start:scope.span.end]
    checked = check_plan(grounded(p, source_question, v, {source_question: [m.id for m in p.measures]}), v, source_question)
    assert isinstance(checked, CheckedPlan) and set(checked.units) == set(members)
    assignments = {piece.text: [m.id for m in p.measures] for piece in ledger(question, 'turn', v)}
    full = check_plan(grounded(p, question, v, assignments), v, question)
    assert 'bank' not in full.missing
    incomplete = model(measures=p.measures[:-1])
    assignments = {piece.text: [m.id for m in incomplete.measures] for piece in ledger(question, 'turn', v)}
    assert 'bank' in check_plan(grounded(incomplete, question, v, assignments), v, question).missing
    v_without, _, _ = bundle('unrelated scope')
    assert not v_without.candidates('bank')
    assert check_plan(grounded(p, source_question, v_without, {source_question: [m.id for m in p.measures]}), v_without, source_question).kind == 'abstain'


@pytest.mark.parametrize('scoped', [False, True])
def test_source_set_never_exposes_or_silently_drops_an_unavailable_member(scoped):
    kwargs = {'scoped' if scoped else 'readable': ['retail']}
    v, p, _ = bundle('organization', **kwargs)
    assert not v.candidates('organization')
    assert 'sources:' not in str(v.public())
    decision = check_plan(p, v, 'organization')
    assert decision.kind == ('abstain' if scoped else 'refuse')


def test_source_set_and_a_value_with_the_same_phrase_remain_ambiguous():
    v, p, _ = bundle('organization', values=[Entry('member', 'value', 'organization', 'retail', column='retail.events.value', value='organization')])
    assert {e.kind for e in v.candidates('organization')} == {'source_set', 'value'}
    assert check_plan(p, v, 'organization').kind == 'clarify'
