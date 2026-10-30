"""The BigQuery sync's place (#95): where WAREHOUSE_BACKEND or RETRIEVAL_BACKEND is bigquery, every make target that loads
a graph runs kge/estate/load_bigquery.py on its hash after the pipeline and before it is marked LOADED, and make release
once the release is made, so the margin check make onboard runs next reads BigQuery as the graph holds it; and the improvement job's
candidate is synced before its margin. On the local stack the targets run what they ran before. kge (kge/cli/), which
each target calls, runs here with a stand-in for every module it runs, so it runs none; the improvement job's commands
are recorded by a stand-in too."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.improve import abtest
from kge.pipeline import envfile, profile

DEMO = Path(__file__).resolve().parents[2]
SYNC = "-m kge.estate.load_bigquery --hash"
HASH = "c" * 64


PARENT_MAKE = ("MAKELEVEL", "MAKEFLAGS", "MFLAGS")


def artifact(folder: Path = DEMO / "build/artifacts/meridian") -> str:
    return f"{folder / HASH}/"


def steps_of(monkeypatch, *args: str, compiled: str | None = None, **env: str) -> list[str]:
    """The modules kge <args> runs, in order, each with its arguments, as one line each; it must succeed."""
    from tests.unit.test_cli import plan
    code, ran = plan(monkeypatch, *args, compiled=compiled or artifact(), **env)
    assert code == 0
    return [" ".join(["-m", *step]) for step in ran]


def command(lines: list[str], module: str) -> str:
    """The one command line that runs the module."""
    found = [line for line in lines if f"-m {module} " in f"{line} "]
    assert len(found) == 1, lines
    return found[0]


def order(lines: list[str]) -> list[str]:
    """The steps the commands run, in order, by module."""
    return [line.split()[1] for line in lines]


@pytest.mark.parametrize("env", [{"WAREHOUSE_BACKEND": "bigquery"}, {"RETRIEVAL_BACKEND": "bigquery"}])
@pytest.mark.parametrize("target", ["load", "graph"])
def test_load_and_graph_sync_after_the_pipeline_and_before_the_graph_is_marked(monkeypatch, target, env):
    steps = order(steps_of(monkeypatch, target, f"HASH={HASH}", **env))
    at = steps.index("kge.estate.load_bigquery")
    assert steps[at - 1] == "kge.pipeline.run" and steps[at + 1] == "kge.graph.release"
    assert steps.count("kge.estate.load_bigquery") == 1


@pytest.mark.parametrize("env", [{"WAREHOUSE_BACKEND": "bigquery"}, {"RETRIEVAL_BACKEND": "bigquery"}])
def test_onboard_syncs_after_the_pipeline_and_before_the_graph_is_marked_and_checked(monkeypatch, tmp_path, env):
    """#95 item 2 for make onboard: the candidate's load, its pipeline, the sync, --mark-loaded, then the margin check,
    each once, so the margin reads BigQuery as the graph holds it."""
    reg = registry(tmp_path)
    steps = order(steps_of(monkeypatch, "onboard", f"PROFILES={reg}", compiled=artifact(tmp_path / "artifacts/meridian" / ("d" * 64)), **env))
    load = steps[steps.index("kge.graph.load"):]
    assert load[:5] == ["kge.graph.load", "kge.pipeline.run", "kge.estate.load_bigquery", "kge.graph.release", "kge.eval.margin"]
    assert steps.count("kge.estate.load_bigquery") == 1 and steps.count("kge.graph.load") == 1


@pytest.mark.parametrize("env", [{"WAREHOUSE_BACKEND": "bigquery"}, {"RETRIEVAL_BACKEND": "bigquery"}])
def test_release_syncs_the_hash_once_the_release_is_made(monkeypatch, env):
    """After kge/graph/release.py, once it has passed: a release it refuses leaves BigQuery's chunks as they were."""
    from tests.unit.test_cli import plan
    lines = steps_of(monkeypatch, "release", f"HASH={HASH}", **env)
    assert lines == [f"-m kge.graph.release --hash {HASH}", f"{SYNC} {HASH}"]
    assert order(steps_of(monkeypatch, "release", **env))[-2:] == ["kge.graph.release", "kge.estate.load_bigquery"]
    code, ran = plan(monkeypatch, "release", f"HASH={HASH}", codes={"kge.graph.release": 2}, **env)
    assert code == 2 and ran == [["kge.graph.release", "--hash", HASH]]


def test_make_release_is_one_call_to_kge(monkeypatch):
    """The Makefile's release, and every target that loads a graph, is one line that hands its settings to kge, which
    syncs: make adds nothing of its own, under a parent make or not."""
    text = (DEMO / "Makefile").read_text()
    for target in ("release", "load", "graph", "onboard", "$(GRAPH)"):
        body = text[text.index(f"\n{target}:") + 1:].split("\n\n", 1)[0].splitlines()[1:]
        assert body == [f"\t$(KGE) {'graph' if target == '$(GRAPH)' else target} $(KGE_ARGS)"], (target, body)


def unset_settings(monkeypatch) -> None:
    """Every setting unset for the test and put back after it, whatever .env (load_env, which writes os.environ itself)
    sets meanwhile: a name monkeypatch never set it does not restore, so one .env set would outlive the test."""
    from kge.pipeline.settings import ENVIRONMENT_NAMES
    for name in ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def make_words(monkeypatch, tmp_path, capsys, dotenv: str, **env: str) -> dict[str, str]:
    """kge/pipeline/profile.py --make's words, with dotenv as the demo's .env and env in the environment."""
    unset_settings(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    (tmp_path / ".env").write_text(dotenv)
    monkeypatch.setattr(envfile, "DEMO", tmp_path)
    monkeypatch.setattr(envfile, "_loaded", set())
    assert profile.main(["--make"]) == 0
    return dict(word.split("=", 1) for word in capsys.readouterr().out.split())


@pytest.mark.parametrize("dotenv", ["WAREHOUSE_BACKEND=bigquery\n", "RETRIEVAL_BACKEND=BigQuery  # vectors\n"])
def test_make_reads_a_backend_set_only_in_dot_env(monkeypatch, tmp_path, capsys, dotenv):
    """The steps load .env, which make does not: make asks profile --make, which reads the settings as they do (a review
    finding on #97), normalised as the factories normalise them."""
    assert make_words(monkeypatch, tmp_path, capsys, dotenv)["sync"] == "kge.estate.load_bigquery"


def test_the_environment_wins_over_dot_env_and_the_app_state_alone_syncs_nothing(monkeypatch, tmp_path, capsys):
    assert "sync" not in make_words(monkeypatch, tmp_path, capsys, "WAREHOUSE_BACKEND=bigquery\n",
                                             WAREHOUSE_BACKEND="duckdb")
    assert "sync" not in make_words(monkeypatch, tmp_path, capsys, "APP_STATE_BACKEND=bigquery\n")
    assert "sync" not in make_words(monkeypatch, tmp_path, capsys, "")


def test_kge_takes_the_decision_from_the_profile_with_its_own_overrides(monkeypatch):
    """A backend on the command line reaches the profile's words too, and a mixed-case value syncs as the factories
    read it."""
    assert "kge.estate.load_bigquery" in order(steps_of(monkeypatch, "release", f"HASH={HASH}", "WAREHOUSE_BACKEND=bigquery"))
    assert "kge.estate.load_bigquery" in order(steps_of(monkeypatch, "release", f"HASH={HASH}", WAREHOUSE_BACKEND=" BigQuery"))
    assert "kge.estate.load_bigquery" not in order(steps_of(monkeypatch, "release", f"HASH={HASH}", "WAREHOUSE_BACKEND=",
                                                            RETRIEVAL_BACKEND="duckdb"))


def test_the_graph_prerequisite_make_demo_builds_syncs_too(monkeypatch, tmp_path):
    """$(GRAPH), the current pointer make demo and make pipeline build when nothing is released, has the same order."""
    reg = registry(tmp_path)
    (tmp_path / "artifacts/meridian/current").unlink()
    from kge import cli
    monkeypatch.chdir(DEMO)
    monkeypatch.setenv("PROFILES", str(reg))
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    assert (DEMO / cli.Commands({}).get("GRAPH")).resolve() == tmp_path / "artifacts/meridian/current"
    steps = order(steps_of(monkeypatch, "pipeline", f"PROFILES={reg}", WAREHOUSE_BACKEND="bigquery"))
    load = steps[steps.index("kge.graph.load"):]
    assert load == ["kge.graph.load", "kge.pipeline.run", "kge.estate.load_bigquery", "kge.graph.release", "kge.graph.release",
                    "kge.pipeline.run"]


def test_the_load_steps_are_written_once():
    """kge load, graph, the graph prerequisite and onboard run one sequence, load_steps: the load is never copied, so a
    step added to it reaches every command that loads a graph."""
    from kge import cli
    text = Path(cli.__file__).read_text()
    assert text.count('"kge.graph.load", "--artifact"') == 1 and text.count('"--mark-loaded"') == 1
    assert text.count("self.load_steps(") == 3


@pytest.mark.parametrize("target", ["load", "graph", "release"])
def test_the_local_stack_runs_no_sync(monkeypatch, target):
    assert "kge.estate.load_bigquery" not in order(steps_of(monkeypatch, target, f"HASH={HASH}"))
    assert "kge.estate.load_bigquery" not in order(steps_of(monkeypatch, target, f"HASH={HASH}", WAREHOUSE_BACKEND="duckdb"))


def test_the_image_onboard_leaves_the_sync_to_make_load():
    """The entry script's onboard runs make onboard and make load, whose load steps sync: it calls the sync itself no more,
    so it never runs twice."""
    assert "kge.estate.load_bigquery" not in (DEMO / "entrypoint.sh").read_text()


def candidate(monkeypatch, **env):
    for name in ("WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    ran = []

    def run_cmd(args, prof, force_rules=False, ok_codes=(0,)):
        ran.append(args[0])
        return SimpleNamespace(stdout=f"/artifacts/meridian/{HASH}\n", stderr="", returncode=0)

    monkeypatch.setattr(abtest, "write_candidate_pack", lambda *a, **k: ("1.3.0", [{"proposer": "rules"}], []))
    monkeypatch.setattr(abtest, "sync_proposal_pack_to_store", lambda *a: None)
    monkeypatch.setattr(abtest, "write_proposal_record", lambda *a: None)
    monkeypatch.setattr(abtest, "_run_cmd", run_cmd)
    prof = SimpleNamespace(id="meridian", client="meridian", clients=Path("/clients"), identities=None,
                           artifacts=Path("/artifacts"))
    out = abtest.build_candidate(prof, "p1", [{"kind": "alias"}], {"base_pack_hash": "a" * 64, "base_version": "1.2.0", "served_hash": "b" * 64})
    assert out["candidate_hash"] == HASH
    return ran


@pytest.mark.parametrize("env", [{"WAREHOUSE_BACKEND": "bigquery"}, {"RETRIEVAL_BACKEND": "bigquery"}])
def test_the_improvement_jobs_candidate_is_synced_before_it_is_marked(monkeypatch, env):
    assert candidate(monkeypatch, **env) == ["kge.compiler.compile", "kge.graph.load", "kge.pipeline.run",
                                              "kge.estate.load_bigquery", "kge.graph.release"]


def test_the_improvement_jobs_candidate_is_not_synced_on_the_local_stack(monkeypatch):
    assert candidate(monkeypatch) == ["kge.compiler.compile", "kge.graph.load", "kge.pipeline.run", "kge.graph.release"]


def registry(tmp_path: Path) -> Path:
    """A registry of one profile whose current pointer names a released artifact, outside the demo's own build."""
    (tmp_path / "artifacts/meridian" / HASH).mkdir(parents=True)
    (tmp_path / "artifacts/meridian" / HASH / "manifest.json").write_text('{"client": "meridian"}')
    (tmp_path / "artifacts/meridian/current").symlink_to(HASH)
    reg = tmp_path / "profiles.yaml"
    reg.write_text("default: meridian\nprofiles:\n  meridian:\n    client: meridian\n"
                   f"    clients: {DEMO / 'clients'}\n    artifacts: artifacts\n    store: stores\n    landing: landing\n"
                   f"    warehouses: warehouses\n    identities: {DEMO / 'deploy/meridian/identities.yaml'}\n    baseline: null\n")
    return reg


def kge_variable(monkeypatch, name: str, *args: str, **env: str) -> str:
    """The value kge gives a setting as it starts, from the demo's folder: args are NAME=value as on its command line."""
    from kge import cli
    monkeypatch.chdir(DEMO)
    with monkeypatch.context() as m:
        for gone in ("PROFILE", *profile_reads(), *PARENT_MAKE):
            m.delenv(gone, raising=False)
        values = dict(arg.split("=", 1) for arg in args)
        for key, value in {**env, **values}.items():
            m.setenv(key, value)
        return cli.Commands(values).get(name)


def onboard(monkeypatch, capsys, reg: Path, **env: str) -> tuple[str, list[str]]:
    """kge onboard on the registry's client, its pack compiling to current's hash: what it said, and the modules it ran."""
    from tests.unit.test_cli import plan
    code, ran = plan(monkeypatch, "onboard", f"PROFILES={reg}", compiled=artifact(reg.parent / "artifacts/meridian"), **env)
    assert code == 0
    return capsys.readouterr().out, [step[0] for step in ran]


def profile_reads() -> list[str]:
    """The settings the Makefile passes to profile --make (PROFILE_READS)."""
    line = next(line for line in (DEMO / "Makefile").read_text().splitlines() if line.startswith("PROFILE_READS :="))
    return line.split(":=", 1)[1].split()


@pytest.mark.parametrize("given", ["command line", "environment"])
def test_kge_pipeline_graph_store_bigquery_names_the_bigquery_pointer(monkeypatch, tmp_path, given):
    """kge pipeline GRAPH_STORE=bigquery resolves GRAPH to the release's graph.bigquery, as its steps will (a review
    finding on #97): the graph store, and the registry, reach the profile from the command line or the environment."""
    reg = registry(tmp_path)
    on_line = [f"PROFILES={reg}", "GRAPH_STORE=bigquery"]
    args, env = (on_line, {}) if given == "command line" else ([], dict(word.split("=", 1) for word in on_line))
    graph = kge_variable(monkeypatch, "GRAPH", *args, **env)
    assert (DEMO / graph).resolve() == tmp_path / "stores/meridian" / HASH / "graph.bigquery"
    graph = kge_variable(monkeypatch, "GRAPH", f"PROFILES={reg}")
    assert (DEMO / graph).resolve() == tmp_path / "stores/meridian" / HASH / "graph.ladybug"


def test_the_makefile_passes_every_setting_profile_make_reads(monkeypatch, tmp_path, capsys):
    """Each setting whose value changes what profile --make prints is one the Makefile passes to it, so a value given on
    make's command line is the one its steps see; PROFILE is passed on its own, first."""
    reg = registry(tmp_path)
    monkeypatch.setattr(envfile, "DEMO", tmp_path)
    monkeypatch.setattr(envfile, "_loaded", set())
    from kge.pipeline.settings import ENVIRONMENT_NAMES
    unset_settings(monkeypatch)

    def says(**env: str) -> tuple[int, str]:
        with monkeypatch.context() as m:
            m.setenv("PROFILES", str(reg))
            for name, value in env.items():
                m.setenv(name, value)
            code = profile.main(["--make"])
            return code, capsys.readouterr().out

    plain = says()
    read = {name for name in ENVIRONMENT_NAMES if says(**{name: "bigquery"}) != plain}
    assert {"PROFILE", "PROFILES", "GRAPH_STORE", "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND"} <= read
    assert read - {"PROFILE"} <= set(profile_reads()), sorted(read)


def mark_current_loaded(tmp_path: Path, store: str, marked: str | None = None) -> None:
    """The registry's current graph made on store (Ladybug's file, or BigQuery's pointer naming a generation), its folder's
    LOADED marker holding marked's entry (none for None)."""
    folder = tmp_path / "stores/meridian" / HASH
    folder.mkdir(parents=True, exist_ok=True)
    graph = folder / f"graph.{store}"
    graph.write_text(json.dumps({"format": 1, "prefix": f"g{HASH[:16]}_1_"}) if store == "bigquery" else "")
    if marked:
        (folder / "LOADED").write_text(json.dumps({marked: {"client": "meridian", "model_hash": HASH}}))


@pytest.mark.parametrize("env", [{}, {"WAREHOUSE_BACKEND": "bigquery"}, {"GRAPH_STORE": "bigquery"},
                                 {"GRAPH_STORE": "bigquery", "RETRIEVAL_BACKEND": "bigquery"}])
def test_make_onboard_leaves_current_alone_when_it_is_loaded_on_this_store(monkeypatch, capsys, tmp_path, env):
    """make onboard of a pack that compiles to current loads nothing when current is LOADED on this graph store, on
    either store and whatever the warehouses: the graph being served is never reloaded or unmarked. The store says
    whether it is (profile --make's loaded=yes), never a backend's name in the Makefile."""
    reg = registry(tmp_path)
    store = env.get("GRAPH_STORE", "ladybug")
    mark_current_loaded(tmp_path, store, store)
    assert kge_variable(monkeypatch, "CURRENT_LOADED", f"PROFILES={reg}", **env) == "yes"
    said, steps = onboard(monkeypatch, capsys, reg, **env)
    assert said.endswith(f"onboard: candidate {HASH}\nonboard: current already\n")
    assert "kge.graph.load" not in steps and "kge.eval.margin" not in steps


@pytest.mark.parametrize("store, marked", [("ladybug", None), ("bigquery", None), ("ladybug", "bigquery"),
                                           ("bigquery", "ladybug")])
@pytest.mark.parametrize("env", [{}, {"WAREHOUSE_BACKEND": "bigquery"}])
def test_make_onboard_loads_current_again_when_it_is_not_loaded_on_this_store(monkeypatch, capsys, tmp_path, store, marked, env):
    """Current with no LOADED entry for this store (a load that did not finish, or a graph loaded on the other store)
    is loaded again, with the sync where the warehouses are BigQuery's."""
    reg = registry(tmp_path)
    mark_current_loaded(tmp_path, store, marked)
    env = {**env, "GRAPH_STORE": store}
    assert kge_variable(monkeypatch, "CURRENT_LOADED", f"PROFILES={reg}", **env) == ""
    said, steps = onboard(monkeypatch, capsys, reg, **env)
    assert "onboard: current already" not in said and f"onboard: [6/7] load       -> make load's steps on {HASH}" in said
    expected = ["kge.graph.load", "kge.pipeline.run", *(["kge.estate.load_bigquery"] if env.get("WAREHOUSE_BACKEND") else []),
                "kge.graph.release", "kge.eval.margin", "kge.estate.onboard"]
    assert steps[steps.index("kge.graph.load"):] == expected


BACKEND_NAMES = re.compile(r"bigquery|ladybug|kuzu|duckdb", re.I)
CONDITIONS = re.compile(r"\$\((if|filter|filter-out|findstring|or|and) |^\s*if(n?eq|n?def)\b|\btest\b|\[ |\bcase\b")


def test_the_makefile_decides_on_no_backend_name():
    """Make decides on what profile --make says (sync=, loaded=, graphfile=), never on a backend's or store's name: a
    line that tests, filters or picks holds no backend name of its own, once make's own variables are set aside."""
    found = []
    for number, line in enumerate((DEMO / "Makefile").read_text().splitlines(), 1):
        if line.lstrip().startswith("#") or not CONDITIONS.search(line):
            continue
        if BACKEND_NAMES.search(re.sub(r"\$\([A-Z_]+\)|^[A-Z_]+ [:?+]?=", "", line)):
            found.append(f"Makefile:{number}: {line.strip()}")
    assert not found, "\n".join(found)


@pytest.mark.parametrize("env", [{"GRAPH_STORE": "bigquery"}, {"GRAPH_STORE": "bigquery", "WAREHOUSE_BACKEND": "bigquery"}])
def test_make_onboard_on_bigquery_loads_a_current_only_ladybug_ever_loaded(monkeypatch, capsys, tmp_path, env):
    """The live run on #97: on GRAPH_STORE=bigquery, current's folder held Ladybug's graph and its LOADED entry from the
    local stack, and no BigQuery pointer, so BigQuery had no tables for it. That is not loaded on this store: onboard
    loads it (and syncs where the warehouses are BigQuery's), never skips on a LOADED marker that is another store's."""
    reg = registry(tmp_path)
    mark_current_loaded(tmp_path, "ladybug", "ladybug")
    assert not (tmp_path / "stores/meridian" / HASH / "graph.bigquery").exists()
    assert kge_variable(monkeypatch, "CURRENT_LOADED", f"PROFILES={reg}", **env) == ""
    _, steps = onboard(monkeypatch, capsys, reg, **env)
    sync = ["kge.estate.load_bigquery"] if env.get("WAREHOUSE_BACKEND") else []
    assert steps[steps.index("kge.graph.load"):steps.index("kge.eval.margin")] == ["kge.graph.load", "kge.pipeline.run", *sync,
                                                                                  "kge.graph.release"]
