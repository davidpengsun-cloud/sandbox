"""A pack declares every human wording of a time mode; operation IDs alone grant none."""
import pytest

from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.plancheck import CheckedPlan, check_plan
from tests.plan_fixtures import model, name, semantics, vocabulary


WORDINGS = [('as reported', 'as_reported'), ('as originally reported', 'as_reported'),
            ('current', 'restated'), ('current version', 'restated'),
            ('current versions', 'restated'), ('restated', 'restated')]


def plan(mode):
    proposed = model(time={'kind': 'single', 'period': {'kind': 'month', 'value': '2025-06', 'calendar': name('cal')}})
    measure, = proposed.measures
    return proposed.model_copy(update={'measures': (measure.model_copy(update={'mode': mode}),)})


@pytest.mark.parametrize('wording,mode', WORDINGS)
@pytest.mark.parametrize('absent', [False, True])
def test_empty_or_absent_pack_modes_refuse_every_previously_builtin_wording(wording, mode, absent):
    sem = semantics(release=True)
    if absent:
        del sem.d.TIME_MODES
    else:
        sem.d.TIME_MODES = {}
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    text = 'Metric for June 2025 ' + wording
    assert any(piece.kind == 'unknown' for piece in ledger(text, 'turn', vocab))
    proposed = ground_plan(plan('as_reported' if mode == 'as_reported' else 'current'), text, 'turn', vocab)
    assert not isinstance(check_plan(proposed, vocab, text), CheckedPlan)


@pytest.mark.parametrize('wording,mode', [*WORDINGS, ('saved basis', 'as_reported'), ('live basis', 'restated')])
def test_an_explicit_pack_wording_selects_its_mode_without_a_builtin_synonym(wording, mode):
    sem = semantics(release=True)
    sem.d.TIME_MODES = {wording: mode}
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    text = 'Metric for June 2025 ' + wording
    operation = 'as_reported' if mode == 'as_reported' else 'current'
    pieces = ledger(text, 'turn', vocab)
    assert not [piece.text for piece in pieces if piece.kind == 'unknown']
    assert any(piece.operation == operation for piece in pieces)
    proposed = ground_plan(plan(operation), text, 'turn', vocab)
    assert isinstance(check_plan(proposed, vocab, text), CheckedPlan)


@pytest.mark.parametrize('wording,_mode', WORDINGS)
def test_a_different_declared_wording_does_not_grant_undeclared_synonyms(wording, _mode):
    sem = semantics(release=True)
    sem.d.TIME_MODES = {'saved basis': 'as_reported', 'live basis': 'restated'}
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    text = 'Metric for June 2025 ' + wording
    assert any(piece.kind == 'unknown' for piece in ledger(text, 'turn', vocab))
    assert not isinstance(check_plan(ground_plan(plan('current'), text, 'turn', vocab), vocab, text), CheckedPlan)


def test_a_declared_metric_name_can_still_use_an_ordinary_mode_word():
    sem = semantics()
    sem.d.TIME_MODES = {}
    vocab = vocabulary(sem, aliases=[('Current', 'ctx')])
    text = 'Current for June 2025'
    pieces = ledger(text, 'turn', vocab)
    assert pieces[0].kind == 'name' and pieces[0].candidates == ('ctx',)
    assert isinstance(check_plan(ground_plan(plan('current'), text, 'turn', vocab), vocab, text), CheckedPlan)
