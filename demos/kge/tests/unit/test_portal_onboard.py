"""Portal and graph onboarding targets: catalogue publish, states, rollback and audit."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from kge.compiler.catalogue import derive, governed_fact, read
from kge.compiler.catalogue_publish import (
    CatalogueRefused, audit, diff_lines, load, mark, publish_portal, publish_release, rollback, states,
)
from kge.compiler.pack import Pack
from kge.services.ask.engine.scope import ProductScope
from kge.services.catalogue import get


def pack_with(folder: Path, products: list[dict], *, documents: dict[str, list[str]] | None = None) -> Pack:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pack.yaml").write_text(yaml.safe_dump({
        "sources": [{"id": "retail"}, {"id": "wealth"}],
        "access": {"roles": [{"id": "steward", "processes": ["certification"], "label": "Steward"}]},
    }))
    if documents:
        for shelf, names in documents.items():
            for name in names:
                path = folder / "fixture" / "docs" / shelf / f"{name}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"---\ntitle: {name} title\nunit: retail\n---\nBody\n")
    (folder / "catalogue.yaml").write_text(yaml.safe_dump({"format": 1, "units": ["retail", "wealth"], "products": products}))
    pack = Pack(folder.name, folder)
    pack.data = yaml.safe_load((folder / "pack.yaml").read_text())
    pack.lock = {"sources": {"retail": {"tables": [{"name": "complaint", "columns": [{"name": "case_id"}]}]},
                              "wealth": {"tables": [{"name": "holding", "columns": [{"name": "id"}]}]}}}
    (folder / "pack.lock").write_text(json.dumps(pack.lock))
    return pack


def graph_product(path: str = "Meridian > Conduct > Complaint") -> dict:
    return {"id": "retail.complaint", "unit": "retail", "table": "complaint", "name": "Complaint",
            "description": "Cases", "product_owner": "Steward", "business_owner": "Analyst",
            "badges": ["SILVER"], "quality_score": 97, "counts": {"subscribe": 0, "access": 0, "views": 0}, "rating": 0,
            "domain": "Retail", "community": "Conduct",
            "application": "Cases", "product_type": "Documents", "path": path, "data_format": "Table",
            "lineage": {"origin_paths": ["register"], "destination_paths": [], "upstream": [], "downstream": []},
            "fields": [{"name": "case_id", "business_name": "Case", "terms": [], "quality": {"dimension": "Completeness", "rule": "present", "rule_type": "completeness"}}]}


class Profile:
    def __init__(self, root: Path):
        self.id = "acme"
        self.client = "acme"
        self.clients = root / "clients"
        self.artifacts = root / "artifacts"
        self.store = root / "store"
        self.pinned = None

    def current(self):
        link = self.artifacts / self.client / "current"
        return link.resolve() if link.is_dir() else None

    def model_hash(self):
        current = self.current()
        return current.name if current else None

    def run_dir(self):
        return self.store / self.client / "run"

    def identity(self):
        return {"hash": self.model_hash(), "graph": "generation-1"}


def release_artifact(prof: Profile, product: dict, digest: str = "abc123") -> None:
    folder = prof.artifacts / prof.client / digest
    folder.mkdir(parents=True)
    (folder / "catalogue.yaml").write_text(yaml.safe_dump({"format": 2, "products": [product]}))
    (folder / "data/nodes").mkdir(parents=True)
    (folder / "data/nodes/Col.csv").write_text("id,data_type,description\nretail.complaint.case_id,VARCHAR,Case\n")
    (folder / "data/edges").mkdir(parents=True)
    (folder / "data/edges/MAPS_TO__Col__Term.csv").write_text("from,to\n")
    current = prof.artifacts / prof.client / "current"
    current.symlink_to(digest, target_is_directory=True)


def approver(monkeypatch):
    monkeypatch.setattr("kge.pipeline.access_table.normalize_username", lambda value: value.strip().lower())
    monkeypatch.setattr("kge.pipeline.access_table.is_admin", lambda prof, who: who == "admin@example.com")
    monkeypatch.setattr("kge.pipeline.access_table.served_roles", lambda prof: {
        "steward": {"processes": ["certification"]},
        "admin": {"processes": ["tagging", "certification", "querying"]},
    })

    class Reader:
        def roles_of(self, who):
            return {
                "steward@example.com": {"steward"},
                "admin.meridian@example.com": {"admin"},
                "both@example.com": {"admin", "steward"},
                "reader@example.com": {"analyst"},
            }.get(who, set())

    monkeypatch.setattr("kge.pipeline.access_table.Access.for_profile", lambda prof: Reader())


def test_portal_only_entries_do_not_change_the_derived_graph_catalogue(tmp_path):
    folder = tmp_path / "acme"
    graph = graph_product()
    plain = pack_with(folder, [graph])
    with_card = pack_with(tmp_path / "with", [graph, {"id": "retail.policies", "unit": "retail", "table": "policies",
                                                      "product_type": "Documents", "name": "Policies", "path": "shelf/policies"}])
    assert derive(read(plain)[0], plain)["products"] == derive(read(with_card)[0], with_card)["products"]


def test_a_document_set_names_fixture_documents_and_an_unknown_one_is_refused(tmp_path):
    valid = pack_with(tmp_path / "valid", [{
        "id": "retail.document_set", "unit": "retail", "table": "document_set", "product_type": "Documents",
        "name": "Retail documents", "documents": ["DD-RETAIL"],
    }], documents={"certified": ["DD-RETAIL"]})
    assert read(valid)[1] == []
    missing = pack_with(tmp_path / "missing", [{
        "id": "retail.document_set", "unit": "retail", "table": "document_set", "product_type": "Documents",
        "name": "Retail documents", "documents": ["missing-doc"],
    }], documents={"certified": ["DD-RETAIL"]})
    assert any("unknown document missing-doc" in str(item) for item in read(missing)[1])
    unknown = pack_with(tmp_path / "table", [{"id": "retail.missing", "unit": "retail", "table": "missing", "product_type": "Table"}])
    assert any("names unknown table retail.missing" in str(item) for item in read(unknown)[1])


def test_portal_publish_changes_a_path_without_a_graph_and_refuses_a_governed_fact(tmp_path, monkeypatch):
    approver(monkeypatch)
    prof = Profile(tmp_path)
    product = graph_product()
    release_artifact(prof, product)
    before = {path.name for path in (prof.artifacts / prof.client).iterdir()}
    folder = prof.clients / prof.client
    pack = pack_with(folder, [{**product, "path": "Meridian > Conduct > Register"}])
    result = publish_portal(prof, pack, "steward@example.com")
    assert "retail.complaint path:" in result["diff"]
    assert "Meridian > Conduct > Register" in result["diff"]
    assert {path.name for path in (prof.artifacts / prof.client).iterdir()} == before | {"catalogue-publish"}
    assert prof.model_hash() == "abc123"
    served = get(prof, ["retail"], portal=True)
    assert served.products[0].path == "Meridian > Conduct > Register"
    assert served.products[0].catalogue_state == "Portal ahead"
    assert "|portal:" in served.release
    assert get(prof, ["retail"]).products[0].path == "Meridian > Conduct > Register"
    changed = pack_with(tmp_path / "governed", [{**product, "quality_score": 1}])
    with pytest.raises(CatalogueRefused, match=r"sets governed fact quality_score"):
        publish_portal(prof, changed, "admin@example.com")
    assert load(prof)["products"][0]["descriptive"]["path"] == "Meridian > Conduct > Register"
    assert audit(prof)[0]["by"] == "steward@example.com"
    with pytest.raises(CatalogueRefused, match="steward or an access admin"):
        publish_portal(prof, pack, "reader@example.com")


def test_a_new_document_is_portal_only_and_hidden_from_other_units_and_from_ask(tmp_path, monkeypatch):
    approver(monkeypatch)
    prof = Profile(tmp_path)
    product = graph_product()
    release_artifact(prof, product)
    pack = pack_with(prof.clients / prof.client, [
        product,
        {"id": "retail.policies", "unit": "retail", "table": "policies", "product_type": "Documents",
         "name": "Policies", "path": "shelf/policies", "data_format": "PDF", "description": "A portal-only policy"},
    ], documents={"certified": []})
    publish_portal(prof, pack, "admin@example.com")
    visible = get(prof, ["retail"], portal=True)
    card = next(item for item in visible.products if item.id == "retail.policies")
    assert card.portal_only is True
    assert card.catalogue_state == "Not in the knowledge graph"
    assert get(prof, ["wealth"], portal=True).products == []
    assert all(item.id != "retail.policies" for item in get(prof, ["retail"]).products)

    class Catalogue:
        tables = {"retail": {"complaint"}}

    class Graph:
        def catalogue(self):
            return Catalogue()

    class Engine:
        g = Graph()

    assert ProductScope(Engine(), ["retail.policies"]).outside_graph()
    assert not ProductScope(Engine(), ["retail.complaint"]).outside_graph()
    assert not ProductScope(Engine(), None).outside_graph()


def test_graph_release_keeps_the_published_description_and_rollback_restores_it(tmp_path, monkeypatch):
    approver(monkeypatch)
    prof = Profile(tmp_path)
    product = graph_product()
    release_artifact(prof, product)
    first = pack_with(prof.clients / prof.client, [{**product, "path": "published/v1"}])
    publish_portal(prof, first, "steward@example.com")
    second = pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}])
    publish_portal(prof, second, "steward@example.com")
    assert get(prof, ["retail"], portal=True).products[0].path == "published/v2"
    graph = prof.model_hash()
    mark(prof, "graph")
    publish_release(prof, "steward@example.com")
    assert get(prof, ["retail"], portal=True).products[0].path == "published/v2"
    assert prof.model_hash() == graph
    rollback(prof, "admin@example.com")
    assert get(prof, ["retail"], portal=True).products[0].path == "published/v1"
    assert prof.model_hash() == graph
    labels = {row["id"]: row["state"] for row in states(prof)}
    assert labels["retail.complaint"] == "Portal ahead"
    mark(prof, "both")
    pack_with(prof.clients / prof.client, [product])
    publish_release(prof, "steward@example.com")
    assert get(prof, ["retail"], portal=True).products[0].path == product["path"]
    assert states(prof)[0]["state"] == "In sync"
    assert "|portal:" not in get(prof, ["retail"]).release
    assert any(row["action"] == "rollback" and row["by"] == "admin@example.com" for row in audit(prof))
    assert diff_lines([{"id": "retail.complaint", "descriptive": {"path": "a"}}],
                      [{"id": "retail.complaint", "descriptive": {"path": "b"}}]) == ["  retail.complaint path: a -> b"]


def test_a_governed_fact_already_on_the_release_is_not_a_new_one():
    held = graph_product()
    assert governed_fact({**held, "path": "other"}, held) is None
    assert governed_fact({**held, "lineage": {"origin_paths": ["added"], "destination_paths": [], "upstream": [], "downstream": []}}, held) == "lineage"


def _publish_ready(tmp_path, monkeypatch, path: str = "published/v1"):
    approver(monkeypatch)
    prof = Profile(tmp_path)
    product = graph_product(path)
    release_artifact(prof, product)
    pack = pack_with(prof.clients / prof.client, [{**product, "path": path}])
    return prof, pack, product


def test_preview_prints_the_diff_and_writes_nothing(tmp_path, monkeypatch, capsys):
    prof, pack, product = _publish_ready(tmp_path, monkeypatch)
    pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}])
    monkeypatch.setattr("kge.pipeline.profile.resolve", lambda: prof)
    from kge.cli import Commands

    Commands({}, environment={}).portal_onboard()
    out = capsys.readouterr().out
    assert "published/v1 -> published/v2" in out
    assert "published nothing" in out
    folder = prof.artifacts / prof.client / "catalogue-publish"
    assert not (folder / "current.json").exists()
    assert not (folder / "previous.json").exists()
    assert not (folder / "audit.jsonl").exists()
    Commands({"PREVIEW": "yes", "PRINCIPAL": "admin@example.com"}).portal_onboard()
    assert "published nothing" in capsys.readouterr().out
    assert not (folder / "current.json").exists()


def test_the_pack_content_admin_is_refused_and_not_audited(tmp_path, monkeypatch):
    prof, pack, product = _publish_ready(tmp_path, monkeypatch, "published/v1")
    pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}])
    with pytest.raises(CatalogueRefused, match="access admin"):
        publish_portal(prof, pack, "admin.meridian@example.com")
    assert audit(prof) == []
    assert load(prof) is None
    published = publish_portal(prof, pack, "admin@example.com")
    assert published["record"]["by"] == "admin@example.com"
    assert any(row["by"] == "admin@example.com" for row in audit(prof))
    assert not any(row["by"] == "admin.meridian@example.com" for row in audit(prof))
    both = publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v3"}]), "both@example.com")
    assert both["record"]["by"] == "both@example.com"


def test_a_real_access_admin_approves_and_the_content_role_does_not(tmp_path, monkeypatch):
    """Membership is the access table and ACCESS_ADMINS, not the address's spelling."""
    monkeypatch.setenv("ACCESS_ADMINS", "access_admin.meridian@example.com")
    rows = [
        {"username": "admin.meridian@example.com", "name": "Content admin", "roles": ["admin"]},
        {"username": "steward.meridian@example.com", "name": "Steward", "roles": ["steward"]},
        {"username": "both.meridian@example.com", "name": "Both", "roles": ["admin", "steward"]},
    ]

    class State:
        def access_grants(self):
            return [dict(row) for row in rows]

    monkeypatch.setattr("kge.pipeline.access_table.access_state", lambda prof: State())
    monkeypatch.setattr("kge.pipeline.access_table.served_roles", lambda prof: {
        "admin": {"processes": ["tagging", "certification", "querying"]},
        "steward": {"processes": ["certification"]},
    })
    prof = Profile(tmp_path)
    product = graph_product()
    release_artifact(prof, product)
    pack = pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}])
    from kge.compiler.catalogue_publish import require_approver

    with pytest.raises(CatalogueRefused, match="admin.meridian@example.com"):
        require_approver(prof, "admin.meridian@example.com")
    assert require_approver(prof, "steward.meridian@example.com") == "steward.meridian@example.com"
    assert require_approver(prof, "ACCESS_ADMIN.Meridian@example.com") == "access_admin.meridian@example.com"
    assert require_approver(prof, "both.meridian@example.com") == "both.meridian@example.com"
    publish_portal(prof, pack, "access_admin.meridian@example.com")
    assert audit(prof)[-1]["by"] == "access_admin.meridian@example.com"
    with pytest.raises(CatalogueRefused):
        publish_portal(prof, pack, "admin.meridian@example.com")
    assert all(row["by"] != "admin.meridian@example.com" for row in audit(prof))


def test_a_failed_current_write_keeps_the_rollback_snapshot(tmp_path, monkeypatch):
    from kge.compiler import catalogue_publish

    prof, pack, product = _publish_ready(tmp_path, monkeypatch, "published/v1")
    publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}]), "admin@example.com")
    folder = prof.artifacts / prof.client / "catalogue-publish"
    previous = (folder / "previous.json").read_bytes()
    current = (folder / "current.json").read_bytes()
    real = catalogue_publish._write

    def fail_current(target, name, value):
        if name == "current.json":
            raise OSError("disk full")
        return real(target, name, value)

    monkeypatch.setattr(catalogue_publish, "_write", fail_current)
    with pytest.raises(OSError, match="disk full"):
        publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v3"}]), "admin@example.com")
    assert (folder / "previous.json").read_bytes() == previous
    assert (folder / "current.json").read_bytes() == current
    assert b"published/v3" not in (folder / "current.json").read_bytes()
    assert b"published/v3" not in (folder / "previous.json").read_bytes()


def test_the_first_publish_can_roll_back_to_the_release_cards(tmp_path, monkeypatch):
    prof, pack, product = _publish_ready(tmp_path, monkeypatch, "published/v1")
    folder = prof.artifacts / prof.client / "catalogue-publish"
    assert not (folder / "current.json").exists()
    graph = prof.model_hash()
    publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}]), "steward@example.com")
    previous = json.loads((folder / "previous.json").read_text())
    assert previous["products"][0]["descriptive"]["path"] == "published/v1"
    assert previous["by"] == "release"
    rollback(prof, "admin@example.com")
    assert get(prof, ["retail"], portal=True).products[0].path == "published/v1"
    assert prof.model_hash() == graph


def test_a_graph_snapshot_without_previous_still_rolls_back(tmp_path, monkeypatch):
    """A graph release can leave current.json and no previous.json. The first edit can still roll back."""
    from kge.compiler.catalogue_publish import _digest, _write

    prof, pack, product = _publish_ready(tmp_path, monkeypatch, "published/v1")
    card = {"id": product["id"], "unit": "retail", "table": "complaint", "portal_only": False,
            "descriptive": {"path": "published/v1", "name": product["name"]}}
    record = {"digest": _digest([card]), "by": "release", "at": "2026-01-01T00:00:00Z", "target": "release", "products": [card]}
    _write(prof, "current.json", record)
    folder = prof.artifacts / prof.client / "catalogue-publish"
    (folder / "previous.json").unlink(missing_ok=True)
    publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}]), "steward@example.com")
    rollback(prof, "admin@example.com")
    assert json.loads((folder / "current.json").read_text())["products"][0]["descriptive"]["path"] == "published/v1"


def test_a_store_error_keeps_the_last_catalogue_and_one_store(tmp_path, monkeypatch):
    from kge.compiler import catalogue_publish
    from kge.ports import StoreError

    prof, _pack, product = _publish_ready(tmp_path, monkeypatch)
    publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}]), "admin@example.com")
    held = load(prof)
    portal = catalogue_publish.suffix(prof)
    assert portal.startswith("|portal:")
    catalogue_publish._known.clear()
    monkeypatch.setattr(catalogue_publish, "PUBLISH_TTL", 0)
    reads = {"n": 0}
    real = catalogue_publish._read

    def flaky(target, name):
        reads["n"] += 1
        if reads["n"] > 1 and name == "current.json":
            raise StoreError("unavailable")
        return real(target, name)

    monkeypatch.setattr(catalogue_publish, "_read", flaky)
    assert load(prof)["digest"] == held["digest"]
    assert load(prof)["digest"] == held["digest"]
    assert catalogue_publish.suffix(prof) == portal
    opens = {"n": 0}
    store = tmp_path / "objects"
    store.mkdir()
    monkeypatch.setenv("STORE_URL", str(store))
    catalogue_publish._stores.clear()
    real_open = __import__("kge.adapters.objstore", fromlist=["open_store"]).open_store

    def counting(url):
        opens["n"] += 1
        return real_open(url)

    monkeypatch.setattr("kge.adapters.objstore.open_store", counting)
    catalogue_publish._store()
    catalogue_publish._store()
    assert opens["n"] == 1


def test_portal_stored_after_releases_the_lease_without_a_graph_run(monkeypatch):
    calls = []
    monkeypatch.setattr("kge.pipeline.profile.resolve", lambda: object())
    monkeypatch.setattr("kge.pipeline.objstore.the_store", lambda: "store")
    monkeypatch.setattr("kge.pipeline.objstore.release_lease", lambda store, prof: calls.append(("lease", store)))
    monkeypatch.setattr("kge.pipeline.objstore.after", lambda *args, **kwargs: calls.append("after"))
    monkeypatch.setattr("kge.graph.release.prepare_retention", lambda prof: calls.append("retention"))
    monkeypatch.setattr("kge.graph.release.finish_onboard", lambda prof: calls.append("finish"))
    from kge.cli import Commands

    Commands({"COMMAND": "onboard", "CATALOGUE_TARGET": "portal"}, environment={})._stored_after()
    assert calls == [("lease", "store")]


def test_an_unknown_table_is_not_a_portal_card(tmp_path):
    prof = Profile(tmp_path)
    product = graph_product()
    pack_with(prof.clients / prof.client, [
        product,
        {"id": "retail.document_set", "unit": "retail", "table": "document_set", "product_type": "Documents",
         "name": "Retail documents"},
    ])

    class Catalogue:
        tables = {"retail": {"complaint"}}

    class Graph:
        def catalogue(self):
            return Catalogue()

    class Engine:
        g = Graph()
        profile = prof

    assert ProductScope(Engine(), ["retail.document_set"]).portal_only()
    assert ProductScope(Engine(), ["retail.missing"]).outside_graph()
    assert not ProductScope(Engine(), ["retail.missing"]).portal_only()
    assert not ProductScope(Engine(), ["retail.complaint"]).portal_only()


def test_a_served_document_set_is_complete_and_stays_inside_its_unit(tmp_path, monkeypatch):
    """The served portal catalogue is the payload the portal suite validates and syncs."""
    approver(monkeypatch)
    prof = Profile(tmp_path)
    product = graph_product()
    release_artifact(prof, product)
    pack = pack_with(prof.clients / prof.client, [
        product,
        {"id": "retail.document_set", "unit": "retail", "table": "document_set", "product_type": "Documents",
         "asset_type": "Unstructured", "name": "Retail documents", "description": "Certified documents for Retail Banking.",
         "documents": ["DD-RETAIL"]},
        {"id": "wealth.document_set", "unit": "wealth", "table": "document_set", "product_type": "Documents",
         "asset_type": "Unstructured", "name": "Wealth documents", "description": "Certified documents for Wealth Management.",
         "documents": ["DD-WEALTH"]},
    ], documents={"certified": ["DD-RETAIL", "DD-WEALTH"]})
    publish_portal(prof, pack, "admin@example.com")
    retail = {item.id for item in get(prof, ["retail"], portal=True).products}
    wealth = {item.id for item in get(prof, ["wealth"], portal=True).products}
    assert retail == {"retail.complaint", "retail.document_set"}
    assert wealth == {"wealth.document_set"}
    served = get(prof, ["retail", "wealth"], portal=True).model_dump(mode="json", exclude_unset=True)
    card = next(item for item in served["products"] if item["id"] == "retail.document_set")
    for key in ("last_refresh", "ingested_at", "records", "storage", "last_dq_run", "path", "data_format"):
        assert key in card, key
    assert card["asset_type"] == "Unstructured" and card["portal_only"] is True
    fixture = Path(__file__).resolve().parents[2] / "portal/server/test/fixtures/served-document-sets.json"
    assert served == json.loads(fixture.read_text())


def test_onboard_status_polls_reuse_the_cached_access_reader(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from kge.pipeline.access_table import Access
    from kge.services.onboard import service

    grants = {"n": 0}

    class State:
        def access_grants(self):
            grants["n"] += 1
            return [{"username": "analyst@example.com", "name": "Analyst", "roles": ["analyst"]}]

    reader = Access(State())
    prof = SimpleNamespace(model_hash=lambda: None, client="acme", artifacts=tmp_path)
    app = SimpleNamespace(engine=lambda: SimpleNamespace(g=SimpleNamespace(people=reader), profile=prof), PROFILE=prof)
    monkeypatch.setattr("kge.services.onboard.jobs.shown_candidate", lambda *args: (None, None))
    monkeypatch.setattr("kge.graph.history.list_versions", lambda *args: {})
    monkeypatch.setattr(service.core, "permissions", lambda *args, **kwargs: {"certify": False})
    monkeypatch.setattr("kge.pipeline.access_table.served_roles", lambda profile: {
        "analyst": {"reads": ["retail"], "writes": [], "processes": ["querying"]},
    })
    monkeypatch.setattr("kge.compiler.catalogue_publish.states", lambda profile: [
        {"id": "retail.a", "unit": "retail", "state": "In sync"},
        {"id": "wealth.b", "unit": "wealth", "state": "In sync"},
    ])

    def fresh(*args, **kwargs):
        raise RuntimeError("fresh access reader")

    monkeypatch.setattr("kge.pipeline.access_table.Access.for_profile", fresh)
    first = service.page_status(app, prof, "analyst@example.com", (False, "reader"), None, {})
    second = service.page_status(app, prof, "analyst@example.com", (False, "reader"), None, {})
    assert [card["id"] for card in first["catalogue_cards"]] == ["retail.a"]
    assert second["catalogue_cards"] == first["catalogue_cards"]
    assert grants["n"] == 1


def test_a_missing_ledger_is_cached_and_the_first_publish_is_visible(tmp_path, monkeypatch):
    from kge.adapters.objstore import open_store
    from kge.compiler import catalogue_publish
    from kge.ports import StoreError

    prof, _pack, product = _publish_ready(tmp_path, monkeypatch)
    url = str(tmp_path / "objects")
    (tmp_path / "objects").mkdir()
    inner = open_store(url)
    current = f"clients/{prof.client}/catalogue-publish/current.json"
    gets = {"n": 0, "fail": False}

    class Store:
        def get(self, key):
            if key == current:
                gets["n"] += 1
                if gets["fail"]:
                    raise StoreError("unavailable")
            return inner.get(key)

        def put(self, key, body):
            return inner.put(key, body)

    monkeypatch.setenv("STORE_URL", url)
    monkeypatch.setattr("kge.adapters.objstore.open_store", lambda address: Store())
    catalogue_publish._stores.clear()
    catalogue_publish._known.clear()
    assert load(prof) is None
    states(prof)
    states(prof)
    assert load(prof) is None
    assert gets["n"] == 1
    published = publish_portal(prof, pack_with(prof.clients / prof.client, [{**product, "path": "published/v2"}]), "admin@example.com")
    assert load(prof)["digest"] == published["record"]["digest"]
    assert gets["n"] == 1
    catalogue_publish._known[catalogue_publish._cache_key(prof)] = (0.0, None)
    gets["fail"] = True
    assert load(prof) is None
    gets["fail"] = False
    assert load(prof)["digest"] == published["record"]["digest"]
    assert gets["n"] == 3
