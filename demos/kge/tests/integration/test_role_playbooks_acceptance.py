"""Execute the repository’s dev team guides against real services and jobs in an isolated local build.

The local transport substitutes only curl's wire: a filesystem mailbox forwards
requests to FastAPI's TestClient, so the acceptance needs no listening socket.
Every HTTP response, job, password and expectation comes from the application.
The optional Compose run uses these repository guides against a disposable installed kit with a running app,
emulator and job image, a released Meridian pack and the sample people/passwords
created by INSTALL. The guides prepare the unit and second release themselves. KGE_PLAYBOOK_COMPOSE names its compose folder and
KGE_PLAYBOOK_PASSWORDS a private JSON email-to-password file. Install the kit and
prepare that scratch scenario through its INSTALL first; this test neither pulls
images nor provisions a host. The running app and job must use MODEL_PROVIDER
none or mock, all three MODEL_*_PROVIDER tier overrides none or mock, hash
embeddings and local backends; changing this test's environment does not change
an already running container. It never selects a GCP environment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from kge.adapters import appstate
from kge.pipeline.settings import ROOT
from tests.scratch import BUILD, profile_of, registry, script, write_registry


HELPER = ROOT / "kit/scripts/kge"
ORDER = ("operator", "access-admin", "knowledge-engineer", "business", "steward")
PEOPLE = dict(zip(ORDER, ("operator", "access_admin", "knowledge_engineer", "business_team", "steward_glossary")))
PEOPLE = {role: f"{person}.meridian@example.com" for role, person in PEOPLE.items()}
CURL = r'''import json, os, pathlib, sys, time, uuid
args = sys.argv[1:]
def option(*names, default=None):
    return next((args[i + 1] for i, arg in enumerate(args[:-1]) if arg in names), default)
headers = {}
for i, arg in enumerate(args[:-1]):
    if arg in ("-H", "--header"):
        value = args[i + 1]
        lines = pathlib.Path(value[1:]).read_text().splitlines() if value.startswith("@") else [value]
        for line in lines:
            key, value = line.split(":", 1)
            headers[key] = value.strip()
body = option("--data-binary", "--data", "-d", default="")
if body.startswith("@"):
    body = pathlib.Path(body[1:]).read_text()
root = pathlib.Path(os.environ["KGE_HTTP_MAILBOX"])
name = uuid.uuid4().hex
request = {"method": option("-X", "--request", default="GET"), "url": args[-1], "headers": headers, "body": body}
pending = root / (name + ".tmp")
pending.write_text(json.dumps(request))
pending.rename(root / (name + ".request"))
response = root / (name + ".response")
deadline = time.monotonic() + 45
while not response.exists():
    if time.monotonic() >= deadline:
        sys.exit("real application request timed out")
    time.sleep(.01)
reply = json.loads(response.read_text())
response.unlink()
output = option("-o", "--output")
if output:
    pathlib.Path(output).write_text(reply["body"])
else:
    sys.stdout.write(reply["body"])
if option("-w", "--write-out"):
    sys.stdout.write(str(reply["status"]))
if reply["status"] >= 400 and any(arg in args for arg in ("-f", "-fsS", "--fail")):
    sys.exit(22)
'''


class GuideRun:
    def __init__(self, folder, state, environment, helper=HELPER):
        self.folder = folder
        self.state = state
        self.env = {**environment, "KGE_STATE": str(state)}
        self.helper = helper
        self.outputs = []

    def command(self, *arguments, private_input=False, timeout=600):
        done = subprocess.run(["bash", str(self.helper), *arguments], cwd=self.folder,
                              env=self.env, input=self.credentials if private_input else "",
                              capture_output=True, text=True, timeout=timeout)
        self.outputs.append((arguments, done.stdout))
        assert done.returncode == 0, f"kge {' '.join(arguments)}\n{done.stdout}\n{done.stderr}"
        return done.stdout

    def as_person(self, role, passwords):
        self.command("logout")
        self.credentials = passwords[PEOPLE[role]] + "\n"
        if role == "steward":
            self.credentials += passwords[PEOPLE["knowledge-engineer"]] + "\n" + passwords[PEOPLE[role]] + "\n"

    def playbook(self, role, *arguments):
        result = self.command("playbook", role, *arguments, private_input=True, timeout=1800)
        assert f"playbook {role}: passed" in result
        return result


class PrivatePasswords(dict):
    def __repr__(self):
        return "<private acceptance passwords>"


@pytest.fixture
def local_scenario(tmp_path, monkeypatch):
    """All writable profile data is copied, including release snapshots used by rollback."""
    from kge.pipeline.access_table import set_admins
    from kge.services.identity.service import Throttle
    import kge.app as appmod

    assert (BUILD / "artifacts").is_dir(), "run make graph pipeline before role-playbook acceptance"
    build = tmp_path / "build"
    shutil.copytree(BUILD, build, symlinks=True,
                    ignore=lambda folder, names: [name for name in names if name == "__pycache__" or
                                                  (Path(folder) == BUILD and name == "review")])
    clients = tmp_path / "clients"
    shutil.copytree(ROOT / "clients/meridian", clients / "meridian")
    write_registry(build, clients=clients, baseline=clients / "meridian/baselines/issue-40.json")
    prof = profile_of(build)
    assert prof.model_hash(), "make graph pipeline must release the local demo first"
    for key in ("STORE_URL", "LANDING_URL", "SIGNIN_PASSWORDS", "CLOUD_RUN_API", "K_SERVICE", "SERVE_HASH", "ENV"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules", "EMBEDDING_PROVIDER": "hash",
                       "MODEL_HIGH_PROVIDER": "none", "MODEL_MEDIUM_PROVIDER": "none", "MODEL_LOW_PROVIDER": "none",
                       "WAREHOUSE_BACKEND": "duckdb", "RETRIEVAL_BACKEND": "graph", "APP_STATE_BACKEND": "files",
                       "GRAPH_STORE": "ladybug",
                       "PROFILES": str(registry(build)), "PROFILE": prof.id, "SIGNIN": "passwords",
                       "PY": sys.executable, "REPEATS": "1"}.items():
        monkeypatch.setenv(key, value)
    state = appstate.access_state(prof)
    state.access_put({"username": PEOPLE["operator"], "name": "Operator", "roles": ["analyst_enterprise"]},
                     by=PEOPLE["operator"], via="job")
    set_admins(prof, [PEOPLE["access-admin"]], by=PEOPLE["operator"])
    passwords_job = script("kge.services.identity.cli", "--passwords", "--rotate-all", build=build)
    assert passwords_job.returncode == 0, passwords_job.stderr
    passwords = PrivatePasswords({row.split()[0]: row.split()[1]
                                  for row in (prof.run_dir() / "passwords.txt").read_text().splitlines()
                                  if row.strip() and not row.startswith("#")})
    margin = subprocess.run(["make", "margin", f"HASH={prof.model_hash()}", "REPEATS=1"], cwd=ROOT,
                            env=os.environ.copy(), capture_output=True, text=True, timeout=600)
    assert margin.returncode == 0, margin.stdout + margin.stderr
    upload = tmp_path / "upload"
    shutil.copytree(clients / "meridian", upload)
    pack = upload / "pack.yaml"
    pack.write_text(pack.read_text().replace("title: Meridian Union Bank governance graph",
                                           "title: Meridian playbook rehearsal", 1))
    for key, value in {"PROFILE": prof, "PASSWORDS": True, "DEMO_MODE": False, "_engine": None,
                       "_combined": None, "_agent": None, "_ids": None, "THROTTLE": Throttle()}.items():
        monkeypatch.setattr(appmod, key, value)
    mailbox = tmp_path / "mailbox"
    mailbox.mkdir(mode=0o700)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f"#!{sys.executable}\n" + CURL)
    curl.chmod(0o700)
    monkeypatch.setenv("KGE_HTTP_MAILBOX", str(mailbox))
    environment = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    run = GuideRun(ROOT, tmp_path / "helper/state", environment)
    run.command("add", "local", str(ROOT), "CLIENT=meridian")
    run.command("use", "local/meridian")
    run.command("config", f"PASSWORDS_FILE={prof.run_dir() / 'passwords.txt'}")
    stop = threading.Event()
    errors = []
    with TestClient(appmod.app) as api:
        def serve():
            try:
                while not stop.wait(.01):
                    for request in mailbox.glob("*.request"):
                        held = json.loads(request.read_text())
                        url = urlsplit(held["url"])
                        response = api.request(held["method"], url.path + ("?" + url.query if url.query else ""),
                                               headers=held["headers"], content=held["body"])
                        reply = request.with_suffix(".response")
                        temporary = reply.with_suffix(".tmp")
                        temporary.write_text(json.dumps({"status": response.status_code, "body": response.text}))
                        temporary.rename(reply)
                        request.unlink()
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield run, prof, passwords, upload, api
        finally:
            stop.set()
            thread.join(timeout=10)
            assert not thread.is_alive(), "application transport did not stop"
            assert not errors, errors


def test_every_role_guide_runs_against_the_local_demo(local_scenario):
    run, prof, passwords, upload, api = local_scenario
    original = prof.model_hash()
    run.as_person("operator", passwords)
    run.playbook("operator")
    assert prof.model_hash() == original, "upload must not release a candidate"
    assert appstate.access_state(prof).access_grants()
    run.as_person("access-admin", passwords)
    run.playbook("access-admin")
    assert prof.model_hash() == original, "access changes must leave the release unchanged"
    events = appstate.access_state(prof).access_audit()
    assert any(row["username"] == "analyst_retail.meridian@example.com" and row["after"] is None for row in events)
    run.as_person("knowledge-engineer", passwords)
    run.playbook("knowledge-engineer")
    run.as_person("business", passwords)
    run.playbook("business")
    run.as_person("steward", passwords)
    output = run.playbook("steward")
    served = [line.split()[1] for line in output.splitlines() if line.startswith("served: ")]
    assert len(served) == 3 and served[0] == served[2] == original and served[1] != original
    assert prof.model_hash() == original, "rollback must restore the original release"
    reviewed = {role for args, output in run.outputs if args[:1] == ("playbook",)
                for role in ORDER if f"playbook {role}: passed" in output}
    assert reviewed == set(ORDER)
    broken = run.state.parent.parent / "broken-kit"
    shutil.copytree(ROOT / "kit/scripts", broken / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (broken / "guides").mkdir()
    guide = (ROOT / "docs/playbooks/operator.md").read_text().replace("regex:^TARGET=(local|compose|gcp)$", "DELIBERATELY_MISSING_OUTPUT", 1)
    (broken / "guides/operator.md").write_text(guide)
    refused = subprocess.run(["bash", str(broken / "scripts/kge"), "playbook", "operator", "--from-step", "2",
                              "--through-step", "2"], cwd=run.folder, env=run.env, capture_output=True, text=True, timeout=30)
    assert refused.returncode != 0
    assert "step 2. Check the selected environment" in refused.stdout + refused.stderr
    assert "DELIBERATELY_MISSING_OUTPUT" in refused.stderr


@pytest.mark.skipif(not os.environ.get("KGE_PLAYBOOK_COMPOSE"), reason="requires disposable real Compose kit stack and sockets")
def test_every_dev_role_guide_runs_against_the_compose_kit(tmp_path):
    """Run repository playbooks against an explicitly supplied disposable local stack."""
    folder = Path(os.environ["KGE_PLAYBOOK_COMPOSE"]).resolve()
    helper = folder.parent / "scripts/kge"
    assert helper.is_file() and (folder / "compose.yaml").is_file()
    passwords_path = Path(os.environ["KGE_PLAYBOOK_PASSWORDS"])
    assert passwords_path.stat().st_mode & 0o077 == 0, "the acceptance passwords file must be private"
    passwords = PrivatePasswords(json.loads(passwords_path.read_text()))
    environment = {**os.environ, "MODEL_PROVIDER": "none"}
    environment.update({f"MODEL_{tier}_PROVIDER": "none" for tier in ("HIGH", "MEDIUM", "LOW")})
    environment.pop("ENV", None)
    run = GuideRun(folder, tmp_path / "helper/state", environment, HELPER)
    run.command("add", "compose", str(folder), "CLIENT=meridian")
    run.command("use", "compose/meridian")
    for role in ORDER:
        run.as_person(role, passwords)
        output = run.playbook(role)
        if role == "steward":
            hashes = [line.split()[1] for line in output.splitlines() if line.startswith("served: ")]
            assert len(hashes) == 3 and hashes[0] == hashes[2] and hashes[0] != hashes[1]
