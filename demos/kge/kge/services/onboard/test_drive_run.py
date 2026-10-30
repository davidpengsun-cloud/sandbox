"""The test drive's preflight, its ordered run and its diagnostic report on the job's configuration."""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from sqlglot import exp

from kge.adapters.model.embed import make_embedder
from kge.adapters.model.providers import model_for, tagger_model
from kge.adapters.objstore import open_scratch, open_store, open_store_if_url, under
from kge.eval import sets
from kge.eval.asking import Unanswered, each
from kge.eval.run_golden import asked_as, score, uncertified_ids
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ENVIRONMENT_NAMES, ROOT, settings
from kge.services.ask import Engine
from kge.services.catalogue import get as catalogue_get
from kge.services.data import DataRefused, preview as data_preview, query as data_query
from kge.services.onboard.test_drive import SAMPLE, CONFIG, PEOPLE, Result, client, filtered

TITLE = "Test drive: a diagnostic report on the job's configuration"
AGENTS = ("onboard", "enhance", "answer", "context")
TIERS = ("HIGH", "MEDIUM", "LOW")
KIT_EXAMPLES = Path("/kit/examples")
HIDDEN = {"MODEL_API_KEY", "MODEL_BASE_URL", "SESSION_SECRET", "SIGNIN_PASSWORDS", "PORTAL_SYNC_TOKEN"}
PASSED, FAILED, NOT_APPLICABLE = "passed", "failed", "not applicable"


def remove_before_rerun() -> tuple[str, ...]:
    from kge.adapters import test_drive_tables
    dataset = test_drive_tables(client())
    return (
        f"clients/{client()}/", f"deploy/{client()}/ and {client()}'s entry in deploy/profiles.yaml",
        f"work/{client()}.tar.gz", f"releases/{client()}/", "the landing bucket's objects",
        f"the BigQuery tables in the {dataset} datasets other than the portal's (_portal_*)",
    )


ACTIONS = {
    "settings": "correct the setting the detail names in the job's configuration",
    "store bucket": "check STORE_URL, and that the job's account may read and list it",
    "landing bucket": "check LANDING_URL, and that the job's account may read and list it",
    "tmp bucket": "check TMP_URL, and that the job's account may read the bucket's metadata and write tmp/<client>/",
    "datasets": "check BIGQUERY_PROJECT and BIGQUERY_DATASET_PREFIX, and that the client's datasets exist",
    "models": "check each configured provider and model name, and the account's access to it",
    "embedding": "check EMBEDDING_PROVIDER, EMBEDDING_MODEL and EMBEDDING_DIM",
    "versions": "copy the kit's example to the store's examples/ (the pilot), or mount the kit's examples (Compose)",
    "golden check": "read each failure as the scorer words it; the released graph does not answer as the example expects",
    "catalogue": "inspect the released catalogue and example lock; every onboarded table must have its governed discovery fields",
    "data": "inspect the preview, query or cross-unit refusal; the released warehouses must serve the example through the data service",
}


class NotApplicable(Exception):
    """An item this install does not have, with the reason."""


def action(name: str) -> str:
    return ACTIONS.get(name, f"read the {name} step's log above, fix its cause, and remove what the report lists before a rerun")


def unanswered(exc: BaseException) -> bool:
    """Whether a step stopped because a question met a rate limit or outage on every try (kge/eval/asking.py Unanswered),
    raised here or said by the margin check its command ran ("margin: cannot check: Unanswered: ...")."""
    return isinstance(exc, Unanswered) or "Unanswered: " in str(exc)


def could_not_check(name: str, started: float, exercised, exc: BaseException) -> Result:
    """A step that could not check, not one that found a fault: BigQuery or Vertex AI turned a question away on every try."""
    reason = str(exc).split("Unanswered: ", 1)[-1] or type(exc).__name__
    return Result(name, FAILED, time.monotonic() - started, list(exercised), filtered(f"could not check: {reason}"),
                  "BigQuery or Vertex AI turned a question away on every try (a rate limit or an outage), so nothing was "
                  "checked and nothing in the configuration is shown to be at fault: run the test drive again later")


def timed(name: str, call, exercised=()) -> Result:
    started = time.monotonic()
    try:
        detail = call()
    except NotApplicable as exc:
        return Result(name, NOT_APPLICABLE, time.monotonic() - started, [], filtered(exc))
    except Exception as exc:
        if unanswered(exc):
            return could_not_check(name, started, exercised, exc)
        return Result(name, FAILED, time.monotonic() - started, list(exercised), filtered(str(exc) or type(exc).__name__),
                      filtered(action(name)))
    return Result(name, PASSED, time.monotonic() - started, list(exercised), filtered(detail or ""))


def host(url: str | None) -> str:
    if not url:
        return "unset"
    return urlsplit(url).hostname or "not a URL"


def effective_settings() -> dict:
    """The allow-list: every name settings() reads and the tiers' provider and name, the key as set or unset, the base
    URL as its host, and the service's own secrets as not the job's."""
    current = settings()
    shown = {name: getattr(current, name) or "unset" for name in ENVIRONMENT_NAMES if name not in HIDDEN}
    for tier in TIERS:
        for part in ("PROVIDER", "NAME"):
            shown[f"MODEL_{tier}_{part}"] = os.getenv(f"MODEL_{tier}_{part}") or "unset"
    shown["PORTAL_SYNC_TOKEN"] = "set" if current.PORTAL_SYNC_TOKEN else "unset"
    shown["MODEL_API_KEY"] = "set" if current.MODEL_API_KEY else "unset"
    shown["MODEL_BASE_URL"] = host(current.MODEL_BASE_URL)
    shown["SESSION_SECRET"] = NOT_APPLICABLE + ": the service's"
    shown["SIGNIN_PASSWORDS"] = NOT_APPLICABLE + ": the service's"
    return dict(sorted(shown.items()))


def configured_models() -> dict:
    """Each distinct provider and model the agents and the tagger are given, once."""
    models = {}
    for model in [*(model_for(agent) for agent in AGENTS), tagger_model()]:
        if model is not None:
            models.setdefault(model.name, model)
    return models


def check_settings() -> str:
    from kge.adapters import validate_settings
    validate_settings()
    make_embedder()
    configured_models()
    return "the backends, models and embedder the settings select were each accepted by its factory"


def check_bucket(url: str) -> str:
    return open_store(url).probe()


def landing_url(kept) -> str:
    return (settings().LANDING_URL or "").strip() or under(kept.url, "landing", client())


def check_tmp() -> str:
    url = (settings().TMP_URL or "").strip()
    if not url:
        raise NotApplicable("TMP_URL is unset: this install has no tmp bucket")
    exercised = [open_store(url).bucket_metadata()]
    area = open_scratch(client())
    key = f"test-drive-{os.getpid()}-{int(time.time())}"
    try:
        area.put(key, b"test-drive")
        exercised.append("write")
        if area.get(key) != b"test-drive":
            raise RuntimeError(f"the probe written to tmp/{client()}/ did not read back")
        exercised.append("read")
    finally:
        area.delete(key)
    exercised.append("delete")
    return ", ".join(exercised) + f" of one probe under tmp/{client()}/"


def check_bigquery() -> str:
    from kge.adapters import bigquery_preflight
    project = bigquery_preflight(client())
    if project is None:
        raise NotApplicable("this install keeps nothing in BigQuery")
    return f"project {project}; the {client()} warehouses and graph datasets' tables listed; seed and onboard prove writing"


def check_models() -> str:
    models = configured_models()
    if not models:
        raise NotApplicable("no model is configured: the steps run on rules")
    for model in models.values():
        model.complete("Reply with the word OK.", max_tokens=256, fixed_budget=True)
    return "one call each: " + ", ".join(models)


def check_embedding() -> str:
    embedder = make_embedder()
    vector = embedder.embed("test drive")
    if len(vector) != embedder.dim:
        raise RuntimeError(f"{embedder.name} gave a vector {len(vector)} wide, not the {embedder.dim} configured")
    return f"{embedder.name}, {len(vector)} wide"


def example_source() -> str:
    """Where put-example reads the example: FROM where set; else the kit's examples, mounted under Compose; else the
    store's examples/, where the installer copies them on the pilot."""
    named = (settings().FROM or "").strip()
    if named:
        return named
    if (KIT_EXAMPLES / SAMPLE).is_dir():
        return str(KIT_EXAMPLES)
    return under((settings().STORE_URL or "").strip(), "examples")


def example_pack(source: str) -> dict:
    key = f"{SAMPLE}/clients/{SAMPLE}/pack.yaml"
    remote = open_store_if_url(source)
    if remote is not None:
        raw = remote.get(key)
    else:
        path = Path(source) / key
        raw = path.read_bytes() if path.is_file() else None
    if raw is None:
        raise RuntimeError(f"no example {SAMPLE} at {source}")
    return yaml.safe_load(raw)


def example_lock(source: str) -> dict:
    key = f"{SAMPLE}/clients/{SAMPLE}/pack.lock"
    remote = open_store_if_url(source)
    raw = remote.get(key) if remote is not None else ((Path(source) / key).read_bytes() if (Path(source) / key).is_file() else None)
    if raw is None:
        raise RuntimeError(f"no example {SAMPLE} lock at {source}")
    return json.loads(raw)


def check_versions() -> str:
    meta = yaml.safe_load((ROOT / "meta-model.yaml").read_text())
    manifest = example_pack(example_source())["manifest"]
    image = settings().KGE_VERSION or "unset"
    return (f"image and kit {image}; meta-model {meta['version']}; example pack {manifest['version']}, "
            f"for meta-model {manifest['meta_model_version']}")


def preflight(kept) -> list[Result]:
    store_url, landing = kept.url, landing_url(kept)
    checks = [
        ("settings", check_settings, ("the backend, model and embedder factories",)),
        ("store bucket", lambda: check_bucket(store_url), ("read", "list")),
        ("landing bucket", lambda: check_bucket(landing), ("read", "list")),
        ("tmp bucket", check_tmp, ("bucket metadata", "write", "read", "delete")),
        ("datasets", check_bigquery, ("project", "catalogue read")),
        ("models", check_models, ("one call per distinct model",)),
        ("embedding", check_embedding, ("one embedding call",)),
        ("versions", check_versions, ("read",)),
    ]
    return [timed(name, call, exercised) for name, call, exercised in checks]


def run_command(args: list[str], extra: dict[str, str] | None = None) -> str:
    """One job command, through the entry script as an execution runs it; its output in the log, filtered."""
    env = {**os.environ, **(extra or {})}
    proc = subprocess.run([str(ROOT / "entrypoint.sh"), *args], cwd=ROOT, env=env, text=True, capture_output=True)
    output = (proc.stdout + proc.stderr).strip()
    if output:
        print(filtered(output), flush=True)
    last = output.splitlines()[-1] if output else ""
    if proc.returncode:
        raise RuntimeError(f"exit {proc.returncode}" + (f": {last}" if last else ""))
    return summary_line(output) or "completed"


def summary_line(output: str) -> str:
    """The line that says what a step did: its GATE line, naming the candidate's hash; else the store's last; else the last."""
    lines = output.splitlines()
    for mark in ("GATE:", "store:"):
        said = [line for line in lines if line.startswith(mark)]
        if said:
            return said[-1]
    return lines[-1] if lines else ""


def steps() -> list[tuple]:
    return [
        ("setup-client", ["setup-client", f"CLIENT={client()}", f"ASKER={CONFIG['asker']}", f"PEOPLE={PEOPLE}"], None,
         ("store write",)),
        ("put-baseline", ["put-baseline"], {"FROM": example_source()}, ("example read", "store write")),
        ("seed", ["seed"], None, ("warehouse write", "landing write")),
        ("lock", ["lock"], None, ("landed catalogue read", "branded pack lock write")),
        ("onboard", ["onboard"], None, ("warehouse write", "graph write", "the pack's gates")),
        ("sign-off", ["sign-off", f"PRINCIPAL={CONFIG['steward']}"], None, ("graph write",)),
        ("release", ["release"], None, ("store write",)),
    ]


def golden() -> dict:
    """The golden set, and the held-out paraphrases, through the engine on the released graph, on the graph preset,
    each scored by the golden scorer."""
    profile = resolve(client())
    engine = Engine.serving(profile)
    try:
        estate = profile.estate()
        principal = profile.asking(CONFIG["asker"])
        unc = uncertified_ids(estate)

        def one(item: dict) -> dict:
            answer = engine.answer(item["question"], principal=asked_as(item, principal), arm_name="graph")
            ok, why = score(item, answer, unc)
            return {"id": item["id"], "passed": ok, "failure": "" if ok else why}

        def scored(path: Path) -> list[dict]:
            return each(one, sets.items(path), lambda item: item["id"])

        gated = scored(estate / "golden.json")
        held = scored(estate / "heldout.json") if (estate / "heldout.json").is_file() else []
    finally:
        engine.close()
    return {"passed": sum(i["passed"] for i in gated), "of": len(gated),
            "failures": [{"id": i["id"], "failure": i["failure"]} for i in gated if not i["passed"]],
            "held_out": {"passed": sum(i["passed"] for i in held), "of": len(held),
                         "failures": [{"id": i["id"], "failure": i["failure"]} for i in held if not i["passed"]]}}


def golden_check(report: dict) -> Result:
    started = time.monotonic()
    exercised = ["Engine.serving", "graph preset", "golden scorer"]
    try:
        detail = golden()
    except Exception as exc:
        if unanswered(exc):
            return could_not_check("golden check", started, exercised, exc)
        return Result("golden check", FAILED, time.monotonic() - started, exercised, filtered(str(exc) or type(exc).__name__),
                      action("golden check"))
    report["golden"] = detail
    summary = (f"{detail['passed']} of {detail['of']} passed; held-out {detail['held_out']['passed']} of "
               f"{detail['held_out']['of']}, reported, not gated")
    if not detail["of"] or detail["passed"] != detail["of"]:
        why = "; ".join(f"{f['id']}: {f['failure']}" for f in detail["failures"]) or "the golden set holds no question"
        return Result("golden check", FAILED, time.monotonic() - started, exercised, filtered(f"{summary}. {why}"),
                      action("golden check"))
    return Result("golden check", PASSED, time.monotonic() - started, exercised, summary)


def catalogue_check() -> str:
    pack = example_pack(example_source())
    lock = example_lock(example_source())
    expected = {source["id"]: len(lock["sources"][source["id"]]["tables"]) for source in pack["sources"]}
    profile = resolve(client())
    response = catalogue_get(profile, list(expected))
    counts = {unit: sum(product.unit_id == unit for product in response.products) for unit in expected}
    if not response.has_catalogue or counts != expected:
        raise RuntimeError(f"products by unit are {counts}, expected {expected}")
    for product in response.products:
        if not product.fields or any(not field.data_type for field in product.fields):
            raise RuntimeError(f"{product.id} has no complete typed columns")
        if product.records < 0 or not product.product_owner or not product.business_owner:
            raise RuntimeError(f"{product.id} has incomplete counts or ownership")
    products = {product.id: product for product in response.products}
    for item in pack.get("lineage", []):
        if not products[item["table"]].lineage.upstream:
            raise RuntimeError(f"{item['table']} has no derived lineage")
    return ", ".join(f"{unit} {counts[unit]}" for unit in expected)


def data_check() -> str:
    profile = resolve(client())
    engine = Engine.serving(profile)
    try:
        catalogue = catalogue_get(profile, list(engine.g.domain()["units"]))
        who = profile.asking(CONFIG["people"][0].split(":", 1)[0])
        pack = example_pack(example_source())
        unit = pack["sources"][0]["id"]
        certified = {mapping["column"].rsplit(".", 1)[0] for mapping in pack["mappings"] if mapping.get("certified")}
        product = next(item for item in catalogue.products if item.unit_id == unit and item.id in certified)
        other = next(item for item in catalogue.products if item.unit_id != unit)
        previewed = data_preview(engine, who, product.unit_id, product.table, 2)
        sql = exp.select(*(exp.column(name, table=product.table) for name in previewed.columns)).from_(product.table).limit(2).sql(engine.w.sql_dialect)
        queried = data_query(engine, who, product.unit_id, sql)
        if not previewed.rows or not queried.rows:
            raise RuntimeError(f"{product.id} returned no rows")
        try:
            data_preview(engine, who, other.unit_id, other.table, 1)
        except DataRefused:
            pass
        else:
            raise RuntimeError(f"the one-unit example person read {other.id}")
    finally:
        engine.close()
    return f"preview and query returned rows for {product.id}; {other.id} was refused"


def cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown(report: dict) -> str:
    lines = [f"# {report['title']}", "", f"Client `{report['client']}`, {report['time']}. Result: **{report['result']}**.", ""]
    for section in ("preflight", "steps"):
        lines += [f"## {section.title()}", "", "| Item | Result | Seconds | Exercised | Detail | What to do |",
                  "|---|---|---:|---|---|---|"]
        for item in report[section]:
            lines.append(f"| {item['name']} | {item['result']} | {item['duration']:.1f} | {cell(', '.join(item['exercised']))} "
                         f"| {cell(item['detail'])} | {cell(item['what_to_do'])} |")
        if not report[section]:
            lines.append("| none run | | | | | |")
        lines.append("")
    lines += ["## Release", "", f"Released hash: `{report.get('released_hash') or 'not released'}`", ""]
    gold = report.get("golden") or {}
    if gold:
        lines += ["## Golden check", "", f"{gold['passed']} of {gold['of']} golden questions passed.", ""]
        lines += [f"- {f['id']}: {f['failure']}" for f in gold["failures"]]
        lines += ["", f"Held-out paraphrases, reported, not gated: {gold['held_out']['passed']} of "
                      f"{gold['held_out']['of']} passed.", ""]
        lines += [f"- {f['id']}: {f['failure']}" for f in gold["held_out"]["failures"]]
        lines.append("")
    lines += ["## Settings", "", "| Setting | Value |", "|---|---|"]
    lines += [f"| {name} | {cell(value)} |" for name, value in report["settings"].items()]
    lines += ["", "## Starting baseline", "", "After a pass, keep the branded pack and release. Edit the pack, then onboard, sign off and release again.", "", "## Before a fresh test drive", "", "Only to discard this baseline and start over, remove:", ""]
    lines += [f"- {where}" for where in report["remove_before_rerun"]]
    return "\n".join(filtered(line) for line in lines) + "\n"


def save(kept, report: dict) -> bool:
    stamp = report["time"]
    try:
        kept.put(f"runs/{client()}/test-drive/{stamp}.json", filtered(json.dumps(report, indent=2)).encode())
        kept.put(f"runs/{client()}/test-drive/{stamp}.md", markdown(report).encode())
    except Exception as exc:
        print(f"test-drive: the report could not be saved in the store, so it is in this log alone: {filtered(exc)}", flush=True)
        return False
    print(f"test-drive: report saved as runs/{client()}/test-drive/{stamp}.json and .md", flush=True)
    return True


def execute(kept) -> int:
    report = {"title": TITLE, "time": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "client": client(),
              "result": FAILED, "settings": effective_settings(), "preflight": [], "steps": [], "released_hash": None,
              "golden": {}, "remove_before_rerun": list(remove_before_rerun())}
    checked = preflight(kept)
    report["preflight"] = [asdict(item) for item in checked]
    failed = any(item.result == FAILED for item in checked)
    if not failed:
        for name, args, env, exercised in steps():
            if name == "setup-client" and kept.get(f"deploy/{client()}/identities.yaml") is not None:
                report["steps"].append(asdict(Result(name, PASSED, detail="existing profile and identities kept unchanged")))
                continue
            item = timed(name, lambda args=args, env=env: run_command(args, env), exercised)
            report["steps"].append(asdict(item))
            if item.result == FAILED:
                failed = True
                break
    if not failed:
        current = kept.get(f"releases/{client()}/current")
        report["released_hash"] = current.decode().strip() if current else None
        item = golden_check(report)
        report["steps"].append(asdict(item))
        failed = item.result == FAILED
    if not failed:
        for name, call, exercised in (
            ("catalogue", catalogue_check, ("catalogue service", "released artifact", "example pack")),
            ("data", data_check, ("data preview service", "data query service", "cross-unit refusal")),
        ):
            item = timed(name, call, exercised)
            report["steps"].append(asdict(item))
            if item.result == FAILED:
                failed = True
                break
    report["result"] = FAILED if failed else PASSED
    print(markdown(report), flush=True)
    saved = save(kept, report)
    return 1 if failed or not saved else 0
