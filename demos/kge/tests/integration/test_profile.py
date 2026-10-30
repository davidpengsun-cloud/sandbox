"""The deployment's profiles (issue #39 item 9): the registry, the profile a process serves, and what names it.

    .venv/bin/python -m pytest tests/integration/test_profile.py -q

deploy/profiles.yaml is deployment config: a default, and per profile id its
client and pointers only, each resolved relative to the file
(kge/pipeline/profile.py). A process resolves its profile once: the id it is given,
else PROFILE, else the registry's default; PROFILES names another registry,
which is how these tests point a process at a scratch copy. The registry is
checked whole before any profile is used: slugs, no root shared or nested, no
profile's file in another's roots, and the client of a profile's current
artifact. The API resolves its profile at import, and no request can name one;
the run config carries it, set by the process, and the run trace and the A/B
manifest record it. A baseline recorded on another profile is put beside
nothing, and make margin will not check against it. #38's baseline moved under
the client, byte for byte.

The API's tests run on a scratch copy; the rest read files, or run a process
of their own.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from kge.services.ask.engine import runtrace
from kge.services.ask.engine.core import PRESETS, STAGES, RunConfig
from kge.eval import margin, report
from kge.landing.zone import CLIENT_ID_RULE
from kge.pipeline.profile import ProfileError, resolve
from tests.scratch import BUILD, profile_of, scratch_build, write_registry

DEMO = BUILD.parent
REGISTRY = DEMO / "deploy" / "profiles.yaml"
B40 = DEMO / "clients" / "meridian" / "baselines" / "issue-40.json"
POINTERS = ("clients", "artifacts", "store", "landing", "warehouses", "identities", "baseline")


def python(code: str, env: dict) -> subprocess.CompletedProcess:
    """code in a process of its own, from the demo folder, with env laid over this one's (None removes a variable)."""
    full = {k: v for k, v in {**os.environ, **env}.items() if v is not None}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=DEMO, env=full, timeout=120)


def entry(client: str, **over) -> dict:
    """A profile's pointers, relative to the registry file as the deployment's are: its client's own folders."""
    return {"client": client, "clients": "../clients", "artifacts": "../build/artifacts", "store": "../build/stores",
            "landing": "../build/landing", "warehouses": f"../build/{client}/warehouses", "identities": f"{client}/identities.yaml",
            "baseline": None, **over}


def registry(tmp: Path, profiles: dict, default: str | None = None) -> Path:
    path = tmp / "deploy" / "profiles.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"default": default or next(iter(profiles)), "profiles": profiles}, sort_keys=False))
    return path


def two(tmp: Path, **beta) -> Path:
    """A registry of two profiles, alpha (the default) and beta, each its own client's."""
    return registry(tmp, {"alpha": entry("alpha"), "beta": entry("beta", **beta)})


def test_the_deployment_s_registry_holds_the_demo_s_profile_as_pointers_only():
    """The demo's client is the default; beside it are the fixture profile of #39's exit gate (tests/integration/test_profiles.py)
    and the client #40 onboarded from scratch (tests/unit/test_onboarding.py)."""
    raw = yaml.safe_load(REGISTRY.read_text())
    assert raw["default"] == "meridian" and list(raw["profiles"]) == ["meridian", "thornbury", "fellmoor"]
    held = raw["profiles"]["meridian"]
    assert list(held) == ["client", *POINTERS], "the fields the spec names, in its order"
    assert all(isinstance(held[k], str) and not Path(held[k]).is_absolute() for k in POINTERS), "pointers, relative to the file"
    p = resolve(registry=REGISTRY, profile_id="meridian")
    assert (p.id, p.client, p.registry) == ("meridian", "meridian", REGISTRY)
    assert (p.clients, p.artifacts, p.store, p.landing, p.warehouses) == (
        DEMO / "clients", BUILD / "artifacts", BUILD / "stores", BUILD / "landing", BUILD / "warehouses")
    assert p.identities == DEMO / "deploy" / "meridian" / "identities.yaml" and p.baseline == B40
    t = resolve(registry=REGISTRY, profile_id="thornbury")
    assert list(raw["profiles"]["thornbury"]) == ["client", *POINTERS]
    assert (t.client, t.clients, t.warehouses, t.baseline) == ("thornbury", DEMO / "clients", BUILD / "thornbury" / "warehouses", None)
    assert t.identities == DEMO / "deploy" / "thornbury" / "identities.yaml"
    f = resolve(registry=REGISTRY, profile_id="fellmoor")
    assert list(raw["profiles"]["fellmoor"]) == ["client", *POINTERS]
    assert (f.client, f.clients, f.warehouses, f.baseline) == ("fellmoor", DEMO / "clients", BUILD / "fellmoor" / "warehouses", None)
    assert f.identities == DEMO / "deploy" / "fellmoor" / "identities.yaml"


def test_a_profile_s_graph_overlay_and_run_state_sit_in_its_client_s_graph_store():
    p = resolve(registry=REGISTRY, profile_id="meridian")
    store = BUILD / "stores" / "meridian"
    assert p.at("ab12").graph_path() == store / "ab12" / "graph.ladybug", "the graph of an artifact, in its hash's folder"
    p = p.at(p.model_hash() or "ab12")
    assert p.graph_path() == store / p.model_hash() / "graph.ladybug"
    assert (p.overlay_dir(), p.run_dir()) == (store / "overlay", store / "run")
    assert p.roots() == {"pack": DEMO / "clients" / "meridian", "artifacts": BUILD / "artifacts" / "meridian", "store": store,
                         "landing": BUILD / "landing" / "meridian", "warehouses": BUILD / "warehouses"}
    paths = p.paths()
    for name, at in (("tokens", "tokens.json"), ("pid", "api.pid"), ("runs", "runs"), ("pipeline_stats", "pipeline_stats.json"),
                     ("eval", "eval.json"), ("principals", "principals.json")):
        assert paths[name] == store / "run" / at, name
    assert paths["graph"] == p.graph_path() and paths["overlay"] == p.overlay_dir() and paths["landed"] == BUILD / "landing" / "meridian" / "current"
    assert paths["pack"] == DEMO / "clients" / "meridian" / "pack.yaml" and paths["baseline"] == B40


def test_current_is_the_artifact_the_client_s_pointer_names_and_none_without_one(tmp_path):
    p = resolve("alpha", registry=two(tmp_path))
    assert p.current() is None and p.model_hash() is None, "no current pointer yet"
    folder = tmp_path / "build" / "artifacts" / "alpha" / "ab12"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"client": "alpha"}))
    (folder.parent / "current").symlink_to("ab12")
    p = resolve("alpha", registry=two(tmp_path))
    assert p.current() == folder and p.model_hash() == "ab12" and p.paths()["current"] == folder


def test_the_profile_is_the_one_given_else_profile_else_the_registry_s_default(tmp_path, monkeypatch):
    path = two(tmp_path)
    monkeypatch.delenv("PROFILE", raising=False)
    monkeypatch.delenv("PROFILES", raising=False)
    assert resolve(registry=path).id == "alpha", "the registry's default"
    monkeypatch.setenv("PROFILE", "beta")
    assert resolve(registry=path).id == "beta", "PROFILE"
    assert resolve("alpha", registry=path).id == "alpha", "the id given wins"
    monkeypatch.setenv("PROFILE", " ")
    assert resolve(registry=path).id == "alpha", "an empty PROFILE is none"
    monkeypatch.setenv("PROFILES", str(path))
    assert resolve().registry == path and resolve().id == "alpha", "PROFILES names the registry"
    assert resolve(registry=REGISTRY).id == "meridian", "a registry given wins"
    monkeypatch.delenv("PROFILES")
    assert resolve().registry == REGISTRY, "the deployment's, deploy/profiles.yaml"
    with pytest.raises(ProfileError) as exc:
        resolve("gamma", registry=path)
    assert str(exc.value) == f"no profile 'gamma' in {path}: the known profiles are alpha, beta"


def test_dot_env_s_profile_and_profiles_are_honoured_and_the_environment_wins(tmp_path):
    path = two(tmp_path)
    (tmp_path / ".env").write_text(f"PROFILES={path}\nPROFILE=beta\n")
    code = (f"from kge.pipeline import envfile; from pathlib import Path; envfile.DEMO = Path({str(tmp_path)!r})\n"
            "from kge.pipeline.profile import resolve; p = resolve(); print(p.id, p.registry)")
    assert python(code, {"PROFILE": None, "PROFILES": None}).stdout.split() == ["beta", str(path)]
    assert python(code, {"PROFILE": "alpha", "PROFILES": None}).stdout.split() == ["alpha", str(path)]


@pytest.mark.parametrize("change, said", [
    (lambda r: r["profiles"].update({"Beta": r["profiles"].pop("beta")}), "profile id 'Beta' is not a slug"),
    (lambda r: r["profiles"]["beta"].update(client="Beta"), "beta: client 'Beta' is refused: " + CLIENT_ID_RULE),
    (lambda r: r["profiles"]["beta"].pop("store"), "beta: no store"),
    (lambda r: r["profiles"]["beta"].update(store=""), "beta: store is not a path"),
    (lambda r: r["profiles"]["beta"].update(database="../db"), "beta: database is not a profile field"),
    (lambda r: r["profiles"]["beta"].update(graph_store="kuzu"),
     "beta: graph_store is retired, Ladybug being the only graph store (Kuzu was removed): remove the line, not 'kuzu'"),
    (lambda r: r.update(default="gamma"), "the default, 'gamma', is no profile"),
    (lambda r: r.update(profiles={}), "no profiles"),
    (lambda r: r["profiles"]["beta"].update(warehouses="../build/alpha/warehouses"),
     "beta's warehouses root {build}/alpha/warehouses is alpha's warehouses root"),
    (lambda r: r["profiles"]["beta"].update(warehouses="../build/stores/alpha/warehouses"),
     "beta's warehouses root {build}/stores/alpha/warehouses is inside alpha's store root {build}/stores/alpha"),
    (lambda r: r["profiles"]["alpha"].update(warehouses="../build/landing/alpha/warehouses"),
     "alpha's warehouses root {build}/landing/alpha/warehouses is inside alpha's landing root {build}/landing/alpha"),
    (lambda r: r["profiles"]["beta"].update(client="alpha"), "beta's pack root {clients}/alpha is alpha's pack root"),
    (lambda r: r["profiles"]["beta"].update(identities="../clients/alpha/identities.yaml"),
     "beta's identities {clients}/alpha/identities.yaml is inside alpha's pack root {clients}/alpha"),
    (lambda r: r["profiles"]["beta"].update(baseline="../build/stores/alpha/run/baseline.json"),
     "beta's baseline {build}/stores/alpha/run/baseline.json is inside alpha's store root {build}/stores/alpha"),
], ids=["id", "client", "missing", "empty", "unknown", "graph_store", "default", "none", "shared", "nested", "own-nested",
        "same-client", "identities", "baseline"])
def test_the_registry_is_refused_whole_naming_what_is_wrong(tmp_path, change, said):
    raw = yaml.safe_load(two(tmp_path).read_text())
    change(raw)
    path = registry(tmp_path, raw["profiles"], raw["default"])
    with pytest.raises(ProfileError) as exc:
        resolve("alpha", registry=path)
    want = said.format(build=(tmp_path / "build").resolve(), clients=(tmp_path / "clients").resolve())
    assert str(exc.value).startswith(f"the profiles registry {path} is refused: ") and want in str(exc.value), str(exc.value)


def test_a_profile_whose_current_artifact_is_another_client_s_is_refused(tmp_path):
    folder = tmp_path / "build" / "artifacts" / "alpha" / "ab12"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text(json.dumps({"client": "beta"}))
    (folder.parent / "current").symlink_to("ab12")
    with pytest.raises(ProfileError) as exc:
        resolve("beta", registry=two(tmp_path))
    assert f"alpha: its current artifact, {folder.resolve()}, is client beta's, not alpha's" in str(exc.value), str(exc.value)


def test_a_registry_that_is_not_yaml_or_not_there_is_refused(tmp_path):
    with pytest.raises(ProfileError, match="no profiles registry"):
        resolve(registry=tmp_path / "none.yaml")
    bad = tmp_path / "profiles.yaml"
    bad.write_text("default: [unclosed\n")
    with pytest.raises(ProfileError, match="cannot be read"):
        resolve(registry=bad)
    bad.write_text("- a list\n")
    with pytest.raises(ProfileError, match="is not a mapping of default and profiles"):
        resolve(registry=bad)


def test_the_profile_module_names_no_client():
    text = (DEMO / "kge" / "pipeline" / "profile.py").read_text().lower()
    assert "meridian" not in text and "thornbury" not in text, "platform code: the registry names the clients"


def test_a_command_that_names_no_principal_asks_as_the_querying_identity_the_deployment_names():
    """make eval, make agent and make ab asked as Meridian's analyst_enterprise, named in platform code, whatever the
    profile: Thornbury has no such principal, so every question was refused. The deployment names the querying identity a
    command asks as when it names none, asker in its identities, and the profile reads it."""
    assert resolve(registry=REGISTRY, profile_id="meridian").asker() == "analyst_enterprise", "as the golden answers assume"
    assert resolve(registry=REGISTRY, profile_id="thornbury").asker() == "actuary"
    for client, who in (("meridian", "analyst_enterprise"), ("thornbury", "actuary")):
        ids = yaml.safe_load((DEMO / "deploy" / client / "identities.yaml").read_text())
        assert ids["asker"] == who and who in {b["principal"] for b in ids["principals"]}


def test_with_no_asker_named_it_is_the_first_querying_identity_the_graph_load_bound_else_none(tmp_path):
    ids = tmp_path / "deploy" / "alpha" / "identities.yaml"
    ids.parent.mkdir(parents=True)
    ids.write_text(yaml.safe_dump({"client": "alpha", "principals": [{"principal": "tagger", "role": "tagger"},
                                                                     {"principal": "reader", "role": "reader"}]}))
    p = resolve(registry=registry(tmp_path, {"alpha": entry("alpha")}))
    assert p.asker() is None, "no asker named, and no graph loaded to say who queries"
    p.run_dir().mkdir(parents=True)
    (p.run_dir() / "principals.json").write_text(json.dumps([{"principal": "tagger", "processes": ["tagging"]},
                                                             {"principal": "reader", "processes": ["querying"]}]))
    assert p.asker() == "reader", "the access mirror the graph load wrote"
    ids.write_text(yaml.safe_dump({**yaml.safe_load(ids.read_text()), "asker": "tagger"}))
    assert p.asker() == "tagger", "the deployment's, as it names it: the loader refuses one that does not query"


def test_a_registry_s_graph_store_line_of_ladybug_or_null_is_read_and_ignored(tmp_path):
    """A registry written while there were two stores (the pilot's, in its object store, among them) still reads."""
    path = registry(tmp_path, {"alpha": entry("alpha", graph_store="ladybug"), "beta": entry("beta", graph_store=None)})
    assert resolve(registry=path, profile_id="alpha").client == "alpha"
    assert resolve(registry=path, profile_id="beta").client == "beta"


def test_the_profile_command_says_what_a_profile_points_at_and_names_the_known_ones_for_an_unknown_id():
    shown = subprocess.run([sys.executable, "-m", "kge.pipeline.profile"], capture_output=True, text=True, cwd=DEMO,
                           env={**os.environ, "PROFILE": "meridian"}, timeout=120)
    assert shown.returncode == 0, shown.stderr
    assert shown.stdout.startswith("profile meridian, client meridian, from deploy/profiles.yaml") and "build/stores/meridian" in shown.stdout
    bad = subprocess.run([sys.executable, "-m", "kge.pipeline.profile", "--profile", "nosuch"], capture_output=True, text=True, cwd=DEMO, timeout=120)
    assert bad.returncode == 2 and bad.stderr.strip() == f"profile: no profile 'nosuch' in {REGISTRY}: the known profiles are meridian, thornbury, fellmoor"


def test_make_passes_the_profile_and_reads_where_its_things_are():
    """The graph served is the current release's; with none released, make graph makes current. The baseline is
    issue-40.json, the profile's since #40 released AUM."""
    env = {k: v for k, v in os.environ.items() if k not in ("PROFILE", "PROFILES")}
    shown = subprocess.run(["make", "-s", f"PY={sys.executable}", "--eval",
                            "show: ; @echo $(PROFILE) $$PROFILE $(GRAPH) $(LANDED) $(PACK) $(BASELINE) $(RUNS) $(ARTIFACTS)", "show"],
                           capture_output=True, text=True, cwd=DEMO, env=env, timeout=120)
    assert shown.returncode == 0, shown.stderr
    h = resolve(registry=REGISTRY).model_hash()
    graph = f"build/stores/meridian/{h}/graph.ladybug" if h else "build/artifacts/meridian/current"
    assert shown.stdout.split() == ["meridian", "meridian", graph,
                                    "build/landing/meridian/current", "clients/meridian", "clients/meridian/baselines/issue-40.json",
                                    "build/stores/meridian/run/runs", "build/artifacts/meridian"]


def test_make_serve_stops_on_an_unknown_profile_naming_the_known_ones():
    """With PORT=0, were it to serve, it would take a free port of its own, never another's."""
    r = subprocess.run(["make", f"PY={sys.executable}", "serve", "PORT=0"], capture_output=True, text=True, cwd=DEMO,
                       env={**os.environ, "PROFILE": "nosuch"}, timeout=120)
    assert r.returncode != 0
    assert f"profile: no profile 'nosuch' in {REGISTRY}: the known profiles are meridian, thornbury, fellmoor" in r.stderr, r.stdout + r.stderr
    assert "uvicorn" not in r.stdout.split("profile.py", 1)[-1], "it stops before serving"


def test_the_env_example_and_the_makefile_pass_the_profile():
    from kge.pipeline.envfile import parse
    values = parse((DEMO / ".env.example").read_text())
    assert values["PROFILE"] == "" and "PROFILES" in values, "the registry's default unless set"
    make = (DEMO / "Makefile").read_text()
    assert "export PROFILE" in make and "CLIENT ?=" not in make and "kge/pipeline/profile.py --make" in make


def test_every_command_takes_the_profile():
    folders = ("services", "semantics", "compiler", "estate", "eval", "graph", "landing", "pipeline")
    clis = sorted(p for p in [*DEMO.glob("*.py"), *(f for d in folders for f in (DEMO / "kge" / d).rglob("*.py"))]
                  if "argparse.ArgumentParser(" in p.read_text())
    assert len(clis) >= 17
    for p in clis:
        assert 'add_argument("--profile"' in p.read_text(), p.relative_to(DEMO)


def test_the_run_trace_has_no_profile_of_its_own_and_the_api_no_build():
    assert not hasattr(runtrace, "DEFAULT_PROFILE") and not hasattr(runtrace, "profile")
    app = (DEMO / "kge" / "app.py").read_text()
    assert "DEMO_BUILD" not in app and "BUILD =" not in app and "PROFILE = resolve()" in app


def test_the_api_will_not_start_on_an_unknown_profile():
    r = python("import kge.app", {"PROFILE": "nosuch"})
    assert r.returncode != 0 and f"no profile 'nosuch' in {REGISTRY}: the known profiles are meridian, thornbury, fellmoor" in r.stderr


def test_the_profile_is_the_run_config_s_and_no_stage():
    assert "profile" not in STAGES and "profile" not in PRESETS["graph"].stages()
    assert RunConfig("graph", "glossary", "graph", "planner", "shelves", "template").profile == ""
    with pytest.raises(ValueError, match="profile is no stage: the process sets it"):
        PRESETS["graph"].with_stages(profile="other")
    assert replace(PRESETS["graph"], profile="x").label == "graph", "it is no stage, so no label names it"


@pytest.fixture()
def served(tmp_path, monkeypatch):
    """The API on a scratch copy run as profile harbour, the demo's client under another profile id."""
    import kge.app as appmod
    from kge.services.identity.service import token_for
    build = scratch_build(tmp_path / "build")
    write_registry(build, profile="harbour")
    p = profile_of(build)
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("API_URL", "http://127.0.0.1:8211")
    for name, value in (("PROFILE", p), ("_engine", None), ("_combined", None), ("_agent", None), ("_ids", None)):
        monkeypatch.setattr(appmod, name, value)
    with TestClient(appmod.app) as c:
        c.as_ = lambda who: {"authorization": f"Bearer {token_for(p.run_dir(), who)}"}
        c.profile, c.app_module, c.pid_while_serving = p, appmod, (p.run_dir() / "api.pid").read_text()
        c.serving = json.loads((p.run_dir() / "api.json").read_text()) if (p.run_dir() / "api.json").is_file() else None
        yield c
    appmod._engine.close() if appmod._engine else None


Q = "Total retail revenue for June 2025"


def test_a_stages_override_that_names_the_profile_is_a_400(served):
    who = served.as_("analyst_enterprise")
    for path in ("/answer", "/ask"):
        r = served.post(path, json={"question": Q, "stages": {"profile": "meridian"}}, headers=who)
        assert r.status_code == 400 and "run_trace" not in r.json(), (path, r.text)
        assert r.json()["error"] == "profile is no stage: the process sets it, chosen at deployment (PROFILE); no request names one"
    assert served.post("/answer", json={"question": Q, "profile": "meridian"}, headers=who).status_code == 422, "nor the body"


def test_the_trace_names_the_process_s_profile_and_the_artifact(served):
    a = served.post("/answer", json={"question": Q}, headers=served.as_("analyst_enterprise")).json()
    rt = a["run_trace"]
    held = served.app_module.engine().g.q("MATCH (m:GraphModel) RETURN m.id AS id")[0]["id"]
    assert rt["profile"] == "harbour" and rt["graph"]["artifact"] == held and a["behaviour"] == "figure"


def test_the_api_keeps_its_run_state_in_the_profile_s_run_folder(served):
    run = served.profile.run_dir()
    assert served.pid_while_serving == str(os.getpid()) and (run / "tokens.json").is_file()
    assert served.serving == {"pid": os.getpid(), "profile": "harbour", "url": "http://127.0.0.1:8211"}, \
        "beside its pid, the profile it serves and its address, for the locked-graph message"
    assert served.profile.paths()["serving"] == run / "api.json"
    assert served.get("/ab").json() == {"runs": [], "baseline": "issue-40.json"}
    assert not (served.profile.estate() / "tokens.json").exists() and not (served.profile.estate() / "api.pid").exists()


def test_the_engine_runs_every_config_as_its_profile_s_and_refuses_another_s(served):
    eng = served.app_module.engine()
    t = runtrace.run("answer", lambda: eng.answer(Q, "analyst_enterprise", config=PRESETS["catalog"]), graph=eng.g)
    assert t["run_trace"]["profile"] == "harbour"
    with pytest.raises(ValueError, match="this process serves profile harbour, not other"):
        eng.answer(Q, "analyst_enterprise", config=replace(PRESETS["graph"], profile="other"))


def test_the_ab_manifest_records_the_profile_and_its_runs_land_in_its_run_folder(served):
    from kge.eval import harness
    eng = served.app_module.engine()
    run = harness.run(served.profile, harness.Arms(eng, llm=None), ["control"], ["golden"], 1, items=["G001"], echo=lambda *_: None)
    assert run.parent == served.profile.run_dir() / "runs"
    assert json.loads((run / "manifest.json").read_text())["profile"] == "harbour"
    assert [r["run_id"] for r in served.get("/ab").json()["runs"]] == [run.name]


def ran(profile: str) -> dict:
    return {"profile": profile, "principal": "analyst_enterprise", "arms": [{"arm": a, "mode": "rules"} for a in ("control", "treatment")],
            "sets": {"golden": {"version": "v2", "sha256": "g" * 64, "items": 45, "size": 45}}}


def test_the_report_puts_no_arm_beside_a_baseline_of_another_profile():
    b = json.loads(B40.read_text())
    far, why = report.apart(ran("harbour"), b)
    assert far == {"control", "treatment"}
    assert why == "this run is profile `harbour`'s, the baseline profile `meridian`'s (each profile is its own client's graph and data)"
    got = report.margin_beside(ran("harbour"), {"golden": {"all": {}}}, b)
    assert got["compared"] == {} and "this run is profile harbour's, the baseline profile meridian's" in got["changed"]["golden"]
    assert report.apart(ran("meridian"), b)[0] == set(), "the same profile: like with like"


def test_make_margin_will_not_check_against_a_baseline_of_another_profile(tmp_path, capsys, monkeypatch):
    other = tmp_path / "other.json"
    other.write_text(json.dumps({**json.loads(B40.read_text()), "profile": "harbour"}))
    monkeypatch.delenv("PROFILES", raising=False)
    monkeypatch.setenv("PROFILE", "meridian")
    assert margin.main(["--baseline", str(other), "--repeats", "1"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == (f"margin: cannot check against {other}: it is profile harbour's baseline, and this process serves "
                           "profile meridian; each profile is put beside its own"), err


def test_38_s_baseline_moved_under_the_client():
    assert not (DEMO / "kge" / "eval" / "baseline.json").exists()
    b = json.loads((DEMO / "clients" / "meridian" / "baselines" / "issue-38.json").read_text())
    assert b["run_id"] == "20261011T081011Z-66c499" and b["profile"] == "meridian"
    allow = (DEMO.parents[1] / "tools" / "disclosure-allow.txt").read_text()
    assert "kge/eval/baseline.json" not in allow and allow.count("demos/kge/clients/meridian/baselines/issue-38.json |") == 2


def test_the_readme_says_how_the_profile_is_chosen_and_where_its_things_are():
    readme = (DEMO / "README.md").read_text()
    para = " ".join(readme.split("**The profile.**", 1)[1].split("\n\n", 1)[0].split())
    for needed in ("`PROFILE`", "`deploy/profiles.yaml`", "`kge/pipeline/profile.py`", "`PROFILE=nosuch make serve`", "a 400",
                   "`build/stores/meridian/`", "`make profile`", "`--profile <id>`", "`PROFILES=<file>`",
                   "`.venv/bin/python -m pytest tests/integration/test_profile.py -q`"):
        assert needed in para, needed
    running = readme.split("## Running it", 1)[1].split("```", 2)[1]
    assert "make profile" in running and "build/stores/meridian/<model_hash>/graph.ladybug" in running


def test_the_readme_says_make_clean_deletes_all_of_build_for_every_profile_steward_decisions_included():
    """make clean is rm -rf build: every profile's estate, landing, artifacts and graph stores, and the steward decisions'
    journals with their archives, which the overlay otherwise keeps through every load. The README says so where it names
    make clean, and does not cast it as clearing a build's old files alone."""
    make = (DEMO / "Makefile").read_text()
    assert "\nBUILD := build\n" in make and make.split("\nclean:\n", 1)[1].split("\n\n", 1)[0].split() == ["rm", "-rf", "$(BUILD)"]
    from kge.pipeline import profile
    for p in profile.read(REGISTRY)[1].values():
        for name in ("artifacts", "landing", "estate", "overlay", "run"):
            assert (p.paths() | {"overlay": p.overlay_dir(), "run": p.run_dir()})[name].resolve().is_relative_to(DEMO / "build"), (p.id, name)
    readme = (DEMO / "README.md").read_text()
    para = " ".join(readme.split("**The profile.**", 1)[1].split("\n\n", 1)[0].split())
    assert "`make clean` clears the old files" not in para
    said = para.split("`make clean`", 1)[1].split(". ", 1)[0]
    for needed in ("the whole of `build/`", "every profile", "steward decisions", "archive"):
        assert needed in said, needed
    running = readme.split("## Running it", 1)[1].split("```", 2)[1]
    line = next(x for x in running.splitlines() if x.startswith("make clean "))
    assert "build/" in line and "steward decisions" in line, line
