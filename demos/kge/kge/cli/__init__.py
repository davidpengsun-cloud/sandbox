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
import os
import subprocess
import sys
from pathlib import Path

from kge.pipeline import profile

COMMANDS = (
    "estate", "seed", "check", "compile", "load", "margin", "onboard", "sign-off", "release", "passwords", "serve",
    "improve", "ab", "baseline", "reset-overlay", "source", "rows", "draft", "accept", "certify-draft", "onboard-status",
    "reset-onboard", "client", "reset-client", "demo", "graph", "pipeline", "lock", "certify", "check-estate", "eval",
    "profile", "portal", "portal-build",
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
        elif arg in COMMANDS:
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
        self.need(bool(self.get("PRINCIPAL")), "make sign-off PRINCIPAL=<steward> [HASH=<hash>]: who signs off, one whose "
                  "role may perform certification")
        model_hash = self.get("HASH") if self.given("HASH") else Path(self.candidate()).name
        self.module("kge.graph.release", "--sign-off", *self.principal(), "--hash", model_hash)

    def release(self):
        model_hash = self.get("HASH") if self.given("HASH") else Path(self.candidate()).name
        self.module("kge.graph.release", "--hash", model_hash)
        self.sync(model_hash)

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

    def onboard(self):
        current, current_loaded = self.get("CURRENT"), self.get("CURRENT_LOADED")
        say("onboard: [1/7] units      -> kge/estate/onboard/units.py build")
        self.module("kge.estate.onboard", "build")
        say("onboard: [2/7] certify    -> kge/estate/certify.py")
        self.module("kge.estate.certify")
        say("onboard: [3/7] lock       -> kge/compiler/lock.py")
        self.module("kge.compiler.lock", *(["--add-new"] if Path(f"{self.get('PACK')}/pack.lock").is_file() else []))
        say("onboard: [4/7] check      -> kge check")
        self.fresh().check()
        say("onboard: [5/7] compile    -> kge/compiler/compile.py")
        artifact = self.module("kge.compiler.compile", capture=True)
        model_hash = Path(artifact).name
        say(f"onboard: candidate {model_hash}")
        if model_hash == current and current_loaded == "yes":
            say("onboard: current already")
            return
        if profile_words(model_hash).get("loaded") == "yes":
            say(f"onboard: candidate already loaded={model_hash}")
        else:
            say(f"onboard: [6/7] load       -> make load's steps on {model_hash}")
            self.load_steps(artifact)
        say(f"onboard: [7/7] margin     -> kge/eval/margin.py --repeats {self.get('REPEATS')} --baseline "
            f"{self.get('BASELINE')} --hash {model_hash}")
        code, saved = self.margin_run(model_hash)
        if saved and not code:
            raise Failed(saved, "onboard")
        self.module("kge.estate.onboard", "record-onboard", "--hash", model_hash, "--passed", "false" if code else "true")
        say(f"onboard: { {0: 'gate passed', 1: 'gate failed'}.get(code, 'not checked')}")
        if code:
            raise Failed(code, "onboard")

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
        self.module("kge.services.identity.cli", "--passwords")

    def ensure_passwords(self):
        self.ensure_graph()
        self.require("PASSWORDS_FILE", self.passwords)

    def eval(self):
        self.module("kge.eval.run_golden", "--arm", self.get("ARM"), *self.principal())

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
    try:
        os.environ.update(values)
        runner = Commands(values)
        if runner.words.get("id") and not os.environ.get("PROFILE"):
            os.environ["PROFILE"] = runner.words["id"]
        for command in commands:
            runner.run(command)
        return 0
    except subprocess.CalledProcessError as exc:
        return exc.returncode if exc.returncode >= 0 else 128 - exc.returncode
    except OSError as exc:
        say(f"kge: {exc}", err=True)
        return 2
    except KeyboardInterrupt:
        return 130
    finally:
        os.environ.clear()
        os.environ.update(previous)
