"""Portal metadata onboarding: land an input, accept it, certify it for the next checked release.

This local operator command has the same self-asserted --as boundary as the
onboarding CLI. It writes only pack input and an audit record, never an artifact,
graph, release pointer or sign-off. Certification still needs normal checks,
steward sign-off and release before the service can show the changed metadata.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kge import packstore
from kge.adapters.objstore import StoreError
from kge.compiler import pack as packs
from kge.compiler import catalogue
from kge.estate.onboard.common import OnboardError, _roles, _pack, writable
from kge.pipeline import access
from kge.pipeline.profile import resolve

BUSINESS = {"name", "description", "product_owner", "business_owner", "badges", "domain", "community", "application", "product_type"}
GOVERNED_BADGES = {"CERTIFIED", "GOVERNED", "RESTRICTED", "PII"}
FIELD = {"name", "business_name", "description"}


def _badge_form(value):
    return re.sub(r"[^A-Z0-9]", "", value).translate(str.maketrans("01L", "OII"))


def _within_one_edit(left, right):
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        return _within_one_edit(right, left)
    i = j = edits = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            if len(left) == len(right):
                i += 1
            j += 1
    return edits + len(left) - i + len(right) - j <= 1


def _normalise_badge(badge):
    value = unicodedata.normalize("NFKC", badge) if isinstance(badge, str) else ""
    if not re.fullmatch(r"[A-Za-z0-9 _-]+", value):
        raise OnboardError(422, "Only portal-owned badges using ASCII letters, digits, spaces, underscores and hyphens are accepted")
    normalized = re.sub(r" +", " ", value.upper()).strip()
    if not 1 <= len(normalized) <= 32:
        raise OnboardError(422, "Only portal-owned badges of 1-32 characters are accepted")
    if any(_within_one_edit(_badge_form(normalized), _badge_form(owned)) for owned in GOVERNED_BADGES):
        raise OnboardError(422, "Only portal-owned badges are accepted; badges resembling KGE certification or governance badges are refused")
    return normalized


def _governed_badge(badge):
    return badge.upper() in GOVERNED_BADGES


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode()


def _current(store):
    return store.read("catalogue.yaml") if store.exists("catalogue.yaml") else b""


def _allowed(prof, who, action="land"):
    ok, why = writable(prof)
    if not ok:
        raise OnboardError(409, why)
    permission, identity = {"land": ("draft", "drafting identity"),
                            "accept": ("accept", "business-team principal"),
                            "certify": ("certify", "certification identity")}[action]
    if not access.permissions(_roles(prof).get(who, {}))[permission]:
        raise OnboardError(403, f"A {identity} must {action} portal metadata")


def _candidate(prof, store, value, who):
    if not isinstance(value, dict) or set(value) != {"format", "client", "products"} or value["format"] != 1 or value["client"] != prof.client:
        raise OnboardError(422, "Input must name this client and metadata format 1")
    if not isinstance(value["products"], list) or not value["products"]:
        raise OnboardError(422, "Input needs products")
    _pack(prof)
    pack = packs.read(prof.clients, prof.client, lock=True, identities=prof.identities)
    try:
        held = yaml.safe_load(_current(store)) or {"format": 1, "units": [], "products": []}
    except yaml.YAMLError:
        raise OnboardError(422, "Catalogue YAML is invalid") from None
    result = copy.deepcopy(held)
    effective = {p["id"]: p for p in catalogue.derive(held, pack)["products"]}
    products = {p["id"]: p for p in result["products"]}
    known = {(unit, table["name"]): table for unit, source in pack.lock["sources"].items() for table in source["tables"]}
    readable = _roles(prof).get(who, {}).get("reads", [])
    seen = set()
    for item in value["products"]:
        if not isinstance(item, dict) or not {"id", "unit", "table", "fields"} <= set(item) or set(item) - BUSINESS - {"id", "unit", "table", "fields"}:
            raise OnboardError(422, "Only portal-owned product metadata is accepted")
        if any(not isinstance(item[key], str) for key in ("id", "unit", "table")):
            raise OnboardError(422, "Product identifiers must be strings")
        key = (item["unit"], item["table"])
        if key not in known or item["id"] != f"{key[0]}.{key[1]}" or item["id"] in seen:
            raise OnboardError(422, "Product must name a distinct landed table")
        if readable != "all" and item["unit"] not in readable:
            raise OnboardError(403, "The onboarding participant cannot read this unit")
        seen.add(item["id"])
        columns = {column["name"] for column in known[key]["columns"]}
        if not isinstance(item["fields"], list) or any(not isinstance(f, dict) or set(f) - FIELD or any(not isinstance(v, str) or not v.strip() for v in f.values()) or f.get("name") not in columns for f in item["fields"]):
            raise OnboardError(422, "Only descriptive metadata of landed columns is accepted")
        if len({f["name"] for f in item["fields"]}) != len(item["fields"]):
            raise OnboardError(422, "Column metadata must be distinct")
        product = products.get(item["id"], {"id": item["id"], "unit": item["unit"], "table": item["table"]})
        defaults = effective[item["id"]]
        defaults.setdefault("name", item["table"].replace("_", " ").title())
        if "badges" in item and isinstance(item["badges"], list) and all(isinstance(b, str) for b in item["badges"]):
            owned = [b for b in defaults.get("badges", []) if _governed_badge(b)]
            item = {**item, "badges": [_normalise_badge(b) for b in item["badges"]] + owned}
        product.update({name: item[name] for name in sorted(BUSINESS) if name in item and item[name] != defaults.get(name)})
        fields = {f["name"]: f for f in product.get("fields", [])}
        default_fields = {f["name"]: f for f in defaults["fields"]}
        locked_fields = {f["name"]: f for f in known[key]["columns"]}
        for field in item["fields"]:
            default = default_fields[field["name"]]
            default.setdefault("description", locked_fields[field["name"]].get("description") or default["business_name"])
            changes = {name: field[name] for name in sorted(FIELD - {"name"}) if name in field and field[name] != default.get(name)}
            if changes:
                fields.setdefault(field["name"], {"name": field["name"]}).update(changes)
        if fields:
            product["fields"] = list(fields.values())
        if set(product) - {"id", "unit", "table"}:
            products[item["id"]] = product
            if item["unit"] not in result["units"]:
                result["units"].append(item["unit"])
    result["products"] = list(products.values())
    findings = catalogue.validate(result, pack)
    if findings:
        raise OnboardError(422, "; ".join(str(f) for f in findings))
    return result


def _changes(before, after, path=()):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [change for key in after for change in _changes(before.get(key), after[key], (*path, key))]
    if isinstance(before, list) and isinstance(after, list) and len(before) <= len(after):
        return [change for index, new in enumerate(after) for change in _changes(before[index] if index < len(before) else None, new, (*path, index))]
    return [{"path": list(path), "before": before, "after": after}]


def _patch_yaml(baseline, candidate):
    try:
        return _patch_yaml_nodes(baseline, candidate)
    except yaml.YAMLError:
        raise OnboardError(422, "Catalogue YAML is invalid") from None


def _patch_yaml_nodes(baseline, candidate):
    """Patch changed YAML nodes, retaining untouched bytes, key order and comments."""
    if not baseline:
        return catalogue.bytes_of(candidate)
    text = baseline.decode()
    edits = []

    def inline(value):
        return json.dumps(value, ensure_ascii=False)

    def visit(node, old, new):
        if old == new:
            return
        if isinstance(node, yaml.MappingNode) and not node.flow_style and isinstance(new, dict) and old.keys() <= new.keys():
            nodes = {key.value: value for key, value in node.value}
            for key in old:
                visit(nodes[key], old[key], new[key])
            added = {key: new[key] for key in new if key not in old}
            if added:
                first_key, first_value = node.value[0]
                end = text.find("\n", first_value.end_mark.index)
                at = len(text) if end < 0 else end + 1
                indent = " " * first_key.start_mark.column
                value = "".join(f"{indent}{key}: {inline(value)}\n" for key, value in added.items())
                edits.append((at, at, ("\n" if end < 0 else "") + value))
        elif isinstance(node, yaml.SequenceNode) and not node.flow_style and len(old) <= len(new):
            for child, previous, value in zip(node.value, old, new):
                visit(child, previous, value)
            if len(new) > len(old):
                end = text.find("\n", len(text[:node.end_mark.index].rstrip()))
                at = len(text) if end < 0 else end + 1
                indent = " " * node.start_mark.column
                value = "".join(f"{indent}- {inline(item)}\n" for item in new[len(old):])
                edits.append((at, at, ("\n" if end < 0 else "") + value))
        else:
            end = len(text[:node.end_mark.index].rstrip())
            replacement = inline(new)
            if isinstance(node, yaml.SequenceNode) and not node.flow_style:
                if new:
                    indent = " " * node.start_mark.column
                    replacement = ("\n" + indent).join("- " + inline(item) for item in new)
                else:
                    replacement = "  []"
            edits.append((node.start_mark.index, end, replacement))

    visit(yaml.compose(text), yaml.safe_load(text), candidate)
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    if yaml.safe_load(text) != candidate:
        raise OnboardError(422, "Catalogue YAML cannot be patched without changing unrelated metadata")
    return text.encode()


def land(prof, value, as_):
    _allowed(prof, as_)
    store = packstore.for_profile(prof)
    with store.lock():
        candidate = _candidate(prof, store, value, as_)
        baseline = _current(store)
        _patch_yaml(baseline, candidate)
        changes = _changes(yaml.safe_load(baseline) or {}, candidate)
        base = _digest(baseline)
        job = _digest(_bytes({"base": base, "input": value}))[:24]
        path = f"portal-metadata/{job}.json"
        if store.exists(path):
            return json.loads(store.read(path))
        record = {"job": job, "client": prof.client, "base": base, "state": "landed", "candidate": candidate,
                  "changes": changes, "input": value, "landed_by": as_, "landed_at": datetime.now(timezone.utc).isoformat()}
        store.write(path, _bytes(record))
        return record


def transition(prof, job, as_, certify=False):
    _allowed(prof, as_, "certify" if certify else "accept")
    if not re.fullmatch(r"[a-f0-9]{24}", job):
        raise OnboardError(400, "Invalid metadata job ID")
    store = packstore.for_profile(prof)
    path = f"portal-metadata/{job}.json"
    with store.lock():
        if not store.exists(path):
            raise OnboardError(404, "Metadata job not found")
        record = json.loads(store.read(path))
        field = "certified_by" if certify else "accepted_by"
        if record.get(field):
            if certify:
                raise OnboardError(409, f"Metadata job is already certified by {record[field]}")
            return record
        if _digest(_current(store)) != record["base"]:
            raise OnboardError(409, "Catalogue changed since landing; export and land a new input")
        if certify and not record.get("accepted_by"):
            raise OnboardError(409, "Business acceptance is required")
        if certify and as_ == record.get("accepted_by"):
            raise OnboardError(403, "A steward cannot certify their own acceptance")
        _candidate(prof, store, record["input"], as_)
        record[field] = as_
        record["state"] = "certified" if certify else "accepted"
        record["certified_at" if certify else "accepted_at"] = datetime.now(timezone.utc).isoformat()
        baseline = _current(store)
        if certify:
            candidate = _patch_yaml(baseline, record["candidate"])
            store.write("catalogue.yaml", candidate)
        try:
            store.write(path, _bytes(record))
        except Exception:
            if certify:
                if baseline:
                    store.write("catalogue.yaml", baseline)
                else:
                    store.delete("catalogue.yaml")
            raise
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--as", dest="as_", required=True)
    commands = parser.add_subparsers(dest="step", required=True)
    commands.add_parser("land").add_argument("--input", required=True)
    for step in ("accept", "certify"):
        commands.add_parser(step).add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        prof = resolve(args.profile)
        if args.step == "land":
            path = Path(args.input)
            if path.stat().st_size > 1024 * 1024:
                raise OnboardError(413, "Metadata input exceeds 1 MB")
            result = land(prof, json.loads(path.read_text()), args.as_)
        else:
            result = transition(prof, args.job, args.as_, args.step == "certify")
        print(json.dumps({key: value for key, value in result.items() if key not in {"candidate", "input"}}))
        return 0
    except yaml.YAMLError:
        print(json.dumps({"error": "Metadata YAML is invalid", "status": 422}))
        return 2
    except (OnboardError, OSError, ValueError, StoreError) as exc:
        print(json.dumps({"error": str(exc), **({"status": exc.status} if isinstance(exc, OnboardError) else {})}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
