"""kge: every step a person runs, without make or a shell.

    kge <command> [<command> ...] [NAME=value ...]
    python -m kge.cli <command> [NAME=value ...]

Each command does what the Makefile's target of the same name did: the same defaults, conditionals, output and exit
codes, the Makefile now a one-line call to it per target. A setting is read as make read it: NAME=value on the command
line, then the environment, then the Makefile's default; the paths make took from kge/pipeline/profile.py --make (the
pack, the estate, the landed release, the graph store, the runs, the artifacts, current, the graph) are read here in
process, once as the command starts, and the environment does not override them, as it did not override make's. The
Makefile includes no .env, and neither does this: the steps and the profile load it themselves. Each step is a Python
module run with this interpreter, or npm and Node.js for the portal, from argument lists; a step's own exit code is the
command's, where make said 2 for every failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path

from kge.pipeline import profile
from kge.pipeline.onboard_steps import ONBOARD_STEPS

COMMANDS = (
    "estate", "seed", "check", "compile", "load", "margin", "onboard", "sign-off", "release", "passwords", "serve",
    "improve", "ab", "baseline", "reset-overlay", "source", "rows", "draft", "accept", "certify-draft", "onboard-status",
    "reset-onboard", "client", "reset-client", "demo", "graph", "pipeline", "lock", "certify", "check-estate", "eval",
    "profile", "portal", "portal-build", "access", "setup-client", "eval-pre", "tagger-eval",
)
FIXED = frozenset({"PACK", "ESTATE_DIR", "LANDED", "STORE", "RUNS", "ARTIFACTS", "CURRENT", "CURRENT_LOADED", "GRAPH",
                   "SUMMARY", "BUILD", "PASSWORDS_FILE", "PORTAL_SYNC_FILE", "SYNC"})
DEFAULTS = {
    "PY": "python3", "HOST": "127.0.0.1", "PORT": "8000", "ARM": "all", "REPEATS": "3", "ARMS": "all", "SETS": "all",
    "ITEM": "all",
}
WORDS = {"PACK": "pack", "ESTATE_DIR": "estate", "LANDED": "landed", "STORE": "store", "RUNS": "runs",
         "ARTIFACTS": "artifacts", "CURRENT": "current", "CURRENT_LOADED": "loaded", "SYNC": "sync"}


class Failed(subprocess.CalledProcessError):
    """A step that stopped the command, with the exit code the command ends with."""

    def __init__(self, code: int, step: str = ""):
        super().__init__(code, step)


def parse(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """The commands, in order, and the NAME=value settings, the last of a name winning; exit 2 on anything else."""
    parser = argparse.ArgumentParser(prog="kge", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("arguments", nargs="+", metavar="COMMAND|NAME=value")
    commands, values = [], {}
    for arg in parser.parse_args(argv).arguments:
        name, equals, value = arg.partition("=")
        if equals:
            if not name or any(c.isspace() for c in name):
                parser.error(f"invalid setting name: {name!r}")
            values[name] = value
        elif arg in COMMANDS or arg == "_stored-after":
            commands.append(arg)
        else:
            parser.error(f"unknown command {arg!r}; choose from: {', '.join(COMMANDS)}")
    if not commands:
        parser.error("a command is required")
    return commands, values


def profile_words(model_hash: str | None = None) -> dict[str, str]:
    """What kge/pipeline/profile.py --make says, as a dict, in process; empty where the profile cannot be read, as the
    Makefile's were, so each step says why itself."""
    try:
        p = profile.resolve()
        from kge.adapters import sync_step
        from kge.adapters.graphstore import graph_path, loaded, store_name
        paths, shown = p.paths(), profile.shown
        checked = model_hash or p.model_hash()
        words = {"id": p.id, "client": p.client, "pack": shown(p.clients / p.client), "estate": shown(p.estate()),
                 "landed": shown(paths["landed"]), "store": shown(p.store / p.client), "runs": shown(paths["runs"]),
                 "baseline": shown(p.baseline) if p.baseline else "", "artifacts": shown(paths["artifacts"]),
                 "current": p.model_hash() or "", "sync": sync_step() or "",
                 "loaded": "yes" if checked and loaded(graph_path(p.store / p.client / checked)) else "",
                 "graphfile": f"graph.{store_name()}"}
    except Exception:
        return {}
    return {name: value for name, value in words.items() if value}


def say(*args, err: bool = False):
    print(*args, file=sys.stderr if err else sys.stdout, flush=True)


class Commands:
    """One invocation: its settings, the profile's words read once as it starts, and the file prerequisites made."""

    def __init__(self, values: dict[str, str], environment: dict[str, str] | None = None):
        self.explicit = dict(values)
        self.environment = dict(os.environ if environment is None else environment)
        self.words = profile_words()
        self.made: set[str] = set()

    def fresh(self) -> Commands:
        """A new invocation with the same command-line settings and environment, .env's left out as make left them out:
        what a sub-make of the Makefile was."""
        return Commands(self.explicit, self.environment)

    def get(self, name: str) -> str:
        if name in self.explicit:
            return self.explicit[name]
        if name not in FIXED and name in self.environment:
            return self.environment[name]
        return self.default(name)

    def default(self, name: str) -> str:
        if name in WORDS:
            return self.words.get(WORDS[name], "")
        if name in DEFAULTS:
            return DEFAULTS[name]
        get = self.get
        if name == "BUILD":
            return "build"
        if name == "GRAPH":
            if not get("STORE"):
                return ""
            return (f"{get('STORE')}/{get('CURRENT')}/{self.words.get('graphfile', '')}" if get("CURRENT")
                    else f"{get('ARTIFACTS')}/current")
        if name == "SUMMARY":
            return f"{get('ESTATE_DIR')}/summary.json" if get("ESTATE_DIR") else ""
        if name == "PASSWORDS_FILE":
            return f"{get('STORE')}/run/passwords.json" if get("STORE") else ""
        if name == "PORTAL_SYNC_FILE":
            return f"{get('STORE')}/run/portal_sync_token" if get("STORE") else f"{get('BUILD')}/portal/run/portal_sync_token"
        if name == "BASELINE":
            return self.words.get("baseline", "")
        if name == "ESTATE":
            return f"{get('PACK')}/baselines/estate-4d9d5c9.json"
        if name == "BEFORE":
            return f"{get('PACK')}/baselines/issue-38.json"
        if name == "MARGIN_STATUS":
            return str(Path(get("RUNS")).parent / "margin.status") if get("RUNS") else "margin.status"
        if name == "API":
            return f"http://{get('HOST')}:{get('PORT')}"
        if name == "KGE_URL":
            return f"http://127.0.0.1:{get('PORT')}"
        if name == "PORTAL_PORT":
            try:
                return str(int(get("PORT")) + 90)
            except ValueError:
                return ""
        return ""

    def given(self, name: str) -> bool:
        """Whether a setting is given, as make's $(if ...) asks: anything but blanks."""
        return bool(self.get(name).strip())

    def option(self, name: str, flag: str) -> list[str]:
        return [flag, self.get(name)] if self.given(name) else []

    def principal(self) -> list[str]:
        """$(AS): --as and PRINCIPAL, where it is given."""
        return self.option("PRINCIPAL", "--as")

    def module(self, name: str, *args: str, capture: bool = False, check: bool = True):
        """python -m <name> <args>, with this interpreter; its exit code, or its standard output where captured. A
        failure stops the command with its code, unless check is off."""
        sys.stdout.flush()
        sys.stderr.flush()
        result = subprocess.run([sys.executable, "-m", name, *map(str, args)], text=True,
                                stdout=subprocess.PIPE if capture else None)
        if check and result.returncode:
            raise Failed(result.returncode, name)
        return (result.stdout or "").rstrip("\n") if capture else result.returncode

    def need(self, condition: bool, message: str, *, err: bool = True):
        if not condition:
            say(message, err=err)
            raise Failed(2, "settings")

    def run(self, command: str):
        return getattr(self, command.replace("-", "_"))()

    def require(self, path_name: str, recipe):
        """A file prerequisite: its recipe runs once where the file is missing; none where its path is empty."""
        path = self.get(path_name)
        if not path or path_name in self.made:
            return
        self.made.add(path_name)
        if not Path(path).exists():
            recipe()

    def estate_from(self) -> str:
        """$(ESTATE_FROM): the client's fixture where its folder holds one, else the generator."""
        return "kge.estate.from_files" if Path(f"{self.get('PACK')}/fixture/schema.yaml").exists() else "kge.estate.generate"

    def make_summary(self):
        self.module(self.estate_from())
        self.module("kge.estate.onboard", "build")

    def make_landed(self):
        self.require("SUMMARY", self.make_summary)
        self.module("kge.estate.certify")

    def ensure_landed(self):
        self.require("LANDED", self.make_landed)

    def ensure_graph(self):
        self.require("GRAPH", self.graph)

    def sync(self, model_hash: str):
        """$(BIGQUERY_SYNC): the sync the profile names, where WAREHOUSE_BACKEND or RETRIEVAL_BACKEND is bigquery."""
        if self.get("SYNC"):
            self.module(self.get("SYNC"), "--hash", model_hash)

    def load_steps(self, artifact: str):
        """$(LOAD_STEPS): load, the pipeline, the sync where there is one, and the LOADED mark, each once the one before
        it passed."""
        model_hash = Path(artifact).name
        self.module("kge.graph.load", "--artifact", artifact)
        self.module("kge.pipeline.run", "--hash", model_hash)
        self.sync(model_hash)
        self.module("kge.graph.release", "--hash", model_hash, "--mark-loaded")

    def candidate(self) -> str:
        """The artifact HASH names, else the one the pack compiles to (compile --check)."""
        if self.given("HASH"):
            return f"{self.get('ARTIFACTS')}/{self.get('HASH')}/"
        return self.module("kge.compiler.compile", "--check", capture=True)

    def estate(self):
        self.module(self.estate_from())
        self.module("kge.estate.onboard", "build")
        self.module("kge.estate.certify")

    seed = estate

    def certify(self):
        self.require("SUMMARY", self.make_summary)
        self.module("kge.estate.certify")

    def lock(self):
        self.ensure_landed()
        self.module("kge.compiler.lock")

    def check(self):
        lock = self.module("kge.compiler.lock", "--check", check=False)
        compiled = self.module("kge.compiler.compile", "--check", check=False)
        if compiled or lock:
            raise Failed(compiled or lock, "check")

    def compile(self):
        self.module("kge.compiler.compile")

    def load(self):
        self.ensure_landed()
        self.load_steps(self.candidate())

    def graph(self):
        self.ensure_landed()
        self.module("kge.compiler.lock", "--check")
        artifact = self.module("kge.compiler.compile", capture=True)
        self.load_steps(artifact)
        self.module("kge.graph.release", "--hash", Path(artifact).name, "--ungated")

    def pipeline(self):
        if not self.given("HASH"):
            self.ensure_graph()
        self.module("kge.pipeline.run", *self.option("HASH", "--hash"))

    def sign_off(self):
        self.need(self.given("PRINCIPAL") != self.given("ROLE"),
                  "sign-off needs exactly one of PRINCIPAL=<username> or ROLE=<certifying role>")
        model_hash = self.get("HASH") if self.given("HASH") else Path(self.candidate()).name
        self.module("kge.graph.release", "--sign-off", *self.principal(), *self.option("ROLE", "--role"), "--hash", model_hash)

    def access(self):
        self.module("kge.services.access.cli")

    def setup_client(self):
        self.module("kge.pipeline.registry", "setup-client")

    def release(self):
        from kge.graph import release
        from kge.adapters.released import POST_FAILED
        prof = profile.resolve()
        (prof.run_dir() / "rollback.json").unlink(missing_ok=True)
        if self.get("PREVIOUS") == "yes":
            code = self.module("kge.graph.release", "--previous", "--defer-retention", check=False)
            model_hash = None
        else:
            model_hash = self.get("HASH") if self.given("HASH") else Path(self.candidate()).name
            code = self.module("kge.graph.release", "--hash", model_hash, "--defer-retention", check=False)
        if code not in (0, POST_FAILED):
            raise Failed(code, "kge.graph.release")
        if model_hash is not None and not (prof.run_dir() / "rollback.json").is_file():
            self.sync(model_hash)
        if not self.given("STORE_URL"):
            release.finish(prof)
        if code:
            raise Failed(code, "kge.graph.release")

    def _stored_after(self):
        """The entry script's saved-job boundary, with retention inside the existing client lease."""
        from kge.graph import release
        from kge.pipeline import objstore
        command = self.get("COMMAND")
        self.need(command in {"release", "onboard"}, "stored completion requires release or onboard")
        prof = profile.resolve()
        store = objstore.the_store()
        try:
            release.prepare_retention(prof)
            objstore.after(command, Path.cwd(), release_job=False)
            if command == "release":
                release.finish(prof)
            else:
                release.finish_onboard(prof)
        finally:
            objstore.release_lease(store, prof)


    def margin_run(self, model_hash: str) -> tuple[int, int]:
        """kge/eval/margin.py on the graph, or model_hash's, its exit code written to MARGIN_STATUS by temp file and
        rename; (its code, the write's: 0, else 1)."""
        status = Path(self.get("MARGIN_STATUS"))
        status.parent.mkdir(parents=True, exist_ok=True)
        status.unlink(missing_ok=True)
        code = self.module("kge.eval.margin", "--repeats", self.get("REPEATS"), "--baseline", self.get("BASELINE"),
                           *(["--hash", model_hash] if model_hash else []), check=False)
        temporary = status.with_name(f"{status.name}.{os.getpid()}")
        try:
            temporary.write_text(f"{code}\n")
            temporary.replace(status)
        except OSError as exc:
            say(f"kge: {exc}", err=True)
            return code, 1
        return code, 0

    def eval_pre(self):
        model_hash = Path(self.candidate()).name
        code, saved = self.margin_run(model_hash)
        if code or saved:
            raise Failed(code or saved, "eval-pre")

    def margin(self):
        if not self.given("HASH"):
            self.ensure_graph()
        model_hash = self.get("HASH") if self.given("HASH") else ""
        baseline = self.get("BASELINE") if self.given("BASELINE") else "''"
        say(f"{self.get('PY')} -m kge.eval.margin --repeats {self.get('REPEATS')} --baseline {baseline}"
            + (f" --hash {model_hash}" if model_hash else ""))
        code, saved = self.margin_run(model_hash)
        if code or saved:
            raise Failed(code or saved, "margin")

    def repair_unit_warehouses(self, prof):
        """A failed journal may have dropped the version a live draft still names."""
        import yaml
        from kge import packstore
        from kge.adapters.warehouse import estate_of
        from kge.estate import ddl
        from kge.estate.onboard import common

        store, estate = packstore.for_profile(prof), estate_of(prof)
        for name in store.list("units"):
            if name.count("/") != 2 or not name.endswith("/draft.yaml"):
                continue
            draft = yaml.safe_load(store.read(name)) or {}
            version = (draft.get("warehouse") or {}).get("version")
            unit = name.split("/")[1]
            if version is None or version in estate.versions(unit):
                continue
            meta = common._manifest(prof, store, unit)
            if meta["rows"]["origin"] == "none":
                continue
            model = ddl.parse(store.read(f"units/{unit}/schema.sql").decode(), unit)
            rows = meta["rows"]
            mode = "invented" if rows["origin"] == "invented" else None
            if rows["origin"] == "csv":
                files = {f"{table}.csv": store.read(f"units/{unit}/data/{table}.csv") for table in rows["files"]}
                mode = {"csv": ddl.read_csvs(model, files)}
            ddl.build(model, estate, mode, version, draft["warehouse"]["inputs"])

    def onboard(self):
        from kge import packstore
        from kge.adapters import appstate
        from kge.adapters.graphstore import graph_path
        from kge.adapters.warehouse import estate_of
        from kge.compiler.inputs import read_declared
        from kge.estate import from_files, upfront
        from kge.estate.onboard import stage
        from kge.graph import leftovers, release
        from kge.pipeline import attempt, objstore, uploads, warehouse_versions

        self.need(not (reason := attempt.retry_refusal("onboard")), reason or "")
        prof = profile.resolve()
        run, pack = prof.run_dir(), prof.clients / prof.client
        run.mkdir(parents=True, exist_ok=True)
        backup = run / "onboard-inputs-before"
        if backup.exists():
            shutil.rmtree(backup)
            say("onboard: discarded onboard-inputs-before/ from a previous run: starting from the current working inputs")
        for name in (objstore.ONBOARD_RECORD, objstore.ONBOARD_RUN, uploads.UPLOAD_RECORD):
            (run / name).unlink(missing_ok=True)
        def now():
            return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        record = {"started_at": now(), "finished_at": None, "outcome": "failed", "step": "inputs", "hash": None,
                  "upload": None, "ignored": [], "suggestion": None, "swept": {}, "dropped": {}, "findings": [], "gate": None}
        changes = {"lock": {"written": False, "changes": []},
                   "estate": {"rebuilt": False, "why": "no-fixture", "version": None, "fixture_sha256": None},
                   "reloaded": False, "rechecked": False}
        before = None
        lock = pack / "pack.lock"
        old_lock = lock.read_bytes() if lock.is_file() else None
        summary = prof.estate() / "summary.json"
        old_summary = summary.read_bytes() if summary.is_file() else None
        captured_units = None
        graph, old_gate, margin_only = None, None, False
        store_url = self.get("STORE_URL")
        landed = False
        inputs_restored = True

        def progress(number, name):
            record["step"] = name
            say(f"onboard: [{number}/{ONBOARD_STEPS}] {name}")

        def save():
            record["finished_at"] = now()
            (run / objstore.ONBOARD_RUN).write_text(json.dumps(record, indent=2) + "\n")

        def unit_files():
            paths = [*prof.estate().glob("*.json"), *(prof.estate() / "docs").rglob("*")]
            return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}

        try:
            progress(1, "inputs")
            if self.given("FROM"):
                if store_url:
                    reason = objstore.page_request_running(objstore.the_store(), prof)
                    self.need(not reason, reason or "")
                planned = uploads.plan(self.get("FROM"), pack, prof.client, store_url)
                record["upload"] = {key: getattr(planned, key) for key in ("source", "sha256", "write", "remove", "ignored")}
                uploads.apply(planned, pack, backup)
                (run / uploads.UPLOAD_RECORD).write_text(json.dumps(record["upload"]))
            progress(2, "sweep")
            swept = leftovers.sweep(prof)
            record["swept"] = swept.to_json()
            for line in leftovers.lines(swept, "swept"):
                say(line)
            before = leftovers.held(prof)
            (run / leftovers.JOURNAL).write_text(json.dumps(before.to_json()))
            progress(3, "check")
            checked = upfront.check(prof)
            record.update(ignored=checked.ignored, suggestion=checked.suggestion, findings=list(map(str, checked.findings)))
            for name in checked.ignored:
                say(f"onboard: ignored: {name}")
            if checked.suggestion:
                say("onboard: pack.yaml declares no inputs; add this to its manifest:")
                say("\n".join("  " + line for line in checked.suggestion.splitlines()))
            for finding in checked.findings:
                say(f"onboard: refused: {finding}")
            if checked.findings:
                say(f"onboard: refused, {len(checked.findings)} finding(s): nothing was written")
                record["outcome"] = "refused"
                raise Failed(2, "check")
            declared = read_declared(pack)
            progress(4, "fixture")
            fixture = pack / (declared.fixture if declared and declared.fixture else "fixture")
            if (declared and declared.fixture) or (declared is None and (fixture / "schema.yaml").is_file()):
                changes["estate"] = from_files.build(fixture, prof.estate(), prof.client, estate=estate_of(prof),
                    if_changed=True, landing=prof.landing, defer_landing=True,
                    sets=declared.sets if declared else ("golden", "heldout"))
            for document in changes["estate"].get("unread_documents", []):
                say(f"onboard: {document['path']}: {document['evidence']}")
            progress(5, "units")
            store = packstore.for_profile(prof)
            units_to_build = set(appstate.for_profile(prof, store).units())
            units_to_build.update(name.split("/")[1] for name in store.list("units") if name.count("/") >= 2)
            captured_units = stage.capture(prof, units_to_build)
            earlier = unit_files()
            self.repair_unit_warehouses(prof)
            units = json.loads(self.module("kge.estate.onboard", "build", "--json", capture=True))
            for message in units.get("updates", []):
                say(message)
            units_changed = bool(units.get("updates")) or earlier != unit_files()
            progress(6, "certify")
            if changes["estate"]["rebuilt"] or units_changed or not Path(self.get("LANDED")).exists():
                self.module("kge.estate.certify")
                landed = True
            else:
                say("onboard: certify: nothing changed")
            progress(7, "lock")
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "lock.json"
                self.module("kge.compiler.lock", "--refresh", "--changes", str(path))
                changes["lock"] = json.loads(path.read_text())
            progress(8, "compile")
            artifact = self.module("kge.compiler.compile", capture=True)
            model_hash = Path(artifact).name
            record["hash"] = model_hash
            say(f"onboard: candidate {model_hash}")
            graph = graph_path(prof.store / prof.client / model_hash)
            loaded = profile_words(model_hash).get("loaded") == "yes"
            progress(9, "load")
            gate = release.gate(graph) or {}
            already = loaded and not changes["estate"]["rebuilt"]
            passed = gate.get("passed") is True and gate.get("model_hash") == model_hash
            if not already:
                changes["reloaded"] = loaded
                self.load_steps(artifact)
            elif not passed:
                changes["rechecked"] = True
                margin_only = True
                gate_path = graph.parent / release.GATE
                old_gate = gate_path.read_bytes() if gate_path.is_file() else None
                say("onboard: checking again: the last check did not pass")
            progress(10, "margin")
            if already and passed:
                record["gate"] = {"passed": True, "run_id": gate.get("run_id")}
                record["outcome"] = "current already" if model_hash == self.get("CURRENT") else "passed"
                message = "current already" if record["outcome"] == "current already" else f"candidate already loaded and checked={model_hash}"
            else:
                previous_gate = release.gate(graph)
                reports = set(prof.paths()["runs"].glob("*/report.md"))
                code, saved = self.margin_run(model_hash)
                gate = release.gate(graph)
                created = set(prof.paths()["runs"].glob("*/report.md")) - reports
                run_id = (max(created, key=lambda path: path.stat().st_mtime_ns).parent.name if created else
                          gate.get("run_id") if gate and gate != previous_gate else None)
                record["gate"] = {"passed": code == 0 and not saved, "run_id": run_id}
                record["outcome"] = {0: "passed", 1: "gate failed", 2: "not checked"}.get(code, "failed")
                if code or saved:
                    say(f"onboard: {record['outcome']}")
                    raise Failed(code or saved, "margin")
                self.module("kge.estate.onboard", "record-onboard", "--hash", model_hash, "--passed", "true")
                message = "gate passed"
            digest = changes["estate"]["fixture_sha256"]
            if digest:
                data = json.loads(summary.read_text())
                data["fixture_sha256"] = digest
                summary.write_text(json.dumps(data, indent=2) + "\n")
            inputs = {"model_hash": model_hash, "declared": checked.declared,
                      "files": dict(checked.files),
                      "inputs_sha256": checked.inputs_sha256, "fixture_sha256": checked.fixture_sha256,
                      "upload_sha256": (record["upload"] or {}).get("sha256"),
                      "warehouses": warehouse_versions.live_versions(pack, build=prof.estate())}
            graph.parent.mkdir(parents=True, exist_ok=True)
            (graph.parent / release.INPUTS).write_text(json.dumps(inputs, indent=2) + "\n")
            changes["inputs"] = {**inputs, "ignored": checked.ignored, "suggestion": checked.suggestion}
            release.mark_changes(graph, changes)
            (run / objstore.ONBOARD_RECORD).write_text(json.dumps({**changes, "landed": landed}))
            save()
            (run / leftovers.JOURNAL).unlink(missing_ok=True)
            if not store_url:
                release.finish_onboard(prof)
            say(f"onboard: {message}")
        except BaseException as exc:
            if record["step"] == "inputs" and isinstance(exc, (ValueError, Failed)):
                record["outcome"] = "refused"
            if record["outcome"] in ("passed", "current already"):
                record["outcome"] = "failed"
            def cleanup(name, action):
                try:
                    action()
                    return True
                except Exception as cleanup_error:
                    say(f"onboard: restoring {name} failed: {cleanup_error}", err=True)
                    return False

            def drop_new():
                dropped = leftovers.drop_new(prof, before)
                record["dropped"] = dropped.to_json()
                for line in leftovers.lines(dropped, "dropped"):
                    say(line)

            def restore_file(path, data):
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)

            def restore_summary():
                """Restore metadata without certifying rows a failed in-place build replaced."""
                if old_summary is not None:
                    if (summary.is_file() and not estate_of(prof).builds_apart
                            and "fixture_sha256" not in json.loads(summary.read_text())):
                        data = json.loads(old_summary)
                        data.pop("fixture_sha256", None)
                        current = json.loads(summary.read_text()).get("warehouse_inputs", {})
                        data["warehouse_inputs"] = {u: value for u, value in data.get("warehouse_inputs", {}).items()
                                                    if current.get(u) == value}
                        summary.write_text(json.dumps(data, indent=2) + "\n")
                    else:
                        summary.write_bytes(old_summary)
                elif summary.is_file():
                    data = json.loads(summary.read_text())
                    data.pop("fixture_sha256", None)
                    summary.write_text(json.dumps(data))

            if before is not None:
                cleanup("new objects", drop_new)
            inputs_restored = cleanup("inputs", lambda: uploads.restore(pack, backup))
            if captured_units is not None:
                cleanup("units", lambda: stage.restore(prof, captured_units))
            cleanup("pack.lock", lambda: restore_file(lock, old_lock))
            cleanup("summary", restore_summary)
            if margin_only:
                cleanup("gate", lambda: restore_file(graph.parent / release.GATE, old_gate))
            cleanup("run record", save)
            raise
        finally:
            if inputs_restored and backup.exists():
                shutil.rmtree(backup)

    def source(self):
        self.need(bool(self.get("UNIT") and (self.get("DDL") or self.get("SEMANTIC"))),
                  "make source UNIT=<unit> DDL=<file>|SEMANTIC=<folder> LABEL=<label> PRINCIPAL=<knowledge engineer>")
        preview = self.given("PREVIEW")
        kind = "SEMANTIC" if self.given("SEMANTIC") else "DDL"
        self.module("kge.estate.onboard", "tables" if preview else "create", "--unit", self.get("UNIT"),
                    f"--{kind.lower()}", self.get(kind), *self.option("LABEL", "--label"),
                    *([] if preview else ["--as", self.get("PRINCIPAL")]),
                    *self.option("ROWS", "--rows"), *self.option("CSV", "--csv"))

    def rows(self):
        self.need(bool(self.get("UNIT") and self.get("ROWS")),
                  "make rows UNIT=<unit> ROWS=csv|invented [CSV=<dir>] PRINCIPAL=<knowledge engineer>")
        self.module("kge.estate.onboard", "rows", "--unit", self.get("UNIT"), "--rows", self.get("ROWS"),
                    *self.option("CSV", "--csv"), "--as", self.get("PRINCIPAL"))

    def draft(self):
        self.module("kge.estate.onboard", "draft", "--unit", self.get("UNIT"), "--as", self.get("PRINCIPAL"))

    def review(self, step: str):
        chosen = self.option("PROPOSAL", "--proposal") or self.option("UNIT", "--unit")
        self.module("kge.estate.onboard", step, *chosen, "--item", self.get("ITEM"), "--as", self.get("PRINCIPAL"))

    def accept(self):
        self.review("accept")

    def certify_draft(self):
        self.review("certify")

    def improve(self):
        self.module("kge.improve.flow", *self.option("PROPOSAL", "--id"), *self.option("UNIT", "--unit"), *self.principal())

    def onboard_status(self):
        self.module("kge.estate.onboard", "status")

    def reset_onboard(self):
        self.need(bool(self.get("UNIT")), "make reset-onboard UNIT=<unit>")
        reset = self.module("kge.estate.onboard", "reset", "--unit", self.get("UNIT"), capture=True)
        if not reset:
            say(f"reset-onboard: unit {self.get('UNIT')}: no onboarded unit to restore a graph for")
            return
        after = self.fresh()
        for step in ("certify", "lock", "graph"):
            after.run(step)

    def client(self):
        self.need(bool(self.get("CLIENT") and self.get("LABEL") and (self.get("DDL") or self.get("DB"))),
                  "make client CLIENT=<id> LABEL=<label> DDL=<file>|DB=<file> [CSV=<dir>|ROWS=invented|none]")
        kind = "DB" if self.given("DB") else "DDL"
        self.module("kge.estate.onboard", "client", "--client", self.get("CLIENT"), "--label", self.get("LABEL"),
                    f"--{kind.lower()}", self.get(kind), *self.option("CSV", "--csv"), *self.option("ROWS", "--rows"),
                    "--as", "platform")

    def reset_client(self):
        self.need(bool(self.get("CLIENT")), "make reset-client CLIENT=<id>")
        self.module("kge.estate.onboard", "reset-client", "--client", self.get("CLIENT"))

    def check_estate(self):
        self.module("kge.estate.check_estate", "--snapshot", self.get("ESTATE"), "--baseline", self.get("BEFORE"))

    def reset_overlay(self):
        self.module("kge.pipeline.overlay", "--reset")

    def passwords(self):
        self.module("kge.services.identity.cli", "--passwords", *(["--rotate-all"] if self.get("ROTATE") == "all" else []))

    def ensure_passwords(self):
        self.ensure_graph()
        self.require("PASSWORDS_FILE", lambda: self.module("kge.services.identity.cli", "--passwords", "--rotate-all"))

    def eval(self):
        self.module("kge.eval.run_golden", "--arm", self.get("ARM"), *self.principal())

    def tagger_eval(self):
        self.module("kge.eval.tagger", "--job", "--repeats", self.get("REPEATS"),
                    *(["--live"] if self.get("LIVE") == "yes" else []),
                    *(["--ab"] if self.get("AB") == "yes" else []),
                    *self.option("TARGETS", "--targets"))

    def ab(self):
        self.module("kge.eval.harness", "--arms", self.get("ARMS"), "--sets", self.get("SETS"), "--repeats",
                    self.get("REPEATS"), *self.principal(), *self.option("BASELINE", "--baseline"))

    def baseline(self):
        self.need(bool(self.get("RUN")), f"make baseline RUN=<run_id>: the run in {self.get('RUNS')} to record as "
                  f"{self.get('BASELINE')}", err=False)
        self.module("kge.eval.report", self.get("RUN"), "--save-baseline", self.get("BASELINE"),
                    *self.option("NAME", "--name"))

    def profile(self):
        sys.stdout.flush()
        code = profile.main([])
        if code:
            raise Failed(code, "profile")

    def serve(self):
        from kge.cli import servers
        servers.serve(self)

    def portal_build(self):
        from kge.cli import servers
        servers.build(self)

    def portal(self):
        from kge.cli import servers
        servers.portal(self)

    def demo(self):
        from kge.cli import servers
        servers.demo(self)


def main(argv: list[str] | None = None) -> int:
    commands, values = parse(sys.argv[1:] if argv is None else argv)
    previous = dict(os.environ)
    heartbeat = None
    try:
        os.environ.update(values)
        from kge.pipeline import attempt, lease
        from kge.pipeline.objstore import the_store
        for command in commands:
            if reason := attempt.retry_refusal(command):
                say(reason, err=True)
                return 2
        runner = Commands(values)
        if runner.words.get("id") and not os.environ.get("PROFILE"):
            os.environ["PROFILE"] = runner.words["id"]
        if os.environ.get("STORE_URL"):
            lease_path = profile.resolve().run_dir() / lease.LEASE_FILE
            if lease_path.is_file():
                heartbeat = lease.heartbeat(the_store(), lease.Lease(**json.loads(lease_path.read_text())))
        for command in commands:
            runner.run(command)
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode if exc.returncode >= 0 else 128 - exc.returncode
    except (OSError, ValueError, RuntimeError) as exc:
        say(f"kge: {exc}", err=True)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        if heartbeat is not None:
            heartbeat.set()
        os.environ.clear()
        os.environ.update(previous)
