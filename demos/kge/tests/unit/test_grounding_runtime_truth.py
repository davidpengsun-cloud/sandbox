"""The grounding eval scores each answer against its truth on the runtime the answer ran on (issue #565): one runtime
list per item for asking and truth, approved reference SQL as truth on any runtime, drafts and expectations that
cannot run reported apart, and the reference warehouse scored as before. No built graph: a fake engine."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

from kge.eval import grounding
from kge.ports import QueryRefused

APPROVED = {'reference_sql_approved_by': 'a steward', 'reference_sql_approved_on': '2026-11-01'}
SQL = 'SELECT SUM(amount) FROM sales'


def warehouse(figures, *, active=True, defaults=('reference', 'nna')):
    """A fake warehouse whose reference SQL gives one figure per runtime, or refuses where the figure is an error;
    with no runtime_id, the list's first runtime answers, as a selection would."""
    calls = []

    def resolve(sql, *, unit=None, readable_units=None, runtimes=None, runtime_id=None):
        calls.append({'runtime_id': runtime_id, 'runtimes': runtimes})
        rt = runtime_id or (runtimes or list(defaults))[0]
        figure = figures[rt]
        if isinstance(figure, Exception):
            raise figure
        return {'figure': figure, 'rows': [(figure,)], 'columns': ['total'], 'runtime': {'id': rt}}
    w = SimpleNamespace(resolve_reference_sql=resolve, runtimes_active=lambda requested=None: active,
                        default_runtime_list=lambda: list(defaults))
    return w, calls


def engine_on(w):
    return SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'all'), UNIT_ORDER=['retail'],
                           g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'retail'}), w=w)


def answering(figures):
    """Every arm answers each item with the figure and runtime the item's id names."""
    def ask(arm, item):
        figure, rt = figures[item['id']]
        return {'behaviour': 'figure', 'figure': figure, **({'runtime': {'id': rt}} if rt else {})}
    return ask


def run(tmp_path, items, w, ask, name='set'):
    path = tmp_path / f'{name}.json'
    path.write_text(json.dumps([{'question': item['id'], 'reply': {}, **item} for item in items]))
    report = grounding.evaluate(engine_on(w), [path], ask=ask,
                                measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    return report


def test_approved_truth_follows_the_runtime_each_answer_ran_on(tmp_path):
    w, calls = warehouse({'reference': 4, 'nna': 9})
    items = [{'id': 'right-nna', 'reference_sql': SQL, 'runtimes': ['nna', 'reference'], **APPROVED},
             {'id': 'stale-nna', 'reference_sql': SQL, 'runtimes': ['nna', 'reference'], **APPROVED},
             {'id': 'right-ref', 'reference_sql': SQL, 'runtimes': ['nna', 'reference'], **APPROVED,
              'expect': {'figure': 4}}]
    report = run(tmp_path, items, w, answering({'right-nna': (9, 'nna'), 'stale-nna': (4, 'nna'),
                                                'right-ref': (4, 'reference')}))['set']
    outcomes = {row['id']: {row[arm] for arm in grounding.ARMS} for row in report['rows']}
    assert outcomes == {'right-nna': {'correct'}, 'stale-nna': {'wrong'}, 'right-ref': {'correct'}}
    assert {call['runtime_id'] for call in calls} == {None, 'nna', 'reference'}
    assert all(call['runtimes'] == ['nna', 'reference'] for call in calls)
    assert len(calls) == 3


def test_an_item_is_asked_on_the_list_its_truth_is_computed_on(monkeypatch):
    asked = []
    config = SimpleNamespace(with_stages=lambda **stages: config)
    monkeypatch.setattr(grounding, 'Combined', lambda *args, **kwargs: SimpleNamespace(
        default_config=lambda arm: config, ask=lambda *args, **kwargs: asked.append(kwargs) or {'behaviour': 'figure'}))
    grounding.ask_with(None, 'C', {'question': 'one', 'runtimes': ['nna', 'reference']}, 'analyst')
    grounding.ask_with(None, 'C', {'question': 'two'}, 'analyst')
    assert asked[0]['runtimes'] == ['nna', 'reference']
    assert 'runtimes' not in asked[1]


def test_reference_sql_that_cannot_run_on_the_answers_runtime_is_reported_apart(tmp_path):
    w, _ = warehouse({'reference': 4, 'nna': QueryRefused('no mapping for sales on nna')})
    items = [{'id': 'one', 'reference_sql': SQL, 'runtimes': ['reference', 'nna'], **APPROVED}]
    report = run(tmp_path, items, w, answering({'one': (9, 'nna')}))
    result = report['set']
    assert all(counts['unavailable'] == 1 and counts['wrong'] == counts['no_truth'] == counts['correct'] == 0
               and counts['answered'] == 1 for counts in result['counts'].values())
    assert result['no_truth'] == []
    assert result['apart'] == [{'id': 'one', 'arm': arm, 'status': 'unavailable',
                                'reason': 'expectation not available on nna: no mapping for sales on nna'}
                               for arm in grounding.ARMS]
    assert not result['b_loses_to_c'] and not result['r_loses_to_b']
    grounding.write(report, tmp_path / 'out')
    text = (tmp_path / 'out/grounding-eval.md').read_text()
    assert 'Reported apart, neither passed nor failed (items): unavailable 1' in text
    assert '- one (B): expectation not available on nna: no mapping for sales on nna' in text


def test_an_unapproved_figure_answered_on_another_runtime_is_a_draft(tmp_path):
    w, calls = warehouse({'reference': 4, 'nna': 9})
    items = [{'id': 'stored', 'expect': {'figure': 4}},
             {'id': 'unapproved', 'reference_sql': SQL},
             {'id': 'on-reference', 'expect': {'figure': 4}}]
    report = run(tmp_path, items, w, answering({'stored': (9, 'nna'), 'unapproved': (9, 'nna'),
                                                'on-reference': (4, 'reference')}))['set']
    outcomes = {row['id']: {row[arm] for arm in grounding.ARMS} for row in report['rows']}
    assert outcomes == {'stored': {'draft'}, 'unapproved': {'draft'}, 'on-reference': {'correct'}}
    assert all(counts['draft'] == 2 and 'unavailable' not in counts for counts in report['counts'].values())
    assert {row['reason'] for row in report['apart'] if row['id'] == 'stored'} == {
        'draft: it has no approved reference SQL, and its expectation was set on the reference warehouse, not on nna'}
    assert calls == [{'runtime_id': None, 'runtimes': None}]


def test_a_decline_stays_a_decline_whatever_its_truth(tmp_path):
    w, _ = warehouse({'reference': 4, 'nna': QueryRefused('no mapping')})
    items = [{'id': 'one', 'reference_sql': SQL, **APPROVED}]
    report = run(tmp_path, items, w, lambda arm, item: {'behaviour': 'abstain', 'runtime': {'id': 'nna'}})['set']
    assert all(counts['declined'] == 1 and 'unavailable' not in counts for counts in report['counts'].values())
    assert 'apart' not in report


def test_reference_only_installs_score_and_report_as_before(tmp_path):
    w, calls = warehouse({'reference': 4}, active=False, defaults=('reference',))
    items = [{'id': 'approved', 'reference_sql': SQL, **APPROVED},
             {'id': 'unapproved', 'reference_sql': SQL},
             {'id': 'stored', 'expect': {'figure': 4}}]
    report = run(tmp_path, items, w, answering({'approved': (4, 'reference'), 'unapproved': (4, None),
                                                'stored': (4, 'reference')}))
    result = report['set']
    assert all(counts == {'correct': 3, 'wrong': 0, 'wrong_citation': 0, 'declined': 0, 'asked': 0, 'no_truth': 0,
                          'unscored': 0, 'missing_reply': 0, 'answered': 3} for counts in result['counts'].values())
    assert 'apart' not in result
    assert calls == [{'runtime_id': None, 'runtimes': None}]
    assert result['ledger_reads'] == 0 and result['ledger_unchecked'], "a stand-in ask lexes nothing, which is flagged"
    assert grounding.passed({'set': {**result, 'ledger_unchecked': False}})
    grounding.write(report, tmp_path / 'out')
    assert 'Reported apart' not in (tmp_path / 'out/grounding-eval.md').read_text()


def test_truth_errors_on_the_reference_warehouse_stay_no_truth_for_unapproved_items(tmp_path):
    w, _ = warehouse({'reference': QueryRefused('outside readable tables')}, active=False, defaults=('reference',))
    report = run(tmp_path, [{'id': 'one', 'reference_sql': SQL}], w, answering({'one': (4, 'reference')}))['set']
    assert all(counts['no_truth'] == 1 and 'unavailable' not in counts for counts in report['counts'].values())


def test_only_a_refusal_becomes_no_truth_and_any_other_error_propagates_uncached():
    import pytest

    w, calls = warehouse({'reference': RuntimeError('warehouse unreachable'), 'nna': QueryRefused('no mapping')})
    cache = {}
    with pytest.raises(RuntimeError, match='warehouse unreachable'):
        grounding.resolve_truth(w, cache, SQL, 'retail', {'retail'}, None, 'reference')
    assert cache == {}
    with pytest.raises(RuntimeError):
        grounding.resolve_truth(w, cache, SQL, 'retail', {'retail'}, None, 'reference')
    assert len(calls) == 2 and cache == {}
    refused = grounding.resolve_truth(w, cache, SQL, 'retail', {'retail'}, None, 'nna')
    assert refused == {'error': 'no mapping', 'runtime': 'nna'} and len(cache) == 1
    assert grounding.resolve_truth(w, cache, SQL, 'retail', {'retail'}, None, 'nna') is refused
    assert len(calls) == 3
