"""The published catalogue, separate from the graph release.

``catalogue.yaml`` is the source for descriptive fields and portal-only cards.
A portal publish or a default release writes that snapshot. A graph release does
not. The previous snapshot is kept for ``rollback portal``. Every write names
who approved it.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kge.compiler import catalogue as discovery
from kge.compiler.pack import Pack
from kge.pipeline.profile import Profile, ProfileError
from kge.ports import StoreError

MARKER = "catalogue-target"
FOLDER = "catalogue-publish"
PACK_CONTENT_ADMIN = "admin"
PUBLISH_TTL = 5.0
LABELS = {"in_sync": "In sync", "portal_ahead": "Portal ahead", "portal_only": "Not in the knowledge graph"}
_known: dict[tuple, tuple[float, dict | None]] = {}
_stores: dict[str, object] = {}
_artifact_cache: dict[str, tuple[tuple[int, int], dict]] = {}
_portal_ids_cache: dict[tuple, frozenset[str]] = {}


class CatalogueRefused(ValueError):
    """A portal catalogue change that must not publish."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def folder(prof: Profile) -> Path:
    return prof.artifacts / prof.client / FOLDER


def marker_path(prof: Profile) -> Path:
    return prof.run_dir() / MARKER


def mark(prof: Profile, target: str) -> None:
    """Remember which onboard target a later release publishes for."""
    path = marker_path(prof)
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace(path, target + "\n")


def marked(prof: Profile) -> str:
    path = marker_path(prof)
    if not path.is_file():
        return ""
    return path.read_text().strip()


def clear_mark(prof: Profile) -> None:
    marker_path(prof).unlink(missing_ok=True)


def _replace(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}")
    temporary.write_text(text)
    os.replace(temporary, path)


def _key(prof: Profile, name: str) -> str:
    return f"clients/{prof.client}/{FOLDER}/{name}"


def _cache_key(prof) -> tuple:
    from kge.pipeline.settings import settings

    url = (settings().STORE_URL or "").strip()
    return (getattr(prof, "client", ""), url, str(folder(prof)))


def _remember(prof, record: dict | None) -> None:
    _known[_cache_key(prof)] = (time.monotonic(), record)


def _store():
    from kge.pipeline.settings import settings

    url = (settings().STORE_URL or "").strip()
    if not url:
        return None
    held = _stores.get(url)
    if held is None:
        from kge.adapters.objstore import open_store
        held = open_store(url)
        _stores[url] = held
    return held


def _read(prof: Profile, name: str) -> dict | None:
    store = _store()
    if store is not None:
        raw = store.get(_key(prof, name))
        if raw:
            return json.loads(raw)
    path = folder(prof) / name
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def _write(prof: Profile, name: str, value: dict) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    _replace(folder(prof) / name, text)
    store = _store()
    if store is not None:
        store.put(_key(prof, name), text.encode())


def load(prof) -> dict | None:
    """The published catalogue, or the last one read when the store cannot be reached.

    A missing ledger is None for the same short interval as a present one, so catalogue,
    search and onboard polls do not read the object store on every call. A store error
    is not cached: the last successful record is kept, and a failure before any
    successful read is None so the next call tries again.
    """
    key = _cache_key(prof)
    now_m = time.monotonic()
    cached = _known.get(key)
    if cached and now_m - cached[0] < PUBLISH_TTL:
        return cached[1]
    try:
        record = _read(prof, "current.json")
    except (OSError, ValueError, ProfileError, StoreError):
        if cached and cached[1] is not None:
            return cached[1]
        return None
    _known[key] = (now_m, record)
    return record


def _audit(prof: Profile, entry: dict) -> None:
    path = folder(prof) / "audit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    store = _store()
    if store is None:
        return
    previous = store.get(_key(prof, "audit.jsonl")) or b""
    store.put(_key(prof, "audit.jsonl"), previous + line.encode())


def audit(prof) -> list[dict]:
    path = folder(prof) / "audit.jsonl"
    store = _store()
    raw = ""
    if store is not None:
        held = store.get(_key(prof, "audit.jsonl"))
        if held:
            raw = held.decode()
    if not raw and path.is_file():
        raw = path.read_text()
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def artifact_products(prof) -> dict[str, dict]:
    """Graph-release catalogue products, keyed by id. Empty when nothing is released."""
    try:
        current = prof.current()
    except (AttributeError, ProfileError, OSError):
        return {}
    if current is None:
        return {}
    path = current / "catalogue.yaml"
    if not path.is_file():
        return {}
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    cached = _artifact_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        held = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    products = {item["id"]: item for item in held.get("products", []) if isinstance(item, dict) and item.get("id")}
    _artifact_cache[str(path)] = (stamp, products)
    return products


def _descriptive(product: dict) -> dict:
    return {key: product[key] for key in discovery.DESCRIPTIVE if key in product}


def _card(product: dict, *, portal_only: bool, documents: list[dict] | None = None) -> dict:
    card = {"id": product["id"], "unit": product["unit"], "table": product["table"],
            "portal_only": portal_only, "descriptive": _descriptive(product)}
    if documents:
        card["documents"] = documents
    return card


def _expand_documents(product: dict, pack: Pack) -> list[dict]:
    held = product.get("documents")
    if held is None:
        return []
    ids, shelves = discovery.known_documents(pack)
    if isinstance(held, dict):
        names = shelves.get(held.get("shelf"), [])
    else:
        names = [item for item in held if item in ids]
    return [{"id": name, "title": discovery.document_title(pack, name)} for name in names]


def snapshot(pack: Pack, prof, *, refuse_governed: bool) -> tuple[list[dict], list[str]]:
    """The catalogue file as a publish: cards, and refusal lines. A refusal publishes nothing."""
    value, findings = discovery.read(pack)
    if findings:
        return [], [str(item) for item in findings]
    if not value:
        return [], ["catalogue.yaml is not in this pack"]
    released = artifact_products(prof)
    cards, refusals = [], []
    for index, product in enumerate(value.get("products", [])):
        if not isinstance(product, dict) or "id" not in product:
            continue
        held = released.get(product["id"])
        portal_only = held is None
        if portal_only and (product.get("unit"), product.get("table")) in {
                (item.get("unit"), item.get("table")) for item in released.values()}:
            portal_only = False
            held = next(item for item in released.values()
                        if (item.get("unit"), item.get("table")) == (product.get("unit"), product.get("table")))
        if refuse_governed:
            fact = discovery.governed_fact(product, None if portal_only else held)
            if fact:
                refusals.append(f"catalogue.yaml:products[{index}].{fact} sets governed fact {fact}")
                continue
        if portal_only:
            cards.append(_card(product, portal_only=True, documents=_expand_documents(product, pack)))
        else:
            base = _descriptive(held or {})
            base.update(_descriptive(product))
            merged = {**product, **base}
            cards.append(_card(merged, portal_only=False))
    if refusals:
        return [], refusals
    return cards, []


def _digest(cards: list[dict]) -> str:
    return hashlib.sha256(json.dumps(cards, sort_keys=True, default=str).encode()).hexdigest()


def diff_lines(before: list[dict], after: list[dict]) -> list[str]:
    """A card diff: added, removed and changed descriptive fields."""
    old = {card["id"]: card for card in before}
    new = {card["id"]: card for card in after}
    lines = []
    for identifier in sorted(set(old) | set(new)):
        if identifier not in old:
            lines.append(f"+ {identifier}" + (" (portal-only)" if new[identifier].get("portal_only") else ""))
            continue
        if identifier not in new:
            lines.append(f"- {identifier}")
            continue
        left, right = old[identifier].get("descriptive", {}), new[identifier].get("descriptive", {})
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                lines.append(f"  {identifier} {key}: {_shown(left.get(key))} -> {_shown(right.get(key))}")
    return lines


def _shown(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _baseline_cards(prof) -> list[dict]:
    current = load(prof)
    if current:
        return current.get("products", [])
    return [_card(product, portal_only=False) for product in artifact_products(prof).values()]


def require_approver(prof: Profile, principal: str) -> str:
    """A steward or an access admin. One of them is enough.

    The pack content role ``admin`` is not an approver, even when that role lists
    certification. An access admin is reserved ``access_admin`` membership or
    ``ACCESS_ADMINS``. A person who is both a steward and that content role may
    approve as a steward. ``PRINCIPAL`` is the operator-asserted name, checked
    for membership the same way sign-off is.
    """
    from kge.pipeline import access_table

    if not principal or not str(principal).strip():
        raise CatalogueRefused("portal catalogue changes need PRINCIPAL=<steward or access admin>")
    try:
        who = access_table.normalize_username(principal)
    except ValueError as exc:
        raise CatalogueRefused(str(exc)) from None
    if access_table.is_admin(prof, who):
        return who
    roles = access_table.served_roles(prof)
    if not roles:
        try:
            pack = yaml.safe_load((prof.clients / prof.client / "pack.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            pack = {}
        roles = {role["id"]: role for role in pack.get("access", {}).get("roles", []) if isinstance(role, dict) and role.get("id")}
    try:
        held = set(access_table.Access.for_profile(prof).roles_of(who))
    except (OSError, ValueError, AttributeError, TypeError):
        held = set()
    held -= {access_table.ACCESS_ADMIN, PACK_CONTENT_ADMIN}
    if any("certification" in (roles.get(role) or {}).get("processes", []) for role in held):
        return who
    raise CatalogueRefused(f"{who} may not approve a portal catalogue change: a steward or an access admin is required")


def _baseline_record(prof) -> dict:
    """The graph release's descriptive cards, the rollback point of a first publish."""
    cards = [_card(product, portal_only=False) for product in artifact_products(prof).values()]
    return {"digest": _digest(cards), "by": "release", "at": now(), "target": "release", "products": cards}


def publish_cards(prof: Profile, cards: list[dict], approver: str, target: str) -> dict:
    """Write the snapshot, then the previous one, and audit the approver.

    ``current.json`` is written before ``previous.json``. A failed current write
    leaves the rollback snapshot untouched. The predecessor is staged under
    ``predecessor.json``, which is not the rollback file.
    """
    previous = load(prof)
    record = {"digest": _digest(cards), "by": approver, "at": now(), "target": target, "products": cards}
    predecessor = previous if previous else _baseline_record(prof)
    _write(prof, "predecessor.json", predecessor)
    _write(prof, "current.json", record)
    _write(prof, "previous.json", predecessor)
    _remember(prof, record)
    _audit(prof, {"at": record["at"], "action": "publish", "by": approver, "digest": record["digest"], "target": target})
    return record


def catalogue_diff(prof, pack: Pack) -> tuple[str, list[dict]]:
    """The card diff of catalogue.yaml. Writes nothing."""
    cards, refusals = snapshot(pack, prof, refuse_governed=True)
    if refusals:
        raise CatalogueRefused("; ".join(refusals))
    lines = diff_lines(_baseline_cards(prof), cards)
    return ("\n".join(lines) if lines else "(no catalogue changes)"), cards


def publish_portal(prof: Profile, pack: Pack, principal: str) -> dict:
    """Check, diff and publish catalogue.yaml. No graph build.

    The diff is computed before the approver is checked and before any write.
    A refusal or an unapproved principal publishes nothing.
    """
    text, cards = catalogue_diff(prof, pack)
    approver = require_approver(prof, principal)
    return {"diff": text, "record": publish_cards(prof, cards, approver, "portal")}


def publish_release(prof: Profile, principal: str | None) -> None:
    """Publish the catalogue with a release. A graph-only onboard leaves the last publish."""
    target = marked(prof)
    if target == "graph":
        clear_mark(prof)
        return
    from kge.compiler.pack import read

    try:
        pack = read(prof.clients, prof.client)
        value, findings = discovery.read(pack)
        if not value:
            if findings and target == "both":
                raise CatalogueRefused("; ".join(str(item) for item in findings))
            return
        cards, refusals = snapshot(pack, prof, refuse_governed=False)
        if refusals:
            if target == "both":
                raise CatalogueRefused("; ".join(refusals))
            return
        current = load(prof)
        if current and current.get("products") == cards:
            return
        publish_cards(prof, cards, principal or "release", "both" if target != "portal" else "portal")
    finally:
        clear_mark(prof)


def rollback(prof: Profile, principal: str) -> dict:
    """Restore the previous published catalogue. The graph is not touched."""
    approver = require_approver(prof, principal)
    previous = _read(prof, "previous.json")
    current = load(prof)
    if not previous or not current:
        raise CatalogueRefused("no previous published catalogue")
    _write(prof, "predecessor.json", current)
    _write(prof, "current.json", previous)
    _write(prof, "previous.json", current)
    _remember(prof, previous)
    _audit(prof, {"at": now(), "action": "rollback", "by": approver, "digest": previous.get("digest", ""), "target": "portal"})
    return previous


def _states_from(released: dict[str, dict], cards: list[dict]) -> list[dict]:
    published = {card["id"]: card for card in cards}
    rows = []
    for identifier, product in sorted(released.items()):
        card = published.get(identifier)
        if card is None:
            state = "in_sync"
        elif card.get("portal_only"):
            state = "portal_only"
        else:
            state = "portal_ahead" if _descriptive(product) != card.get("descriptive", {}) else "in_sync"
        rows.append({"id": identifier, "state": LABELS[state], "unit": product.get("unit", "")})
    for identifier, card in sorted(published.items()):
        if identifier in released or not card.get("portal_only"):
            continue
        rows.append({"id": identifier, "state": LABELS["portal_only"], "unit": card.get("unit", "")})
    return rows


def states(prof) -> list[dict]:
    """Each card's state against the graph release and the published catalogue."""
    released = artifact_products(prof)
    current = load(prof)
    cards = current.get("products", []) if current else []
    if not current:
        return [{"id": identifier, "state": LABELS["in_sync"], "unit": product.get("unit", "")}
                for identifier, product in sorted(released.items())]
    return _states_from(released, cards)


def portal_only_ids(prof) -> frozenset[str]:
    """Product ids that are portal-only cards, not unknown tables used as page context."""
    catalogue = Path(getattr(prof, "clients", "")) / getattr(prof, "client", "") / "catalogue.yaml"
    try:
        stamp = catalogue.stat().st_mtime_ns if catalogue.is_file() else 0
    except OSError:
        stamp = 0
    current = load(prof)
    key = (getattr(prof, "client", ""), str(catalogue), stamp, (current or {}).get("digest", ""))
    cached = _portal_ids_cache.get(key)
    if cached is not None:
        return cached
    ids: set[str] = set()
    for card in (current or {}).get("products") or []:
        if card.get("portal_only") and card.get("unit") and card.get("table"):
            ids.add(f"{card['unit']}.{card['table']}")
    try:
        from kge.compiler.pack import read as read_pack

        value, findings = discovery.read(read_pack(prof.clients, prof.client))
    except (OSError, ProfileError, AttributeError, TypeError, ValueError, yaml.YAMLError):
        value = None
    if isinstance(value, dict):
        released = artifact_products(prof)
        pairs = {(item.get("unit"), item.get("table")) for item in released.values()}
        for product in value.get("products", []):
            if not isinstance(product, dict):
                continue
            pair = (product.get("unit"), product.get("table"))
            if product.get("id") in released or pair in pairs:
                continue
            if pair[0] and pair[1]:
                ids.add(f"{pair[0]}.{pair[1]}")
    found = frozenset(ids)
    _portal_ids_cache[key] = found
    return found


def suffix(prof, released: dict[str, dict] | None = None, current: dict | None = None, *, loaded: bool = False) -> str:
    """A release-id suffix when the published catalogue differs from the graph release."""
    if not loaded:
        current = load(prof)
    if not current:
        return ""
    released = artifact_products(prof) if released is None else released
    if _states_from(released, current.get("products", [])) == [
            {"id": identifier, "state": LABELS["in_sync"], "unit": product.get("unit", "")}
            for identifier, product in sorted(released.items())]:
        return ""
    return "|portal:" + current.get("digest", "")[:16]


def frozen_descriptive(enrichment: dict, prof) -> dict:
    """Keep the last graph release's descriptive fields, so a graph build does not publish catalogue edits."""
    released = artifact_products(prof)
    if not released or not isinstance(enrichment, dict):
        return enrichment
    products = []
    for product in enrichment.get("products", []):
        if not isinstance(product, dict):
            products.append(product)
            continue
        held = released.get(product.get("id"))
        if held is None:
            products.append(product)
            continue
        frozen = dict(product)
        for key in discovery.DESCRIPTIVE:
            if key in held:
                frozen[key] = held[key]
            else:
                frozen.pop(key, None)
        products.append(frozen)
    return {**enrichment, "products": products}


def apply_card(product: dict, card: dict | None) -> dict:
    """Overlay a published card's descriptive fields onto a graph catalogue product."""
    if not card or card.get("portal_only"):
        return product
    merged = dict(product)
    for key, value in card.get("descriptive", {}).items():
        merged[key] = value
    return merged
