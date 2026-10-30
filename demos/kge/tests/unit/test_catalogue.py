"""The released discovery catalogue and its readable-unit filter."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import yaml
from fastapi import HTTPException
from starlette.requests import Request

from kge.services.catalogue.service import get, identity
from kge.services.catalogue import routes


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@dataclass
class Released:
    artifact: Path
    pinned: str | None = None
    client: str = "fixture"

    def current(self) -> Path:
        return self.artifact

    @property
    def artifacts(self) -> Path:
        return self.artifact.parents[1]

    def model_hash(self) -> str:
        return self.artifact.name

    def identity(self) -> dict:
        return {"hash": self.artifact.name, "graph": "generation", "chunks": None}

    def graph_path(self) -> Path:
        return self.artifact / "graph"


def product(unit: str) -> dict:
    return {"id": f"{unit}.accounts", "unit": unit, "table": "accounts", "description": f"{unit} accounts", "product_owner": "owner", "business_owner": "steward", "badges": ["Certified"], "quality_score": 98, "last_refresh": "2026-10-20T00:00:00Z", "ingested_at": "2026-10-20T01:00:00Z", "counts": {"subscribe": 1, "access": 2, "views": 3}, "rating": 4.5, "domain": "Banking", "community": unit.title(), "application": "Ledger", "product_type": "TABLE", "records": 12000, "path": f"Meridian > {unit} > Accounts", "data_format": "Parquet", "storage": "1.2 GB", "last_dq_run": "2026-10-20T00:00:00Z", "lineage": {"origin_paths": ["source"], "destination_paths": ["served"], "upstream": [], "downstream": []}, "fields": [{"name": "account_id", "business_name": "Account ID", "terms": ["Catalogue account"], "quality": {"dimension": "Completeness", "rule": "Present", "rule_type": "completeness", "fail_percent": 0}}]}


def released(tmp_path: Path, catalogue: bool = True) -> Released:
    artifact = tmp_path / "artifacts" / "fixture" / "abc123"
    write_csv(artifact / "data/nodes/Col.csv", [{"id": "retail.accounts.account_id", "data_type": "VARCHAR", "description": "Retail account identifier"}, {"id": "wealth.accounts.account_id", "data_type": "VARCHAR", "description": "Wealth account identifier"}])
    write_csv(artifact / "data/edges/MAPS_TO__Col__Term.csv", [{"from": "retail.accounts.account_id", "to": "Account"}])
    if catalogue:
        (artifact / "catalogue.yaml").write_text(yaml.safe_dump({"format": 1, "units": ["retail", "wealth"], "products": [product("retail"), product("wealth")]}))
    return Released(artifact)


def test_the_release_returns_only_readable_units_with_kge_fields(tmp_path):
    response = get(released(tmp_path), ["retail"])
    assert response.release == "abc123|generation|catalogue-badges-v3"
    assert [item.id for item in response.products] == ["retail.accounts"]
    assert response.has_catalogue is True
    field = response.products[0].fields[0]
    assert (field.data_type, field.description, field.terms) == ("VARCHAR", "Retail account identifier", ["Account", "Catalogue account"])
    assert get(released(tmp_path / "none"), []).products == []


def test_catalogue_terms_fall_back_to_the_catalogue_when_kge_has_no_mapping(tmp_path):
    response = get(released(tmp_path), ["wealth"])
    assert response.products[0].fields[0].terms == ["Catalogue account"]


def test_an_old_release_without_a_catalogue_still_answers(tmp_path):
    profile = released(tmp_path, catalogue=False)
    response = get(profile, ["wealth"])
    assert response.products == [] and response.has_catalogue is False


def test_an_old_shape_catalogue_defaults_new_fields(tmp_path):
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    catalogue = yaml.safe_load(path.read_text())
    for item in catalogue["products"]:
        for key in ("records", "path", "data_format", "storage", "last_dq_run"):
            item.pop(key)
        for field in item["fields"]:
            field.pop("terms")
    path.write_text(yaml.safe_dump(catalogue))
    response = get(profile, ["wealth"])
    assert response.has_catalogue is True
    assert response.products[0].records == 0
    assert response.products[0].storage == ""
    assert response.products[0].fields[0].terms == []
    assert response.release == identity(profile).release == "abc123|generation|catalogue-badges-v3"
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(response.model_dump(exclude_unset=True))


def test_lineage_is_limited_to_products_in_readable_units(tmp_path):
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0]["lineage"]["upstream"] = ["retail.accounts", "wealth.accounts"]
    held["products"][0]["lineage"]["downstream"] = ["wealth.accounts"]
    path.write_text(yaml.safe_dump(held))
    lineage = get(profile, ["retail"]).products[0].lineage
    assert lineage.upstream == ["retail.accounts"]
    assert lineage.downstream == []


def request(headers: dict[str, str]) -> Request:
    return Request({"type": "http", "method": "GET", "path": "/catalogue/portal",
                    "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()]})


def test_the_whole_catalogue_fails_closed_off_cloud_run(monkeypatch):
    secret = "s" * 32
    compared = []
    real_compare = routes.hmac.compare_digest
    monkeypatch.setattr(routes.hmac, "compare_digest", lambda left, right: compared.append((len(left), len(right))) or real_compare(left, right))
    monkeypatch.setattr(routes, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": None,
                                                                           "PORTAL_SYNC_TOKEN": secret})())
    monkeypatch.setattr(routes, "get", lambda *_: "catalogue")
    monkeypatch.setattr(routes, "served", SimpleNamespace())
    serving_profile = object()
    monkeypatch.setattr(routes.served, "engine", lambda: type("Engine", (), {"profile": serving_profile, "g": type("Graph", (), {"release": lambda self: None, "domain": lambda self: {"units": ["retail"]}})()})(), raising=False)
    for headers in ({"x-portal-service": "catalogue-sync"}, {"x-portal-sync-token": "w" * 32}, {}):
        try:
            routes.whole_catalogue(request(headers))
        except HTTPException as exc:
            assert exc.status_code == 403
        else:
            raise AssertionError("an untrusted portal reached the whole catalogue")
    assert routes.whole_catalogue(request({"x-portal-sync-token": secret})) == "catalogue"
    try:
        routes.whole_catalogue(request({"x-portal-sync-token": "malformed-é"}))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("a malformed portal token reached the whole catalogue")
    assert compared == [(0, 32), (32, 32), (0, 32), (32, 32), (14, 32)]

    monkeypatch.setattr(routes, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": None,
                                                                           "PORTAL_SYNC_TOKEN": None})())
    try:
        routes.whole_catalogue(request({"x-portal-service": "catalogue-sync"}))
    except HTTPException as exc:
        assert exc.status_code == 503 and exc.detail == "the portal sync is not configured"
    else:
        raise AssertionError("an unconfigured portal reached the whole catalogue")


def test_the_cloud_run_catalogue_path_still_uses_the_verified_caller(monkeypatch):
    trusted = type("Trusted", (), {"trusts": lambda self, token: token == "verified-google-token"})()
    monkeypatch.setattr(routes, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": "audience",
                                                                           "PORTAL_SYNC_TOKEN": "s" * 32})())
    monkeypatch.setattr(routes, "served", SimpleNamespace(TRUSTED_CALLER=trusted))
    monkeypatch.setattr(routes, "get", lambda *_: "catalogue")
    serving_profile = object()
    monkeypatch.setattr(routes.served, "engine", lambda: type("Engine", (), {"profile": serving_profile, "g": type("Graph", (), {"release": lambda self: None, "domain": lambda self: {"units": ["retail"]}})()})(), raising=False)
    assert routes.whole_catalogue(request({"x-caller-token": "verified-google-token"})) == "catalogue"
    try:
        routes.whole_catalogue(request({"x-portal-sync-token": "s" * 32}))
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("Cloud Run accepted the local shared-secret path")


def test_catalogue_release_uses_the_same_profile_as_the_serving_engine(monkeypatch):
    old_profile = object()
    released_profile = object()
    monkeypatch.setattr(routes, "served", SimpleNamespace(PROFILE=old_profile))
    monkeypatch.setattr(routes.served, "engine", lambda: type("Engine", (), {"profile": released_profile})(), raising=False)
    monkeypatch.setattr(routes, "authenticate_portal", lambda _request: None)
    monkeypatch.setattr(routes, "release_identity", lambda profile: profile)
    assert routes.portal_catalogue_release(request({})) is released_profile


@pytest.mark.parametrize("client", ["meridian", "fellmoor"])
def test_compiled_endpoint_conforms_to_catalogue_contract(tmp_path, monkeypatch, client):
    import json
    from dataclasses import replace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jsonschema import Draft202012Validator

    from kge.pipeline.profile import resolve
    from tests.scratch import DEMO, compile_pack

    artifact = compile_pack(tmp_path / "artifacts", client=client)
    profile = replace(resolve(client), artifacts=tmp_path / "artifacts", store=tmp_path / "stores", pinned=artifact.name)
    catalogue = yaml.safe_load((artifact / "catalogue.yaml").read_text())
    if client == "fellmoor":
        assert not (DEMO / "clients" / client / "catalogue.yaml").exists()
        assert all("CERTIFIED" not in product["badges"] for product in catalogue["products"])
    engine = SimpleNamespace(profile=profile, g=SimpleNamespace(release=lambda: None, domain=lambda: {"units": catalogue["units"]}))
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: engine))
    monkeypatch.setenv("PORTAL_SYNC_TOKEN", "s" * 32)
    monkeypatch.delenv("TRUSTED_CALLER_AUDIENCE", raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as http:
        response = http.get("/catalogue/portal", headers={"x-portal-sync-token": "s" * 32})
        assert response.status_code == 200
        payload = response.json()
        release = http.get("/catalogue/release", headers={"x-portal-sync-token": "s" * 32}).json()
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)
    assert payload["contract_version"] == "1" and payload["release"] == release["release"] == artifact.name + "|catalogue-badges-v3"
    assert len(payload["products"]) == len(catalogue["products"]) > 0
    assert all(product.get("steward") for product in payload["products"])


def test_catalogue_rejects_unknown_fields_and_missing_release(tmp_path, monkeypatch):
    from pydantic import ValidationError

    from kge.pipeline.profile import ProfileError

    profile = released(tmp_path)
    monkeypatch.setattr(profile, "identity", lambda: {})
    with pytest.raises(ProfileError, match="no released catalogue identity"):
        get(profile, ["retail"])
    profile = released(tmp_path)
    payload = get(profile, ["retail"]).model_dump(exclude_unset=True)
    from kge.services.catalogue import CatalogueResponse
    for target in (payload, payload["products"][0], payload["products"][0]["fields"][0],
                   payload["products"][0]["counts"], payload["products"][0]["lineage"],
                   payload["products"][0]["fields"][0]["quality"]):
        target["unknown"] = True
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CatalogueResponse.model_validate(payload)
        del target["unknown"]


def test_every_meridian_product_preserves_enrichment_and_compiled_model_richness(tmp_path):
    from dataclasses import replace
    from kge.pipeline.profile import resolve
    from tests.scratch import DEMO, compile_pack
    artifact = compile_pack(tmp_path / "artifacts")
    profile = replace(resolve("meridian"), artifacts=tmp_path / "artifacts", pinned=artifact.name)
    enrichment = yaml.safe_load((DEMO / "clients/meridian/catalogue.yaml").read_text())
    response = get(profile, enrichment["units"])
    products = {product.id: product for product in response.products}
    assert len(products) == 59
    assert sum(bool(field.terms) for product in products.values() for field in product.fields) == 244
    for old in enrichment["products"]:
        product = products[old["id"]]
        assert product.data_format == "DuckDB table"
        assert product.quality_score == old["quality_score"]
        fields = {field.name: field for field in product.fields}
        for field in old["fields"]:
            assert set(field.get("terms", [])) <= set(fields[field["name"]].terms)
        for direction, references in old["lineage"].items():
            assert set(references) <= set(getattr(product.lineage, direction))
            assert len(getattr(product.lineage, direction)) >= len(references)
    sage_products = [product for product in products.values() if product.application.startswith("SAGE")]
    assert len(sage_products) == 17
    assert {product.domain for product in sage_products} == {"Client & Account", "Finance & Insurance", "Reference"}
    assert all(any("SAGE > MERIDIAN_DW" in path for path in product.lineage.origin_paths) for product in sage_products)
    for row in rows_from(artifact / "data/edges/MAPS_TO__Col__Term.csv"):
        product_id, column = row["from"].rsplit(".", 1)
        field = next(field for field in products[product_id].fields if field.name == column)
        assert row["to"] in field.terms
    from kge.compiler.pack import read
    for lineage in read(DEMO / "clients", "meridian").data["lineage"]:
        assert set(lineage["derived_from"]) <= set(products[lineage["table"]].lineage.upstream)
        for upstream in lineage["derived_from"]:
            assert lineage["table"] in products[upstream].lineage.downstream
    standalone = yaml.safe_load((DEMO / "examples/meridian-portal/catalogue.yaml").read_text())
    for product in standalone["products"]:
        expected = products[product["id"]].model_dump()
        expected["unit"] = expected.pop("unit_id")
        business_owners = {"product_owner", "business_owner"}
        assert {k: v for k, v in product.items() if k not in business_owners} == {k: v for k, v in expected.items() if k not in business_owners}
        source = next(item for item in enrichment["products"] if item["id"] == product["id"])
        for key in business_owners:
            assert expected[key] == source[key]


def rows_from(path):
    with path.open() as source:
        return list(csv.DictReader(source))


def test_data_format_describes_the_selected_warehouse_without_file_format_claims(monkeypatch):
    from kge.adapters.warehouse import catalogue_format
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    assert catalogue_format() == "BigQuery native table"
    assert catalogue_format("VIEW") == "BigQuery native view"


def test_badges_come_from_enrichment_regardless_of_landed_membership(tmp_path):
    import json
    from kge.landing.zone import Zone
    from kge.pipeline.landed_tables import write_landed_tables
    profile = released(tmp_path)
    profile.landing = tmp_path / "landing"
    before = get(profile, ["retail", "wealth"])
    landed = Zone(profile.landing, profile.client).land_files(
        {"structured/retail.json": json.dumps({"source": "retail", "tables": [{"name": "accounts"}]}).encode()}, {})
    write_landed_tables(profile.graph_path().parent, landed)
    after = get(profile, ["retail", "wealth"])
    assert after == before
    assert [p.badges for p in after.products] == [["Certified"], ["Certified"]]
    assert all("certified" not in p.model_dump() for p in after.products)
