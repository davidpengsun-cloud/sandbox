"""Stored job acceptance on separate filesystems, with real jobs and socket-free HTTP.

Only curl and the Compose process launcher are replaced. The launcher executes
the image entrypoint in a copied platform, never the app's working directory.
No Docker daemon, Google service or model provider is used.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest

from kge.pipeline import objstore
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from tests.integration.test_role_playbooks_acceptance import CURL, GuideRun, PEOPLE, PrivatePasswords


DOCKER = r'''import os, pathlib, subprocess, sys
args = sys.argv[1:]
if args == ["compose", "restart", "app"]:
    os.execv(sys.executable, [sys.executable, str(pathlib.Path(__file__).with_name("curl")),
                            "-X", "POST", "http://localhost/__test_restart"])
if args[:5] != ["compose", "run", "--rm", "job", "onboard-request"] or len(args) != 5:
    sys.exit("unexpected Compose command")
root = pathlib.Path(os.environ["KGE_TEST_JOB_ROOT"])
env = {**os.environ, "PYTHONPATH": str(root), "PROFILES": str(root / "deploy/profiles.yaml")}
sys.exit(subprocess.run(["sh", str(root / "entrypoint.sh"), "onboard-request"], cwd=root, env=env).returncode)
'''


@pytest.fixture
def stored_scenario(tmp_path, monkeypatch):
    """Install a folder store, then start the app from only its published release."""
    from kge.services.identity.service import Throttle
    from kge.services.onboard import jobs
    import kge.app as appmod

    job_root, app_root = tmp_path / "job", tmp_path / "app"
    job_root.mkdir()
    app_root.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(mode=0o700)
    console = bin_dir / "kge"
    console.write_text(f"#!{sys.executable}\nfrom kge.cli import main\nraise SystemExit(main())\n")
    console.chmod(0o700)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])
    for name in ("kge", "standards"):
        shutil.copytree(ROOT / name, job_root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("entrypoint.sh", "meta-model.yaml"):
        shutil.copy2(ROOT / name, job_root / name)
    for key in ("LANDING_URL", "SIGNIN_PASSWORDS", "CLOUD_RUN_API", "K_SERVICE", "VERTEX_PROJECT", "RUN_REGION",
                "SERVE_HASH", "ENV", "BASELINE", "HASH", "CLIENT", "FROM", "ACCESS_ADMINS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {"MODEL_PROVIDER": "none", "TAGGER_PROVIDER": "rules", "EMBEDDING_PROVIDER": "hash",
                       "MODEL_HIGH_PROVIDER": "none", "MODEL_MEDIUM_PROVIDER": "none", "MODEL_LOW_PROVIDER": "none",
                       "WAREHOUSE_BACKEND": "duckdb", "RETRIEVAL_BACKEND": "graph", "APP_STATE_BACKEND": "files",
                       "GRAPH_STORE": "ladybug", "STORE_URL": str(tmp_path / "store"), "PROFILE": "meridian",
                       "SIGNIN": "passwords", "KGE_VERSION": "dev", "PY": sys.executable, "REPEATS": "1",
                       "PROFILES": str(app_root / "deploy/profiles.yaml")}.items():
        monkeypatch.setenv(key, value)
    job_env = {**os.environ, "PYTHONPATH": str(job_root), "PROFILES": str(job_root / "deploy/profiles.yaml")}

    def job(*args):
        result = subprocess.run(["sh", str(job_root / "entrypoint.sh"), *args], cwd=job_root,
                                env=job_env, capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, result.stdout + result.stderr

    job("setup-client", "CLIENT=meridian", "ASKER=analyst_enterprise", "BASELINE=baselines/issue-40.json")
    examples = tmp_path / "examples"
    shutil.copytree(ROOT / "clients/meridian", examples / "meridian/clients/meridian")
    job("put-example", f"FROM={examples}")
    store = objstore.the_store()
    store.put("deploy/meridian/identities.yaml", (ROOT / "deploy/meridian/identities.yaml").read_bytes())
    job("seed")
    job("onboard")
    job("sign-off", f"PRINCIPAL={PEOPLE['steward']}")
    job("release")
    job("passwords", "ROTATE=all")
    passwords = PrivatePasswords({line.split()[0]: line.split()[1]
                                  for line in store.get("secrets/meridian/passwords.txt").decode().splitlines()
                                  if line.strip() and not line.startswith("#")})
    objstore.before("serve", app_root)
    prof = resolve(registry=app_root / "deploy/profiles.yaml")
    monkeypatch.setattr(jobs, "ROOT", app_root)
    for key, value in {"PROFILE": prof, "PASSWORDS": True, "DEMO_MODE": False, "_engine": None,
                       "_combined": None, "_agent": None, "_ids": None, "UNRELEASED_MESSAGE": None,
                       "THROTTLE": Throttle()}.items():
        monkeypatch.setattr(appmod, key, value)
    mailbox, compose = tmp_path / "mailbox", tmp_path / "compose"
    for folder in (mailbox, compose):
        folder.mkdir(mode=0o700)
    shutil.copy2(ROOT / "kit/compose/compose.yaml", compose / "compose.yaml")
    for name, code in (("curl", CURL), ("docker", DOCKER)):
        path = bin_dir / name
        path.write_text(f"#!{sys.executable}\n" + code)
        path.chmod(0o700)
    environment = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                   "KGE_HTTP_MAILBOX": str(mailbox), "KGE_TEST_JOB_ROOT": str(job_root)}
    run = GuideRun(ROOT, tmp_path / "helper/state", environment)
    run.command("add", "compose", str(compose), "CLIENT=meridian")
    run.command("use", "compose/meridian")
    stop, errors, requests, statuses = threading.Event(), [], [], []
    with TestClient(appmod.app) as api:
        def serve():
            try:
                while not stop.wait(.01):
                    for path in mailbox.glob("*.request"):
                        held = json.loads(path.read_text())
                        url = urlsplit(held["url"])
                        if url.path == "/__test_restart":
                            appmod.switch()
                            reply = {"status": 200, "body": ""}
                        else:
                            response = api.request(held["method"], url.path, headers=held["headers"], content=held["body"])
                            reply = {"status": response.status_code, "body": response.text}
                            if url.path == "/onboard/run":
                                requests.append(json.loads(held["body"]))
                            if url.path == "/onboard" and response.status_code == 200:
                                statuses.append(response.json())
                        temporary = path.with_suffix(".tmp")
                        temporary.write_text(json.dumps(reply))
                        temporary.rename(path.with_suffix(".response"))
                        path.unlink()
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            yield run, prof, passwords, requests, statuses, job_root, appmod
        finally:
            stop.set()
            thread.join(timeout=10)
            assert not thread.is_alive(), "application transport did not stop"
            assert not errors, errors


def test_steward_playbook_follows_separate_stored_jobs(stored_scenario):
    run, prof, passwords, requests, statuses, job_root, appmod = stored_scenario
    original = prof.model_hash()
    assert prof.store != resolve(registry=job_root / "deploy/profiles.yaml").store
    for role in ("knowledge-engineer", "business"):
        run.as_person(role, passwords)
        run.playbook(role)
    run.as_person("steward", passwords)
    try:
        output = run.playbook("steward")
    finally:
        assert all("principal" not in request for request in requests)
        checked = next(status for status in statuses if (status.get("job") or {}).get("step") == "checks"
                       and status["job"]["state"] == "done")
        assert checked["candidate"]["loaded"]
        assert checked["candidate"]["gate"]["passed"] is True
        assert checked["eval_pre"]["passed"] is True
        assert checked["serving"] == original
        signed = next(status for status in statuses if (status.get("job") or {}).get("step") == "sign-off"
                      and status["job"]["state"] == "done")
        assert signed["candidate"]["signed_off"]["principal"] == PEOPLE["steward"]
    assert [request["step"] for request in requests] == ["checks", "sign-off", "release", "rollback"]
    assert all("principal" not in request for request in requests)
    served = [line.split()[1] for line in output.splitlines() if line.startswith("served: ")]
    assert len(served) == 3 and served[0] == served[2] == original and served[1] != original
    assert appmod.engine().profile.model_hash() == original
