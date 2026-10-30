"""The graph the engine reads (Graph): the graph store's queries, the client's domain read from the graph once, retrieval
through the retrieval seam, the access mirror (Rights) and the catalogue (Catalogue) read once per release, and the runtime
overlay's steward decisions. WRITES names the keywords that send a query to the graph's one write connection; ACCESS and
CATALOGUE, the types a write must name to drop the Rights or the Catalogue read."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import uuid
from dataclasses import dataclass

from kge.adapters.retrieval import open_retrieval, pinned
from kge.ports import Retrieval
from kge.compiler.checks import CONVERSION
from kge.adapters.model import make_embedder
from kge.pipeline.overlay import REQUESTED, Journal, OverlayError, record, text_sha256
from kge.pipeline.privacy import assess_privacy, redact
from kge.pipeline.profile import Profile, ProfileError
from kge.adapters.graphstore import batch, connect, open_db
from kge.pipeline.state import rederive
from kge.pipeline import warehouse_versions
from kge.services.ask.engine.question import CANDIDATE


DECISIONS = ("accept", "reject")
UPSTREAM = re.compile(r"(\w+) upstream\b")
WRITES = re.compile(r"\b(?:CREATE|MERGE|SET|DELETE|REMOVE|DROP|ALTER|COPY|CHECKPOINT|INSTALL|LOAD|ATTACH|DETACH"
                    r"|BEGIN|COMMIT|ROLLBACK)\b", re.I)
ACCESS = re.compile(r"\b(?:Principal|CAN_READ|CAN_WRITE|MAY_PERFORM|Process)\b")
CATALOGUE = re.compile(r"\b(?:Tbl|Col|HAS_COLUMN|JOINS_TABLE|Release)\b")
TIERS = ("gold", "silver", "bronze")
OPEN_TRIES = 3
CITE_AS = {"gold": "authority", "silver": "attributed"}
GLOSSARY = "the pack's glossary"


@dataclass(frozen=True)
class Rights:
    """The access mirror of one release, as the graph holds it, by principal id: each principal's role (in id order), the
    units it reads (CAN_READ), the processes it may perform (MAY_PERFORM), and those who write terms (CAN_WRITE); release
    is the identity of the release it was read from (Graph.identity)."""
    release: dict
    roles: dict
    reads: dict
    processes: dict
    writers: frozenset


@dataclass(frozen=True)
class Catalogue:
    """The catalogue of one release, as the graph holds it: each unit's tables (by name), the declared joins from each
    unit's tables ({ft, fc, tt, tc}), each table's columns (by <unit>.<table>), and the landed release the graph was
    loaded from (Graph.release); release is the identity of the release it was read from (Graph.identity)."""
    release: dict
    tables: dict
    joins: dict
    columns: dict
    landed: dict | None


class Graph:
    """One graph database, shared by every request: the profile's, graph.ladybug in the folder of its client's graph store
    that the current artifact's model hash names (Profile.graph_path), read once, when the graph is opened: a release
    that swaps current later is served from the next start. On Ladybug (kge/adapters/graphstore/ladybug.py). The API answers requests on
    several threads at once, and a steward's decision writes while others read. Ladybug's way to use one database from
    many threads, kept from Kuzu, which it forks, is to give each its own connection to it. So every read runs on a
    connection of its own, opened and closed in the call, as a warehouse query runs on a cursor of its own: reads run side by side, and none reads another's result. Writes share
    one connection: the store allows one write transaction at a time (Ladybug unless enable_multi_writes, which is
    off) and refuses a second rather than queue it, and a connection runs its queries one at a time. A write commits
    before q returns, so a read after it sees it.

    A steward's decision is the runtime overlay: once its graph writes have committed, it is appended to the journal in
    the client's graph store (kge/pipeline/overlay.py), which every later load and pipeline replay, so it outlives this
    graph."""

    def __init__(self, profile: Profile):
        """The profile's graph, its current read once: the graph opened is the one this process serves until it
        restarts. Its generation and its chunks table (chunks) are pinned together as it opens: a server's from one read
        of its hash's release record (Profile.released), which a release writes whole, so the two are always one
        release's and its identity (Profile.identity_of) names them; a graph no record names has its identity read
        before and after it opens, and is opened again where a load moved it meanwhile (open_unreleased). A path that does not exist is refused here, since opening a graph that is not there makes an empty
        one that fails later, unclearly. writes counts the queries that may write, so the run trace reads the
        fingerprint once per write."""
        profile = profile.pin()
        try:
            path = profile.graph_path()
        except ProfileError as exc:
            raise FileNotFoundError(str(exc)) from None
        if not path.exists():
            raise FileNotFoundError(f"{path} does not exist: build it with make graph and make pipeline")
        self.profile = profile
        record = profile.released()
        if record is not None:
            self.db = open_db(path, profile.run_dir() / "api.pid", record.get("graph"))
            self.chunks = pinned(profile, record)
            self.identity = profile.identity_of(record)
        else:
            self.open_unreleased(path)
        self.conn = connect(self.db)
        self.embedder = make_embedder()
        self.writes = 0
        self._domain: dict | None = None
        self._rights: Rights | None = None
        self._catalogue: Catalogue | None = None
        self._snapshot_lock = threading.Lock()
        self._journal: Journal | None = None
        self._model_hash: str | None = None

    def open_unreleased(self, path) -> None:
        """A graph no release record names (a candidate, or a graph that is its own file): its identity read before and
        after it opens, and opened again where a load moved it meanwhile."""
        for _ in range(OPEN_TRIES):
            named = self.profile.identity()
            self.db = open_db(path, self.profile.run_dir() / "api.pid")
            self.chunks = pinned(self.profile)
            self.identity = self.profile.identity()
            if self.identity == named:
                return
            self.db.close()
        raise RuntimeError(f"{path}: it changed on each of {OPEN_TRIES} tries to open it: open it again")

    def close(self) -> None:
        self.conn.close()
        self.db.close()

    def reader(self):
        """A connection of its own for reads. Close it when done (with g.reader() as conn)."""
        return connect(self.db)

    def q(self, cypher: str, params: dict | None = None) -> list[dict]:
        """The query's rows, nulls as None rather than pandas' NaN. A query that may write bumps writes once it is done,
        failed or not, so a fingerprint read while it ran keeps the stamp it saw."""
        if not WRITES.search(cypher):
            with self.reader() as conn, conn.execute(cypher, params or {}) as result:
                df = result.get_as_df()
        else:
            try:
                df = self.conn.execute(cypher, params or {}).get_as_df()
            finally:
                self.writes += 1
                with self._snapshot_lock:
                    if ACCESS.search(cypher):
                        self._rights = None
                    if CATALOGUE.search(cypher):
                        self._catalogue = None
        df = df.astype(object).where(df.notna(), None)
        return df.to_dict("records")

    def terms(self) -> list[str]:
        return [r["t.name"] for r in self.q("MATCH (t:Term) RETURN t.name")]

    def domain(self) -> dict:
        """The client's domain, as its compiled pack loaded it into the graph. Read once, then kept: one process serves one
        client, and nothing the graph is asked or told at run time changes it.

          units       the sources, in the pack's order: pair names, unit words and the agent's contract follow it
          labels      each unit's label; keys, its customer-like key column; key_tables, the table whose primary key
                      that column is
          restricted  (unit, table, column) of each column a restricted-attribute Policy GOVERNS, in the pack's order:
                      refused in every arm
          aliases     each phrase that names a term: the term's name, or __candidate__:<name> for a candidate not in the
                      glossary yet, in the pack's order; unit_words, each phrase that names a unit, the same way
          calendars   each calendar's year_start_month and quarter_label, by id
          periods     each table's period binding, by <unit>.<table>: its column, kind, calendar, and the table and
                      column a month or a quarter rolls up through (None for none)
          modes, named_periods, group_by, entity_words
                      since meta-model 1.1, the phrases a context's query reads: a time mode (as_reported, restated),
                      a named period (latest), a dimension to group by (its column's id) and an entity a question
                      names by id (its id), each in the pack's order
          acronyms    each acronym and the columns it spells, by id
          curated     the curated sources (a certified cross-reference), in the pack's order: no unit's
          vocabulary  since meta-model 1.2, the rest of the parser's vocabulary, {kind, params} in the pack's order
                      (kge/semantics/vocabulary.py): the intents' phrases, the value vocabularies and the predicates; mailboxes,
                      the local parts of the client's functional mailboxes
          cites       what the policy that restricts each restricted column cites, by <unit>.<table>.<column>
          restricted_phrases
                      what people call a restricted column besides its name, as its policy gives it, by
                      <unit>.<table>.<column> (e-mail, birthdate): a question that says one asks for the column
          title       the pack's title, on the compiled model the graph was loaded from
          not_joinable
                      each column the pack declares not joinable to a table of another unit (NOT_JOINABLE_TO), in id
                      order: {column, unit, name, table, table_unit, note}
          currencies  the certified measures' currencies; conversions, (from, to, conversion) for each measure converted
                      from another currency (<rate>_from_<currency>, S10), each once; both leave out onboarded units'
                      measures, whose currencies (and those they convert from) are term_currencies, by term name
          onboarded   the units onboarding drafted into the artifact (onboarded_units), in id order
          business    each column's business name, <business table>.<business column>, by <unit>.<table>.<column>: the
                      name the pack's column aliases give a column the warehouse names physically (since pack 1.5.0)

        RuntimeError for a graph the pipeline or a store made that no pack compiled for this meta-model was loaded
        into."""
        if self._domain is None:
            try:
                self._domain = self.read_domain()
            except RuntimeError as exc:
                if "Binder exception" not in str(exc):
                    raise
                raise RuntimeError(f"the graph holds no client domain ({str(exc).splitlines()[0]}): it was not loaded from a "
                                   "pack compiled for this platform's meta-model: make graph compiles the pack and loads it") from None
        return self._domain

    def read_domain(self) -> dict:
        """The client's domain as the graph holds it. An onboarded unit's currencies are held for its own terms only
        (G042): a GBP measure onboarded beside the estate does not make GBP answerable for another unit's term."""
        units = self.q("MATCH (u:Unit) RETURN u.id AS id, u.label AS label, u.key_column AS key ORDER BY u.ordinal, u.id")
        order = [u["id"] for u in units]
        keys = {u["id"]: u["key"] for u in units}
        pk: dict[tuple[str, str], list[str]] = {}
        for r in self.q("MATCH (t:Tbl)-[:HAS_COLUMN]->(c:Col) WHERE c.is_key RETURN t.unit AS unit, t.name AS tbl, c.name AS col "
                        "ORDER BY t.id, c.name"):
            pk.setdefault((r["unit"], r["tbl"]), []).append(r["col"])
        key_tables = {u: t for (u, t), cols in pk.items() if u in keys and cols == [keys[u]]}
        restricted = [(r["unit"], r["tbl"], r["col"]) for r in self.q(
            "MATCH (p:Policy)-[g:GOVERNS]->(c:Col) WHERE p.kind = 'restricted_attribute' "
            "RETURN c.unit AS unit, c.tbl AS tbl, c.name AS col ORDER BY g.ordinal, c.id")]
        named = self.q("MATCH (a:Alias)-[:ALIAS_OF]->(t:Term) RETURN a.phrase AS phrase, a.ordinal AS n, t.name AS name")
        named += [{**r, "name": CANDIDATE + r["name"]} for r in self.q(
            "MATCH (a:Alias) WHERE a.kind = 'candidate' RETURN a.phrase AS phrase, a.ordinal AS n, a.target AS name")]
        words = self.q("MATCH (a:Alias)-[:ALIAS_OF]->(u:Unit) RETURN a.phrase AS phrase, a.ordinal AS n, u.id AS name")
        calendars = {r["id"]: {"year_start_month": int(r["start"]), "quarter_label": r["label"]} for r in self.q(
            "MATCH (c:Calendar) RETURN c.id AS id, c.year_start_month AS start, c.quarter_label AS label ORDER BY c.id")}
        periods = {r["tbl"]: {"column": r["col"], **{k: r[k] for k in ("kind", "calendar", "rollup_table", "rollup_column")}} for r in self.q(
            "MATCH (t:Tbl)-[p:PERIOD_COLUMN]->(c:Col) RETURN t.id AS tbl, c.name AS col, p.kind AS kind, p.calendar AS calendar, "
            "p.rollup_table AS rollup_table, p.rollup_column AS rollup_column ORDER BY t.id")}
        in_order = lambda rows: {r["phrase"]: r["name"] for r in sorted(rows, key=lambda r: (r["n"], r["phrase"]))}  # noqa: E731
        semantic = self.q("MATCH (a:Alias) WHERE a.kind IN ['time_mode', 'period', 'group_by', 'entity'] "
                          "RETURN a.phrase AS phrase, a.kind AS kind, a.target AS name, a.ordinal AS n")
        kinds = {k: in_order([r for r in semantic if r["kind"] == k]) for k in ("time_mode", "period", "group_by", "entity")}
        business = {r["col"]: r["phrase"] for r in self.q(
            "MATCH (a:Alias)-[:ALIAS_OF]->(c:Col) WHERE a.kind = 'column' RETURN a.phrase AS phrase, c.id AS col ORDER BY col")}
        acronyms: dict[str, list[str]] = {}
        for r in self.q("MATCH (c:Col)-[:SPELLED_WITH]->(a:Acronym) RETURN a.id AS acronym, c.id AS col ORDER BY acronym, col"):
            acronyms.setdefault(r["acronym"], []).append(r["col"])
        curated = [r["id"] for r in self.q("MATCH (c:CuratedSource) RETURN c.id AS id, c.ordinal AS n ORDER BY n, id")]
        vocab = [{"kind": r["kind"], "params": json.loads(r["params"])} for r in self.q(
            "MATCH (v:Vocabulary) RETURN v.kind AS kind, v.params AS params, v.ordinal AS n ORDER BY kind, n")]
        cites, phrases = {}, {}
        for r in self.q("MATCH (p:Policy)-[:GOVERNS]->(c:Col) WHERE p.kind = 'restricted_attribute' RETURN c.id AS col, "
                        "p.id AS policy, p.params AS params ORDER BY col"):
            params = json.loads(r["params"])
            cites[r["col"]] = params.get("cite") or r["policy"]
            if (params.get("phrases") or {}).get(r["col"]):
                phrases[r["col"]] = list(params["phrases"][r["col"]])
        titles = [r["title"] for r in self.q("MATCH (m:GraphModel) RETURN m.title AS title ORDER BY title")]
        not_joinable = [{"column": r["col"], "unit": r["unit"], "name": r["name"], "table": r["tbl"], "table_unit": r["to_unit"],
                         "note": r["note"]} for r in self.q(
            "MATCH (c:Col)-[n:NOT_JOINABLE_TO]->(t:Tbl) RETURN c.id AS col, c.unit AS unit, c.name AS name, t.id AS tbl, "
            "t.unit AS to_unit, n.note AS note ORDER BY col, tbl")]
        measures = self.q("MATCH (m:Measure) RETURN m.id AS id, m.currency AS currency, m.conversion AS conversion ORDER BY m.id")
        onboarded = self.onboarded_units()
        own = lambda r: r["id"].split(".", 1)[0] in onboarded   # noqa: E731
        term_currencies: dict[str, list[str]] = {}
        if onboarded:
            names = {r["id"]: r["term"] for r in self.q("MATCH (m:Measure)-[:MEASURE_OF]->(t:Term) RETURN m.id AS id, t.name AS term")}
            for r in measures:
                if own(r) and r["id"] in names:
                    held = {r["currency"]} if r["currency"] else set()
                    held |= {m.group(1) for m in [CONVERSION.fullmatch(r["conversion"] or "")] if m}
                    term_currencies[names[r["id"]]] = sorted(set(term_currencies.get(names[r["id"]], [])) | held)
            measures = [r for r in measures if not own(r)]
        converted = sorted({(m.group(1), r["currency"], r["conversion"]) for r in measures
                            if (m := CONVERSION.fullmatch(r["conversion"] or ""))})
        return {"units": order, "labels": {u["id"]: u["label"] for u in units}, "keys": keys, "key_tables": key_tables,
                "restricted": restricted, "aliases": in_order(named), "unit_words": in_order(words), "calendars": calendars,
                "periods": periods, "modes": kinds["time_mode"], "named_periods": kinds["period"], "group_by": kinds["group_by"],
                "entity_words": kinds["entity"], "acronyms": acronyms, "curated": curated,
                "vocabulary": [v for v in vocab if v["kind"] != "mailbox"],
                "mailboxes": sorted(v["params"]["mailbox"] for v in vocab if v["kind"] == "mailbox"), "cites": cites,
                "restricted_phrases": phrases,
                "title": titles[0] if len(titles) == 1 else None, "not_joinable": not_joinable,
                "currencies": sorted({r["currency"] for r in measures if r["currency"]}), "conversions": converted,
                "term_currencies": term_currencies, "onboarded": sorted(onboarded), "business": business}

    def onboarded_units(self) -> set[str]:
        """The units onboarding drafted into the artifact this graph was loaded from: those its drafts.json names
        (kge/compiler/emit.py writes it only when a unit has a draft). An empty set when there is none, so a client with no
        onboarded unit reads its domain exactly as before."""
        try:
            folder = self.profile.artifacts / self.profile.client / self.profile.model_hash()
            items = json.loads((folder / "drafts.json").read_text()).get("items")
        except (OSError, TypeError, ValueError, AttributeError):
            return set()
        return {x["unit"] for x in items if isinstance(x, dict) and isinstance(x.get("unit"), str)} if isinstance(items, list) else set()

    def warehouse_versions(self) -> dict[str, int]:
        """The warehouse version of each onboarded unit the artifact this graph was loaded from names (its drafts.json,
        kge/pipeline/warehouse_versions.py): the rows its checks were run on. Empty when it names none, so a client with no
        onboarded unit opens its warehouses exactly as before."""
        h = self.profile.model_hash()
        if h is None:
            return {}
        return warehouse_versions.named(self.profile.artifacts / self.profile.client / h)

    def warehouse_inputs(self) -> dict[str, str]:
        """The digest of the inputs each named warehouse version's rows were made from, as the artifact names it."""
        h = self.profile.model_hash()
        if h is None:
            return {}
        return warehouse_versions.inputs_named(self.profile.artifacts / self.profile.client / h)

    def mailboxes(self) -> frozenset[str]:
        """The client's functional mailboxes: an address at one is no person's (kge/pipeline/privacy.py)."""
        return frozenset(self.domain()["mailboxes"])

    def release(self) -> dict | None:
        """The landed release the graph was loaded from (kge/landing/zone.py): {id, client, policy, as_of}; None if it names
        none. From the Catalogue read."""
        landed = self.catalogue().landed
        return dict(landed) if landed is not None else None

    def implementations(self, term: str) -> list[dict]:
        rows = self.q("MATCH (c:Col)-[m:MAPS_TO {certified: true}]->(t:Term {name: $t}) RETURN c.unit AS unit, c.tbl AS tbl, c.name AS col, "
                      "c.data_type AS dtype, c.grain AS grain, c.currency AS currency, c.calendar AS calendar, c.conversion AS conversion, "
                      "m.certified_by AS certified_by, m.certified_on AS certified_on ORDER BY c.unit", {"t": term})
        return rows

    def standards(self, term: str) -> list[dict]:
        """A term's mappings to the industry standards the platform preloads (meta-model 1.3.0): each concept with its
        SKOS relation (read with the concept as the object), the steward's note, its standard and that standard's
        version, and its IRI, as plain text. For the map alone: no answer, context package or A/B run reads them. A graph
        compiled before 1.3.0 holds no standard on its concepts, and maps no term to one."""
        if "standard" not in self.properties("OntologyConcept"):
            return []
        return self.q("MATCH (t:Term {name: $t})-[m:MAPPED_TO]->(o:OntologyConcept) RETURN o.id AS concept, o.label AS label, "
                      "o.iri AS iri, m.relation AS relation, m.note AS note, o.standard AS standard, "
                      "o.standard_version AS version, o.sub_graph AS sub_graph ORDER BY o.sub_graph, o.standard, o.id", {"t": term})

    def properties(self, table: str) -> set[str]:
        """The properties a node table of the loaded graph declares."""
        return {r["name"] for r in self.q(f"CALL table_info('{table}') RETURN name")}

    def defining_docs(self, term: str) -> list[dict]:
        return self.q("MATCH (d:Document)-[:DEFINES]->(t:Term {name: $t}) OPTIONAL MATCH (d)-[:SUPERSEDED_BY]->(n:Document) "
                      "RETURN d.id AS id, d.title AS title, d.shelf AS shelf, d.version AS version, d.published_at AS published_at, "
                      "n.id AS superseded_by ORDER BY d.published_at, d.id", {"t": term})

    def uncertified_mentions(self, term: str, limit: int = 4) -> list[dict]:
        return self.q("MATCH (d:Document {shelf: 'uncertified'})-[:HAS_CHUNK]->(c:Chunk)-[:MENTIONS]->(t:Term {name: $t}) "
                      "RETURN d.id AS id, d.title AS title, d.kind AS kind, d.tier AS tier, d.ai_ready AS ai_ready, c.id AS chunk, "
                      "CASE WHEN c.readiness = 'embed-prohibited' THEN '[withheld: this chunk is embed-prohibited]' ELSE left(c.text, 160) END AS excerpt "
                      "ORDER BY d.id, c.id LIMIT $k", {"t": term, "k": limit})

    def certification(self, ids: list[str]) -> dict[str, dict]:
        """What upstream AI certification landed for each document, as the graph holds it."""
        if not ids:
            return {}
        rows = self.q("MATCH (d:Document) WHERE d.id IN $ids RETURN d.id AS doc, d.title AS title, d.shelf AS shelf, d.unit AS unit, d.owner AS owner, "
                      "d.version AS version, d.ai_ready AS ai_ready, d.tier AS tier, d.cert_from AS cert_from, d.cert_to AS cert_to",
                      {"ids": list(ids)})
        return {r["doc"]: r for r in rows}

    def kept(self, name: str, read):
        """The per-release read held as name (_rights, _catalogue), made by read() at most once for the release identity the
        graph pinned, under one lock, and only read after that, so requests on many threads share one."""
        held = getattr(self, name)
        if held is not None and held.release == self.identity:
            return held
        with self._snapshot_lock:
            held = getattr(self, name)
            if held is None or held.release != self.identity:
                held = read()
                setattr(self, name, held)
            return held

    def catalogue(self) -> Catalogue:
        """The catalogue every governed read's allowlist reads (kge/pipeline/sqlguard.py build_allowlist: a unit's tables,
        their columns and declared joins; the landed release), read once per release and kept as rights() is: a preview
        or a query sends the graph store none of it. Only a load writes these types; a write through q naming one
        (CATALOGUE) drops it all the same. The engine reads it as it opens."""
        return self.kept("_catalogue", self.read_catalogue)

    def read_catalogue(self) -> Catalogue:
        """The catalogue as the graph holds it now, in one query per kind."""
        tables: dict[str, set[str]] = {}
        for r in self.q("MATCH (t:Tbl) RETURN t.unit AS unit, t.name AS name"):
            tables.setdefault(r["unit"], set()).add(r["name"])
        joins: dict[str, list[dict]] = {}
        for r in self.q("MATCH (a:Tbl)-[j:JOINS_TABLE]->(b:Tbl) RETURN a.unit AS unit, j.from_table AS ft, j.from_column AS fc, "
                        "j.to_table AS tt, j.to_column AS tc"):
            joins.setdefault(r.pop("unit"), []).append(r)
        columns: dict[str, list[str]] = {}
        for r in self.q("MATCH (t:Tbl)-[:HAS_COLUMN]->(c:Col) RETURN t.id AS tbl, c.name AS col"):
            columns.setdefault(r["tbl"], []).append(r["col"])
        rows = self.q("MATCH (r:Release) RETURN r.id AS id, r.client AS client, r.policy AS policy, r.as_of AS as_of ORDER BY r.id")
        return Catalogue(release=self.identity, tables={u: frozenset(t) for u, t in tables.items()},
                         joins={u: tuple(j) for u, j in joins.items()}, columns={t: tuple(c) for t, c in columns.items()},
                         landed=rows[0] if len(rows) == 1 else None)

    def unit_tables(self, unit: str) -> set[str]:
        """The unit's tables, by name."""
        return set(self.catalogue().tables.get(unit, ()))

    def unit_joins(self, unit: str) -> list[dict]:
        """The declared joins from the unit's tables: {ft, fc, tt, tc} each."""
        return [dict(j) for j in self.catalogue().joins.get(unit, ())]

    def rights(self) -> Rights:
        """The access mirror every request's access check reads (who reads which unit, performs which process, writes
        terms), read from the graph store once per release and answered from memory after that: an access check sends
        the graph store no query. It is kept for the release identity the graph pinned as it opened (identity); a new
        release, the same hash at a new generation too, is served by a new graph with its own (kge/app.py switch, or a
        restart). A write through q that names a type of the mirror (ACCESS: a steward's accept gives the term writers
        CAN_WRITE on the new term) drops it, and the next check reads it again. The engine reads it as it opens."""
        return self.kept("_rights", self.read_rights)

    def read_rights(self) -> Rights:
        """The access mirror as the graph holds it now, in one query per kind."""
        def edges(cypher: str) -> dict:
            held: dict[str, set[str]] = {}
            for r in self.q(cypher):
                held.setdefault(r["p"], set()).add(r["x"])
            return {k: frozenset(v) for k, v in held.items()}
        return Rights(release=self.identity,
                      roles={r["id"]: r["role"] for r in self.q("MATCH (p:Principal) RETURN p.id AS id, p.role AS role ORDER BY id")},
                      reads=edges("MATCH (p:Principal)-[:CAN_READ]->(u:Unit) RETURN p.id AS p, u.id AS x"),
                      processes=edges("MATCH (p:Principal)-[:MAY_PERFORM]->(x:Process) RETURN p.id AS p, x.id AS x"),
                      writers=frozenset(r["p"] for r in self.q("MATCH (p:Principal)-[:CAN_WRITE]->(:Term) RETURN DISTINCT p.id AS p")))

    def readable_units(self, principal: str) -> set[str]:
        return set(self.rights().reads.get(principal, ()))

    def processes(self, principal: str) -> set[str]:
        return set(self.rights().processes.get(principal, ()))

    def may(self, principal: str, process: str) -> bool:
        """tagging, certification or querying: the process an identity is bound to."""
        return process in self.rights().processes.get(principal, ())

    def can_write_terms(self, principal: str) -> bool:
        return principal in self.rights().writers

    def principal_ids(self) -> list[str]:
        """The ordered identity roster."""
        return list(self.rights().roles)

    def principals(self) -> list[dict]:
        return [self._principal({"id": who, "role": role}) for who, role in self.rights().roles.items()]

    def principal(self, who: str) -> dict:
        """One principal and its grants; its id alone for one the graph does not hold."""
        roles = self.rights().roles
        return self._principal({"id": who, "role": roles[who]}) if who in roles else {"id": who}

    def _principal(self, row: dict) -> dict:
        return {**row, "reads": sorted(self.readable_units(row["id"]), key=self.domain()["units"].index),
                "processes": sorted(self.processes(row["id"])), "writes_terms": self.can_write_terms(row["id"])}

    def join_access(self, principal: str, units: list[str]) -> dict:
        """The join-time access rule, read off the same CAN_READ mirror as everything else.

        A combination across warehouses proceeds only if the principal can read
        every warehouse in it. All or nothing: no partial answer from the units
        it can read, because a partial combination is a different figure.
        """
        readable = self.readable_units(principal)
        involved = sorted(set(units), key=self.domain()["units"].index)
        denied = [u for u in involved if u not in readable]
        return {"principal": principal, "warehouses_involved": involved, "allowed_warehouses": [u for u in involved if u in readable],
                "denied_warehouses": denied, "allowed": not denied}

    def no_join(self, a: str, b: str) -> dict | None:
        rows = self.q("MATCH (x:Unit {id: $a})-[n:NO_JOIN]-(y:Unit {id: $b}) RETURN n.asserted_by AS asserted_by, n.asserted_on AS asserted_on, n.note AS note", {"a": a, "b": b})
        return rows[0] if rows else None

    def data_dictionaries(self, units: list[str]) -> list[str]:
        """The data dictionaries of these units, by id: the documents a no-join fact between them rests on."""
        return [r["id"] for r in self.q("MATCH (d:Document {kind: 'data_dictionary'}) WHERE d.unit IN $u RETURN d.id AS id ORDER BY d.id",
                                        {"u": sorted(set(units))})]

    def certified_join(self, a: str, b: str) -> dict | None:
        """The certified join fact between two units, either way round: who asserted it, when, its note and the declared
        join it runs through. None for a pair with none (every pair but those a certified cross-reference links)."""
        rows = self.q("MATCH (x:Unit)-[c:CERTIFIED_JOIN]-(y:Unit) WHERE x.id = $a AND y.id = $b RETURN c.asserted_by AS asserted_by, "
                      "c.asserted_on AS asserted_on, c.note AS note, c.declared_join AS declared_join", {"a": a, "b": b})
        return rows[0] if rows else None

    def join_path(self, unit: str, from_table: str, to_table: str) -> list[dict] | None:
        rows = self.q("MATCH p = (a:Tbl {id: $a})-[:JOINS_TABLE* SHORTEST 1..5]-(b:Tbl {id: $b}) "
                      "RETURN properties(rels(p),'from_table') AS ft, properties(rels(p),'from_column') AS fc, "
                      "properties(rels(p),'to_table') AS tt, properties(rels(p),'to_column') AS tc LIMIT 1",
                      {"a": f"{unit}.{from_table}", "b": f"{unit}.{to_table}"})
        if not rows:
            return None
        r = rows[0]
        return [{"from_table": ft, "from_column": fc, "to_table": tt, "to_column": tc} for ft, fc, tt, tc in zip(r["ft"], r["fc"], r["tt"], r["tc"])]

    def column_owners(self, unit: str, column: str) -> list[str]:
        return [r["c.tbl"] for r in self.q("MATCH (c:Col {unit: $u, name: $n}) RETURN c.tbl", {"u": unit, "n": column})]

    def column_type(self, unit: str, table: str, column: str) -> str:
        rows = self.q("MATCH (c:Col {id: $id}) RETURN c.data_type", {"id": f"{unit}.{table}.{column}"})
        return rows[0]["c.data_type"] if rows else ""

    def table_columns(self, unit: str, table: str) -> list[str]:
        """The table's columns, by name, from the Catalogue read."""
        return list(self.catalogue().columns.get(f"{unit}.{table}", ()))

    def candidate_sources(self, phrase: str) -> list[dict]:
        return self.q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:TAGGED]->(t:Tag {family: 'candidate_term'}) WHERE lower(t.value) = lower($p) "
                      "RETURN d.id AS id, d.shelf AS shelf, t.id AS tag, t.confidence AS confidence, t.evidence AS evidence", {"p": phrase})

    @property
    def retrieval(self) -> Retrieval:
        """The retrieval backend (kge/adapters/retrieval/), chosen from RETRIEVAL_BACKEND, else WAREHOUSE_BACKEND, each
        time it is asked for, as the search always was."""
        return open_retrieval(self)

    def retrieve(self, query: str, principal: str, purpose: str, k: int = 5, shelf: str | None = None,
                 tiers: tuple[str, ...] | list[str] | None = None) -> dict:
        """Vector search over the chunks this principal may read for this purpose. shelf: one shelf only.
        tiers: only documents upstream certification landed as AI-ready, at one of these tiers (the
        tier rule); None for no tier filter. Every row says the tier and readiness its document landed with.
        The exact search filters first and scores the survivors, a tie going to the chunk id so a run is repeatable;
        the scan scores everything embedded and filters after, as an index does, to show what that loses."""
        if tiers is not None:
            tiers = list(tiers)
            if not tiers or any(t not in TIERS for t in tiers):
                raise ValueError(f"tiers must name one or more of {', '.join(TIERS)}, not {tiers!r}")
        qv = self.embedder.embed(query)
        units = sorted(self.readable_units(principal))
        return self.retrieval.search(qv, units, purpose, k, shelf, tiers)

    def overlay(self) -> Journal:
        """The runtime overlay's journal of the client this graph was loaded for, as its Release names it, in the profile's
        graph store (kge/pipeline/overlay.py). OverlayError if the graph names no client to journal for, or another client than
        the profile's."""
        if self._journal is None:
            rel = self.release()
            if rel is None:
                raise OverlayError(f"the graph {self.profile.graph_path()} names no landed release, so no client whose overlay "
                                   "journal a decision goes in: load it with make graph")
            if rel["client"] != self.profile.client:
                raise OverlayError(f"the graph {self.profile.graph_path()} was loaded for {rel['client']}, and profile "
                                   f"{self.profile.id} is {self.profile.client}'s: no decision goes in another client's journal")
            self._journal = Journal(self.profile.overlay_dir(), rel["client"])
            try:
                rows = self.q("MATCH (m:GraphModel) RETURN m.id AS id")
            except RuntimeError:
                rows = []
            self._model_hash = rows[0]["id"] if len(rows) == 1 else None
        return self._journal

    def tag_props(self, tag_id: str) -> dict:
        """A tag's properties as the journal keeps them, read by WHERE (see steward_decide)."""
        return self.q("MATCH (t:Tag) WHERE t.id = $id RETURN t.id AS id, t.family AS family, t.value AS value, t.generator AS generator, "
                      "t.confidence AS confidence, t.model AS model, t.prompt_version AS prompt_version, t.created_at AS created_at, "
                      "t.evidence AS evidence", {"id": tag_id})[0]

    def journal(self, principal: str, at: str, decision: str, tag_id: str, machine: dict, chunk: str | None = None,
                doc: str | None = None, term: dict | None = None) -> None:
        """Write-through: the decision whose graph writes have just committed, appended to the overlay journal with the tag
        it wrote (tag_id), the machine tag it decided as it was (machine), and the chunk and the sha256 of its text. An
        append that fails is an OverlayError that says the graph holds the decision and the next make graph will not."""
        journal = self.overlay()
        on = None
        if chunk is not None:
            text = self.q("MATCH (c:Chunk) WHERE c.id = $c RETURN c.text AS text", {"c": chunk})[0]["text"] or ""
            on = {"id": chunk, "doc": doc, "text_sha256": text_sha256(text)}
        try:
            journal.append(record(journal.client, self._model_hash, principal, at, decision, self.tag_props(tag_id), machine, on, term))
        except OverlayError as exc:
            raise OverlayError(f"{machine['id']} was decided in the graph, but the decision was not journaled: {exc}. The graph "
                               "holds it until the next make graph, which will not replay it") from exc

    def matches(self) -> list[dict]:
        """The cross-reference matches certification landed for a steward (kge/graph/load.py), not decided yet, each with the
        units the declared join through its curated table links and what upstream made of it (upstream: proposed or
        rejected, as its evidence opens): a match is a Tag of family xref_match, its value the curated table and the
        match's id, on no chunk."""
        rows = self.q("MATCH (t:Tag) WHERE t.family = 'xref_match' AND t.generator <> 'human' AND NOT EXISTS { MATCH (:Tag)-[:SUPERSEDES]->(t) } "
                      "RETURN t.id AS tag, t.family AS family, t.value AS value, t.confidence AS confidence, t.evidence AS evidence, "
                      "t.model AS method ORDER BY t.confidence DESC, t.id")
        ends = {}
        for r in self.q("MATCH (j:JoinRule)-[:JOIN_FROM]->(a:Tbl), (j)-[:JOIN_TO]->(b:Tbl) WHERE j.through IS NOT NULL AND j.through <> '' "
                        "RETURN j.through AS through, a.unit AS a, b.unit AS b ORDER BY through"):
            ends[r["through"]] = sorted({r["a"], r["b"]}, key=self.domain()["units"].index)
        for r in rows:
            r["through"] = r["value"].rsplit(":", 1)[0]
            r["units"] = ends.get(r["through"], [])
            said = UPSTREAM.match(r["evidence"] or "")
            r["upstream"] = said.group(1) if said else None
        return rows

    def steward_queue(self, principal: str | None = None) -> list[dict]:
        """The queue as a steward sees it. Privacy evidence is redacted: the queue
        says what kind of personal data matched and whether it looks personal or
        functional, never the data itself. It lands redacted (kge/pipeline/privacy.py);
        the queue redacts it again, as a second line. With the principal asking:
        the cross-reference matches certification did not certify, too, for one
        who may certify and reads every unit a match links (Graph.matches)."""
        if principal is not None and self.may(principal, "certification"):
            readable = self.readable_units(principal)
            matched = [{**{k: m[k] for k in ("tag", "family", "value", "confidence", "evidence", "upstream")}, "doc": m["through"], "shelf": "curated",
                        "chunk": None, "units": m["units"]} for m in self.matches() if m["units"] and set(m["units"]) <= readable]
        else:
            matched = []
        return self.chunk_queue() + matched

    def chunk_queue(self) -> list[dict]:
        rows = self.q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:TAGGED]->(t:Tag) WHERE t.generator <> 'human' "
                      "AND (t.family = 'candidate_term' OR (t.family = 'privacy' AND d.shelf = 'certified')) "
                      "AND NOT EXISTS { MATCH (:Tag)-[:SUPERSEDES]->(t) } "
                      "RETURN t.id AS tag, t.family AS family, t.value AS value, t.confidence AS confidence, t.evidence AS evidence, "
                      "d.id AS doc, d.shelf AS shelf, c.id AS chunk ORDER BY t.confidence DESC, t.id")
        for r in rows:
            if r["family"] == "privacy":
                r["assessment"] = assess_privacy(r["value"], r["evidence"], self.mailboxes())
                r["evidence"] = redact(r["value"], r["evidence"], self.mailboxes())
                r["second_check_required_to_reject"] = r["assessment"] == "likely_personal"
                pending = self.pending_reject(r["tag"])
                r["reject_requested_by"] = pending
        return rows

    def decide_match(self, tag_id: str, principal: str, decision: str, value: str | None) -> dict:
        """A steward's decision on a cross-reference match: a human tag that supersedes the match's, as on any tag, by a
        certification identity who reads every unit the match links. It is the graph's record of the decision and is
        never written back: the match counts in a figure only once the cross-reference certifies it upstream."""
        held = [m for m in self.matches() if m["tag"] == tag_id]
        if not held:
            raise LookupError(f"{tag_id} is not waiting for a decision (already decided, or itself a decision)")
        m = held[0]
        if not m["units"] or not set(m["units"]) <= self.readable_units(principal):
            raise PermissionError(f"{principal} may not read {', '.join(u for u in m['units'] if u not in self.readable_units(principal))}: "
                                  "a match is decided only by one who reads every unit it links")
        if value is not None:
            raise ValueError("a cross-reference match is accepted or rejected as it landed: no value")
        self.overlay()
        machine = self.tag_props(tag_id)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        new_id = f"{tag_id}/h{uuid.uuid4().hex[:12]}"
        self.q("CREATE (:Tag {id: $id, family: 'xref_match', value: $v, generator: 'human', confidence: 1.0, model: $who, prompt_version: '', "
               "created_at: $at, evidence: $ev})",
               {"id": new_id, "v": m["value"] if decision == "accept" else f"rejected:{m['value']}", "who": principal, "at": now,
                "ev": f"{decision} by {principal}; not written back: the match counts once the cross-reference certifies it upstream"})
        self.q("MATCH (n:Tag), (o:Tag) WHERE n.id = $n AND o.id = $o CREATE (n)-[:SUPERSEDES]->(o)", {"n": new_id, "o": tag_id})
        self.journal(principal, now, decision, new_id, machine)
        clash = (" The match is rejected upstream: it counts in no figure unless the cross-reference certifies it there."
                 if decision == "accept" and m["upstream"] == "rejected" else "")
        return {"superseded": tag_id, "by": new_id, "decision": decision, "generator": "human", "confidence": 1.0, "written_back": False,
                "note": "Recorded in the graph only: figures change when the cross-reference is certified again upstream." + clash}

    def pending_reject(self, tag_id: str) -> str | None:
        rows = self.q("MATCH (r:Tag {id: $id}) RETURN r.model AS by", {"id": f"{tag_id}/reject-request"})
        return rows[0]["by"] if rows else None

    def steward_decide(self, tag_id: str, principal: str, decision: str, value: str | None = None, definition: str | None = None) -> dict:
        """A certification decision. Raises PermissionError for the wrong identity,
        ValueError for a malformed decision, LookupError for a tag not in the queue,
        and OverlayError for a decision the graph holds but the overlay journal
        does not (journal). The tag is matched by WHERE, not inline by its key: on
        Ladybug 0.21 a node matched inline loses its properties when the OPTIONAL
        MATCH after it finds nothing. Rejecting a privacy tag that looks like real
        personal data would embed it, so one steward asks and a second, different
        certification identity confirms. Readiness is re-derived from every live
        tag on the chunk, so a Restricted classification that does not rest on the
        retired tag still holds the gate shut."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISIONS)}, not {decision!r}")
        if not self.may(principal, "certification"):
            raise PermissionError(f"{principal} is not a certification identity; only stewards decide queue items")
        match = next((m for m in self.q("MATCH (t:Tag) WHERE t.id = $id AND t.family = 'xref_match' RETURN t.id AS id", {"id": tag_id})), None)
        if match is not None:
            return self.decide_match(tag_id, principal, decision, value)
        rows = self.q("MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)-[:TAGGED]->(t:Tag) WHERE t.id = $id "
                      "OPTIONAL MATCH (s:Tag)-[:SUPERSEDES]->(t) RETURN t.family AS family, t.value AS value, t.generator AS generator, t.evidence AS evidence, "
                      "c.id AS chunk, d.id AS doc, d.shelf AS shelf, count(s) AS decided", {"id": tag_id})
        if not rows:
            raise LookupError(f"no tag {tag_id}")
        old = rows[0]
        if old["generator"] == "human" or old["decided"]:
            raise LookupError(f"{tag_id} is not waiting for a decision (already decided, or itself a decision)")
        if old["family"] not in ("candidate_term", "privacy"):
            raise ValueError(f"{old['family']} tags are not decided in the steward queue")
        if old["family"] == "candidate_term" and not self.can_write_terms(principal):
            raise PermissionError(f"{principal} has no CAN_WRITE on terms")
        if value is not None and decision != "accept":
            raise ValueError("a value may only be given with accept")
        self.overlay()
        machine = self.tag_props(tag_id)
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if decision == "reject" and old["family"] == "privacy" and assess_privacy(old["value"], old["evidence"], self.mailboxes()) == "likely_personal":
            requested_by = self.pending_reject(tag_id)
            if requested_by is None:
                self.q("CREATE (:Tag {id: $id, family: 'privacy_review', value: 'reject_requested', generator: 'human', confidence: 1.0, "
                       "model: $who, prompt_version: '', created_at: $at, evidence: $ev})",
                       {"id": f"{tag_id}/reject-request", "who": principal, "at": now,
                        "ev": f"reject of {old['value']} requested by {principal}; a second steward must confirm"})
                self.q("MATCH (c:Chunk {id: $c}), (r:Tag {id: $r}) CREATE (c)-[:TAGGED]->(r)", {"c": old["chunk"], "r": f"{tag_id}/reject-request"})
                self.journal(principal, now, REQUESTED, f"{tag_id}/reject-request", machine, old["chunk"], old["doc"])
                return {"status": "pending_second_check", "tag": tag_id, "requested_by": principal,
                        "reason": "This looks like real personal data; rejecting it would embed it. A second, different steward must confirm the reject."}
            if requested_by == principal:
                raise PermissionError(f"{principal} requested this reject; a second, different certification identity must confirm it")
        new_id = f"{tag_id}/h{uuid.uuid4().hex[:12]}"
        new_value = value or (old["value"] if decision == "accept" else f"rejected:{old['value']}")
        self.q("CREATE (:Tag {id: $id, family: $f, value: $v, generator: 'human', confidence: 1.0, model: $who, prompt_version: '', created_at: $at, evidence: $ev})",
               {"id": new_id, "f": old["family"], "v": new_value, "who": principal, "at": now, "ev": f"{decision} by {principal}"})
        self.q("MATCH (c:Chunk {id: $c}), (n:Tag {id: $n}), (o:Tag {id: $o}) CREATE (c)-[:TAGGED]->(n), (n)-[:SUPERSEDES]->(o)",
               {"c": old["chunk"], "n": new_id, "o": tag_id})
        result = {"superseded": tag_id, "by": new_id, "decision": decision, "generator": "human", "confidence": 1.0}
        if decision == "reject" and old["family"] == "privacy" and self.pending_reject(tag_id):
            result["confirmed_request_of"] = self.pending_reject(tag_id)
        term = None
        if decision == "accept" and old["family"] == "candidate_term":
            term = {"name": new_value, "domain": "Customer", "steward": principal, "definition": definition or "", "defined_by": old["doc"]}
            self.q("MERGE (t:Term {name: $n}) SET t.domain = $d, t.steward = $who, t.definition = $def",
                   {"n": new_value, "d": term["domain"], "who": principal, "def": term["definition"]})
            self.q("MATCH (d:Document {id: $d}), (t:Term {name: $n}) CREATE (d)-[:DEFINES]->(t)", {"d": old["doc"], "n": new_value})
            self.q("MATCH (p:Principal)-[:CAN_WRITE]->(:Term) WITH DISTINCT p MATCH (t:Term {name: $n}) "
                   "WHERE NOT EXISTS { MATCH (p)-[:CAN_WRITE]->(t) } CREATE (p)-[:CAN_WRITE]->(t)", {"n": new_value})
            result["term_created"] = new_value
        r = None
        if old["family"] in ("privacy", "classification"):
            try:
                with batch(self.conn) as w:
                    r = rederive(self.q, w, old["chunk"], self.embedder, reason=f"re-derived after {decision} of {tag_id} by {principal}")
            finally:
                self.writes += 1
            result["readiness"] = r["readiness"]
            if r["embedded_now"]:
                result["embedded_now"] = True
        self.journal(principal, now, decision, new_id, machine, old["chunk"], old["doc"], term)
        if r is not None:
            self.retrieval.chunk_decided(old["chunk"], r["readiness"], r.get("embedded_now"))
        return result
