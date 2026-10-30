"""The new role queries the serving graph, and a pack authored for 1.3 still compiles and serves."""
import json
import shutil

import yaml

from kge.estate.onboard import permissions

from tests.scratch import DEMO, compile_pack, load_artifact, open_engine, scratch_build, script, write_registry


def test_engineer_queries_the_serving_graph(eng):
    assert eng.g.may("knowledge_engineer", "querying")
    assert not eng.g.may("knowledge_engineer", "certification")
    for process in ("drafting", "building", "improving"):
        assert eng.g.may("knowledge_engineer", process)
    can = permissions(eng.profile, "knowledge_engineer")
    assert all(can[action] for action in ("create", "draft", "run", "improve"))
    question = "Total retail revenue for June 2025"
    engineer = eng.answer(question, principal="knowledge_engineer")
    analyst = eng.answer(question, principal="analyst_enterprise")
    assert engineer["behaviour"] == analyst["behaviour"]
    assert engineer["figure"] == analyst["figure"]


def test_a_1_3_pack_compiles_loads_and_serves(tmp_path):
    clients = tmp_path / "clients"
    shutil.copytree(DEMO / "clients/meridian", clients / "meridian")
    path = clients / "meridian/pack.yaml"
    pack = yaml.safe_load(path.read_text())
    pack["manifest"]["meta_model_version"] = "1.3.0"
    pack["manifest"]["platform"]["meta_model"] = "^1.3"
    pack["schema"]["meta_model"] = "1.3"
    pack["access"]["roles"] = [r for r in pack["access"]["roles"] if r["id"] != "knowledge_engineer"]
    for role in pack["access"]["roles"]:
        role["processes"] = [p for p in role["processes"] if p not in {"drafting", "building", "improving"}]
    path.write_text(yaml.safe_dump(pack, sort_keys=False))
    identities = yaml.safe_load((DEMO / "deploy/meridian/identities.yaml").read_text())
    identities["principals"] = [p for p in identities["principals"] if p["role"] != "knowledge_engineer"]
    identity_path = tmp_path / "identities.yaml"
    identity_path.write_text(yaml.safe_dump(identities))
    art = compile_pack(tmp_path / "compiled", clients)
    assert json.loads((art / "manifest.json").read_text())["meta_model_version"] == "1.3.0"
    build = scratch_build(tmp_path / "build", graph=False)
    write_registry(build, clients=str(clients), identities=str(identity_path))
    loaded = load_artifact(build, art)
    assert loaded.returncode == 0, loaded.stdout + loaded.stderr
    piped = script("kge.pipeline.run", build=build, env={"MODEL_PROVIDER": "none"})
    assert piped.returncode == 0, piped.stdout + piped.stderr
    engine = open_engine(build)
    try:
        answer = engine.answer("Total retail revenue for June 2025", principal="analyst_enterprise")
        assert answer["behaviour"] == "figure"
        assert answer["figure"] > 0
        assert engine.g.may("business_team", "querying")
        assert engine.g.may("steward_glossary", "certification")
    finally:
        engine.close()
