"""Question-run shadow sidecars do not change the memo protocol for older workers."""
import pytest

from kge.pipeline import questionrun


def test_new_checkpoint_is_one_save_and_legacy_worker_reads_the_bare_reply():
    record, saves = {}, []
    reply = {'kind': 'abstain', 'missing': []}
    observation = {'id': 'a' * 32, 'comparison': 'agree'}
    with questionrun.checkpoints(record, lambda: saves.append(questionrun.dumps(record))):
        assert questionrun.memo_with_sidecar('rules:turn', lambda: (reply, observation)) == (reply, observation)
    assert len(saves) == 1
    stored = questionrun.loads(saves[0])
    assert stored['plan'] == {'0:rules:turn': reply}
    assert stored['shadow_plan'] == {'0:rules:turn': observation}
    with questionrun.checkpoints(stored, lambda: pytest.fail('a memo hit must not save')):
        assert questionrun.memo('rules:turn', lambda: pytest.fail('a memo hit must not compute')) == reply
    with questionrun.checkpoints(stored, lambda: pytest.fail('a memo hit must not save')):
        assert questionrun.memo_with_sidecar('rules:turn', lambda: pytest.fail('a memo hit must not compute')) == (reply, observation)


def test_legacy_memo_never_recomputes_an_unknown_epoch():
    reply = {'kind': 'abstain'}
    record = {'plan': {'0:rules:turn': reply}}
    with questionrun.checkpoints(record, lambda: pytest.fail('legacy hit must not save')):
        assert questionrun.memo_with_sidecar('rules:turn', lambda: pytest.fail('legacy hit must not recompute')) == (reply, None)
    assert 'shadow_plan' not in record


def test_sidecar_uses_exactly_one_memo_index_among_ordinary_memos():
    record, saves = {}, []
    with questionrun.checkpoints(record, lambda: saves.append(1)):
        assert questionrun.memo('before', lambda: 1) == 1
        assert questionrun.memo_with_sidecar('rules', lambda: (2, {'id': 'b' * 32})) == (2, {'id': 'b' * 32})
        assert questionrun.memo('after', lambda: 3) == 3
    assert record['plan'] == {'0:before': 1, '1:rules': 2, '2:after': 3}
    assert list(record['shadow_plan']) == ['1:rules']
    assert len(saves) == 3


def test_absent_sidecar_is_not_stored_and_unscoped_calls_do_not_save():
    assert questionrun.memo_with_sidecar('rules', lambda: ('reply', None)) == ('reply', None)
    record, saves = {}, []
    with questionrun.checkpoints(record, lambda: saves.append(1)):
        assert questionrun.memo_with_sidecar('rules', lambda: ('reply', None)) == ('reply', None)
    assert record == {'plan': {'0:rules': 'reply'}}
    assert saves == [1]


def test_compute_failure_propagates_without_a_saved_or_partial_entry():
    record, saves = {}, []
    def failed():
        raise ValueError('failed')
    with questionrun.checkpoints(record, lambda: saves.append(1)):
        with pytest.raises(ValueError, match='failed'):
            questionrun.memo_with_sidecar('rules', failed)
        assert questionrun.memo('next', lambda: 'ok') == 'ok'
    assert record == {'plan': {'1:next': 'ok'}}
    assert saves == [1]


def test_save_failure_propagates_like_the_existing_memo():
    record = {}
    def failed():
        raise OSError('checkpoint failed')
    with questionrun.checkpoints(record, failed):
        with pytest.raises(OSError, match='checkpoint failed'):
            questionrun.memo_with_sidecar('rules', lambda: ('reply', {'id': 'c' * 32}))
    assert record == {'plan': {}}
    saves = []
    with questionrun.checkpoints(record, lambda: saves.append(1)):
        assert questionrun.memo_with_sidecar('rules', lambda: ('fresh', {'id': 'd' * 32})) == ('fresh', {'id': 'd' * 32})
    assert saves == [1]


def test_orphan_sidecar_is_not_mistaken_for_a_new_observation():
    record = {'shadow_plan': {'0:rules': {'id': 'old'}}}
    with questionrun.checkpoints(record, lambda: None):
        assert questionrun.memo_with_sidecar('rules', lambda: ('fresh', None)) == ('fresh', None)
    assert '0:rules' not in record['shadow_plan']


def test_shadow_observation_sidecar_is_validated_and_contains_no_private_input():
    import json
    from types import SimpleNamespace

    from kge.adapters.shadowlog.record import line_of
    from kge.semantics.plan import Abstain, Clarify
    from kge.services.ask.engine.shadow import observe

    private = ('private question text', 'private principal', 'SELECT secret', 'private row', 'private answer')
    graph = Abstain(kind='abstain', spans=(), missing=private)
    coded = Clarify(kind='clarify', spans=(), candidates=())
    observation = observe(release='release-1', graph=graph, coded=coded, graph_shapes=(), served='model',
                          pieces=(), vocab=SimpleNamespace(entries={}))
    sidecar = json.loads(line_of(observation))
    record, persisted = {}, []
    with questionrun.checkpoints(record, lambda: persisted.append(questionrun.dumps(record))):
        questionrun.memo_with_sidecar('rules', lambda: ({'kind': 'abstain'}, sidecar))
    recovered = questionrun.loads(persisted[0])['shadow_plan']['0:rules']
    assert recovered == sidecar
    assert recovered['id'] == observation.id
    text = json.dumps(recovered)
    assert all(secret not in text for secret in private)
    assert not any(key in recovered for key in ('question', 'principal', 'sql', 'rows', 'answer_text'))
