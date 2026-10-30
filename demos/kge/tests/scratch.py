"""The demo's build for the tests: its graph opened in-process, and a throwaway copy for tests that write to it.

Every command and the engine run for a profile (kge/pipeline/profile.py), so a
build is run through a profiles registry: the deployment's,
deploy/profiles.yaml, for the demo's own build, and one of its own, written
into it (write_registry), for a scratch copy. profile_of(build) is the profile
a build is run as, and script passes its registry to a command as PROFILES,
the registry's test seam.

open_engine opens a build's graph, and fails the test, never skips it, when
the API holds it: the graph store lets one process open the graph at a time,
and a run beside the API would otherwise pass with its graph tests skipped.
THE_BUILD is the demo's own build, which the deployment's registry points
into, even where a test stands BUILD elsewhere.

scratch_build links the warehouses and flat files and copies the graph of the
store this run is on (kge/adapters/graphstore/), with its sidecars, so a steward
decision or a pipeline run in a test never touches the demo's own graph. The
client's graph store, build/stores/<client>/, holds a graph per compiled
artifact, in a folder named by its model hash, and the runtime overlay that
goes with them (kge/pipeline/overlay.py): the graph of the artifact the client's
current pointer names, with its LOADED marker for this store, and the overlay
are copied, never linked, so a test's decision is journaled in the copy and
never in the demo's own. The artifacts are a folder of the copy's own, the
current one linked into it and current pointing at it, so a test that compiles,
loads or releases there never moves the demo's current pointer. An overlay's
archive, the journals make reset-overlay set aside, is the demo's history and
not the graph's, so it is left out, and so is the run state (run/: tokens, the
API's pid, A/B runs, stats), which a scratch copy makes its own, but for the
access mirror the graph load wrote (principals.json). The other store's graph,
if the build has one, is left out; with graph=False no graph is copied, and no
overlay either, for a test that loads one of its own into a fresh store. The
landing zone is linked like the rest: a test that lands a release of its own
replaces the link with a folder first.

compile_pack compiles a pack as make compile does, and load_artifact loads an
artifact into a build's graph as make graph does, each in a process of its own,
then points the build's current at it, as make graph's release does (the
pipeline is the test's to run, so the graph is loaded but not LOADED).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / "build"
DEMO = BUILD.parent
THE_BUILD = DEMO / "build"
REGISTRY = DEMO / "deploy" / "profiles.yaml"
CLIENT = "meridian"
OWN = "profiles.yaml"


def registry(build: Path | None = None) -> Path:
    """The profiles registry build (default BUILD) is run with: the deployment's for the demo's own, else the one written
    into it."""
    build = Path(BUILD if build is None else build)
    return REGISTRY if build == THE_BUILD else build / OWN


def profile_of(build: Path | None = None):
    """The profile build is run as: its registry's default, whatever PROFILE says (script runs a command so)."""
    return default_profile(registry(build))


def graph_of(build: Path | None = None) -> Path:
    """The graph build's profile serves, for the store this run is on: graph.<store> in the folder of its client's graph
    store that the current artifact's model hash names. ProfileError if build has released none."""
    return profile_of(build).graph_path()


def run_of(build: Path | None = None) -> Path:
    """The run state of build's profile: its tokens, the API's pid, its A/B runs, the pipeline's stats, the eval."""
    return profile_of(build).run_dir()


@contextmanager
def running_as(build: Path):
    """PROFILES set to build's registry, and PROFILE to none, while a command's main runs in this process."""
    held = {k: os.environ.get(k) for k in ("PROFILES", "PROFILE")}
    os.environ["PROFILES"], os.environ["PROFILE"] = str(registry(build)), ""
    try:
        yield
    finally:
        for k, v in held.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def default_profile(path: Path):
    """The default profile of the registry at path, checked whole."""
    from kge.pipeline.profile import read
    default, held = read(path)
    return held[default]


def write_registry(build: Path, profile: str | None = None, **pointers) -> Path:
    """build's own registry, profiles.yaml in it: the demo's profile (its client, its graph store) under profile (default:
    the demo's id), with each pointer into the demo's build moved into build, relative to the file, so a copy of build
    takes its registry with it, and the rest (its clients, identities and baseline) as they are; pointers replaces any,
    a path given as it is."""
    import yaml
    demo = default_profile(REGISTRY)

    def moved(p: Path | None) -> str | None:
        if p is None:
            return None
        return p.relative_to(THE_BUILD).as_posix() if THE_BUILD in p.parents else str(p)
    entry = {"client": demo.client, "clients": moved(demo.clients), "artifacts": moved(demo.artifacts), "store": moved(demo.store),
             "landing": moved(demo.landing), "warehouses": moved(demo.warehouses), "identities": moved(demo.identities),
             "baseline": moved(demo.baseline),
             **{k: (str(v) if isinstance(v, Path) else v) for k, v in pointers.items()}}
    pid = profile or demo.id
    path = Path(build) / OWN
    path.write_text(yaml.safe_dump({"default": pid, "profiles": {pid: entry}}, sort_keys=False))
    return path


def open_engine(build: Path | None = None):
    """The engine on build's graph; a failed test, naming the API's process, if the API holds it. Any other error opening
    the graph is raised as itself. It imports pytest and the engine when called, so scratch_build needs neither. The
    failure is raised outside the except, so it gives the reason alone, unchained."""
    import pytest

    from kge.services.ask.engine.core import Engine
    from kge.adapters.graphstore import GraphLocked
    try:
        return Engine(profile_of(build))
    except GraphLocked as exc:
        held = exc
    pytest.fail(f"graph is locked, stop the API first: {held}", pytrace=False)


RUN_STATE = ("tokens.json", "api.pid", "api.json", "eval.json", "pipeline_stats.json", "runs")


def scratch_build(dest: Path, graph: bool = True) -> Path:
    """A copy of the demo's build (BUILD) at dest, run through a registry of its own (write_registry). Returns dest.

    What an older build kept at its top, its graphs among it, is skipped with the run state."""
    from kge.graph.release import loaded, write_marker
    from kge.pipeline.overlay import ARCHIVE
    from kge.adapters.graphstore import STORE, sidecars
    demo = profile_of(BUILD)
    store = demo.store / demo.client
    home = demo.artifacts / demo.client
    current = demo.current()
    dest.mkdir(parents=True, exist_ok=True)
    for p in BUILD.iterdir():
        if p.name in RUN_STATE or p.name.startswith("graph.") or p.name == OWN:
            continue
        if p.name == "artifacts":
            (dest / p.name).mkdir()
            for a in p.iterdir():
                if a != home:
                    (dest / p.name / a.name).symlink_to(a)
            mine = dest / home.relative_to(BUILD)
            mine.mkdir()
            if current is not None:
                (mine / current.name).symlink_to(current)
                (mine / "current").symlink_to(current.name)
        elif p.name != "stores":
            (dest / p.name).symlink_to(p)
    mine = dest / store.relative_to(BUILD)
    if graph and store.is_dir() and current is not None:
        path = demo.graph_path()
        (mine / current.name).mkdir(parents=True)
        for p in [path, *sidecars(path)]:
            if p.exists():
                (shutil.copytree if p.is_dir() else shutil.copy2)(p, mine / current.name / p.name)
        certification = path.parent / "certification"
        if certification.is_dir():
            shutil.copytree(certification, mine / current.name / "certification")
        if loaded(path) is not None:
            write_marker(mine / current.name, {STORE: loaded(path)})
        if (store / "overlay").is_dir():
            shutil.copytree(store / "overlay", mine / "overlay", symlinks=True,
                            ignore=lambda folder, names: [ARCHIVE] if ARCHIVE in names else [])
        if (store / "run" / "principals.json").is_file():
            (mine / "run").mkdir()
            shutil.copy2(store / "run" / "principals.json", mine / "run" / "principals.json")
    write_registry(dest)
    return dest


def script(path: str, *args, build: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """A demo module run as a script (python -m), from the demo folder, on the store this run is on, run for build's
    profile (PROFILES). path names the module (kge.graph.load) or its file (kge/graph/load.py)."""
    module = path.removesuffix(".py").replace("/", ".")
    return subprocess.run([sys.executable, "-m", module, *map(str, args)], capture_output=True, text=True, cwd=DEMO,
                          env={**os.environ, "PROFILES": str(registry(build)), "PROFILE": "",
                               **(env or {})})


def stand_in(build: Path, model_hash: str = "c0ffee") -> Path:
    """build's current pointer at an artifact folder of its own that holds only a manifest naming its client, for a test
    that makes a graph by hand: the profile then names the graph's place in its graph store. That graph's path."""
    import json
    from kge.graph.release import swap
    prof = profile_of(build)
    folder = prof.artifacts / prof.client / model_hash
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"client": prof.client, "model_hash": model_hash}) + "\n")
    swap(prof, model_hash)
    return profile_of(build).graph_path()


def compile_pack(out: Path, clients: Path = DEMO / "clients", client: str = CLIENT) -> Path:
    """client's pack compiled into out (kge/compiler/compile.py, as make compile runs it): the artifact folder it printed."""
    r = script("kge/compiler/compile.py", "--client", client, "--clients", clients, "--out", out)
    if r.returncode != 0:
        raise RuntimeError(f"the pack did not compile: {r.stderr}")
    return Path(r.stdout.strip())


def load_artifact(build: Path, artifact: Path, *more, env: dict | None = None) -> subprocess.CompletedProcess:
    """kge/graph/load.py on artifact into build's graph store, for build's profile, as make graph runs it; then, if it loaded,
    build's current pointed at it, as make graph's release does, the artifact linked into build's artifacts if it is not
    there. The build's artifacts must be its own folder (scratch_build's), never a link to another build's."""
    import json
    from kge.graph.release import swap
    r = script("kge/graph/load.py", "--artifact", artifact, *more, build=build, env=env)
    if r.returncode == 0:
        prof = profile_of(build)
        assert not any(p.is_symlink() for p in (prof.artifacts, prof.artifacts / prof.client)), "a build's own artifacts"
        model = json.loads((Path(artifact) / "manifest.json").read_text())["model_hash"]
        held = prof.artifacts / prof.client / model
        if not held.exists():
            held.parent.mkdir(parents=True, exist_ok=True)
            held.symlink_to(Path(artifact).resolve())
        swap(prof, model)
    return r
