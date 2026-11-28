"""The on-mode prompt's verified examples (#464, J5): clients/meridian/baselines/examples.json is what the rules certify
for the 20 golden questions now, each plan is a QueryPlan, and the engine reads them through the pack store."""
import json
from types import SimpleNamespace

import pytest

from kge.estate import examples
from kge.pipeline.settings import ROOT
from kge.semantics.plan import QueryPlan
from kge.services.ask.engine import planmodel


def test_the_committed_examples_are_the_rules_certified_plans_of_the_golden_questions(eng):
    path = ROOT / 'clients/meridian' / planmodel.EXAMPLES
    assert path.read_text() == examples.text(eng), 'run make examples'
    held = planmodel.verified_examples(eng)
    assert len(held) == 20 and all(QueryPlan.model_validate(e['plan']) for e in held)
    assert planmodel.verified_examples(SimpleNamespace()) == ()


def test_the_release_job_writes_only_the_certified_golden_questions_and_never_fails_on_one(eng, monkeypatch):
    golden = examples.golden_20()
    stray = (SimpleNamespace(question='What is the weather on Mars?', id='X1'), *golden[0][1:])
    monkeypatch.setattr(examples, 'golden_20', lambda: [*golden, stray])
    with pytest.raises(ValueError, match='X1'):
        examples.text(eng)
    path = ROOT / 'clients/meridian' / planmodel.EXAMPLES
    assert examples.text(eng, certified_only=True) == path.read_text()


def test_the_job_s_file_in_the_store_is_what_the_on_mode_prompt_reads(tmp_path, monkeypatch):
    from kge.adapters.objstore import open_store
    held = [{'question': 'q', 'plan': {'schema': 'query-plan/v1'}}]
    store = tmp_path / 'store'
    open_store(str(store)).put('clients/acme/' + planmodel.EXAMPLES, json.dumps(held).encode())
    monkeypatch.setenv('STORE_URL', str(store))
    profile = SimpleNamespace(client='acme', clients=tmp_path / 'clients', run_dir=lambda: tmp_path / 'run')
    engine = SimpleNamespace(profile=profile, identity={'release': 'r1', 'hash': 'h' * 64})
    assert planmodel.verified_examples(engine) == tuple(held)


def test_the_release_job_writes_them_into_the_profile_s_own_pack(eng, tmp_path, monkeypatch, capsys):
    from kge.pipeline import profile as profiles
    from kge.services.ask.engine import core
    prof = SimpleNamespace(client='acme', clients=tmp_path / 'clients')
    monkeypatch.setattr(profiles, 'resolve', lambda name=None: prof)
    monkeypatch.setattr(core.Engine, 'serving', classmethod(lambda cls, p: SimpleNamespace(
        answer=eng.answer, close=lambda: None)))
    assert examples.main(['--release']) == 0
    written = tmp_path / 'clients/acme' / planmodel.EXAMPLES
    assert written.read_text() == (ROOT / 'clients/meridian' / planmodel.EXAMPLES).read_text()
    assert 'examples: wrote baselines/examples.json: 20 verified examples' in capsys.readouterr().out


def test_the_examples_are_asked_as_the_profile_s_asker_never_a_named_role(eng, monkeypatch):
    """A client whose querying role has another name (#553): its profile's asker, here finance_reporting, reads what
    analyst_enterprise reads, and no analyst_enterprise may ask. The library is the committed one, on --release too,
    never an empty list; and no role is named in the job."""
    import inspect
    rights = eng.g.may
    monkeypatch.setattr(eng, 'ASKER', 'finance_reporting')
    monkeypatch.setattr(eng.g, 'may', lambda who, process: who != 'analyst_enterprise' and rights(who, process))
    assert eng.answer('What was NNA for March 2025?', 'analyst_enterprise')['behaviour'] == 'refuse'
    path = ROOT / 'clients/meridian' / planmodel.EXAMPLES
    assert examples.text(eng) == path.read_text()
    assert examples.text(eng, certified_only=True) == path.read_text()
    assert not hasattr(examples, 'ASKER') and 'analyst_enterprise' not in inspect.getsource(examples)
