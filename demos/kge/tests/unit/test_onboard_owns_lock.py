"""The single onboard path owns refresh and changed fixtures, with subprocess steps replaced locally."""
import json
from pathlib import Path

import pytest

from kge import cli
from kge.estate import from_files
from kge.graph import release
from kge.pipeline import objstore
from tests.unit.test_cli import shop as shop, stand_in_onboard


@pytest.fixture
def onboard(shop, monkeypatch):
    stand_in_onboard(monkeypatch)
    events, records = [], []
    commands = cli.Commands({"CURRENT": "same", "CURRENT_LOADED": "yes", "MARGIN_STATUS": str(shop.run_dir() / "margin.status")})
    Path(commands.get("LANDED")).mkdir(parents=True)
    scenario = {"hash": "same", "loaded": True, "fixture": False, "rebuilt": False, "refresh_exit": 0, "margin_exit": 0,
                "unit_changed": False, "certify_exit": 0, "updates": []}
    lock = {"written": False, "changes": []}
    monkeypatch.setattr(cli, "profile_words", lambda model_hash=None: {"loaded": "yes"} if scenario["loaded"] else {})

    def build(fixture, out, client, *, if_changed, landing, defer_landing, estate, sets):
        assert if_changed is True and fixture.resolve() == shop.clients / shop.client / "fixture"
        assert out == shop.estate() and client == shop.client
        assert landing == shop.landing and defer_landing is True
        out.mkdir(parents=True, exist_ok=True)
        (out / "summary.json").write_text("{}")
        events.append("fixture")
        return {"rebuilt": scenario["rebuilt"], "why": "fixture-changed" if scenario["rebuilt"] else "unchanged",
                "version": 2, "fixture_sha256": "f" * 64}

    def module(name, *args, capture=False, check=True):
        events.append((name, *args))
        if name == "kge.estate.onboard" and args[:1] == ("build",):
            assert args == ("build", "--json")
            if scenario["unit_changed"]:
                shop.estate().mkdir(parents=True, exist_ok=True)
                (shop.estate() / "golden.json").write_text("[]")
            return json.dumps({"units": ["new"] if scenario["unit_changed"] else [], "updates": scenario["updates"]})
        if name == "kge.estate.certify" and scenario["certify_exit"]:
            raise cli.Failed(scenario["certify_exit"], name)
        if name == "kge.compiler.lock":
            assert args[:2] == ("--refresh", "--changes")
            if scenario["refresh_exit"]:
                cli.say("pack.yaml: mappings[0] names retail.sales.amount, which the warehouse no longer holds")
                raise cli.Failed(scenario["refresh_exit"], name)
            Path(args[2]).write_text(json.dumps(lock))
        if name == "kge.compiler.compile":
            return str(shop.artifacts / shop.client / scenario["hash"])
        if name == "kge.eval.margin":
            return scenario["margin_exit"]
        return 0

    def mark(graph, record):
        assert not scenario["margin_exit"]
        if "kge.eval.margin" in modules(events):
            assert events[-1] == ("kge.estate.onboard", "record-onboard", "--hash", scenario["hash"], "--passed", "true")
        assert graph.parent.resolve() == shop.store / shop.client / scenario["hash"]
        records.append(json.loads(json.dumps(record)))
        events.append("changes")

    monkeypatch.setattr(from_files, "build", build)
    monkeypatch.setattr(release, "mark_changes", mark)
    monkeypatch.setattr(commands, "module", module)

    def run(**values):
        scenario.update(values)
        if scenario["fixture"]:
            fixture = shop.clients / shop.client / "fixture"
            fixture.mkdir(parents=True, exist_ok=True)
            (fixture / "schema.yaml").touch()
        commands.onboard()

    return run, scenario, events, records, lock


def modules(events):
    return [event[0] for event in events if isinstance(event, tuple)]


@pytest.mark.parametrize("backend", ["duckdb", "bigquery"])
def test_meridian_without_fixture_refreshes_before_compiling(onboard, monkeypatch, backend):
    monkeypatch.setenv("WAREHOUSE_BACKEND", backend)
    run, _, events, records, _ = onboard
    run(hash="new", loaded=False)
    assert "fixture" not in events
    names = modules(events)
    assert names.index("kge.compiler.lock") < names.index("kge.compiler.compile") < names.index("kge.graph.load")
    assert records[0]["estate"] == {"rebuilt": False, "why": "no-fixture", "version": None, "fixture_sha256": None}
    assert records[0]["reloaded"] is False


def test_unchanged_current_builds_lands_and_loads_nothing(onboard, capsys, shop):
    run, _, events, records, _ = onboard
    stale = shop.run_dir() / objstore.ONBOARD_RECORD
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps({"estate": {"rebuilt": True}}))
    run(fixture=True)
    assert events[0] == "fixture"
    assert not {"kge.estate.certify", "kge.graph.load", "kge.eval.margin"} & set(modules(events))
    output = capsys.readouterr().out
    assert len(records) == 1 and output.endswith("onboard: current already\n")
    assert "certify: nothing changed" in output and "certify    ->" not in output
    assert objstore.onboard_changes(shop)["estate"]["rebuilt"] is False


def test_missing_landing_is_certified_even_when_inputs_are_unchanged(onboard, shop, capsys):
    run, _, events, records, _ = onboard
    (shop.landing / shop.client / "current").rmdir()
    run(fixture=True)
    names = modules(events)
    assert names.index("kge.estate.certify") < names.index("kge.compiler.lock")
    assert "kge.graph.load" not in names and len(records) == 1
    output = capsys.readouterr().out
    assert "[6/10] certify" in output and "certify: nothing changed" not in output
    assert objstore.onboard_changes(shop)["landed"] is True
    assert objstore.onboard_changes(shop)["estate"]["rebuilt"] is False


@pytest.mark.parametrize("candidate,reloaded", [("same", True), ("new", True)])
def test_rebuild_loads_even_an_already_loaded_hash_then_marks_changes_and_checks(onboard, candidate, reloaded, capsys):
    run, _, events, records, lock = onboard
    lock.update(written=True, changes=[{"source": "retail", "table": "sales", "column": "extra", "change": "column-added",
                                        "was": None, "now": "VARCHAR"}])
    run(fixture=True, rebuilt=True, hash=candidate)
    names = modules(events)
    assert events[0] == "fixture"
    assert names.index("kge.estate.certify") < names.index("kge.compiler.lock") < names.index("kge.compiler.compile")
    assert names.index("kge.graph.load") < names.index("kge.eval.margin")
    assert events.index("changes") > next(i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "kge.eval.margin")
    load = next(event for event in events if isinstance(event, tuple) and event[0] == "kge.graph.load")
    assert Path(load[-1]).name == candidate
    assert {key: records[0][key] for key in ("lock", "estate", "reloaded")} == {"lock": lock, "estate": {"rebuilt": True, "why": "fixture-changed", "version": 2,
                                                "fixture_sha256": "f" * 64}, "reloaded": reloaded}
    progress = [line for line in capsys.readouterr().out.splitlines() if line.startswith("onboard: [")]
    assert progress == [f"onboard: [{i}/10] {name}" for i, name in enumerate(
        ("inputs", "sweep", "check", "fixture", "units", "certify", "lock", "compile", "load", "margin"), 1)]


def test_refresh_exit_two_prints_pack_findings_and_never_compiles_or_loads(onboard, capsys):
    run, _, events, records, _ = onboard
    with pytest.raises(cli.Failed) as error:
        run(fixture=True, rebuilt=True, refresh_exit=2)
    assert error.value.returncode == 2
    assert "pack.yaml: mappings[0] names retail.sales.amount" in capsys.readouterr().out
    assert not {"kge.compiler.compile", "kge.graph.load", "kge.eval.margin"} & set(modules(events))
    assert not records


def test_unit_changes_are_certified_before_refresh(onboard):
    run, _, events, _, _ = onboard
    run(unit_changed=True, hash="new", loaded=False)
    names = modules(events)
    assert names.index("kge.estate.onboard") < names.index("kge.estate.certify") < names.index("kge.compiler.lock")


def test_failed_margin_does_not_commit_changes_after_load(onboard):
    run, _, _, records, _ = onboard
    with pytest.raises(cli.Failed) as error:
        run(fixture=True, rebuilt=True, margin_exit=2)
    assert error.value.returncode == 2 and records == []


def test_unchanged_unit_content_is_not_certified_again(onboard):
    run, _, events, records, _ = onboard
    run(unit_changed=True, hash="new", loaded=False)
    events.clear()
    records.clear()
    run(hash="same", loaded=True)
    assert not {"kge.estate.certify", "kge.graph.load", "kge.eval.margin"} & set(modules(events))
    assert len(records) == 1


def test_failed_certification_does_not_request_a_landing_upload(onboard, shop):
    run, _, events, records, _ = onboard
    with pytest.raises(cli.Failed) as error:
        run(fixture=True, rebuilt=True, certify_exit=2)
    assert error.value.returncode == 2
    assert "kge.compiler.lock" not in modules(events)
    assert objstore.onboard_changes(shop) is None and not records


def test_onboard_prints_each_units_landed_message_unchanged(onboard, capsys):
    run, _, events, _, _ = onboard
    messages = ["bancassurance v2 landed (4 changed, 3 added, 4 removed)", "ticketing v3 landed (1 changed)"]
    run(updates=messages)
    output = capsys.readouterr().out.splitlines()
    assert all(output.count(message) == 1 for message in messages)
    assert output.index(messages[0]) < output.index(messages[1])
    assert "kge.estate.certify" in modules(events)
