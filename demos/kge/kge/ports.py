"""The ports: the protocols the engine and the batch side ask of what reaches outside the process or picks a backend,
each offered by the adapters of one kind (kge/adapters/<kind>/), whose package chooses the backend:

  WarehouseSet           kge/adapters/warehouse/: Warehouses (DuckDB files, duckdb.py), BigQueryWarehouses (bigquery.py)
  EstateWarehouses       kge/adapters/warehouse/: DuckDBEstate (DuckDB files, duckdb.py), BigQueryEstate (tables in the
                         client's warehouses dataset, bigquery_estate.py); WarehouseSession, a connection either opens
  Retrieval              kge/adapters/retrieval/: GraphRetrieval (the graph's own vectors, graph.py), BigQueryRetrieval
                         (VECTOR_SEARCH, bigquery.py)
  AppState               kge/adapters/appstate/: FilesState (files in the pack store, files.py), BigQueryState (tables,
                         bigquery.py)
  SessionState           kge/adapters/sessionstate/: StoreSessionState (GCS or folder, store.py),
                         BigQuerySessionState (existing app-state tables, bigquery.py)
  ObjectStore            kge/adapters/objstore/: LocalStore (a folder, folder.py), GcsStore (Cloud Storage, gcs.py)
  ModelProvider          kge/adapters/model/: LLM (providers.py), the offline mock (mock.py) among its providers
  Embeddings             kge/adapters/model/: HashEmbedder, LocalEmbedder, VertexEmbedder (embed.py)
  GraphStore             kge/adapters/graphstore/: LadybugStore (ladybug.py), BigQueryStore (bigquery.py)
  TrustedCaller          kge/adapters/caller/: GoogleIdTokens (google-auth, google_idtoken.py), TrustNoCaller (none.py)
  FeedbackLog            kge/adapters/feedbacklog/: FolderFeedbackLog (JSON lines in the run state, folder.py),
                         StoreFeedbackLog (one object a record in the object store, objstore.py)
  ShadowLog              kge/adapters/shadowlog/: FolderShadowLog (JSON lines, one file a release, in the run state,
                         folder.py), StoreShadowLog (one object an observation under shadow/<client>/<release>/ in the
                         object store, objstore.py)
  KgeTools               kge/adapters/kgetools/: McpKgeTools (KGE's own MCP tools as the signed-in person, mcp.py); the
                         in-process tools live in the engine

Traces have no port here: the OpenTelemetry API is theirs, and kge/adapters/tracing/ sets up its exporters.

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
from collections.abc import Iterable, Mapping
from typing import Any, Callable, NamedTuple, Protocol, runtime_checkable

AUTHORED = "duckdb"
"""The sqlglot dialect the code's own SQL is written in: DuckDB's, on every backend."""

REFERENCE = "reference"
"""The internal reference runtime's id: the warehouses the pack was compiled against, which need no runtime entry."""

WarehouseProgress = Callable[[str, int, int | None], None]


@dataclass(frozen=True)
class DocumentPage:
    """One page's Markdown and how it was read: text-layer or model."""

    markdown: str
    method: str


@dataclass(frozen=True)
class DocumentReading:
    """A reading in page order; page starts and provenance survive in the kept Markdown."""

    pages: tuple[DocumentPage, ...]

    @property
    def markdown(self) -> str:
        return "\n\n".join(f"<!-- page: {i}; read: {page.method} -->\n\n{page.markdown}"
                            for i, page in enumerate(self.pages, 1))


@runtime_checkable
class DocumentReader(Protocol):
    """A provider's declared file extensions, identity and conversion to paged Markdown."""

    name: str
    formats: tuple[str, ...]

    def identity(self) -> str:
        """The conversion revision and configured provider; changing it deliberately rereads content."""
        ...

    def read(self, content: bytes, format: str, name: str = "") -> DocumentReading:
        """Read supported content, preserving words, tables and descriptions of pictures and charts. name: the document as
        an error names it."""
        ...


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
class QuestionRuns(Protocol):
    """Shared private question records with optimistic revisions."""

    def get(self, run_id: str) -> tuple[dict | None, Any]:
        """Read a record; a done answer requires completed_at at or before expires_at, regardless of read time."""
        ...

    def put(self, run_id: str, record: dict, revision: Any) -> Any:
        ...


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

    def attached(self, units: list[str], dialect: str = AUTHORED, on_progress: WarehouseProgress | None = None) -> AbstractContextManager[Any]:
        """A session that reads these units' tables and the curated sources together, each table named
        <unit>.<table>; a cursor-like object with execute, fetchone and fetchall, reading SQL in dialect."""
        ...

    def cursor(self, unit: str, dialect: str = AUTHORED, on_progress: WarehouseProgress | None = None) -> Any:
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

    def table_facts(self, unit: str, table: str) -> dict:
        """The table's records (int or None) and storage (bytes or None), at the version this set opened.
        Also accepts curated sources. Read once into the release's catalogue snapshot, never per request."""
        ...

    def primary_keys(self, unit: str) -> list[tuple[str, list[str]]]:
        """(table, key columns) for each table the warehouse declares a primary key on."""
        ...

    def foreign_keys(self, unit: str) -> list[tuple[str, list[str], str, list[str]]]:
        """(table, columns, referenced table, referenced columns) for each declared foreign key, ordered by table, then
        columns."""
        ...

    def resolve_reference_sql(
        self,
        sql: str,
        *,
        unit: str | None = None,
        runtimes: list[str] | None = None,
        runtime_id: str | None = None,
        readable_units: set[str] | None = None,
    ) -> dict:
        """Resolve a UAT reference_sql ground-truth query against the served or selected runtime warehouse."""
        ...

    def configure_runtimes(self, subgraph: dict) -> None:
        """Load the runtime subgraph from the graph and check every runtime in the default list live."""
        ...

    def runtimes_health(self) -> dict[str, dict]:
        """Per-runtime availability and reason for /healthz."""
        ...

    def list_runtimes(self, readable_units: set[str] | None = None) -> list[dict]:
        """Runtimes the caller may use and which reference tables each holds."""
        ...

    def default_runtime_list(self) -> list[str]:
        """The deployment's active default runtime list."""
        ...

    def runtimes_active(self, requested: list[str] | None = None) -> bool:
        """Whether runtimes are declared in the pack, configured in the deployment, or requested on the call."""
        ...

    def select_runtime(self, statements: list[dict], *, runtimes: list[str] | None = None,
                       readable_units: set[str] | None = None,
                       measures: list[str] | tuple[str, ...] = ()) -> dict:
        """Resolve which runtime answers for the reference statements."""
        ...

    def reference_runtime_meta(self, statement: dict, *,
                               measures: list[str] | tuple[str, ...] = ()) -> tuple[dict | None, dict]:
        """Check partition policy on reference SQL and build runtime metadata without rewriting authored SQL."""
        ...

    def execute_statement(self, statement: dict, *, runtime_id: str = REFERENCE,
                          measures: list[str] | tuple[str, ...] = (),
                          limits: QueryLimits | None = None,
                          on_stage: object = None, on_progress: WarehouseProgress | None = None,
                          on_ready: Callable[[dict, dict], None] | None = None) -> tuple[dict, list[tuple] | None, dict]:
        """Rename along IMPLEMENTS, render in the runtime's dialect, run the SQL and cost gates, and execute."""
        ...

    def statement_sql(self, statement: dict, runtime_id: str) -> str:
        """Render an already checked statement using the selected runtime's held mappings, without a query."""
        ...

    def submit(self, statement: dict, *, runtime_id: str = REFERENCE, measures=(), job_id: str, on_ready=None,
               on_stage=None) -> dict:
        """Gate and submit once, returning a serialisable warehouse handle without waiting."""
        ...

    def poll(self, handle: dict) -> dict:
        """Read state, progress and completed results without submitting another query."""
        ...

    def release(self, handle: dict) -> None:
        """Release a completed result after its checkpoint has been saved."""
        ...

    def cancel(self, handle: dict) -> None:
        """Stop a submitted statement after its question's lifetime."""
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


class EstateLimits(NamedTuple):
    """The backend's limits on authored descriptions and labels; None means no limit."""

    description_chars: int | None
    label_chars: int | None
    table_bytes: int | None = None
    column_chars: int | None = None
    columns: int | None = None
    primary_key_columns: int | None = None
    foreign_keys: int | None = None
    self_references: bool = True


class HeldObject(NamedTuple):
    """One backend object, named without its contents; kind is table, view or property graph."""

    dataset: str
    name: str
    kind: str


@runtime_checkable
class EstateWarehouses(Protocol):
    """A client's warehouses as the estate and onboarding write and read them: version None is an established unit's
    one warehouse, a whole number a version of a unit onboarding made."""

    backend: str
    """duckdb or bigquery: what a run says it wrote to, never a branch for a caller."""

    builds_apart: bool
    """Whether fixture rebuilds keep each warehouse version apart until release."""

    def limits(self) -> EstateLimits:
        """The backend's description and label length limits."""
        ...

    def refuses_type(self, declared: str) -> str | None:
        """Why the backend refuses a declared column type, or None when it accepts it."""
        ...

    def refuses_schema(self, unit: str, statements: list[str]) -> str | None:
        """Why the backend refuses the complete schema before writes, or None."""
        ...

    def refuses_key_type(self, declared: str) -> str | None:
        """Why the backend refuses this type in a primary or foreign key, or None."""
        ...

    def inventory(self) -> list[HeldObject]:
        """Every table and view in the client's warehouses dataset except application-owned objects; no objects for files."""
        ...

    def clear(self, objects: list[HeldObject]) -> list[HeldObject]:
        """The listed objects dropped, skipping ones already missing and keeping application-owned objects and the dataset."""
        ...

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
    """The state changed after it was read."""


class StateUnavailable(RuntimeError):
    """The BigQuery state backend cannot be signed in to (no credentials, or no project)."""


@runtime_checkable
class AppState(Protocol):
    """Onboarding's application state for one client.

    Access optimistic concurrency is per instance: use one instance per operation, reading and then writing
    on that same instance. Do not share an instance between concurrent operations.
    """

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

    def append_history(self, unit: str, entries: list[dict]) -> None:
        """Append entries in order without creating an onboarding manifest; the caller holds lock() as for commit.
        Remote conflicts are refused or retried without losing committed entries."""
        ...

    def snapshot(self, unit: str) -> tuple[dict, list[dict]]:
        """The manifest and history read together."""
        ...

    def capture_units(self, units: Iterable[str]) -> object:
        """An opaque checkpoint of these units' records and absences, including native revisions and history order."""
        ...

    def restore_units(self, captured: object) -> None:
        """Replace exactly the checkpoint's units, undoing a failed build without adding history or revisions."""
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

    def access_grants(self) -> list[dict]:
        """Live rows: username, name, roles, updated_at and updated_by, sorted by username."""
        ...

    def access_put(self, row: dict, *, by: str, via: str) -> dict:
        """Upsert username and roles, with optional name (default empty string), and audit atomically.
        Return the audit entry, or raise StateConflict on a stale read.
        """
        ...

    def access_migrate(self, rows: list[dict], *, by: str) -> list[dict]:
        """Seed a never-initialized table and its audit in one atomic commit.
        Refuse a stale read or any existing grant or audit; return the audit entries.
        """
        ...

    def access_delete(self, username: str, *, by: str, via: str) -> dict | None:
        """Remove and audit atomically; None when no such grant exists."""
        ...

    def access_audit(self, limit: int = 200) -> list[dict]:
        """Newest first: sequence, at, by, via, username, before, after, digest and outside.
        Outside records grants differing from the previous audit digest, including missing audit.
        """
        ...

    def access_digest(self) -> str:
        """SHA256 of canonical usernames, names and sorted roles, excluding update metadata."""
        ...


@runtime_checkable
class SessionState(Protocol):
    """Personal tokens and signed-out sessions for one client, independent of access grants."""

    def identity_snapshot(self) -> dict:
        """Personal tokens and unexpired signed-out ids; refresh every three seconds, refuse at thirty seconds."""
        ...

    def identity_token(self, principal: str, candidate: str) -> str:
        """Return the existing personal token or atomically keep candidate, preserving concurrent writers."""
        ...

    def identity_sign_out(self, sid: str, expires: int) -> None:
        """Durably revoke this session id until expiry without replacing other revocations."""
        ...

    def refresh_identity(self) -> None:
        """Read shared storage and replace the in-memory snapshot, surfacing read failures."""
        ...

    def start_identity_refresh(self) -> None:
        """Start the three-second background refresh."""
        ...

    def stop_identity_refresh(self) -> None:
        """Stop this instance's background refresh."""
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


"""The feedback seam (kge/adapters/feedbacklog/): the append-only log of what people said of an answer, confirmed or not
what they meant. A record holds the release id, the time, the verdict, answer_mode, answered_by and the normalised plan,
and nothing else: never the question text, the principal, a word, a figure or a row. The port only appends; nothing
reads, rewrites or deletes a record through it. feedback_log(state_dir) gives JSON lines under <state>/feedback/, or,
with STORE_URL set, one object a record under feedback/<release>/ in the object store."""

FEEDBACK_VERDICTS = ("confirmed", "not_what_i_meant")


@dataclass(frozen=True)
class FeedbackRecord:
    """One verdict on one answer, exactly the fields the log keeps. receipt_id names the receipt the verdict was given
    on, a digest of the sealed receipt that tells nothing of what it holds: a receipt may be posted again, and a reader
    counts the last verdict per receipt_id."""

    release: str
    time: str
    verdict: str
    answer_mode: str
    answered_by: str
    plan: Any
    receipt_id: str


@dataclass(frozen=True)
class RoleAuditEntry:
    """One call the Cerebro role matrix checked (cerebro.md §8.3): when, who, their Cerebro roles, the matrix row, the
    route or tool, and whether it was allowed. Never a question, an answer or a figure."""

    time: str
    who: str
    cerebro_roles: tuple[str, ...]
    row: str
    endpoint: str
    outcome: str


@runtime_checkable
class RoleAuditLog(Protocol):
    """An append-only log of the role matrix's checks, read newest first by an administrator."""

    def append(self, entry: RoleAuditEntry) -> None:
        """The entry kept after every entry before it, none overwritten."""
        ...

    def newest(self, limit: int) -> list[dict]:
        """At most limit entries, newest first."""
        ...


@runtime_checkable
class FeedbackLog(Protocol):
    """An append-only feedback log."""

    def append(self, record: FeedbackRecord) -> None:
        """The record kept after every record before it, none overwritten."""
        ...


"""The shadow seam (kge/adapters/shadowlog/): the append-only record of each comparison between the graph rules planner
and the coded one (question-shapes.md §4.4), one observation a compared question. An observation never holds the
question text, the principal, a figure, a row or SQL; review evidence (normalised plans, ledger kinds and operations,
applied span positions) is kept only for a disagreement or a takeover. Keys are laid out per release, so retention, the
two-release window plus review (design Q4), is the deletion of whole releases, done by the job's fold step in B3 (#451),
which holds the store's admin role. The service can only add observations: the port has no delete. shadow_log(state_dir,
client) gives JSON lines under <state>/shadow/, or, with STORE_URL set, one object an observation under
shadow/<client>/<release>/ in the object store."""

SHADOW_OUTCOMES = ("propose", "clarify", "abstain")
SHADOW_COMPARISONS = ("agree", "plans_differ", "propose_decline", "declines_differ")
SHADOW_SERVED = ("coded", "graph", "model")


@dataclass(frozen=True)
class ShadowObservation:
    """One comparison of the graph and coded planners on one question, exactly the fields the shadow log keeps.

    id            32 lowercase hex characters, fresh for each observation
    time          ISO-8601 in UTC, to the millisecond
    release       the served release id
    graph_shapes  the shapes the graph adapter matched; () where it matched none
    coded_shapes  the coded builders (or the shape ids that mirror them), attributed by node-kind signature
    shapes        the sorted union of the two: the one denominator M3, M5 and M6 share
    graph_outcome, coded_outcome
                  each side's reply kind, one of SHADOW_OUTCOMES
    comparison    one of SHADOW_COMPARISONS
    served        which side's plan answered, one of SHADOW_SERVED
    fallback      the coded side served (the graph's plan was not taken)
    takeover      the graph side served a plan where the coded side declined
    new_phrase    the question used a phrase outside the release's baseline
    evidence      None, or the redacted review evidence of a disagreement or a takeover: {"graph", "coded", "ledger"}"""

    id: str
    time: str
    release: str
    graph_shapes: tuple[str, ...]
    coded_shapes: tuple[str, ...]
    shapes: tuple[str, ...]
    graph_outcome: str
    coded_outcome: str
    comparison: str
    served: str
    fallback: bool
    takeover: bool
    new_phrase: bool
    evidence: Any


@runtime_checkable
class ShadowLog(Protocol):
    """An append-only shadow log, read a release at a time by the job that folds and prunes it."""

    def append(self, observation: ShadowObservation) -> None:
        """The observation kept after every one before it, none overwritten."""
        ...

    def releases(self) -> list[str]:
        """The release ids the log holds observations for, sorted."""
        ...

    def read(self, release: str) -> list[dict]:
        """One release's observations, oldest first; [] where the log holds none."""
        ...


"""The model seam (kge/adapters/model/): the language model a step calls, whichever provider serves it, and the embedder
that turns a chunk's text into the graph's vector. The factories make_llm() and model_for(agent) give a ModelProvider
(providers.py's LLM, the offline mock among them), make_embedder() an Embeddings (embed.py's hash, local and Vertex AI
embedders)."""


@dataclass(frozen=True)
class ModelTurn:
    """One turn of a structured call after its first user turn: the model's previous contract, or the user's correction."""

    role: str
    """model or user."""

    text: str
    """The turn's text."""

    replay: Mapping[str, Any] | None = None
    """The provider's own record of a model turn, sent back exactly as it came (Gemini's candidate content parts, thought
    signatures included); None sends the text."""


@dataclass(frozen=True)
class ModelRequest:
    """One structured call: a system instruction, the data and the question as the first user turn, the contract's JSON
    schema, and any correction turns after it."""

    call: str
    """plan, classify or wording: which of Ask's calls it is, which picks its reply budget and thinking cap."""

    version: str
    """The prompt's version, which is traced."""

    instructions: str
    """The system instruction."""

    content: str
    """The first user turn: the data sections, then the question, last."""

    schema: Mapping[str, Any]
    """The contract's provider-neutral JSON schema."""

    contract: str
    """The contract's name (OpenAI's json_schema name)."""

    turns: tuple[ModelTurn, ...] = ()
    """Correction turns after the first user turn, model then user, alternating."""

    less_thinking: int = 0
    """Steps of thinking given back to the contract after a reply that stopped at its budget."""

    attempt: int = 0
    """Which attempt this is, 0 for the first."""

    legacy_prompt: str = ""
    """The request as one text, for a model that takes one prompt only (the mock, a test double)."""

    json_mode: bool = False
    """Send no schema: the provider's JSON mode, with the schema shown in the system instruction instead. The reply is
    validated in code against the same contract all the same. The full plan (query-plan-v1) is always sent so, on every
    provider: its recursive PlanReply schema inlines to about a megabyte, which Vertex AI could not flatten, and one form
    for every provider keeps model_first's replies comparable; any other call is retried so once when a provider refuses
    its schema."""


@dataclass(frozen=True)
class ModelReply:
    """A structured call's reply: the contract as data and as the text the model returned, and its tokens by part."""

    data: dict
    """The contract, parsed."""

    text: str
    """The contract exactly as returned."""

    replay: Mapping[str, Any] | None = None
    """The provider's own record of the reply, for a correction turn (ModelTurn.replay)."""

    tokens_in: int | None = None
    tokens_thinking: int | None = None
    tokens_contract: int | None = None
    finish: str | None = None
    """Why the reply stopped, as the provider said it; None when it did not say."""

    json_mode: bool = False
    """Whether the reply was asked for in JSON mode, with no schema sent: the request was (ModelRequest.json_mode), or
    the provider refused its schema and the call was made again so. A correction after it is sent in JSON mode too, so
    a schema already refused is never sent again in the same conversation."""


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

    def structured(self, request: ModelRequest) -> ModelReply:
        """One structured call: the request's contract schema sent as the provider's structured output, the reply parsed
        as one JSON object. Raises Truncated when the reply stopped at its budget (its more_room says whether less
        thinking would leave the contract more room), NotJSON when the reply is not one JSON object, Refused or
        UnavailablePlan, and whatever the call itself raised."""
        ...


@runtime_checkable
class Embeddings(Protocol):
    """An embedder: text in, a unit vector of dim floats out, named so retrieval compares only vectors of one space."""

    name: str
    """The embedding model, as a chunk records it (embedding_model)."""

    mention_similarity: float
    """The calibrated default floor for steward suggestions in this embedding space."""

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

    def mentions_changed(self, chunk: str) -> None:
        """Refresh the retrieval copy of a chunk's links after steward acceptance."""
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

    def remember_read(self, conn: Any, cypher: str, rows: list[dict]) -> None:
        """Retain an immutable read already returned by a wider snapshot, without a store job."""
        ...

    def missing_type(self, error: Exception) -> bool:
        """Whether a read failed solely because this graph omits a node or edge type."""
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

    def generations(self) -> list[str]:
        """Every generation prefix in the client's graph and candidates datasets; none for files or absent datasets."""
        ...

    def drop_generations(self, prefixes: Iterable[str]) -> list[str]:
        """The named generations dropped, property graphs before tables; the prefixes actually found and dropped."""
        ...

    def inventory(self) -> list[HeldObject]:
        """Every table, view and property graph in the client's graph and candidates datasets except application-owned objects."""
        ...

    def clear(self, objects: list[HeldObject]) -> list[HeldObject]:
        """The listed objects dropped, property graphs first, skipping missing objects and keeping application-owned objects."""
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


class GraphReadRecorder(Protocol):
    """The recording side of snapshot reads, independent of the graph's store."""

    def record(self, kind: str, value: Any, used: bool = True) -> dict:
        """Record returned elements and return their safe node and edge ids."""
        ...


class KgeTools(Protocol):
    """KGE's question tools as an agent reaches them: the context package for a question and the governed answer, each
    as the person whose credential the tools were opened with, the same JSON the matching endpoint gives. A refusal is
    the endpoint's (its status and detail), never a quiet empty package or answer."""

    def get_context(self, question: str, *, purpose: str, arm: str, products: list[str] | None,
                    plan_vocabulary: bool, history: Iterable[Any], runtimes: list[str] | None) -> dict:
        """POST /context's package for question on arm, as JSON."""
        ...

    def ask(self, question: str, *, purpose: str, arm: str, stages: Any, products: list[str] | None,
            history: Iterable[Any], runtimes: list[str] | None, on_event: Callable[[dict], None] | None = None) -> dict:
        """POST /ask's answer to question on arm, as JSON; on_event, where given, is called with each pipeline event the
        direct stream would send, in order, each once, while the answer runs."""
        ...
