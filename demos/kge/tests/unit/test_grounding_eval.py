import json

import pytest

from kge.eval.grounding import Replay, compare, load, write
from tests.bigquery_fake import native_measure


def test_replay_loader_preserves_missing_reply_and_question_identity(tmp_path):
    path = tmp_path / 'uat.json'
    path.write_text(json.dumps([{'id': 'one', 'kind': 'figure', 'question': 'First?', 'expect': {'figure': 4}, 'reply': {'kind': 'propose'}},
                                {'id': 'two', 'kind': 'figure', 'question': 'Second?', 'expect': {'figure': 5}}]))
    items = load(path)
    assert items[0]['reply'] == {'kind': 'propose'}
    assert 'reply' not in items[1]


def test_comparison_does_not_count_missing_or_unscored_as_correct(tmp_path):
    items = [{'id': '1', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}},
             {'id': '2', 'question': 'two', 'expect': {'figure': 5}},
             {'id': '3', 'question': 'three', 'expect': {}, 'reply': {}}]
    def ask(arm, item):
        if item['id'] == '3':
            return {'behaviour': 'abstain'} if arm == 'C' else {'behaviour': 'figure', 'figure': 7}
        if arm == 'C':
            return {'behaviour': 'figure', 'figure': item['expect']['figure']}
        return {'behaviour': 'figure', 'figure': 9}
    report = compare(items, ask)
    assert report['counts']['A']['wrong'] == 1
    assert report['counts']['A']['missing_reply'] == 1
    assert report['counts']['A']['no_truth'] == 1
    assert report['counts']['A']['unscored'] == 0
    assert report['no_truth'] == [{'id': '3', 'arm': 'A', 'reason':
        'No golden answer, citation, text expectation or reference SQL truth is available.'}]
    assert report['counts']['A']['answered'] == 2
    assert report['a_loses_to_c'] == 1
    write({'sample': report}, tmp_path)
    assert json.loads((tmp_path / 'grounding-eval.json').read_text()) == {'sample': report}
    assert 'one — C: correct; A: wrong' in (tmp_path / 'grounding-eval.md').read_text()


def test_replay_rebinds_current_turn_without_changing_recording():
    reply = {'plan': {'evidence': [{'turn': 'turn', 'start': 0, 'end': 3}]}}
    model = Replay(reply)
    prompt = 'query-plan-v1\n' + json.dumps({'obligations': [{'span': {'turn': 'live-turn'}}]})
    assert model.json(prompt)['plan']['evidence'][0]['turn'] == 'live-turn'
    assert reply['plan']['evidence'][0]['turn'] == 'turn'


def test_grounded_arm_hook_receives_same_item_and_reply():
    item = {'id': '1', 'question': 'one', 'expect': {'figure': 4}, 'reply': {'kind': 'propose'}}
    def grounded(case):
        assert case == item
        return {'behaviour': 'figure', 'figure': 4}
    result = compare([item], lambda arm, case: {'behaviour': 'figure', 'figure': 4}, grounded)
    assert result['counts']['B']['correct'] == 1
    assert result['differences'] == []


def test_table_rows_detect_wrong_values_and_missing_rows():
    from kge.eval.grounding import outcome
    item = {'expect': {'rows': {'East': 10, 'West': 20}}}
    rules = {'behaviour': 'figure', 'rows': {'East': 10, 'West': 20}}
    assert outcome(item, rules, rules) == 'correct'
    assert outcome(item, {'behaviour': 'figure', 'rows': {'East': 10, 'West': 21}}, rules) == 'wrong'
    assert outcome(item, {'behaviour': 'figure', 'rows': {'East': 10}}, rules) == 'wrong'


def test_capture_loader_accepts_raw_json_reply_and_principal(tmp_path):
    path = tmp_path / 'capture.json'
    path.write_text(json.dumps([{'question': 'NNA?', 'principal': 'analyst_enterprise',
                                 'raw_model_reply': '{"kind":"clarify"}'}]))
    item = load(path)[0]
    assert item['principal'] == 'analyst_enterprise'
    assert item['reply'] == {'kind': 'clarify'}
    assert item['id'] == 'capture-001'


def test_structured_sql_truth_compares_all_rows_and_cells():
    from kge.eval.grounding import outcome
    def answer(value):
        return {'behaviour': 'combined', 'result': {'rows': [
            {'dimensions': [{'value': 'East'}], 'cells': [{'value': {'value': value}}]},
            {'dimensions': [{'value': 'West'}], 'cells': [{'value': {'value': 20}}]}]}}
    item = {'_truth_rows': [('East', 10), ('West', 20)]}
    assert outcome(item, answer('10.00'), {}) == 'correct'
    assert outcome(item, answer('11'), {}) == 'wrong'


def test_sql_totals_are_checked_against_the_matching_truth_rows():
    from kge.eval.grounding import outcome

    columns = [{'kind': 'dimension'}, {'kind': 'measure', 'currency': 'USD'}]
    def row(dimension, value, total=False):
        return {'dimensions': [{'value': dimension}], 'cells': [{'value': {'value': value}}], 'total': total}
    def answer(total):
        return {'behaviour': 'combined', 'result': {'columns': columns, 'rows': [
            row('East', '10.00'), row('West', '20.00'), row(None, total, True)]}}
    item = {'_truth_rows': [('East', 10), ('West', 20)]}
    assert outcome(item, answer('30.00')) == 'correct'
    assert outcome(item, answer('999.00')) == 'wrong'
    assert outcome(item, answer('30.009')) == 'wrong'
    golden = {'expect': {'rows': {'East': 10, 'West': 20}}}
    assert outcome(golden, {**answer('30.00'), 'rows': {'East': 10, 'West': 20}}) == 'correct'
    assert outcome(golden, {**answer('999.00'), 'rows': {'East': 10, 'West': 20}}) == 'wrong'


def test_sql_cells_use_currency_and_derived_precision():
    from kge.eval.grounding import outcome

    def answer(amount, share, percent):
        return {'behaviour': 'combined', 'result': {'columns': [
            {'kind': 'dimension'}, {'kind': 'measure', 'currency': 'USD'},
            {'kind': 'derived'}, {'kind': 'derived', 'format': 'percent'}], 'rows': [
            {'dimensions': [{'value': 'East'}], 'cells': [
                {'value': {'value': amount}}, {'value': {'value': share}}, {'value': {'value': percent}}]}]}}
    item = {'_truth_rows': [('East', 10, 0.5, 0.5)]}
    assert outcome(item, answer('10.004', '0.500', '0.500')) == 'correct'
    assert outcome(item, answer('10.009', '0.500', '0.500')) == 'wrong'
    assert outcome({'_truth_rows': [('East', 10.006, 0.5, 0.5)]}, answer('10.004', '0.500', '0.500')) == 'wrong'
    assert outcome(item, answer('10.00', '0.509', '0.500')) == 'wrong'
    assert outcome(item, answer('10.00', '0.500', '0.50009')) == 'wrong'


def test_unverifiable_derived_totals_fail_closed():
    from kge.eval.grounding import outcome

    result = {'columns': [{'kind': 'dimension'}, {'kind': 'derived'}], 'rows': [
        {'dimensions': [{'value': 'East'}], 'cells': [{'value': {'value': '0.5'}}]},
        {'dimensions': [{'value': None}], 'cells': [{'value': {'value': '0.5'}}], 'total': True}]}
    assert outcome({'_truth_rows': [('East', 0.5)]}, {'behaviour': 'combined', 'result': result}) == 'wrong'


def test_comparison_records_grounded_losses_and_expected_outcomes():
    items = [{'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {},
              'expected_outcome': ['correct', 'declined']}]
    def ask(arm, item):
        if arm == 'B':
            return {'behaviour': 'clarify'}
        return {'behaviour': 'figure', 'figure': 4}
    result = compare(items, ask, with_b=True)
    assert result['b_loses_to_c'] == ['one']
    assert result['expected_outcome_failures'] == ['one']


def test_parts_are_checked_even_when_totals_match():
    from kge.eval.grounding import outcome
    item = {'expect': {'parts': {'retail': 1, 'wealth': 2}}}
    rules = {'behaviour': 'figure', 'figure': 3, 'parts': {'retail': 1, 'wealth': 2}}
    assert outcome(item, rules, rules) == 'wrong'
    assert outcome(item, {key: value for key, value in rules.items() if key != 'figure'}, rules) == 'correct'
    assert outcome({'expect': {**item['expect'], 'figure': 3}}, rules, rules) == 'correct'
    assert outcome(item, {'behaviour': 'figure', 'figure': 3, 'parts': {'retail': 2, 'wealth': 1}}, rules) == 'wrong'


def test_missing_table_does_not_equal_empty_table():
    from kge.eval.grounding import outcome
    assert outcome({'expect': {'rows': {}}}, {'behaviour': 'figure'}, {}) == 'wrong'
    assert outcome({'expect': {'rows': {}}}, {'behaviour': 'figure', 'rows': {}}, {}) == 'correct'
    assert outcome({'_truth_rows': []}, {'behaviour': 'combined', 'result': {}}, {}) == 'wrong'
    assert outcome({'_truth_rows': []}, {'behaviour': 'combined', 'result': {'rows': []}}, {}) == 'correct'


def test_job_budget_counts_each_request_and_checks_total_and_each_kind(tmp_path):
    items = [{'id': 'one', 'question': 'one', 'reply': {}},
             {'id': 'two', 'question': 'two', 'reply': {}}]
    amounts = {'one': {'C': (2, 0), 'A': (0, 2), 'B': (2, 2)},
               'two': {'C': (1, 1), 'A': (1, 1), 'B': (1, 1)}}
    def measure(arm, item, call):
        graph, warehouse = amounts[item['id']][arm]
        return call(), {'graph': graph, 'warehouse': warehouse, 'dry': 0, 'retrieval': 0, 'catalogue': 0}
    result = compare(items, lambda arm, item: {'behaviour': 'abstain'}, with_b=True, measure=measure)
    assert result['job_budget_failures'] == ['one']
    assert result['rows'][0]['jobs']['B']['total'] == 4
    assert result['jobs_per_request']['B']['total'] == {'total': 6, 'max': 4}
    assert result['jobs_per_request']['C']['graph'] == {'total': 3, 'max': 2}
    write({'sample': result}, tmp_path)
    report = (tmp_path / 'grounding-eval.md').read_text()
    assert '| B | total | 6 | 4 |' in report
    assert 'B job budget failures: one' in report
    assert 'not measured' not in report


def test_job_budget_flags_kind_increase_and_missing_reply_is_not_zero_jobs():
    items = [{'id': 'one', 'question': 'one', 'reply': {}}, {'id': 'two', 'question': 'two'}]
    def measure(arm, item, call):
        return call(), {'graph': int(arm == 'B'), 'warehouse': int(arm != 'B'),
                        'dry': 0, 'retrieval': 0, 'catalogue': 0}
    result = compare(items, lambda arm, item: {'behaviour': 'abstain'}, with_b=True, measure=measure)
    assert result['job_budget_failures'] == ['one']
    assert set(result['rows'][1]['jobs']) == {'C'}
    assert result['counts']['B']['missing_reply'] == 1


def test_job_budget_rejects_incomplete_or_invalid_measurements():
    import pytest
    item = {'id': 'one', 'question': 'one'}
    for counts in ({'graph': 0}, {'graph': -1, 'warehouse': 0, 'dry': 0, 'retrieval': 0, 'catalogue': 0},
                   {'graph': True, 'warehouse': 0, 'dry': 0, 'retrieval': 0, 'catalogue': 0}):
        with pytest.raises(ValueError, match='job counts'):
            compare([item], lambda arm, item: {}, measure=lambda arm, item, call: (call(), counts))


def test_native_measure_uses_job_budget_classifier_and_isolates_parallel_requests():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from types import SimpleNamespace

    fake = SimpleNamespace(graph=SimpleNamespace(accepts=lambda sql: sql == 'graph'), post=lambda url, body: None)
    measure = native_measure(fake)
    barrier = Barrier(2)
    def request(sql):
        def call():
            fake.post('/queries', {'query': sql})
            barrier.wait(timeout=10)
            return {'behaviour': 'abstain'}
        return measure('C', {}, call)[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(request, sql) for sql in ('graph', 'warehouse')]
        graph, warehouse = [future.result() for future in futures]
    assert graph == {'graph': 1, 'warehouse': 0, 'dry': 0, 'retrieval': 0, 'catalogue': 0}
    assert warehouse == {'graph': 0, 'warehouse': 1, 'dry': 0, 'retrieval': 0, 'catalogue': 0}


def test_every_non_answer_behaviour_is_a_decline_and_partial_figures_fail():
    from kge.eval.grounding import DECLINES, outcome

    for behaviour in (*DECLINES, 'error', 'unrecognised', None):
        assert outcome({'expect': {'figure': 4}}, {'behaviour': behaviour}, {}) == 'declined'
    assert outcome({'expect': {'no_figure': True}}, {'behaviour': 'abstain_with_reason', 'figure': 4}, {}) == 'wrong'
    assert outcome({'expect': {'no_figure': True}}, {'behaviour': 'clarify', 'parts': {'x': 4}}, {}) == 'wrong'


def test_catalogue_and_text_answers_are_scored_against_golden_expectations():
    from kge.eval.grounding import outcome

    item = {'expect': {'answer_text': 'Two certified products', 'behaviour': 'catalogue'}}
    assert outcome(item, {'behaviour': 'catalogue', 'answer_text': 'Two certified products'}, {}) == 'correct'
    assert outcome(item, {'behaviour': 'catalogue', 'answer_text': 'Three certified products'}, {}) == 'wrong'
    assert outcome({'expect': {'answer': 'published daily'}},
                   {'behaviour': 'text', 'answer_text': 'The product is published daily.'}, {}) == 'correct'
    assert outcome({'expect': {'answer': 'published daily'}},
                   {'behaviour': 'text', 'answer_text': 'The product is published weekly.'}, {}) == 'wrong'


def test_definition_citations_are_scored_instead_of_unscored():
    from kge.eval.grounding import outcome

    item = {'expect': {'behaviour': 'cite_certified', 'cite': ['certified'], 'must_not_cite': ['draft']}}
    assert outcome(item, {'behaviour': 'cite_certified', 'cite': ['certified']}, {}) == 'correct'
    assert outcome(item, {'behaviour': 'cite_certified', 'cite': ['draft']}, {}) == 'wrong_citation'
    assert outcome(item, {'behaviour': 'cite_certified', 'cite': ['certified', 'draft']}, {}) == 'wrong_citation'
    assert outcome(item, {'behaviour': 'cite_certified', 'cite': []}, {}) == 'wrong_citation'


def test_rules_answer_is_never_used_as_independent_truth():
    from kge.eval.grounding import outcome

    answer = {'behaviour': 'combined', 'result': {'rows': [
        {'dimensions': [], 'cells': [{'value': {'value': 99}}]}]}}
    assert outcome({}, answer, answer) == 'no_truth'
    assert outcome({'expect': {'ground_truth_error': 'Reference query refused'}}, answer, answer) == 'no_truth'
    assert outcome({'_truth_rows': [(4,)]}, answer, answer) == 'wrong'


def test_all_numeric_shapes_and_totals_are_checked_together():
    from kge.eval.grounding import outcome

    item = {'expect': {'figure': 3, 'parts': {'one': 1, 'two': 2}}}
    assert outcome(item, {'behaviour': 'parts_with_reason', 'figure': 3, 'parts': {'one': 1, 'two': 2}}, {}) == 'correct'
    assert outcome(item, {'behaviour': 'parts_with_reason', 'figure': 4, 'parts': {'one': 1, 'two': 2}}, {}) == 'wrong'
    assert outcome(item, {'behaviour': 'parts_with_reason', 'figure': 3, 'parts': {'one': 2, 'two': 1}}, {}) == 'wrong'
    assert outcome({'expect': {'figure': 4}}, {'behaviour': 'catalogue', 'answer_text': 'Four'}, {}) == 'wrong'


def test_default_sets_include_every_regression_fixture_and_exclude_uat_examples(monkeypatch, tmp_path):
    from kge.eval import grounding

    monkeypatch.setattr(grounding, 'ROOT', tmp_path)
    folder = tmp_path / 'tests/fixtures/grounding_eval'
    folder.mkdir(parents=True)
    names = ('citations.json', 'reference.json', 'certified.json', 'mistakes.json', 'new-regression.json',
             'uat-empty.json', 'uat-synthetic.json')
    for name in names:
        (folder / name).write_text('[]')
    assert grounding.default_sets(folder) == [tmp_path / 'build/golden.json', tmp_path / 'build/heldout.json',
        *(folder / name for name in sorted(names) if not name.startswith('uat-'))]


@pytest.mark.parametrize('argv,reason', [
    ([], '--recordings'),
    (['--recordings', 'recordings.json'], 'provide evaluation sets or --regressions'),
])
def test_cli_requires_explicit_replay_inputs_before_opening_an_engine(monkeypatch, capsys, argv, reason):
    from kge.eval import grounding

    monkeypatch.setattr(grounding.Engine, 'serving', lambda *args: pytest.fail('no engine before input validation'))
    with pytest.raises(SystemExit) as stopped:
        grounding.main(argv=argv)
    assert stopped.value.code == 2
    assert reason in capsys.readouterr().err


def test_gate_rejects_unmeasured_missing_no_truth_wrong_losses_and_changed_refusals(monkeypatch):
    from copy import deepcopy
    from itertools import cycle
    from kge.eval import grounding
    from kge.eval.grounding import JOB_KINDS, passed

    clock = cycle((0, 1))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(clock))
    item = {'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}
    def measure(arm, item, call):
        return call(), dict.fromkeys(JOB_KINDS, 0)
    def ask(arm, item):
        return {'behaviour': 'figure', 'figure': 4}
    good = compare([item], ask, with_b=True, measure=measure)
    assert passed({'sample': good})
    assert not passed({})
    assert not passed({'sample': compare([], ask, with_b=True, measure=measure)})
    assert not passed({'sample': compare([item], ask, with_b=True)})
    for key in ('job_budget_failures', 'citation_budget_failures', 'b_loses_to_c', 'expected_outcome_failures', 'refusal_failures', 'no_truth'):
        bad = deepcopy(good)
        bad[key] = ['one']
        assert not passed({'sample': bad}), key
    for arm in ('C', 'A', 'B'):
        for key in ('unscored', 'missing_reply'):
            bad = deepcopy(good)
            bad['counts'][arm][key] = 1
            assert not passed({'sample': bad}), (arm, key)
    wrong = compare([item], lambda arm, item: {'behaviour': 'figure', 'figure': 5 if arm == 'B' else 4},
                    with_b=True, measure=measure)
    assert not passed({'sample': wrong})
    no_truth = compare([{**item, 'expect': {}}], ask, with_b=True, measure=measure)
    assert not passed({'sample': no_truth})
    refused = compare([{**item, 'expect': {'behaviour': 'refuse'}}],
                      lambda arm, item: {'behaviour': 'clarify' if arm == 'B' else 'refuse'},
                      with_b=True, measure=measure)
    assert refused['refusal_failures'] == ['one']
    assert not passed({'sample': refused})


def test_latency_is_one_sample_per_request_and_is_informational(monkeypatch, tmp_path):
    from copy import deepcopy
    from kge.eval import grounding

    monkeypatch.setattr(grounding, 'each', lambda call, items, name: [call(item) for item in items])
    durations = [1, 2, 1.2, 2, 2, 2.4]
    times = iter(value for duration in durations for value in (0, duration))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(times))
    items = [{'id': str(i), 'question': 'one', 'expect': {'figure': 4}, 'reply': {}} for i in range(2)]
    measured = []
    def measure(arm, item, call):
        measured.append((arm, item['id']))
        return call(), dict.fromkeys(grounding.JOB_KINDS, 0)
    report = compare(items, lambda arm, item: {'behaviour': 'figure', 'figure': 4}, with_b=True, measure=measure)
    assert measured == [(arm, str(i)) for i in range(2) for arm in ('C', 'A', 'B')]
    assert report['latency_seconds']['C'] == {'median': 1.5, 'p95': 2}
    assert report['latency_seconds']['A'] == {'median': 2, 'p95': 2}
    assert abs(report['latency_seconds']['B']['p95'] - 2.4) < 1e-10
    assert report['rows'][0]['latency_seconds'] == {'C': 1, 'A': 2, 'B': 1.2}
    assert report['latency_pass']
    assert grounding.passed({'reference': report})
    failed = deepcopy(report)
    failed['latency_pass'] = False
    assert grounding.passed({'reference': failed})
    write({'reference': report}, tmp_path)
    text = (tmp_path / 'grounding-eval.md').read_text()
    assert 'UAT: not run, no captures' in text
    assert 'Reference: 2 of 25 items; shortfall: 23.' in text
    assert '| C | 1.500000 | 2.000000 |' in text
    assert 'informational: local runs make no model or BigQuery calls; latency is checked on the pilot' in text
    assert 'One timing per request.' in text
    assert 'median of 3' not in text
    assert report['latency_comparison']['split'] == {'A_answered': 2, 'A_declined': 0, 'C_answered': 0}
    assert report['latency_comparison']['ratios'] == {'median': 0.8999999999999999, 'p95': 1.2}



def test_latency_measures_exactly_one_request_and_scores_that_answer():
    from kge.eval.grounding import JOB_KINDS

    calls = []
    def ask(arm, item):
        calls.append(('ask', arm))
        return {'behaviour': 'figure', 'figure': 4 if len(calls) % 2 == 0 else 9}
    def measure(arm, item, call):
        calls.append(('measure', arm))
        return call(), dict.fromkeys(JOB_KINDS, 0)
    item = {'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}
    report = compare([item], ask, with_b=True, measure=measure)
    assert calls == [(kind, arm) for arm in ('C', 'A', 'B')
                     for kind in ('measure', 'ask')]
    assert all(report['rows'][0][arm] == 'correct' for arm in ('C', 'A', 'B'))
    assert all(report['rows'][0]['jobs'][arm]['total'] == 0 for arm in ('C', 'A', 'B'))

def test_latency_over_old_threshold_cannot_fail_gate(monkeypatch):
    from kge.eval import grounding

    monkeypatch.setattr(grounding, 'each', lambda call, items, name: [call(item) for item in items])
    item = {'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}
    def check(durations):
        times = iter(value for duration in durations for value in (0, duration))
        monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(times))
        return compare([item], lambda arm, item: {'behaviour': 'figure', 'figure': 4}, with_b=True,
                       measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    report = check([1, 1, 1.26])
    assert not report['latency_pass']
    assert grounding.passed({'sample': report})
    items = [{**item, 'id': str(i)} for i in range(3)]
    for durations in ([1, 1, 1.26, 1, 1, 1.26, 1, 2, 2],
                      [1, 1, 1, 1, 1, 1, 1, 2, 2.41]):
        times = iter(value for duration in durations for value in (0, duration))
        monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(times))
        report = compare(items, lambda arm, item: {'behaviour': 'figure', 'figure': 4}, with_b=True,
                         measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
        assert not report['latency_pass']
        assert grounding.passed({'sample': report})


def test_latency_absolute_floor_allows_small_increase_over_ratio(monkeypatch):
    from kge.eval import grounding

    times = iter(value for duration in (0.1, 0.5, 0.74) for value in (0, duration))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(times))
    item = {'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}
    report = compare([item], lambda arm, item: {'behaviour': 'figure', 'figure': 4}, with_b=True,
                     measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    assert report['latency_seconds']['B']['median'] > 1.2 * report['latency_seconds']['A']['median']
    assert report['latency_seconds']['B']['p95'] > 1.2 * report['latency_seconds']['C']['p95']
    assert report['latency_pass']
    assert grounding.passed({'sample': report})


def test_replay_validation_refusal_is_a_decline_without_product_fallback(monkeypatch):
    from types import SimpleNamespace
    from kge.adapters.model.errors import NoFallback
    from kge.eval import grounding

    config = SimpleNamespace(with_stages=lambda **stages: config)
    def refused(*args, **kwargs):
        raise NoFallback('The pilot refuses this invalid recorded plan')
    monkeypatch.setattr(grounding, 'Combined', lambda *args, **kwargs: SimpleNamespace(
        default_config=lambda arm: config, ask=refused))
    answer = grounding.ask_with(None, 'A', {'question': 'one', 'reply': {}}, 'analyst_enterprise')
    assert answer == {'behaviour': 'abstain_with_reason', 'reason': 'The pilot refuses this invalid recorded plan'}
    assert grounding.outcome({'expect': {'figure': 4}}, answer) == 'declined'


def test_reference_sql_truth_is_resolved_once_per_access_scope_outside_requests(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from kge.eval.grounding import JOB_KINDS, evaluate

    queries = []
    def truth(sql, **scope):
        queries.append((sql, scope))
        return {'figure': None, 'rows': [('East', 4)]}
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'all'), UNIT_ORDER=['retail'],
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'retail', 'wealth'} if who == 'all' else {'retail'}),
        w=SimpleNamespace(resolve_reference_sql=truth))
    path = tmp_path / 'tables.json'
    path.write_text(json.dumps([{'id': identifier, 'question': 'one', 'principal': principal, 'reply': {},
        'reference_sql': 'SELECT region, SUM(amount) FROM sales GROUP BY region', 'expect': {'unit': 'retail'}}
        for identifier, principal in [('one', 'all'), ('two', 'all'), ('three', 'narrow')]]))
    def ask(arm, item):
        return {'behaviour': 'combined', 'result': {'rows': [
            {'dimensions': [{'value': 'East'}], 'cells': [{'value': {'value': 4}}]}]}}
    def measure(arm, item, call):
        assert len(queries) == 2
        return call(), dict.fromkeys(JOB_KINDS, 0)
    report = evaluate(engine, [path], ask=ask, measure=measure)['tables']
    assert len(queries) == 2
    assert queries[0][1] == {'unit': 'retail', 'readable_units': {'retail', 'wealth'}, 'runtimes': None}
    assert queries[1][1] == {'unit': 'retail', 'readable_units': {'retail'}, 'runtimes': None}
    assert all(counts['correct'] == 3 for counts in report['counts'].values())
    assert report['no_truth'] == []
    assert report['jobs_per_request']['B']['total'] == {'total': 0, 'max': 0}


def test_refused_reference_sql_is_listed_as_no_truth_and_cannot_pass(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from kge.eval.grounding import JOB_KINDS, evaluate, passed
    from kge.ports import QueryRefused

    def refused(sql, **scope):
        raise QueryRefused('Reference SQL is outside readable tables')
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'narrow'), UNIT_ORDER=['retail'],
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'retail'}),
        w=SimpleNamespace(resolve_reference_sql=refused))
    path = tmp_path / 'refused.json'
    path.write_text(json.dumps([{'id': 'one', 'question': 'one', 'reply': {}, 'expect': {}, 'reference_sql': 'SELECT 4'}]))
    report = evaluate(engine, [path], ask=lambda arm, item: {'behaviour': 'figure', 'figure': 4},
                      measure=lambda arm, item, call: (call(), dict.fromkeys(JOB_KINDS, 0)))
    assert report['refused']['no_truth'] == [
        {'id': 'one', 'arm': arm, 'reason': 'Reference SQL is outside readable tables'} for arm in ('C', 'A', 'B')]
    assert all(counts['correct'] == 0 and counts['no_truth'] == 1 for counts in report['refused']['counts'].values())
    assert not passed(report)


@pytest.mark.parametrize('change', [
    {'figure': 999},
    {'behaviour': 'sum_with_verdict'},
    {'commensurable': True},
    {'behaviour': 'sum_with_verdict', 'commensurable': True, 'figure': 999},
    {'figures': [{'value': 999}]},
])
def test_noncommensurable_parts_reject_extra_figures_verdicts_and_behaviours(change):
    from kge.eval.grounding import outcome

    expect = {'behaviour': 'parts_with_reason', 'commensurable': False, 'parts': {'retail': 10, 'wealth': 20}}
    answer = dict(expect)
    assert outcome({'id': 'G021', 'expect': expect}, answer) == 'correct'
    assert outcome({'id': 'G021', 'expect': expect}, {**answer, **change}) == 'wrong'


@pytest.mark.parametrize('total, expected', [('4.00', 'correct'), ('999.00', 'wrong')])
def test_scalar_expectations_check_structured_totals(total, expected):
    from kge.eval.grounding import outcome

    result = {'columns': [{'kind': 'measure', 'currency': 'USD'}], 'rows': [
        {'dimensions': [], 'cells': [{'value': {'value': '4.00'}}]},
        {'dimensions': [], 'cells': [{'value': {'value': total}}], 'total': True}]}
    assert outcome({'expect': {'figure': 4}}, {'behaviour': 'figure', 'figure': 4, 'result': result}) == expected


def test_scalar_expectations_reject_extra_structured_and_display_figures():
    from kge.eval.grounding import outcome

    item = {'expect': {'figure': 4}}
    answer = {'behaviour': 'figure', 'figure': 4}
    result = {'columns': [{'kind': 'measure'}], 'rows': [
        {'dimensions': [], 'cells': [{'value': {'value': 4}}]},
        {'dimensions': [], 'cells': [{'value': {'value': 999}}]}]}
    assert outcome(item, {**answer, 'result': result}) == 'wrong'
    assert outcome(item, {**answer, 'figures': [{'value': 999}]}) == 'wrong'
    assert outcome(item, {**answer, 'figures': [{'value': 4}]}) == 'correct'
    assert outcome(item, {**answer, 'rows': {'invented': 999}}) == 'wrong'
    assert outcome(item, {**answer, 'ratio': 999}) == 'wrong'


def test_parts_expectations_check_structured_cell_ownership_and_forbid_totals():
    from copy import deepcopy
    from kge.eval.grounding import outcome

    expect = {'behaviour': 'parts_with_reason', 'commensurable': False, 'parts': {'retail': 10, 'wealth': 20}}
    result = {'columns': [{'kind': 'measure', 'lineage': ['retail.revenue'], 'currency': 'USD'},
                          {'kind': 'measure', 'lineage': ['wealth.revenue'], 'currency': 'USD'}], 'rows': [
        {'dimensions': [], 'cells': [{'value': {'value': 10}}, {'value': {'value': 20}}]}]}
    answer = {**expect, 'result': result}
    assert outcome({'expect': expect}, answer) == 'correct'
    swapped = deepcopy(answer)
    swapped['result']['rows'][0]['cells'].reverse()
    assert outcome({'expect': expect}, swapped) == 'wrong'
    extra = deepcopy(answer)
    extra['result']['rows'].append({**result['rows'][0], 'total': True})
    assert outcome({'expect': expect}, extra) == 'wrong'


@pytest.mark.parametrize('behaviour', ['abstain', 'clarify', 'refuse', 'figure'])
def test_truth_sql_error_fails_gate_even_when_every_arm_declines(monkeypatch, tmp_path, behaviour):
    from contextlib import nullcontext
    from itertools import cycle
    from types import SimpleNamespace
    from kge.eval import grounding
    from kge.ports import QueryRefused

    clock = cycle((0, 1))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(clock))
    def refused(sql, **scope):
        raise QueryRefused('Reference SQL is outside readable tables')
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'narrow'), UNIT_ORDER=['retail'],
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'retail'}),
        w=SimpleNamespace(resolve_reference_sql=refused))
    path = tmp_path / 'truth-error.json'
    path.write_text(json.dumps([{'id': 'one', 'question': 'one', 'kind': 'figure', 'reply': {},
                                'reference_sql': 'SELECT 4'}]))
    report = grounding.evaluate(engine, [path], ask=lambda arm, item: {'behaviour': behaviour},
        measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    result = report['truth-error']
    assert result['latency_pass']
    assert result['no_truth'] == [{'id': 'one', 'arm': arm, 'reason': 'Reference SQL is outside readable tables'}
                                  for arm in ('C', 'A', 'B')]
    assert all(counts['no_truth'] == 1 and counts['declined'] == counts['asked'] == 0
               for counts in result['counts'].values())
    assert not grounding.passed(report)
    grounding.write(report, tmp_path / 'report')
    assert 'Reference SQL is outside readable tables' in (tmp_path / 'report/grounding-eval.md').read_text()


@pytest.mark.parametrize('a_answered,b_answered,c_answered,baseline,kind', [
    (True, True, True, 'A', 'answered'),
    (False, False, True, 'A', 'declined'),
    (False, True, True, 'C', 'answered'),
    (True, False, True, None, None),
    (False, True, False, None, None),
])
def test_latency_baseline_matches_each_items_outcome_kind(monkeypatch, tmp_path,
        a_answered, b_answered, c_answered, baseline, kind):
    from kge.eval import grounding

    times = iter(value for duration in (1, 0.1, 1.2) for value in (0, duration))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(times))
    answered = dict(zip(('C', 'A', 'B'), (c_answered, a_answered, b_answered)))
    def ask(arm, item):
        return {'behaviour': 'figure', 'figure': 4} if answered[arm] else {'behaviour': 'abstain'}
    result = compare([{'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}], ask, with_b=True)
    comparison = result['latency_comparison']
    if baseline is None:
        assert comparison['unpaired'] == ['one']
        assert not result['latency_pass']
        assert 'latency_comparison' not in result['rows'][0]
    else:
        assert comparison['unpaired'] == []
        assert comparison['split'] == {key: int(key == f'{baseline}_{kind}')
                                       for key in ('A_answered', 'A_declined', 'C_answered')}
        assert result['rows'][0]['latency_comparison']['baseline'] == baseline
        assert result['rows'][0]['latency_comparison']['kind'] == kind
        assert result['latency_pass'] is (baseline == 'C')
    write({'sample': result}, tmp_path)
    text = (tmp_path / 'grounding-eval.md').read_text()
    for key, count in comparison['split'].items():
        assert f'| {key} | {count} |' in text


def test_latency_comparisons_keep_pairs_instead_of_matching_unrelated_percentiles():
    from kge.eval.grounding import compare_latency

    rows = [{'id': str(index), 'A_kind': 'answered', 'B_kind': 'answered', 'C_kind': 'answered',
             'latency_seconds': {'A': a, 'B': b, 'C': 10}}
            for index, (a, b) in enumerate(((1, 2), (2, 1)))]
    comparison, passed = compare_latency(rows)
    assert comparison['ratios'] == {'median': 1.25, 'p95': 2}
    assert comparison['delta_seconds'] == {'median': 0, 'p95': 1}
    assert passed is False
    assert comparison['split'] == {'A_answered': 2, 'A_declined': 0, 'C_answered': 0}


@pytest.mark.parametrize('a,b,expected', [(0, 0, True), (0, 0.2, True), (0, 0.26, False)])
def test_latency_zero_baseline_keeps_the_absolute_allowance(a, b, expected):
    from kge.eval.grounding import compare_latency

    _, passed = compare_latency([{'id': 'one', 'A_kind': 'declined', 'B_kind': 'declined', 'C_kind': 'declined',
                                 'latency_seconds': {'A': a, 'B': b, 'C': 0}}])
    assert passed is expected


@pytest.mark.parametrize('change,expected', [
    ({}, 'wrong_citation'),
    ({'cite': ['POL']}, 'correct'),
    ({'cite': ['POL', 'DRAFT']}, 'wrong_citation'),
    ({'figure': 5}, 'wrong'),
    ({'behaviour': 'sum_with_verdict'}, 'wrong'),
    ({'figures': [{'value': 999}]}, 'wrong'),
])
def test_correct_figure_missing_citation_is_never_correct_or_a_wrong_figure(change, expected):
    from kge.eval.grounding import outcome

    item = {'expect': {'figure': 4, 'behaviour': 'figure', 'cite': ['POL'], 'must_not_cite': ['DRAFT']}}
    assert outcome(item, {'behaviour': 'figure', 'figure': 4, **change}) == expected


def test_citation_budget_is_per_item_and_never_offsets_a_new_error(monkeypatch, tmp_path):
    from itertools import cycle
    from kge.eval import grounding

    clock = cycle((0, 1))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(clock))
    items = [{'id': identifier, 'question': 'one', 'reply': {}, 'expect': {'figure': 4, 'cite': ['POL']}}
             for identifier in ('old', 'new')]
    def ask(arm, item):
        cited = (arm == 'B') == (item['id'] == 'old')
        return {'behaviour': 'figure', 'figure': 4, 'cite': ['POL'] if cited else []}
    result = compare(items, ask, with_b=True,
                     measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    assert result['counts']['B']['wrong'] == result['counts']['C']['wrong'] == 0
    assert result['counts']['B']['wrong_citation'] == result['counts']['C']['wrong_citation'] == 1
    assert result['counts']['B']['correct'] == result['counts']['C']['correct'] == 1
    assert result['counts']['B']['answered'] == result['counts']['C']['answered'] == 2
    assert result['citation_budget_failures'] == ['new']
    assert not grounding.passed({'sample': result})
    write({'sample': result}, tmp_path)
    text = (tmp_path / 'grounding-eval.md').read_text()
    assert '| Wrong figure | Wrong citation |' in text
    assert '- new (B): missing POL; forbidden none' in text


def test_existing_citation_failure_passes_only_when_b_is_no_worse_per_item(monkeypatch):
    from itertools import cycle
    from kge.eval import grounding

    clock = cycle((0, 1))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(clock))
    item = {'id': 'H013', 'question': 'one', 'reply': {}, 'expect': {'figure': 4, 'cite': ['POL', 'OTHER']}}
    def compare_citations(citations):
        return compare([item], lambda arm, item: {'behaviour': 'figure', 'figure': 4, 'cite': citations[arm]},
                       with_b=True, measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    same = compare_citations(dict.fromkeys(('C', 'A', 'B'), ['POL']))
    assert all(counts['correct'] == counts['wrong'] == 0 and counts['wrong_citation'] == 1
               for counts in same['counts'].values())
    assert same['citation_budget_failures'] == []
    assert grounding.passed({'sample': same})
    worse = compare_citations({'C': ['POL'], 'A': ['POL'], 'B': []})
    assert worse['citation_budget_failures'] == ['H013']
    assert not grounding.passed({'sample': worse})


def test_missing_recording_in_rules_floor_is_still_a_missing_reply(monkeypatch, tmp_path):
    from itertools import cycle
    from kge.eval import grounding

    clock = cycle((0, 1))
    monkeypatch.setattr(grounding.time, 'perf_counter', lambda: next(clock))
    def ask(arm, item):
        return {'behaviour': 'figure', 'figure': 4, 'trace': [] if arm == 'C' else [
            {'step': 'model_plan_refused', 'reason': 'Missing plan recording: key', 'fallback': 'rules'}]}
    result = compare([{'id': 'one', 'question': 'one', 'expect': {'figure': 4}, 'reply': {}}], ask,
                     with_b=True, measure=lambda arm, item, call: (call(), dict.fromkeys(grounding.JOB_KINDS, 0)))
    assert result['rows'][0]['B_figure'] == 4
    assert result['counts']['C']['correct'] == 1
    for arm in ('A', 'B'):
        assert result['counts'][arm]['missing_reply'] == 1
        assert result['counts'][arm]['correct'] == 0
        assert result['rows'][0][f'{arm}_missing_recording'] is True
    assert not grounding.passed({'sample': result})
    write({'sample': result}, tmp_path)
    assert '- one (B): missing recording' in (tmp_path / 'grounding-eval.md').read_text()


def test_mapped_count_lineage_scores_g026_parts_and_rejects_wrong_owners():
    from copy import deepcopy
    from kge.eval.grounding import outcome

    expect = {'behaviour': 'parts_with_reason', 'commensurable': False, 'parts': {'retail': 176, 'sme': 59}}
    result = {'columns': [{'kind': 'dimension'},
        {'kind': 'count', 'lineage': ['context:mapped:retail.customer_activity.is_active_flag:count',
                                    'mapped:retail.customer_activity.is_active_flag:count', '2025-06-01']},
        {'kind': 'count', 'lineage': ['context:mapped:sme.client_activity.active_flag:count',
                                    'mapped:sme.client_activity.active_flag:count', '2025-06-01']}],
        'rows': [{'dimensions': [{'value': '2025-06'}], 'cells': [
            {'value': {'value': 176}}, {'value': {'value': 59}}]}]}
    item, answer = {'id': 'G026', 'expect': expect}, {**expect, 'result': result}
    assert outcome(item, answer) == 'correct'
    for values in ((175, 59), (59, 176), (176, 60)):
        wrong = deepcopy(answer)
        for cell, value in zip(wrong['result']['rows'][0]['cells'], values):
            cell['value']['value'] = value
        assert outcome(item, wrong) == 'wrong'
    for lineage in ([], ['mapped:wealth.customer_activity.active:count'],
                    ['mapped:retail.customer_activity.active:count', 'mapped:sme.client_activity.active:count']):
        wrong = deepcopy(answer)
        wrong['result']['columns'][1]['lineage'] = lineage
        assert outcome(item, wrong) == 'wrong'


def test_w13_declares_its_own_truth_unit_instead_of_defaulting_to_retail(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from kge.eval.grounding import JOB_KINDS, evaluate
    from kge.pipeline.settings import ROOT

    item = next(item for item in load(ROOT / 'tests/fixtures/grounding_eval/reference.json') if item['id'] == 'W13')
    assert item['unit'] == 'wealth'
    queries = []
    def truth(sql, **scope):
        queries.append((sql, scope))
        assert scope == {'unit': 'wealth', 'readable_units': {'retail', 'wealth'}, 'runtimes': None}
        return {'figure': 0, 'rows': [(False,)]}
    engine = SimpleNamespace(profile=SimpleNamespace(asking=lambda _: 'all'), UNIT_ORDER=['retail', 'wealth'],
        g=SimpleNamespace(as_roles=nullcontext, readable_units=lambda who: {'retail', 'wealth'}),
        w=SimpleNamespace(resolve_reference_sql=truth))
    path = tmp_path / 'reference.json'
    path.write_text(json.dumps([item]))
    report = evaluate(engine, [path], ask=lambda arm, item: {'behaviour': 'abstain_with_reason'},
                      measure=lambda arm, item, call: (call(), dict.fromkeys(JOB_KINDS, 0)))['reference']
    assert len(queries) == 1
    assert 'MONTH_DIM_1' in queries[0][0]
    assert report['no_truth'] == []
    assert report['refusal_failures'] == []
    assert all(counts['declined'] == 1 and counts['wrong'] == counts['no_truth'] == 0
               for counts in report['counts'].values())


def test_reused_engines_reset_request_state_and_open_exactly_one_per_arm(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from kge.eval import grounding

    engines = []
    def opened():
        engine = SimpleNamespace(g=SimpleNamespace(as_roles=nullcontext, _term_reads={'initial': []},
            _chunk_counts={}, _definition_readiness={}, _scoped_vocabularies={}, _role_templates=None, _model_hash='hash'),
            semantics=SimpleNamespace(), close=lambda: None)
        engines.append(engine)
        return engine
    def ask(engine, arm, item, principal):
        assert engine.g._term_reads == {'initial': []}
        assert engine.g._chunk_counts == engine.g._definition_readiness == engine.g._scoped_vocabularies == {}
        assert not hasattr(engine.semantics, 'plan_snapshot')
        engine.g._term_reads['initial'].append(item['id'])
        engine.g._chunk_counts['request'] = 1
        engine.g._definition_readiness['request'] = 1
        engine.g._scoped_vocabularies['request'] = 1
        engine.semantics.plan_snapshot = object()
        return {'behaviour': 'figure', 'figure': 4}
    monkeypatch.setattr(grounding, 'ask_with', ask)
    runner = grounding.RequestEngines(opened, 'all')
    items = [{'id': str(i), 'question': 'one', 'reply': {}, 'expect': {'figure': 4}} for i in range(4)]
    try:
        report = compare(items, runner.ask, with_b=True)
        assert len(engines) == 3
        assert len({id(engine) for engine in runner.engines.values()}) == 3
        assert all(counts['correct'] == 4 for counts in report['counts'].values())
    finally:
        runner.close()


def test_measured_gate_is_outside_every_ci_integration_collection():
    from kge.pipeline.settings import ROOT
    from tests import lanes

    gate = ROOT / 'tests/eval/test_grounding_eval_gate.py'
    assert gate.is_file()
    assert not (ROOT / 'tests/integration/test_grounding_eval_gate.py').exists()
    assert not (ROOT / 'kge/eval/test_grounding_eval_gate.py').exists()
    assert all('test_grounding_eval_gate.py' not in path for lane in ('unit', 'integration', 'e2e') for path in lanes.files_in(lane))
    assert lanes.files_in('eval') == ['tests/eval/test_grounding_eval_gate.py']
    assert lanes.lane_of(gate, 'test_grounding_eval_gate') == 'eval'
    makefile = (ROOT / 'Makefile').read_text()
    assert 'pytest tests/eval -m eval -q -s' in makefile
    assert 'pytest $$($(PY) tests/lanes.py unit) -q -m unit' in makefile
    assert 'pytest $$($(PY) tests/lanes.py integration) -q -m integration' in makefile
