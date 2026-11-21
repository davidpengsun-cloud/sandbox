"""Write Meridian's fixture from a fresh generated sample, never from an installed client.

Run make sample-fixture to replace clients/meridian/fixture. The generator remains
the source of truth; the committed files let an installer edit the sample through
the same onboarding loop as any other fixture client.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from kge.adapters.warehouse import open_estate
from kge.estate import certify
from kge.pipeline.settings import ROOT

SAMPLE = ROOT / "clients/meridian"
SETS = ("golden", "heldout", "golden_wealth_v1", "sage_v1", "golden_figures_v1", "golden_documents_v1", "heldout_documents_v1")


def parents_first(tables: list[dict]) -> list[dict]:
    """Stable dependency order, also accepted by the BigQuery estate."""
    pending = list(tables)
    ordered, seen = [], set()
    while pending:
        ready = [t for t in pending if all(k["ref_table"] in seen for k in t["foreign_keys"])]
        if not ready:
            raise ValueError("sample tables have a missing or cyclic foreign-key parent")
        for table in ready:
            ordered.append(table)
            seen.add(table["name"])
            pending.remove(table)
    return ordered


def write_fixture(generated: Path, fixture: Path) -> None:
    """Re-express a freshly generated sample, preserving catalogue, rows and text."""
    estate = open_estate(generated / "warehouses", "meridian")
    pack = yaml.safe_load((SAMPLE / "pack.yaml").read_text())
    houses = {}
    for source in [*pack["sources"], *pack["curated_sources"]]:
        unit = source["id"]
        tables = parents_first(certify.catalogue_of(estate, unit)["tables"])
        houses[unit] = {"label": source["label"], "tables": [
            {"name": t["name"], "columns": [{"name": c["name"], "type": c["type"], "description": c["comment"]}
                                            for c in t["columns"]],
             "primary_key": t["primary_key"], "foreign_keys": t["foreign_keys"]} for t in tables]}
        data = fixture / "data" / unit
        data.mkdir(parents=True, exist_ok=True)
        session = estate.open(unit)
        try:
            for table in tables:
                columns = [c["name"] for c in table["columns"]]
                rows = session.execute(f"SELECT {', '.join(columns)} FROM {table['name']} "
                                       f"ORDER BY {', '.join(table['primary_key'])}").fetchall()
                with (data / f"{table['name']}.csv").open("w", newline="") as stream:
                    writer = csv.writer(stream, lineterminator="\n")
                    writer.writerow(columns)
                    writer.writerows(rows)
        finally:
            session.close()
    documents = json.loads((generated / "documents.json").read_text())
    (fixture / "schema.yaml").write_text(yaml.safe_dump(
        {"warehouses": houses, "document_order": [d.get("original", d["path"]) for d in documents]}, sort_keys=False))
    for doc in documents:
        text = (generated / doc["path"]).read_text()
        lines = text.splitlines(keepends=True)
        body = "".join(lines[next(i for i in range(4, len(lines)) if not lines[i].startswith("|")):])
        if body.startswith("\n"):
            body = body[1:]
        meta = {key: value for key, value in doc.items() if key not in {"doc_id", "shelf", "path", "original", "reading"} and value is not None}
        target = fixture / doc.get("original", doc["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if doc.get("original"):
            target.write_bytes((generated / "originals" / doc["original"]).read_bytes())
            target.with_name(target.name + ".yaml").write_text(yaml.safe_dump(meta, sort_keys=False))
        else:
            target.write_text("---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n" + body)
    for name in SETS:
        shutil.copyfile(generated / f"{name}.json", fixture / f"{name}.json")


def generate_fixture(destination: Path) -> None:
    """Generate afresh in isolation, then replace the derived fixture as a whole."""
    with tempfile.TemporaryDirectory(prefix="kge-sample-fixture-") as scratch:
        scratch_dir = Path(scratch)
        generated, fixture = scratch_dir / "estate", scratch_dir / "fixture"
        env = {**os.environ, "PROFILE": "meridian", "PROFILES": str(ROOT / "deploy/profiles.yaml")}
        subprocess.run([sys.executable, "-m", "kge.estate.generate", "--out", str(generated)],
                       cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
        write_fixture(generated, fixture)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(fixture, destination)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["meridian"], default="meridian", help="only the sample may be exported")
    parser.add_argument("--out", type=Path, default=SAMPLE / "fixture")
    args = parser.parse_args()
    generate_fixture(args.out)
    print(f"sample-fixture: wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
