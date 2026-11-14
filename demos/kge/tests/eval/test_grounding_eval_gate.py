from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import os

from kge.eval import grounding
from kge.pipeline.settings import ROOT
from tests import scratch
from tests.bigquery_fake import grounding_worker, initialize_grounding_worker, pilot


def test_grounding_eval_gate(monkeypatch, tmp_path):
    """Measure equally cold request caches on the pilot adapters, with no network or model calls."""
    fake, twin, opened, engines = pilot(monkeypatch, tmp_path)
    try:
        engine = opened()
        with ProcessPoolExecutor(max_workers=min(8, os.cpu_count() or 1),
                mp_context=multiprocessing.get_context('spawn'), initializer=initialize_grounding_worker,
                initargs=(str(tmp_path / 'workers'), str(scratch.BUILD))) as pool:
            def compare_items(items):
                return grounding.summarize(list(pool.map(grounding_worker, items)))
            report = grounding.evaluate(engine, grounding.default_sets(ROOT / 'tests/fixtures/grounding_eval'), compare_items=compare_items,
                                        recordings=ROOT / 'tests/fixtures/agent-query-plan-v1-recordings.json')
        grounding.write(report, ROOT / 'build')
        print((ROOT / 'build/grounding-eval.md').read_text())
        assert all(result['jobs_per_request'] is not None for result in report.values())
        assert all(counts['unscored'] == counts['missing_reply'] == counts['no_truth'] == 0
                   for result in report.values() for counts in result['counts'].values())
        assert all(not result['b_loses_to_c'] for result in report.values()), {
            name: result['b_loses_to_c'] for name, result in report.items()}
        assert all(result['counts']['B']['wrong'] == 0 for result in report.values()), {
            name: [row['id'] for row in result['rows'] if row['B'] == 'wrong'] for name, result in report.items()}
        assert all(not result['citation_budget_failures'] for result in report.values()), {
            name: result['citation_budget_failures'] for name, result in report.items()}
        assert all(set(row['latency_seconds']) == {'C', 'A', 'B'}
                   for result in report.values() for row in result['rows'])
        assert all(not result['job_budget_failures'] for result in report.values()), {
            name: result['job_budget_failures'] for name, result in report.items()}
        assert grounding.passed(report)
    finally:
        for engine in engines:
            engine.close()
        if twin.conn is not None:
            twin.conn.close()
            twin.db.close()
        fake.con.close()
