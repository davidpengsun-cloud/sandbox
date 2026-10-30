"""Release bundles serve catalogue and data with no landing access."""
from contextlib import nullcontext
from dataclasses import replace
import shutil
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from kge.adapters.objstore import open_store
from kge.landing.zone import Zone
from kge.pipeline.landed_tables import PREDATES
from kge.pipeline.objstore import fetch, publish
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.services.ask import Engine
from kge.services.catalogue import routes as catalogue
from kge.services.data import routes as data
from tests.gcs_stub import serve


@pytest.fixture(params=["folder", "gcs"])
def released_service(request, tmp_path, monkeypatch):
    source = resolve("meridian")
    app_root = tmp_path / "app"
    profile = replace(source, **{key: app_root / getattr(source, key).relative_to(ROOT)
                                for key in ("artifacts", "store", "landing", "warehouses", "clients", "identities")})
    with serve() if request.param == "gcs" else nullcontext() as stub:
        if stub:
            monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"http://127.0.0.1:{stub.server_port}")
        url = "gs://landed-tables-store" if stub else str(tmp_path / "object-store")
        monkeypatch.setenv("STORE_URL", url)
        monkeypatch.delenv("LANDING_URL", raising=False)
        store = open_store(url)
        publish(store, ROOT, source)
        fetch(store, app_root, profile)
        shutil.copytree(source.landing / source.client, profile.landing / profile.client)
        shutil.rmtree(profile.landing)
        assert not profile.landing.exists()

        def unavailable(*args, **kwargs):
            pytest.fail("the serving process tried to open landing")

        monkeypatch.setattr(Zone, "open", unavailable)
        engine = Engine.serving(profile)
        monkeypatch.setattr(catalogue, "served", SimpleNamespace(engine=lambda: engine))
        monkeypatch.setattr(data, "served", SimpleNamespace(engine=lambda: engine))
        monkeypatch.setenv("PORTAL_SYNC_TOKEN", "s" * 32)
        monkeypatch.delenv("TRUSTED_CALLER_AUDIENCE", raising=False)
        app = FastAPI()
        app.include_router(catalogue.router)
        app.include_router(data.router)
        app.dependency_overrides[catalogue.principal] = lambda: "analyst_enterprise"
        app.dependency_overrides[data.querying] = lambda: "analyst_enterprise"
        try:
            with TestClient(app) as http:
                yield http, engine
            if stub:
                assert all("landing" not in path for _, path, _ in stub.seen)
        finally:
            engine.close()


def test_catalogue_badges_and_all_reads_come_from_the_release_without_landing(released_service):
    http, engine = released_service
    headers = {"x-portal-sync-token": "s" * 32}
    catalogue_response = http.get("/catalogue/portal", headers=headers)
    assert catalogue_response.status_code == 200
    payload = catalogue_response.json()
    assert len(payload["products"]) == 59
    enriched = yaml.safe_load((engine.profile.current() / "catalogue.yaml").read_text())
    badges = {p["id"]: p["badges"] for p in enriched["products"]}
    assert all("certified" not in p and p["badges"] == badges[p["id"]] for p in payload["products"])
    assert http.get("/catalogue", headers=headers).json()["products"] == payload["products"]
    identity = http.get("/catalogue/release", headers=headers)
    assert identity.status_code == 200 and identity.json()["release"] == payload["release"]
    for product in payload["products"]:
        response = http.post("/data/preview", json={"unit": product["unit_id"], "table": product["table"]})
        assert response.status_code == 200, response.text
    exported = http.post("/data/export", json={"unit": "wealth", "table": "TRAN_FACT_1"})
    assert exported.status_code == 200 and len(exported.text.splitlines()) == 3946
    queried = http.post("/data/query", json={"unit": "retail", "sql": "SELECT count(*) AS n FROM customer"})
    assert queried.status_code == 200 and queried.json()["rows"] == [{"n": 200}]


def test_old_release_keeps_catalogue_sync_and_refuses_reads_plainly(released_service):
    http, engine = released_service
    headers = {"x-portal-sync-token": "s" * 32}
    before = http.get("/catalogue/portal", headers=headers).json()
    shutil.rmtree(engine.profile.graph_path().parent / "landed-tables")
    response = http.get("/catalogue/portal", headers=headers)
    assert response.status_code == 200 and response.json()["has_catalogue"]
    assert response.json() == before
    assert http.get("/catalogue/release", headers=headers).status_code == 200
    assert http.get("/catalogue").status_code == 200
    for kind in ("preview", "export", "query"):
        body = {"unit": "retail", **({"sql": "SELECT customer_id FROM customer"} if kind == "query" else {"table": "customer"})}
        refused = http.post(f"/data/{kind}", json=body)
        assert refused.status_code == 403 and refused.json() == {"detail": PREDATES}
