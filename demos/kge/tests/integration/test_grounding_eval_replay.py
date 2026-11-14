from pathlib import Path

from kge.eval.grounding import Replay, compare, load
from kge.services.ask.engine.combined import Combined


def test_recorded_correct_plan_matches_rules_figure(eng):
    path = Path(__file__).parents[1] / 'fixtures/grounding_eval/certified.json'
    item = next(item for item in load(path) if item['id'] == 'a_phrase')
    def ask(arm, case):
        combined = Combined(eng, llm=None if arm == 'C' else Replay(case['reply']))
        config = combined.default_config('graph')
        if arm == 'A':
            config = config.with_stages(sql_source='planner+model', renderer='template')
        return combined.ask(case['question'], 'analyst_enterprise', config=config)
    with eng.g.as_roles():
        result = compare([item], ask)
    assert result['rows'][0]['C'] == 'correct'
    assert result['rows'][0]['A'] == 'correct'
    assert result['a_loses_to_c'] == 0


def test_grounded_arm_uses_planner_switch_on_recorded_split(eng):
    path = Path(__file__).parents[1] / 'fixtures/grounding_eval/certified.json'
    item = next(item for item in load(path) if item['id'] == 'b_split')
    def ask(arm, case):
        combined = Combined(eng, llm=None if arm == 'C' else Replay(case['reply']))
        combined.plan_grounding = arm == 'B'
        config = combined.default_config('graph')
        if arm != 'C':
            config = config.with_stages(sql_source='planner+model', renderer='template')
        return combined.ask(case['question'], 'analyst_enterprise', config=config)
    with eng.g.as_roles():
        result = compare([item], ask, with_b=True)
    assert result['rows'][0]['C'] == 'correct'
    assert result['rows'][0]['A'] == 'asked'
    assert result['rows'][0]['B'] == 'correct'
    assert result['b_loses_to_c'] == []


def test_measured_requests_keep_fresh_caches_in_either_item_order(monkeypatch, tmp_path):
    from kge.pipeline.settings import ROOT
    from tests.bigquery_fake import pilot
    from tests.bigquery_fake import CountingRunner

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        runner = CountingRunner(fake, opened, engines, engine.profile.asking(None))
        item = next(item for item in load(ROOT / 'tests/fixtures/grounding_eval/certified.json') if item['id'] == 'a_phrase')
        items = [{**item, 'id': identifier} for identifier in ('first', 'second')]
        first = compare(items, runner.ask, with_b=True, measure=runner.measure)
        second = compare(list(reversed(items)), runner.ask, with_b=True, measure=runner.measure)
        expected = first['rows'][0]['jobs']
        assert expected['C']['graph'] > 0
        assert all(row['jobs'] == expected for report in (first, second) for row in report['rows'])
        assert all(report['counts'][arm]['correct'] == 2 for report in (first, second) for arm in ('C', 'A', 'B'))
        assert all(not report['job_budget_failures'] for report in (first, second))
        assert engines == [engine, *runner.requests.engines.values()]
        assert len(engines) == 4
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_native_document_seed_preserves_heldout_citation_truth(monkeypatch, tmp_path):
    from kge.eval.grounding import write
    from kge.pipeline.settings import ROOT
    from tests.bigquery_fake import pilot
    from tests.bigquery_fake import CountingRunner, documents

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        documents(fake, engine)
        runner = CountingRunner(fake, opened, engines, engine.profile.asking(None))
        item = next(item for item in load(ROOT / 'build/heldout.json',
                    ROOT / 'tests/fixtures/agent-query-plan-v1-recordings.json') if item['id'] == 'H013')
        answers = {}
        def ask(arm, case):
            answers[arm] = runner.ask(arm, case)
            return answers[arm]
        result = compare([item], ask, with_b=True, measure=runner.measure)
        assert item['expect']['cite'] == ['POL-DATA-CLASS']
        assert all(answer['behaviour'] == 'cite_certified' and 'POL-DATA-CLASS' not in answer.get('cite', [])
                   for answer in answers.values()), answers
        assert result['rows'][0]['id'] == 'H013'
        assert all(result['rows'][0][arm] == 'wrong_citation' and result['counts'][arm]['wrong_citation'] == 1
                   and result['counts'][arm]['wrong'] == 0 and result['counts'][arm]['answered'] == 1
                   and result['counts'][arm]['correct'] == 0 for arm in ('C', 'A', 'B')), result
        assert result['wrong_citations'] == [
            {'id': 'H013', 'arm': arm, 'missing': ['POL-DATA-CLASS'], 'forbidden': []} for arm in ('C', 'A', 'B')]
        assert result['citation_budget_failures'] == []
        write({'heldout': result}, tmp_path / 'report')
        assert '- H013 (B): missing POL-DATA-CLASS; forbidden none' in (tmp_path / 'report/grounding-eval.md').read_text()
        assert result['b_loses_to_c'] == []
        assert result['no_truth'] == []
        assert result['job_budget_failures'] == []
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_g021_correct_parts_cannot_hide_an_invented_commensurable_total(eng):
    from kge.eval.grounding import ask_with, outcome
    from kge.pipeline.settings import ROOT

    item = next(item for item in load(ROOT / 'build/golden.json') if item['id'] == 'G021')
    with eng.g.as_roles():
        answer = ask_with(eng, 'C', item, 'analyst_enterprise')
    assert outcome(item, answer) == 'correct'
    for change in ({'figure': 999}, {'commensurable': True}, {'behaviour': 'sum_with_verdict'},
                   {'behaviour': 'sum_with_verdict', 'commensurable': True, 'figure': 999}):
        assert outcome(item, {**answer, **change}) == 'wrong', change


def test_ab_harness_grounded_arm_replays_the_same_split_plan_as_evaluation(eng):
    from kge.eval.grounding import ask_with, outcome
    from kge.eval.harness import Arms, choose_arms
    from kge.pipeline.settings import ROOT

    item = next(item for item in load(ROOT / 'tests/fixtures/grounding_eval/certified.json') if item['id'] == 'b_split')
    arms = Arms(eng, llm=Replay(item['reply']))
    assert choose_arms('grounded') == ['grounded']
    with arms.as_roles():
        expected = ask_with(eng, 'B', item, 'analyst_enterprise')
        answer = arms.ask('grounded', item['question'], 'analyst_enterprise')
    assert outcome(item, answer) == 'correct'
    assert answer['figure'] == expected['figure']
    assert answer['behaviour'] == expected['behaviour']
    assert answer['run_trace']['path'] == 'ask'
    assert arms.grounded.plan_grounding is True


def test_commensurable_golden_sum_keeps_its_checked_breakdown(eng):
    from kge.eval.grounding import ask_with, outcome
    from kge.pipeline.settings import ROOT

    item = next(item for item in load(ROOT / 'build/golden.json') if item['id'] == 'G022')
    with eng.g.as_roles():
        answer = ask_with(eng, 'C', item, 'analyst_enterprise')
    assert outcome(item, answer) == 'correct'
    assert answer['commensurable'] is True
    assert answer['parts']
    assert outcome(item, {**answer, 'commensurable': False}) == 'wrong'
    assert outcome(item, {**answer, 'behaviour': 'parts_with_reason'}) == 'wrong'


def test_ab_grounded_arm_adds_no_native_request_jobs(monkeypatch, tmp_path):
    from kge.eval.grounding import ask_with, outcome
    from kge.eval.harness import Arms
    from kge.pipeline.settings import ROOT
    from tests.bigquery_fake import native_measure, pilot

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        item = next(item for item in load(ROOT / 'tests/fixtures/grounding_eval/certified.json') if item['id'] == 'b_split')
        measure = native_measure(fake)
        answers, jobs = {}, {}
        for arm in ('evaluation', 'harness'):
            engine = opened()
            arms = Arms(engine, llm=Replay(item['reply']))
            arms.about()
            with arms.as_roles():
                call = (lambda: arms.ask('grounded', item['question'], 'analyst_enterprise')) if arm == 'harness' else (
                    lambda: ask_with(engine, 'B', item, 'analyst_enterprise'))
                answers[arm], jobs[arm] = measure(arm, item, call)
            engine.close()
            engines.remove(engine)
        assert all(outcome(item, answer) == 'correct' for answer in answers.values())
        assert jobs['harness'] == jobs['evaluation']
        assert jobs['harness']['warehouse'] > 0
        assert answers['harness']['figure'] == answers['evaluation']['figure']
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()


def test_reused_native_engines_match_fresh_request_job_counts_on_diverse_sample(monkeypatch, tmp_path):
    import json
    from kge.eval.grounding import ask_with, evaluate, outcome
    from kge.pipeline.settings import ROOT
    from tests.bigquery_fake import CountingRunner, documents, native_measure, pilot

    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        documents(fake, engine)
        runner = CountingRunner(fake, opened, engines, engine.profile.asking(None))
        fixtures = ROOT / 'tests/fixtures/grounding_eval'
        items = [next(item for item in load(fixtures / 'certified.json') if item['id'] == 'a_phrase'),
                 next(item for item in load(ROOT / 'build/golden.json',
                    ROOT / 'tests/fixtures/agent-query-plan-v1-recordings.json') if item['id'] == 'G026'),
                 next(item for item in load(ROOT / 'build/heldout.json',
                    ROOT / 'tests/fixtures/agent-query-plan-v1-recordings.json') if item['id'] == 'H013')]
        w13 = next(item for item in load(fixtures / 'reference.json') if item['id'] == 'W13')
        measure = native_measure(fake)
        with engine.g.as_roles():
            truth = engine.w.resolve_reference_sql(w13['reference_sql'], unit=w13['unit'],
                readable_units=engine.g.readable_units(engine.profile.asking(None)))
        assert truth['rows'] == [(False,)]
        assert truth['figure'] is None
        path = tmp_path / 'w13.json'
        path.write_text(json.dumps([w13]))
        w13_report = evaluate(engine, [path], ask=runner.ask, measure=runner.measure)['w13']
        assert w13_report['no_truth'] == []
        assert w13_report['refusal_failures'] == []
        assert all(counts['declined'] == 1 for counts in w13_report['counts'].values())
        items.append(w13)
        items.append({**items[0], 'id': 'retail-only', 'principal': 'analyst_retail'})
        fresh_jobs = {}
        for item in items:
            for arm in ('C', 'A', 'B'):
                fresh = opened()
                try:
                    with fresh.g.as_roles():
                        answer, jobs = measure(arm, item, lambda: ask_with(fresh, arm, item, engine.profile.asking(None)))
                    fresh_jobs[item['id'], arm] = jobs
                    assert outcome(item, answer) in ('correct', 'wrong_citation', 'declined')
                    if item['id'] == 'retail-only':
                        assert outcome(item, answer) == 'declined'
                        assert jobs['warehouse'] == jobs['dry'] == 0
                finally:
                    fresh.close()
                    engines.remove(fresh)
        for order in (items, list(reversed(items))):
            report = compare(order, runner.ask, with_b=True, measure=runner.measure)
            assert report['job_budget_failures'] == []
            for row in report['rows']:
                for arm in ('C', 'A', 'B'):
                    assert {key: value for key, value in row['jobs'][arm].items() if key != 'total'} == fresh_jobs[row['id'], arm]
            assert next(row for row in report['rows'] if row['id'] == 'G026')['B'] == 'correct'
            assert all(next(row for row in report['rows'] if row['id'] == 'retail-only')[arm] == 'declined'
                       for arm in ('C', 'A', 'B'))
        assert len(engines) == 4
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
