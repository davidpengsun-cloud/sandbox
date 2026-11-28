"""Exercise shadow comparison against the real local graph and serving planner."""
from unittest.mock import patch
from threading import Event

from kge.estate.golden_sage import GOLDEN_20
from kge.eval import sets
from kge.services.ask import service
from kge.services.ask.engine import planpublic
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.engine.shadowing import Shadowing
from tests.scratch import BUILD


def test_warm_cold_and_invalidated_inputs_keep_the_served_answer(eng, monkeypatch, caplog):
    question = next(item['question'] for item in sets.items(BUILD / 'sage_v1.json')
                    if item.get('category') == GOLDEN_20 and not item.get('variant_of'))
    original = Shadowing.read
    candidate = planpublic._shadow_candidate
    comparisons = []
    shadow_io = []

    def checked(self, request, **kwargs):
        result = original(self, request, **kwargs)
        comparisons.append(result.outcome)
        return result

    def without_io(*args, **kwargs):
        def forbidden(*args, **kwargs):
            shadow_io.append(True)
            raise AssertionError('shadow backend I/O')
        with patch.object(eng.g, 'q', side_effect=forbidden), patch.object(eng.w, 'columns', side_effect=forbidden):
            return candidate(*args, **kwargs)

    def answer():
        before = len(comparisons)
        result = service.answer(eng, eng.profile, question, 'analyst_enterprise', 'analysis', 'graph', PRESETS['graph'])
        return ({key: result.get(key) for key in ('behaviour', 'reason', 'answer_text', 'sql', 'result')},
                bool(result.get('run_trace', {}).get('shadow', {}).get('observation_id')), len(comparisons) - before)

    monkeypatch.setattr(Shadowing, 'read', checked)
    monkeypatch.setattr(planpublic, '_shadow_candidate', without_io)
    warm, warm_observed, warm_compared = answer()
    templates = getattr(eng.g, '_templates', None)
    monkeypatch.setattr(eng.g, '_templates', None, raising=False)
    cold, cold_observed, cold_compared = answer()
    assert eng.g._templates is None
    monkeypatch.setattr(eng.g, '_templates', templates)
    shadow_templates = eng.g._shadow_templates
    monkeypatch.setattr(eng.g, '_shadow_templates', None)
    failed, failed_observed, failed_compared = answer()
    monkeypatch.setattr(eng.g, '_shadow_templates', shadow_templates)
    monkeypatch.setattr(eng.g, 'writes', eng.g.writes + 1)
    invalidated, invalidated_observed, invalidated_compared = answer()
    eng.g.q('MATCH (s:Shape) SET s.stage = s.stage')
    assert eng.g._shadow_templates is None
    worker = eng._shadow_retry._thread
    assert worker is not None
    worker.join(10)
    assert not worker.is_alive()
    refreshed, refreshed_observed, refreshed_compared = answer()

    assert warm == cold == failed == invalidated == refreshed
    assert shadow_io == []
    assert (warm_observed, warm_compared) == (True, 1)
    assert (cold_observed, cold_compared) == (True, 1)
    assert (failed_observed, failed_compared) == (False, 0)
    assert 'shadow comparison could not be recorded' in caplog.text
    assert (invalidated_observed, invalidated_compared) == (True, 1)
    assert (refreshed_observed, refreshed_compared) == (True, 1)

    started, release = Event(), Event()
    read_templates = eng.g.read_templates
    def unavailable():
        raise RuntimeError('private preparation failure')
    def paused_read():
        started.set()
        assert release.wait(10)
        return read_templates()
    monkeypatch.setattr(eng.g, 'prepare_shadow_templates', unavailable)
    monkeypatch.setattr(eng.g, 'read_templates', paused_read)
    monkeypatch.setattr(eng.g, '_shadow_templates', None)
    eng.refresh()
    worker = eng._shadow_retry._thread
    try:
        assert worker is not None and started.wait(5)
        pending, pending_observed, pending_compared = answer()
    finally:
        release.set()
        if worker is not None:
            worker.join(10)
    assert worker is not None and not worker.is_alive()
    recovered, recovered_observed, recovered_compared = answer()
    assert pending == recovered == warm
    assert (pending_observed, pending_compared) == (False, 0)
    assert (recovered_observed, recovered_compared) == (True, 1)
    assert shadow_io == []
    assert 'private preparation failure' not in caplog.text
