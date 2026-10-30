"""Read small, declarative semantic folders without executing their contents.

A client figure's bounds (FIGURE_DIGITS) are onboard.py's FIGURE_LIMIT, the same 10**18. RESERVED names onboarding's own
state beside a unit's inputs, which no uploaded file may carry.
"""
from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import math
import re
import tokenize
from pathlib import Path

import yaml
import sqlglot
from sqlglot import exp

from kge.estate import ddl
from kge.estate.from_files import FRONT, META

MAX_FILE = 256 * 1024
MAX_FILES = 50
MAX_DOCS = 20
MAX_DOC = 64 * 1024
MAX_GOLDEN_FILES = 10
MAX_GOLDEN_ITEMS = 100
MAX_GOLDEN_SQL = 16 * 1024
FIGURE_DIGITS, FIGURE_PLACES = 18, 6
MODEL_PROMPT_BYTES = 128 * 1024
RESERVED = {"proposal.json"}
DICT_NAMES = {"data-dictionary.yml", "data-dictionary.yaml", "data-dictionary.json"}
GOLDEN_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
TYPE_MAP = {"integer": "BIGINT", "int": "BIGINT", "number": "DECIMAL(18,2)", "decimal": "DECIMAL(18,2)",
            "string": "VARCHAR", "text": "VARCHAR", "date": "DATE", "time": "TIMESTAMP",
            "timestamp": "TIMESTAMP", "yesno": "BOOLEAN", "boolean": "BOOLEAN"}
OSI_TYPES = {"String": "VARCHAR", "Integer": "BIGINT", "Decimal": "DECIMAL(18,2)", "Float": "DOUBLE",
             "Boolean": "BOOLEAN", "Date": "DATE", "Time": "TIMESTAMP", "DateTime": "TIMESTAMP",
             "DateTimeTz": "TIMESTAMP"}

OPS = {exp.EQ: "=", exp.NEQ: "!=", exp.LT: "<", exp.LTE: "<=", exp.GT: ">", exp.GTE: ">=", exp.Like: "LIKE"}


def restore_proposal(value):
    if isinstance(value, dict) and set(value) == {"__bytes__"}:
        import base64
        return base64.b64decode(value["__bytes__"])
    if isinstance(value, dict):
        return {key: restore_proposal(item) for key, item in value.items()}
    if isinstance(value, list):
        return [restore_proposal(item) for item in value]
    return value


def _literal(node, dtype: str):
    """Return a typed scalar. SQL is parsed first so a value can never become syntax."""
    if isinstance(node, exp.Boolean):
        value = bool(node.this)
    elif isinstance(node, exp.Literal):
        try:
            value = node.this if node.is_string else (float(node.this) if any(x in node.this.lower() for x in (".", "e")) else int(node.this))
        except (ValueError, OverflowError):
            raise SemanticError("filter value is not a finite number") from None
    elif isinstance(node, exp.Null):
        value = None
    else:
        raise SemanticError("filter value must be a literal")
    upper = dtype.upper()
    if isinstance(value, float) and not math.isfinite(value):
        raise SemanticError("filter value is not a finite number")
    if upper.startswith(("INT", "BIGINT", "SMALLINT", "DECIMAL", "NUMERIC", "DOUBLE", "REAL", "FLOAT")) and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise SemanticError("filter value does not match the numeric column")
    if upper == "BOOLEAN" and not isinstance(value, bool):
        raise SemanticError("filter value does not match the boolean column")
    if upper.startswith(("VARCHAR", "CHAR", "DATE", "TIMESTAMP")) and not isinstance(value, str):
        raise SemanticError("filter value does not match the text or date column")
    if upper == "DATE":
        try: parsed = dt.date.fromisoformat(value)
        except ValueError: raise SemanticError("filter value does not match the date column") from None
        if parsed.isoformat() != value: raise SemanticError("filter value does not match the date column")
    if upper.startswith("TIMESTAMP"):
        try: dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError: raise SemanticError("filter value does not match the timestamp column") from None
    return value


def parse_conditions(value, tables: list[dict]) -> list[dict]:
    """Parse the deliberately small recipe language into column/op/value nodes."""
    columns = {(t["name"], c["name"]): c["type"] for t in tables for c in t["columns"]}
    bare = {c: (t, typ) for (t, c), typ in columns.items()}
    ambiguous = {c for c in bare if sum(1 for _, name in columns if name == c) > 1}

    def resolve(node):
        if not isinstance(node, exp.Column):
            raise SemanticError("filter left side must be a column; functions are not drafted")
        table, name = node.table or None, node.name
        if table is None:
            if name in ambiguous or name not in bare:
                raise SemanticError(f"filter column {name} is unknown or ambiguous")
            table, dtype = bare[name]
        else:
            dtype = columns.get((table, name))
            if dtype is None:
                raise SemanticError(f"filter column {table}.{name} is unknown")
        return table, name, dtype

    def one(node):
        if isinstance(node, exp.And):
            return one(node.left) + one(node.right)
        if isinstance(node, exp.Or):
            raise SemanticError("OR conditions are not drafted")
        if type(node) in OPS:
            table, column, dtype = resolve(node.left)
            return [{"table": table, "column": column, "op": OPS[type(node)], "value": _literal(node.right, dtype)}]
        if isinstance(node, exp.In):
            if node.args.get("query") is not None:
                raise SemanticError("subqueries are not drafted")
            table, column, dtype = resolve(node.this)
            values = [_literal(x, dtype) for x in node.expressions]
            if not values:
                raise SemanticError("IN needs at least one value")
            return [{"table": table, "column": column, "op": "NOT IN" if node.args.get("not") else "IN", "value": values}]
        if isinstance(node, exp.Not) and isinstance(node.this, exp.In):
            result = one(node.this); result[0]["op"] = "NOT IN"; return result
        if isinstance(node, exp.Not) and isinstance(node.this, exp.Like):
            result = one(node.this); result[0]["op"] = "NOT LIKE"; return result
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            table, column, _ = resolve(node.this)
            return [{"table": table, "column": column, "op": "IS NULL", "value": None}]
        if isinstance(node, exp.Not) and isinstance(node.this, exp.Is) and isinstance(node.this.expression, exp.Null):
            table, column, _ = resolve(node.this.this)
            return [{"table": table, "column": column, "op": "IS NOT NULL", "value": None}]
        raise SemanticError("filter uses a function or unsupported condition")

    if isinstance(value, list):
        nodes = []
        for raw in value:
            if not isinstance(raw, dict) or set(raw) != {"column", "op", "value"}:
                raise SemanticError("filter objects need exactly column, op and value")
            op = str(raw["op"]).upper()
            if op not in {"=", "!=", "<", "<=", ">", ">=", "IN", "NOT IN", "IS NULL", "IS NOT NULL"}:
                raise SemanticError(f"filter operator {op} is not supported")
            col = str(raw["column"])
            rendered = f"{col} {op}"
            if op not in {"IS NULL", "IS NOT NULL"}:
                vals = raw["value"] if op in {"IN", "NOT IN"} else [raw["value"]]
                if not isinstance(vals, list): raise SemanticError("IN filter value must be a list")
                def quote(x): return "'" + x.replace("'", "''") + "'" if isinstance(x, str) else str(x).upper() if isinstance(x, bool) else str(x)
                rendered += " (" + ", ".join(quote(x) for x in vals) + ")" if op in {"IN", "NOT IN"} else " " + quote(vals[0])
            nodes += parse_conditions(rendered, tables)
        return nodes
    if not isinstance(value, str) or not value.strip():
        raise SemanticError("filter must be a condition")
    try:
        tree = sqlglot.parse_one(value, read="duckdb")
    except (sqlglot.errors.ParseError, RecursionError, ValueError, OverflowError, tokenize.TokenError):
        raise SemanticError("filter is not a plain condition") from None
    if any(isinstance(x, (exp.Select, exp.Subquery)) for x in tree.walk()):
        raise SemanticError("subqueries are not drafted")
    return one(tree)


class SemanticError(ddl.DdlError):
    pass


class NoAliasLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()
            raise SemanticError(f"YAML line {event.start_mark.line + 1}: aliases are refused")
        event = self.peek_event()
        if getattr(event, "anchor", None):
            raise SemanticError(f"YAML line {event.start_mark.line + 1}: anchors are refused")
        return super().compose_node(parent, index)


class FigureLoader(NoAliasLoader):
    """A golden file's loader: a figure written as a bare number is read from its own text as a Decimal, so
    12345678901234567.89 keeps every digit a float would round away."""


def _decimal_scalar(loader, node):
    try:
        return decimal.Decimal(loader.construct_scalar(node).replace("_", ""))
    except decimal.InvalidOperation:
        raise SemanticError(f"YAML line {node.start_mark.line + 1}: {node.value!r} is not a decimal number") from None


FigureLoader.add_constructor("tag:yaml.org,2002:float", _decimal_scalar)


def _yaml(name: str, data: bytes, loader=NoAliasLoader):
    text = _text(name, data)
    if "{{" in text or "{%" in text:
        line = next(i for i, value in enumerate(text.splitlines(), 1) if "{{" in value or "{%" in value)
        raise SemanticError(f"{name}:{line}: templates are refused")
    try:
        return yaml.load(text, Loader=loader) or {}
    except SemanticError as exc:
        raise SemanticError(f"{name}:{str(exc).removeprefix('YAML line ')}") from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise SemanticError(f"{name}:{mark.line + 1 if mark else 1}: invalid YAML") from None


def _text(name, data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SemanticError(f"{name}:{exc.start + 1}: UTF-8 text is required") from None


def read_folder(folder: Path) -> dict[str, bytes]:
    folder = folder.resolve()
    if not folder.is_dir():
        raise SemanticError(f"{folder}: semantic folder is not a directory")
    out = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise SemanticError(f"{path.relative_to(folder)}: symlinks are refused")
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel in RESERVED:
            raise SemanticError(f"{rel}: the name is reserved for onboarding's own state; rename the file")
        allowed = (rel == "dbt_project.yml" or rel in DICT_NAMES or
                   (rel.startswith("models/") and len(Path(rel).parts) <= 4 and Path(rel).suffix in {".yml", ".yaml", ".sql"}) or
                   (len(Path(rel).parts) == 2 and rel.startswith("seeds/") and rel.endswith(".csv")) or
                   (len(Path(rel).parts) == 2 and rel.startswith("golden/") and Path(rel).suffix.lower() in {".yaml", ".yml", ".json"}) or
                   (len(Path(rel).parts) == 2 and rel.startswith("docs/") and rel.endswith(".md")) or
                   (len(Path(rel).parts) == 1 and Path(rel).suffix.lower() in {".md", ".yaml", ".yml", ".json"}) or
                   rel.endswith(".view.lkml") or rel.endswith(".model.lkml"))
        if not allowed:
            raise SemanticError(f"{rel}: path is outside the semantic-folder allowlist")
        if len(out) >= MAX_FILES:
            raise SemanticError(f"{rel}: semantic folder has more than {MAX_FILES} files")
        limit = MAX_DOC if rel.startswith("docs/") else ddl.MAX_CSV_BYTES if rel.startswith("seeds/") else MAX_FILE
        data = path.read_bytes()
        if len(data) > limit:
            raise SemanticError(f"{rel}: file is larger than {limit} bytes")
        out[rel] = data
    return out


def _golden(files: dict[str, bytes]) -> list[dict]:
    """Read the deliberately small client-golden shape; SQL remains inert text here."""
    names = sorted(n for n in files if n.startswith("golden/"))
    if len(names) > MAX_GOLDEN_FILES:
        raise SemanticError(f"golden: more than {MAX_GOLDEN_FILES} files")
    items, seen = [], set()
    for name in names:
        raw = _object(name, files[name], figures=True)
        if not isinstance(raw, list):
            raise SemanticError(f"{name}: golden file must be a list")
        if len(items) + len(raw) > MAX_GOLDEN_ITEMS:
            raise SemanticError(f"golden: more than {MAX_GOLDEN_ITEMS} items")
        for number, item in enumerate(raw, 1):
            at = f"{name}:{number}"
            if not isinstance(item, dict):
                raise SemanticError(f"{at}: golden item must be an object")
            allowed = {"id", "question", "expect", "sql", "source", "tolerance"}
            unknown = set(item) - allowed
            if unknown:
                raise SemanticError(f"{at}: unknown golden field(s): {', '.join(sorted(unknown))}")
            gid = item.get("id")
            if not isinstance(gid, str) or not GOLDEN_ID.fullmatch(gid):
                raise SemanticError(f"{at}: id must match {GOLDEN_ID.pattern}")
            if gid in seen:
                raise SemanticError(f"{at}: duplicate golden id {gid}")
            seen.add(gid)
            question, source, expect = item.get("question"), item.get("source"), item.get("expect")
            if not isinstance(question, str) or not question.strip() or len(question) > 500:
                raise SemanticError(f"{at}: question must be 1 to 500 characters")
            if not isinstance(source, str) or not source.strip() or len(source) > 500:
                raise SemanticError(f"{at}: source must be 1 to 500 characters")
            if not isinstance(expect, dict) or set(expect) not in ({"figure"}, {"behaviour"}):
                raise SemanticError(f"{at}: expect must contain exactly figure or behaviour")
            sql = item.get("sql")
            if "figure" in expect:
                if not isinstance(sql, str) or not sql.strip():
                    raise SemanticError(f"{at}: a figure needs reference SQL")
                if len(sql.encode()) > MAX_GOLDEN_SQL:
                    raise SemanticError(f"{at}: SQL is larger than {MAX_GOLDEN_SQL} bytes")
                figure, tolerance = _figure(at, "figure", expect["figure"]), _figure(at, "tolerance", item.get("tolerance", "0"))
                if tolerance < 0:
                    raise SemanticError(f"{at}: tolerance must not be negative")
                item = {**item, "expect": {"figure": str(figure)}, "tolerance": str(tolerance)}
            else:
                if expect["behaviour"] not in {"abstain", "refuse"}:
                    raise SemanticError(f"{at}: behaviour must be abstain or refuse")
                if sql is not None:
                    raise SemanticError(f"{at}: an abstain or refuse item must not carry SQL")
                if "tolerance" in item:
                    raise SemanticError(f"{at}: tolerance belongs only to a figure")
            items.append({**item, "file": name})
    return items


def _figure(at: str, field: str, value) -> decimal.Decimal:
    """A client's figure or tolerance, bounded before any arithmetic: at most FIGURE_DIGITS digits before the point and
    FIGURE_PLACES after it, so a figure with an exponent of a billion is refused here rather than formatted into a gigabyte of digits later."""
    if isinstance(value, bool) or not isinstance(value, (int, decimal.Decimal, str)) or len(str(value)) > 64:
        raise SemanticError(f"{at}: {field} must be a decimal number, written as a quoted string if it has many digits")
    try:
        number = decimal.Decimal(str(value).strip())
    except decimal.InvalidOperation:
        raise SemanticError(f"{at}: {field} must be a decimal number") from None
    if not number.is_finite():
        raise SemanticError(f"{at}: {field} must be finite")
    if number and (number.adjusted() >= FIGURE_DIGITS or -number.as_tuple().exponent > FIGURE_PLACES):
        raise SemanticError(f"{at}: {field} must have at most {FIGURE_DIGITS} digits before the point and {FIGURE_PLACES} after it")
    return number


def _ident(name, file="semantic", line=1):
    if not isinstance(name, str) or not ddl.IDENT.fullmatch(name):
        raise SemanticError(f"{file}:{line}: identifier {name!r} must match {ddl.IDENT.pattern}")
    return name


def _dtype(value, file, line=1):
    raw = str(value).strip()
    mapped = TYPE_MAP.get(raw.lower(), raw.upper())
    if not any(mapped == t or mapped.startswith(t + "(") for t in ddl.TYPES):
        raise SemanticError(f"{file}:{line}: type {raw!r} is refused")
    return mapped


def _documents(files):
    docs = []
    names = [n for n in files if n.startswith("docs/")]
    if len(names) > MAX_DOCS:
        raise SemanticError(f"docs: more than {MAX_DOCS} documents")
    for name in names:
        text = _text(name, files[name])
        match = FRONT.match(text)
        if not match:
            raise SemanticError(f"{name}:1: document needs YAML front matter")
        meta = _yaml(name, match.group(1).encode())
        unknown = sorted(set(meta) - set(META))
        if unknown or not meta.get("title") or not meta.get("kind"):
            raise SemanticError(f"{name}:2: invalid document front matter")
        docs.append({"name": Path(name).name, "meta": meta, "body": text[match.end():].strip()})
    return docs


def _doc_hints(documents, measures):
    missed = []
    for doc in documents:
        sections = re.split(r"(?m)^## +(.+?)\s*$", doc["body"])
        for i in range(1, len(sections), 2):
            heading, body = sections[i].strip(), sections[i + 1].strip()
            found = next((m for m in measures if heading.lower() in {m["name"].lower(), m["term"].lower()}), None)
            if not found:
                missed.append({"document": doc["name"], "heading": heading, "why": "no declared metric matches the heading"}); continue
            paragraphs = re.split(r"\n\s*\n", body)
            if not found.get("definition") and paragraphs:
                found["definition"] = paragraphs[0].strip()
            aliases = re.findall(r"(?mi)^Also called:\s*(.+)$", body)
            found.setdefault("aliases", []).extend(a.strip() for line in aliases for a in line.split(",") if a.strip())
            found["from"] = doc["name"]
    return missed


def _dbt(files, unit):
    _yaml("dbt_project.yml", files["dbt_project.yml"])
    tables, semantic_models, metrics, ignored = [], [], [], ["dbt_project.yml beyond name"]
    for name, data in sorted(files.items()):
        if name.endswith(".sql"):
            ignored.append(name); continue
        if not name.startswith("models/") or Path(name).suffix not in {".yml", ".yaml"}: continue
        doc = _yaml(name, data)
        for raw in doc.get("models", []) or []:
            tname = _ident(raw.get("name"), name); cols=[]; pk=[]; fks=[]
            for col in raw.get("columns", []) or []:
                cname=_ident(col.get("name"), name)
                if not col.get("data_type"): raise SemanticError(f"{name}:1: column {cname} has no data_type")
                tests=col.get("data_tests", col.get("tests", [])) or []
                constraints=[x.get("type") if isinstance(x,dict) else x for x in col.get("constraints",[]) or []]
                if "primary_key" in constraints or ("unique" in tests and "not_null" in tests): pk.append(cname)
                for test in tests:
                    rel=test.get("relationships") if isinstance(test,dict) else None
                    if rel:
                        match=re.fullmatch(r"ref\('([a-z][a-z0-9_]{0,62})'\)", str(rel.get("to")))
                        if not match: raise SemanticError(f"{name}:1: relationships.to is refused")
                        fks.append((cname, match.group(1), _ident(rel.get("field"),name)))
                cols.append({"name":cname,"type":_dtype(col["data_type"],name),"constraints":[],"comment":col.get("description")})
            constraints=[]
            if len(pk)==1: constraints.append(f"PRIMARY KEY ({pk[0]})")
            constraints += [f"FOREIGN KEY ({a}) REFERENCES {b} ({c})" for a,b,c in fks]
            tables.append({"name":tname,"columns":cols,"constraints":constraints,"comment":raw.get("description")})
        semantic_models += doc.get("semantic_models",[]) or []; metrics += doc.get("metrics",[]) or []
        ignored += [f"{name}:{k}" for k in ("sources","saved_queries","exposures") if doc.get(k)]
    by_table={t["name"]:t for t in tables}; declared={}; times={}
    for sm in semantic_models:
        table=_ident(sm.get("name"),"semantic model")
        times[table]=sm.get("defaults",{}).get("agg_time_dimension")
        for m in sm.get("measures",[]) or []: declared[m.get("name")]=(table,m)
    measures=[]; not_drafted=[]
    for metric in metrics:
        name=metric.get("name","metric")
        if metric.get("type") != "simple":
            not_drafted.append({"metric":name,"why":"only simple metrics are drafted"}); continue
        table, measure=declared.get(metric.get("type_params",{}).get("measure"),(None,None))
        if not measure or measure.get("agg") != "sum" or not ddl.IDENT.fullmatch(str(measure.get("expr",""))):
            not_drafted.append({"metric":name,"why":"measure must be sum over a bare column"}); continue
        column=measure["expr"]; col=next((c for c in by_table.get(table,{}).get("columns",[]) if c["name"]==column),None)
        if not col or not col["type"].startswith(("DECIMAL","NUMERIC","DOUBLE","REAL","FLOAT","INT","BIGINT","SMALLINT")):
            not_drafted.append({"metric":name,"why":"measure column is absent or non-numeric"}); continue
        currency=(metric.get("meta") or {}).get("currency") or (column.rsplit("_",1)[-1].upper() if re.search(r"_[a-z]{3}$",column) else None)
        if not currency:
            not_drafted.append({"metric":name,"why":"currency is not declared"}); continue
        try: filters = parse_conditions(metric.get("filter"), tables) if metric.get("filter") else []
        except SemanticError as exc: not_drafted.append({"metric":name,"why":str(exc)}); continue
        nad = measure.get("non_additive_dimension") or {}
        grain = "period_end" if nad.get("window_choice") in {"max", "min"} else None
        measures.append({"table":table,"column":column,"term":_ident(name,"metric"),"name":metric.get("label") or name.replace("_"," ").title(),
                         "definition":metric.get("description"),"currency":str(currency).upper(),"time":times.get(table),"aliases":[],
                         "filters":filters,"grain":grain})
    drafted={(m["table"],m["column"]) for m in measures}
    for table in tables:
        for col in table["columns"]:
            if re.search(r"_[a-z]{3}$",col["name"]) and (table["name"],col["name"]) not in drafted:
                not_drafted.append({"column":f"{table['name']}.{col['name']}","why":"currency-shaped column has no declared metric"})
    return tables, measures, ignored, not_drafted


def _dictionary(files, unit, name):
    raw = json.loads(_text(name,files[name])) if name.endswith(".json") else _yaml(name,files[name])
    tables=[]; measures=[]; not_drafted=[]
    for table in raw.get("tables",[]) or []:
        tname=_ident(table.get("name"),name); cols=[]; pk=[]
        for col in table.get("columns",[]) or []:
            cname=_ident(col.get("name"),name); cols.append({"name":cname,"type":_dtype(col.get("type"),name),"constraints":[],"comment":col.get("description")})
            if col.get("key") == "primary": pk.append(cname)
            if col.get("measure"):
                m=col["measure"] or {}; currency=m.get("currency")
                if not currency: not_drafted.append({"column":f"{tname}.{cname}","why":"currency is not declared"})
                elif not cols[-1]["type"].startswith(("DECIMAL","NUMERIC","DOUBLE","REAL","FLOAT","INT","BIGINT","SMALLINT")):
                    not_drafted.append({"column":f"{tname}.{cname}","why":"measure column is non-numeric"})
                else:
                    try: filters = parse_conditions(m.get("filters", []), tables + [{"name":tname,"columns":cols}])
                    except SemanticError as exc: not_drafted.append({"metric":m.get("term") or cname,"why":str(exc)}); continue
                    measures.append({"table":tname,"column":cname,"term":m.get("term") or cname,"name":m.get("label") or cname.replace("_"," ").title(),"definition":col.get("description"),"currency":str(currency).upper(),"time":m.get("period_column"),"aliases":[],"filters":filters,"grain":m.get("grain")})
        tables.append({"name":tname,"columns":cols,"constraints":[f"PRIMARY KEY ({pk[0]})"] if len(pk)==1 else [],"comment":table.get("description")})
    return tables,measures,[name+": metadata beyond the documented shape"],not_drafted


def _object(name, data, figures: bool = False):
    """figures: read bare decimal numbers as Decimal from their own text (a golden file's), not as floats."""
    if name.endswith(".json"):
        try:
            return json.loads(_text(name, data), parse_float=decimal.Decimal if figures else None)
        except (json.JSONDecodeError, RecursionError, decimal.InvalidOperation):
            raise SemanticError(f"{name}: invalid JSON") from None
    return _yaml(name, data, FigureLoader if figures else NoAliasLoader)


def _osi(files, unit, name, raw):
    """Apache Ossie core 0.2.0.dev0, apache/ossie commit 53418ba; the spec is cited in the README, not copied here."""
    if not isinstance(raw, dict) or raw.get("version") != "0.2.0.dev0" or not isinstance(raw.get("name"), str):
        raise SemanticError(f"{name}: OSI needs version 0.2.0.dev0 and name")
    datasets = raw.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise SemanticError(f"{name}: OSI datasets must be a non-empty array")
    tables, measures, not_drafted = [], [], []
    by_name = {}
    for dataset in datasets:
        if not isinstance(dataset, dict) or not isinstance(dataset.get("source"), str) or not dataset["source"].strip():
            raise SemanticError(f"{name}: each OSI dataset needs name and source")
        tname = _ident(dataset.get("name"), name); cols = []
        fields = dataset.get("fields", [])
        if not isinstance(fields, list): raise SemanticError(f"{name}: OSI fields must be an array")
        for field in fields:
            cname = _ident(field.get("name") if isinstance(field, dict) else None, name)
            exprs = ((field.get("expression") or {}).get("dialects") or [])
            if not any(isinstance(e, dict) and e.get("expression") == cname for e in exprs):
                raise SemanticError(f"{name}: OSI field {tname}.{cname} needs a bare-column expression")
            dtype = field.get("datatype")
            if dtype not in OSI_TYPES:
                raise SemanticError(f"{name}: OSI field {tname}.{cname} has unsupported datatype {dtype!r}")
            cols.append({"name": cname, "type": OSI_TYPES[dtype], "constraints": [], "comment": field.get("description")})
        known = {c["name"] for c in cols}
        pk = dataset.get("primary_key", []) or []
        unique = dataset.get("unique_keys", []) or []
        if not isinstance(pk, list) or any(x not in known for x in pk): raise SemanticError(f"{name}: OSI primary_key names an unknown field")
        if not isinstance(unique, list) or any(not isinstance(k, list) or not k or any(x not in known for x in k) for k in unique):
            raise SemanticError(f"{name}: OSI unique_keys are invalid")
        constraints = ([f"PRIMARY KEY ({', '.join(pk)})"] if pk else []) + [f"UNIQUE ({', '.join(k)})" for k in unique if k != pk]
        table = {"name": tname, "source": dataset["source"], "columns": cols, "constraints": constraints,
                 "comment": dataset.get("description")}
        tables.append(table); by_name[tname] = table
    for rel in raw.get("relationships", []) or []:
        if not isinstance(rel, dict): raise SemanticError(f"{name}: OSI relationship must be an object")
        left, right = rel.get("from"), rel.get("to"); a, b = rel.get("from_columns"), rel.get("to_columns")
        if left not in by_name or right not in by_name or not isinstance(a, list) or not isinstance(b, list) or not a or len(a) != len(b):
            raise SemanticError(f"{name}: OSI relationship {rel.get('name')!r} is invalid")
        if any(x not in {c['name'] for c in by_name[left]['columns']} for x in a) or any(x not in {c['name'] for c in by_name[right]['columns']} for x in b):
            raise SemanticError(f"{name}: OSI relationship {rel.get('name')!r} names an unknown field")
        by_name[left]["constraints"].append(f"FOREIGN KEY ({', '.join(a)}) REFERENCES {right} ({', '.join(b)})")
    for metric in raw.get("metrics", []) or []:
        mid = metric.get("name", "metric") if isinstance(metric, dict) else "metric"
        exprs = ((metric.get("expression") or {}).get("dialects") or []) if isinstance(metric, dict) else []
        expressions = [e.get("expression") for e in exprs if isinstance(e, dict)]
        match = next((m for x in expressions if (m := re.fullmatch(
            r"SUM\(\s*([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\s*\)(?:\s+FILTER\s*\(\s*WHERE\s+(.+)\))?", str(x), re.I))), None)
        if not match:
            not_drafted.append({"metric": mid, "why": "metric must be a simple aggregate sum of one field"}); continue
        table, column, condition = match.groups(); col = next((c for c in by_name.get(table, {}).get("columns", []) if c["name"] == column), None)
        if not col or not col["type"].startswith(("DECIMAL", "NUMERIC", "DOUBLE", "REAL", "FLOAT", "INT", "BIGINT", "SMALLINT")):
            not_drafted.append({"metric": mid, "why": "measure column is absent or non-numeric"}); continue
        context = metric.get("ai_context") or {}; currency = context.get("currency") if isinstance(context, dict) else None
        currency = currency or (column.rsplit("_", 1)[-1].upper() if re.search(r"_[a-z]{3}$", column) else None)
        if not currency:
            not_drafted.append({"metric": mid, "why": "currency is not declared"}); continue
        aliases = context.get("synonyms", []) if isinstance(context, dict) else []
        if not isinstance(aliases, list) or any(not isinstance(x, str) for x in aliases):
            raise SemanticError(f"{name}: OSI metric {mid} synonyms must be text")
        time = context.get("period_column") if isinstance(context, dict) else None
        try: filters = parse_conditions(condition, tables) if condition else []
        except SemanticError as exc: not_drafted.append({"metric":mid,"why":str(exc)}); continue
        grain = context.get("grain") if isinstance(context, dict) else None
        measures.append({"table": table, "column": column, "term": _ident(mid, name), "name": mid.replace("_", " ").title(),
                         "definition": metric.get("description"), "currency": str(currency).upper(), "time": time, "aliases": aliases,
                         "filters":filters,"grain":grain})
    return tables, measures, [name + ": OSI metadata outside the supported mapping"], not_drafted


def _model_read(files, name):
    """Bound a model-read file before any model sees it; the flow bounds each step's whole prompt as well."""
    if len(files[name]) > MODEL_PROMPT_BYTES: raise SemanticError(f"{name}: semantic file is too large for the model")
    _text(name, files[name])


def _lookml(files, unit):
    """Read LookML views. A negated filter value is not drafted: Looker reads "-x" as "is not x" and keeps the rows
    where the field is NULL, an OR with IS NULL that a recipe (AND only) cannot say, and a plain != would drop those
    rows."""
    tables=[]; measures=[]; ignored=[]; not_drafted=[]; views={}
    for name,data in sorted(files.items()):
        if not name.endswith(".view.lkml"): continue
        text=_text(name,data)
        if "{{" in text or "{%" in text: raise SemanticError(f"{name}:1: templates are refused")
        for vm in re.finditer(r"(?ms)^view:\s*(\w+)\s*\{(.*?)^\}",text):
            v=_ident(vm.group(1),name,text[:vm.start()].count("\n")+1); body=vm.group(2)
            stm=re.search(r"sql_table_name:\s*([\w.]+)\s*;;",body)
            if not stm: raise SemanticError(f"{name}:{text[:vm.start()].count(chr(10))+1}: view needs sql_table_name")
            table=_ident(stm.group(1).split(".")[-1],name); cols=[]; pk=[]
            for dm in re.finditer(r"(?ms)^\s*dimension(?:_group)?:\s*(\w+)\s*\{(.*?)^\s*\}",body):
                c=_ident(dm.group(1),name); block=dm.group(2); typ=re.search(r"\btype:\s*(\w+)",block); sql=re.search(r"\bsql:\s*([^;]+);;",block)
                if not typ or not sql or sql.group(1).strip() not in {f"${{TABLE}}.{c}",f"${{{c}}}"}: raise SemanticError(f"{name}:{text[:vm.start()+dm.start()].count(chr(10))+1}: dimension is outside the safe subset")
                desc=re.search(r'\b(?:description|label):\s*"([^"]*)"',block); cols.append({"name":c,"type":_dtype(typ.group(1),name),"constraints":[],"comment":desc.group(1) if desc else None})
                if re.search(r"\bprimary_key:\s*yes",block): pk.append(c)
            views[v]=(table,cols); tables.append({"name":table,"columns":cols,"constraints":[f"PRIMARY KEY ({pk[0]})"] if len(pk)==1 else [],"comment":None})
            for mm in re.finditer(r"(?ms)^\s*measure:\s*(\w+)\s*\{(.*?)^\s*\}",body):
                mid=_ident(mm.group(1),name); block=mm.group(2); typ=re.search(r"\btype:\s*(\w+)",block); sql=re.search(r"\bsql:\s*([^;]+);;",block)
                expr=sql.group(1).strip() if sql else ""
                col=expr.removeprefix("${TABLE}.").removeprefix("${").removesuffix("}")
                if not typ or typ.group(1)!="sum" or not ddl.IDENT.fullmatch(col): not_drafted.append({"metric":mid,"why":"measure must be sum over ${TABLE}.column or ${column}"}); continue
                fmt=re.search(r"\bvalue_format_name:\s*(\w+)",block); currency=fmt.group(1).upper() if fmt and fmt.group(1).lower() in {"gbp","usd","eur"} else None
                if not currency: not_drafted.append({"metric":mid,"why":"value_format_name must be gbp, usd or eur"}); continue
                label=re.search(r'\blabel:\s*"([^"]*)"',block); desc=re.search(r'\bdescription:\s*"([^"]*)"',block)
                filter_match = re.search(r"(?ms)\bfilters:\s*\[(.*?)\]", block)
                raw_filters = re.findall(r'(\w+)\s*:\s*"([^"]*)"', filter_match.group(1) if filter_match else "")
                def lookml_filter(field: str, value: str) -> str:
                    if "," in value:
                        raise SemanticError("LookML comma filters are OR conditions and are not drafted")
                    if value.upper() == "NULL":
                        return f"{field} IS NULL"
                    if value.upper() == "-NULL":
                        return f"{field} IS NOT NULL"
                    if value.startswith("-"):
                        raise SemanticError(f"LookML's {value} keeps NULL rows (an OR condition) and is not drafted")
                    op = "LIKE" if "%" in value else "="
                    quoted = "'" + value.replace("'", "''") + "'"
                    return f"{field} {op} {quoted}"
                try: condition = " AND ".join(lookml_filter(field, value) for field, value in raw_filters)
                except SemanticError as exc: not_drafted.append({"metric":mid,"why":str(exc)}); continue
                try: filters = parse_conditions(condition, tables) if condition else []
                except SemanticError as exc: not_drafted.append({"metric":mid,"why":str(exc)}); continue
                measures.append({"table":table,"column":col,"term":mid,"name":label.group(1) if label else mid.replace("_"," ").title(),"definition":desc.group(1) if desc else None,"currency":currency,"time":None,"aliases":[],"filters":filters,"grain":None})
    for name,data in sorted(files.items()):
        if not name.endswith(".model.lkml"): continue
        text=_text(name,data)
        for jm in re.finditer(r"(?ms)join:\s*(\w+)\s*\{(.*?)\}",text):
            target=jm.group(1); sql=re.search(r"sql_on:\s*\$\{(\w+)\.(\w+)\}\s*=\s*\$\{(\w+)\.(\w+)\}\s*;;",jm.group(2))
            if not sql or target not in views or sql.group(1) not in views or sql.group(3) not in views: raise SemanticError(f"{name}:{text[:jm.start()].count(chr(10))+1}: join is outside the safe subset or names an unknown view")
            a,ac,b,bc=sql.groups(); child=next(t for t in tables if t["name"]==views[a][0]); child["constraints"].append(f"FOREIGN KEY ({ac}) REFERENCES {views[b][0]} ({bc})")
    return tables,measures,ignored,not_drafted


def proposal_inputs(files: dict[str, bytes]) -> dict[str, str]:
    """What a cached proposal is keyed by: each kept semantic file's sha256 (onboarding keeps no .sql model file)."""
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items()) if not name.endswith(".sql")}


def restore(held: bytes, files: dict[str, bytes], unit: str) -> dict | None:
    """The proposal onboarding cached at create (units/<unit>/proposal.json, outside the uploaded folder's namespace),
    checked again rather than trusted: its keys must be the files' hashes, its tables pass the DDL parser, its measures
    and their conditions the same checks a model's reply passes (kge/estate/draftflow.py), and the files' own golden items,
    documents and seeds are read from the files, never from the cache. None when it does not match or fails a check:
    the caller parses the files again."""
    try:
        data = json.loads(held)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("inputs") != proposal_inputs(files) or not isinstance(data.get("proposal"), dict):
        return None
    try:
        return _rechecked(restore_proposal(data["proposal"]), files, unit)
    except (ddl.DdlError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        import logging
        logging.getLogger(__name__).warning("cached proposal for %s refused on its checks: %s", unit, exc)
        return None


def _condition_text(node: dict) -> str:
    left, op, value = f"{node['table']}.{node['column']}", node["op"], node.get("value")
    quote = lambda x: ("'" + x.replace("'", "''") + "'" if isinstance(x, str)   # noqa: E731
                       else str(x).upper() if isinstance(x, bool) else repr(x) if isinstance(x, float) else str(x))
    if op in {"IS NULL", "IS NOT NULL"}:
        return f"{left} {op}"
    if op in {"IN", "NOT IN"}:
        return f"{left} {op} (" + ", ".join(quote(x) for x in value) + ")"
    return f"{left} {op} {quote(value)}"


def _rechecked(proposal: dict, files: dict[str, bytes], unit: str) -> dict:
    from kge.estate import draftflow
    fmt = proposal["format"]
    if fmt not in {"dbt", "lookml", "dictionary", "osi", "model"}:
        raise ValueError(f"unknown format {fmt!r}")
    model = ddl.parse(ddl.render(proposal["model"]), unit)
    tables = proposal["model"]["tables"]
    hints = proposal["hints"]
    measures = []
    for m in hints["measures"]:
        checked, gaps = draftflow.check_measures({"measures": [{**m, "filters": []}]}, tables, "proposal.json")
        if gaps or len(checked) != 1:
            raise ValueError(f"measure {m.get('term')!r} does not pass its checks")
        filters = []
        for node in m["filters"]:
            again = parse_conditions(_condition_text(node), tables)
            if again != [node]:
                raise ValueError(f"a condition of {m.get('term')!r} does not read back as itself")
            filters.append(again[0])
        if "from" in m and not isinstance(m["from"], str):
            raise ValueError("a measure's document name is text")
        measures.append({**checked[0], "filters": filters, **({"from": m["from"]} if "from" in m else {})})
    golden = draftflow.check_golden({"golden": hints["golden"]}, measures, "proposal.json")
    texts = lambda rows: all(isinstance(r, dict) and all(isinstance(v, (str, int, type(None))) for v in r.values())  # noqa: E731
                             for r in rows)
    not_drafted, outcomes, made_by = hints["not_drafted"], hints["outcomes"], hints["made_by"]
    if not (isinstance(not_drafted, list) and texts(not_drafted) and isinstance(outcomes, list) and texts(outcomes)
            and isinstance(made_by, dict) and texts(made_by.values()) and isinstance(proposal["ignored"], list)
            and all(isinstance(x, str) for x in proposal["ignored"])):
        raise ValueError("provenance and gaps must be plain records")
    return {"format": fmt, "model": model, "seeds": {Path(n).name: d for n, d in files.items() if n.startswith("seeds/")},
            "documents": _documents(files), "golden": _golden(files),
            "hints": {"from": f"the {fmt} semantic files", "measures": measures, "not_drafted": not_drafted,
                      "made_by": made_by, "outcomes": outcomes, "golden": golden},
            "ignored": proposal["ignored"], "not_drafted": not_drafted}


def parse(files: dict[str, bytes], unit: str) -> dict:
    """Parse a unit's semantic folder. Every format passes through the drafting flow (kge/estate/draftflow.py), a code
    reader's output without a model call and a model-read file through the model's steps, each with its rule-based
    fallback; the DDL parser is the final allowlist and reference check."""
    ddl.validate_unit(unit)
    reserved = sorted(RESERVED & set(files))
    if reserved:
        raise SemanticError(f"{reserved[0]}: the name is reserved for onboarding's own state; rename the file")
    formats=[]
    if "dbt_project.yml" in files: formats.append("dbt")
    if any(n.endswith(".view.lkml") for n in files): formats.append("lookml")
    dictionaries=[n for n in files if Path(n).name in DICT_NAMES]
    if dictionaries: formats.append("dictionary")
    roots=[n for n in files if len(Path(n).parts)==1 and Path(n).suffix.lower() in {".yaml",".yml",".json"} and n not in DICT_NAMES and n != "dbt_project.yml"]
    osi=[]
    for name in roots:
        raw=_object(name,files[name])
        if isinstance(raw,dict) and "version" in raw and "datasets" in raw: osi.append((name,raw))
    if osi: formats.append("osi")
    model_files=[n for n in files if len(Path(n).parts)==1 and Path(n).suffix.lower()==".md"]
    if not formats and roots: model_files += roots
    if model_files: formats.append("model")
    if len(formats)!=1: raise SemanticError("semantic folder: expected exactly one unambiguous dbt, LookML, data dictionary, OSI or model-read semantic file")
    fmt=formats[0]
    if fmt=="dbt": read=_dbt(files,unit)
    elif fmt=="lookml": read=_lookml(files,unit)
    elif fmt=="dictionary": read=_dictionary(files,unit,dictionaries[0])
    elif fmt=="osi": read=_osi(files,unit,*osi[0])
    else:
        if len(model_files)!=1: raise SemanticError("semantic folder: model reading needs exactly one semantic file")
        _model_read(files,model_files[0]); read=None
    documents=_documents(files)
    from kge.estate import draftflow
    structured=None if read is None else {"tables":read[0],"measures":read[1],"not_drafted":read[3]}
    try: flowed=draftflow.run(files,unit,model_files[0] if read is None else fmt,documents,structured)
    except ddl.DdlError: raise
    except ValueError as exc: raise SemanticError(str(exc)) from None
    tables,measures,not_drafted=flowed["tables"],flowed["measures"],flowed["not_drafted"]
    ignored=read[2] if read is not None else [model_files[0]+": read by the onboarding drafting flow"]
    if not tables:
        why=next(o["reason"] for o in flowed["outcomes"] if o["node"]=="tables")
        raise SemanticError("semantic folder: no tables were declared" + ("" if read is not None else
            f"; {model_files[0]} has none under ## <table> headings with | column | type | description | key | ({why})"))
    seeds={Path(n).name:d for n,d in files.items() if n.startswith("seeds/")}
    model={"unit":unit,"tables":tables,"statements":0}
    model=ddl.parse(ddl.render(model),unit)
    golden = _golden(files)
    flow={"made_by":flowed["made_by"],"outcomes":flowed["outcomes"],"golden":flowed["golden"]}
    return {"format":fmt,"model":model,"seeds":seeds,"documents":documents,"golden":golden,
            "hints":{"from":f"the {fmt} semantic files","measures":measures,"not_drafted":not_drafted,**flow},
            "ignored":ignored,"not_drafted":not_drafted}
