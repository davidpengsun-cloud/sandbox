"""The discovery catalogue derived from a client pack and its optional enrichment."""
from __future__ import annotations

import yaml

from kge.compiler.pack import Finding, Pack, dated
from kge.adapters.warehouse import catalogue_format

FILE = "catalogue.yaml"
YAML = {"sort_keys": False, "default_flow_style": False, "allow_unicode": True, "width": 4096, "indent": 2, "line_break": "\n"}


def read(pack: Pack) -> tuple[dict | None, list[Finding]]:
    """Read and validate the optional descriptive catalogue enrichment."""
    path = pack.folder / FILE
    if not path.is_file():
        return None, []
    try:
        value = dated(yaml.safe_load(path.read_text()))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, [Finding("C01", FILE, f"cannot be read ({type(exc).__name__})")]
    return value, validate(value, pack)


def validate(value: dict, pack: Pack) -> list[Finding]:
    """Validate a candidate enrichment before it becomes onboarding pack input."""
    findings: list[Finding] = []
    if not isinstance(value, dict) or value.get("format") != 1 or not isinstance(value.get("units"), list) or not isinstance(value.get("products"), list):
        return [Finding("C01", FILE, "must hold format: 1, a units list and a products list")]
    declared_units = [item.get("id") for item in pack.data.get("sources", [])]
    catalogue_units = value["units"]
    if len(catalogue_units) != len(set(catalogue_units)) or not all(isinstance(unit, str) and unit in declared_units for unit in catalogue_units):
        return [Finding("C01", f"{FILE}:units", "must name distinct units declared by the pack")]
    expected = {(unit, table["name"]): table for unit, source in pack.lock["sources"].items()
                if unit in catalogue_units for table in source["tables"]}
    seen: set[tuple[str, str]] = set()
    lineage_references: list[tuple[str, str]] = []
    principals = set(pack.principal_roles)
    allowed = {"id", "unit", "table", "name", "description", "product_owner", "business_owner", "steward", "badges", "quality_score",
               "counts", "rating", "domain", "community", "application", "product_type", "lineage", "fields"}
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
        if key not in expected:
            findings.append(Finding("C01", f"{FILE}:{at}", f"names unknown table {key[0]}.{key[1]}"))
            continue
        if product.get("id") != f"{key[0]}.{key[1]}":
            findings.append(Finding("C01", f"{FILE}:{at}.id", f"must be {key[0]}.{key[1]}"))
        if key in seen:
            findings.append(Finding("C01", f"{FILE}:{at}", f"duplicates {key[0]}.{key[1]}"))
        seen.add(key)
        for field in ("name", "description", "domain", "community", "application"):
            if field in product and (not isinstance(product[field], str) or not product[field].strip()):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", "must be a non-empty string"))
        if "product_type" in product and product["product_type"] not in {"TABLE", "VIEW"}:
            findings.append(Finding("C01", f"{FILE}:{at}.product_type", "must be TABLE or VIEW"))
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
        if "steward" in product and product["steward"] not in principals:
            findings.append(Finding("C01", f"{FILE}:{at}.steward", "must name a principal from the deployment identities"))
        for field, low, high in (("quality_score", 0, 100), ("rating", 0, 5)):
            number = product.get(field)
            if field in product and (isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high):
                findings.append(Finding("C01", f"{FILE}:{at}.{field}", f"must be a number from {low} to {high}"))
        counts = product.get("counts")
        if counts is not None and (not isinstance(counts, dict) or set(counts) != {"subscribe", "access", "views"} or any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts.values())):
            unknown = sorted(set(counts) - {"subscribe", "access", "views"}) if isinstance(counts, dict) else []
            detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give non-negative whole subscribe, access and views counts"
            findings.append(Finding("C01", f"{FILE}:{at}.counts", detail))
        fields = product.get("fields")
        expected_fields = {column["name"] for column in expected[key]["columns"]}
        if fields is not None and not isinstance(fields, list):
            findings.append(Finding("C01", f"{FILE}:{at}.fields", "must be a list"))
        elif fields is not None:
            names = [field.get("name") for field in fields if isinstance(field, dict)]
            if not set(names) <= expected_fields or len(names) != len(set(names)):
                unknown_fields = sorted(set(names) - expected_fields)
                detail = "; ".join(filter(None, [f"unknown {', '.join(unknown_fields)}" if unknown_fields else "",
                                                   "a column is repeated" if len(names) != len(set(names)) else ""]))
                findings.append(Finding("C01", f"{FILE}:{at}.fields", detail))
            for field_index, field in enumerate(fields):
                if not isinstance(field, dict) or "name" not in field or not set(field) <= {"name", "business_name", "description", "terms", "quality"}:
                    unknown = sorted(set(field) - {"name", "business_name", "description", "terms", "quality"}) if isinstance(field, dict) else []
                    detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give name, business_name, terms and quality"
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}]", detail))
                    continue
                if "description" in field and (not isinstance(field["description"], str) or not field["description"].strip()):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].description", "must be a non-empty string"))
                if "business_name" in field and (not isinstance(field["business_name"], str) or not field["business_name"].strip()):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].business_name", "must be a non-empty string"))
                if "terms" in field and (not isinstance(field["terms"], list) or not all(isinstance(term, str) and term.strip() for term in field["terms"])):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].terms", "must be a list of non-empty strings"))
                quality = field.get("quality")
                if quality is not None and (not isinstance(quality, dict) or set(quality) != {"dimension", "rule", "rule_type", "fail_percent"}):
                    unknown = sorted(set(quality) - {"dimension", "rule", "rule_type", "fail_percent"}) if isinstance(quality, dict) else []
                    detail = f"has unknown keys {', '.join(unknown)}" if unknown else "must give dimension, rule, rule_type and fail_percent"
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].quality", detail))
                elif quality is not None and (isinstance(quality["fail_percent"], bool) or not isinstance(quality["fail_percent"], (int, float)) or not 0 <= quality["fail_percent"] <= 100):
                    findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].quality.fail_percent", "must be a number from 0 to 100"))
                elif quality is not None:
                    for name in ("dimension", "rule", "rule_type"):
                        if not isinstance(quality[name], str) or not quality[name].strip():
                            findings.append(Finding("C01", f"{FILE}:{at}.fields[{field_index}].quality.{name}", "must be a non-empty string"))
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
    mappings: dict[str, list[str]] = {}
    for mapping in pack.data.get("mappings", []):
        if mapping.get("certified"):
            mappings.setdefault(mapping["column"], []).append(mapping["term"])
    lineage = {item["table"]: item.get("derived_from", []) for item in pack.data.get("lineage", [])}
    downstream: dict[str, list[str]] = {}
    for target, inputs in lineage.items():
        for source in inputs:
            downstream.setdefault(source, []).append(target)
    principals = sorted(pack.principal_roles)
    stewards = [name for name in principals if "certification" in pack.principal_roles[name].get("processes", [])]
    owners = [name for name in principals if "querying" in pack.principal_roles[name].get("processes", []) and name not in stewards]
    default_steward = stewards[0] if stewards else principals[0]
    metadata_stewards = [name for name in stewards if "steward" in pack.principal_roles[name].get("id", name).lower()]
    result = {"format": 2, "landed_tables": {"rule": "landed-structured-tables", "version": 1},
              "units": sorted(pack.lock["sources"]), "products": []}
    as_of = str(pack.lock.get("as_of") or "")
    for unit, source in sorted(pack.lock["sources"].items()):
      for table in source["tables"]:
        product_id = f"{unit}.{table['name']}"
        enrichment = enrichments.get(product_id, {})
        readable_owners = [name for name in owners if pack.principal_roles[name].get("reads") == "all" or unit in pack.principal_roles[name].get("reads", [])]
        owner = readable_owners[0] if readable_owners else owners[0] if owners else default_steward
        fields = []
        enriched_fields = {item["name"]: item for item in enrichment.get("fields", [])}
        for column in table["columns"]:
            column_id = f"{product_id}.{column['name']}"
            extra = enriched_fields.get(column["name"], {})
            terms = sorted(set(mappings.get(column_id, [])) | set(extra.get("terms", [])))
            quality = extra.get("quality") or {"dimension": "Governance", "rule": "No field quality rule is recorded", "rule_type": "Not_Assessed", "fail_percent": 0}
            fields.append({"name": column["name"], "business_name": extra.get("business_name") or column["name"].replace("_", " ").title(),
                           "terms": terms, "quality": quality, **({"description": extra["description"]} if "description" in extra else {})})
        storage = table.get("storage")
        mapped_columns = sum(bool(mappings.get(f"{product_id}.{column['name']}")) for column in table["columns"])
        quality_score = round(100 * mapped_columns / len(table["columns"]), 1) if table["columns"] else 0
        derived = {"id": product_id, "unit": unit, "table": table["name"],
                   "description": table.get("description") or f"The governed {table['name'].replace('_', ' ')} table from {sources.get(unit, {}).get('label', unit)}.",
                   "product_owner": default_steward, "business_owner": owner,
                   "badges": ["GOVERNED"], "quality_score": quality_score,
                   "counts": {"subscribe": 0, "access": 0, "views": 0}, "rating": 0,
                   "domain": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(),
                   "community": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(),
                   "application": sources.get(unit, {}).get("label") or unit.replace("_", " ").title(), "product_type": "TABLE",
                   "lineage": {"origin_paths": [product_id], "destination_paths": sorted(downstream.get(product_id, [])),
                               "upstream": sorted(lineage.get(product_id, [])), "downstream": sorted(downstream.get(product_id, []))},
                   "fields": fields, "records": int(table.get("rows", 0)), "path": product_id, "data_format": catalogue_format(table.get("kind", "TABLE")),
                   "storage": format_bytes(storage) if storage is not None else "", "last_dq_run": as_of,
                   "last_refresh": as_of, "ingested_at": as_of}
        for direction, references in enrichment.get("lineage", {}).items():
            derived["lineage"][direction] = sorted(set(derived["lineage"][direction]) | set(references))
        unit_stewards = [name for name in metadata_stewards if pack.principal_roles[name].get("reads") == "all" or unit in pack.principal_roles[name].get("reads", [])]
        if unit_stewards:
            derived["steward"] = enrichment.get("steward", unit_stewards[0])
        for key in ("name", "product_owner", "business_owner", "product_type", "description", "badges", "quality_score", "counts", "rating", "domain", "community", "application"):
            if key in enrichment:
                derived[key] = enrichment[key]
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
