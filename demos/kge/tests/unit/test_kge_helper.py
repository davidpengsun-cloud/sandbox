"""The installed helper dispatches through isolated commands and never reaches a cloud.

HTTP transport uses a curl double by default. Set KGE_HELPER_SOCKETS=1 to also exercise
curl against a loopback HTTP server on a host that permits sockets.
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import shutil
import signal
import shlex
import time
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


HELPER = Path(__file__).resolve().parents[2] / "kit" / "scripts" / "kge"
TOKEN = "personal-test-token"
PASSWORD = "<test-password>"
STANDIN = r'''import json, os, pathlib, signal, sys, time
from urllib.parse import urlparse
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["KGE_TEST_ROOT"])
if os.environ.get("DRY_RUN") == "yes":
    (root / "dry-run-invoked-tool").write_text(name)
    sys.exit("dry-run must not invoke " + name)
def log(value):
    with (root / "calls").open("a") as stream:
        stream.write(json.dumps(value) + "\n")
def option(short, long=None, default=None):
    for index, value in enumerate(args):
        if value in (short, long) and index + 1 < len(args):
            return args[index + 1]
        if long and value.startswith(long + "="):
            return value.split("=", 1)[1]
    return default
if name == "curl":
    url = next((value for value in reversed(args) if value.startswith(("http://", "https://"))), "")
    body = option("-d", "--data", option("--data-binary", "--data-raw", ""))
    if body.startswith("@"):
        body = sys.stdin.read() if body == "@-" else pathlib.Path(body[1:]).read_text()
    method = option("-X", "--request", "POST" if body else "GET")
    parsed = json.loads(body) if body and body.startswith("{") else body
    headers = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg in ("-H", "--header")]
    expanded = []
    for header in headers:
        expanded.extend(pathlib.Path(header[1:]).read_text().splitlines() if header.startswith("@") else [header])
    log({"tool": name, "method": method, "url": url, "body": parsed,
         "bearer": any("Authorization: Bearer personal-test-token" in h for h in expanded)})
    path = urlparse(url).path
    if path == "/healthz":
        port = str(urlparse(url).port)
        occupied = root / "occupied-port"
        if not (root / ("proxy-" + port)).exists() and not (occupied.exists() and occupied.read_text() == port):
            sys.exit(7)
    status = 503 if path == "/healthz" and (root / "unhealthy-proxy").exists() else 200
    value = {"ok": True}
    if path == "/onboard/run":
        value = {"job": {"id": "job-1", "state": "running"}}
    if path == "/whoami":
        value = {"id": "steward.meridian@example.com", "role": "steward"}
        if (root / "whoami.json").exists():
            value = json.loads((root / "whoami.json").read_text())
        if (root / "unauthorized").exists():
            status, value = 401, {"detail": "revoked"}
    if path == "/session/token":
        (root / "unauthorized").unlink(missing_ok=True)
        value = {"access_token": "personal-test-token", "token_type": "bearer", "expires_in": 3600}
    if "passwords.txt" in url:
        value = "steward.meridian@example.com <test-password>\n"
    content = value if isinstance(value, str) else json.dumps(value)
    output = option("-o", "--output")
    if output:
        pathlib.Path(output).write_text(content)
    else:
        sys.stdout.write(content)
    if option("-w", "--write-out"):
        sys.stdout.write(str(status))
    sys.exit(22 if status >= 400 and any(a in args for a in ("-f", "-fsS", "--fail", "--fail-with-body")) else 0)
log({"tool": name, "args": args, "cwd": os.getcwd()})
refusal = root / "refuse"
if refusal.exists():
    expected = json.loads(refusal.read_text())
    if name == expected["tool"] and args[:len(expected["args"])] == expected["args"]:
        print("passwords: merge base unavailable", file=sys.stderr)
        print("job diagnostic on stdout")
        sys.exit(9)
if name == "docker":
    (root / "docker-client").write_text(os.environ.get("CLIENT", ""))
if name == "make" and args[0] == "passwords":
    client = next(arg.split("=", 1)[1] for arg in args if arg.startswith("PROFILE="))
    source = pathlib.Path.cwd() / "build" / "stores" / client / "run" / "passwords.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("steward.meridian@example.com <test-password>\n")
if (name == "make" and args[0] == "passwords") or (name == "docker" and "passwords" in args) or (name == "gcloud" and any(arg.startswith("--args=passwords") for arg in args)):
    print("job generated <test-password>")
if name == "gcloud":
    if args[:3] == ["secrets", "versions", "add"]:
        file = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--data-file="))
        (root / "secret-version").write_text(pathlib.Path(file).read_text())
    elif args[:3] == ["secrets", "versions", "access"]:
        pathlib.Path(option("--out-file")).write_text('{"analyst.meridian@example.com":"old-hash"}')
    elif args[:2] == ["config", "get-value"]:
        if args[2] == "account":
            account = root / "account"
            print(account.read_text() if account.exists() else "operator.meridian@example.com")
        else:
            print("test-project" if args[2] in ("project", "core/project") else "us-central1")
    elif len(args) >= 3 and args[:1] == ["run"] and args[2] in ("list", "describe"):
        filename = args[1] + "-" + args[2]
        if args[2] == "describe":
            filename += "-" + args[3]
        file = root / (filename + ".json")
        print(file.read_text() if file.exists() else "[]")
    elif args[:3] == ["run", "jobs", "execute"]:
        replay = root / "execution.json"
        if replay.exists():
            value = json.loads(replay.read_text())
            print(json.dumps(value))
            completed = next(item for item in value["status"]["conditions"] if item["type"] == "Completed")
            sys.exit(0 if completed["status"] == "True" else 1)
    elif args[:3] == ["run", "services", "proxy"]:
        replay = root / "proxy.json"
        if replay.exists():
            value = json.loads(replay.read_text())
            print(value["stderr"], file=sys.stderr, flush=True)
            if value["exit_code"]:
                sys.exit(value["exit_code"])
        marker = root / ("proxy-" + option("--port"))
        (root / "unhealthy-proxy").unlink(missing_ok=True)
        marker.touch()
        def stop(*_):
            marker.unlink(missing_ok=True)
            sys.exit(0)
        signal.signal(signal.SIGTERM, stop)
        while True:
            time.sleep(.1)
    elif args[:2] == ["storage", "cp"] and len(args) >= 4:
        source, destination = args[-2:]
        if destination.startswith("gs://") and pathlib.Path(source).is_file():
            (root / ("uploaded-" + destination.rsplit("/", 1)[-1])).write_text(pathlib.Path(source).read_text())
        if source.startswith("gs://") and not destination.startswith("gs://"):
            pathlib.Path(destination).write_text("steward.meridian@example.com <test-password>\n" if source.endswith("passwords.txt") else '{"steward.meridian@example.com":"hash"}')
    elif args[:2] == ["storage", "cat"]:
        print("{}")
'''


class Helper:
    def __init__(self, root):
        self.root = root
        self.folder = root / "demo folder"
        self.folder.mkdir()
        (self.folder / "Makefile").touch()
        (self.folder / "compose.yaml").write_text("services: {}\n")
        self.state = root / "config" / "state"
        self.bin = root / "bin"
        self.bin.mkdir()
        for name in ("gcloud", "docker", "make", "curl"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + STANDIN)
            path.chmod(0o755)
        self.env = {**os.environ, "HOME": str(root), "KGE_STATE": str(self.state),
                    "KGE_TEST_ROOT": str(root), "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "MODEL_PROVIDER": "none"}
        self.env.pop("ENV", None)
        self.env.pop("PROFILE", None)

    def run(self, *args, input="", ok=True):
        result = subprocess.run([shutil.which("bash"), str(HELPER), *args], cwd=self.folder,
                                env=self.env, input=input, text=True, capture_output=True, timeout=60)
        if ok:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0, result.stdout + result.stderr
        assert result.stdout.startswith("kge: "), result.stdout
        assert PASSWORD not in result.stdout + result.stderr
        assert TOKEN not in result.stdout + result.stderr
        return result

    def read(self):
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(self.state)
        return config

    def write(self, target="gcp", client="meridian", **settings):
        config = self.read()
        name = f"{target}/{client}"
        config["kge"] = {"CURRENT": name}
        config[name] = {"TARGET": target, "CLIENT": client, "FOLDER": str(self.folder),
                        "PROJECT": "test-project", "REGION": "us-central1", "JOB": f"kge-{client}",
                        "STORE_URL": "gs://test-store", "PASSWORDS_SECRET": f"kge-{client}-passwords",
                        "GCS_URL": "http://127.0.0.1:4443", "SERVICE": f"kge-{client}", "URL": "http://127.0.0.1:8000", "AUTO": "on", **settings}
        self.state.parent.mkdir(exist_ok=True)
        with self.state.open("w") as stream:
            config.write(stream)
        self.state.chmod(0o600)
        return name

    def token(self, target="local", client="meridian"):
        path = self.state.parent / "tokens" / target / client
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TOKEN)
        path.chmod(0o600)
        return path

    def calls(self, tool=None):
        path = self.root / "calls"
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [call for call in calls if tool is None or call["tool"] == tool]

    def clear(self):
        (self.root / "calls").unlink(missing_ok=True)

    def forbid_runners(self):
        for name in ("curl", "gcloud", "docker", "make"):
            (self.bin / name).write_text(
                '#!/bin/sh\nprintf "%s\\n" "$0" >> "$KGE_TEST_ROOT/forbidden-runner"\nexit 99\n')


@pytest.fixture
def helper(tmp_path):
    value = Helper(tmp_path)
    yield value
    if value.state.exists():
        for section in value.read().values():
            pid = section.get("PID")
            if pid:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass


def test_add_use_config_and_private_state(helper):
    helper.run("add", "local", "CLIENT=meridian")
    assert helper.read()["kge"]["CURRENT"] == "local/meridian"
    assert helper.read()["local/meridian"]["FOLDER"] == str(helper.folder)
    assert helper.state.stat().st_mode & 0o777 == 0o600
    helper.run("add", "compose", str(helper.folder), "CLIENT=aster")
    helper.run("use", "local/meridian")
    helper.run("config", "AUTO=off")
    assert helper.read()["local/meridian"]["AUTO"] == "off"
    assert "AUTO=off" in helper.run("config").stdout
    assert "password" not in helper.state.read_text().lower()


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
@pytest.mark.parametrize("command,mapped,extra", [
    ("onboard", "onboard", []), ("release", "release", []),
    ("rollback", "release", ["PREVIOUS=yes"]), ("teardown", "teardown", []),
    ("setup-client", "setup-client", ["CLIENT=meridian"]), ("releases", "releases", []), ("access", "access", []),
])
def test_job_dispatch_exact_arguments_and_saved_folder(helper, target, command, mapped, extra):
    helper.write(target)
    if target == "local" and command == "teardown":
        result = helper.run(command, "NOTE=two words", ok=False)
        assert "teardown requires a Compose or GCP store" in result.stderr
        assert not helper.calls()
        return
    if target == "local" and command == "releases":
        helper.token()
        helper.run(command)
        assert helper.calls("curl")[-1]["url"].endswith("/versions")
        assert not helper.calls("make")
        return
    result = helper.run(command, "NOTE=two words")
    if target == "gcp":
        expected = ["run", "jobs", "execute", "kge-meridian", "--project", "test-project", "--region",
                    "us-central1", "--wait", "--args=" + ",".join([mapped, "NOTE=two words", *extra])]
        tool = "gcloud"
    elif target == "compose":
        expected = ["compose", "run", "--rm", "job", mapped, "NOTE=two words", *extra]
        tool = "docker"
    else:
        expected = [mapped, "PROFILE=meridian", "NOTE=two words", *extra]
        tool = "make"
    assert helper.calls(tool) == [{"tool": tool, "args": expected, "cwd": str(helper.folder)}]
    assert tool in result.stdout + result.stderr
    if target == "compose":
        assert (helper.root / "docker-client").read_text() == "meridian"


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_admins_keeps_comma_list_in_one_job_argument(helper, target):
    helper.write(target)
    helper.run("admins", "steward.meridian@example.com", "business.meridian@example.com", "BY=operator.meridian@example.com")
    call = helper.calls()[0]
    assert any("BY=operator.meridian@example.com" in arg for arg in call["args"])
    assert any("ADMINS=steward.meridian@example.com,business.meridian@example.com" in arg for arg in call["args"])
    assert any("access" in arg for arg in call["args"])


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_setup_client_accepts_only_the_selected_client(helper, target):
    helper.write(target)
    helper.run("setup-client", "CLIENT=meridian")
    args = helper.calls()[0]["args"]
    assert sum(arg.count("CLIENT=meridian") for arg in args) == 1
    helper.clear()
    for arg in ("CLIENT=aster", "CLIENT=", "PROFILE=meridian"):
        helper.run("setup-client", arg, ok=False)
    assert not helper.calls()


def test_admins_uses_gcp_account_for_audit(helper):
    helper.write()
    helper.run("admins", "steward.meridian@example.com")
    calls = [call["args"] for call in helper.calls()]
    assert calls[0] == ["config", "get-value", "account"]
    assert calls[1][-1] == "--args=access,ADMINS=steward.meridian@example.com,BY=operator.meridian@example.com"


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_admins_explicit_actor_is_passed_without_account_lookup(helper, target):
    helper.write(target)
    helper.run("admins", "BY=auditor.meridian@example.com", "steward.meridian@example.com")
    calls = helper.calls()
    assert len(calls) == 1
    assert any("BY=auditor.meridian@example.com" in arg for arg in calls[0]["args"])


@pytest.mark.parametrize("target", ["local", "compose"])
def test_admins_requires_explicit_actor_outside_gcp(helper, target):
    helper.write(target)
    result = helper.run("admins", "steward.meridian@example.com", ok=False)
    assert "BY=<operator email>" in result.stderr
    assert not helper.calls()


@pytest.mark.parametrize("account", ["", "(unset)", "lookup-fails"])
def test_admins_requires_actor_when_gcp_account_is_unavailable(helper, account):
    helper.write()
    (helper.root / "account").write_text(account)
    if account == "lookup-fails":
        (helper.root / "refuse").write_text(json.dumps({"tool": "gcloud", "args": ["config", "get-value"]}))
    result = helper.run("admins", "steward.meridian@example.com", ok=False)
    assert "BY=<operator email>" in result.stderr
    assert len(helper.calls()) == 1


@pytest.mark.parametrize("args", [("BY=",), ("BY=operator.meridian@example.com", "BY=engineer.meridian@example.com"), ("NAME=reader.meridian@example.com",)])
def test_admins_refuses_invalid_actor_arguments_before_calls(helper, args):
    helper.write()
    helper.run("admins", "steward.meridian@example.com", *args, ok=False)
    assert not helper.calls()


def test_environment_override_does_not_change_current(helper):
    helper.write("local", "meridian")
    helper.write("compose", "aster")
    result = helper.run("ENV=local/meridian", "release", "HASH=abc")
    assert result.stdout.splitlines()[0] == "kge: local/meridian"
    assert helper.calls("make")[0]["args"] == ["release", "PROFILE=meridian", "HASH=abc"]
    assert helper.read()["kge"]["CURRENT"] == "compose/aster"


@pytest.mark.parametrize("args", [("use", "local/missing"), ("onboard",), ("unknown",)])
def test_missing_environment_and_unknown_command_refuse(helper, args):
    result = helper.run(*args, ok=False)
    assert result.stderr.strip()
    assert not helper.calls()


def test_state_is_data_not_shell(helper):
    helper.write("local")
    helper.run("config", f"URL=$(touch {helper.root}/executed)")
    helper.run("config")
    assert not (helper.root / "executed").exists()


def resource(name, client, kind="services", store="gs://test-store"):
    env = [{"name": "PROFILE", "value": client}, {"name": "STORE_URL", "value": store},
           {"name": "LANDING_URL", "value": "gs://landing"},
           {"name": "VERTEX_PROJECT", "value": "model-project"},
           {"name": "VERTEX_REGION", "value": "global"},
           {"name": "VERTEX_EMBEDDING_REGION", "value": "us-east1"},
           {"name": "SIGNIN_PASSWORDS", "valueFrom": {"secretKeyRef": {"name": name + "-passwords", "key": "latest"}}}]
    template = {"spec": {"containers": [{"env": env}]}}
    return {"metadata": {"name": name, "labels": {"cloud.googleapis.com/location": "us-central1"}},
            "spec": {"template": template if kind == "services" else {"spec": {"template": template}}},
            "status": {"url": f"https://{name}.example.invalid"}}


def discovery(helper):
    for kind in ("services", "jobs"):
        items = [resource("kge-" + client, client, kind) for client in ("meridian", "aster")]
        (helper.root / f"{kind}-list.json").write_text(json.dumps(items))
        for item in items:
            (helper.root / f"{kind}-describe-{item['metadata']['name']}.json").write_text(json.dumps(item))


def test_sync_discovers_two_clients_and_is_idempotent_and_read_only(helper):
    discovery(helper)
    result = helper.run("sync", "PROJECT=test-project", "REGION=us-central1")
    assert "new" in result.stdout.lower()
    config = helper.read()
    for client in ("aster", "meridian"):
        env = config[f"gcp/{client}"]
        assert env["JOB"] == f"kge-{client}"
        assert env["SERVICE"] == f"kge-{client}"
        assert env["PASSWORDS_SECRET"] == f"kge-{client}-passwords"
        assert env["STORE_URL"] == "gs://test-store"
        assert env["LANDING_URL"] == "gs://landing"
        assert env["VERTEX_EMBEDDING_REGION"] == "us-east1"
        assert env["URL"] == f"https://kge-{client}.example.invalid"
    before = helper.state.read_bytes()
    helper.run("sync", "PROJECT=test-project", "REGION=us-central1")
    assert helper.state.read_bytes() == before
    assert all(call["args"][2] in ("list", "describe") or call["args"][0] == "config" for call in helper.calls("gcloud"))
    updated = resource("kge-meridian", "meridian", store="gs://new-store")
    (helper.root / "services-describe-kge-meridian.json").write_text(json.dumps(updated))
    (helper.root / "jobs-describe-kge-meridian.json").write_text(json.dumps(resource("kge-meridian", "meridian", "jobs", "gs://new-store")))
    assert "changed" in helper.run("sync", "PROJECT=test-project", "REGION=us-central1").stdout.lower()
    assert helper.read()["gcp/meridian"]["STORE_URL"] == "gs://new-store"


def test_setup_uses_read_only_discovery(helper):
    discovery(helper)
    helper.run("setup", "PROJECT=test-project", "REGION=us-central1")
    assert "gcp/meridian" in helper.read()
    assert not any("update" in call["args"] or "deploy" in call["args"] for call in helper.calls("gcloud"))


def test_gcp_password_flow_updates_secret_before_plain_download_and_delete(helper):
    helper.write()
    helper.run("passwords", "ROTATE=all")
    calls = [call["args"] for call in helper.calls("gcloud")]
    assert calls[0][:4] == ["run", "jobs", "execute", "kge-meridian"]
    assert "passwords" in calls[0][-1] and "ROTATE=all" in calls[0][-1]
    secret = next(i for i, call in enumerate(calls) if call[:3] == ["secrets", "versions", "add"])
    plain = next(i for i, call in enumerate(calls) if call[:2] == ["storage", "cp"] and any(arg.endswith("/passwords.txt") for arg in call))
    deletes = [i for i, call in enumerate(calls) if call[:2] == ["storage", "rm"]]
    assert 0 < secret < plain < min(deletes)
    assert "kge-meridian-passwords" in calls[secret]
    assert json.loads((helper.root / "secret-version").read_text()) == {"steward.meridian@example.com": "hash"}
    assert all("--all-versions" in calls[index] for index in deletes)
    deleted = " ".join(arg for index in deletes for arg in calls[index])
    assert "/passwords.json" in deleted and "/passwords.txt" in deleted
    path = Path(calls[plain][-1])
    assert path.stat().st_mode & 0o777 == 0o600
    assert PASSWORD in path.read_text()
    assert PASSWORD not in helper.state.read_text()


@pytest.mark.parametrize("rotate", [False, True])
@pytest.mark.parametrize("revision", [False, True])
@pytest.mark.parametrize("secret", ["kge-meridian-passwords", "projects/secret-project/secrets/shared-passwords"])
def test_gcp_password_merge_pointer_and_revision_order(helper, rotate, revision, secret):
    helper.write(PASSWORDS_SECRET=secret)
    args = (["ROTATE=all"] if rotate else []) + (["REVISION=yes"] if revision else [])
    before = int(time.time())
    result = helper.run("passwords", *args)
    after = int(time.time())
    calls = [call["args"] for call in helper.calls("gcloud")]
    job_index = 0 if rotate else 2
    if not rotate:
        base = calls[0][-1]
        assert Path(base).name == "base.json"
        assert calls[0] == ["secrets", "versions", "access", "latest", "--secret", secret,
                            "--project", "test-project", "--out-file", base]
        assert calls[1] == ["storage", "cp", base, "gs://test-store/secrets/meridian/passwords.json"]
        assert json.loads((helper.root / "uploaded-passwords.json").read_text()) == {"analyst.meridian@example.com": "old-hash"}
        assert not Path(base).exists()
    else:
        assert not any(call[:3] == ["secrets", "versions", "access"] for call in calls)
    assert calls[job_index][-1] == "--args=passwords" + (",ROTATE=all" if rotate else "")
    assert calls[job_index + 1][:3] == ["storage", "cp", "gs://test-store/secrets/meridian/passwords.json"]
    assert calls[job_index + 2][:4] == ["secrets", "versions", "add", secret]
    pointer = calls[job_index + 3]
    assert pointer[:2] == ["storage", "cp"]
    assert pointer[-1] == "gs://test-store/deploy/meridian/passwords-secret"
    resource = secret if secret.startswith("projects/") else f"projects/test-project/secrets/{secret}"
    assert (helper.root / "uploaded-passwords-secret").read_text() == resource + "\n"
    assert not Path(pointer[2]).exists()
    assert calls[job_index + 4][:3] == ["storage", "cp", "gs://test-store/secrets/meridian/passwords.txt"]
    assert calls[job_index + 5][:3] == ["storage", "rm", "--all-versions"]
    assert "within a minute once it supports the passwords-secret pointer" in result.stdout
    printed = next(shlex.split(line)[1:] for line in result.stderr.splitlines()
                   if line.startswith("+ gcloud run services update"))
    assert printed[:9] == ["gcloud", "run", "services", "update", "kge-meridian",
                           "--project", "test-project", "--region", "us-central1"]
    assert printed[-1].startswith("--revision-suffix=passwords-")
    assert before <= int(printed[-1].rsplit("-", 1)[-1]) <= after
    if revision:
        assert calls[job_index + 6] == printed[1:]
    else:
        assert "Otherwise, run this exact command" in result.stdout
        assert not any(call[:3] == ["run", "services", "update"] for call in calls)
    assert len(calls) == job_index + 6 + int(revision)


@pytest.mark.parametrize("failed", [["secrets", "versions", "access"], ["storage", "cp"]])
def test_password_merge_base_failure_stops_before_job(helper, failed):
    helper.write()
    (helper.root / "refuse").write_text(json.dumps({"tool": "gcloud", "args": failed}))
    helper.run("passwords", ok=False)
    assert not any(call["args"][:3] == ["run", "jobs", "execute"] for call in helper.calls())


def test_password_revision_requires_service_before_any_calls(helper):
    helper.write(SERVICE="")
    helper.run("passwords", "REVISION=yes", ok=False)
    assert not helper.calls()


def test_gcp_upload_verifies_pack_before_onboarding(helper):
    helper.write()
    pack = helper.root / "pack"
    pack.mkdir()
    (pack / "pack.yaml").write_text("client: meridian\n")
    (pack / "nested").mkdir()
    (pack / "nested" / "source.csv").write_text("id\n1\n")
    helper.run("upload", str(pack))
    calls = [call["args"] for call in helper.calls("gcloud")]
    assert calls[0][:2] == ["storage", "cp"]
    assert any("gs://test-store/uploads/meridian/" in arg for arg in calls[0])
    assert any("pack.yaml" in arg for call in calls[1:-1] for arg in call)
    assert calls[-1][:3] == ["run", "jobs", "execute"]
    assert "onboard" in calls[-1][-1] and "FROM=gs://test-store/uploads/meridian/" in calls[-1][-1]


def test_upload_without_pack_refuses_before_any_external_command(helper):
    helper.write()
    helper.run("upload", str(helper.folder), ok=False)
    assert not helper.calls()


def test_proxy_starts_reuses_and_stops(helper):
    helper.write(PORT="18765")
    helper.run("proxy")
    first = helper.read()["gcp/meridian"]
    assert int(first["PID"]) > 0
    assert first["PORT"] == "18765"
    assert first["URL"] == "http://localhost:18765"
    helper.run("proxy")
    assert len(helper.calls("gcloud")) == 1
    assert helper.calls("gcloud")[0]["args"] == ["run", "services", "proxy", "kge-meridian", "--project", "test-project", "--region", "us-central1", "--port", "18765"]
    helper.run("proxy", "stop")
    assert not helper.read()["gcp/meridian"].get("PID")


@pytest.mark.parametrize("failure", ["unresponsive", "http503"])
def test_proxy_restarts_live_but_unhealthy_saved_process(helper, failure):
    helper.write(PORT="18764")
    helper.run("proxy")
    old_pid = int(helper.read()["gcp/meridian"]["PID"])
    os.kill(old_pid, 0)
    if failure == "unresponsive":
        (helper.root / "proxy-18764").unlink()
    else:
        (helper.root / "unhealthy-proxy").touch()
    helper.clear()
    helper.run("proxy")
    calls = helper.calls()
    assert calls[0]["tool"] == "curl" and calls[0]["url"].endswith("/healthz")
    assert len(helper.calls("gcloud")) == 1
    assert int(helper.read()["gcp/meridian"]["PID"]) != old_pid
    assert (helper.root / "proxy-18764").exists()
    helper.run("proxy", "stop")


@pytest.mark.parametrize("target", ["local", "compose"])
def test_local_targets_have_no_proxy(helper, target):
    helper.write(target)
    helper.run("proxy")
    helper.run("proxy", "stop")
    assert not helper.calls("gcloud")


def test_login_writes_private_personal_token_without_password_in_state(helper):
    helper.write("local")
    helper.run("login", input=f"steward.meridian@example.com\n{PASSWORD}\n")
    calls = helper.calls("curl")
    signin = [call for call in calls if call["url"].endswith("/session") or call["url"].endswith("/session/token")]
    assert [call["url"].rsplit("/", 1)[-1] for call in signin] == ["token"]
    assert all(call["method"] == "POST" and call["body"] == {"username": "steward.meridian@example.com", "password": PASSWORD} for call in signin)
    token = helper.state.parent / "tokens/local/meridian"
    assert token.read_text().strip() == TOKEN
    assert token.stat().st_mode & 0o777 == 0o600
    assert PASSWORD not in helper.state.read_text() and TOKEN not in helper.state.read_text()
    helper.run("logout")
    assert not token.exists()


def test_connect_reuses_valid_token_and_disconnect_forgets_it(helper):
    helper.write("local")
    token = helper.token()
    result = helper.run("connect")
    assert "connected: local/meridian as steward.meridian@example.com (steward)" in result.stdout
    assert not any(call["url"].endswith("/session/token") for call in helper.calls("curl"))
    helper.run("disconnect")
    assert not token.exists()


@pytest.mark.parametrize("shape", [{"role": "steward"}, {"role": ["steward"]},
                                  {"roles": ["steward"]}, {"roles": "steward"}])
def test_connect_reports_real_whoami_role_and_plural_roles(helper, shape):
    helper.write("local")
    helper.token()
    (helper.root / "whoami.json").write_text(json.dumps({"id": "steward.meridian@example.com", **shape}))
    result = helper.run("connect")
    assert "connected: local/meridian as steward.meridian@example.com (steward)" in result.stdout


@pytest.mark.parametrize("command,step", [("checks", "checks"), ("improve", "improve"), ("sign-off", "sign-off"), ("publish", "release"), ("unpublish", "rollback")])
def test_api_role_commands_use_bearer_and_never_impersonate(helper, command, step):
    helper.write("local")
    helper.token()
    helper.run(command, "UNIT=retail", "HASH=abc")
    call = helper.calls("curl")[-1]
    assert call["method"] == "POST" and call["url"].endswith("/onboard/run")
    assert call["body"] == {"step": step, "unit": "retail", "hash": "abc"}
    assert call["bearer"]
    assert "principal" not in call["body"]
    assert not helper.calls("make")


@pytest.mark.parametrize("args,method,path,body", [
    (("grant", "business.meridian@example.com", "ROLES=steward,business", "NAME=Bob B"), "PUT", "/access/business.meridian%40example.com", {"roles": ["steward", "business"], "name": "Bob B"}),
    (("revoke", "business.meridian@example.com"), "DELETE", "/access/business.meridian%40example.com", ""),
    (("list",), "GET", "/access", ""), (("audit",), "GET", "/access/audit", ""),
])
def test_access_api_paths_and_bodies(helper, args, method, path, body):
    helper.write("local")
    helper.token()
    helper.run(*args)
    call = helper.calls("curl")[-1]
    assert call["method"] == method and call["url"].endswith(path)
    assert call["body"] == body and call["bearer"]


def test_auto_connect_logs_in_before_api_call(helper):
    helper.write("local")
    helper.run("checks", input=f"steward.meridian@example.com\n{PASSWORD}\n")
    calls = helper.calls("curl")
    assert any(call["url"].endswith("/session/token") for call in calls[:-1])
    assert calls[-1]["url"].endswith("/onboard/run") and calls[-1]["bearer"]


def test_auto_off_without_token_names_connect_command(helper):
    helper.write("local", AUTO="off")
    result = helper.run("checks", ok=False)
    assert "kge connect" in result.stderr + result.stdout
    assert not helper.calls()


def test_revoked_token_with_auto_off_refuses_before_action(helper):
    helper.write("local", AUTO="off")
    helper.token()
    (helper.root / "unauthorized").touch()
    helper.run("checks", ok=False)
    assert not any(call["url"].endswith("/onboard/run") for call in helper.calls("curl"))


@pytest.mark.skipif(os.environ.get("KGE_HELPER_SOCKETS") != "1", reason="KGE_HELPER_SOCKETS=1 enables loopback acceptance outside the socket-restricted sandbox")
def test_login_and_api_against_http_server(helper):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def answer(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else None
            requests.append((self.command, self.path, body, self.headers.get("Authorization")))
            result = {"id": "steward.meridian@example.com", "role": "steward"}
            if self.path == "/session/token":
                result = {"access_token": TOKEN, "token_type": "bearer", "expires_in": 3600}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = answer
        do_POST = answer

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        (helper.bin / "curl").unlink()
        helper.write("local", URL=f"http://127.0.0.1:{server.server_port}")
        try:
            helper.run("checks", input=f"steward.meridian@example.com\n{PASSWORD}\n")
        finally:
            server.shutdown()
            thread.join()
    assert ("POST", "/onboard/run", {"step": "checks"}, f"Bearer {TOKEN}") in requests
    assert next(body for _, path, body, _ in requests if path == "/session/token") == {"username": "steward.meridian@example.com", "password": PASSWORD}


def test_help_names_distinct_operator_and_steward_commands(helper):
    result = helper.run("help")
    assert "(F5)" not in result.stdout
    assert 'INSTALL.md, "Passwords and the service"' in result.stdout
    for name in ("release", "rollback", "publish", "unpublish", "connect", "AUTO=off", "Python 3"):
        assert name in result.stdout


@pytest.mark.parametrize("setting", ["PASSWORD=secret", "TOKEN=secret", "SIGNIN_PASSWORDS=secret"])
def test_configuration_refuses_secret_values(helper, setting):
    helper.write("local")
    before = helper.state.read_bytes()
    helper.run("config", setting, ok=False)
    assert helper.state.read_bytes() == before


def test_auto_connect_replaces_revoked_token_before_action(helper):
    helper.write("local")
    helper.token()
    (helper.root / "unauthorized").touch()
    helper.run("checks", input=f"steward.meridian@example.com\n{PASSWORD}\n")
    calls = helper.calls("curl")
    token = next(i for i, call in enumerate(calls) if call["url"].endswith("/session/token"))
    assert token < len(calls) - 1
    assert calls[-1]["url"].endswith("/onboard/run") and calls[-1]["bearer"]


def test_api_refuses_principal_impersonation_argument(helper):
    helper.write("local")
    helper.token()
    helper.run("checks", "PRINCIPAL=reader.meridian@example.com", ok=False)
    assert not any(call["url"].endswith("/onboard/run") for call in helper.calls("curl"))


def test_local_passwords_copies_private_file_without_cloud_calls(helper):
    helper.write("local")
    result = helper.run("passwords", "ROTATE=all")
    assert helper.calls("make")[0]["args"] == ["passwords", "PROFILE=meridian", "ROTATE=all"]
    assert not helper.calls("gcloud") and not helper.calls("curl")
    path = Path(result.stdout.splitlines()[-1])
    assert path.stat().st_mode & 0o777 == 0o600
    assert PASSWORD in path.read_text()


def test_compose_passwords_downloads_then_deletes_only_plain_file(helper):
    helper.write("compose")
    result = helper.run("passwords", "ROTATE=all")
    assert "Restart app to read the merged hashes: kge restart" in result.stdout
    assert helper.calls("docker")[0]["args"] == ["compose", "run", "--rm", "job", "passwords", "ROTATE=all"]
    calls = helper.calls("curl")
    assert [call["method"] for call in calls] == ["GET", "DELETE"]
    assert all("secrets%2Fmeridian%2Fpasswords.txt" in call["url"] for call in calls)
    assert not helper.calls("gcloud")
    path = Path(result.stdout.splitlines()[-1])
    assert path.stat().st_mode & 0o777 == 0o600
    assert PASSWORD in path.read_text()


def test_local_upload_passes_pack_folder_to_make(helper):
    helper.write("local")
    pack = helper.root / "pack with spaces"
    pack.mkdir()
    (pack / "pack.yaml").write_text("client: meridian\n")
    helper.run("upload", str(pack), "MODE=check")
    assert helper.calls("make")[0]["args"] == ["onboard", "PROFILE=meridian", f"FROM={pack}", "MODE=check"]
    assert not helper.calls("gcloud") and not helper.calls("curl")


def test_compose_upload_posts_every_file_checks_pack_then_onboards(helper):
    helper.write("compose")
    pack = helper.root / "pack"
    pack.mkdir()
    (pack / "pack.yaml").write_text("client: meridian\n")
    (pack / "rows.csv").write_text("id\n1\n")
    helper.run("upload", str(pack))
    calls = helper.calls()
    assert [call["method"] for call in calls[:-1]] == ["POST", "POST", "GET"]
    assert all("uploads%2Fmeridian%2F" in call["url"] for call in calls[:-1])
    assert "pack.yaml" in calls[-2]["url"]
    assert calls[-1]["tool"] == "docker"
    assert calls[-1]["args"][:5] == ["compose", "run", "--rm", "job", "onboard"]
    assert calls[-1]["args"][5].startswith("FROM=gs://test-store/uploads/meridian/")


def test_default_state_location_is_under_private_home(helper):
    helper.env.pop("KGE_STATE")
    helper.run("add", "local", "CLIENT=meridian")
    path = helper.root / ".config" / "kge" / "state"
    assert path.exists() and path.stat().st_mode & 0o777 == 0o600
    assert not helper.state.exists()


def test_environment_variable_override_does_not_persist(helper):
    helper.write("local", "meridian")
    helper.write("compose", "aster")
    helper.env["ENV"] = "local/meridian"
    helper.token()
    helper.run("releases")
    assert helper.calls("curl")[-1]["url"].endswith("/versions")
    assert not helper.calls("make")
    assert helper.read()["kge"]["CURRENT"] == "compose/aster"


def test_tokens_are_not_reused_for_another_client(helper):
    helper.write("local", "meridian")
    helper.token()
    helper.write("local", "aster", AUTO="off")
    helper.run("checks", ok=False)
    assert not helper.calls("curl")
    assert (helper.state.parent / "tokens/local/meridian").exists()


def test_gcp_api_uses_proxy_and_disconnect_clears_proxy_and_token(helper):
    helper.write("gcp", PORT="18766")
    helper.token("gcp")
    helper.run("checks")
    calls = helper.calls("curl")
    assert all(call["url"].startswith("http://localhost:18766/") for call in calls)
    assert calls[-1]["url"].endswith("/onboard/run") and calls[-1]["bearer"]
    helper.run("disconnect")
    assert not helper.read()["gcp/meridian"].get("PID")
    assert not (helper.state.parent / "tokens/gcp/meridian").exists()


@pytest.mark.parametrize("target,setting,command", [
    ("gcp", "JOB", "onboard"), ("gcp", "PROJECT", "release"), ("gcp", "REGION", "passwords"),
    ("gcp", "JOB", "passwords"), ("gcp", "PASSWORDS_SECRET", "passwords"),
    ("local", "FOLDER", "onboard"), ("compose", "FOLDER", "onboard"),
    ("compose", "GCS_URL", "passwords"), ("gcp", "SERVICE", "proxy"),
])
def test_missing_runner_settings_refuse_before_external_calls(helper, target, setting, command):
    helper.write(target, **{setting: ""})
    result = helper.run(command, ok=False)
    assert setting in result.stdout + result.stderr
    assert not helper.calls()


def test_proxy_selects_an_unused_saved_port_for_another_client(helper):
    helper.write("gcp", "meridian", PORT="8085")
    helper.run("proxy")
    helper.write("gcp", "aster")
    helper.run("proxy")
    assert helper.read()["gcp/meridian"]["PORT"] == "8085"
    assert helper.read()["gcp/aster"]["PORT"] == "8086"
    assert len(helper.calls("gcloud")) == 2


def test_explicit_duplicate_proxy_port_refuses(helper):
    helper.write("gcp", "meridian", PORT="18765")
    helper.write("gcp", "aster", PORT="18765")
    helper.run("proxy", ok=False)
    assert not helper.calls()


def test_compose_add_reads_environment_without_executing_it(helper):
    (helper.folder / ".env").write_text(f"CLIENT=aster\nKGE_PORT=8012\nGCS_PORT=4455\nUNUSED=$(touch {helper.root}/executed)\n")
    result = helper.run("add", "compose", str(helper.folder))
    assert result.stdout.splitlines()[0] == "kge: compose/aster"
    config = helper.read()["compose/aster"]
    assert config["URL"] == "http://127.0.0.1:8012"
    assert config["GCS_URL"] == "http://127.0.0.1:4455"
    assert not (helper.root / "executed").exists()


def test_failed_secret_update_preserves_store_files_and_skips_plain_download(helper):
    helper.write()
    (helper.root / "refuse").write_text(json.dumps({"tool": "gcloud", "args": ["secrets", "versions", "add"]}))
    helper.run("passwords", ok=False)
    calls = [call["args"] for call in helper.calls("gcloud")]
    assert calls[-1][:3] == ["secrets", "versions", "add"]
    assert not any(call[:2] == ["storage", "rm"] for call in calls)
    assert not any(arg.endswith("passwords.txt") for call in calls for arg in call)


def test_failed_password_job_stops_before_download_or_secret_update(helper):
    helper.write()
    (helper.root / "refuse").write_text(json.dumps({"tool": "gcloud", "args": ["run", "jobs", "execute"]}))
    result = helper.run("passwords", ok=False)
    assert "passwords: merge base unavailable" in result.stderr
    assert "job diagnostic on stdout" in result.stderr
    calls = [call["args"] for call in helper.calls()]
    assert [call[:3] for call in calls] == [
        ["secrets", "versions", "access"], ["storage", "cp", calls[1][2]], ["run", "jobs", "execute"]]


def test_failed_proxy_names_required_invoker_role(helper):
    helper.write(PORT="18767")
    (helper.root / "refuse").write_text(json.dumps({"tool": "gcloud", "args": ["run", "services", "proxy"]}))
    result = helper.run("proxy", ok=False)
    assert "roles/run.invoker" in result.stdout + result.stderr
    assert not helper.read()["gcp/meridian"].get("PID")


def test_occupied_proxy_port_refuses_before_launch_or_signin(helper):
    helper.write(PORT="18768")
    (helper.root / "occupied-port").write_text("18768")
    result = helper.run("connect", input=f"steward.meridian@example.com\n{PASSWORD}\n", ok=False)
    assert "port" in (result.stdout + result.stderr).lower()
    assert not helper.calls("gcloud")
    assert not any(call["url"].endswith("/session") for call in helper.calls("curl"))


def test_proxy_settings_cannot_change_until_stopped(helper):
    helper.write(PORT="18769")
    helper.run("proxy")
    before = helper.state.read_bytes()
    result = helper.run("config", "PORT=18770", ok=False)
    assert "proxy stop" in result.stdout + result.stderr
    assert helper.state.read_bytes() == before
    helper.run("proxy", "stop")
    helper.run("config", "PORT=18770")
    assert helper.read()["gcp/meridian"]["PORT"] == "18770"


def test_login_refuses_setting_override_disguised_as_email(helper):
    helper.write()
    helper.run("login", "SERVICE=wrong.meridian@example.com", ok=False)
    assert not helper.calls()


def test_connect_refuses_malformed_identity_without_claiming_connection(helper):
    helper.write("local")
    helper.token()
    (helper.root / "whoami.json").write_text("{}")
    result = helper.run("connect", ok=False)
    assert "connected:" not in result.stdout


def test_sync_clears_settings_removed_from_discovered_resources(helper):
    discovery(helper)
    helper.run("sync", "PROJECT=test-project", "REGION=us-central1")
    for kind in ("services", "jobs"):
        path = helper.root / f"{kind}-describe-kge-meridian.json"
        raw = path.read_text()
        row = json.loads(raw)
        def strip(value):
            if isinstance(value, dict):
                if "env" in value:
                    value["env"] = [item for item in value["env"]
                                    if item["name"] not in ("SIGNIN_PASSWORDS", "VERTEX_REGION")]
                for child in value.values():
                    strip(child)
            elif isinstance(value, list):
                for child in value:
                    strip(child)
        strip(row)
        path.write_text(json.dumps(row))
    result = helper.run("sync", "PROJECT=test-project", "REGION=us-central1")
    assert "changed: gcp/meridian" in result.stdout
    assert not helper.read()["gcp/meridian"]["PASSWORDS_SECRET"]
    assert not helper.read()["gcp/meridian"]["VERTEX_REGION"]


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_eval_pre_is_one_job_command_and_post_is_automatic_only(helper, target):
    helper.write(target)
    result = helper.run("eval", "pre", "REPEATS=1")
    assert result.returncode == 0, result.stderr
    calls = helper.calls()
    tool = {"local": "make", "compose": "docker", "gcp": "gcloud"}[target]
    call = next(c for c in calls if c["tool"] == tool)
    assert any("eval-pre" in arg for arg in call["args"])
    helper.clear()
    result = helper.run("eval", "post", ok=False)
    assert result.returncode != 0
    assert "automatically" in result.stderr
    assert helper.calls() == []


REPLAY = HELPER.parents[2] / "tests" / "fixtures" / "kge_helper"


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_documented_discovery_replay(helper, version):
    rows = json.loads((REPLAY / f"{version}.json").read_text())
    for name, value in rows.items():
        (helper.root / f"{name}.json").write_text(json.dumps(value))
    helper.run("sync", "PROJECT=your-project")
    section = helper.read()["gcp/meridian"]
    assert dict(section) == {
        "TARGET": "gcp", "CLIENT": "meridian", "PROJECT": "your-project", "REGION": "us-central1",
        "JOB": "kge-meridian-job", "SERVICE": "kge-meridian",
        "STORE_URL": "gs://your-project-kge-store", "LANDING_URL": "gs://your-project-meridian-landing",
        "PASSWORDS_SECRET": "projects/your-project/secrets/kge-meridian-passwords",
        "VERTEX_PROJECT": "your-project", "VERTEX_REGION": "global", "VERTEX_EMBEDDING_REGION": "us-central1",
        "SERVICE_URL": "https://kge-meridian.example.com", "URL": "https://kge-meridian.example.com",
    }
    calls = [call["args"] for call in helper.calls("gcloud") if call["args"][0] == "run"]
    assert [(args[1], args[2]) for args in calls] == [
        ("services", "list"), ("services", "describe"), ("jobs", "list"), ("jobs", "describe")]
    assert all("--format=json" in args for args in calls)
    before = helper.state.read_bytes()
    assert "unchanged: gcp/meridian" in helper.run("sync", "PROJECT=your-project").stdout
    assert helper.state.read_bytes() == before


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_documented_execution_replay_propagates_wait_result(helper, outcome):
    helper.write(JOB="kge-meridian-job")
    shutil.copyfile(REPLAY / f"execution-{outcome}.json", helper.root / "execution.json")
    result = helper.run("releases", ok=outcome == "success")
    row = json.loads(result.stdout.split("\n", 1)[1])
    assert row["kind"] == "Execution"
    assert row["status"]["succeededCount"] == int(outcome == "success")
    assert "--wait" in helper.calls("gcloud")[0]["args"]


@pytest.mark.parametrize("outcome", ["ready", "refused"])
def test_documented_proxy_replay_uses_readiness_not_log_text(helper, outcome):
    helper.write(PORT="18765")
    shutil.copyfile(REPLAY / f"proxy-{outcome}.json", helper.root / "proxy.json")
    result = helper.run("proxy", ok=outcome == "ready")
    if outcome == "ready":
        assert helper.read()["gcp/meridian"]["URL"] == "http://localhost:18765"
        assert any(call["url"].endswith("/healthz") for call in helper.calls("curl"))
        helper.run("proxy", "stop")
    else:
        assert "roles/run.invoker" in result.stderr
        assert not helper.read()["gcp/meridian"].get("PID")


def saved_files(helper):
    return {str(path.relative_to(helper.state.parent)): (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns)
            for path in helper.state.parent.rglob("*") if path.is_file()}


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
@pytest.mark.parametrize("args", [
    ("onboard",), ("release",), ("rollback",), ("teardown", "CONFIRM=meridian"),
    ("setup-client", "ASKER=analyst_enterprise"), ("access", "LIST=yes"), ("releases",),
    ("eval", "pre"), ("passwords",), ("passwords", "ROTATE=all"), ("restart",), ("versions",),
    ("admins", "access_admin.meridian@example.com", "BY=operator.meridian@example.com"),
    ("connect",), ("disconnect",), ("proxy",), ("proxy", "stop"),
    ("login", "steward.meridian@example.com"), ("logout",), ("wait",), ("review",),
    ("accept", "UNIT=retail"), ("certify", "PROPOSAL=sample"),
    ("checks",), ("improve", "UNIT=retail"), ("sign-off",), ("publish",), ("unpublish",),
    ("grant", "business.meridian@example.com", "ROLES=admin,business", "NAME=Business owner"),
    ("revoke", "business.meridian@example.com"), ("list",), ("audit",),
    ("use", "{env}"), ("config", "AUTO=off"), ("config",), ("help",),
])
def test_global_dry_run_never_invokes_runners_or_changes_saved_files(helper, target, args):
    env = helper.write(target, PID="999999", PORT="18765")
    helper.token(target)
    before = saved_files(helper)
    refused = target == "local" and args[0] == "teardown"
    result = helper.run("--dry-run", *(arg.replace("{env}", env) for arg in args), ok=not refused)
    if refused:
        assert "teardown requires a Compose or GCP store" in result.stderr
    assert helper.calls() == []
    assert not (helper.root / "dry-run-invoked-tool").exists()
    assert saved_files(helper) == before
    assert "connected:" not in result.stdout
    if args[0] in ("onboard", "release", "eval", "passwords"):
        assert {"local": "+ make", "compose": "+ docker", "gcp": "+ gcloud"}[target] in result.stderr
    if args[0] in ("checks", "grant", "revoke", "list"):
        assert "+ curl" in result.stderr
        assert "principal" not in result.stderr


@pytest.mark.parametrize("mode", ["flag", "argument", "environment"])
def test_dry_run_sync_with_no_state_prints_dependent_discovery_templates(helper, mode):
    if mode == "environment":
        helper.env["DRY_RUN"] = "yes"
    args = ["--dry-run"] if mode == "flag" else ["DRY_RUN=yes"] if mode == "argument" else []
    result = helper.run("sync", *args)
    assert not helper.state.parent.exists()
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()
    for command in ("config get-value project", "config get-value run/region", "services list", "services describe", "jobs list", "jobs describe"):
        assert command in result.stderr
    assert "discovered-region" in result.stderr
    assert "state unchanged" in result.stdout


@pytest.mark.parametrize("target", ["local", "compose"])
def test_dry_run_add_does_not_create_state(helper, target):
    helper.run("--dry-run", "add", target, str(helper.folder))
    assert not helper.state.parent.exists()
    assert not helper.calls()


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_dry_run_upload_lists_transfers_and_onboard_without_execution(helper, target):
    helper.write(target)
    (helper.folder / "pack.yaml").write_text("client: meridian\n")
    before = saved_files(helper)
    result = helper.run("upload", str(helper.folder), "DRY_RUN=yes")
    assert "onboard" in result.stderr
    if target != "local":
        assert "pack.yaml" in result.stderr
        assert "uploads" in result.stderr
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()


def test_dry_run_gcp_passwords_includes_merge_secret_pointer_cleanup_and_revision(helper):
    helper.write()
    before = saved_files(helper)
    result = helper.run("--dry-run", "passwords", "REVISION=yes")
    lines = [shlex.split(line)[1:] for line in result.stderr.splitlines() if line.startswith("+ gcloud")]
    assert [line[1:4] for line in lines if line[1] in ("secrets", "run")] == [
        ["secrets", "versions", "access"], ["run", "jobs", "execute"],
        ["secrets", "versions", "add"], ["run", "services", "update"]]
    transfers = [line for line in lines if line[1:3] == ["storage", "cp"]]
    assert any(line[-1] == "gs://test-store/deploy/meridian/passwords-secret" for line in transfers)
    assert any(line[-1] == "gs://test-store/secrets/meridian/passwords.json" for line in transfers)
    assert any(line[-2] == "gs://test-store/secrets/meridian/passwords.txt" for line in transfers)
    assert any(line[1:4] == ["storage", "rm", "--all-versions"] for line in lines)
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()


@pytest.mark.parametrize("role", ["operator", "access-admin", "knowledge-engineer", "business", "steward"])
@pytest.mark.parametrize("safe", [False, True])
@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_global_dry_run_expands_safe_playbook_runners(helper, role, safe, target):
    helper.write(target)
    helper.forbid_runners()
    before = saved_files(helper)
    result = helper.run("--dry-run", "playbook", role, *(["--safe"] if safe else []), "UNIT=retail")
    assert f"playbook {role}: dry-run" in result.stdout
    if safe and role in {"knowledge-engineer", "steward"}:
        assert "skipped (mutating)" in result.stdout and not result.stderr
    else:
        runner = {"local": "make", "compose": "docker", "gcp": "gcloud"}[target]
        assert "+ curl" in result.stderr or f"+ {runner}" in result.stderr
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()
    assert not (helper.root / "forbidden-runner").exists()


def test_dry_run_settings_preserve_literal_values_defaults_and_overrides(helper):
    helper.write("local", AUTO="off")
    helper.forbid_runners()
    config = helper.read()
    del config["local/meridian"]["URL"]
    url = "http://localhost:8000/a path/$(touch forbidden)=value\nsecond line"
    config["DEFAULT"] = {"URL": url}
    with helper.state.open("w") as stream:
        config.write(stream)
    before = saved_files(helper)
    result = helper.run("--dry-run", "review")
    quoted = subprocess.check_output(["bash", "-c", 'printf "%q" "$1"', "bash", url + "/whoami"], text=True)
    assert quoted in result.stderr
    result = helper.run("--dry-run", "onboard", "FOLDER=/missing-folder", f"FOLDER={helper.folder}")
    assert "+ make onboard" in result.stderr
    assert saved_files(helper) == before
    assert not (helper.root / "forbidden-runner").exists()


@pytest.mark.parametrize("invalid, message", [
    ("URL=duplicate\n", "already exists"),
    ("EXTRA=contains\0nul\n", "NUL is not allowed in settings"),
])
def test_dry_run_refuses_unreadable_settings_before_runners(helper, invalid, message):
    helper.write("local")
    helper.forbid_runners()
    original = helper.state.read_bytes()
    with helper.state.open("a") as stream:
        stream.write(invalid)
    result = subprocess.run(["bash", str(HELPER), "--dry-run", "playbook", "steward"],
                            env=helper.env, text=True, capture_output=True, timeout=60)
    helper.state.write_bytes(original)
    assert result.returncode != 0
    assert message in result.stderr
    if "\0" in invalid:
        assert "could not read dry-run settings" in result.stderr
    assert not (helper.root / "forbidden-runner").exists()


@pytest.mark.parametrize("target", ["local", "compose"])
def test_dry_run_password_failure_preserves_existing_output(helper, target):
    helper.write(target, FOLDER=str(helper.root / "missing-folder"))
    output = helper.state.parent / "passwords" / target / "meridian.XXXXXX"
    output.parent.mkdir(parents=True)
    output.write_text("keep this file")
    before = saved_files(helper)
    helper.run("--dry-run", "passwords", ok=False)
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_dry_run_connect_needs_no_credentials_and_auto_off_does_not_sign_in(helper, target):
    helper.write(target, AUTO="off")
    before = saved_files(helper)
    result = helper.run("--dry-run", "connect")
    assert "/session/token" in result.stderr
    if target == "gcp":
        assert "+ curl -sS --connect-timeout 1 --max-time 1" in result.stderr
        assert "+ curl -fsS --connect-timeout 1 --max-time 1" in result.stderr
    result = helper.run("--dry-run", "checks")
    assert "/session/token" not in result.stderr
    assert "/whoami" in result.stderr and "/onboard/run" in result.stderr
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()


@pytest.mark.parametrize("args", [
    ("setup",), ("admins", "access_admin.meridian@example.com"), ("put-example",),
])
def test_dry_run_remaining_gcp_operator_commands(helper, args):
    helper.write()
    before = saved_files(helper)
    result = helper.run(*args, "--dry-run")
    assert "+ gcloud" in result.stderr
    assert saved_files(helper) == before
    assert not helper.calls()
    assert not (helper.root / "dry-run-invoked-tool").exists()


def test_dry_run_refuses_manual_eval_post_and_identity_in_body(helper):
    helper.write()
    before = saved_files(helper)
    helper.run("--dry-run", "eval", "post", ok=False)
    helper.run("--dry-run", "checks", "PRINCIPAL=steward.meridian@example.com", ok=False)
    assert saved_files(helper) == before
    assert not helper.calls()


@pytest.mark.parametrize("command", ["teardown", "put-example"])
def test_local_refuses_store_only_operator_commands(helper, command):
    helper.write("local")
    result = helper.run(command, ok=False)
    assert "requires a Compose or GCP store" in result.stderr
    assert not helper.calls()


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_dry_restart_never_claims_readiness_or_calls_health(helper, target):
    helper.write(target)
    helper.env["DRY_RUN"] = "yes"
    before = saved_files(helper)
    result = helper.run("restart")
    assert "restart: dry run, nothing restarted" in result.stdout
    assert "compose app ready" not in result.stdout and "nothing needed;" not in result.stdout
    if target == "compose":
        assert "docker compose restart app" in result.stderr
        assert "curl -fsS --connect-timeout 1 --max-time 2 -o /dev/null http://127.0.0.1:8000/healthz" in result.stderr
    assert not helper.calls()
    assert saved_files(helper) == before


def test_compose_restart_checks_health_before_claiming_ready(helper):
    helper.write("compose")
    (helper.root / "occupied-port").write_text("8000")
    result = helper.run("restart")
    assert "restart: compose app ready" in result.stdout
    assert [call["tool"] for call in helper.calls()] == ["docker", "curl"]
    assert helper.calls("curl")[-1]["url"].endswith("/healthz")


@pytest.mark.parametrize("target", ["local", "compose", "gcp"])
def test_versions_reads_the_existing_api_with_personal_identity(helper, target):
    helper.write(target)
    helper.token(target)
    result = helper.run("versions")
    call = helper.calls("curl")[-1]
    assert call["method"] == "GET" and call["url"].endswith("/versions") and call["bearer"]
    assert not call["body"]
    assert '"ok": true' in result.stdout
    assert not helper.calls("docker")
