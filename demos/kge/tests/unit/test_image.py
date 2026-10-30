"""One image for the app and the jobs (issue #48 item 5): the Dockerfile, its .dockerignore and its entry script.

    .venv/bin/python -m pytest tests/unit/test_image.py -q

The image holds the platform code, the meta-model, the Makefile, the requirements and the legal notices, with the simulated estate's generator that seed runs, and no client's pack or deployment, no build, no .env, no token or secret:
at run time clients/ and deploy/ are mounted read only and build/ is a volume. It has no make (issue #170). Its entry
script, entrypoint.sh, maps each of its commands to today's kge commands, for the profile PROFILE names, with nothing of
its own. These tests read the Dockerfile, put every file of the demo folder through the .dockerignore's rules as Docker
reads them, run the entry script beside a kge that records what it was asked, and serve a scratch copy of the demo's
build through it, as the container serves (on 0.0.0.0, SIGNIN=passwords, no demo mode), asked with a bearer token and by
a person signed in with the password kge passwords made. It builds on a client's base (issue #173): the base a build
argument, installed with uv into a virtual environment as root, run as uid 10001 in group 0, its entry script rewritten
with LF ends and every shared object loaded before the platform is installed. Building the image needs Docker and the
network, so no test here does; the check's own logic is run here on stand-in files.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2]
DOCKERFILE = DEMO / "Dockerfile"
IGNORE = DEMO / ".dockerignore"
ENTRY = DEMO / "entrypoint.sh"
PLATFORM_DIRS = ("kge", "standards", "ui")
PLATFORM_FILES = ("Makefile", "meta-model.yaml", "requirements.txt", "constraints.txt", "entrypoint.sh", "pyproject.toml", "LICENSE",
                  "THIRD_PARTY_NOTICES.md")
NEVER = ("clients/meridian/pack.yaml", "deploy/profiles.yaml", "deploy/meridian/identities.yaml",
         "build/stores/meridian/run/tokens.json", "build/warehouses/retail.duckdb", "tests/unit/test_image.py", ".env",
         ".env.local", ".venv/bin/python", "requirements-dev.txt", "README.md", "scripts/access_demo.py", "api/.env",
         "api/tokens.json", "pipeline/passwords.json", "graph/server.pem", "eval/signing.key", "compiler/credentials.json",
         "landing/secrets.yaml", "kge/__pycache__/app.cpython-311.pyc", "estate/generate.pyc")


def instructions() -> list[tuple[str, str]]:
    """The Dockerfile's instructions, (KEYWORD, arguments), comments and continuations joined."""
    out, held = [], ""
    for line in DOCKERFILE.read_text().splitlines():
        if not held and (not line.strip() or line.lstrip().startswith("#")):
            continue
        held += line.rstrip("\\").strip() + " "
        if not line.endswith("\\"):
            word, _, rest = held.strip().partition(" ")
            out.append((word.upper(), rest.strip()))
            held = ""
    return out


def final_stage() -> list[tuple[str, str]]:
    """The instructions of the Dockerfile's last stage, the image itself, from its FROM on."""
    said = instructions()
    start = max(i for i, (word, _args) in enumerate(said) if word == "FROM")
    return said[start:]


def test_the_base_is_a_build_argument_whose_default_is_python_at_an_exact_version():
    """The base is BASE_IMAGE, declared before the first FROM, so a client builds on its own base without editing the
    file (issue #173). Its default is the slim one of an exact Python release on a named Debian suite, held to its
    digest, so a rebuild pushed under the same tag changes nothing: never a tag that moves. The one other stage is
    uv's, from UV_IMAGE, held to its digest too, and the image is built on the base alone."""
    said = instructions()
    first = next(i for i, (word, _args) in enumerate(said) if word == "FROM")
    args = dict(a.split("=", 1) for word, a in said[:first] if word == "ARG")
    pinned = r"python:(\d+\.\d+\.\d+)-slim-([a-z]+)@sha256:[0-9a-f]{64}"
    assert re.fullmatch(pinned, args.get("BASE_IMAGE", "")), f"{args}: the base's default, an exact slim release, by digest"
    assert re.fullmatch(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", args.get("UV_IMAGE", "")), args
    froms = [a for word, a in said if word == "FROM"]
    assert froms == ["${UV_IMAGE} AS uv", "${BASE_IMAGE}"], froms


def test_uv_installs_into_a_virtual_environment_of_the_base_s_python_3_12_and_pip_never_runs():
    """uv, the pinned one mounted from its stage or the base's own where UV_BIN names it, makes /app/.venv from the
    base's python3, never a Python it downloads, stops the build naming any other version than 3.12, and installs the
    pinned dependencies into it, then the platform in place, its setuptools only for the build. pip runs in no step of
    the image, and uv is never copied into it."""
    runs = [a for word, a in final_stage() if word == "RUN"]
    installs = [a for a in runs if '"$uv" ' in a]
    assert len(installs) == 2, runs
    for step in installs:
        assert "--mount=from=uv,source=/uv,target=/bin/uv" in step and "uv=${UV_BIN:-/bin/uv}" in step
        assert "UV_PYTHON_DOWNLOADS=never" in step and "UV_NO_CACHE=1" in step
    deps, platform = installs
    assert '"$uv" venv --python "$(command -v python3)" /app/.venv' in deps
    assert re.search(r'"\$version" != 3\.12 \]; then echo "[^"]*\$version[^"]*" >&2; exit 1', deps), "names the version"
    assert deps.index("version=") < deps.index('"$uv" venv'), "the version is checked before the environment is made"
    assert "-r requirements.txt -c constraints.txt" in deps and "--python /app/.venv/bin/python" in deps
    assert "--no-deps --build-constraints constraints.txt -e ." in platform and "--python /app/.venv/bin/python" in platform
    assert not any(re.search(r"(?<![\w-])pip3?(?![\w-])", a.replace("uv\" pip", "").replace('"$uv" pip', "")) for a in runs), runs
    assert not any(word == "COPY" and "--from=uv" in a for word, a in instructions()), "uv is not shipped"
    env = " ".join(a for word, a in final_stage() if word == "ENV")
    assert "PATH=/app/.venv/bin:$PATH" in env, "the environment's kge and python first on PATH"


def test_the_index_and_certificates_are_build_arguments_and_credentials_a_secret():
    """The index, the trust store and a CA bundle are build arguments, each reaching uv only when given; the CA bundle is
    SSL_CERT_FILE inside the install steps alone, never ENV, so kit/build.sh's guard still refuses an image that trusts
    one. A credential is the netrc secret, mounted for the install steps, never a build argument."""
    said = final_stage()
    declared = {a.split("=", 1)[0] for word, a in said if word == "ARG"}
    assert {"UV_BIN", "UV_DEFAULT_INDEX", "UV_SYSTEM_CERTS", "CA_BUNDLE", "KGE_VERSION"} == declared, declared
    assert not any(re.search(r"PASSWORD|TOKEN|NETRC|SECRET|CREDENTIAL", a, re.I) for word, a in instructions() if word == "ARG")
    env = " ".join(a for word, a in said if word == "ENV")
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT", "UV_", "NETRC", "CA_BUNDLE"):
        assert name not in env, f"{name} set in the image"
    for step in [a for word, a in said if word == "RUN" and '"$uv" ' in a]:
        assert "--mount=type=secret,id=netrc,required=false" in step
        assert "if [ -f /run/secrets/netrc ]; then export NETRC=/run/secrets/netrc; fi" in step
        assert 'if [ -n "$CA_BUNDLE" ]; then export SSL_CERT_FILE="$CA_BUNDLE"; fi' in step
        assert 'export UV_SYSTEM_CERTS UV_NATIVE_TLS="$UV_SYSTEM_CERTS"' in step
        assert 'if [ -n "$UV_DEFAULT_INDEX" ]; then export UV_DEFAULT_INDEX; else unset UV_DEFAULT_INDEX; fi' in step


def test_the_install_steps_run_as_root_and_the_image_as_uid_10001_in_group_0():
    """USER 0 straight after FROM, whatever the base's default user, and no other user until the last: 10001:0, by
    number, with no useradd, which a client's base may not have. That uid owns build/, clients/, deploy/ and its HOME,
    each group-writable, and no step runs after it."""
    said = final_stage()
    assert said[1] == ("USER", "0"), said[:2]
    users = [(i, a) for i, (word, a) in enumerate(said) if word == "USER"]
    assert [a for _i, a in users] == ["0", "10001:0"], users
    assert not any(word == "RUN" for word, _a in said[users[-1][0]:]), "every step runs as root"
    runs = " ".join(a for word, a in said if word == "RUN")
    assert not re.search(r"\b(useradd|adduser|groupadd|addgroup)\b", runs)
    assert "chown 10001:0 build clients deploy /home/kge" in runs and "chmod 0775 build clients deploy /home/kge" in runs


def test_home_is_the_run_time_user_s_own_folder_outside_build():
    """A uid with no passwd entry gets HOME=/, and the job's work upload takes all of build/: HOME is /home/kge."""
    env = " ".join(a for word, a in final_stage() if word == "ENV")
    home = re.search(r"\bHOME=(\S+)", env)
    assert home and home.group(1) == "/home/kge", env
    assert not home.group(1).startswith(("/app/build", "/app")) and home.group(1) != "/"


def test_the_image_sets_its_own_entrypoint_so_a_base_s_is_never_inherited():
    said = final_stage()
    assert [a for word, a in said if word == "ENTRYPOINT"] == ['["sh", "/app/entrypoint.sh"]']
    assert [a for word, a in said if word == "CMD"] == ['["serve"]']


def test_the_build_rewrites_the_entry_script_with_lf_ends(tmp_path):
    """A Windows checkout or copy may give entrypoint.sh CRLF ends, which sh cannot run (set: -: invalid option), and a
    minimal base has no sed or dos2unix: the step after the script is copied rewrites it with the base's Python. The
    rewrite is run here, as written, on a CRLF copy."""
    said = final_stage()
    copied = next(i for i, (word, a) in enumerate(said) if word == "COPY" and "entrypoint.sh" in a.split())
    step = next(a for word, a in said[copied:] if word == "RUN" and "/app/entrypoint.sh" in a)
    code = re.search(r"python3 -c '([^']+)'", step).group(1)
    script = tmp_path / "entrypoint.sh"
    script.write_bytes(ENTRY.read_bytes().replace(b"\n", b"\r\n"))
    subprocess.run([sys.executable, "-c", code.replace("/app/entrypoint.sh", str(script))], check=True)
    assert script.read_bytes() == ENTRY.read_bytes().replace(b"\r\n", b"\n")
    assert b"\r" not in ENTRY.read_bytes()


def test_the_folder_keeps_its_shell_and_docker_files_lf():
    """.gitattributes beside the Dockerfiles, so a copy of the folder carries it."""
    held = (DEMO / ".gitattributes").read_text().splitlines()
    for pattern in ("*.sh", "Dockerfile", "portal/Dockerfile", "kit/build.sh"):
        assert f"{pattern} text eol=lf" in held, pattern


def test_the_build_loads_every_shared_object_before_the_platform_is_installed():
    """kge/cli/check_libraries.py runs with the environment's Python right after the dependencies are installed, before the
    platform's install, which builds with the base's Python and adds no shared object."""
    said = final_stage()
    runs = [(i, a) for i, (word, a) in enumerate(said) if word == "RUN"]
    check = [i for i, a in runs if a == "/app/.venv/bin/python /app/kge/cli/check_libraries.py"]
    assert len(check) == 1, runs
    installs = [i for i, a in runs if '"$uv" ' in a]
    assert installs[0] < check[0] < installs[1]
    assert any(word == "COPY" and a.split()[0] == "kge" for word, a in said[:check[0]]), "the check is copied before it runs"


def check_libraries():
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_libraries", DEMO / "kge/cli/check_libraries.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_library_check_names_each_missing_library_and_the_package_whose_file_needs_it(tmp_path, monkeypatch, capsys):
    """The check opens every shared object, versioned ones too, and reports a missing library (the loader's 'cannot
    open shared object file') by the library and the distribution that installed the file; another loader error, a
    symbol another extension provides, is not a missing library. Each file is opened in a child process, run here in
    this one with a stand-in loader, and a file that ends its child (its own start-up code, outside an interpreter that
    imports it) is reported as opened, the next child going on from the file after it."""
    check = check_libraries()
    files = {name: tmp_path / name for name in ("_multiarray_umath.so", "libgfortran-1.so.5.0.0", "_lbug.so", "fine.so",
                                                  "symbol.so", "crash.so", "notes.txt", "so.py")}
    for path in files.values():
        path.write_bytes(b"")
    errors = {"_multiarray_umath.so": "libz.so.1: cannot open shared object file: No such file or directory",
              "libgfortran-1.so.5.0.0": "libz.so.1: cannot open shared object file: No such file or directory",
              "_lbug.so": "libssl.so.3: cannot open shared object file: No such file or directory",
              "symbol.so": "symbol.so: undefined symbol: PyInit_other"}
    opened = []

    class Ended(Exception):
        """The stand-in for a file whose start-up code ends the process."""

    def cdll(name):
        opened.append(Path(name).name)
        if Path(name).name == "crash.so":
            raise Ended
        if Path(name).name in errors:
            raise OSError(errors[Path(name).name])

    def child(argv, capture_output, text):
        assert argv[1].endswith("check_libraries.py") and argv[2] == check.CHILD
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = check.open_each(argv[3:])
        except Ended:
            code = -11
        return subprocess.CompletedProcess(argv, code, out.getvalue(), "")

    monkeypatch.setattr(check.ctypes, "CDLL", cdll)
    monkeypatch.setattr(check.subprocess, "run", child)
    monkeypatch.setattr(check.sys, "argv", ["check_libraries.py"])
    monkeypatch.setattr(check, "site_packages", lambda: [tmp_path.resolve()])
    owner = {"_multiarray_umath.so": "numpy", "libgfortran-1.so.5.0.0": "numpy", "_lbug.so": "ladybug", "fine.so": "x",
             "symbol.so": "y", "crash.so": "z"}
    monkeypatch.setattr(check, "owners", lambda roots: {files[n].resolve(): d for n, d in owner.items()})
    assert check.main() == 1
    assert sorted(opened) == sorted(owner), "every shared object once, and nothing else"
    captured = capsys.readouterr()
    assert "missing system library libz.so.1, needed by numpy (2 file(s)" in captured.err
    assert "missing system library libssl.so.3, needed by ladybug (1 file(s)" in captured.err
    assert "symbol" not in captured.err and "PyInit" not in captured.err and "crash.so" not in captured.err
    assert "crash.so found every library" in captured.out
    errors.clear()
    assert check.main() == 0
    assert "all 6 shared objects" in capsys.readouterr().out
    monkeypatch.setattr(check.sys, "argv", ["check_libraries.py", check.CHILD, str(files["fine.so"])])
    assert check.main() == 0 and capsys.readouterr().out == f"open\t{files['fine.so']}\n", "the child's own lines"


def test_the_library_check_names_a_file_s_distribution_by_its_record(tmp_path):
    """The owner of a file is the distribution whose RECORD lists it, read from its dist-info folder without
    importlib.metadata (which a base's Python missing libz.so.1 cannot import): this environment's pytest, and a
    stand-in distribution whose RECORD names a bundled, versioned library."""
    check = check_libraries()
    pytest_file = Path(pytest.__file__).resolve()
    assert check.owners([pytest_file.parents[1]]).get(pytest_file) == "pytest"
    info = tmp_path / "numpy-2.0.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: numpy\nVersion: 2.0.0\n\nbody Name: other\n")
    (info / "RECORD").write_text("numpy.libs/libgfortran-1.so.5.0.0,sha256=x,1\nnumpy-2.0.0.dist-info/RECORD,,\n")
    assert check.owners([tmp_path])[(tmp_path / "numpy.libs/libgfortran-1.so.5.0.0").resolve()] == "numpy"
    source = (DEMO / "kge/cli/check_libraries.py").read_text()
    assert "importlib.metadata" not in source.split('"""', 2)[2].split("def owners", 1)[0], "no importlib.metadata import"


def test_the_image_has_no_make():
    """Every command runs through kge, the package's console script (issue #170): no make stage, no make copied in,
    and the entry script calls no make."""
    said = instructions()
    assert not any(word == "FROM" and re.search(r"\bAS\s+make\b", args, re.I) for word, args in said), "no make stage"
    assert "/usr/bin/make" not in DOCKERFILE.read_text(), "no make copied into the image"
    assert not re.search(r"(?<![\w-])make (?=[a-z])", ENTRY.read_text().split("set -eu\n", 1)[1]), "the entry script runs kge"


def test_the_image_copies_the_platform_and_nothing_else():
    """The standards' notices are copied beside the dependency notices too, from the folder the image copies whole."""
    copied, staged = set(), []
    for word, args in instructions():
        assert word != "ADD", "COPY only: ADD fetches and unpacks"
        if word == "COPY" and "--from=" in args:
            staged.append(args)
        elif word == "COPY":
            *sources, _dest = [a for a in args.split() if not a.startswith("--")]
            copied |= {s.rstrip("/") for s in sources}
    assert copied == set(PLATFORM_DIRS) | set(PLATFORM_FILES) | {"standards/NOTICES.md"}, copied
    assert staged == [], "nothing copied from another image"


def test_build_is_a_volume_and_the_image_runs_as_no_root():
    said = instructions()
    volumes = " ".join(args for word, args in said if word == "VOLUME")
    assert "/app/build" in volumes, "build/ is a volume"
    users = [args for word, args in said if word == "USER"]
    assert users and users[-1] not in ("root", "0") and not users[-1].startswith("0:"), "it runs as a user of its own"
    assert [args for word, args in said if word == "EXPOSE"] == ["8000"]


def test_the_image_version_defaults_to_dev_and_can_be_set_by_the_builder():
    said = instructions()
    assert ("ARG", "KGE_VERSION=dev") in said
    assert ("ENV", "KGE_VERSION=$KGE_VERSION") in said


def rules() -> list[tuple[bool, re.Pattern]]:
    """The .dockerignore's patterns in order, each (excludes, pattern), translated as Docker reads them: * within one path
    segment, ** across any number, a leading ! letting back in what an earlier one left out."""
    out = []
    for line in IGNORE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keep = line.startswith("!")
        pat = line.lstrip("!").strip("/")
        rx, i = "", 0
        while i < len(pat):
            if pat.startswith("**/", i):
                rx, i = rx + "(?:.*/)?", i + 3
            elif pat.startswith("**", i):
                rx, i = rx + ".*", i + 2
            elif pat[i] == "*":
                rx, i = rx + "[^/]*", i + 1
            elif pat[i] == "?":
                rx, i = rx + "[^/]", i + 1
            elif pat[i] == "[":
                end = pat.index("]", i)
                rx, i = rx + pat[i:end + 1], end + 1
            else:
                rx, i = rx + re.escape(pat[i]), i + 1
        out.append((not keep, re.compile(rx)))
    return out


def sent(path: str, held: list[tuple[bool, re.Pattern]]) -> bool:
    """Whether Docker sends path in the build context: the last pattern that matches it, or a folder it is in, decides."""
    parts = path.split("/")
    prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    out = True
    for excludes, rx in held:
        if any(rx.fullmatch(p) for p in prefixes):
            out = not excludes
    return out


def folder_files() -> list[str]:
    """Every file of the demo folder, relative to it, but inside the folders no image may hold (the virtualenv, the
    build, Node's packages), which NEVER stands for."""
    out = []
    for root, dirs, files in os.walk(DEMO):
        rel = Path(root).relative_to(DEMO)
        if rel.parts and rel.parts[0] in (".venv", "build", "node_modules", ".pytest_cache"):
            dirs[:] = []
            continue
        out += [(rel / f).as_posix() for f in files]
    return out


def test_the_build_context_holds_the_platform_and_nothing_else():
    held = rules()
    context = {p for p in folder_files() if sent(p, held)}
    platform = {p for p in folder_files() if (p.split("/")[0] in PLATFORM_DIRS or p in PLATFORM_FILES)
                and "__pycache__" not in p.split("/") and not p.endswith(".pyc")}
    assert context - {"Dockerfile", ".dockerignore"} == platform, sorted(context ^ platform)
    assert {f"{d}/__init__.py" for d in PLATFORM_DIRS if d not in ("ui", "standards")} | {"ui/index.html", "meta-model.yaml",
                                                                                         "standards/preload.yaml",
                                                                                         "standards/NOTICES.md"} <= context
    leaked = [p for p in NEVER if sent(p, held)]
    assert leaked == [], f"in the build context: {leaked}"


RUNTIME = {"clients", "deploy", "build", ".env"}
RUNTIME |= {"examples"}
ROOTED = re.compile(r"""(?:(?<![.\w])(?:DEMO|APP|ROOT|root)|parents\[1\])\s*/\s*["']([^"'/]+)""")


def test_every_file_the_runtime_or_the_compiler_reads_from_the_demo_s_root_is_in_the_image():
    """A module the image runs reads only what the image copies: a folder named from the demo's root that the copy list
    leaves out (the preloaded standards, say) is a module missing from the image, found here before a deployment. A
    path named from the demo's root (DEMO, APP, ROOT or parents[1], never a store's self.root) to clients, deploy, build or a
    .env is mounted or written at run time; the invented examples are read from the kit's examples under the image,
    never from the image itself (kge/estate/onboard/source.py _question_files)."""
    named: dict[str, set[str]] = {}
    for path in [*(p for d in PLATFORM_DIRS for p in (DEMO / d).rglob("*.py"))]:
        for top in ROOTED.findall(path.read_text()):
            named.setdefault(top, set()).add(path.relative_to(DEMO).as_posix())
    assert {"meta-model.yaml", "standards", "clients", "deploy"} <= set(named), sorted(named)
    held = rules()
    copied = {s.rstrip("/") for word, args in instructions() if word == "COPY" and "--from=" not in args
              for s in [a for a in args.split() if not a.startswith("--")][:-1]}
    missing = {top: sorted(where) for top, where in named.items() if top not in RUNTIME
               and (top not in copied or not sent(top if (DEMO / top).is_file() else f"{top}/x", held))}
    assert missing == {}, f"read by the code, not copied into the image or left out of its build context: {missing}"


def test_the_ignore_rules_are_read_as_docker_reads_them():
    held = [(True, re.compile(r"[^/]*")), (False, re.compile(r"api")), (True, re.compile(r"(?:.*/)?__pycache__"))]
    assert sent("api/app.py", held) and not sent("api/__pycache__/app.pyc", held) and not sent("clients/x/pack.yaml", held)


def kge_path(folder: Path) -> str:
    """A PATH whose kge runs PY -m kge.cli from the working folder, as the image's console script runs the installed
    package: the entry script runs the code beside it, a scratch copy's too, never the checkout's editable install."""
    folder.mkdir(parents=True, exist_ok=True)
    shim = folder / "kge"
    shim.write_text('#!/bin/sh\nexec "${PY:-python3}" -m kge.cli "$@"\n')
    shim.chmod(0o755)
    return f"{folder}{os.pathsep}{os.environ['PATH']}"


@pytest.fixture
def entry(tmp_path):
    """The entry script, run beside a kge that records each call (its arguments, and what it sees of the environment)
    and exits KGE_EXIT; returns run(*args, env=) -> (completed process, calls)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "kge.jsonl"
    fake = bin_ / "kge"
    fake.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                    "seen = {k: os.environ.get(k) for k in ('PROFILE', 'PRINCIPAL', 'SIGNIN', 'DEMO_MODE', 'REPEATS')}\n"
                    "with open(os.environ['KGE_LOG'], 'a') as f:\n"
                    "    f.write(json.dumps({'args': sys.argv[1:], 'env': seen, 'cwd': os.getcwd()}) + '\\n')\n"
                    "sys.exit(int(os.environ.get('KGE_EXIT', '0')))\n")
    fake.chmod(0o755)
    clean = {k: v for k, v in os.environ.items() if k not in ("PROFILE", "PRINCIPAL", "SIGNIN", "DEMO_MODE", "API_URL", "REPEATS",
                                                              "STORE_URL", "LANDING_URL")}

    def run(*args, env=None):
        log.unlink(missing_ok=True)
        r = subprocess.run(["sh", str(ENTRY), *args], capture_output=True, text=True, cwd=tmp_path,
                           env={**clean, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "KGE_LOG": str(log),
                                "PY": sys.executable, **(env or {})})
        calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        return r, calls
    return run


def test_each_command_is_today_s_kge_commands(entry):
    for command, targets in (("seed", ["estate"]), ("improve", ["improve"]), ("sign-off", ["sign-off"]),
                             ("release", ["release"]), ("eval", ["ab"]), ("passwords", ["passwords"])):
        r, calls = entry(command)
        assert r.returncode == 0, r.stderr
        assert [c["args"] for c in calls] == [targets], (command, calls)
        assert calls[0]["cwd"] == str(DEMO), "run from the demo folder"


def test_every_top_level_package_the_entry_script_reaches_is_in_the_image():
    """Every Python package folder in the demo, but the compatibility shims the frozen snapshot tests import (api/,
    pipeline/, eval/), and every script entrypoint.sh, Makefile, or kge/services/onboard/jobs.py reaches is copied into the image and
    let through by .dockerignore; but scripts/, the developer's tools (make demo's scripts/side_by_side.py), never in the
    image, whose entry script runs no make demo."""
    packages = {p.parent.name for p in DEMO.glob("*/__init__.py") if p.parent.name != "tests"
                and not p.read_text().startswith('"""Compatibility shim')}
    sources = ENTRY.read_text() + "\n" + (DEMO / "Makefile").read_text() + "\n" + (DEMO / "kge" / "services" / "onboard" / "jobs.py").read_text()
    reached = {d for d in re.findall(r"(?<![/\w])([a-z_]+)/[a-z_]+\.py\b", sources) if d not in ("tests", "tools", "kit", "scripts")}
    copied = {
        s.rstrip("/")
        for word, args in instructions()
        if word == "COPY" and "--from=" not in args
        for s in [a for a in args.split() if not a.startswith("--")][:-1]
    }
    held = rules()
    for d in sorted(packages | reached):
        assert d in PLATFORM_DIRS, f"{d}: missing from PLATFORM_DIRS"
        assert d in copied, f"{d}: missing from Dockerfile COPY"
        assert sent(f"{d}/__init__.py", held), f"{d}: excluded by .dockerignore"


def test_serve_serves_on_every_address_at_the_port_with_api_url_from_the_environment(entry):
    r, calls = entry("serve", env={"API_URL": "http://127.0.0.1:8441", "PORT": "8441"})
    assert r.returncode == 0, r.stderr
    assert [c["args"] for c in calls] == [["serve", "HOST=0.0.0.0", "API=http://127.0.0.1:8441"]]
    _, calls = entry("serve")
    assert [c["args"] for c in calls] == [["serve", "HOST=0.0.0.0"]], "no API_URL: kge serve's own"


def test_serve_signs_in_with_passwords_and_never_in_demo_mode_unless_asked(entry):
    _, calls = entry("serve")
    assert calls[0]["env"]["SIGNIN"] == "passwords" and not calls[0]["env"]["DEMO_MODE"]
    _, calls = entry("serve", env={"SIGNIN": "tokens"})
    assert calls[0]["env"]["SIGNIN"] == "tokens", "the caller's wins"
    _, calls = entry("seed")
    assert calls[0]["env"]["SIGNIN"] is None, "serve's alone"


def unreleased(tmp_path: Path) -> dict:
    """PROFILES naming a registry whose profile reads the demo's packs and has released nothing, so the image's onboard
    loads and checks the candidate whatever this checkout has built."""
    registry = tmp_path / "profiles.yaml"
    registry.write_text("default: meridian\nprofiles:\n  meridian:\n    client: meridian\n"
                        f"    clients: {DEMO / 'clients'}\n    artifacts: {tmp_path / 'artifacts'}\n"
                        f"    store: {tmp_path / 'stores'}\n    landing: {tmp_path / 'landing'}\n"
                        f"    warehouses: {tmp_path / 'warehouses'}\n"
                        f"    identities: {DEMO / 'deploy' / 'meridian' / 'identities.yaml'}\n    baseline: null\n")
    return {"PROFILES": str(registry)}


def test_onboard_without_units_checks_compiles_loads_and_checks_the_candidate(entry, tmp_path):
    r, calls = entry("onboard", "REPEATS=1", env=unreleased(tmp_path))
    assert r.returncode == 0, r.stderr
    art = subprocess.run([sys.executable, "-m", "kge.compiler.compile", "--check"], capture_output=True, text=True, cwd=DEMO,
                         env={k: v for k, v in os.environ.items() if k not in ("PROFILE", "PROFILES")}).stdout.strip()
    assert [c["args"] for c in calls] == [["check", "compile", "REPEATS=1"], ["load", f"HASH={Path(art).name}", "REPEATS=1"],
                                          ["margin", f"HASH={Path(art).name}", "REPEATS=1"]], calls


def test_onboard_stops_at_the_first_step_that_fails(entry, tmp_path):
    stale = tmp_path / "stores" / "meridian" / "run" / "margin.status"
    stale.parent.mkdir(parents=True)
    stale.write_text("1\n")
    r, calls = entry("onboard", env={"KGE_EXIT": "2", **unreleased(tmp_path)})
    assert r.returncode == 2 and [c["args"] for c in calls] == [["check", "compile"]]
    assert not stale.exists()


def test_settings_after_the_command_are_kge_s_and_the_profile_is_the_environment_s(entry):
    r, calls = entry("sign-off", "PRINCIPAL=steward_glossary", env={"PROFILE": "thornbury"})
    assert r.returncode == 0, r.stderr
    assert calls[0]["args"] == ["sign-off", "PRINCIPAL=steward_glossary"]
    assert calls[0]["env"]["PRINCIPAL"] == "steward_glossary" and calls[0]["env"]["PROFILE"] == "thornbury"
    r, calls = entry("seed", "PROFILE=fellmoor")
    assert calls[0]["env"]["PROFILE"] == "fellmoor"


def test_an_unknown_command_or_argument_runs_nothing_and_names_the_commands(entry):
    for args in ((), ("demo",), ("serve", "--reload"), ("seed", "estate")):
        r, calls = entry(*args)
        assert r.returncode == 2 and calls == [], (args, calls)
        for command in ("serve", "seed", "onboard", "improve", "sign-off", "release", "eval", "baseline", "passwords", "setup-client",
                        "put-example", "releases"):
            assert command in r.stderr, (args, r.stderr)
        assert "SIGNIN=passwords" in r.stderr and "kge passwords" in r.stderr, "the help says how serve signs people in"
        assert "--entrypoint" not in r.stderr and not re.search(r"\bmake\b", r.stderr), "the image's own command, no make"


def test_the_readme_runs_the_image_as_the_entry_script_takes_it():
    """The README's Running it in a container, followed as written: each step a command the entry script takes, in
    onboarding's order, passwords before serve, the passwords read from the profile's run state, and serve with a
    terminal, through which Ctrl-C reaches the API."""
    from tests.scratch import profile_of
    section = (DEMO / "README.md").read_text().split("## Running it in a container", 1)[1].split("\n## ", 1)[0]
    block = [line.split("#")[0].strip() for line in section.split("```", 2)[1].splitlines() if line.strip()]
    assert block[0] == "docker build -t kge-demo ." and block[1].startswith("MOUNTS=(")
    taken = set(re.findall(r"^    ([a-z-]+)(?:\)|\s*\))", ENTRY.read_text(), re.M))
    steps = []
    for line in block[2:]:
        assert line.startswith('docker run --rm ') and '"${MOUNTS[@]}"' in line, line
        entry, _, args = line.partition(" kge-demo")
        override = re.search(r"--entrypoint (\S+)", entry)
        steps.append(f"{override.group(1)} {args.strip()}" if override else (args.split() or ["serve"])[0])
        assert override or steps[-1] in taken, f"{line}: no command of the entry script's"
    run = profile_of().run_dir().relative_to(DEMO)
    assert steps == ["seed", "onboard", "sign-off", "release", "passwords", f"cat {run}/passwords.txt", "serve"], steps
    assert " -it " in block[-1] and "-p 8000:8000" in block[-1], "serve with a terminal, on the README's port"
    assert "SIGNIN=passwords" in section and "kge passwords" in section and "--entrypoint make" not in section


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_answers_with_a_bearer_token_and_a_password_as_the_container_serves(tmp_path):
    """The real kge serve, through the entry script, on a scratch copy of the demo's build: on 0.0.0.0 at PORT, with
    API_URL the address it names itself by, SIGNIN=passwords as the container serves and no demo mode. The page loads
    and offers the password box, a caller with no token is a 401, and golden item G001 gets its figure asked with
    analyst_enterprise's bearer token, and asked by analyst_enterprise signed in with the password kge passwords made.
    A stop is a SIGTERM to the container's first process alone (docker stop, Cloud Run), which exec made kge: the API,
    kge's own child, shuts down as on Ctrl-C, its pid file removed, and nothing of the process group is left behind."""
    import httpx

    from kge.services.identity.service import token_for
    from tests.scratch import profile_of, scratch_build, script
    build = scratch_build(tmp_path / "build")
    made = script("kge/services/identity/cli.py", "--passwords", build=build)
    assert made.returncode == 0, made.stderr
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    env = {k: v for k, v in os.environ.items() if k not in ("PROFILE", "SIGNIN", "DEMO_MODE", "API_URL")}
    env.update({"PROFILES": str(build / "profiles.yaml"), "PORT": str(port), "API_URL": url,
                "PY": sys.executable, "MODEL_PROVIDER": "none",
                "PATH": kge_path(tmp_path / "bin")})
    log = open(tmp_path / "serve.log", "w")
    proc = subprocess.Popen(["sh", str(ENTRY), "serve"], cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        http = httpx.Client(base_url=url, timeout=120)
        deadline = time.time() + 120
        while time.time() < deadline:
            assert proc.poll() is None, (tmp_path / "serve.log").read_text()[-2000:]
            try:
                if http.get("/profile").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.3)
        else:
            pytest.fail(f"serve did not start: {(tmp_path / 'serve.log').read_text()[-2000:]}")
        page = http.get("/")
        assert page.status_code == 200 and "<title" in page.text
        assert http.get("/mode").json() == {"demo_mode": False, "passwords": True}, "the password box, no demo mode"
        golden = json.loads((DEMO / "build" / "golden.json").read_text())
        g001 = next(i for i in golden["items"] if i["id"] == "G001")
        assert http.post("/answer", json={"question": g001["question"]}).status_code == 401, "no token, no answer"
        run = profile_of(build).run_dir()
        bearer = {"authorization": f"Bearer {token_for(run, 'analyst_enterprise')}"}
        a = http.post("/answer", json={"question": g001["question"]}, headers=bearer)
        assert a.status_code == 200, a.text
        assert a.json()["behaviour"] == "figure" and a.json()["figure"] == float(g001["expect"]["figure"]), a.json()
        assert json.loads((run / "api.json").read_text())["url"] == url, "the address it names itself by: API_URL"
        plain = dict(line.split() for line in (run / "passwords.txt").read_text().splitlines() if line and line[0] != "#")
        wrong = http.post("/session", json={"username": "analyst_enterprise", "password": plain["steward"]})
        assert wrong.status_code == 401 and "set-cookie" not in wrong.headers, "another's password opens nothing"
        signed = http.post("/session", json={"username": "analyst_enterprise", "password": plain["analyst_enterprise"]})
        assert signed.status_code == 200 and signed.json()["id"] == "analyst_enterprise", signed.text
        a = http.post("/answer", json={"question": g001["question"]})
        assert a.status_code == 200 and a.json()["figure"] == float(g001["expect"]["figure"]), a.text
        http.close()
        os.kill(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
        assert not (run / "api.pid").exists(), (tmp_path / "serve.log").read_text()[-2000:]
        with pytest.raises(ProcessLookupError):
            os.killpg(proc.pid, 0)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        log.close()


def test_portal_image_includes_the_catalogue_contract():
    dockerfile = (DEMO / "portal/Dockerfile").read_text()
    assert "COPY server/contract ./contract" in dockerfile
    assert (DEMO / "portal/server/contract/catalogue.v1.schema.json").is_file()
