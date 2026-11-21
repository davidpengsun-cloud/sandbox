"""The declared discovery catalogue, its release and its readable-unit filter."""
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
from kge.services import http


def enrichment_pack(folder, declarations):
    from kge.compiler.pack import read

    manifest = {} if declarations is None else {"inputs": declarations}
    (folder / "pack.yaml").write_text(yaml.safe_dump({"manifest": manifest, "sources": []}))
    pack = read(folder.parent, folder.name, lock=False)
    pack.lock = {"sources": {}}
    return pack


@pytest.mark.parametrize("catalogue", [None, "absent"])
def test_undeclared_catalogue_is_not_read_even_when_invalid(tmp_path, catalogue):
    from kge.compiler.catalogue import read

    declarations = {"estate": {"generator": "sample"}, "sets": ["golden"]}
    if catalogue is None:
        declarations["catalogue"] = None
    pack = enrichment_pack(tmp_path, declarations)
    (tmp_path / "catalogue.yaml").write_text("[invalid YAML")
    assert read(pack) == (None, [])


def test_only_the_named_catalogue_is_read_and_findings_name_it(tmp_path):
    from kge.compiler.catalogue import read

    pack = enrichment_pack(tmp_path, {"estate": {"generator": "sample"}, "sets": ["golden"], "catalogue": "discovery.yaml"})
    (tmp_path / "catalogue.yaml").write_text("[invalid YAML")
    value = {"format": 1, "units": [], "products": []}
    (tmp_path / "discovery.yaml").write_text(yaml.safe_dump(value))
    assert read(pack) == (value, [])
    value["units"] = ["unknown"]
    (tmp_path / "discovery.yaml").write_text(yaml.safe_dump(value))
    assert [(f.rule, f.path) for f in read(pack)[1]] == [("C01", "discovery.yaml:units")]
    (tmp_path / "discovery.yaml").write_text("[invalid YAML")
    assert [(f.rule, f.path) for f in read(pack)[1]] == [("C01", "discovery.yaml")]


def test_legacy_pack_reads_catalogue_as_before(tmp_path):
    from kge.compiler.catalogue import read

    pack = enrichment_pack(tmp_path, None)
    assert read(pack) == (None, [])
    value = {"format": 1, "units": [], "products": []}
    (tmp_path / "catalogue.yaml").write_text(yaml.safe_dump(value))
    assert read(pack) == (value, [])
    (tmp_path / "catalogue.yaml").write_text("[invalid YAML")
    assert [(f.rule, f.path) for f in read(pack)[1]] == [("C01", "catalogue.yaml")]


def test_malformed_declarations_do_not_fall_back_to_legacy_catalogue(tmp_path):
    from kge.compiler.catalogue import read

    pack = enrichment_pack(tmp_path, {"estate": {"generator": "other"}, "sets": ["golden"]})
    (tmp_path / "catalogue.yaml").write_text("[invalid YAML")
    value, findings = read(pack)
    assert value is None and findings
    assert all(f.rule == "I00" for f in findings)


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
    real_compare = http.hmac.compare_digest
    monkeypatch.setattr(http.hmac, "compare_digest", lambda left, right: compared.append((len(left), len(right))) or real_compare(left, right))
    monkeypatch.setattr(http, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": None,
                                                                           "PORTAL_SYNC_TOKEN": secret})())
    monkeypatch.setattr(routes, "get", lambda *_, **__: "catalogue")
    monkeypatch.setattr(routes, "served", SimpleNamespace())
    serving_profile = object()
    monkeypatch.setattr(routes.served, "engine", lambda: type("Engine", (), {"profile": serving_profile, "g": type("Graph", (), {"catalogue": lambda self: SimpleNamespace(figures={}), "release": lambda self: None, "domain": lambda self: {"units": ["retail"]}})()})(), raising=False)
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

    monkeypatch.setattr(http, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": None,
                                                                           "PORTAL_SYNC_TOKEN": None})())
    try:
        routes.whole_catalogue(request({"x-portal-service": "catalogue-sync"}))
    except HTTPException as exc:
        assert exc.status_code == 503 and exc.detail == "the portal sync is not configured"
    else:
        raise AssertionError("an unconfigured portal reached the whole catalogue")


def test_the_cloud_run_catalogue_path_still_uses_the_verified_caller(monkeypatch):
    trusted = type("Trusted", (), {"trusts": lambda self, token: token == "verified-google-token"})()
    monkeypatch.setattr(http, "settings", lambda: type("Settings", (), {"TRUSTED_CALLER_AUDIENCE": "audience",
                                                                           "PORTAL_SYNC_TOKEN": "s" * 32})())
    monkeypatch.setattr(routes, "served", SimpleNamespace(TRUSTED_CALLER=trusted))
    monkeypatch.setattr(http, "served", routes.served)
    monkeypatch.setattr(routes, "get", lambda *_, **__: "catalogue")
    serving_profile = object()
    monkeypatch.setattr(routes.served, "engine", lambda: type("Engine", (), {"profile": serving_profile, "g": type("Graph", (), {"catalogue": lambda self: SimpleNamespace(figures={}), "release": lambda self: None, "domain": lambda self: {"units": ["retail"]}})()})(), raising=False)
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
    monkeypatch.setattr(routes.served, "engine", lambda: SimpleNamespace(profile=released_profile, g=SimpleNamespace(catalogue=lambda: SimpleNamespace(figures={}))), raising=False)
    monkeypatch.setattr(routes, "authenticate_portal", lambda _request: None)
    monkeypatch.setattr(routes, "release_identity", lambda profile, figures: profile)
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
    engine = SimpleNamespace(profile=profile, g=SimpleNamespace(catalogue=lambda: SimpleNamespace(figures={p["id"]: {"records": 7, "storage": "8 bytes"} for p in catalogue["products"]}), release=lambda: None, domain=lambda: {"units": catalogue["units"]}))
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
    assert payload["contract_version"] == "1" and payload["release"] == release["release"]
    assert payload["release"].startswith(artifact.name + "|catalogue-badges-v3|")
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
    from kge.compiler.catalogue import resolve as resolve_use_cases
    from tests.scratch import DEMO, compile_pack
    artifact = compile_pack(tmp_path / "artifacts")
    profile = replace(resolve("meridian"), artifacts=tmp_path / "artifacts", pinned=artifact.name)
    enrichment = yaml.safe_load((DEMO / "clients/meridian/catalogue.yaml").read_text())
    standalone = yaml.safe_load((DEMO / "examples/meridian-portal/catalogue.yaml").read_text())
    figures = {p["id"]: {k: p[k] for k in ("records", "storage")} for p in standalone["products"]}
    response = get(profile, enrichment["units"], figures)
    products = {product.id: product for product in response.products}
    assert len(products) == 59
    assert sum(bool(field.terms) for product in products.values() for field in product.fields) == 244
    portal_only = {old["id"] for old in enrichment["products"] if old["id"] not in products}
    assert portal_only == {"retail.document_set", "sme.document_set", "corporate.document_set", "wealth.document_set"}
    for old in enrichment["products"]:
        if old["id"] in portal_only:
            assert old["product_type"] == "Documents" and old["documents"]
            continue
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
    source_pack = read(DEMO / "clients", "meridian").data
    role_titles = {role["label"] for role in source_pack["access"]["roles"]}
    for lineage in source_pack["lineage"]:
        assert set(lineage["derived_from"]) <= set(products[lineage["table"]].lineage.upstream)
        for upstream in lineage["derived_from"]:
            assert lineage["table"] in products[upstream].lineage.downstream
    standalone = yaml.safe_load((DEMO / "examples/meridian-portal/catalogue.yaml").read_text())
    for product in standalone["products"]:
        expected = products[product["id"]].model_dump()
        expected.pop("catalogue_state", None)
        expected.pop("portal_only", None)
        expected.pop("documents", None)
        expected["unit"] = expected.pop("unit_id")
        assert expected.pop("refresh_cadence") == ""
        assert expected.pop("schema") == ""
        source = next(item for item in enrichment["products"] if item["id"] == product["id"])
        declared = source.get("use_cases", [])
        resolved = {item["id"]: item for item in resolve_use_cases(enrichment)["products"]}[product["id"]].get("use_cases", [])
        assert [{key: value for key, value in case.items() if value} for case in expected["use_cases"]] == resolved
        assert [case["id"] for case in resolved] == declared
        if declared:
            expected["use_cases"] = declared
        else:
            assert expected.pop("use_cases") == []
        for field in expected["fields"]:
            assert field.pop("pii") is False
            assert field["quality"].pop("description") == ""
        business_owners = {"product_owner", "business_owner"}
        titles = business_owners | {"steward"}
        assert {k: v for k, v in product.items() if k not in titles} == {k: v for k, v in expected.items() if k not in titles}
        assert expected["steward"] in role_titles
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


def meridian_catalogue():
    from kge.compiler.catalogue import read
    from kge.compiler.pack import read as read_pack
    pack = read_pack(Path("clients"), "meridian")
    value, findings = read(pack)
    assert findings == []
    return pack, value


def test_vocabulary_values_are_accepted_in_any_case_and_older_spellings_are_normalised():
    from kge.compiler.catalogue import derive, validate
    pack, value = meridian_catalogue()
    product = value["products"][0]
    product.update(product_type="TABLE", asset_type="structured", usage_purpose="OPERATIONAL")
    assert validate(value, pack) == []
    derived = {item["id"]: item for item in derive(value, pack)["products"]}[product["id"]]
    assert (derived["product_type"], derived["asset_type"], derived["usage_purpose"]) == ("Table", "Structured", "Operational")


@pytest.mark.parametrize("field,bad", [("product_type", "Spreadsheet"), ("asset_type", "Semi-structured"), ("usage_purpose", "Archival")])
def test_a_value_outside_a_vocabulary_is_refused(field, bad):
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    value["products"][0][field] = bad
    assert [(f.rule, f.path.rsplit(".", 1)[-1]) for f in validate(value, pack)] == [("C01", field)]


def test_a_sources_apm_id_reaches_each_of_its_tables():
    from kge.compiler.catalogue import derive
    pack, value = meridian_catalogue()
    source = next(item for item in value.get("sources", []) if item.get("APM_ID"))
    derived = [item for item in derive(value, pack)["products"] if item["id"].split(".", 1)[0] == source["id"]]
    assert derived and all(item["APM_ID"] == source["APM_ID"] for item in derived)


def test_classification_project_and_owner_emails_are_accepted_and_normalised():
    from kge.compiler.catalogue import derive, validate
    pack, value = meridian_catalogue()
    product = value["products"][0]
    table = next(item for item in pack.lock["sources"][product["unit"]]["tables"] if item["name"] == product["table"])
    columns = [column["name"] for column in table["columns"]]
    held = {field["name"]: field for field in product.setdefault("fields", [])}
    for name, classification in zip(columns[:2], ("restricted", "HIGHLY CONFIDENTIAL")):
        held.setdefault(name, {"name": name})["classification"] = classification
    product["fields"] = list(held.values())
    product.update(project="Customer 360", product_owner_email=" dana.smith@example.com ", business_owner_email="")
    assert validate(value, pack) == []
    derived = {item["id"]: item for item in derive(value, pack)["products"]}
    mine = derived[product["id"]]
    assert (mine["project"], mine["product_owner_email"], mine["business_owner_email"]) == ("Customer 360", "dana.smith@example.com", "")
    classes = {field["name"]: field.get("classification") for field in mine["fields"]}
    assert [classes[name] for name in columns[:2]] == ["Restricted", "Highly Confidential"]
    others = [item for key, item in derived.items() if key != product["id"]]
    assert others and all(not {"project", "product_owner_email", "business_owner_email"} & set(item) for item in others)


@pytest.mark.parametrize("key,bad", [("project", 7), ("product_owner_email", "dana"), ("business_owner_email", "a@b"),
                                     ("product_owner_email", 3)])
def test_a_malformed_project_or_owner_email_is_refused(key, bad):
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    value["products"][0][key] = bad
    assert [(f.rule, f.path.rsplit(".", 1)[-1]) for f in validate(value, pack)] == [("C01", key)]


@pytest.mark.parametrize("bad", ["Secret", "", 3])
def test_a_field_classification_outside_the_scale_is_refused(bad):
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    product = value["products"][0]
    table = next(item for item in pack.lock["sources"][product["unit"]]["tables"] if item["name"] == product["table"])
    column = table["columns"][0]["name"]
    held = {field["name"]: field for field in product.setdefault("fields", [])}
    held.setdefault(column, {"name": column})["classification"] = bad
    product["fields"] = list(held.values())
    findings = validate(value, pack)
    assert [(f.rule, f.path.rsplit(".", 1)[-1]) for f in findings] == [("C01", "classification")]
    assert "Highly Confidential" in findings[0].message


def test_the_release_carries_classification_project_and_owner_emails_in_the_contract(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0].update(project="Customer 360", product_owner_email="dana@example.com", business_owner_email="lee@example.com")
    held["products"][0]["fields"][0]["classification"] = "Restricted"
    path.write_text(yaml.safe_dump(held))
    response = get(profile, ["retail", "wealth"])
    first, second = response.products
    assert (first.project, first.product_owner_email, first.business_owner_email) == ("Customer 360", "dana@example.com", "lee@example.com")
    assert first.fields[0].classification == "Restricted"
    assert (second.project, second.product_owner_email, second.fields[0].classification) == ("", "", "")
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(response.model_dump())



def test_hotfix_fields_are_checked_and_derived_without_changing_existing_catalogues():
    from kge.compiler.catalogue import derive, validate
    pack, value = meridian_catalogue()
    product = value["products"][0]
    field = product["fields"][0]
    product["refresh_cadence"] = "Daily"
    field["pii"] = True
    field["quality"]["description"] = "Every row has an identifier."
    assert validate(value, pack) == []
    mine = next(item for item in derive(value, pack)["products"] if item["id"] == product["id"])
    actual = next(item for item in mine["fields"] if item["name"] == field["name"])
    assert mine["refresh_cadence"] == "Daily"
    assert actual["pii"] is True
    assert actual["quality"]["description"] == "Every row has an identifier."
    for target, key, bad in [(product, "refresh_cadence", 7), (field, "pii", "Yes"),
                             (field, "pii", 1), (field["quality"], "description", False),
                             (field["quality"], "unexpected", "value")]:
        before = target.get(key)
        target[key] = bad
        findings = validate(value, pack)
        assert len(findings) == 1 and key in findings[0].path + findings[0].message
        if before is None:
            del target[key]
        else:
            target[key] = before
    product["refresh_cadence"] = ""
    field["pii"] = False
    field["quality"]["description"] = ""
    assert validate(value, pack) == []
    for target, key in [(product, "refresh_cadence"), (field, "pii"), (field["quality"], "description")]:
        del target[key]
    assert validate(value, pack) == []


def test_hotfix_fields_reach_the_release_contract_and_older_catalogues_load(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0]["refresh_cadence"] = "Daily"
    held["products"][0]["fields"][0]["pii"] = True
    held["products"][0]["fields"][0]["quality"]["description"] = "Identifier must be present."
    path.write_text(yaml.safe_dump(held))
    response = get(profile, ["retail", "wealth"])
    first, second = response.products
    assert first.refresh_cadence == "Daily"
    assert first.fields[0].pii is True
    assert first.fields[0].quality.description == "Identifier must be present."
    assert (second.refresh_cadence, second.fields[0].pii, second.fields[0].quality.description) == ("", False, "")
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(response.model_dump())


def test_path_data_format_apm_id_and_schema_override_the_derived_values_and_unset_keep_them():
    from kge.compiler.catalogue import derive, validate
    pack, value = meridian_catalogue()
    source = next(item for item in value.get("sources", []) if item.get("APM_ID"))
    mine, other = [item for item in value["products"] if item["unit"] == source["id"]][:2]
    before = {item["id"]: item for item in derive(value, pack)["products"]}
    supplied = {"path": "gs://meridian-docs/policies", "data_format": "PDF", "APM_ID": "APM-DOC-7", "schema": "policies"}
    mine.update(supplied)
    assert validate(value, pack) == []
    after = {item["id"]: item for item in derive(value, pack)["products"]}
    assert {key: after[mine["id"]][key] for key in supplied} == supplied
    assert after[other["id"]] == before[other["id"]]
    assert "schema" not in after[other["id"]]
    assert (after[other["id"]]["path"], after[other["id"]]["APM_ID"]) == (other["id"], source["APM_ID"])
    for key in supplied:
        for bad in ("", "  ", 7, None):
            held = mine[key]
            mine[key] = bad
            assert [(f.rule, f.path.rsplit(".", 1)[-1]) for f in validate(value, pack)] == [("C01", key)]
            mine[key] = held
    mine["schema_name"] = "policies"
    findings = validate(value, pack)
    assert len(findings) == 1 and "unknown keys schema_name" in findings[0].message


def test_a_column_takes_several_rules_and_a_blank_fail_percent_without_a_zero():
    from kge.compiler.catalogue import derive, validate
    pack, value = meridian_catalogue()
    product = value["products"][0]
    field = product["fields"][0]
    first = dict(field["quality"])
    blank = {"dimension": "Validity", "rule": "Well formed", "rule_type": "custom", "fail_percent": None}
    absent = {"dimension": "Timeliness", "rule": "Loaded daily", "rule_type": "custom"}
    field["quality"] = [first, blank, absent]
    assert validate(value, pack) == []
    derived = next(item for item in derive(value, pack)["products"] if item["id"] == product["id"])
    rules = next(item for item in derived["fields"] if item["name"] == field["name"])["quality"]
    assert rules == [first, blank, absent]
    assert rules[1]["fail_percent"] is None and "fail_percent" not in rules[2]
    for bad, where in [([], "quality"), ([first, {**blank, "unknown": 1}], "quality[1]"), ([first, {**blank, "fail_percent": "blank"}], "fail_percent"),
                       ([first, {**blank, "fail_percent": 101}], "fail_percent"), ([first, "rule"], "quality[1]"), ([first, {"rule": "x", "rule_type": "y"}], "quality[1]")]:
        field["quality"] = bad
        findings = validate(value, pack)
        assert len(findings) == 1 and findings[0].path.endswith(where), (bad, findings)
    field["quality"] = {**first, "fail_percent": None}
    assert validate(value, pack) == []
    del field["quality"]["fail_percent"]
    assert validate(value, pack) == []
    field["quality"]["unknown"] = 1
    assert "unknown keys unknown" in validate(value, pack)[0].message


def test_supplied_values_and_rules_survive_the_release_refresh_and_reach_the_contract(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    supplied = {"path": "gs://meridian-docs/policies", "data_format": "PDF", "APM_ID": "APM-DOC-7", "schema": "policies"}
    held["products"][0].update(supplied)
    rules = [{"dimension": "Completeness", "rule": "Present", "rule_type": "completeness", "fail_percent": 0}, {"dimension": "Validity", "rule": "Well formed", "rule_type": "custom", "fail_percent": None}]
    held["products"][0]["fields"][0]["quality"] = rules
    held["products"][1]["fields"][0]["quality"] = {"dimension": "Completeness", "rule": "Present", "rule_type": "completeness"}
    path.write_text(yaml.safe_dump(held))
    figures = {"retail.accounts": {"records": 7, "storage": "8 bytes"}, "wealth.accounts": {"records": 9, "storage": "9 bytes"}}
    response = get(profile, ["retail", "wealth"], figures)
    first, second = response.products
    assert (first.path, first.data_format, first.APM_ID, first.schema_name, first.records, first.storage) == (*supplied.values(), 7, "8 bytes")
    assert (second.path, second.data_format, second.schema_name) == ("Meridian > wealth > Accounts", "Parquet", "")
    assert [rule.fail_percent for rule in first.fields[0].quality] == [0, None]
    assert second.fields[0].quality.fail_percent is None
    payload = response.model_dump(mode="json", exclude_unset=True)
    assert payload["products"][0]["schema"] == "policies" and "schema" not in payload["products"][1]
    assert "fail_percent" not in payload["products"][1]["fields"][0]["quality"]
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)
    Draft202012Validator(schema).validate(response.model_dump(mode="json"))


def test_the_portal_route_sends_schema_rule_lists_and_blank_fail_percent_as_the_contract_reads_them(tmp_path, monkeypatch):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0]["schema"] = "policies"
    held["products"][0]["fields"][0]["quality"] = [{"dimension": "Completeness", "rule": "Present", "rule_type": "completeness", "fail_percent": 0},
                                                   {"dimension": "Validity", "rule": "Well formed", "rule_type": "custom"}]
    path.write_text(yaml.safe_dump(held))
    engine = SimpleNamespace(profile=profile, g=SimpleNamespace(catalogue=lambda: SimpleNamespace(figures=None), domain=lambda: {"units": ["retail", "wealth"]}))
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: engine))
    monkeypatch.setenv("PORTAL_SYNC_TOKEN", "s" * 32)
    monkeypatch.delenv("TRUSTED_CALLER_AUDIENCE", raising=False)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as http:
        response = http.get("/catalogue/portal", headers={"x-portal-sync-token": "s" * 32})
    assert response.status_code == 200
    payload = response.json()
    first, second = payload["products"]
    assert first["schema"] == "policies" and "schema_name" not in first and "schema" not in second
    assert first["fields"][0]["quality"] == [{"dimension": "Completeness", "rule": "Present", "rule_type": "completeness", "fail_percent": 0.0},
                                             {"dimension": "Validity", "rule": "Well formed", "rule_type": "custom"}]
    assert second["fields"][0]["quality"]["fail_percent"] == 0
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)


USE_CASES = [{"title": "Month-end review", "description": "Finance compares revenue with the plan.", "url": "https://example.com/review"},
             {"title": "Branch ranking", "description": "Regional leads rank branches by growth."}]
DEFINED = [{"id": "uc-month-end-review", "title": "Month-end review", "objective": "Finance compares revenue with the plan.",
            "domain": "Finance", "owner": "Financial Controller", "url": "https://example.com/review",
            "kpi": {"name": "Monthly revenue", "definition": "Interest and fee income in the month.", "grain": "Month", "unit": "USD"},
            "example_questions": ["Total retail revenue for June 2025"]},
           {"id": "uc-branch-ranking", "title": "Branch ranking", "objective": "Regional leads rank branches by growth."}]
RESOLVED_INLINE = [{"id": "uc-month-end-review", "title": "Month-end review", "objective": "Finance compares revenue with the plan.",
                    "url": "https://example.com/review"},
                   {"id": "uc-branch-ranking", "title": "Branch ranking", "objective": "Regional leads rank branches by growth."}]


def with_defined(value, *entries):
    value["use_cases"] = [*value.get("use_cases", []), *(dict(entry) for entry in (entries or DEFINED))]
    return value


def test_use_cases_are_defined_once_listed_by_id_and_compiled_resolved_in_the_asset_order():
    from kge.compiler.catalogue import DESCRIPTIVE, derive, validate
    pack, value = meridian_catalogue()
    for product in value["products"]:
        product.pop("use_cases", None)
    value.pop("use_cases")
    without = derive(value, pack)
    assert all("use_cases" not in product for product in without["products"])
    with_defined(value)
    product = value["products"][0]
    product["use_cases"] = ["uc-branch-ranking", "uc-month-end-review"]
    assert validate(value, pack) == []
    compiled = derive(value, pack)
    assert "use_cases" not in compiled
    derived = {item["id"]: item for item in compiled["products"]}
    assert derived[product["id"]]["use_cases"] == [DEFINED[1], DEFINED[0]]
    assert {**derived[product["id"]], "use_cases": None} == {**{item["id"]: item for item in without["products"]}[product["id"]], "use_cases": None}
    assert "use_cases" in DESCRIPTIVE
    product["use_cases"] = []
    assert validate(value, pack) == []


def test_resolving_is_idempotent_and_keeps_only_the_resolved_keys():
    from kge.compiler.catalogue import resolve, use_case_entry
    value = {"format": 1, "units": [], "use_cases": DEFINED, "products": [{"id": "retail.accounts", "use_cases": ["uc-month-end-review"]}]}
    once = resolve(value)
    assert once["products"][0]["use_cases"] == [DEFINED[0]] and value["products"][0]["use_cases"] == ["uc-month-end-review"]
    assert resolve(once) == once
    assert use_case_entry({**DEFINED[1], "domain": "", "kpi": None, "stray": "dropped"}) == DEFINED[1]
    assert use_case_entry({"title": "Q&A -- Review!", "description": "Inline."}) == {"id": "uc-q-a-review", "title": "Q&A -- Review!", "objective": "Inline."}
    assert [use_case_entry(entry) for entry in USE_CASES] == RESOLVED_INLINE


def test_an_unknown_use_case_id_is_refused_naming_it():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    with_defined(value)
    product = value["products"][1]
    product["use_cases"] = ["uc-month-end-review", "uc-missing"]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:products[1].use_cases[1]", f"{product['id']} names unknown use case uc-missing")]


def test_a_use_case_defined_twice_is_refused_naming_it():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    with_defined(value, *DEFINED, {"id": "uc-branch-ranking", "title": "Again", "objective": "A second definition."})
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        (f"catalogue.yaml:use_cases[{len(value['use_cases']) - 1}].id", "uc-branch-ranking is defined twice")]


def test_an_asset_that_lists_a_use_case_twice_is_refused_naming_it():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    with_defined(value)
    product = value["products"][2]
    product["use_cases"] = ["uc-branch-ranking", "uc-month-end-review", "uc-branch-ranking"]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:products[2].use_cases[2]", f"{product['id']} lists use case uc-branch-ranking twice")]


def test_an_inline_use_case_whose_id_the_asset_also_lists_is_refused_as_listed_twice():
    from kge.compiler.catalogue import use_case_slug, validate
    pack, value = meridian_catalogue()
    with_defined(value)
    product = value["products"][2]
    product["use_cases"] = ["uc-branch-ranking", {"title": "Branch ranking", "description": "The same use case given inline."}]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:products[2].use_cases[1]", f"{product['id']} lists use case uc-branch-ranking twice")]
    product["use_cases"] = [{"title": "Branch ranking", "description": "Given inline first."}, "uc-branch-ranking"]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:products[2].use_cases[1]", f"{product['id']} lists use case uc-branch-ranking twice")]
    assert use_case_slug("Риск") == "uc-420-438-441-43a" and use_case_slug("日本語") != use_case_slug("Риск")


def test_two_inline_titles_that_slug_alike_on_one_asset_are_accepted_and_the_same_title_twice_is_refused():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    with_defined(value)
    product = value["products"][2]
    product["use_cases"] = [{"title": "A/B", "description": "One."}, {"title": "a b", "description": "Another."}]
    assert validate(value, pack) == []
    product["use_cases"] = [{"title": "A/B", "description": "One."}, {"title": " A/B ", "description": "The same title."}]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:products[2].use_cases[1]", f"{product['id']} lists use case uc-a-b twice")]


@pytest.mark.parametrize("change,where,message", [
    ({"id": "uc month"}, ".id", "must be a non-empty string with no spaces"),
    ({"id": ""}, ".id", "must be a non-empty string with no spaces"),
    ({"title": " "}, ".title", "must give a title"),
    ({"objective": None}, ".objective", "must give an objective"),
    ({"domain": ""}, ".domain", "must be a non-empty string"),
    ({"owner": 3}, ".owner", "must be a non-empty string"),
    ({"url": "http://example.com/x"}, ".url", "https address"),
    ({"kpi": "Revenue"}, ".kpi", "kpi must be a mapping of name, definition, grain and unit"),
    ({"kpi": {"name": "Revenue", "target": 5}}, ".kpi", "kpi has unknown keys target"),
    ({"kpi": {"definition": "No name."}}, ".kpi.name", "a kpi must give a name"),
    ({"kpi": {"name": "Revenue", "unit": ""}}, ".kpi.unit", "must be a non-empty string"),
    ({"example_questions": "One question"}, ".example_questions", "a list of non-empty strings"),
    ({"example_questions": ["Fine", " "]}, ".example_questions", "a list of non-empty strings"),
    ({"description": "The inline key"}, "", "unknown keys description"),
])
def test_a_use_case_definition_that_breaks_a_rule_is_refused(change, where, message):
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    with_defined(value, DEFINED[1], {**DEFINED[0], **change})
    findings = validate(value, pack)
    assert len(findings) == 1
    assert findings[0].path == f"catalogue.yaml:use_cases[{len(value['use_cases']) - 1}]{where}" and message in findings[0].message


def test_a_use_cases_list_that_is_not_a_list_or_holds_no_mapping_is_refused():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    for product in value["products"]:
        product.pop("use_cases", None)
    value["use_cases"] = {"id": "uc-one"}
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [("catalogue.yaml:use_cases", "must be a list")]
    value["use_cases"] = ["uc-one"]
    assert [(finding.path, finding.message) for finding in validate(value, pack)] == [
        ("catalogue.yaml:use_cases[0]", "must be a mapping of id, title and objective")]


@pytest.mark.parametrize("entry,where,message", [
    ({"description": "No title."}, ".title", "must give a title"),
    ({"title": "  ", "description": "Blank title."}, ".title", "must give a title"),
    ({"title": "No description"}, ".description", "must give a description"),
    ({"title": "Blank", "description": ""}, ".description", "must give a description"),
    ({"title": "Plain web", "description": "Not https.", "url": "http://example.com/x"}, ".url", "https address"),
    ({"title": "Script", "description": "Not an address.", "url": "javascript:alert(1)"}, ".url", "https address"),
    ({"title": "No host", "description": "Scheme alone.", "url": "https://"}, ".url", "https address"),
    ({"title": "Blank url", "description": "Empty.", "url": ""}, ".url", "https address"),
    ({"title": "Spaced", "description": "A space.", "url": "https://example.com/a b"}, ".url", "https address"),
    ({"title": "Upper", "description": "Not written https://.", "url": "HTTPS://example.com"}, ".url", "https address"),
    ({"title": "Extra", "description": "Unknown key.", "purpose": "Not an ODPS use-case field"}, "", "unknown keys purpose"),
    (42, "", "must be an id, or a mapping"),
])
def test_an_inline_use_case_without_a_title_or_description_or_with_a_non_https_url_is_refused_naming_the_asset(entry, where, message):
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    product = value["products"][1]
    product["use_cases"] = [dict(USE_CASES[0]), entry]
    findings = validate(value, pack)
    assert len(findings) == 1
    assert findings[0].path == f"catalogue.yaml:products[1].use_cases[1]{where}"
    assert findings[0].message.startswith(f"{product['id']}: ") and message in findings[0].message


def test_use_cases_that_are_not_a_list_are_refused_naming_the_asset():
    from kge.compiler.catalogue import validate
    pack, value = meridian_catalogue()
    value["products"][0]["use_cases"] = {"title": "One", "description": "Not in a list."}
    findings = validate(value, pack)
    assert [(finding.path, finding.message) for finding in findings] == [
        ("catalogue.yaml:products[0].use_cases", f"{value['products'][0]['id']}: use_cases must be a list")]


def write_catalogue(tmp_path, value):
    folder = tmp_path / "pack"
    folder.mkdir(exist_ok=True)
    (folder / "catalogue.yaml").write_text(yaml.safe_dump(value))
    return SimpleNamespace(data={}, folder=folder)


def test_inline_use_cases_still_load_resolved_with_a_warning_for_each(tmp_path):
    from kge.compiler.catalogue import derive, validate, warnings
    pack, value = meridian_catalogue()
    product = value["products"][2]
    product["use_cases"] = [dict(USE_CASES[0]), "uc-account-servicing", dict(USE_CASES[1])]
    assert validate(value, pack) == []
    held = write_catalogue(tmp_path, value)
    advice = "gives a use case inline; define it under use_cases and list its id (inline entries are accepted for one release)"
    assert warnings(held) == [f"catalogue.yaml:products[2].use_cases[0]: {product['id']} {advice}",
                              f"catalogue.yaml:products[2].use_cases[2]: {product['id']} {advice}"]
    derived = {item["id"]: item for item in derive(value, pack)["products"]}[product["id"]]["use_cases"]
    assert [case["id"] for case in derived] == ["uc-month-end-review", "uc-account-servicing", "uc-branch-ranking"]
    assert [derived[0], derived[2]] == RESOLVED_INLINE


def test_more_than_three_use_cases_warn_without_refusing(tmp_path):
    from kge.compiler.catalogue import read, validate, warnings
    pack, value = meridian_catalogue()
    four = [{"id": f"uc-use-{index}", "title": f"Use {index}", "objective": f"Use case {index}."} for index in range(4)]
    with_defined(value, *four)
    value["products"][2]["use_cases"] = [case["id"] for case in four]
    assert validate(value, pack) == []
    held = write_catalogue(tmp_path, value)
    assert warnings(held) == [f"catalogue.yaml:products[2].use_cases: {value['products'][2]['id']} has 4 use cases; Discover shows the first 3"]
    value["products"][2]["use_cases"] = value["products"][2]["use_cases"][:3]
    write_catalogue(tmp_path, value)
    assert warnings(held) == []
    assert warnings(SimpleNamespace(data={}, folder=tmp_path / "none")) == []
    shipped = read(pack)[0]
    assert all(len(product.get("use_cases", [])) <= 3 for product in shipped["products"])


def test_meridian_defines_its_use_cases_once_lists_them_by_id_and_its_pack_does_not_warn():
    from kge.compiler.catalogue import warnings
    pack, value = meridian_catalogue()
    defined = {case["id"]: case for case in value["use_cases"]}
    declared = {product["id"]: product["use_cases"] for product in value["products"] if product.get("use_cases")}
    assert 3 <= len(declared) <= 6
    assert all(1 <= len(cases) <= 3 and all(isinstance(case, str) and case in defined for case in cases) for cases in declared.values())
    assert {case for cases in declared.values() for case in cases} == set(defined)
    assert any(case.get("url") for case in defined.values()) and any(not case.get("url") for case in defined.values())
    assert any(case.get("kpi") and case.get("example_questions") for case in defined.values())
    assert all(case.get("domain") and case.get("owner") for case in defined.values())
    assert warnings(pack) == []


def test_use_cases_reach_the_release_contract_and_a_product_without_them_sends_none(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0]["use_cases"] = DEFINED
    path.write_text(yaml.safe_dump(held))
    response = get(profile, ["retail", "wealth"])
    first, second = response.products
    assert [case.model_dump(exclude_unset=True) for case in first.use_cases] == DEFINED
    assert second.use_cases == []
    payload = response.model_dump(exclude_unset=True)
    assert payload["products"][0]["use_cases"] == DEFINED and "use_cases" not in payload["products"][1]
    assert "kpi" not in response.model_dump()["products"][0]["use_cases"][1]
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(payload)
    Draft202012Validator(schema).validate(response.model_dump())


def test_a_release_with_use_cases_in_the_inline_shape_still_serves_them_resolved(tmp_path):
    import json
    from jsonschema import Draft202012Validator
    from tests.scratch import DEMO
    profile = released(tmp_path)
    path = profile.artifact / "catalogue.yaml"
    held = yaml.safe_load(path.read_text())
    held["products"][0]["use_cases"] = USE_CASES
    path.write_text(yaml.safe_dump(held))
    response = get(profile, ["retail", "wealth"])
    assert [case.model_dump(exclude_unset=True) for case in response.products[0].use_cases] == RESOLVED_INLINE
    schema = json.loads((DEMO / "portal/server/contract/catalogue.v1.schema.json").read_text())
    Draft202012Validator(schema).validate(response.model_dump(exclude_unset=True))


def test_a_portal_publish_carries_changed_use_cases_as_a_descriptive_field():
    from kge.compiler.catalogue_publish import apply_card, diff_lines
    before = [{"id": "retail.accounts", "unit": "retail", "table": "accounts", "portal_only": False, "descriptive": {"use_cases": USE_CASES}}]
    after = [{**before[0], "descriptive": {"use_cases": USE_CASES[1:]}}]
    lines = diff_lines(before, after)
    assert len(lines) == 1 and lines[0].startswith("  retail.accounts use_cases: ")
    assert apply_card({"id": "retail.accounts", "use_cases": USE_CASES}, after[0])["use_cases"] == USE_CASES[1:]
