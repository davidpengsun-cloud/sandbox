"""The discovery catalogue derived from a client pack and its optional enrichment."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from kge.compiler.inputs import INPUTS, declared_findings, read_declared
from kge.compiler.pack import Finding, Pack, dated
from kge.adapters.warehouse import catalogue_format

FILE = "catalogue.yaml"
YAML = {"sort_keys": False, "default_flow_style": False, "allow_unicode": True, "width": 4096, "indent": 2, "line_break": "\n"}


VOCABULARIES = {"product_type": ("Table", "View", "API/MCP", "Documents"),
                "asset_type": ("Structured", "Unstructured"),
                "usage_purpose": ("Analytical", "Operational"),
                "classification": ("Public", "Internal", "Confidential", "Highly Confidential", "Restricted")}
ALIASES = {"api": "API/MCP", "mcp": "API/MCP", "api_mcp": "API/MCP", "document": "Documents"}
PRODUCT_TEXT = ("project", "dataset_type", "refresh_cadence")
OWNER_EMAILS = ("product_owner_email", "business_owner_email")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
FIELD_KEYS = {"name", "business_name", "description", "terms", "quality", "classification", "pii"}
QUALITY_REQUIRED = {"dimension", "rule", "rule_type"}
QUALITY_OPTIONAL = {"fail_percent", "description"}
PRODUCT_OVERRIDES = ("path", "data_format", "APM_ID", "schema")
DESCRIPTIVE = ("name", "description", "product_owner", "business_owner", "product_owner_email", "business_owner_email",
               "steward", "badges", "domain", "community", "application", "product_type", "asset_type", "usage_purpose",
               "dataset_type", "project", "refresh_cadence", *PRODUCT_OVERRIDES)
GOVERNED_FACTS = ("lineage", "fields", "quality_score")
PORTAL_TYPES = frozenset({"Documents", "API/MCP"})


def vocabulary_value(field: str, value) -> str | None:
    """The canonical spelling of a vocabulary value, in any case, with older spellings such as TABLE; None if it is not one."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    for allowed in VOCABULARIES[field]:
        if key == allowed.lower():
            return allowed
    alias = ALIASES.get(key) if field == "product_type" else None
    return alias


def read(pack: Pack) -> tuple[dict | None, list[Finding]]:
    """Read and validate the optional descriptive catalogue enrichment."""
    name = FILE
    manifest = pack.data.get("manifest")
    if isinstance(manifest, dict) and INPUTS in manifest:
        findings = declared_findings(pack.folder)
        if findings:
            return None, findings
        declared = read_declared(pack.folder)
        if declared is None or declared.catalogue is None:
            return None, []
        name = declared.catalogue
    path = pack.folder / name
    if not path.is_file():
        return None, []
    try:
        value = dated(yaml.safe_load(path.read_text()))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [Finding("C01", name, f"cannot be read ({type(exc).__name__})")]
    return value, [Finding(f.rule, name + f.path[len(FILE):], f.message) for f in validate(value, pack)]


def quality_findings(rule, at: str) -> list[Finding]:
    """A field quality rule: dimension, rule and rule_type are required; a blank or absent fail_percent is shown blank."""
    if not isinstance(rule, dict) or not QUALITY_REQUIRED <= set(rule) or set(rule) - QUALITY_REQUIRED - QUALITY_OPTIONAL:
        unknown = sorted(set(rule) - QUALITY_REQUIRED - QUALITY_OPTIONAL) if isinstance(rule, dict) else []
        return [Finding("C01", at, f"has unknown keys {', '.join(unknown)}" if unknown else "must give dimension, rule and rule_type")]
    findings = []
    fail = rule.get("fail_percent")
    if fail is not None and (isinstance(fail, bool) or not isinstance(fail, (int, float)) or not 0 <= fail <= 100):
        findings.append(Finding("C01", f"{at}.fail_percent", "must be a number from 0 to 100 or blank"))
    if "description" in rule and not isinstance(rule["description"], str):
        findings.append(Finding("C01", f"{at}.description", "must be a string"))
    for name in sorted(QUALITY_REQUIRED):
        if not isinstance(rule[name], str) or not rule[name].strip():
            findings.append(Finding("C01", f"{at}.{name}", "must be a non-empty string"))
    return findings


def fixture_docs(pack: Pack) -> Path | None:
    """The fixture's docs folder, when the pack has one."""
    from kge.compiler.inputs import read_declared

    declared = read_declared(pack.folder)
    fixture = pack.folder / (declared.fixture if declared and declared.fixture else "fixture")
    documents = fixture / "docs"
    return documents if documents.is_dir() else None


def known_documents(pack: Pack) -> tuple[set[str], dict[str, list[str]]]:
    """Document ids, and the ids on each shelf, from the fixture."""
    docs = fixture_docs(pack)
    found: set[str] = set()
    shelves: dict[str, list[str]] = {}
    if docs is None:
        return found, shelves
    for shelf in sorted(path for path in docs.iterdir() if path.is_dir()):
        names = sorted(path.stem for path in shelf.iterdir() if path.is_file() and path.suffix != ".yaml" and not path.name.endswith(".yaml"))
        shelves[shelf.name] = names
        found.update(names)
    return found, shelves


def document_title(pack: Pack, doc_id: str) -> str:
    """The fixture document's title, or its id when the file has none."""
    docs = fixture_docs(pack)
    if docs is None:
        return doc_id
    for path in docs.glob(f"*/{doc_id}.*"):
        if path.suffix == ".yaml":
            continue
        text = path.read_text(errors="replace")
        if text.startswith("---"):
            for line in text.split("---", 2)[1].splitlines():
                if line.startswith("title:"):
                    return line.split(":", 1)[1].strip().strip("'\"") or doc_id
        return doc_id
    return doc_id


def document_findings(held, documents: tuple[set[str], dict[str, list[str]]], at: str, *, checked: bool) -> list[Finding]:
    """A document set names fixture documents, by id or by shelf. A pack copy with no fixture is not checked."""
    if held is None or not checked:
        return []
    ids, shelves = documents
    if isinstance(held, dict) and set(held) == {"shelf"} and isinstance(held.get("shelf"), str):
        if held["shelf"] not in shelves or not shelves[held["shelf"]]:
            return [Finding("C01", f"{at}.documents.shelf", f"names unknown shelf {held['shelf']}")]
        return []
    if isinstance(held, list) and held and all(isinstance(item, str) and item.strip() for item in held):
        missing = [item for item in held if item not in ids]
        if missing:
            return [Finding("C01", f"{at}.documents", f"names unknown document {missing[0]}")]
        return []
    return [Finding("C01", f"{at}.documents", "must list document ids, or name a shelf")]


def governed_fact(entry: dict, held: dict | None) -> str | None:
    """The governed fact a portal entry tries to set, or None when it only describes the card.

    A value already in the graph release is not a new fact: lineage and column terms the release
    already holds may stay in the file while a path changes.
    """
    for key in GOVERNED_FACTS:
        if key not in entry:
            continue
        if held is None or not _contained(entry[key] if key != "fields" else _held_fact({"fields": entry[key]}, key),
                                          held.get(key) if key != "fields" else _held_fact(held, key)):
            return key
    return None


def _contained(left, right) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return all(key in right and _contained(value, right[key]) for key, value in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return all(any(_contained(item, held) if isinstance(item, (dict, list)) else _canon(item) == _canon(held)
                       for held in right) for item in left)
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return left == right


def _held_fact(held: dict, key: str):
    value = held.get(key)
    if key != "fields" or not isinstance(value, list):
        return value
    return [{name: field.get(name) for name in ("name", "business_name", "description", "terms", "quality", "classification", "pii") if name in field}
            for field in value if isinstance(field, dict)]


def _canon(value) -> str:
    import json
    return json.dumps(value, sort_keys=True, default=str)


def validate(value: dict, pack: Pack) -> list[Finding]:
    """Validate a candidate enrichment before it becomes onboarding pack input."""
    findings: list[Finding] = []
    if not isinstance(value, dict) or value.get("format") != 1 or not isinstance(value.get("units"), list) or not isinstance(value.get("products"), list):
        return [Finding("C01", FILE, "must hold format: 1, a units list and a products list")]
    declared_units = [item.get("id") for item in pack.data.get("sources", [])]
    catalogue_units = value["units"]
    if len(catalogue_units) != len(set(catalogue_units)) or not all(isinstance(unit, str) and unit in declared_units for unit in catalogue_units):
        return [Finding("C01", f"{FILE}:units", "must name distinct units declared by the pack")]
    source_metadata = value.get("sources", [])
    if not isinstance(source_metadata, list):
        findings.append(Finding("C01", f"{FILE}:sources", "must be a list"))
    else:
        seen_sources = set()
        for index, source in enumerate(source_metadata):
            at = f"{FILE}:sources[{index}]"
            if not isinstance(source, dict) or set(source) - {"id", "APM_ID"} or not isinstance(source.get("id"), str) or source["id"] not in declared_units:
                findings.append(Finding("C01", at, "must name a declared source id and optional APM_ID"))
                continue
            if source["id"] in seen_sources:
                findings.append(Finding("C01", at, "must name a distinct source"))
            seen_sources.add(source["id"])
            if "APM_ID" in source and not isinstance(source["APM_ID"], str):
                findings.append(Finding("C01", at + ".APM_ID", "must be a string"))
    expected = {(unit, table["name"]): table for unit, source in pack.lock["sources"].items()
                if unit in catalogue_units for table in source["tables"]}
    seen: set[tuple[str, str]] = set()
    lineage_references: list[tuple[str, str]] = []
    roles = {r["id"]: r for r in pack.data.get("access", {}).get("roles", [])}
    stewards = {key for key, role in roles.items() if "certification" in role.get("processes", [])}
    allowed = {"id", "unit", "table", "name", "description", "product_owner", "business_owner", "steward", "badges", "quality_score",
               "counts", "rating", "domain", "community", "application", "product_type", "asset_type", "usage_purpose", "dataset_type", "lineage", "fields",
               "project", "product_owner_email", "business_owner_email", "refresh_cadence", "documents", *PRODUCT_OVERRIDES}
    documents = known_documents(pack)
    documents_checked = fixture_docs(pack) is not None
    for index, product in enumerate(value["products"]):
        at = f"products[{index}]"
        if not isinstance(product, dict):
            findings.append(Finding("C01", f"{FILE}:{at}", "must be a mapping"))
            continue
        missing = sorted({"id", "unit", "table"} - set(product))
        if missing:
            findings.append(Finding("C01", f"{FILE}:{at}", f"is missing {', '.join(missing)}"))
            continue
        unknown = sorted(set(product) - allowed)
        if unknown:
            findings.append(Finding("C01", f"{FILE}:{at}", f"has unknown keys {', '.join(unknown)}"))
            continue
        key = (product.get("unit"), product.get("table"))
        portal_only = key not in expected
        if portal_only:
            kind = vocabulary_value("product_type", product.get("product_type"))
            if kind not in PORTAL_TYPES:
                findings.append(Finding("C01", f"{FILE}:{at}", f"names unknown table {key[0]}.{key[1]}"))
                continue
            governed = next((name for name in GOVERNED_FACTS if name in product), None)
            if governed:
                findings.append(Finding("C01", f"{FILE}:{at}.{governed}", f"sets governed fact {governed}"))
                continue
            findings.extend(document_findings(product.get("documents"), documents, f"{FILE}:{at}", checked=documents_checked))
        elif "documents" in product:
            findings.append(Finding("C01", f"{FILE}:{at}.documents", "is only for a card that is not in the graph"))
        if product.get("id") != f"{key[0]}.{key[1]}":
            findings.append(Finding("C01", f"{FILE}:{at}.id", f"must be {key[0]}.{key[1]}"))
        if key in seen:
            findings.append(Finding("C01", f"{FILE}:{at}", f"duplicates {key[0]}.{key[1]}"))
        seen.add(key)
        for field in ("name", "description", "domain", "community", "application"):
            if field in product and (not isinstance(product[field], str) or not product[field].strip()):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", "must be a non-empty string"))
        for field, vocabulary in VOCABULARIES.items():
            if field in product and vocabulary_value(field, product[field]) is None:
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", f"must be one of {', '.join(vocabulary)}"))
        for field in PRODUCT_TEXT:
            if field in product and not isinstance(product[field], str):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", "must be a string"))
        for field in PRODUCT_OVERRIDES:
            if field in product and (not isinstance(product[field], str) or not product[field].strip()):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", "must be a non-empty string"))
        for field in OWNER_EMAILS:
            if field in product and (not isinstance(product[field], str) or (product[field].strip() and not EMAIL.fullmatch(product[field].strip()))):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", "must be an email address or blank"))
        badges = product.get("badges", [])
        if "badges" in product and (not isinstance(badges, list) or not all(isinstance(item, str) and item.strip() for item in badges)):
            findings.append(Finding("C01", f"{FILE}:{at}.badges", "must be a list of non-empty strings"))
        lineage = product.get("lineage")
        lineage_fields = {"origin_paths", "destination_paths", "upstream", "downstream"}
        if lineage is not None and (not isinstance(lineage, dict) or set(lineage) != lineage_fields):
            unknown = sorted(set(lineage) - lineage_fields) if isinstance(lineage, dict) else []
            detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give origin_paths, destination_paths, upstream and downstream"
            findings.append(Finding("C01", f"{FILE}:{at}.lineage", detail))
        elif lineage is not None:
            for field in sorted(lineage_fields):
                if not isinstance(lineage[field], list) or not all(isinstance(item, str) and item.strip() for item in lineage[field]):
                    findings.append(Finding("C01", f"{FILE}:{at}.lineage.{field}", "must be a list of non-empty strings"))
                elif field in {"upstream", "downstream"}:
                    lineage_references.extend((f"{at}.lineage.{field}", item) for item in lineage[field])
        for owner in ("product_owner", "business_owner"):
            if owner in product and (not isinstance(product[owner], str) or not product[owner].strip()):
                findings.append(Finding("C01", f"{FILE}:{at}.{owner}", "must give the business-stated owner"))
        if "steward" in product and product["steward"] not in stewards:
            findings.append(Finding("C01", f"{FILE}:{at}.steward", "must name a role of the pack that performs certification"))
        for field, low, high in (("quality_score", 0, 100), ("rating", 0, 5)):
            number = product.get(field)
            if field in product and (isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", f"must be a number from {low} to {high}"))
        counts = product.get("counts")
        if counts is not None and (not isinstance(counts, dict) or set(counts) != {"subscribe", "access", "views"} or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts.values())):
            unknown = sorted(set(counts) - {"subscribe", "access", "views"}) if isinstance(counts, dict) else []
            detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give non-negative whole subscribe, access and views counts"
            findings.append(Finding("C01", f"{FILE}:{at}.counts", detail))
        if portal_only:
            continue
        fields = product.get("fields")
        expected_fields = {column["name"] for column in expected[key]["columns"]}
        if fields is not None and not isinstance(fields, list):
            findings.append(Finding("C01", f"{FILE}:{at}.fields", "must be a list"))
        elif fields is not None:
            names = [field.get("name") for field in fields if isinstance(field, dict)]
            for field_index, field in enumerate(fields):
                if isinstance(field, dict) and isinstance(field.get("name"), str) and field["name"] not in expected_fields:
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].name",
                                            f"{key[0]}.{key[1]}.{field['name']} is not a column in pack.lock"))
            if len(names) != len(set(names)):
                findings.append(Finding("C01", f"{FILE}:{at}.fields", "a column is repeated"))
            for field_index, field in enumerate(fields):
                if not isinstance(field, dict) or "name" not in field or not set(field) <= FIELD_KEYS:
                    unknown = sorted(set(field) - FIELD_KEYS) if isinstance(field, dict) else []
                    detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give name, business_name, terms and quality"
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}]", detail))
                    continue
                if "classification" in field and vocabulary_value("classification", field["classification"]) is None:
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].classification",
                                            f"must be one of {', '.join(VOCABULARIES['classification'])}"))
                if "description" in field and (not isinstance(field["description"], str) or not field["description"].strip()):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].description", "must be a non-empty string"))
                if "business_name" in field and (not isinstance(field["business_name"], str) or not field["business_name"].strip()):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].business_name", "must be a non-empty string"))
                if "terms" in field and (not isinstance(field["terms"], list) or not all(isinstance(term, str) and term.strip() for term in field["terms"])):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].terms", "must be a list of non-empty strings"))
                if "pii" in field and not isinstance(field["pii"], bool):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].pii", "must be a boolean"))
                quality = field.get("quality")
                rules = quality if isinstance(quality, list) else [quality] if quality is not None else []
                if isinstance(quality, list) and not quality:
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].quality", "must give at least one rule"))
                for rule_index, rule in enumerate(rules):
                    rule_at = f"{FILE}:{at}.fields[{field_index}].quality" + (f"[{rule_index}]" if isinstance(quality, list) else "")
                    findings.extend(quality_findings(rule, rule_at))
    product_ids = {f"{unit}.{table['name']}" for unit, source in pack.lock["sources"].items() for table in source["tables"]}
    for at, reference in lineage_references:
        if reference not in product_ids:
            findings.append(Finding("C01", f"{FILE}:{at}", f"names unknown product {reference}"))
    return findings


def derive(value: dict | None, pack: Pack) -> dict:
    """Build every product from governed inputs, then merge optional descriptive enrichment.

    The landed-tables record revision changes the artifact hash so onboarding writes the record on upgrade.
    The load records membership from its landed release beside the graph, not from descriptive enrichment.
    """
    enrichments = {item["id"]: item for item in (value or {}).get("products", [])}
    sources = {item["id"]: item for item in pack.data.get("sources", [])}
    source_metadata = {item["id"]: item for item in (value or {}).get("sources", [])}
    mappings: dict[str, list[str]] = {}
    for mapping in pack.data.get("mappings", []):
        if mapping.get("certified"):
            mappings.setdefault(mapping["column"], []).append(mapping["term"])
    lineage = {item["table"]: item.get("derived_from", []) for item in pack.data.get("lineage", [])}
    downstream: dict[str, list[str]] = {}
    for target, inputs in lineage.items():
        for source in inputs:
            downstream.setdefault(source, []).append(target)
    roles = {r["id"]: r for r in pack.data.get("access", {}).get("roles", [])}
    stewards = [key for key, role in roles.items() if "certification" in role.get("processes", [])]
    owners = [key for key, role in roles.items() if "querying" in role.get("processes", [])]
    default_steward = roles[stewards[0]]["label"] if stewards else ""
    result = {"format": 2, "landed_tables": {"rule": "landed-structured-tables", "version": 1},
              "units": sorted(pack.lock["sources"]), "products": []}
    as_of = str(pack.lock.get("as_of") or "")
    for unit, source in sorted(pack.lock["sources"].items()):
      for table in source["tables"]:
        product_id = f"{unit}.{table['name']}"
        enrichment = enrichments.get(product_id, {})
        readable_owners = [name for name in owners if roles[name].get("reads") == "all" or unit in roles[name].get("reads", [])]
        owner = roles[(readable_owners or owners)[0]]["label"] if owners else default_steward
        fields = []
        enriched_fields = {item["name"]: item for item in enrichment.get("fields", [])}
        for column in table["columns"]:
            column_id = f"{product_id}.{column['name']}"
            extra = enriched_fields.get(column["name"], {})
            terms = sorted(set(mappings.get(column_id, [])) | set(extra.get("terms", [])))
            quality = extra.get("quality") or {"dimension": "Governance", "rule": "No field quality rule is recorded", "rule_type": "Not_Assessed", "fail_percent": 0}
            fields.append({"name": column["name"], "business_name": extra.get("business_name") or column["name"].replace("_", " ").title(),
                           "terms": terms, "quality": quality, **({"description": extra["description"]} if "description" in extra else {}),
                           **({"pii": extra["pii"]} if "pii" in extra else {}),
                           **({"classification": vocabulary_value("classification", extra["classification"])} if "classification" in extra else {})})
        mapped_columns = sum(bool(mappings.get(f"{product_id}.{column['name']}")) for column in table["columns"])
        quality_score = round(100 * mapped_columns / len(table["columns"]), 1) if table["columns"] else 0
        derived = {"id": product_id, "unit": unit, "table": table["name"],
                   "description": table.get("description") or f"The governed {table['name'].replace('_', ' ')} table from {sources.get(unit, {}).get('label', unit)}.",
                   "product_owner": default_steward, "business_owner": owner,
                   "badges": ["GOVERNED"], "quality_score": quality_score,
                   "counts": {"subscribe": 0, "access": 0, "views": 0}, "rating": 0,
                   "domain": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(),
                   "community": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(),
                   "application": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(), "product_type": "Table",
                   "asset_type": "", "usage_purpose": "", "dataset_type": "",
                   "APM_ID": source_metadata.get(unit, {}).get("APM_ID", ""),
                   "lineage": {"origin_paths": [product_id], "destination_paths": sorted(downstream.get(product_id, [])),
                               "upstream": sorted(lineage.get(product_id, [])), "downstream": sorted(downstream.get(product_id, []))},
                   "fields": fields, "path": product_id, "data_format": catalogue_format(table.get("kind", "TABLE")),
                   "last_dq_run": as_of,
                   "last_refresh": as_of, "ingested_at": as_of}
        for direction, references in enrichment.get("lineage", {}).items():
            derived["lineage"][direction] = sorted(set(derived["lineage"][direction]) | set(references))
        unit_stewards = [name for name in stewards if roles[name].get("reads") == "all" or unit in roles[name].get("reads", [])]
        steward = enrichment.get("steward") or (unit_stewards[0] if unit_stewards else None)
        if steward:
            derived["steward"] = roles[steward]["label"]
        for key in ("name", "product_owner", "business_owner", "product_type", "asset_type", "usage_purpose", "dataset_type", "description", "badges", "quality_score", "counts", "rating", "domain", "community", "application",
                    "project", "refresh_cadence", *PRODUCT_OVERRIDES, *OWNER_EMAILS):
            if key in enrichment:
                derived[key] = vocabulary_value(key, enrichment[key]) if key in VOCABULARIES else enrichment[key].strip() if key in OWNER_EMAILS else enrichment[key]
        result["products"].append(derived)
    return result


def format_bytes(value: int) -> str:
    """A compact binary size for the discovery page."""
    amount = float(value)
    for unit in ("bytes", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "bytes" else f"{int(amount)} bytes"
        amount /= 1024
    raise AssertionError("unreachable")


def bytes_of(value: dict) -> bytes:
    """Canonical YAML carried by the compiled artifact."""
    return yaml.safe_dump(value, **YAML).encode()
