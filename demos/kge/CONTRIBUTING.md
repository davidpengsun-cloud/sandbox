# Contributing to the KGE demo

How the demo's code is laid out, how to extend it in the places it is meant to be extended, and which tests each kind of change needs. The repository's own rules (branches, pull requests, releases) are in the [repository's CONTRIBUTING.md](../../CONTRIBUTING.md); how to run the demo is in the [README](README.md).

## Module map

The demo is one package, `kge/`, of services, one per capability, served by one app and one image. Each module sits in a layer, and imports only from its own layer or the layers below it, in this order, top to bottom:

| Layer | Folder or module | What it holds |
|---|---|---|
| scripts | `scripts/` (one-off tools, run as `python scripts/<name>.py`, never in the image), `examples/`, `kit/` | Entry points run on their own, which nothing imports |
| app | `kge/app.py` | The app: its state, its life, and every service's router and `/mcp` mounted |
| routes | `kge/services/<service>/routes.py`, `kge/services/ask/mcp.py`, `kge/services/http.py` | HTTP alone: routers, request and response models, sign-in |
| service | `kge/services/<service>/service.py`, `kge/services/onboard/jobs.py`, `kge/services/identity/cli.py` | Each service's logic, which no web framework reaches |
| improve | `kge/improve/` | The improvement job |
| estate | `kge/estate/` | The generated estate and onboarding |
| eval | `kge/eval/` | The question sets, A/B harness, margin and parity |
| core | `kge/services/ask/engine/`, `kge/services/identity/service.py`, `kge/semantics/` | The engine, the reference agent, the combined path, sign-in identities, the shared model |
| graph | `kge/graph/` | Graph load, release and history |
| compiler | `kge/compiler/` | The pack compiler |
| packstore | `kge/packstore.py` | The pack store |
| pipeline | `kge/pipeline/`, `kge/adapters/` | Profiles, graph state, the object store, the SQL gate, the run trace's recording side; the adapters, the model and embedding clients among them |
| landing | `kge/landing/` | The landing zone |
| ports | `kge/ports.py` | The protocols adapters offer |

[`tests/unit/test_layers.py`](tests/unit/test_layers.py) holds the order and fails on an import that goes up a layer, on a service that reaches inside another (a service uses another only through its package, `from kge.services.ask import Engine`), and on logic that imports `fastapi`. The few imports that go up by design are named there, each with its reason. Move code down rather than add to that list.

### `kge/app.py` and `kge/services/`

- `app.py`: the FastAPI app: the engine and identities it holds, pinned per request, its life (the run state's pid, `/mcp`'s session manager), and each service's router mounted.
- `services/http.py`: what the routes share: the app's state read per request (`served`), the bodies' base (`Body`), sign-in (`principal`, `needs`) and demo mode.
- `services/ask/`: `/answer`, `/ask`, `/agent`, `/context`, `/retrieve`, `/map/{term}` (`routes.py`), the MCP server at `/mcp` (`mcp.py`), a term's map and the unmatched-question log (`service.py`), and the engine (`engine/`):
  - `engine/core.py`: `Engine` (parse, map, plan, execute, judge, cite); the names of the three modules below are importable from it too.
  - `engine/question.py`: the question parser (`parse`, `Request`, `Period`) and the calendar, currency and window readings it rests on.
  - `engine/graphview.py`: `Graph`, the graph the engine reads (the graph store, the client's domain, and vector retrieval through the retrieval seam).
  - `engine/config.py`: the run config (`RunConfig`, `STAGES`), the presets (`PRESETS`) and the agent's configs (`AGENT_ARMS`).
  - `engine/combined.py`: one question answered with figures from the warehouse and the documents that back them (`/ask`).
  - `engine/agent.py`: the reference text-to-SQL agent, run with the graph off and on.
  - `engine/context.py`: the context workflow, a LangGraph `StateGraph`, which builds the package the agent's prompt carries.
  - `engine/runtrace.py`: one run's trace: stages, reads, model calls, and the graph's fingerprint; what records into it is `kge/pipeline/trace.py`'s.
  - `engine/present.py`: the rich answer's display block.
- `services/identity/`: `/session`, `/session/demo`, `/whoami`, `/principals` (`routes.py`); who is asking, decided by the server: tokens, sessions and passwords (`service.py`).
- `services/onboard/`: the onboarding routes (`routes.py`); one long-running job per profile, its state, the checks a step passes before it starts and the thread that runs it (`jobs.py`); and the job's commands and Cloud Run client (`service.py`).
- `services/quality/`: `/golden`, `/ab`, an A/B run's report, `/eval`.
- `services/steward/`: the steward's queue and decisions.
- `services/tools/`: the page, `/healthz`, the onboarding guide and its small Markdown renderer (`markdown.py`), `/mode`, `/presets`, `/summary`, `/profile`, `/versions` and the SQL check.

### `kge/semantics/` and `kge/ports.py`

- `semantics/query.py`: a context's query, assembled from the graph's query-semantics layer.
- `semantics/vocabulary.py`: the question parser's vocabulary from the client's pack.
- `ports.py`: the protocols the adapters (`kge/adapters/`) offer: `WarehouseSet`, `Retrieval`, `AppState`, `ObjectStore`, `ModelProvider`, `Embeddings` and `GraphStore`.

### `kge/adapters/`

One package per kind, each offering a protocol of `kge/ports.py`, its local and Google Cloud backends side by side and its `__init__.py` choosing one.

- `appstate/`: onboarding application state, the `AppState` port: files in the pack store (`files.py`), or BigQuery tables on Google Cloud (`bigquery.py`); `for_profile` picks one by `APP_STATE_BACKEND`.
- `graphstore/`: the graph store seam, `GraphStore`:
  - `__init__.py`: the graph store the process runs on (`store()`, chosen by `GRAPH_STORE` each time it is asked) and its members by name, which callers import.
  - `ladybug.py`: Ladybug, the local stack's store, and the only module that imports it.
  - `bigquery.py`: BigQuery, the pilot's store: the node and edge tables and the property graph in the client's graph dataset, the `graph.bigquery` pointer, and the REST client.
  - `cypher.py`, `gql.py`: the Cypher subset the code speaks, parsed, and its BigQuery form: GQL in `GRAPH_TABLE` for a read, DML for a write, and the DDL the compiler emits.
  - `base.py`: what every store shares (`GraphLocked`).
- `model/`: the model seam, `ModelProvider` and `Embeddings`:
  - `__init__.py`: what callers import; `make_llm`, `model_for` and `make_embedder` choose the backend from the settings.
  - `providers.py`, `mock.py`: the one model client, its tiers, and the offline mock.
  - `embed.py`: embedders: hash (the default), local, Vertex AI.
  - `errors.py`: calls to outside services, and the one-line error a failed call raises.
- `objstore/`: the object store an installed deployment keeps a client's state in, the `ObjectStore` port: a local folder (`folder.py`) or a Cloud Storage bucket (`gcs.py`); `open_store` picks one by the URL.
- `retrieval/`: the retrieval seam's backends, `GraphRetrieval` (the graph's own vectors, `graph.py`) and `BigQueryRetrieval` (`VECTOR_SEARCH` and the steward's readiness sync, `bigquery.py`), and `open_retrieval` (`__init__.py`), which picks one from `RETRIEVAL_BACKEND`, else `WAREHOUSE_BACKEND`.
- `warehouse/`: the warehouse seam's backends, `Warehouses` (DuckDB files, `duckdb.py`) and `BigQueryWarehouses` (`bigquery.py`), and `open_warehouses` (`__init__.py`), which picks one from `WAREHOUSE_BACKEND`.
- `bigquery.py`: the BigQuery client every BigQuery adapter shares: the account, one query, the dataset names.

### `kge/compiler/`

- `pack.py`: a client's pack as the compiler reads it.
- `checks.py`: the structural checks a pack passes before it compiles.
- `compile.py`: compiles a pack into a graph artifact.
- `emit.py`: what the compiled artifact holds and how it is written.
- `lock.py`: freezes a client's landed source catalogue into `pack.lock`.
- `drafts.py`: proposes pack additions from one unit's catalogue.
- `standards.py`: the preloaded reference standards.
- `vocabulary.py`: the closed vocabulary set a pack's terminology is checked against.

### `kge/estate/`

- `spec.py`, `generate.py`, `documents.py`: the synthetic estate's shape and build.
- `from_files.py`: an estate built from a client's fixture files.
- `golden.py`, `golden_wealth.py`, `heldout.py`: the question sets.
- `certify.py`: upstream AI certification, as a stand-in.
- `check_estate.py`: the estate is additive.
- `onboard/`: onboarding one business unit, for the commands (`python -m kge.estate.onboard`) and the API; it stays in the estate layer because the batch side calls it (the Makefile's commands, the onboarding job, and graph release and history, which land the certified units before a release), and it reads the ask engine's term parser, which a service may not reach inside another for. One module per step:
  - `common.py`: what every step shares: file names, `OnboardError`, roles and the write check, a unit's manifest and history, landing a step's writes, the unit's warehouse.
  - `source.py`: `tables`, `create` (a new unit, or an update staged beside the live one) and `rows`.
  - `figures.py`: a client's reference figures, checked by its own SQL under the SQL gate.
  - `drafting.py`: `draft`, and what an update changed item by item.
  - `review.py`: `accept` and `certify`, a unit's items or an open proposal's.
  - `status.py`: `show`, `status` and `record-onboard`.
  - `units.py`: `build` (the certified units landed in the pack and their warehouses) and `reset`.
  - `clients.py`: `client` and `reset-client`.
  - `cli.py` and `__main__.py`: the command line.
- `semantic.py`: the semantic-file readers (dbt, LookML, data dictionary, OSI) and the format dispatch.
- `draftflow.py`: the drafting flow, a LangGraph sub-flow with a rule-based fallback at every step.
- `ddl.py`: the small, non-executing DDL reader onboarding uses.
- `load_bigquery.py`: syncs the estate and chunk vectors into BigQuery.

### `kge/eval/`

- `sets.py`: the question sets, read the same way wherever one is read.
- `run_golden.py`: runs the golden set on every preset arm.
- `harness.py`, `metrics.py`, `report.py`: the A/B harness, its measures and its report.
- `margin.py`: the post-load check against the pack's gates and the baseline.
- `parity.py`: preset parity against a frozen snapshot.

### `kge/graph/`

- `schema.py`: the graph schema.
- `load.py`: loads a compiled pack and the landed estate into the graph.
- `release.py`: swaps the client's current pointer to a loaded graph.
- `history.py`: version history, kept packs and rollback.
- `legacy.py`: the per-row loader kept as the parity oracle.

### `kge/improve/`

- `flow.py`: the LangGraph improvement workflow.
- `gather.py`: reads the served release's misses, held-out records dropped first.
- `rules.py`: classifies each miss.
- `edits.py`: proposes edits, checks them against the allowlist, writes the candidate pack.
- `abtest.py`: builds, tests and judges a candidate.

### `kge/pipeline/`

- `profile.py`: the deployment's profiles.
- `state.py`: chunk state: live tags and readiness.
- `sqlguard.py`: the gate every warehouse query passes before it runs, in the `pipeline` layer so the warehouse adapters run it.
- `trace.py`: the run trace's recording side (the open trace, the calls that record into it, the JSON-lines run records), which the adapters and the graph's history use; the trace itself is `kge/services/ask/engine/runtrace.py`.
- `run.py`, `tagger.py`, `chunking.py`, `privacy.py`: tag, gate, embed and write the landed chunks.
- `overlay.py`: the runtime overlay of steward decisions.
- `objstore.py`: the commands an installed deployment runs on its object store: pulls before a command, puts after it, and the store's own commands.
- `unit_history.py`: graph lifecycle events recorded in each unit's application state.
- `warehouse_versions.py`: the versioned warehouses of onboarded units.
- `envfile.py`: loads `.env`.

### Other folders

- `clients/<client>/`: a client's pack, lock, baselines and, for a fixture client, its fixture.
- `deploy/profiles.yaml`, `deploy/<client>/identities.yaml`: the profiles and each client's principals.
- `ui/index.html`: the page, one file.
- `standards/`: the reference standards the platform preloads.
- `scripts/`: one-off tools, run from this folder as `python scripts/<name>.py` (`access_demo.py`, the join-time access rule on two principals); not the package, and never in the image.
- `tests/unit/`, `tests/integration/`, `tests/e2e/`: the tests, a file's folder its lane; the shared helpers and fixtures in `tests/`.
- `infra/`, `kit/`, `Dockerfile`, `entrypoint.sh`: the pilot on Google Cloud and its release kit. The [gcp-pilot-ops skill](../../.claude/skills/gcp-pilot-ops/SKILL.md) covers changes to them.

## Extension recipes

A new backend, or a new operation one needs, follows the ports-and-adapters rule of [demo-layout's code rules](../../.claude/skills/demo-layout/SKILL.md#code-rules).

### A warehouse backend

1. Write a class that offers every member of `WarehouseSet` in [`kge/ports.py`](kge/ports.py). That covers the queries, the gate with the backend's own dry run, the catalogue methods (`catalogue_columns`, `primary_keys`, `foreign_keys`) and `close`. Name the SQL the backend runs twice: `query_dialect`, as a prompt asks a model for it (`"BigQuery GoogleSQL"`), and `sql_dialect`, its sqlglot name (`"bigquery"`). Every query method takes the SQL's `dialect`: the code's own SQL arrives as `AUTHORED`, DuckDB's, which a backend that speaks another rewrites (`BigQueryWarehouses.rewrite_sql`); a model's arrives as `sql_dialect`, which the gate reads as that dialect and the backend runs as written but for its tables' names (`route_sql`), never translated.
2. Return tables by their unadorned names, whatever the backend stores them under.
3. Put it in `kge/adapters/warehouse/`, one module, and select it in `open_warehouses` ([`kge/adapters/warehouse/__init__.py`](kge/adapters/warehouse/__init__.py)) from `WAREHOUSE_BACKEND`.
4. Test it with a fake transport, as `tests/unit/test_bigquery.py` fakes `_post`, including each catalogue method.

Nothing outside the engine reaches a backend's own connection: use the seam's methods, not `w.con`.

### A graph store

[`kge/adapters/graphstore/`](kge/adapters/graphstore/__init__.py) is the graph's one seam: its factory, `store()`, gives the `GraphStore` of [`kge/ports.py`](kge/ports.py) that `GRAPH_STORE` names, Ladybug (`ladybug.py`, the default) or BigQuery (`bigquery.py`), side by side, and the package offers the store's members by name (`STORE`, `store_module`, `graph_path`, `open_db`, `connect`, `store_info`, `graph_q`). Callers (the engine, the graph's load, release and history, the pipeline, the steward) use the port and the package alone: never a backend's module (`tests/unit/test_layers.py` holds it) and never a branch on `GRAPH_STORE`.

The port's operations are what the code needs of a graph: `path` (the graph's file, or a pointer to it), `sidecars`, `open`, `connect` (a connection whose `execute(cypher, params)` returns a result with `get_all` and `get_as_df`), `q`, `info`, `module`, for a load `dialect`, `ddl` and `create` (a fresh graph's tables from the DDL in the store's dialect, which the store's own `ddl` gives from the artifact), for a release `release_entry` (what a release names of the graph, which `kge/adapters/released.py` writes with retrieval's chunks in one release record, `release.json`, whole, and a later load of the same hash leaves), `generation_of` (the generation an entry names) and `served` (what a server opens: `Profile.serve()`, from one read of that record, `Profile.released`, `open`'s `entry`), `batch` (a writer, `kge/adapters/graphstore/base.py` `Writer`: upsert, update, create and delete a node by its key, create and delete edges by their ends' keys, and load an artifact file with COPY's NULL rule, each with the meaning it has run in the order made, which the store sends as it can: Ladybug one statement each, at once; BigQuery one set-based statement per kind and table, in one transaction, when the batch ends), `generation` (the generation a graph's file names, part of a release's identity), and `prune` (drop what nothing reads once a release is made: no pointer names it, and neither do the release ledger's releases; a release's identity is its hash, its graph generation and its chunks table, and the release ledger (`served.json` in the client's graph store) keeps the one just released and the one before it; a service follows each release, the same hash at a new generation too, on its next status poll or a restart, so a prune never drops what it reads as long as it follows before the release after next). A caller writes through a batch and reads nothing it has written in it before it ends. The code speaks one language to every store, the Cypher subset its queries use: Ladybug runs it, and BigQuery runs each query's own form, translated by `gql.py`; a construct outside the subset is refused, naming the query. Answers are the same on both, rows under the same names and types. A new store:

1. Is a module of its own in `kge/adapters/graphstore/`, a class offering every member of `GraphStore`, named in `STORES` in the package's `__init__.py`, the only module that imports its library; a value of `GRAPH_STORE` the package does not know is refused.
2. Runs the Cypher the code sends, itself or through a translation of its own, with the rows Ladybug gives for it: column names as Ladybug names them, NULL where Ladybug has one, NULL sorting last.
3. Is tested without its service: its translations as text, its client against a stand-in of its API, and the demo end to end on a twin of that API answered by Ladybug, as `tests/graphtwin.py` stands in BigQuery's (`tests/integration/test_graphstore_twin.py`: `make parity` and a load, the pipeline and steward decisions give what Ladybug gives). The local stack's checks, `make parity` among them, stay on Ladybug.

### A model provider or tier

1. A provider goes in `PROVIDERS` in [`kge/adapters/model/providers.py`](kge/adapters/model/providers.py), with its request in `Chat._reply`.
2. A REST provider posts through `post`, so a failure is one line naming the service. Add the provider's host to `SERVICES` in [`kge/adapters/model/errors.py`](kge/adapters/model/errors.py) so the line names it.
3. A tier goes in `TIERS`; an agent's preferred tier goes in `PREFERRED_TIER`.
4. Test the provider with a fake transport (`monkeypatch.setattr(llm, "_post", ...)`) and a fake `urlopen` for its error body. Never call a live model in a test; `MODEL_PROVIDER` is `none` or `mock` there.

### A client pack

1. Add the pack under `clients/<client>/` (`pack.yaml` and the files its manifest includes), its principals in `deploy/<client>/identities.yaml`, and a profile in `deploy/profiles.yaml`.
2. Client names live in those files only. The platform code names no client.
3. A client whose estate comes from files keeps them in `clients/<client>/fixture/` (`kge/estate/from_files.py`).
4. Run `PROFILE=<client> make compile`, then `PROFILE=<client> make graph pipeline`; `tests/integration/test_profiles.py` and `tests/unit/test_client_onboard.py` show the checks a new client passes.

### A page tab

1. The page is [`ui/index.html`](ui/index.html). A detail tab is a `<button data-tab="<name>">` in the detail panel's `.tabs` and a branch in `selectTab`; a panel is a `<section class="panel">`.
2. The page reads only what the API returns: add the data to a route first.
3. Check it in `tests/unit/test_page.py` and, for what a presenter sees, in `make demo-script`.

### An API route

1. Add it to the `routes.py` of the service whose capability it is, under `kge/services/`, holding HTTP alone, and put what it does in that service's `service.py`; a new capability is a service of its own, its router mounted in [`kge/app.py`](kge/app.py). A route that reads as a principal takes `who: str = Depends(needs("querying"))` from `kge/services/http.py`, or the process it needs.
2. Answer a question under `runtrace.run(...)` so it carries a run trace.
3. Raise `HTTPException` with the status and a sentence for a refusal. A failed call to an outside service needs nothing: the app answers its `ExternalCallError` with a 503 and its one line.
4. A route the MCP server exposes is a tool in `mcp_server` (`kge/services/ask/mcp.py`), through `tool`.
5. Test it with `fastapi.testclient.TestClient` on a scratch build (`tests/scratch.py`), as `tests/integration/test_tiers.py` does.

### A semantic-file reader

1. Add a reader to [`kge/estate/semantic.py`](kge/estate/semantic.py) that returns the tables, measures and not-drafted items the other readers return, then add its detection to `parse`. A folder must match exactly one format.
2. Read declaratively and never execute what a file contains. Conditions go through `parse_conditions`.
3. Add an example folder under `examples/` and tests beside `tests/unit/test_onboard_unit.py`. Its output passes through the drafting flow like the other formats.

## No comments in the code

The demo's Python carries no `#` comments (issue #83). What a comment would explain goes where it is read with the code:

- **A name** that says what a value or step is.
- **A docstring** for why: the rule a function holds, a data or regulatory caveat, an invariant the code does not show, the issue a behaviour came from. A test's docstring says why the case matters; its assertion says what holds.
- **This file** for a convention that spans many files.

Tool directives stay, bare: `# noqa` with its codes, `# type: ignore`, coverage pragmas, `# gitleaks:allow`, a shebang, a coding line and licence lines. The reason for one, such as why a `noqa: BLE001` catches everything or why a test's key is fake, goes in the docstring, not after the directive.

Two docstrings are read as data, so their format is held: `kge/compiler/checks.py`'s module docstring (the tests parse its rule lines) and `kge/services/ask/engine/runtrace.py`'s `fingerprint` (a test parses its key list). A FastAPI route handler's docstring and a pydantic request model's become OpenAPI text, so an implementation reason goes in the module docstring instead.

[`tools/check_no_comments.py`](../../tools/check_no_comments.py) holds the rule in the repository checks. `tests/test_context.py` and `tests/test_agent_loop.py` are exempt by name: frozen-snapshot tests, edited only by the owner.

## Tests each change needs

| Lane | What it needs | How it runs |
|---|---|---|
| Unit | No build | `make test-unit`, or `.venv/bin/python -m pytest -q -m unit tests/<file>`: the layer test, the compiler's, the error lines' |
| Integration | `make graph pipeline` first, and the API stopped | `make test-integration`; most tests copy the built graph to a scratch folder of their own |
| End to end | Node 18 and the API stopped | `make test-e2e`: `tests/e2e/`'s tests, then `make demo-script`, the README's demo script in a browser |

A test file's folder is its lane: `tests/unit/`, `tests/integration/` or `tests/e2e/`. A new test file goes in one of them, or the collection stops; [`tests/lanes.py`](tests/lanes.py) names the tests of a unit file that need the build, and the two frozen snapshot tests that stay in `tests/`. Shared helpers stay in `tests/`. `make lint` (ruff) runs beside them.

Beside the tests:

- `make parity`: the three rule presets, result by result, against the frozen snapshot. Any change on the rules path runs it, and it must pass with no result moved beyond those stated (the Makefile's `parity` comment).
- `make margin`: the loaded graph against its pack's gates and the A/B baseline. Any change to the pack, the compiler, the loader or the pipeline runs it.
- `make demo-script`: any change to the page or to what a demo step shows.

| Change | Tests |
|---|---|
| Warehouse backend or seam | `tests/unit/test_bigquery.py`, `tests/integration/test_agent.py`, `tests/integration/test_combined.py`, `tests/unit/test_query_semantics.py`, `make parity` |
| Graph store | Integration lane on that store, `make parity` |
| Model provider or tier | `tests/unit/test_model_path.py`, `tests/unit/test_external.py`, `tests/integration/test_run_trace.py` |
| Client pack | `tests/integration/test_profiles.py`, `tests/unit/test_client_onboard.py`, `make margin` |
| Page tab | `tests/unit/test_page.py`, `make demo-script` |
| API route | The route's test through `TestClient`, `tests/integration/test_signin.py` for its sign-in rules |
| Semantic-file reader | `tests/unit/test_onboard_unit.py`, `tests/unit/test_semantic.py` |
| Any import between folders | `tests/unit/test_layers.py` |

The context and agent snapshots in `tests/fixtures/` are frozen: a change that moves them is a change to answers, and the owner decides it.
