"""Build the discovery catalogue from a released KGE artifact."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kge.pipeline.profile import Profile, ProfileError


class QualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    rule: str
    description: str = ""
    rule_type: str
    fail_percent: float | None = Field(default=None, ge=0, le=100)


class CatalogueField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    business_name: str
    data_type: str
    description: str
    quality: QualityRule | Annotated[list[QualityRule], Field(min_length=1)]
    terms: list[str] = Field(default_factory=list)
    classification: str = ""
    pii: bool = False


class Counts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscribe: int = Field(ge=0)
    access: int = Field(ge=0)
    views: int = Field(ge=0)


class Lineage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin_paths: list[str]
    destination_paths: list[str]
    upstream: list[str]
    downstream: list[str]


class CatalogueDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    title: str


class CatalogueProduct(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)
    id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str
    product_owner: str
    business_owner: str
    product_owner_email: str = ""
    business_owner_email: str = ""
    steward: str = ""
    badges: list[str]
    quality_score: float = Field(ge=0, le=100)
    last_refresh: str = ""
    refresh_cadence: str = ""
    ingested_at: str = ""
    counts: Counts
    rating: float = Field(ge=0, le=5)
    domain: str
    community: str
    application: str
    product_type: str
    asset_type: str = ""
    usage_purpose: str = ""
    dataset_type: str = ""
    APM_ID: str = ""
    schema_name: str = Field(default="", alias="schema")
    project: str = ""
    records: int | None = Field(default=0, ge=0)
    path: str = ""
    data_format: str = ""
    storage: str = ""
    last_dq_run: str = ""
    lineage: Lineage
    fields: list[CatalogueField]
    catalogue_state: str = ""
    portal_only: bool = False
    documents: list[CatalogueDocument] = Field(default_factory=list)


class CatalogueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1"]
    release: str = Field(min_length=1)
    has_catalogue: bool
    products: list[CatalogueProduct]


class CatalogueIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    release: str | None = None
    has_catalogue: bool


def rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="") as source:
            return list(csv.DictReader(source))
    except FileNotFoundError:
        return []


def release_id(profile: Profile, figures: dict | None = None, portal: str = "") -> str:
    """Include the snapshot's figures so a row-only reload refreshes the portal even at the same model hash.

    A portal publish that the graph release does not yet carry adds ``portal`` so the portal refreshes
    without a new graph hash.
    """
    held = profile.identity()
    if not held or not held.get("hash"):
        raise ProfileError("no released catalogue identity is available")
    versions = [json.dumps(held["warehouses"], sort_keys=True, separators=(",", ":"))] if held.get("warehouses") else []
    snapshot = [hashlib.sha256(json.dumps(figures, sort_keys=True, separators=(",", ":")).encode()).hexdigest()] if figures is not None else []
    base = "|".join([*(str(held.get(key)) for key in ("hash", "graph", "chunks") if held.get(key)), *versions, "catalogue-badges-v3", *snapshot])
    return base + portal if portal else base


def _published(profile: Profile):
    from kge.compiler.catalogue_publish import artifact_products, load, suffix

    current = load(profile)
    cards = {card["id"]: card for card in (current or {}).get("products", [])}
    released = artifact_products(profile)
    return cards, suffix(profile, released, current, loaded=True), current


def _portal_product(card: dict) -> dict:
    descriptive = card.get("descriptive", {})
    empty = {"origin_paths": [], "destination_paths": [], "upstream": [], "downstream": []}
    product = {"id": card["id"], "unit": card["unit"], "table": card["table"],
            "name": descriptive.get("name") or card["table"].replace("_", " ").title(),
            "description": descriptive.get("description") or "",
            "product_owner": descriptive.get("product_owner") or "",
            "business_owner": descriptive.get("business_owner") or "",
            "badges": descriptive.get("badges") or [],
            "quality_score": 0, "counts": {"subscribe": 0, "access": 0, "views": 0}, "rating": 0,
            "domain": descriptive.get("domain") or card["unit"],
            "community": descriptive.get("community") or "",
            "application": descriptive.get("application") or "",
            "product_type": descriptive.get("product_type") or "Documents",
            "lineage": empty, "fields": [], "path": descriptive.get("path") or "",
            "data_format": descriptive.get("data_format") or "",
            **{key: descriptive[key] for key in descriptive if key not in {"name", "description", "badges"}},
            "catalogue_state": "Not in the knowledge graph", "portal_only": True,
            "documents": card.get("documents") or []}
    for key, default in {"last_refresh": "", "ingested_at": "", "records": 0, "path": "", "data_format": "",
                         "storage": "", "last_dq_run": ""}.items():
        product.setdefault(key, default)
    return product


def get(profile: Profile, readable_units: list[str], figures: dict | None = None, *, portal: bool = False) -> CatalogueResponse:
    artifact = profile.current() if profile.pinned is None else profile.artifacts / profile.client / profile.pinned
    if artifact is None:
        raise ProfileError("no released catalogue artifact is available")
    try:
        held = yaml.safe_load((artifact / "catalogue.yaml").read_text())
    except FileNotFoundError:
        return CatalogueResponse(contract_version="1", release=release_id(profile, figures), has_catalogue=False, products=[])
    columns = {row["id"]: row for row in rows(artifact / "data/nodes/Col.csv")}
    terms: dict[str, list[str]] = {}
    for row in rows(artifact / "data/edges/MAPS_TO__Col__Term.csv"):
        terms.setdefault(row["from"], []).append(row["to"])
    from kge.compiler.catalogue_publish import LABELS, apply_card

    cards, portal_suffix, published = _published(profile)
    visible = [value for value in held.get("products", []) if value["unit"] in readable_units]
    visible_ids = {value["id"] for value in visible}
    products = []
    for original in visible:
        card = cards.get(original["id"])
        value = apply_card(dict(original), card)
        state = ""
        if published and card and not card.get("portal_only"):
            state = LABELS["portal_ahead"] if _descriptive(original) != card.get("descriptive", {}) else LABELS["in_sync"]
        fields = []
        for metadata in value["fields"]:
            column_id = f"{value['id']}.{metadata['name']}"
            column = columns[column_id]
            fields.append(CatalogueField(**{**metadata, "terms": sorted(set(terms.get(column_id, [])) | set(metadata.get("terms", []))),
                                           "data_type": column["data_type"], "description": metadata.get("description", column["description"])}))
        content = {key: item for key, item in value.items() if key not in {"unit", "fields"}}
        if figures is not None:
            content.update(figures.get(value["id"], {"records": None, "storage": ""}))
        for key, default in {"last_refresh": "", "ingested_at": "", "records": 0, "path": "", "data_format": "",
                             "storage": "", "last_dq_run": ""}.items():
            content.setdefault(key, default)
        content["lineage"] = {**value["lineage"],
                              "upstream": [item for item in value["lineage"]["upstream"] if item in visible_ids],
                              "downstream": [item for item in value["lineage"]["downstream"] if item in visible_ids]}
        content.setdefault("name", value["table"].replace("_", " ").title())
        extra = {"catalogue_state": state} if state else {}
        products.append(CatalogueProduct(**content, unit_id=value["unit"], fields=fields, **extra))
    if portal and published:
        for card in published.get("products", []):
            if not card.get("portal_only") or card.get("unit") not in readable_units or card["id"] in visible_ids:
                continue
            products.append(CatalogueProduct(**{key: item for key, item in _portal_product(card).items() if key != "unit"},
                                             unit_id=card["unit"]))
    return CatalogueResponse(contract_version="1", release=release_id(profile, figures, portal_suffix), has_catalogue=True, products=products)


def _descriptive(product: dict) -> dict:
    from kge.compiler.catalogue import DESCRIPTIVE
    return {key: product[key] for key in DESCRIPTIVE if key in product}


def identity(profile: Profile, figures: dict | None = None) -> CatalogueIdentity:
    artifact = profile.current() if profile.pinned is None else profile.artifacts / profile.client / profile.pinned
    if artifact is None:
        return CatalogueIdentity(has_catalogue=False)
    _, portal_suffix, _ = _published(profile)
    return CatalogueIdentity(release=release_id(profile, figures, portal_suffix), has_catalogue=(artifact / "catalogue.yaml").is_file())


__all__ = ["CatalogueField", "CatalogueIdentity", "CatalogueProduct", "CatalogueResponse", "Counts", "Lineage",
           "QualityRule", "get", "identity"]
