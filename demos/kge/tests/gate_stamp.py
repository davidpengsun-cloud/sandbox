"""The local gate's pass record: each leg of make local-gate stamps the inputs it passed on, and a push checks the stamps.

python tests/gate_stamp.py start LEG       before a leg: notes HEAD for the leg when the repository's tree is clean and
                                           nothing narrows the run, else nothing
python tests/gate_stamp.py finish LEG [DATA]
                                           after the leg passed: stamps the leg for HEAD's inputs when HEAD is the noted
                                           commit and the tree is still clean (build/gate-stamps/<leg>/<key>, a / in the
                                           leg's name as _), keeping a copy of its coverage data file DATA beside the
                                           stamp, else says why it stamps nothing
python tests/gate_stamp.py data LEG...     the coverage data each leg kept when it passed on HEAD's inputs, one path a line;
                                           exit 1 when a leg has none
python tests/gate_stamp.py clean           exit 0 when the tree is clean and nothing narrows the run: a build made now
                                           may serve the run's later legs
python tests/gate_stamp.py select [COMMIT] the legs the commit's change needs (HEAD unless given): prints rule: <class>,
                                           legs: all or the legs (LOCAL_GATE_LEGS order, then LOCAL_GATE_NARROW's), then
                                           why: lines
python tests/gate_stamp.py check [COMMIT]  exit 0 when the commit (HEAD unless given) is already in origin/develop (a
                                           branch restarted from develop after a squash), or every leg select names for
                                           it, of those the commit's own Makefile lists in LOCAL_GATE_LEGS or
                                           LOCAL_GATE_NARROW (every leg means LOCAL_GATE_LEGS alone), has a stamp
                                           for its inputs in that commit and, for HEAD, the tree is clean; else exit 1
                                           saying why

LOCAL_GATE_LEGS in the Makefile is the one list: the legs make local-gate runs, in order, each as leg:path[,path...] with
the paths, from the repository's root, that can change its result ('.' is the whole repository), read from the commit
itself. A leg's key is a hash of its name, its paths and the git object each path names in the commit, so a commit that
changes nothing under a leg's paths keeps that leg's stamp (a rebase that leaves them alone included), and any change
under them needs a fresh one. LOCAL_GATE_NARROW lists, the same way, the legs that run a few unit test files alone
(the ones that read the Terraform or the kit): only a selection names them, and every leg never needs them.

The selection classes the paths changed between the commit and its merge base with origin/develop by the table beside
LOCAL_GATE_LEGS (GATE_PATHS, GATE_CLASSES, GATE_ALONE), read from the merge base's Makefile, so a change cannot widen its
own class. The legs are the union of every touched class's. It fails safe: no merge base, an empty change, a path the table does not know, a table that does not read, a
change to the Makefile, this script, .pre-commit-config.yaml or the checks, kit and kit-demo workflows, or git failing
to answer selects every leg. A stamp records the rule, the legs selected and the merge base it passed under.

Clean means git status --porcelain prints nothing for the whole repository, untracked files included, and no tracked
file is marked assume-unchanged or skip-worktree (git ls-files -v), so the gate ran on the committed bytes alone. A run
is narrowed when the Makefile names an override in GATE_OVERRIDES (SHARD, PART, MIN, a pytest argument and the like,
set on the command line or in the environment; the leg selector LEG is not one), PYTEST_ADDOPTS is set, or make runs
with -i or -k (MAKEFLAGS), which would go on past a failing step, or -n, -t or -q, which run no leg at all, or
-e, under which the environment replaces the Makefile's lists: only a complete run, at the configured coverage floor,
stamps. A stamp names its leg, its key, the commit it ran on and when it passed.
"""

from datetime import datetime, timezone
from fnmatch import fnmatchcase
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

STAMPS = Path("build") / "gate-stamps"
PENDING = STAMPS / ".pending"
LEGS = re.compile(r"^LOCAL_GATE_LEGS :=((?:.*\\\n)*.*)$", re.M)
NARROW = re.compile(r"^LOCAL_GATE_NARROW :=((?:.*\\\n)*.*)$", re.M)
NAME = re.compile(r"^[a-z][a-z0-9-]*(/[0-9]+)?$")
NO_COMMIT = "0" * 40
HOOK_GIT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX")
DEVELOP = "origin/develop"
GUARDED = ("Makefile", "tests/gate_stamp.py")
ROOT_GUARDED = (".pre-commit-config.yaml", ".github/workflows/checks.yml", ".github/workflows/kit.yml",
                ".github/workflows/kit-demo.yml")
COVERAGE = "gate-coverage"


def git_env() -> dict[str, str]:
    """The environment without what git sets for a hook: a pre-push hook gets GIT_DIR, and with it git takes the folder
    it runs in (demos/kge) as the work tree's root, so everything outside it would look deleted. Without them git finds
    the repository, a linked worktree included, from the folder, as it does at a prompt."""
    return {key: value for key, value in os.environ.items() if key not in HOOK_GIT}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, env=git_env()).stdout.strip()


def top() -> str:
    """The repository's root: git ls-files lists only the files under the folder it runs in, and the gate runs in the
    demo's folder, so every whole-tree question is asked from the root."""
    return git("rev-parse", "--show-toplevel")


def hidden() -> list[str]:
    """Tracked files git is told not to look at, anywhere in the repository: assume-unchanged (a lower-case tag) or
    skip-worktree (S)."""
    return [line[2:] for line in git("-C", top(), "ls-files", "-v").splitlines() if line[:1].islower() or line[:1] == "S"]


def unclean() -> str:
    if git("-C", top(), "status", "--porcelain", "--untracked-files=all"):
        return "the tree has changes"
    if hidden():
        return "tracked files are marked assume-unchanged or skip-worktree: " + ", ".join(hidden()[:5])
    return ""


def lax_make() -> bool:
    """Whether make runs with -i or -k, which would carry on past a failing step and still reach the stamp, with -n,
    -t or -q, under which a leg's recursive make runs nothing and still succeeds, or with -e, under which the environment
    replaces the Makefile's own lists (a narrow leg's files, GATE_OVERRIDES itself): GNU make puts its single-letter flags
    first in MAKEFLAGS (eiknqt), and options before any -- may follow."""
    words = os.environ.get("MAKEFLAGS", "").split()
    words = words[:words.index("--")] if "--" in words else words
    letters = words[0] if words and not words[0].startswith("-") and "=" not in words[0] else ""
    short = "".join(word[1:] for word in words if word.startswith("-") and not word.startswith("--"))
    long = {"--ignore-errors", "--keep-going", "--dry-run", "--just-print", "--recon", "--touch", "--question",
            "--environment-overrides"} & set(words)
    return bool(set(letters + short) & {"e", "i", "k", "n", "t", "q"} or long)


def narrowed() -> str:
    reasons = os.environ.get("GATE_OVERRIDES", "").split()
    if os.environ.get("PYTEST_ADDOPTS"):
        reasons.append("PYTEST_ADDOPTS")
    if lax_make():
        reasons.append("make -i or -k")
    return " ".join(reasons)


def makefile(commit: str) -> str:
    """This folder's Makefile (demos/kge) as the commit holds it, empty when it holds none."""
    found = subprocess.run(["git", "show", f"{commit}:./Makefile"], capture_output=True, text=True, env=git_env())
    return found.stdout if found.returncode == 0 else ""


def legs(commit: str = "HEAD", narrow: bool = False) -> dict[str, list[str]]:
    """The legs make local-gate runs and each one's input paths, as the commit's own Makefile lists them (this folder's
    Makefile, demos/kge); with narrow, LOCAL_GATE_NARROW's legs too, which a selection may name and a leg may stamp."""
    text = makefile(commit)
    return {**listed_legs(text), **narrow_legs(text)} if narrow else listed_legs(text)


def narrow_legs(text: str) -> dict[str, list[str]]:
    """LOCAL_GATE_NARROW in a Makefile's text, read as LOCAL_GATE_LEGS is; empty when it is missing, does not parse, or
    names a leg LOCAL_GATE_LEGS lists."""
    found = listed_legs(text, NARROW)
    return {} if set(found) & set(listed_legs(text)) else found


def listed_legs(text: str, pattern: re.Pattern = LEGS) -> dict[str, list[str]]:
    """LOCAL_GATE_LEGS in a Makefile's text. An entry that does not parse makes the whole list empty, so nothing can be
    vouched for."""
    match = pattern.search(text)
    entries: dict[str, list[str]] = {}
    for entry in match.group(1).replace("\\\n", " ").split() if match else []:
        leg, _, paths = entry.partition(":")
        inputs = [path for path in paths.split(",") if path]
        if not NAME.match(leg) or leg in entries or not inputs or any(path.startswith(("/", "-")) or ".." in path.split("/")
                                                                       for path in inputs):
            return {}
        entries[leg] = inputs
    return entries


def folder(leg: str) -> str:
    return leg.replace("/", "_")


def key(commit: str, leg: str, inputs: list[str]) -> str:
    """The hash of the leg, its paths and the object each path names in the commit (missing when it names none)."""
    lines = [leg]
    for path in inputs:
        spec = f"{commit}^{{tree}}" if path == "." else f"{commit}:{path}"
        found = subprocess.run(["git", "-C", top(), "rev-parse", "--verify", "-q", spec], capture_output=True, text=True,
                               env=git_env())
        lines.append(f"{path}\t{found.stdout.strip() if found.returncode == 0 else 'missing'}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def known(leg: str) -> bool:
    if leg in legs(narrow=True):
        return True
    print(f"gate stamp: '{leg}' is not a leg of HEAD's make local-gate ({' '.join(legs(narrow=True)) or 'no LOCAL_GATE_LEGS'}), so "
          "it stamps nothing", file=sys.stderr)
    return False


def start(leg: str) -> int:
    if not NAME.match(leg):
        print(f"gate stamp: '{leg}' is not a leg name", file=sys.stderr)
        return 2
    PENDING.mkdir(parents=True, exist_ok=True)
    pending = PENDING / folder(leg)
    pending.unlink(missing_ok=True)
    if not known(leg):
        return 0
    why = narrowed()
    if why:
        print(f"gate stamp: {why} narrows this gate run, so the {leg} leg stamps nothing", file=sys.stderr)
        return 0
    why = unclean()
    if why:
        print(f"gate stamp: {why}, so the {leg} leg stamps nothing", file=sys.stderr)
        return 0
    pending.write_text(git("rev-parse", "HEAD") + "\n")
    return 0


def finish(leg: str, data: str = "") -> int:
    if not NAME.match(leg):
        print(f"gate stamp: '{leg}' is not a leg name", file=sys.stderr)
        return 2
    head = git("rev-parse", "HEAD")
    pending = PENDING / folder(leg)
    noted = pending.read_text().strip() if pending.is_file() else ""
    pending.unlink(missing_ok=True)
    if noted != head:
        print(f"gate stamp: {leg} not stamped, the leg did not start on this commit with a clean tree and a complete run",
              file=sys.stderr)
        return 0
    why = unclean() or narrowed()
    if why:
        print(f"gate stamp: {leg} not stamped, {why}", file=sys.stderr)
        return 0
    inputs = legs(narrow=True).get(leg)
    if not inputs:
        print(f"gate stamp: {leg} not stamped, HEAD's LOCAL_GATE_LEGS and LOCAL_GATE_NARROW do not list it", file=sys.stderr)
        return 0
    digest = key(head, leg, inputs)
    chosen = selection(head)
    record = {"leg": leg, "key": digest, "inputs": inputs, "commit": head,
              "passed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "rule": chosen["rule"],
              "selected": chosen["legs"] or "all", "base": chosen["base"]}
    (STAMPS / folder(leg)).mkdir(parents=True, exist_ok=True)
    kept = STAMPS / folder(leg) / f"{digest}.coverage"
    kept.unlink(missing_ok=True)
    if data and Path(data).is_file():
        shutil.copyfile(data, kept)
    (STAMPS / folder(leg) / digest).write_text(json.dumps(record) + "\n")
    print(f"gate stamp: the {leg} leg passed on {head}, for {', '.join(inputs)}")
    return 0


def stamped(leg: str, digest: str) -> bool:
    stamp = STAMPS / folder(leg) / digest
    if not stamp.is_file():
        return False
    try:
        record = json.loads(stamp.read_text())
    except ValueError:
        return False
    return isinstance(record, dict) and record.get("key") == digest and record.get("leg") == leg


def data(*wanted: str) -> int:
    """The coverage data the legs kept when they passed on HEAD's inputs, for a leg that combines them."""
    listed = legs(narrow=True)
    paths = []
    for leg in wanted:
        digest = key("HEAD", leg, listed[leg]) if leg in listed else ""
        kept = STAMPS / folder(leg) / f"{digest}.coverage"
        if not digest or not stamped(leg, digest) or not kept.is_file():
            print(f"gate stamp: the {leg} leg has no pass with coverage data on HEAD's inputs: run make local-gate "
                  f"LEG={leg} first", file=sys.stderr)
            return 1
        paths.append(str(kept))
    print("\n".join(paths))
    return 0


def block(text: str, name: str) -> list[str] | None:
    """A Makefile variable's words, its continuation lines joined, or None when the text does not set it."""
    match = re.search(rf"^{name} :=((?:.*\\\n)*.*)$", text, re.M)
    return match.group(1).replace("\\\n", " ").split() if match else None


def table(text: str) -> dict | None:
    """The selection table in a Makefile's text: GATE_PATHS, each class:pattern in the order they are tried; GATE_CLASSES,
    each class:leg,leg...; GATE_ALONE, each class:pattern every changed path must match for the class to stay narrow;
    with LOCAL_GATE_LEGS' order, LOCAL_GATE_NARROW's legs, and the legs gate-coverage measures (FULL_LEGS but
    parity-margin). None when any part is missing or does not read, or a class names a leg neither list does."""
    order = list(listed_legs(text))
    narrow = list(narrow_legs(text))
    rows, kinds, alone, full = (block(text, name) for name in ("GATE_PATHS", "GATE_CLASSES", "GATE_ALONE", "FULL_LEGS"))
    if not order or not rows or not kinds or alone is None or not full:
        return None
    classes: dict[str, list[str]] = {}
    for entry in kinds:
        name, _, members = entry.partition(":")
        chosen = [leg for leg in members.split(",") if leg]
        if not NAME.match(name) or name == "full" or name in classes or not chosen or not set(chosen) <= {*order, *narrow}:
            return None
        classes[name] = chosen
    rules = []
    for entry in rows:
        name, _, pattern = entry.partition(":")
        if not pattern or (name != "full" and name not in classes):
            return None
        rules.append((name, pattern))
    scopes: dict[str, str] = {}
    for entry in alone:
        name, _, pattern = entry.partition(":")
        if name not in classes or name in scopes or not pattern:
            return None
        scopes[name] = pattern
    return {"order": order, "narrow": narrow, "rules": rules, "classes": classes, "alone": scopes,
            "measured": [leg for leg in full if leg != "parity-margin"]}


def guarded(prefix: str) -> set[str]:
    """The paths whose change always runs the full gate, from the repository's root, given this folder's prefix there: the
    gate's own definition, which the table cannot vouch for."""
    return {prefix + name for name in GUARDED} | set(ROOT_GUARDED)


def shown(path: str) -> str:
    return path.encode("unicode_escape", "backslashreplace").decode("ascii")


def classify(paths: list[str], spec: dict, guarded: set[str]) -> tuple[str, list[str] | None, list[str]]:
    """The rule, the legs (None for every leg) and the reasons, for changed paths: the union of the legs of every class
    touched, gate legs in LOCAL_GATE_LEGS order, then the narrow legs, which a selection with every unit leg drops."""
    hits: dict[str, list[str]] = {}
    for path in paths:
        name = "full" if path in guarded else next((kind for kind, pattern in spec["rules"] if fnmatchcase(path, pattern)),
                                                   "full")
        hits.setdefault(name, []).append(path)
    names = [name for name in ("full", *spec["classes"]) if name in hits]
    why = []
    for name in names:
        examples = " ".join(shown(path) for path in hits[name][:3]) + (" ..." if len(hits[name]) > 3 else "")
        adds = "full gate" if name == "full" else " ".join(spec["classes"][name])
        why.append(f"why:  {name}: {len(hits[name])} path(s), e.g. {examples} -> {adds}")
    strays = False
    for name in names:
        pattern = spec["alone"].get(name)
        outside = [path for path in paths if pattern and not fnmatchcase(path, pattern)]
        if outside:
            strays = True
            why.append(f"why:  {name} stays narrow only when every changed path matches {pattern}, and {shown(outside[0])} "
                       "does not -> full gate")
    rule = "+".join(names)
    if "full" in hits or strays:
        return ("full" if rule == "full" else f"full ({rule})"), None, why
    chosen = {leg for name in names for leg in spec["classes"][name]}
    if COVERAGE in chosen and not set(spec["measured"]) <= chosen:
        chosen.discard(COVERAGE)
        why.append(f"why:  {COVERAGE} runs only with every leg it measures ({' '.join(spec['measured'])}) -> dropped")
    units = [leg for leg in spec["order"] if leg.startswith("unit-")]
    dropped = [leg for leg in spec["narrow"] if leg in chosen] if units and set(units) <= chosen else []
    if dropped:
        chosen -= set(dropped)
        why.append(f"why:  {' '.join(dropped)} run(s) inside the unit legs ({' '.join(units)}) -> dropped")
    ordered = [leg for leg in [*spec["order"], *spec["narrow"]] if leg in chosen]
    if not ordered:
        return f"full ({rule})", None, [*why, "why:  the classes name no leg to run -> full gate"]
    return rule, None if set(spec["order"]) <= chosen else ordered, why


def selection(commit: str = "HEAD") -> dict:
    """The legs the commit's change needs: its paths changed since its merge base with origin/develop, classed by the merge
    base's table. Every leg (legs None) whenever anything is in doubt, a path that is a symbolic link or a submodule on
    either side included, since what it points at is not classed."""
    result: dict = {"rule": "full", "legs": None, "why": [], "base": ""}
    try:
        sha = git("rev-parse", "--verify", f"{commit}^{{commit}}")
        found = subprocess.run(["git", "merge-base", DEVELOP, sha], capture_output=True, text=True, env=git_env())
        if found.returncode != 0 or not found.stdout.strip():
            return {**result, "why": [f"why:  no merge base of {DEVELOP} and {sha} -> full gate"]}
        base = found.stdout.strip()
        result["base"] = base
        diff = subprocess.run(["git", "-C", top(), "diff-tree", "-r", "-z", "--no-renames", base, sha], check=True,
                              capture_output=True, env=git_env()).stdout
        fields = diff.decode("utf-8", "surrogateescape").split("\0")
        entries = list(zip(fields[0::2], fields[1::2]))
        paths = [path for _, path in entries]
        links = {path for meta, path in entries if not re.fullmatch(r":(100644|100755|000000) (100644|100755|000000) .*", meta)}
        if not paths:
            return {**result, "why": [f"why:  no changed paths in {base}..{sha} -> full gate"]}
        spec = table(makefile(base))
        if spec is None:
            return {**result, "why": [f"why:  the Makefile at the merge base {base} has no selection table that reads "
                                      "-> full gate"]}
        rule, chosen, why = classify(paths, spec, guarded(git("rev-parse", "--show-prefix")) | links)
        return {**result, "rule": rule, "legs": chosen, "why": why}
    except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
        return {**result, "why": [f"why:  git could not answer ({type(error).__name__}) -> full gate"]}


def select(commit: str = "HEAD") -> int:
    chosen = selection(commit)
    print(f"rule: {chosen['rule']}")
    print(f"legs: {' '.join(chosen['legs']) if chosen['legs'] else 'all'}")
    for line in chosen["why"]:
        print(line)
    return 0


def check(commit: str = "HEAD") -> int:
    """The push's question: did every leg of make local-gate pass on exactly this commit's inputs, with nothing changed
    since."""
    if commit == NO_COMMIT:
        return 0
    sha = git("rev-parse", "--verify", f"{commit}^{{commit}}")
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, DEVELOP], capture_output=True,
                      env=git_env()).returncode == 0:
        print(f"gate stamp: {sha} is already in {DEVELOP}, which its ready run checked")
        return 0
    required = legs(sha)
    if not required:
        print(f"gate stamp: {sha}'s Makefile lists no LOCAL_GATE_LEGS it can read, so nothing can vouch for it",
              file=sys.stderr)
        return 1
    chosen = selection(sha)
    listed = legs(sha, narrow=True)
    narrow = bool(chosen["legs"]) and set(chosen["legs"]) <= set(listed)
    if narrow:
        required = {leg: listed[leg] for leg in chosen["legs"]}
    print(f"gate stamp: {sha} needs {' '.join(required) if narrow else 'every leg'}, by rule {chosen['rule']}")
    for line in chosen["why"]:
        print(f"gate stamp: {line}")
    missing = [leg for leg, inputs in required.items() if not stamped(leg, key(sha, leg, inputs))]
    if missing:
        print(f"gate stamp: {sha} has no local gate pass on its inputs for {' '.join(missing)}: run make local-gate "
              f"(or make local-gate LEG=<leg>) on it with a clean tree", file=sys.stderr)
        return 1
    if sha == git("rev-parse", "HEAD"):
        why = unclean()
        if why:
            print(f"gate stamp: {why}, which the stamps do not cover", file=sys.stderr)
            return 1
    print(f"gate stamp: {sha} passed {'the legs its change needs' if narrow else 'every leg of make local-gate'} on its "
          f"inputs ({' '.join(required)})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "start":
        sys.exit(start(sys.argv[2]))
    if len(sys.argv) in (3, 4) and sys.argv[1] == "finish":
        sys.exit(finish(*sys.argv[2:]))
    if len(sys.argv) >= 3 and sys.argv[1] == "data":
        sys.exit(data(*sys.argv[2:]))
    if len(sys.argv) in (2, 3) and sys.argv[1] == "check":
        sys.exit(check(*sys.argv[2:]))
    if len(sys.argv) in (2, 3) and sys.argv[1] == "select":
        sys.exit(select(*sys.argv[2:]))
    if len(sys.argv) == 2 and sys.argv[1] == "clean":
        sys.exit(1 if unclean() or narrowed() else 0)
    print("usage: python tests/gate_stamp.py start LEG|finish LEG [DATA]|data LEG...|check [COMMIT]|select [COMMIT]|clean",
          file=sys.stderr)
    sys.exit(2)
