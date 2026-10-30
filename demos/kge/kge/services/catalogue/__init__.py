"""Build the discovery catalogue from a released KGE artifact."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from kge.pipeline.profile import Profile, ProfileError


class QualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    rule: str
    rule_type: str
    fail_percent: float = Field(ge=0, le=100)


class CatalogueField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    business_name: str
    data_type: str
    description: str
    quality: QualityRule
    terms: list[str] = Field(default_factory=list)


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


class CatalogueProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    table: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str
    product_owner: str
    business_owner: str
    steward: str = ""
    badges: list[str]
    quality_score: float = Field(ge=0, le=100)
    last_refresh: str = ""
    ingested_at: str = ""
    counts: Counts
    rating: float = Field(ge=0, le=5)
    domain: str
    community: str
    application: str
    product_type: str
    records: int = Field(default=0, ge=0)
    path: str = ""
    data_format: str = ""
    storage: str = ""
    last_dq_run: str = ""
    lineage: Lineage
    fields: list[CatalogueField]


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


def release_id(profile: Profile) -> str:
    """Include the projection revision so an existing portal refreshes when catalogue badges change."""
    held = profile.identity()
    if not held or not held.get("hash"):
        raise ProfileError("no released catalogue identity is available")
    return "|".join([*(str(held.get(key)) for key in ("hash", "graph", "chunks") if held.get(key)), "catalogue-badges-v3"])


def get(profile: Profile, readable_units: list[str]) -> CatalogueResponse:
    artifact = profile.current() if profile.pinned is None else profile.artifacts / profile.client / profile.pinned
    if artifact is None:
        raise ProfileError("no released catalogue artifact is available")
    try:
        held = yaml.safe_load((artifact / "catalogue.yaml").read_text())
    except FileNotFoundError:
        return CatalogueResponse(contract_version="1", release=release_id(profile), has_catalogue=False, products=[])
    columns = {row["id"]: row for row in rows(artifact / "data/nodes/Col.csv")}
    terms: dict[str, list[str]] = {}
    for row in rows(artifact / "data/edges/MAPS_TO__Col__Term.csv"):
        terms.setdefault(row["from"], []).append(row["to"])
    visible = [value for value in held.get("products", []) if value["unit"] in readable_units]
    visible_ids = {value["id"] for value in visible}
    products = []
    for value in visible:
        fields = []
        for metadata in value["fields"]:
            column_id = f"{value['id']}.{metadata['name']}"
            column = columns[column_id]
            fields.append(CatalogueField(**{**metadata, "terms": sorted(set(terms.get(column_id, [])) | set(metadata.get("terms", []))),
                                           "data_type": column["data_type"], "description": metadata.get("description", column["description"])}))
        content = {key: item for key, item in value.items() if key not in {"unit", "fields"}}
        for key, default in {"last_refresh": "", "ingested_at": "", "records": 0, "path": "", "data_format": "",
                             "storage": "", "last_dq_run": ""}.items():
            content.setdefault(key, default)
        content["lineage"] = {**value["lineage"],
                              "upstream": [item for item in value["lineage"]["upstream"] if item in visible_ids],
                              "downstream": [item for item in value["lineage"]["downstream"] if item in visible_ids]}
        content.setdefault("name", value["table"].replace("_", " ").title())
        products.append(CatalogueProduct(**content, unit_id=value["unit"],
                                         fields=fields))
    return CatalogueResponse(contract_version="1", release=release_id(profile), has_catalogue=True, products=products)


def identity(profile: Profile) -> CatalogueIdentity:
    artifact = profile.current() if profile.pinned is None else profile.artifacts / profile.client / profile.pinned
    if artifact is None:
        return CatalogueIdentity(has_catalogue=False)
    return CatalogueIdentity(release=release_id(profile), has_catalogue=(artifact / "catalogue.yaml").is_file())


__all__ = ["CatalogueField", "CatalogueIdentity", "CatalogueProduct", "CatalogueResponse", "Counts", "Lineage",
           "QualityRule", "get", "identity"]
