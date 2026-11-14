"""Runtime resolution, live schema checks, SQL renaming along IMPLEMENTS, partition filter enforcement, and execution."""
from __future__ import annotations

from contextlib import contextmanager
import datetime as dt
import time
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from kge.adapters.warehouse.types import DECIMAL_TYPE, catalogue_type
from kge.adapters.deadline import RequestDeadlineExceeded
from kge.pipeline.settings import bigquery_max_bytes_billed, default_runtimes
from kge.pipeline.sqlguard import Allowlist, CheckedAllowlist, canonical, check, gate
from kge.pipeline.trace import QueryCancelled
from kge.ports import AUTHORED, QueryLimits, QueryRefused

LOCATION_CHARS = ("/", "\\", ":", ".duckdb", ".db")
INT_FAMILY = {"SMALLINT", "INTEGER", "BIGINT"}
FLOAT_FAMILY = {"FLOAT", "DOUBLE"}


def _scope_sources(scope) -> dict:
    """Resolve correlated qualifiers from the nearest scope, retaining local alias shadowing."""
    sources = {}
    while scope is not None:
        for name, source in scope.sources.items():
            sources.setdefault(name.lower(), source)
        scope = scope.parent
    return sources


def same_type(expected: str, actual: str) -> bool:
    exp_cat = catalogue_type(expected)
    act_cat = catalogue_type(actual)
    if exp_cat == act_cat:
        return True
    if exp_cat in INT_FAMILY and act_cat in INT_FAMILY:
        return True
    if exp_cat in FLOAT_FAMILY and act_cat in FLOAT_FAMILY:
        return True
    if DECIMAL_TYPE.fullmatch(exp_cat) and (DECIMAL_TYPE.fullmatch(act_cat) or act_cat in FLOAT_FAMILY):
        return True
    return False


def _looks_like_location(value: str, known_locations: set[str]) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    if text in known_locations:
        return True
    return any(token in text for token in LOCATION_CHARS)


class RuntimeManager:
    """Manages declared runtimes, startup schema verification, IMPLEMENTS SQL rewriting, and runtime execution."""

    def __init__(self, warehouses: Any):
        self.warehouses = warehouses
        self.runtimes: dict[str, dict] = {}
        self.rt_tables: dict[tuple[str, str], dict] = {}
        self.rt_columns: dict[tuple[str, str], dict[str, dict]] = {}
        self.certified_in: dict[str, str] = {}
        self.partitioned: dict[str, str] = {}
        self.ref_columns: dict[tuple[str, str], str] = {}
        self.ref_table_units: dict[str, str] = {}
        self.reference_allowlists: dict[str, Allowlist] = {}
        self.pack_default_runtimes: list[str] = ["reference"]
        self.folder: Path | None = getattr(warehouses, "folder", None)
        self._health: dict[str, dict] = {}
        self._duck_cons: dict[str, Any] = {}
        self._bq_loaders: dict[str, tuple[Any, str, str]] = {}
        self._configure_default_reference()

    def _configure_default_reference(self) -> None:
        self.runtimes["reference"] = {
            "id": "reference",
            "label": "Reference warehouse",
            "kind": "reference",
            "dialect": getattr(self.warehouses, "sql_dialect", "duckdb"),
            "location": str(getattr(self.warehouses, "folder", "") or getattr(self.warehouses, "dataset_id", "reference")),
            "execution_identity": None,
            "billing_project": None,
            "units": list(getattr(self.warehouses, "units", [])),
        }
        self._health["reference"] = {
            "available": True,
            "reason": None,
            "problems": [],
            "kind": "reference",
            "dialect": self.runtimes["reference"]["dialect"],
        }

    def configure(self, subgraph: dict) -> None:
        """Load the runtime subgraph from the graph and check every runtime in the default list live."""
        self.close()
        self.runtimes = {}
        self._configure_default_reference()
        raw_runtimes = subgraph.get("runtimes") or {}
        rt_iter = raw_runtimes.items() if isinstance(raw_runtimes, dict) else ((r["id"], r) for r in raw_runtimes if isinstance(r, dict) and "id" in r)
        for rt_id, info in rt_iter:
            units = list(info.get("units") or [])
            if rt_id == "reference" and not units:
                units = list(getattr(self.warehouses, "units", []))
            self.runtimes[rt_id] = {
                "id": rt_id,
                "label": info.get("label") or rt_id,
                "kind": "reference" if rt_id == "reference" or info.get("kind") in ("internal", "reference") else (info.get("kind") or "external"),
                "dialect": info.get("dialect") or getattr(self.warehouses, "sql_dialect", "duckdb"),
                "location": info.get("location") or "",
                "execution_identity": info.get("execution_identity"),
                "billing_project": info.get("billing_project"),
                "units": units,
            }
        self.rt_tables = dict(subgraph.get("rt_tables") or {})
        for rtt in subgraph.get("runtime_tables") or []:
            if isinstance(rtt, dict) and rtt.get("runtime") and rtt.get("implements"):
                rt_id = rtt["runtime"]
                imp = str(rtt["implements"])
                unit, _, ref_tbl = imp.partition(".")
                ref_tbl_name = (ref_tbl or unit).lower()
                if rt_id in self.runtimes and ref_tbl and unit not in self.runtimes[rt_id]["units"]:
                    self.runtimes[rt_id]["units"].append(unit)
                self.rt_tables[(rt_id, ref_tbl_name)] = {
                    "id": rtt.get("id") or f"{rt_id}.{imp}",
                    "table": rtt.get("name") or rtt.get("table") or ref_tbl_name,
                    "location": rtt.get("location") or self.runtimes.get(rt_id, {}).get("location") or "",
                    "ref_table": ref_tbl_name,
                }
        self.rt_columns = {k: dict(v) for k, v in (subgraph.get("rt_columns") or {}).items()}
        for rtc in subgraph.get("runtime_columns") or []:
            if isinstance(rtc, dict) and rtc.get("runtime") and rtc.get("implements"):
                rt_id = rtc["runtime"]
                parts = str(rtc["implements"]).split(".")
                if len(parts) >= 2:
                    ref_tbl_name = parts[-2].lower()
                    ref_col_name = parts[-1].lower()
                    self.rt_columns.setdefault((rt_id, ref_tbl_name), {})[ref_col_name] = {
                        "id": rtc.get("id") or f"{rt_id}.{rtc['implements']}",
                        "column": rtc.get("name") or rtc.get("column") or ref_col_name,
                        "type": rtc.get("data_type") or rtc.get("type") or "",
                        "ref_column": ref_col_name,
                    }
        self.certified_in = dict(subgraph.get("certified_in") or {})
        raw_part = dict(subgraph.get("partitioned") or {})
        self.partitioned = {k.split(".")[-1].lower(): v.lower() for k, v in raw_part.items() if isinstance(v, str)}
        self.ref_columns = dict(subgraph.get("ref_columns") or {})
        self.ref_table_units = dict(subgraph.get("ref_table_units") or {})
        self.reference_allowlists = dict(subgraph.get("reference_allowlists") or {})
        for rc in subgraph.get("reference_columns") or []:
            if isinstance(rc, dict) and rc.get("tbl") and rc.get("name"):
                t_low = str(rc["tbl"]).lower()
                c_low = str(rc["name"]).lower()
                if rc.get("unit"):
                    self.ref_table_units[t_low] = str(rc["unit"])
                self.ref_columns[(t_low, c_low)] = str(rc.get("data_type") or "")
        pack_defaults = list(subgraph.get("default_runtimes") or [])
        self.pack_default_runtimes = pack_defaults if pack_defaults else ["reference"]
        if subgraph.get("folder"):
            self.folder = Path(subgraph["folder"])
        self._health = {}
        try:
            default_list = self.default_runtime_list()
        except ValueError:
            default_list = ["reference"]
        to_check = list(dict.fromkeys([*default_list, *self.runtimes.keys()]))
        for rt_id in to_check:
            if rt_id in self.runtimes:
                self._health[rt_id] = self.check_runtime(rt_id)

    def default_runtime_list(self) -> list[str]:
        """Return the deployment's active default runtime list before request-level overrides."""
        env_list = default_runtimes()
        if env_list:
            return list(env_list)
        if self.pack_default_runtimes:
            return list(self.pack_default_runtimes)
        return ["reference"]

    def runtimes_active(self, requested: list[str] | None = None) -> bool:
        """Whether runtimes are declared or requested (with no runtimes declared, everything behaves as today)."""
        if requested is not None:
            return True
        if default_runtimes() is not None:
            return True
        if self.pack_default_runtimes != ["reference"]:
            return True
        if self.certified_in:
            return True
        return any(rt_id != "reference" for rt_id in self.runtimes)

    def resolve_runtime_list(self, requested: list[str] | None = None) -> tuple[list[str] | None, str | None]:
        """Resolve and validate the ordered list of runtime ids according to precedence."""
        known_locations = {
            str(rt.get("location") or "").strip()
            for rt in self.runtimes.values()
            if rt.get("location") and rt.get("location") != rt.get("id")
        }
        raw_list = (
            list(requested)
            if requested is not None and len(requested) > 0
            else self.default_runtime_list()
        )
        if not raw_list:
            raw_list = ["reference"]
        resolved: list[str] = []
        for item in raw_list:
            token = str(item or "").strip()
            if _looks_like_location(token, known_locations):
                return None, f"runtime list must contain runtime ids only, not location {token!r}"
            if token not in self.runtimes:
                return None, f"unknown runtime {token!r}"
            if token not in resolved:
                resolved.append(token)
        return resolved, None

    def _resolve_duckdb_path(self, location: str) -> Path:
        raw = str(location or "").strip()
        if raw.startswith("file://"):
            raw = raw[7:]
        candidate = Path(raw)
        if candidate.is_absolute():
            return candidate
        bases = []
        if self.folder is not None:
            bases.extend([self.folder, self.folder.parent, self.folder.parent.parent])
        wh_folder = getattr(self.warehouses, "folder", None)
        if wh_folder is not None:
            wh_path = Path(wh_folder)
            bases.extend([wh_path, wh_path.parent, wh_path.parent.parent])
        for base in bases:
            probe = (base / candidate).resolve()
            if probe.is_file():
                return probe
        if self.folder is not None:
            return (self.folder / candidate).resolve()
        return candidate.resolve()

    def _open_duckdb(self, rt_id: str):
        import duckdb

        if rt_id in self._duck_cons:
            return self._duck_cons[rt_id]
        rt = self.runtimes[rt_id]
        path = self._resolve_duckdb_path(rt.get("location") or "")
        if not path.is_file():
            raise FileNotFoundError(f"missing database file {rt.get('location')!r}")
        con = duckdb.connect(str(path), read_only=True)
        self._duck_cons[rt_id] = con
        return con

    @contextmanager
    def _bounded_duckdb(self, rt_id: str, limits: QueryLimits):
        import threading
        import time

        import duckdb

        rt = self.runtimes[rt_id]
        path = self._resolve_duckdb_path(rt.get("location") or "")
        if not path.is_file():
            raise QueryRefused(f"missing database file {rt.get('location')!r}")
        deadline = limits.deadline if limits.deadline is not None else time.monotonic() + limits.seconds
        con = duckdb.connect(
            ":memory:",
            config={
                "threads": "1",
                "memory_limit": f"{limits.memory_bytes}B",
                "max_temp_directory_size": "0B",
                "autoload_known_extensions": "false",
                "autoinstall_known_extensions": "false",
            },
        )
        stopped = threading.Event()
        timer = None

        def interrupt_until_closed():
            if stopped.wait(max(0, deadline - time.monotonic())):
                return
            while not stopped.is_set():
                try:
                    con.interrupt()
                except duckdb.Error:
                    return
                stopped.wait(0.01)

        try:
            timer = threading.Thread(target=interrupt_until_closed, daemon=True)
            timer.start()
            escaped = str(path.resolve()).replace("'", "''")
            con.execute(f"ATTACH '{escaped}' AS governed (READ_ONLY)")
            con.execute("USE governed")
            con.execute("SET enable_external_access=false")
            con.execute("SET lock_configuration=true")
            if deadline - time.monotonic() <= 0:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
            yield con
            if time.monotonic() >= deadline:
                raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit")
        except duckdb.InterruptException:
            raise QueryRefused(f"query exceeded the {limits.seconds:g} second time limit") from None
        except duckdb.OutOfMemoryException:
            raise QueryRefused("query exceeded the working memory limit") from None
        except duckdb.Error as exc:
            raise QueryRefused("warehouse execution failed: " + str(exc).splitlines()[0][:240]) from None
        finally:
            try:
                con.close()
            finally:
                stopped.set()
                if timer is not None and timer.ident is not None:
                    timer.join()

    def check_runtime(self, rt_id: str) -> dict:
        """Check a runtime live against its declared subgraph."""
        if rt_id not in self.runtimes:
            msg = f"unknown runtime {rt_id!r}"
            return {"available": False, "reason": msg, "problems": [msg], "kind": "external", "dialect": "duckdb"}
        rt = self.runtimes[rt_id]
        kind = rt.get("kind") or ("reference" if rt_id == "reference" else "external")
        dialect = rt.get("dialect") or "duckdb"
        if rt_id == "reference":
            return {"available": True, "reason": None, "problems": [], "kind": "reference", "dialect": dialect}

        mapped_tables = {
            ref_tbl: info
            for (r_id, ref_tbl), info in self.rt_tables.items()
            if r_id == rt_id
        }
        if not mapped_tables:
            msg = "no tables mapped in runtime subgraph"
            return {"available": False, "reason": msg, "problems": [msg], "kind": kind, "dialect": dialect}

        try:
            phys_schema = self._read_physical_schema(rt_id)
        except PermissionError:
            msg = "execution_identity cannot be assumed"
            return {"available": False, "reason": msg, "problems": [msg], "kind": kind, "dialect": dialect}
        except Exception:
            msg = "runtime is unreachable"
            return {"available": False, "reason": msg, "problems": [msg], "kind": kind, "dialect": dialect}

        phys_tables = {tuple(part.lower() for part in tbl): {c.lower(): t for c, t in cols.items()}
                       for tbl, cols in phys_schema.items()}
        for ref_tbl, tbl_info in sorted(mapped_tables.items()):
            phys_tbl = tbl_info["table"]
            p_low = tuple(part.lower() for part in phys_tbl.split("."))
            if p_low not in phys_tables:
                msg = f"missing table {phys_tbl} (implements {ref_tbl})"
                return {
                    "available": False,
                    "reason": msg,
                    "problems": [msg],
                    "kind": kind,
                    "dialect": dialect,
                }
            actual_cols_map = phys_tables[p_low]
            col_mappings = self.rt_columns.get((rt_id, ref_tbl), {})
            for ref_col, col_info in sorted(col_mappings.items()):
                phys_col = col_info["column"]
                c_low = phys_col.lower()
                if c_low not in actual_cols_map:
                    msg = f"missing column {phys_tbl}.{phys_col} (implements {ref_tbl}.{ref_col})"
                    return {
                        "available": False,
                        "reason": msg,
                        "problems": [msg],
                        "kind": kind,
                        "dialect": dialect,
                    }
                actual_type = actual_cols_map[c_low]
                expected_type = col_info.get("type") or self.ref_columns.get((ref_tbl, ref_col)) or ""
                if expected_type and not same_type(expected_type, actual_type):
                    msg = f"column {phys_tbl}.{phys_col} type changed: expected {expected_type}, found {actual_type}"
                    return {
                        "available": False,
                        "reason": msg,
                        "problems": [msg],
                        "kind": kind,
                        "dialect": dialect,
                    }
        return {"available": True, "reason": None, "problems": [], "kind": kind, "dialect": dialect}

    def _bigquery_loader(self, rt_id: str) -> tuple[Any, str, str]:
        if rt_id in self._bq_loaders:
            return self._bq_loaders[rt_id]
        from kge.adapters.bigquery import CLOUD_PLATFORM, BigQueryLoader
        from kge.adapters.deadline import MIN_CALL_SECONDS, request_remaining

        rt = self.runtimes[rt_id]
        billing = rt.get("billing_project") or getattr(self.warehouses, "project", None)
        loc = str(rt.get("location") or "").strip()
        project, _, dataset = loc.partition(".")
        if not dataset:
            dataset = project
            project = billing or ""
        loader = BigQueryLoader(project=billing or project or None)
        exec_id = str(rt.get("execution_identity") or "").strip()
        if exec_id:
            source_creds, resolved_project = loader.account()
            active_principal = str(getattr(source_creds, "service_account_email", "") or "").strip()
            if active_principal != exec_id:
                request_remaining(MIN_CALL_SECONDS)
                try:
                    from google.auth import impersonated_credentials
                    from google.auth.transport.requests import Request

                    imp = impersonated_credentials.Credentials(
                        source_credentials=source_creds,
                        target_principal=exec_id,
                        target_scopes=[CLOUD_PLATFORM],
                    )
                    imp.refresh(Request())
                    loader._account = (imp, billing or project or resolved_project)
                except Exception as exc:
                    raise PermissionError("execution_identity cannot be assumed") from exc
        cached = (loader, project, dataset)
        self._bq_loaders[rt_id] = cached
        return cached

    def _read_physical_schema(self, rt_id: str) -> dict[tuple[str, ...], dict[str, str]]:
        rt = self.runtimes[rt_id]
        dialect = rt.get("dialect") or "duckdb"
        if dialect == "duckdb":
            if str(rt.get("execution_identity") or "").strip():
                raise PermissionError("execution_identity cannot be assumed")
            con = self._open_duckdb(rt_id)
            rows = con.execute(
                "SELECT database_name, schema_name, table_name, column_name, data_type FROM duckdb_columns() WHERE NOT internal"
            ).fetchall()
            current_db, current_schema = con.execute("SELECT current_database(), current_schema()").fetchone()
            out: dict[tuple[str, ...], dict[str, str]] = {}
            for db, schema, tbl, col, dtype in rows:
                names = [(schema, tbl), (db, tbl), (db, schema, tbl)]
                if db == current_db and schema == current_schema:
                    names.append((tbl,))
                for name in names:
                    out.setdefault(tuple(str(part) for part in name), {})[str(col)] = str(dtype)
            return out
        if dialect == "bigquery":
            loader, project, dataset = self._bigquery_loader(rt_id)
            datasets = set()
            for (rid, _), info in self.rt_tables.items():
                if rid != rt_id:
                    continue
                parts = info["table"].split(".")
                datasets.add((parts[0], parts[1]) if len(parts) == 3 else (project, parts[0] if len(parts) == 2 else dataset))
            out = {}
            for proj, ds in sorted(datasets):
                target = ".".join(p for p in (proj, ds, "INFORMATION_SCHEMA.COLUMNS") if p)
                data = loader.run(f"SELECT table_name, column_name, data_type FROM `{target}`")
                for row in data.get("rows") or []:
                    fields = [cell.get("v") for cell in row.get("f", [])]
                    if len(fields) >= 3 and fields[0] and fields[1]:
                        names = [(proj, ds, str(fields[0]))]
                        if proj == project:
                            names.append((ds, str(fields[0])))
                            if ds == dataset:
                                names.append((str(fields[0]),))
                        for name in names:
                            out.setdefault(name, {})[str(fields[1])] = catalogue_type(str(fields[2] or "STRING"))
            return out
        raise ValueError(f"unsupported dialect {dialect!r} for runtime {rt_id!r}")

    def health(self) -> dict[str, dict]:
        """Return health status for all configured runtimes."""
        for rt_id in self.runtimes:
            if rt_id not in self._health:
                self._health[rt_id] = self.check_runtime(rt_id)
        return {k: dict(v) for k, v in self._health.items()}

    def list_runtimes(self, readable_units: set[str] | None = None) -> list[dict]:
        """List runtimes the caller may use and which reference tables each holds."""
        health = self.health()
        allowed_units = set(readable_units) if readable_units is not None else set(getattr(self.warehouses, "units", []))
        certified_rts = set(self.certified_in.values())
        items: list[dict] = []
        for rt_id, rt in self.runtimes.items():
            rt_units = set(rt.get("units") or [])
            if rt_id == "reference":
                rt_units = set(getattr(self.warehouses, "units", []))
            visible_units = rt_units & allowed_units
            if not visible_units and allowed_units:
                continue
            if rt_id == "reference":
                tables = sorted({
                    f"{unit}.{tbl}" if "." not in tbl else tbl
                    for tbl, unit in self.ref_table_units.items()
                    if unit in visible_units
                })
                if not tables:
                    tables = sorted({
                        f"{u}.{tbl}" if "." not in tbl else tbl
                        for u in visible_units
                        for tbl in self.warehouses.calendar_tables(u)
                    })
            else:
                tables = sorted({
                    f"{self.ref_table_units[ref_tbl]}.{ref_tbl}" if ref_tbl in self.ref_table_units and "." not in ref_tbl else ref_tbl
                    for (r_id, ref_tbl) in self.rt_tables
                    if r_id == rt_id and self.ref_table_units.get(ref_tbl, next(iter(visible_units), "")) in visible_units
                })
            st = health.get(rt_id) or {"available": True, "reason": None}
            is_certified = (rt_id in certified_rts) if certified_rts else (rt_id == "reference")
            entry = {
                "id": rt_id,
                "label": rt.get("label") or rt_id,
                "kind": rt.get("kind") or ("reference" if rt_id == "reference" else "external"),
                "dialect": rt.get("dialect") or "duckdb",
                "available": bool(st.get("available", False)),
                "certified": is_certified,
                "tables": tables,
            }
            if st.get("reason"):
                entry["reason"] = st["reason"]
            items.append(entry)
        return items

    def _extract_needed(self, statements: list[dict]) -> tuple[set[str], set[str], set[tuple[str, str]]]:
        """Extract (units, ref_tables, (ref_table, ref_col)) required by the reference SQL statements."""
        units: set[str] = set()
        tables: set[str] = set()
        columns: set[tuple[str, str]] = set()
        curated_set = {u.lower() for u in (getattr(self.warehouses, "curated", []) or [])}
        known_ref_tables = {
            t.lower()
            for t, _ in self.ref_columns
            if self.ref_table_units.get(t.lower(), "") not in curated_set
        } | {t for t, u in self.ref_table_units.items() if u not in curated_set}
        for stmt in statements:
            unit = stmt.get("unit") or ""
            if unit and unit.lower() not in curated_set:
                units.add(unit)
            sql = stmt.get("sql") or ""
            allow = stmt.get("allow")
            try:
                tree = sqlglot.parse_one(sql, read="duckdb")
            except Exception:
                continue
            ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
            try:
                scopes = list(traverse_scope(tree))
            except Exception:
                scopes = []
            for scope in scopes:
                scope_ref_sources: dict[str, str] = {}
                scope_cte_sources: set[str] = set(ctes)
                scope_tables: list[str] = []
                local_sources = {name.lower() for name in scope.sources}
                for src_key, src_val in _scope_sources(scope).items():
                    k_low = src_key.lower()
                    if isinstance(src_val, exp.Table):
                        t_name = src_val.name.lower()
                        if not src_val.db and t_name in ctes:
                            scope_cte_sources.add(k_low)
                            scope_cte_sources.add(t_name)
                            continue
                        if (src_val.db and src_val.db.lower() in curated_set) or self.ref_table_units.get(t_name, "") in curated_set:
                            scope_cte_sources.add(k_low)
                            scope_cte_sources.add(t_name)
                            continue
                        if src_val.db:
                            units.add(src_val.db.lower())
                        tables.add(t_name)
                        if src_key in local_sources and t_name not in scope_tables:
                            scope_tables.append(t_name)
                        scope_ref_sources[k_low] = t_name
                        scope_ref_sources.setdefault(t_name, t_name)
                    else:
                        scope_cte_sources.add(k_low)
                select_aliases = {
                    s.alias.lower()
                    for s in (scope.expression.selects if isinstance(scope.expression, exp.Select) else [])
                    if isinstance(s, exp.Alias) and s.alias
                }
                for col in scope.columns:
                    c_name = col.name.lower()
                    t_qual = col.table.lower() if col.table else ""
                    if t_qual:
                        if t_qual in scope_cte_sources and t_qual not in scope_ref_sources:
                            continue
                        resolved_table = scope_ref_sources.get(t_qual, t_qual)
                        if resolved_table in tables or resolved_table in known_ref_tables:
                            tables.add(resolved_table)
                            columns.add((resolved_table, c_name))
                    else:
                        if not scope_tables:
                            continue
                        if c_name in select_aliases and col.find_ancestor(exp.Order, exp.Group, exp.Having, exp.Qualify):
                            continue
                        if len(scope_tables) == 1:
                            columns.add((scope_tables[0], c_name))
                        else:
                            matched = [
                                tbl for tbl in scope_tables
                                if (tbl, c_name) in self.ref_columns
                                or (allow and c_name in {x.lower() for x in (allow.columns.get(tbl) or set())})
                            ]
                            for tbl in matched:
                                columns.add((tbl, c_name))
        return units, tables, columns

    def select_runtime(
        self,
        statements: list[dict],
        *,
        runtimes: list[str] | None = None,
        readable_units: set[str] | None = None,
        measures: list[str] | tuple[str, ...] = (),
    ) -> dict:
        """Filter the ordered candidate runtime list to those available, authorized, and holding every table/column."""
        candidates, err = self.resolve_runtime_list(runtimes)
        if err or not candidates:
            return {"ok": False, "reason": err or "no runtime available", "reasons": [err or "no runtime available"]}

        curated_set = {u.lower() for u in (getattr(self.warehouses, "curated", []) or [])}
        needed_units, needed_tables, needed_cols = self._extract_needed(statements)
        for tbl in needed_tables:
            u = self.ref_table_units.get(tbl)
            if u and u.lower() not in curated_set:
                needed_units.add(u)
        needed_units -= curated_set

        reasons: list[str] = []
        skipped: list[dict] = []
        for rt_id in candidates:
            st = self._health.get(rt_id) if rt_id in self._health else self.check_runtime(rt_id)
            self._health[rt_id] = st
            rt_meta = self.runtimes.get(rt_id) or {"id": rt_id, "label": rt_id}
            rt_label = rt_meta.get("label") or rt_id
            if not st.get("available"):
                why = f"unavailable ({st.get('reason') or 'failed check'})"
                reasons.append(f"{rt_id}: {why}")
                skipped.append({"id": rt_id, "label": rt_label, "reason": why})
                continue
            rt = self.runtimes[rt_id]
            rt_units = set(rt.get("units") or [])
            if rt_id == "reference":
                rt_units = set(getattr(self.warehouses, "units", []))
            access_problem = self._access_problem(rt_id, needed_units, readable_units)
            if access_problem:
                reasons.append(access_problem)
                skipped.append({"id": rt_id, "label": rt_label, "reason": access_problem})
                continue
            missing_units = sorted(u for u in needed_units if rt_units and u not in rt_units)
            if missing_units:
                why = f"unit {', '.join(missing_units)} is not held by runtime {rt_id}"
                reasons.append(f"{rt_id}: {why}")
                skipped.append({"id": rt_id, "label": rt_label, "reason": why})
                continue
            if rt_id == "reference":
                unmapped = []
                for tbl, col in sorted(needed_cols):
                    if self.ref_columns and (tbl, col) not in self.ref_columns:
                        allow_ok = any(
                            col in {c.lower() for c in (s.get("allow").columns.get(tbl) or set())}
                            for s in statements
                            if s.get("allow")
                        )
                        if not allow_ok:
                            unmapped.append(f"{tbl}.{col}")
                if unmapped:
                    why = f"unmapped reference column {', '.join(unmapped)}"
                    reasons.append(f"{rt_id}: {why}")
                    skipped.append({"id": rt_id, "label": rt_label, "reason": why})
                    continue
            else:
                missing_tables = [
                    f"{self.ref_table_units[tbl]}.{tbl}" if tbl in self.ref_table_units else tbl
                    for tbl in sorted(needed_tables)
                    if (rt_id, tbl) not in self.rt_tables
                ]
                if missing_tables:
                    why = f"table {', '.join(missing_tables)} has no IMPLEMENTS mapping in runtime {rt_id}"
                    reasons.append(f"{rt_id}: {why}")
                    skipped.append({"id": rt_id, "label": rt_label, "reason": why})
                    continue
                missing_cols = [
                    f"{self.ref_table_units[tbl]}.{tbl}.{col}" if tbl in self.ref_table_units else f"{tbl}.{col}"
                    for tbl, col in sorted(needed_cols)
                    if col not in {k.lower() for k in self.rt_columns.get((rt_id, tbl), {})}
                ]
                if missing_cols:
                    why = f"column {', '.join(missing_cols)} has no IMPLEMENTS mapping in runtime {rt_id}"
                    reasons.append(f"{rt_id}: {why}")
                    skipped.append({"id": rt_id, "label": rt_label, "reason": why})
                    continue
            return {
                "ok": True,
                "runtime_id": rt_id,
                "runtime": self.runtime_summary(rt_id),
                "skipped": skipped,
                **self.certification_meta(rt_id, measures),
            }

        return {
            "ok": False,
            "reason": "; ".join(reasons) if reasons else "no runtime in the list can answer this question",
            "reasons": reasons,
            "skipped": skipped,
        }

    def runtime_summary(self, rt_id: str) -> dict:
        rt = self.runtimes.get(rt_id) or self.runtimes["reference"]
        return {
            "id": rt["id"],
            "label": rt.get("label") or rt["id"],
            "kind": rt.get("kind") or ("reference" if rt["id"] == "reference" else "external"),
            "dialect": rt.get("dialect") or "duckdb",
        }

    def certification_meta(self, rt_id: str, measures: list[str] | tuple[str, ...] = ()) -> dict:
        rt = self.runtimes.get(rt_id) or self.runtimes["reference"]
        is_external = (rt.get("kind") or ("reference" if rt_id == "reference" else "external")) == "external"
        declared = [self.certified_in[m] for m in measures if m and m in self.certified_in]
        if not declared:
            runtime_certified = False
            expected_rt = None
        else:
            runtime_certified = all(exp_rt == rt_id for exp_rt in declared)
            mismatch = next((exp_rt for exp_rt in declared if exp_rt != rt_id), None)
            expected_rt = mismatch or declared[0]
        meta: dict[str, Any] = {
            "runtime_certified": runtime_certified,
            "certified_in": expected_rt,
            "live_data": is_external,
        }
        if is_external:
            meta["queried_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        return meta

    def reference_runtime_meta(
        self,
        statement: dict,
        *,
        measures: list[str] | tuple[str, ...] = (),
    ) -> tuple[dict | None, dict]:
        """Check partition policy on reference SQL and build runtime metadata without rewriting authored SQL."""
        ref_sql = statement["sql"]
        _, ref_tables, _ = self._extract_needed([statement])
        meta = {
            "runtime": self.runtime_summary("reference"),
            "physical_tables": sorted(ref_tables),
            "sql": ref_sql,
            "reference_sql": ref_sql,
            **self.certification_meta("reference", measures),
        }
        part_err = self.check_partition_policy(ref_sql)
        if part_err:
            guard = {
                "passed": False,
                "problems": [part_err],
                "failed_at": "partition",
                "checks": ["partition"],
            }
            return guard, meta
        return None, meta

    def check_partition_policy(self, sql: str) -> str | None:
        """Verify that every touched table in self.partitioned has a plain prunable date filter in WHERE."""
        if not self.partitioned:
            return None
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except Exception:
            return None
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        alias_to_table: dict[str, str] = {}
        touched_partitioned: dict[str, str] = {}
        for t in tree.find_all(exp.Table):
            t_name = t.name.lower()
            if not t.db and t_name in ctes:
                continue
            alias_to_table[t_name] = t_name
            if t.alias:
                alias_to_table[t.alias.lower()] = t_name
            if t_name in self.partitioned:
                touched_partitioned[t_name] = self.partitioned[t_name].lower()
        if not touched_partitioned:
            return None

        wheres = list(tree.find_all(exp.Where))
        if not wheres:
            tbl, col = next(iter(touched_partitioned.items()))
            return f"partitioned table {tbl} requires a period filter on partition column {tbl}.{col}"

        WRAPPING_NODES = (
            exp.Anonymous,
            exp.Cast,
            exp.TryCast,
            exp.Extract,
            exp.DateTrunc,
            exp.TimestampTrunc,
            exp.StrToTime,
            exp.TimeToStr,
        )
        PRUNABLE_COMPARISONS = (
            exp.EQ,
            exp.GT,
            exp.GTE,
            exp.LT,
            exp.LTE,
            exp.Between,
            exp.In,
        )

        for tbl, part_col in sorted(touched_partitioned.items()):
            has_prunable = False
            for where in wheres:
                for col in where.find_all(exp.Column):
                    if col.name.lower() != part_col:
                        continue
                    t_qual = col.table.lower() if col.table else ""
                    if t_qual and alias_to_table.get(t_qual, t_qual) != tbl:
                        continue
                    parent = col.parent
                    while isinstance(parent, exp.Paren):
                        parent = parent.parent
                    if isinstance(parent, WRAPPING_NODES) or (
                        isinstance(parent, exp.Func)
                        and not isinstance(parent, (*PRUNABLE_COMPARISONS, exp.Connector, exp.Not, exp.Paren))
                    ):
                        return (
                            f"partition column {tbl}.{part_col} must be filtered as a plain date equality or range, "
                            f"never wrapped in a function or cast"
                        )
                    if isinstance(parent, PRUNABLE_COMPARISONS):
                        has_prunable = True
            if not has_prunable:
                return f"partitioned table {tbl} requires a period filter on partition column {tbl}.{part_col}"
        return None

    def _rename_sql_for_runtime(self, sql: str, unit: str, rt_id: str, allow: Allowlist) -> tuple[str, Allowlist, list[str]]:
        """Rename reference tables and columns along IMPLEMENTS into the runtime's physical names and subgraph."""
        tree = sqlglot.parse_one(sql, read="duckdb")
        ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
        ref_to_phys_table: dict[str, str] = {}
        renamed_cols: set[int] = set()

        try:
            scopes = list(traverse_scope(tree))
        except Exception:
            scopes = []

        for scope in scopes:
            scope_ref_sources: dict[str, str] = {}
            scope_cte_sources: set[str] = set(ctes)
            scope_tables: list[str] = []
            local_sources = {name.lower() for name in scope.sources}
            for src_key, src_val in _scope_sources(scope).items():
                k_low = src_key.lower()
                if isinstance(src_val, exp.Table):
                    t_name = src_val.name.lower()
                    if not src_val.db and t_name in ctes:
                        scope_cte_sources.add(k_low)
                        scope_cte_sources.add(t_name)
                        continue
                    if (rt_id, t_name) not in self.rt_tables:
                        raise QueryRefused(f"table {t_name} has no IMPLEMENTS mapping in runtime {rt_id}")
                    ref_to_phys_table[t_name] = self.rt_tables[(rt_id, t_name)]["table"]
                    if src_key in local_sources and t_name not in scope_tables:
                        scope_tables.append(t_name)
                    scope_ref_sources[k_low] = t_name
                    scope_ref_sources.setdefault(t_name, t_name)
                else:
                    scope_cte_sources.add(k_low)

            select_aliases = {
                s.alias.lower()
                for s in (scope.expression.selects if isinstance(scope.expression, exp.Select) else [])
                if isinstance(s, exp.Alias) and s.alias
            }

            for col in list(scope.columns):
                if id(col) in renamed_cols:
                    continue
                orig_name = col.name
                c_name = orig_name.lower()
                t_qual = col.table.lower() if col.table else ""
                if t_qual:
                    if t_qual in scope_cte_sources and t_qual not in scope_ref_sources:
                        continue
                    ref_tbl = scope_ref_sources.get(t_qual)
                    if not ref_tbl:
                        raise QueryRefused(f"table {t_qual} has no IMPLEMENTS mapping in runtime {rt_id}")
                    col_map = {k.lower(): v["column"] for k, v in self.rt_columns.get((rt_id, ref_tbl), {}).items()}
                    if c_name not in col_map:
                        raise QueryRefused(f"column {ref_tbl}.{c_name} has no IMPLEMENTS mapping in runtime {rt_id}")
                    phys_col = col_map[c_name]
                    if t_qual == ref_tbl:
                        source = _scope_sources(scope).get(t_qual)
                        if isinstance(source, exp.Table) and not source.alias:
                            col.set("table", exp.to_identifier(ref_to_phys_table[ref_tbl].rsplit(".", 1)[-1]))
                    col.set("this", exp.to_identifier(phys_col))
                    renamed_cols.add(id(col))
                    if isinstance(col.parent, exp.Select):
                        col.replace(exp.alias_(col.copy(), orig_name))
                else:
                    if not scope_tables:
                        continue
                    if c_name in select_aliases and col.find_ancestor(exp.Order, exp.Group, exp.Having, exp.Qualify):
                        continue
                    if c_name in scope_cte_sources:
                        continue
                    candidate_tables = [
                        ref_tbl for ref_tbl in scope_tables
                        if c_name in {k.lower() for k in self.rt_columns.get((rt_id, ref_tbl), {})}
                        or (ref_tbl, c_name) in self.ref_columns
                    ]
                    if not candidate_tables and len(scope_tables) == 1:
                        candidate_tables = [scope_tables[0]]
                    if candidate_tables:
                        ref_tbl = candidate_tables[0]
                        col_map = {k.lower(): v["column"] for k, v in self.rt_columns.get((rt_id, ref_tbl), {}).items()}
                        if c_name not in col_map:
                            raise QueryRefused(f"column {ref_tbl}.{c_name} has no IMPLEMENTS mapping in runtime {rt_id}")
                        phys_col = col_map[c_name]
                        col.set("this", exp.to_identifier(phys_col))
                        renamed_cols.add(id(col))
                        if isinstance(col.parent, exp.Select):
                            col.replace(exp.alias_(col.copy(), orig_name))

        for t in tree.find_all(exp.Table):
            t_name = t.name.lower()
            if not t.db and t_name in ctes:
                continue
            if (rt_id, t_name) not in self.rt_tables:
                raise QueryRefused(f"table {t_name} has no IMPLEMENTS mapping in runtime {rt_id}")
            phys_table = self.rt_tables[(rt_id, t_name)]["table"]
            ref_to_phys_table[t_name] = phys_table
            physical = exp.to_table(phys_table)
            if "." in phys_table:
                for part in physical.parts:
                    part.set("quoted", True)
                if not t.alias:
                    t.set("alias", exp.TableAlias(this=exp.to_identifier(physical.name)))
            for part in ("this", "db", "catalog"):
                t.set(part, physical.args.get(part))

        phys_tables_set: set[str] = set()
        phys_cols_map: dict[str, set[str]] = {}
        for (r_id, ref_tbl), tbl_info in self.rt_tables.items():
            if r_id != rt_id:
                continue
            p_tbl = tbl_info["table"]
            phys_tables_set.add(p_tbl)
            phys_cols_map[p_tbl] = {
                c_info["column"]
                for c_info in self.rt_columns.get((rt_id, ref_tbl), {}).values()
            }
        def _map_table_col(t_str: str, c_str: str) -> tuple[str, str] | None:
            ref_tbl = t_str.replace("__", ".").split(".")[-1].lower()
            if (rt_id, ref_tbl) not in self.rt_tables:
                return None
            col_map = {k.lower(): v["column"] for k, v in self.rt_columns.get((rt_id, ref_tbl), {}).items()}
            if c_str.lower() not in col_map:
                return None
            return self.rt_tables[(rt_id, ref_tbl)]["table"], col_map[c_str.lower()]

        def _rename_canonical(cond: str) -> str | None:
            try:
                node = sqlglot.parse_one(cond, read="duckdb")
            except Exception:
                return None
            for c in list(node.find_all(exp.Column)):
                if not c.table:
                    continue
                mapped = _map_table_col(c.table, c.name)
                if mapped is None:
                    return None
                pt, pc = mapped
                c.set("table", exp.to_identifier(pt.replace(".", "__")))
                c.set("this", exp.to_identifier(pc))
            return canonical(node, {})

        phys_joins: set[frozenset] = set()
        for j in getattr(allow, "joins", set()) or set():
            items = list(j)
            if len(items) == 2 and all(isinstance(x, tuple) and len(x) == 2 for x in items):
                (t1, c1), (t2, c2) = items
            elif len(items) == 4 and all(isinstance(x, str) for x in items):
                t1, c1, t2, c2 = items
            else:
                continue
            mc1 = _map_table_col(t1, c1)
            mc2 = _map_table_col(t2, c2)
            if mc1 and mc2:
                phys_joins.add(frozenset({mc1, mc2}))

        phys_predicates = {
            rc for p in (getattr(allow, "predicates", set()) or set())
            if (rc := _rename_canonical(p)) is not None
        }
        phys_declared = {
            rc for d in (getattr(allow, "declared", set()) or set())
            if (rc := _rename_canonical(d)) is not None
        }
        phys_links: set[frozenset] = set()
        for link in getattr(allow, "links", set()) or set():
            mapped_link = [_rename_canonical(c) for c in link]
            if all(x is not None for x in mapped_link):
                phys_links.add(frozenset(mapped_link))
        phys_measures = {
            mc for t, c in (getattr(allow, "measures", set()) or set())
            if (mc := _map_table_col(t, c)) is not None
        }
        phys_required: dict[str, dict] = {}
        for jid, need in (getattr(allow, "required", {}) or {}).items():
            thr_ref = (need.get("through") or "").replace("__", ".").split(".")[-1].lower()
            if (rt_id, thr_ref) not in self.rt_tables:
                continue
            thr_phys = self.rt_tables[(rt_id, thr_ref)]["table"]
            thr_cols = {k.lower(): v["column"] for k, v in self.rt_columns.get((rt_id, thr_ref), {}).items()}
            req_pairs = {
                frozenset({mc1, mc2})
                for p in (need.get("pairs") or set())
                if len(p) == 2
                for (t1, c1), (t2, c2) in [tuple(p)]
                if (mc1 := _map_table_col(t1, c1)) and (mc2 := _map_table_col(t2, c2))
            }
            req_conj = {
                rc: v for k, v in (need.get("conjuncts") or {}).items()
                if (rc := _rename_canonical(k)) is not None
            }
            win = need.get("window")
            req_win = (
                {k: thr_cols[v.lower()] if k in ('is_current', 'valid_from', 'valid_to') else v
                 for k, v in win.items() if k not in ('is_current', 'valid_from', 'valid_to')
                 or isinstance(v, str) and v.lower() in thr_cols}
                if isinstance(win, dict) else None
            )
            if req_win is not None and not {'is_current', 'valid_from', 'valid_to'} <= req_win.keys():
                req_win = None
            phys_required[jid] = {
                "pairs": req_pairs,
                "conjuncts": req_conj,
                "through": thr_phys,
                "window": req_win,
            }

        certified: dict[str, set[str]] = {}
        for table, columns in allow.columns.items():
            for column in columns:
                if mapped := _map_table_col(table, column):
                    certified.setdefault(mapped[0], set()).add(mapped[1])
        phys_windows = {
            (low[0], low[1], high[1]) for table, valid_from, valid_to in allow.windows
            if (low := _map_table_col(table, valid_from)) and (high := _map_table_col(table, valid_to))
        }
        rt_allow = Allowlist(
            unit=unit,
            tables=phys_tables_set,
            columns=certified,
            restricted={mapped for t, c in allow.restricted if (mapped := _map_table_col(t, c))},
            cites={mapped: cite for (t, c), cite in allow.cites.items() if (mapped := _map_table_col(t, c))},
            windows=phys_windows,
            checked_cte_joins=isinstance(allow, CheckedAllowlist),
            joins=phys_joins,
            schema=phys_cols_map,
            measures=phys_measures,
            attached=getattr(allow, "attached", False),
            predicates=phys_predicates,
            required=phys_required,
            declared=phys_declared,
            links=phys_links,
        )
        physical_tables = sorted(set(ref_to_phys_table.values()))
        return tree.sql("duckdb"), rt_allow, physical_tables

    def _render_for_dialect(self, renamed_duck_sql: str, dialect: str) -> str:
        if dialect == "bigquery":
            from kge.adapters.warehouse.bigquery import rewrite_sql

            return rewrite_sql(renamed_duck_sql, lambda _u: "", unit=None, duckdb_types=True,
                               preserve_qualified_names=True)
        return renamed_duck_sql

    def statement_sql(self, statement: dict, runtime_id: str) -> str:
        """Rendering reuses the selected runtime's schema and mappings; it never submits a job."""
        if runtime_id == "reference":
            return statement["sql"]
        sql, _, _ = self._rename_sql_for_runtime(statement["sql"], statement["unit"], runtime_id, statement["allow"])
        return self._render_for_dialect(sql, self.runtimes[runtime_id].get("dialect") or "duckdb")

    def execute_statement(
        self,
        statement: dict,
        *,
        runtime_id: str = "reference",
        measures: list[str] | tuple[str, ...] = (),
        limits: QueryLimits | None = None,
        on_stage: Any = None,
        on_progress: Any = None,
        on_ready: Any = None,
        job_id: str | None = None,
    ) -> tuple[dict, list[tuple] | dict | None, dict]:
        """Run partition policy check, IMPLEMENTS rename, runtime subgraph SQL gate, cost gate, and execution."""
        ref_sql = statement["sql"]
        unit = statement["unit"]
        allow = statement["allow"]

        if runtime_id == "reference":
            part_guard, meta = self.reference_runtime_meta(statement, measures=measures)
            if part_guard is not None:
                return part_guard, None, meta
            units = list(statement.get("units") or ([unit] if unit else []))
            session = (
                self.warehouses.attached(units, AUTHORED, **({"on_progress": on_progress} if on_progress else {}))
                if len(units) > 1
                else self.warehouses.cursor(unit, AUTHORED, **({"on_progress": on_progress} if on_progress else {}))
            )
            with session as con:
                if on_stage is not None:
                    on_stage("gate")
                guard = gate(con, ref_sql, allow)
                if not guard.get("passed"):
                    return guard, None, meta
                if on_ready is not None:
                    on_ready(guard, meta)
                if on_stage is not None:
                    on_stage("execute")
                rows = (self.warehouses.submit_query(ref_sql, unit, units, job_id) if job_id is not None
                        else con.execute(ref_sql).fetchall())
                return guard, rows, meta

        part_err = self.check_partition_policy(ref_sql)
        if part_err:
            guard = {
                "passed": False,
                "problems": [part_err],
                "failed_at": "partition",
                "checks": ["partition"],
            }
            meta = {
                "runtime": self.runtime_summary(runtime_id),
                "physical_tables": [],
                "sql": ref_sql,
                "reference_sql": ref_sql,
                **self.certification_meta(runtime_id, measures),
            }
            return guard, None, meta

        rt = self.runtimes.get(runtime_id)
        if not rt:
            guard = {"passed": False, "problems": [f"unknown runtime {runtime_id!r}"], "failed_at": "runtime", "checks": ["runtime"]}
            return guard, None, {"runtime": self.runtime_summary("reference"), "physical_tables": [], "sql": ref_sql, "reference_sql": ref_sql}

        try:
            renamed_duck_sql, rt_allow, phys_tables = self._rename_sql_for_runtime(ref_sql, unit, runtime_id, allow)
        except QueryRefused as exc:
            guard = {"passed": False, "problems": [str(exc)], "failed_at": "implements", "checks": ["implements"]}
            meta = {
                "runtime": self.runtime_summary(runtime_id),
                "physical_tables": [],
                "sql": ref_sql,
                "reference_sql": ref_sql,
                **self.certification_meta(runtime_id, measures),
            }
            return guard, None, meta

        dialect = rt.get("dialect") or "duckdb"
        rendered_sql = self._render_for_dialect(renamed_duck_sql, dialect)
        meta = {
            "runtime": self.runtime_summary(runtime_id),
            "physical_tables": phys_tables,
            "sql": rendered_sql,
            "reference_sql": ref_sql,
            **self.certification_meta(runtime_id, measures),
        }

        if on_stage is not None:
            on_stage("gate")
        problems = check(rendered_sql, rt_allow, dialect=dialect)
        if problems:
            guard = {
                "passed": False,
                "problems": problems,
                "failed_at": "static",
                "checks": ["read-only", "tables", "columns", "joins"],
            }
            return guard, None, meta

        if dialect == "duckdb":
            if str(rt.get("execution_identity") or "").strip():
                guard = {
                    "passed": False,
                    "problems": ["execution_identity cannot be assumed"],
                    "failed_at": "runtime",
                    "checks": ["runtime"],
                }
                return guard, None, meta
            con = self._open_duckdb(runtime_id)
            cur = con.cursor()
            try:
                from kge.pipeline.trace import check_cancelled, register_cancel_hook
                check_cancelled()
                register_cancel_hook(lambda: getattr(cur, "interrupt", lambda: None)())
                guard = gate(cur, rendered_sql, rt_allow, False, "duckdb")
                if not guard.get("passed"):
                    return guard, None, meta
                if on_ready is not None:
                    on_ready(guard, meta)
                if on_stage is not None:
                    on_stage("execute")
                check_cancelled()
                started = time.monotonic()
                if on_progress is not None:
                    on_progress("running", 0, None)
                if job_id is not None:
                    from kge.adapters.warehouse import localjobs
                    def compute(register_cancel):
                        worker = self._open_duckdb(runtime_id).cursor()
                        try:
                            register_cancel(worker.interrupt)
                            return worker.execute(rendered_sql).fetchall()
                        finally:
                            worker.close()
                    return guard, localjobs.submit(job_id, compute), meta
                rows = cur.execute(rendered_sql).fetchall()
                check_cancelled()
                if on_progress is not None:
                    on_progress("done", int((time.monotonic() - started) * 1000), None)
                return guard, rows, meta
            finally:
                cur.close()

        if dialect == "bigquery":
            from kge.adapters.bigquery import typed_rows

            max_bytes = bigquery_max_bytes_billed(10_737_418_240)
            try:
                loader, project, dataset = self._bigquery_loader(runtime_id)
            except PermissionError:
                guard = {
                    "passed": False,
                    "problems": ["execution_identity cannot be assumed"],
                    "failed_at": "runtime",
                    "checks": ["runtime"],
                }
                return guard, None, meta
            except Exception as exc:
                guard = {
                    "passed": False,
                    "problems": [f"runtime unavailable: {str(exc).splitlines()[0][:160]}"],
                    "failed_at": "runtime",
                    "checks": ["runtime"],
                }
                return guard, None, meta

            try:
                dry = loader.run(rendered_sql, dataset_id=(project, dataset), dry_run=True)
            except (QueryCancelled, RequestDeadlineExceeded):
                raise
            except Exception as exc:
                guard = {
                    "passed": False,
                    "problems": [f"dry run failed: {str(exc).splitlines()[0][:160]}"],
                    "failed_at": "dry run",
                    "checks": ["read-only", "tables", "columns", "joins", "dry run"],
                }
                return guard, None, meta

            estimated = int(dry.get("totalBytesProcessed") or 0)
            if max_bytes and estimated > int(max_bytes):
                guard = {
                    "passed": False,
                    "problems": [f"dry run estimated {estimated} bytes, exceeding cap of {int(max_bytes)} bytes"],
                    "failed_at": "cost gate",
                    "checks": ["read-only", "tables", "columns", "joins", "dry run", "cost gate"],
                }
                return guard, None, meta

            guard = {"passed": True, "problems": [], "failed_at": None,
                     "checks": ["read-only", "tables", "columns", "joins", "dry run", "cost gate"]}
            if on_ready is not None:
                on_ready(guard, meta)
            if on_stage is not None:
                on_stage("execute")
            if job_id is not None:
                from kge.adapters.warehouse import jobs
                return guard, jobs.submit(loader, rendered_sql, (project, dataset), job_id, max_bytes), meta
            try:
                data = loader.run(rendered_sql, dataset_id=(project, dataset), max_bytes_billed=int(max_bytes) if max_bytes else None,
                                  **({"on_progress": on_progress} if on_progress else {}))
            except (QueryCancelled, RequestDeadlineExceeded):
                raise
            except Exception as exc:
                guard = {
                    "passed": False,
                    "problems": [f"execution failed: {str(exc).splitlines()[0][:160]}"],
                    "failed_at": "execute",
                    "checks": ["read-only", "tables", "columns", "joins", "dry run", "cost gate", "execute"],
                }
                return guard, None, meta

            from kge.adapters.warehouse.jobs import record_completed
            record_completed(loader, data)
            rows = typed_rows(data)
            guard = {
                "passed": True,
                "problems": [],
                "failed_at": None,
                "checks": ["read-only", "tables", "columns", "joins", "dry run", "cost gate"],
            }
            return guard, rows, meta

        guard = {"passed": False, "problems": [f"unsupported runtime dialect {dialect!r}"], "failed_at": "runtime", "checks": ["runtime"]}
        return guard, None, meta

    def poll(self, handle):
        runtime_id, job = handle["runtime_id"], handle["job"]
        if runtime_id == "reference":
            return self.warehouses.poll_query(job)
        if self.runtimes[runtime_id]["dialect"] == "bigquery":
            from kge.adapters.warehouse import jobs
            return jobs.poll(self._bigquery_loader(runtime_id)[0], job)
        from kge.adapters.warehouse import localjobs
        return localjobs.poll(job)

    def release(self, handle):
        runtime_id, job = handle["runtime_id"], handle["job"]
        if runtime_id == "reference":
            return self.warehouses.release_query(job)
        if self.runtimes[runtime_id]["dialect"] != "bigquery":
            from kge.adapters.warehouse import localjobs
            localjobs.release(job)

    def cancel(self, handle):
        runtime_id, job = handle["runtime_id"], handle["job"]
        if runtime_id == "reference":
            return self.warehouses.cancel_query(job)
        if self.runtimes[runtime_id]["dialect"] == "bigquery":
            from kge.adapters.warehouse import jobs
            return jobs.cancel(self._bigquery_loader(runtime_id)[0], job)
        from kge.adapters.warehouse import localjobs
        localjobs.cancel(job)

    def _access_problem(self, runtime_id: str, needed_units: set[str], readable_units: set[str] | None) -> str | None:
        if readable_units is None:
            return None
        rt_units = set(self.runtimes[runtime_id].get("units") or [])
        denied = needed_units - readable_units
        if not denied and runtime_id != "reference" and rt_units:
            if not rt_units & readable_units:
                denied = rt_units
            elif not rt_units.issubset(readable_units):
                denied = needed_units - (rt_units & readable_units)
        return f"{runtime_id}: access denied for unit {', '.join(sorted(denied))}" if denied else None

    def check_reference_sql(self, sql: str, unit: str, readable_units: set[str] | None,
                            runtime_id: str = "reference", dialect: str = "duckdb") -> Allowlist:
        """Apply Ask's unit access and schema allowlist without a warehouse or graph query."""
        needed_units, tables, _ = self._extract_needed([{"sql": sql, "unit": unit}])
        curated = set(self.warehouses.curated or [])
        needed_units |= {self.ref_table_units[t] for t in tables if t in self.ref_table_units} - curated
        problem = self._access_problem(runtime_id, needed_units, readable_units)
        if problem:
            raise QueryRefused(problem)
        allow = self.reference_allowlists.get(unit)
        if allow is None:
            raise QueryRefused(f"no reference allowlist for unit {unit}")
        problems = check(sql, allow, dialect=dialect)
        if problems:
            raise QueryRefused("; ".join(problems))
        return allow

    def resolve_reference_sql(
        self,
        sql: str,
        *,
        unit: str | None = None,
        runtimes: list[str] | None = None,
        runtime_id: str | None = None,
        readable_units: set[str] | None = None,
    ) -> dict | None:
        """Resolve and execute a reference_sql query against a specific or selected external runtime, or return None for reference."""
        from kge.adapters.warehouse import (
            check_readonly_reference_sql,
            extract_figure,
            strip_schema_placeholder,
        )

        cleaned = strip_schema_placeholder(sql)
        try:
            duck_sql = sqlglot.transpile(cleaned, read="bigquery", write="duckdb")[0]
        except Exception:
            duck_sql = cleaned
        check_readonly_reference_sql(duck_sql, dialect="duckdb")
        chosen_unit = unit or (self.warehouses.units[0] if getattr(self.warehouses, "units", None) else "")

        target_rt = runtime_id
        if target_rt is not None:
            if target_rt not in self.runtimes:
                raise QueryRefused(f"unknown runtime {target_rt!r}")
        elif self.runtimes_active(runtimes):
            sel = self.select_runtime(
                [{"sql": duck_sql, "unit": chosen_unit, "allow": None}],
                runtimes=runtimes,
                readable_units=readable_units,
            )
            if not sel.get("ok"):
                raise QueryRefused(sel.get("reason") or "no runtime available")
            target_rt = sel["runtime_id"]
        else:
            target_rt = "reference"

        if target_rt == "reference":
            return None

        rt = self.runtimes.get(target_rt)
        if not rt:
            raise QueryRefused(f"unknown runtime {target_rt!r}")

        allow = self.check_reference_sql(duck_sql, chosen_unit, readable_units, target_rt)
        renamed_duck_sql, rt_allow, phys_tables = self._rename_sql_for_runtime(duck_sql, chosen_unit, target_rt, allow)
        dialect = rt.get("dialect") or "duckdb"
        rendered_sql = self._render_for_dialect(renamed_duck_sql, dialect)
        check_readonly_reference_sql(rendered_sql, dialect=dialect)
        problems = check(rendered_sql, rt_allow, dialect=dialect)
        if problems:
            raise QueryRefused("; ".join(problems))

        if dialect == "duckdb":
            if str(rt.get("execution_identity") or "").strip():
                raise QueryRefused("execution_identity cannot be assumed")
            limits = QueryLimits(seconds=60.0, rows=1000)
            with self._bounded_duckdb(target_rt, limits) as con:
                con.execute(f"EXPLAIN {rendered_sql}")
                res = con.execute(rendered_sql)
                cols = [d[0] for d in (res.description or [])]
                rows = res.fetchmany(limits.rows)
                return {
                    "sql": rendered_sql,
                    "reference_sql": duck_sql,
                    "runtime": self.runtime_summary(target_rt),
                    "physical_tables": phys_tables,
                    "columns": cols,
                    "rows": rows,
                    "figure": extract_figure(rows),
                    "bytes_estimated": 0,
                }

        if dialect == "bigquery":
            from kge.adapters.bigquery import typed_rows

            try:
                loader, project, dataset = self._bigquery_loader(target_rt)
            except PermissionError as exc:
                raise QueryRefused(str(exc)) from exc
            max_bytes = bigquery_max_bytes_billed(10_737_418_240)
            dry = loader.run(rendered_sql, dataset_id=(project, dataset), dry_run=True)
            estimated = int(dry.get("totalBytesProcessed") or 0)
            if max_bytes and estimated > int(max_bytes):
                raise QueryRefused(
                    f"dry run estimated {estimated} bytes, which exceeds BIGQUERY_MAX_BYTES_BILLED cap of {int(max_bytes)} bytes"
                )
            data = loader.run(rendered_sql, dataset_id=(project, dataset), max_bytes_billed=int(max_bytes) if max_bytes else None)
            cols = [f.get("name", "") for f in data.get("schema", {}).get("fields", [])]
            rows = typed_rows(data)[:1000]
            return {
                "sql": rendered_sql,
                "reference_sql": cleaned,
                "runtime": self.runtime_summary(target_rt),
                "physical_tables": phys_tables,
                "columns": cols,
                "rows": rows,
                "figure": extract_figure(rows),
                "bytes_estimated": estimated,
            }

        raise QueryRefused(f"unsupported runtime dialect {dialect!r}")

    def close(self) -> None:
        for con in self._duck_cons.values():
            try:
                con.close()
            except Exception:
                pass
        self._duck_cons.clear()
        self._bq_loaders.clear()
