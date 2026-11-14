"""FROM is staged as a whole, protected from page jobs, and published only by success."""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from kge import cli
from kge.adapters import appstate
from kge.compiler.pack import Finding
from kge.estate import upfront
from kge.pipeline import objstore, uploads
from tests.unit.test_onboard_atomic import atomic as atomic, files


def upload(commands, prof, remote):
    pack = prof.clients / prof.client
    (pack / "catalogue.yaml").write_bytes(b"stale catalogue")
    (pack / "dropped.yaml").write_bytes(b"stale include")
    objstore.push(pack, remote, f"clients/{prof.client}/")
    incoming = {name: data for name, data in files(pack).items()
                if name not in ("catalogue.yaml", "dropped.yaml")}
    incoming["fixture/data/shop/sale.csv"] = incoming["fixture/data/shop/sale.csv"].replace(b"1.50", b"9.50")
    incoming["units/extra/retained"] = b"an upload cannot write here"
    incoming["pack.lock"] = b"an upload cannot write the lock"
    for name, data in incoming.items():
        remote.put(f"uploads/{prof.client}/this-run/{name}", data)
    commands.explicit["FROM"] = Path(remote.url).as_uri() + f"/uploads/{prof.client}/this-run"
    return incoming


def test_upload_changes_are_local_until_success_and_owned_inputs_are_ignored(atomic):
    commands, prof, remote, scenario, _, _, _, app = atomic
    incoming = upload(commands, prof, remote)
    original = {key: remote.get(key) for key in remote.list("clients/") if "/units/" not in key}
    commands.onboard()
    assert {key: remote.get(key) for key in original} == original
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["upload"]["sha256"] == uploads.digest({k: v for k, v in incoming.items()
                                                        if not k.startswith("units/") and k != "pack.lock"})
    assert "pack.lock" in record["upload"]["ignored"]
    assert "units/extra/retained" in record["upload"]["ignored"]
    assert record["upload"]["remove"] == ["catalogue.yaml", "dropped.yaml"]
    assert not (prof.clients / prof.client / "catalogue.yaml").exists()
    assert b"sam" in prof.identities.read_bytes()
    objstore.after("onboard", app)
    assert remote.get("clients/shop/catalogue.yaml") is None
    assert remote.get("clients/shop/dropped.yaml") is None
    assert remote.get("clients/shop/fixture/data/shop/sale.csv") == incoming["fixture/data/shop/sale.csv"]
    assert remote.get("clients/shop/pack.lock") == b"new lock"
    assert remote.get("deploy/shop/identities.yaml") is None
    assert remote.get("clients/shop/units/extra/retained") is None
    assert remote.get("clients/shop/units/extra/new") == b"created during build"


@pytest.mark.parametrize("failure", ["findings", "load", "margin"])
def test_failed_upload_restores_previous_inputs_people_and_lock(atomic, monkeypatch, failure, capsys):
    commands, prof, remote, scenario, _, _, _, app = atomic
    upload(commands, prof, remote)
    before = files(prof.clients / prof.client)
    people = prof.identities.read_bytes()
    stored = {key: remote.get(key) for key in remote.list()}
    if failure == "findings":
        monkeypatch.setattr(upfront, "check", lambda prof: upfront.Report(True,
            [Finding("I01", "pack.yaml:input", "missing input"), Finding("A01", "access:role", "missing role")],
            ["clients/shop/extra.yaml"], None, {}, "inputs", None))
    else:
        scenario["failure"] = failure
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == {"findings": 2, "load": 7, "margin": 1}[failure]
    assert files(prof.clients / prof.client) == before
    assert prof.identities.read_bytes() == people
    objstore.failed("onboard", app)
    assert {key: remote.get(key) for key in remote.list() if not key.startswith("runs/")} == stored
    if failure == "findings":
        output = capsys.readouterr().out
        assert "I01" in output and "A01" in output and "2 finding(s)" in output
        assert scenario["builds"] == 0


@pytest.mark.parametrize("kind", ["request", "stored-job", "state-job"])
def test_from_refuses_a_live_page_request_before_applying(atomic, monkeypatch, kind):
    commands, prof, remote, scenario, _, _, _, _ = atomic
    upload(commands, prof, remote)
    before = files(prof.clients / prof.client)
    job = {"state": "running", "started_at": datetime.now(timezone.utc).isoformat()}
    if kind == "request":
        remote.put("clients/shop/onboard/request.json", b"{}")
    elif kind == "stored-job":
        remote.put("clients/shop/onboard/job.json", json.dumps(job).encode())
    else:
        class State:
            def job(self):
                return job
        monkeypatch.setattr(appstate, "job_backend", lambda prof: State())
    with pytest.raises(cli.Failed) as error:
        commands.onboard()
    assert error.value.returncode == 2
    assert files(prof.clients / prof.client) == before
    assert scenario["builds"] == 0


def test_stale_jobs_do_not_refuse_from_but_malformed_running_jobs_do(atomic):
    _, prof, remote, _, _, _, _, _ = atomic
    for minutes, expected in ((184, True), (186, False)):
        job = {"state": "running", "started_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()}
        remote.put("clients/shop/onboard/job.json", json.dumps(job).encode())
        assert bool(objstore.page_request_running(remote, prof)) is expected
    remote.put("clients/shop/onboard/job.json", b'{"state":"running"}')
    assert objstore.page_request_running(remote, prof)


def test_from_without_pack_or_from_another_clients_prefix_is_refused(atomic):
    commands, prof, remote, scenario, _, _, _, _ = atomic
    before = files(prof.clients / prof.client)
    for source in (Path(remote.url).as_uri() + "/uploads/other/run", Path(remote.url).as_uri() + "/uploads/shop/empty"):
        commands.explicit["FROM"] = source
        with pytest.raises(uploads.Refused):
            commands.onboard()
        assert files(prof.clients / prof.client) == before
        assert scenario["builds"] == 0


def test_local_absolute_upload_replaces_same_size_same_time_bytes(atomic, monkeypatch, tmp_path):
    commands, prof, remote, scenario, _, _, _, _ = atomic
    monkeypatch.delenv("STORE_URL")
    commands.explicit["STORE_URL"] = ""
    pack = prof.clients / prof.client
    source = tmp_path / "local-upload"
    for name, data in files(pack).items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    name = "fixture/data/shop/sale.csv"
    old = (pack / name).read_bytes()
    changed = old.replace(b"1.50", b"9.50")
    assert changed != old and len(changed) == len(old)
    (source / name).write_bytes(changed)
    stat = (pack / name).stat()
    os.utime(source / name, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    commands.explicit["FROM"] = str(source)
    commands.onboard()
    assert (pack / name).read_bytes() == changed
    assert scenario["builds"] == 1
    record = json.loads((prof.run_dir() / objstore.ONBOARD_RUN).read_text())
    assert record["upload"]["source"] == str(source)
    assert record["outcome"] == "passed"
