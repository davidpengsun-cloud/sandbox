"""The ports: the protocols the engine and the batch side ask of what reaches outside the process or picks a backend,
each offered by the adapters of one kind (kge/adapters/<kind>/), whose package chooses the backend:

  WarehouseSet           kge/adapters/warehouse/: Warehouses (DuckDB files, duckdb.py), BigQueryWarehouses (bigquery.py)
  EstateWarehouses       kge/adapters/warehouse/: DuckDBEstate (DuckDB files, duckdb.py), BigQueryEstate (tables in the
                         client's warehouses dataset, bigquery_estate.py); WarehouseSession, a connection either opens
  Retrieval              kge/adapters/retrieval/: GraphRetrieval (the graph's own vectors, graph.py), BigQueryRetrieval
                         (VECTOR_SEARCH, bigquery.py)
  AppState               kge/adapters/appstate/: FilesState (files in the pack store, files.py), BigQueryState (tables,
                         bigquery.py)
  ObjectStore            kge/adapters/objstore/: LocalStore (a folder, folder.py), GcsStore (Cloud Storage, gcs.py)
  ModelProvider          kge/adapters/model/: LLM (providers.py), the offline mock (mock.py) among its providers
  Embeddings             kge/adapters/model/: HashEmbedder, LocalEmbedder, VertexEmbedder (embed.py)
  GraphStore             kge/adapters/graphstore/: LadybugStore (ladybug.py), BigQueryStore (bigquery.py)
  TrustedCaller          kge/adapters/caller/: GoogleIdTokens (google-auth, google_idtoken.py), TrustNoCaller (none.py)

Each port's own section below says what it holds.

The warehouse seam: what the engine, the agent and the combined path ask of a client's warehouses, whichever backend
holds them. DuckDB files on this machine (kge/adapters/warehouse/duckdb.py Warehouses) and BigQuery datasets
(kge/adapters/warehouse/bigquery.py BigQueryWarehouses) both offer it; a new backend offers the same and nothing above
this line changes.

Every method takes a unit, one of units, and answers with plain tuples, so the callers never see a backend's own
connection or result types. SQL passed in names its dialect (dialect, a sqlglot name): the code's own SQL (the planner's,
the contexts', the templates') is AUTHORED, DuckDB's, which a backend that speaks another writes as its own
(BigQueryWarehouses: rewrite_sql); SQL a model wrote is the warehouses' own, sql_dialect, which a backend runs as
written but for its tables' names. The catalogue methods return tables by their unadorned names, as a query names them,
whatever the backend stores them under."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import AbstractContextManager
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Callable, Protocol, runtime_checkable

AUTHORED = "duckdb"
"""The sqlglot dialect the code's own SQL is written in: DuckDB's, on every backend."""


@dataclass(frozen=True)
class QueryLimits:
    """Person-authored reads: wall time, local working memory, billed scan bytes and returned rows.
    deadline, when supplied, is the caller's monotonic deadline shared by planning and execution;
    seconds stays the original budget reported to the person."""
    seconds: float = 5
    memory_bytes: int = 268_435_456
    scan_bytes: int = 104_857_600
    rows: int = 1000
    deadline: float | None = None


class QueryRefused(ValueError):
    """A warehouse stopped a read at its execution or resource limit."""


@runtime_checkable
class WarehouseSet(Protocol):
    """One warehouse per unit of the client, read-only, safe to call from several request threads at once."""

    units: list[str]
    """The client's units, in the pack's order: one warehouse each."""

    curated: list[str]
    """The curated sources (a certified cross-reference's), read only through attached, never on their own."""

    query_dialect: str
    """The SQL dialect the warehouses run, as a prompt asks a model for it: "DuckDB" for the DuckDB files, "BigQuery
    GoogleSQL" for BigQuery. A model writes the SQL of the engine that runs it."""

    sql_dialect: str
    """The same dialect by its sqlglot name ("duckdb", "bigquery"): what SQL a model wrote is read as, by the gate and
    by the warehouses, the dialect argument of every method below for it."""

    def attached(self, units: list[str], dialect: str = AUTHORED) -> AbstractContextManager[Any]:
        """A session that reads these units' tables and the curated sources together, each table named
        <unit>.<table>; a cursor-like object with execute, fetchone and fetchall, reading SQL in dialect."""
        ...

    def cursor(self, unit: str, dialect: str = AUTHORED) -> Any:
        """A cursor of its own on the unit's warehouse (execute, fetchone, fetchall), usable as a context manager,
        reading SQL in dialect."""
        ...

    def row(self, unit: str, sql: str, dialect: str = AUTHORED) -> tuple | None:
        """The first row of a read, or None."""
        ...

    def rows(self, unit: str, sql: str, dialect: str = AUTHORED) -> list[tuple]:
        """Every row of a read."""
        ...

    def query(self, unit: str, sql: str, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> tuple[list[str], list[tuple]]:
        """The output column names and rows of a read."""
        ...

    def run(self, unit: str, sql: str, dialect: str = AUTHORED) -> Any:
        """The first value of the first row of a read."""
        ...

    def gate(self, unit: str, sql: str, allow, figure: bool = False, dialect: str = AUTHORED, limits: QueryLimits | None = None) -> dict:
        """The SQL gate (kge/pipeline/sqlguard.py), the query read as dialect, with the backend's own dry run of what it
        would run."""
        ...

    def columns(self, unit: str) -> list[tuple[str, str, str]]:
        """(table, column, type) for every column the unit's warehouse holds, in no set order: the gate's allowlist."""
        ...

    def calendar_tables(self, unit: str) -> set[str]:
        """The names of the unit's tables."""
        ...

    def catalogue_columns(self, unit: str) -> list[tuple[str, str, str]]:
        """(table, column, type) for every column, ordered by table, then by the column's position in it: the schema
        the agent's graph-off context shows."""
        ...

    def primary_keys(self, unit: str) -> list[tuple[str, list[str]]]:
        """(table, key columns) for each table the warehouse declares a primary key on."""
        ...

    def foreign_keys(self, unit: str) -> list[tuple[str, list[str], str, list[str]]]:
        """(table, columns, referenced table, referenced columns) for each declared foreign key, ordered by table, then
        columns."""
        ...

    def close(self) -> None:
        """Let go of every connection the set holds."""
        ...


"""The estate's warehouse seam: where the estate's builders (kge/estate/generate.py, from_files.py), certification
(kge/estate/certify.py) and onboarding (kge/estate/onboard/) create, load, validate and read a client's warehouses,
whichever backend holds them. DuckDB files on this machine (kge/adapters/warehouse/duckdb.py DuckDBEstate) and tables
in the client's BigQuery dataset, <prefix>_<client>_warehouses (kge/adapters/warehouse/bigquery_estate.py
BigQueryEstate) both offer it; kge/adapters/warehouse/ open_estate picks one by WAREHOUSE_BACKEND, so the BigQuery path
creates no DuckDB file and loads no DuckDB module. A caller never sees a backend's connection, file or module.

A warehouse is a unit's (or a curated source's): one per established unit, and one per version of a unit onboarding
made (kge/pipeline/warehouse_versions.py). It is made by DDL statements in DuckDB's dialect, as kge/estate/ddl.py
rebuilds them from a validated model or a builder writes them (CREATE TABLE with its keys, COMMENT ON TABLE and
COLUMN), staged, loaded through a session and published; a session's SQL is DuckDB's dialect too, rewritten by a
backend that speaks another. Every catalogue names types as DuckDB does (kge/adapters/warehouse/types.py), so what
certification lands is the same on both backends."""


class WarehouseRefused(ValueError):
    """The warehouse refused a statement or rows: a value of the wrong type, a key constraint, SQL it cannot run. Its
    message is the backend's first line, as DuckDB words it where the check is the adapter's own."""


class FigureTimeout(RuntimeError):
    """A figure's query ran past its time limit and was stopped."""


@runtime_checkable
class WarehouseSession(Protocol):
    """A connection to one warehouse (or several attached, read only), as DuckDB's offers one."""

    def execute(self, sql: str, parameters: list | None = None) -> Any:
        """Run one statement (? marks its parameters); a result with fetchone, fetchall and description. A read sees
        every row loaded before it."""
        ...

    def update_many(self, table: str, key: str, columns: list[str], rows: list[tuple]) -> int:
        """Set columns on the rows of table whose key each row names: rows are (key value, *column values), one row of
        the table each. One statement a row where that is cheap (DuckDB's), one for them all where each statement is a
        job of its own (BigQuery's MERGE-like UPDATE from UNNEST). Returns the number of rows given."""
        ...

    def load(self, table: str, columns: list[str], rows: list[list], batch: int | None = None) -> int:
        """Insert rows, each value for columns in order (a leading part of the table's columns, the rest NULL), cast
        and checked as DuckDB casts and checks them on insert (WarehouseRefused otherwise); how many. batch: rows
        per statement where the backend inserts by statement."""
        ...

    def describe(self, table: str) -> dict:
        """{columns: [{name, type, comment, ordinal}], primary_key, foreign_keys: [{columns, ref_table, ref_columns}],
        rows}: one table as the warehouse holds it, in its columns' order, its types named as DuckDB names them."""
        ...

    def storage(self, table: str) -> int | None:
        """The table's storage in bytes where the warehouse reports it, otherwise None."""
        ...

    def tables(self) -> list[str]:
        """The names of the warehouse's tables, in name order."""
        ...

    def sample(self, table: str, column: str, limit: int = 50) -> list[str]:
        """Up to limit distinct non-NULL values of a column, as text."""
        ...

    def figure(self, sql: str, seconds: float) -> tuple | None:
        """The first row of a read stopped after seconds (FigureTimeout); WarehouseRefused if the warehouse cannot run
        it, its text kept from the caller's reply."""
        ...

    def close(self) -> None:
        """Let the connection go; on a staged warehouse, every row loaded is in it before this returns."""
        ...


@runtime_checkable
class EstateWarehouses(Protocol):
    """A client's warehouses as the estate and onboarding write and read them: version None is an established unit's
    one warehouse, a whole number a version of a unit onboarding made."""

    backend: str
    """duckdb or bigquery: what a run says it wrote to, never a branch for a caller."""

    def holds_files(self) -> bool:
        """Whether the warehouses are files on this machine, so the job's work and a release carry them; where they
        are not (tables in a warehouse service), no warehouse file is packed or unpacked (kge/pipeline/objstore.py)."""
        ...

    def kept_in(self, dataset: str) -> bool:
        """Whether the warehouses are that dataset's tables already, so the sync into it (kge/estate/load_bigquery.py)
        has nothing to copy."""
        ...

    def stage(self, unit: str, statements: list[str], version: int | None = None) -> WarehouseSession:
        """A new warehouse made by the statements, beside any the unit has, open to load: nothing reads it until
        publish puts it in place (discard drops it)."""
        ...

    def publish(self, unit: str, version: int | None = None, inputs: str | None = None,
                keep: frozenset[int] = frozenset()) -> None:
        """The staged warehouse put in place of the unit's (or as its version, stamped with the digest of the inputs
        its rows were made from); a version keep names that holds rows from other inputs is never replaced
        (FileExistsError)."""
        ...

    def discard(self, unit: str, version: int | None = None) -> None:
        """A staged warehouse dropped, if any."""
        ...

    def open(self, unit: str, version: int | None = None, write: bool = False) -> WarehouseSession:
        """The unit's warehouse (or version), read only unless write."""
        ...

    def attached(self, units: list[str]) -> WarehouseSession:
        """One read-only session over several established warehouses, each table named <unit>.<table>."""
        ...

    def preview(self, statements: list[str]) -> AbstractContextManager[WarehouseSession]:
        """A throwaway warehouse made by the statements, kept nowhere: rows loaded into it are checked and counted
        (describe), and nothing outside the process is written."""
        ...

    def scratch(self, unit: str, statements: list[str]) -> AbstractContextManager[WarehouseSession]:
        """A throwaway warehouse that can be queried, dropped when the context ends."""
        ...

    def exists(self, unit: str) -> bool:
        """Whether the unit has an established warehouse."""
        ...

    def established(self) -> list[str]:
        """The units with an established warehouse, in name order."""
        ...

    def label(self, unit: str, version: int | None = None) -> str:
        """Where the warehouse is, as a run record names it."""
        ...

    def connection(self, unit: str) -> dict:
        """The unit's connection as a drafted pack's source names it: {dialect, ref}, the engine and where the unit's
        tables are (kge/compiler/drafts.py propose)."""
        ...

    def versions(self, unit: str) -> list[int]:
        """The versions of an onboarded unit the backend holds, oldest first."""
        ...

    def held(self) -> dict[str, list[int]]:
        """Every onboarded unit's versions, by unit."""
        ...

    def stamped(self, unit: str, version: int) -> str | None:
        """The digest of the inputs a version's rows were made from; None if it has none."""
        ...

    def drop(self, unit: str, version: int) -> None:
        """One version of an onboarded unit removed."""
        ...

    def remove(self, unit: str) -> None:
        """Every warehouse of the unit removed, established and versions."""
        ...

    def present(self) -> bool:
        """Whether the client has any warehouse."""
        ...

    def check(self, units: list[str], versions: dict[str, int], model_hash: str | None = None,
              inputs: dict[str, str] | None = None) -> None:
        """MissingWarehouse naming each version an artifact names that is not held, or whose rows were made from other
        inputs than it names."""
        ...

    def read_database(self, path: Path) -> tuple[list[dict], dict[str, dict[str, bytes]]]:
        """A client's database file read as models and CSVs (kge/estate/ddl.py from_database), where the backend reads
        one; WarehouseRefused where it does not."""
        ...


"""The app-state seam: where onboarding keeps each unit's manifest, its history and the client-wide job record, whichever
backend holds them. Files in the pack store (kge/adapters/appstate/files.py FilesState) and BigQuery tables in the
client's graph dataset (kge/adapters/appstate/bigquery.py BigQueryState) both offer it; kge/adapters/appstate/
for_profile picks one by APP_STATE_BACKEND. A unit's commit is made against the revision its manifest was read at, and
refused with StateConflict when another writer's commit came first."""


class StateConflict(RuntimeError):
    """The unit changed after it was read."""


class StateUnavailable(RuntimeError):
    """The BigQuery state backend cannot be signed in to (no credentials, or no project)."""


@runtime_checkable
class AppState(Protocol):
    """Onboarding's application state for one client."""

    tables: bool
    """True where the units' manifests and history are kept in tables, False where they are the pack's
    units/<unit>/unit.yaml and history.jsonl files, which a snapshot of the pack carries."""

    def lock(self) -> AbstractContextManager[Any]:
        """Held across a read, a change and its commit, so this process's own writers take turns."""
        ...

    def units(self) -> list[str]:
        """The units with state, sorted."""
        ...

    def manifest(self, unit: str) -> dict:
        """The unit's manifest, as its commit is made against; FileNotFoundError where it has none."""
        ...

    def history(self, unit: str) -> list[dict]:
        """The unit's history entries, oldest first."""
        ...

    def snapshot(self, unit: str) -> tuple[dict, list[dict]]:
        """The manifest and history read together."""
        ...

    def commit(self, unit: str, manifest: dict, additions: list[dict]) -> dict:
        """The manifest written and additions appended to the history; StateConflict where the unit changed since it
        was read."""
        ...

    def delete(self, unit: str) -> None:
        """The unit's state removed."""
        ...

    def job(self) -> dict | None:
        """The client-wide onboarding job record, or None."""
        ...

    def save_job(self, job: dict) -> None:
        """The client-wide job record replaced."""
        ...

    def clear_job(self) -> None:
        """The client-wide job record removed."""
        ...


"""The object-store seam: where an installed deployment keeps what is a client's, never in the image, whichever backend
holds it. A local folder (kge/adapters/objstore/folder.py LocalStore) and a Google Cloud Storage bucket
(kge/adapters/objstore/gcs.py GcsStore) both offer it; kge/adapters/objstore/ open_store picks one by the URL. A key is a
relative POSIX path (kge/adapters/objstore/keys.py check); a store fails with StoreError, naming the key and the URL,
never what it held. A client's scratch area, for files a step stages and then drops, is the narrower ScratchArea:
open_scratch gives tmp/<client>/ in the tmp bucket TMP_URL names, or a local temporary folder where it is unset."""


class StoreError(RuntimeError):
    """A store that failed: the key and the URL, never what it held."""


class StoreConflict(StoreError):
    """A write the store refused because the object changed since its generation was read: someone else's write won."""


@runtime_checkable
class ObjectStore(Protocol):
    """One object store: keys to bytes."""

    url: str
    """The URL the store was opened from."""

    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None where there is none."""
        ...

    def probe(self) -> str:
        """Read one object and list the store, as a preflight does, and say what was exercised and any caveat; StoreError
        where the bucket or folder does not exist, never an empty list or a missing object."""
        ...

    def bucket_metadata(self) -> str:
        """Read the bucket's own metadata (a folder's, on a folder) and say what was exercised; StoreError where it does
        not exist or cannot be read."""
        ...

    def put(self, key: str, data: bytes) -> None:
        """The object written whole."""
        ...

    def get_version(self, key: str) -> tuple[bytes | None, str]:
        """An object's bytes and an opaque version ("0" means absent)."""
        ...

    def put_version(self, key: str, data: bytes, version: str) -> str:
        """Put only if version still names the object returned by get_version, and return the new one."""
        ...

    def list(self, prefix: str = "") -> list[str]:
        """Every key under prefix, sorted."""
        ...

    def delete(self, key: str) -> None:
        """The object removed; nothing where there is none."""
        ...

    def source_uri(self, key: str) -> str | None:
        """Where another Google Cloud service, a BigQuery load job, reads the object from (gs://<bucket>/<name>), or None
        where none can, as on a local folder. Says only where it would be: nothing is read or written."""
        ...


@runtime_checkable
class ScratchArea(Protocol):
    """A client's scratch area: files a step puts, has read by their exact key or URI, and deletes. It offers no list:
    on Cloud Storage a list is checked against the bucket, which the client's grant on its own prefix cannot allow."""

    url: str
    """The URL the area was opened from."""

    def get(self, key: str) -> bytes | None:
        """The object's bytes, or None where there is none."""
        ...

    def put(self, key: str, data: bytes) -> None:
        """The object written whole."""
        ...

    def delete(self, key: str) -> None:
        """The object removed; nothing where there is none."""
        ...

    def source_uri(self, key: str) -> str | None:
        """Where another Google Cloud service, a BigQuery load job, reads the object from (gs://<bucket>/<name>), or None
        where none can, as on a local folder. Says only where it would be: nothing is read or written."""
        ...


"""The model seam (kge/adapters/model/): the language model a step calls, whichever provider serves it, and the embedder
that turns a chunk's text into the graph's vector. The factories make_llm() and model_for(agent) give a ModelProvider
(providers.py's LLM, the offline mock among them), make_embedder() an Embeddings (embed.py's hash, local and Vertex AI
embedders)."""


@runtime_checkable
class ModelProvider(Protocol):
    """One configured model: one prompt in, one reply out, each call recorded in the run's trace."""

    provider: str
    """The provider's name, one of PROVIDERS (kge/adapters/model/providers.py)."""

    model: str
    """The model it asks for."""

    @property
    def name(self) -> str:
        """provider:model, as a run names the model."""
        ...

    def identity(self) -> dict:
        """The model as a run records it, with a hash of every setting that shapes a reply, never the key."""
        ...

    def complete(self, prompt: str, max_tokens: int = 800) -> str:
        """The reply's text. Refused if the model declines; a failed call raises what it raised."""
        ...

    def json(self, prompt: str, max_tokens: int = 800) -> dict:
        """The first JSON object in the reply. NotJSON if there is none."""
        ...


@runtime_checkable
class Embeddings(Protocol):
    """An embedder: text in, a unit vector of dim floats out, named so retrieval compares only vectors of one space."""

    name: str
    """The embedding model, as a chunk records it (embedding_model)."""

    dim: int
    """The vector's length: the graph's DIM unless asked otherwise; the Vertex AI embedder's is EMBEDDING_DIM (768
    unset) where the vectors live in BigQuery."""

    def embed(self, text: str) -> list[float]:
        """The text's vector, normalised to unit length."""
        ...


@runtime_checkable
class Retrieval(Protocol):
    """The retrieval seam: vector search over a client's chunks, whichever backend holds their vectors. The graph store
    (kge/adapters/retrieval/graph.py GraphRetrieval) and BigQuery (kge/adapters/retrieval/bigquery.py BigQueryRetrieval)
    both offer it. The caller (Graph.retrieve) has checked the tiers, embedded the question and found the units the
    principal may read; a backend searches, records what it read in the run's trace, and answers as the graph's own
    search does: {asked_for, skipped_other_model, exact, post_filtered, index_returned, post_filter_lost,
    embedding_model}. A backend whose vectors can differ in width from the question's (BigQuery's, after an
    EMBEDDING_DIM change without a new sync) adds skipped_other_width, and logs it; the graph store's are one width."""

    def search(self, qv: list[float], units: list[str], purpose: str, k: int, shelf: str | None,
               tiers: list[str] | None) -> dict:
        """The k chunks nearest qv that these units, this purpose, the shelf (None for every one) and the tiers (None
        for no tier filter) allow."""
        ...

    def chunk_decided(self, chunk: str, readiness: str, embedded_now: bool | None) -> None:
        """A steward's decision re-derived the chunk's readiness in the graph: carry it to the backend's own copy, if it
        keeps one."""
        ...


"""The graph store seam (kge/adapters/graphstore/): the graph database a client's graph is kept in, opened, connected to,
made and loaded through it alone. Ladybug offers it (kge/adapters/graphstore/ladybug.py LadybugStore), embedded in the
process, and so does BigQuery (kge/adapters/graphstore/bigquery.py BigQueryStore), tables and a property graph in the
client's graph dataset; the package's store() gives the one GRAPH_STORE names. Callers speak Cypher, the subset the
demo's queries use, to either: Ladybug runs it, and BigQuery runs its BigQuery form (GQL in GRAPH_TABLE for a read, DML
on the tables for a write, kge/adapters/graphstore/gql.py). A connection's rows come back under the same names and
types from both."""


@runtime_checkable
class GraphStore(Protocol):
    """A graph database: Cypher in, rows out, and a graph made and filled from a compiled artifact."""

    name: str
    """The store's name, as a graph's file (graph.<name>), its release marker and the run trace name it."""

    dialect: str
    """The DDL dialect a graph is made from in this store: kuzu (the artifact's ddl/kuzu.cypher) or bigquery (the
    compiled model's BigQuery DDL); ddl gives the statements."""

    def module(self) -> Any:
        """The store's own module, imported when first asked for: a store with a library missing fails here, plainly."""
        ...

    def path(self, root: Path) -> Path:
        """The graph kept under root: its file, or, for a store whose graph is not a file, the pointer to it."""
        ...

    def sidecars(self, path: Path) -> list[Path]:
        """The files the store keeps beside a graph, its write-ahead log among them."""
        ...

    def open(self, path: Path, pidfile: Path | None = None, entry: dict | None = None) -> Any:
        """The graph at path, opened; GraphLocked, naming the process in pidfile, while another process holds it, where
        the store lets one process hold a graph at a time. entry, a release record's (release_entry), names the graph
        to open where the store keeps one: a server opens what the release record it read names, never rereading it."""
        ...

    def connect(self, db: Any) -> Any:
        """A connection to an open graph: execute(cypher, params), whose result offers get_all() and get_as_df(), and
        close()."""
        ...

    def info(self, conn: Any = None) -> dict:
        """{engine, version, storage_version}: the store, as a run records it."""
        ...

    def q(self, conn: Any) -> Callable[..., list[dict]]:
        """A q(cypher, params) over a connection, returning dict rows with None for nulls."""
        ...

    def ddl(self, folder: Path, model: dict) -> list[str]:
        """The statements a load of the artifact in folder makes a fresh graph's tables with, in the store's dialect:
        model is the artifact's graph-model.yaml (Ladybug: the artifact's own ddl/kuzu.cypher, a statement a line;
        BigQuery: the compiled model's tables and property graph, kge/adapters/graphstore/gql.py ddl)."""
        ...

    def create(self, conn: Any, model: dict, ddl: list[str]) -> None:
        """A fresh graph's tables, from ddl in the store's dialect; model is the artifact's graph-model.yaml, the
        compiled model the DDL was built from."""
        ...

    def batch(self, conn: Any) -> Any:
        """A batch of writes on conn (kge/adapters/graphstore/base.py Writer), a context manager: upsert, update,
        create_node, delete_node, create_edge, delete_edges and load_file, given the meaning they have run one after
        another; the store sends them as it can (Ladybug one statement each, at once; BigQuery one set-based statement
        per kind and table, in one transaction, when the batch ends)."""
        ...

    def release_entry(self, path: Path) -> dict | None:
        """What a release of the graph at path records of it, as its pointer names it now, in the release record
        (kge/adapters/released.py), beside retrieval's, so a server opens it (open's entry) until the next release,
        whatever a load of the same model hash repoints meanwhile: BigQuery's graph pointer, whole; None where the graph
        is its own file (Ladybug)."""
        ...

    def generation_of(self, entry: dict | None) -> str | None:
        """The generation a release record's entry names (release_entry): the table prefix of a BigQuery pointer; None
        for none."""
        ...

    def served(self, path: Path) -> Path:
        """What a server opens for the graph at path: the release record, where a release recorded the generation it
        released, else path itself (a candidate served by its hash, or a store whose graph is its own file)."""
        ...

    def generation(self, path: Path) -> str | None:
        """The generation of the graph the file at path names, which with the model hash and the chunks table makes a
        release's identity (Profile.identity): the table prefix a BigQuery pointer names; None where the graph is its
        own file (Ladybug) or the pointer names none."""
        ...

    def loaded(self, path: Path) -> bool:
        """Whether the graph at path is loaded on this store: the graph (its file, or the generation its pointer names)
        is there and the LOADED marker beside it holds this store's entry. Read from the files alone, never opening the
        graph, and never raising: False where either cannot be read."""
        ...

    def discard(self, path: Path) -> list[str]:
        """Drop the generation the graph at path names, and its pointer: a candidate's, once its check has ended
        (kge/improve/abtest.py discard), never what the hash's release record names. Nothing where the graph is its own
        file (Ladybug), which stays. What was dropped."""
        ...

    def prune(self, root: Path, held: Iterable[str] = ()) -> list[str]:
        """Drop what the store holds of a client's graphs that nothing may read any more: no graph under root (each model
        hash's folder of the client's graph store) names it, nor its release record (kge/adapters/released.py), nor held, the generations of the releases a service
        may still serve (the release ledger's: the one just released and the one before it, kge/graph/release.py
        served); what was dropped."""
        ...


@runtime_checkable
class TrustedCaller(Protocol):
    """Who may name the address a sign-in came from: a caller in front of KGE (a trusted frontend on Cloud Run) that
    counts its own callers' sign-ins by their addresses, and so forwards each one's. Pilot sign-in's per-address limit
    (kge/services/identity/routes.py) counts that address in place of the caller's own only for a request whose caller
    token this trusts; any other request counts the caller's own address, as without one."""

    def trusts(self, token: str) -> bool:
        """Whether token proves the caller is the one trusted: verified here, never taken on its word. False for any
        token it cannot verify, never raising."""
        ...
