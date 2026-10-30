# Standalone Graph Model Spec — draft v0.3

Changelog: v0.3 (issue #39, 14 Oct 2026) adds §8, profiles and layout, as the demo builds them: one platform build, each client's pack, compiled artifact, graph store and runtime overlay in folders of its own, and the client chosen at deployment. The owner's R1 revises §3.3 and §4: AI certification keeps a store of its own, and its outcomes are read from the landing zone on every load, never kept in the runtime overlay. §5's extensions are namespaced by the client, §6 names the artifact's folders, and a line marked **Built (#39)** says what the demo now builds where the text below proposes it. v0.2 folds in the AI-readiness tier design (Tier node, Tag 1/Tag 2 fields, AT_TIER and DERIVED_UNDER edges, TAGGED as a rel group, denormalised `ai_ready`/`tier`, `Chunk.gate_level`, §3.3). v0.1 was the first draft.

Status: draft. Grounded in `kge/graph/schema.py` and `kge/graph/load.py` on `origin/develop` @ `01836f6`
(`docs/use-cases/kge/demo/` there, now [`demos/kge/`](../../../../demos/kge/README.md)). Today the schema exists only as
Kuzu DDL strings in Python (`NODE_TABLES`, `REL_TABLES`), and instance loading
is hand-written Cypher per type in `load.py`. This spec makes both a data
artifact.

## 1. Two documents, one format

| Document | Owner | Changes when |
|---|---|---|
| **Meta-model** (`meta-model.yaml`) | platform repo | platform release; semver; domain-agnostic |
| **Graph model** (`graph-model.yaml` + data) | emitted by the compiler from a pack | every pack compile; content-hashed |

Format: YAML authored, canonical JSON emitted (sorted keys, LF, UTF-8) so the
hash is stable. Same JSON Schema validates both.

## 2. Element grammar

```yaml
node_types:
  <Name>:
    layer: harvested | curated | access | runtime     # who writes it (see §4)
    key: [<prop>, ...]                                 # primary key; Kuzu supports one column -> composite keys are rendered as a derived id
    id_template: "{source}.{table}.{name}"             # optional; deterministic id grammar
    properties:
      <prop>: {type: STRING|INT64|DOUBLE|BOOLEAN|DATE|TIMESTAMP|STRING[]|FLOAT[n],
               required: bool, enum: [...], default: ..., description: ...}
    provenance: none | assertion | generated           # adds standard props, §2.1
    extensible: bool                                   # may packs add x_ properties
edge_types:
  <NAME>:
    from: <Node>   to: <Node>
    cardinality: one_to_one | one_to_many | many_to_one | many_to_many
    symmetric: bool                                    # e.g. NO_JOIN is unordered
    layer: ...
    properties: {...}
    provenance: ...
```

### 2.1 Standard provenance property sets
- `assertion` (human/curated facts): `asserted_by STRING req`, `asserted_on DATE req`, `note STRING`, `pack_version STRING req`.
- `certification` (adds to assertion): `certified BOOLEAN req`, `certified_by`, `certified_on`.
- `generated` (machine output): `generator STRING req enum[human, rule, model]`, `confidence DOUBLE req`, `model STRING`, `prompt_version STRING`, `created_at TIMESTAMP`, `evidence STRING`.
- `lifecycle` (NEW in v0.2, on every `Tag`; the brief's §4.5.1 minimum envelope + policy link): `effective_from DATE req`, `effective_to DATE` (null = open until superseded; for Tag 1/Tag 2 defaults to the readiness policy's review window), `version STRING req`, `policy_version STRING` (req for `ai_readiness` and `ai_certification_tier`), `bypass STRING` (Rule 2 bypass id, null otherwise).

The demo already uses each set, inconsistently: `NO_JOIN` has assertion props,
`MAPS_TO` mixes certification + generated, `Tag` and `MENTIONS` carry generated
props, `Col.certified` duplicates `MAPS_TO.certified`.

## 3. Platform meta-model (fixed)

### 3.1 What exists today (kge/graph/schema.py): 9 node types, 16 edge types

Nodes: `Unit`, `Tbl`, `Col`, `Term`, `Document`, `Chunk`, `Tag`, `Principal`, `Process`.

Edges:
| Edge | From → To | Props today | Written by |
|---|---|---|---|
| HAS_TABLE | Unit → Tbl | – | load.py (harvest) |
| HAS_COLUMN | Tbl → Col | – | load.py (harvest) |
| JOINS | Col → Col | via | load.py (declared FKs) |
| JOINS_TABLE | Tbl → Tbl | from/to table/column | load.py (declared FKs) |
| MAPS_TO | Col → Term | certified, certified_by, certified_on, generator, confidence | load.py (mappings.csv) |
| NO_JOIN | Unit → Unit | asserted_by, asserted_on, note | load.py (no_join.json) |
| CAN_READ | Principal → Unit | – | load_principals |
| CAN_WRITE | Principal → Term | – | load_principals (only for `writes: terms`) |
| MAY_PERFORM | Principal → Process | – | load_principals |
| HAS_CHUNK | Document → Chunk | – | kge/pipeline/run.py |
| DEFINES | Document → Term | – | load.py; steward accept in core.py |
| SUPERSEDED_BY | Document → Document | – | load.py |
| MENTIONS | Chunk → Term | confidence, generator | kge/pipeline/run.py |
| TAGGED | Chunk → Tag | – | pipeline, steward |
| SUPERSEDES | Tag → Tag | – | steward_decide |
| PROPOSES | Tag → Col | term | **nothing** (declared, never written) |

### 3.2 Meta-model v1 (proposal): 15 node types, 25 edge types

That is the 9 + 16 from §3.1, plus new nodes `Measure`, `Calendar`, `Alias`, `Policy`, `Tier`, `Role` and new edges `MEASURE_OF`, `COMPUTED_FROM`, `IN_CALENDAR`, `PERIOD_COLUMN`, `ALIAS_OF`, `GOVERNS`, `AT_TIER`, `DERIVED_UNDER`, `HAS_ROLE`.

Renames for clarity (aliases kept for the demo's Cypher): `Unit`→`Source`,
`Tbl`→`Table`, `Col`→`Column`. Additions are marked NEW; each closes a place
where the demo encodes domain meaning in Python.

**Built (#39):** `meta-model.yaml` 1.0.0 built this v1 as 15 node types and 22 edge types. Each type has its logical
name here and the name the graph loads it under (`loaded_as`: `Unit`, `Tbl`, `Col`; `Term` keyed by its name, with an
`id` slug beside it), so the runtime's Cypher ran unchanged. It adds `Release`, the landed release a graph was loaded
from, and `GraphModel`, the compiled artifact by its model hash. `Tier`, `Role`, `AT_TIER`, `DERIVED_UNDER` and
`HAS_ROLE` wait for Phase 4: the deployment's identities are bound to the pack's role templates at load, and `CAN_READ`
stays on `Principal`. The measure fields stay on `Col` beside `Measure`, denormalised from it (`denormalised_from`).
Versions 1.1.0 and 1.2.0 only add to it (§8).

| Node | Layer | Key | Notes |
|---|---|---|---|
| Source (Unit) | harvested+curated | id | `label`, `entity_key`, `dialect` |
| Table (Tbl) | harvested | id = `{source}.{table}` | |
| Column (Col) | harvested | id = `{source}.{table}.{column}` | `data_type`, `description`, `is_key`. Drop `certified/grain/currency/calendar/conversion` here → moved to Measure / MAPS_TO. Denormalised from tags (§3.3): `ai_ready BOOLEAN` (default false), `tier STRING enum[bronze, silver, gold]` (nullable) |
| Term | curated | **id** (slug; today `name`) | `name`, `domain`, `steward`, `definition`, `status enum[certified,candidate]` |
| Measure **NEW** | curated | id | `expression`, `aggregation enum`, `grain`, `currency`, `conversion`, `snapshot`, `population`. Calendar via `IN_CALENDAR` edge (not a property). Today: `MEASURE_METADATA` flattened onto Col |
| Calendar **NEW** | curated | id | `year_start_month`, `quarter_label`. Today: `fiscal_from_calendar` in core.py |
| Alias **NEW** | curated | phrase | `kind enum[term,source,candidate,group_by,entity,time_mode,period]`. Today: `ALIASES`, `UNIT_WORDS`, `DEFINITIONAL`, `FILTER_PATTERNS` |
| Policy **NEW** | curated | id | `kind`, `params` (JSON), `version`. Today: `special` branches in `Engine.answer`; **Built (#40):** the two that named the client read `NOT_JOINABLE_TO` and the `Measure` currencies instead, with no new kind. `kind = readiness` holds the gate, Tag 1, Tag 2 and bypass rules (§3.3) |
| Tier **NEW** | curated (origin: pack) | id enum[bronze, silver, gold] | `rank INT64` (bronze 1 < silver 2 < gold 3), `allows STRING[]` ⊆ {embed, retrieve, cite_attributed, cite_authority}, `description`. Governance-owned data, not code |
| Document | curated (registry) | id | `title, shelf enum[certified,uncertified], kind, owner, version, published_at`. Denormalised from tags (§3.3): `ai_ready BOOLEAN` (default false), `tier STRING enum[bronze, silver, gold]` (nullable) |
| Chunk | runtime | id | `text, gate_level enum[prohibited, redacted, clear]` (NEW), `readiness enum[embed-prohibited, retrieval-internal, citable]` (kept for compatibility, §3.3), `purposes, embedding FLOAT[dim], embedding_model` |
| Tag | runtime | id | generated provenance; `family enum[business_meaning, classification, privacy, candidate_term, ai_readiness, ai_certification_tier, privacy_review]`, `value`; NEW `effective_from DATE`, `effective_to DATE` (nullable), `version STRING`, `policy_version STRING`, `bypass STRING` (the Rule 2 bypass id that applied, null otherwise) |
| Role **NEW** | access | id | pack role templates; today roles are a string on Principal |
| Principal | access (deployment) | id | `role` → becomes HAS_ROLE |
| Process | platform constant | id | tagging, certification, querying |

| Edge | From → To | Card. | Layer / provenance |
|---|---|---|---|
| HAS_TABLE | Source → Table | 1:N | harvested |
| HAS_COLUMN | Table → Column | 1:N | harvested |
| JOINS | Column → Column | N:M | harvested (`via`) |
| JOINS_TABLE | Table → Table | N:M | harvested (derived from JOINS) |
| NO_JOIN | Source ↔ Source | N:M, symmetric | curated, assertion |
| MAPS_TO | Column → Term | N:M | curated, certification (+generated when machine-proposed) |
| MEASURE_OF **NEW** | Measure → Term | N:1 | curated |
| COMPUTED_FROM **NEW** | Measure → Column | N:M (`role enum[value, filter, period]`) | curated |
| IN_CALENDAR **NEW** | Measure → Calendar | N:1 | curated |
| PERIOD_COLUMN **NEW** | Table → Column | 1:N (`kind enum[month, fiscal_quarter, snapshot, event_date]`) | curated |
| ALIAS_OF **NEW** | Alias → Term/Source/Column | N:1 | curated (Kuzu needs one REL table per from/to pair, or a REL GROUP) |
| GOVERNS **NEW** | Policy → Column/Source/Term/Document | N:M | curated. Scope only: which assets a policy covers. Never carries a tier or outcome |
| DEFINES | Document → Term | N:M | curated; steward accept writes it at runtime too |
| SUPERSEDED_BY | Document → Document | N:1 | curated |
| HAS_CHUNK | Document → Chunk | 1:N | runtime |
| MENTIONS | Chunk → Term | N:M | runtime, generated |
| TAGGED | **rel group** [Chunk, Document, Column] → Tag | 1:N | runtime. Today Chunk → Tag only. Tag 1 and Tag 2 attach to Document or Column, the asset a steward reviews |
| SUPERSEDES | Tag → Tag | 1:1 | runtime (human decision) |
| AT_TIER **NEW** | Tag → Tier | N:1 | runtime; only from `ai_certification_tier` tags |
| DERIVED_UNDER **NEW** | Tag → Policy (`kind = readiness`) | N:1 | runtime; which rule-pack version produced the candidate |
| PROPOSES | Tag → Column | N:M (`term`) | runtime; **reserved** for column-term proposals (`column-term-mapping.md`); keep, mark unused |
| HAS_ROLE **NEW** | Principal → Role | N:M | deployment |
| CAN_READ | Role → Source (today Principal → Unit) | N:M | access |
| CAN_WRITE | Role → Term, Role → Column (mappings) | N:M | access; today only Term, see conflicts |
| MAY_PERFORM | Role → Process | N:M | access |

### 3.3 Readiness and tiers

The readiness design is folded in under the brief's settled rule, **same tag model, additive only** ([`ai-readiness-certification.md`](../ai-readiness-certification.md) §4.5–§5; [logical view](../ai-readiness-certification-logical.md) §4.3, §7). No existing family, edge or property changes meaning. Four things are kept apart:

| Concern | Lives in | Layer | Written by |
|---|---|---|---|
| **Rules**: gate-level conditions, Tag 1 halves, Tag 2 conditions, Rule 2 bypasses, review window | `Policy {kind: readiness}.params`, scoped by `GOVERNS` | curated | compiler, from pack `policies.readiness` |
| **Definitions**: what bronze, silver and gold allow | `Tier` nodes | curated | compiler, from pack `policies.tiers` |
| **Outcomes**: Tag 1 `ai_readiness` yes/no, Tag 2 `ai_certification_tier` bronze/silver/gold | certification's own store, landed with each release (R1, R2); in the graph `Tag` + `TAGGED` (from Document/Column) + `AT_TIER` + `DERIVED_UNDER`, loaded from the landing | curated, from the landing (v0.3, R1) | upstream certification, its steward gate included, in its own store; the graph reads them on every load and never writes them (R1) |
| **Fast lookup** | `Document.ai_ready/tier`, `Column.ai_ready/tier`, `Chunk.gate_level` | runtime-maintained props on curated or harvested nodes | rewritten on every derivation, like `Chunk.readiness` today |

Rules:
- **Gate level** per chunk: `prohibited < redacted < clear`; absent ranks as prohibited. It decides embedding only (logical view decision 1). Today's `readiness` maps as `embed-prohibited → prohibited`, and `citable` and `retrieval-internal → clear`. The demo has no `redacted`. Shelf-dependent citing moves to Tag 2.
- **Worst chunk sets the document.** Tag 1 on a Document is yes only if `min(gate_level)` over its chunks ≥ redacted, plus the consumable half. A redacted document caps at silver.
- **Tag 2 only when Tag 1 is yes.** A tag of family `ai_certification_tier` has `value` equal to its `AT_TIER` target id, which the validator checks.
- **Steward gate (Rule 1).** Every candidate is `generator: rule` until a steward confirms it (`generator: human`, `SUPERSEDES`). **Rule 2 bypass:** `generator: rule` with `bypass` set to the governance-defined bypass id. No tier is ever revised upward by hand.
- **Lifecycle.** `effective_to` defaults to the policy's review window. Expiry or a policy-version change re-derives; `DERIVED_UNDER` answers which tags are affected.
- **Fail closed.** If there is no live Tag 1, then `ai_ready = false` and `tier = null`. If Tag 1 is no, then `tier = null`.
- **Name collision with the demo.** The demo already writes a chunk tag `family: ai_readiness` whose value is the three-level chunk readiness (`kge/pipeline/state.py:rederive`). In this model, that tag is legacy and carries the gate level. Tag 1 is recognised only when it's `TAGGED` from a Document or Column (pack-contract conflict 23).
- **Grain.** Tags are decided on Document and Column. Chunks inherit by traversal (`HAS_CHUNK`), not by copy. **There are no tiers on Term**: a term is curated meaning, not a served asset. **Source** may carry an optional derived summary, such as share of columns at each tier, computed and never authored. It is not in the meta-model v1 properties.

**Revised by R1 (v0.3).** The owner's R1: AI certification runs before graph ingestion and has its own data store, as
another data application, and is not mingled with the graph layer. So Tag 1, Tag 2, their steward gate (Rule 1, Rule 2)
and their lifecycle are certification's. It lands its outcomes into the landing zone with each release (R2), and the
graph reads them from the landing on every load, failing closed: no landed outcome, no AI readiness. The graph never
writes them, so they are not runtime-layer rows and never sit in the runtime overlay (§4, §8), and a recompile cannot
lose them. Where the rules above have a steward confirm a candidate (`generator: human`, `SUPERSEDES`), that happens in
certification's store, before the outcome lands. The fast lookup fields are set from the landing at load, and
`Chunk.gate_level` by the pipeline as before. **Built (#39):** `kge/estate/certify.py`, the demo's stand-in for
certification, keeps its run record in its own store, `<estate>/certification/<client>/runs/` in the client's simulated
estate (§8), and lands one row per document in the release's `certification.json`; `kge/graph/load.py` reads each onto its
`Document` (`ai_ready`, `tier` and the certification fields), and a document with no landed row loads as not AI-ready.

## 4. Layers and who writes what

| Layer | Written by | Survives recompile? |
|---|---|---|
| harvested | compiler, from `pack.lock` snapshot | replaced |
| curated | compiler, from pack (includes `Policy`, `Tier`) | replaced |
| curated, from the landing (v0.3, R1) | upstream certification, from its own store: the document registry and each document's Tag 1 and Tag 2 outcomes and denormalised `ai_ready`/`tier`, read from the landing zone at load | replaced: read again from the landing on every load; the graph never writes them back |
| access | deploy step (IdP bindings + pack roles) | replaced |
| runtime | the pipeline (chunks, its tags, mentions, `gate_level`) and stewards (human tags, `SUPERSEDES`, accepted terms) | **must survive**: steward decisions are the runtime overlay, journaled in the client's graph store and re-attached by stable ids (Chunk id, Tag id, Term id); the pipeline's tags are derived again under the same ids. **Built (#39)**, §8 |

**Revised by R1 (v0.3).** v0.2 had the upstream certification producer write the runtime layer. R1 keeps certification
apart, with a store of its own (§3.3): what it certified lands in the landing zone (R2), and every load reads it from
there, as it reads the pack's compiled layers from the artifact. The runtime overlay holds what is decided on top of the
loaded graph, steward decisions, and never certification; the pipeline's own tags need no overlay, since it derives them
again under the same ids. **Built (#39):** the demo's `make graph` still builds the graph from scratch, and the steward
decisions survive it: they are journaled in the client's graph store and replayed by every load (§8).

## 5. Pack extensions

A pack may, in `schema.extensions`:
- add node types (`x_<client>_<Name>`, namespaced by the pack's client id, v0.3) in layer `curated` only;
- add edge types between any meta or extension types;
- add optional properties (`x_<client>_<prop>`) to `extensible: true` meta types;
- add enum values to enums marked `open: true` (e.g. `Document.kind`, `Tag.family`).
It may not remove, rename or retype meta elements, make an optional meta
property required, or write to the runtime/access layers.
Meridian needs no extensions; a fraud pack (`docs/use-cases/fraud/` on
`feature/fraud-use-cases`) is the obvious first test of the mechanism (speculative).

**Built (#39):** the compiler holds a pack's extensions to these rules (S04 in pack-contract §5.1): each adds one node
type, edge type or property, curated, namespaced `x_<client>_` (a hyphen in the client id is an underscore there), and
takes no name the meta-model has. Meridian's pack and Thornbury's declare none.

## 6. Compile flow

```
pack/ ──S-rules──► resolved pack (canonical JSON)
          │
meta-model.yaml ─┤ merge extensions, check §5 rules
          ▼
graph-model.yaml (schema)  +  data/nodes/<Type>.csv, data/edges/<EDGE>__<From>__<To>.csv  (instances)
          │                                       │
          ├─► ddl/kuzu.cypher (CREATE NODE/REL TABLE, generated)
          ├─► ddl/spanner.sql (optional target; schema.py docstring claims portability)
          ▼
manifest.json { client, pack_id, pack_version, pack_hash, lock_hash,
                meta_model_version, compiler_version,
                schema_hash, data_hash, model_hash = sha256(schema_hash ‖ data_hash),
                tests: frozen golden/heldout with resolved figures }
          ▼
written to artifacts/<client>/<model_hash>/   (v0.3, §8)
```

Loading = `COPY <Table> FROM 'data/…csv'` per type in a fixed order (nodes, then
edges), replacing the per-row `CREATE` in `load.py`. `Tier` and `Policy` load with
the other curated nodes, before the runtime overlay is re-attached. Runtime
`AT_TIER` and `DERIVED_UNDER` rows re-resolve against `Tier.id` and `Policy.id`,
which are stable across compiles. Runtime services read
`manifest.json` and refuse to serve a model whose `meta_model_version` they do
not support. Runtime-layer rows carry the `model_hash` they were written against
so a recompile can report orphans (e.g. a Tag whose Column vanished).

**Built (#39)** (§8), as drawn but for the manifest and the tests. The manifest names the client, and its model hash is
one sha256 over every other file's path and sha256, not `schema_hash ‖ data_hash`. The tests are not frozen into the
artifact: the pack names its versioned question sets, and restating them with `reference_sql` was cut from issue #40 and
is left as a later step. No Spanner DDL is written. The loader (`kge/graph/load.py`) checks the artifact whole first (every
file's sha256, the model hash, the
format, a meta-model version of its own major and no newer, the embedding dimension and the client) and copies the
files in the manifest's load order, nodes then edges; `kge/graph/legacy.py` keeps the per-row loader it replaced as the
parity oracle. Each record of the runtime overlay carries the model hash it was decided on, and one whose chunk,
document or term is gone, or whose chunk's text changed, is reported as an orphan and never attached.

## 7. Determinism rules
- Ids from `id_template` only; no UUIDs in compiled layers (the demo uses `uuid4` only in steward tags, runtime layer: fine).
- Rows sorted by key; properties in schema order; floats rendered with repr.
- No timestamps in compiled data except values from the pack.
- `embedding_dim` is a compile parameter from the manifest, not an import (`schema.py` imports `kge.adapters.model.embed.DIM`). **Built (#39):** the pack's `compile: {embedding_dim: 128}` sizes the emitted DDL's embedding column, and the loader refuses an artifact whose dimension is not the encoder's.
- **Built (#39):** rows are sorted by key, properties come in the meta-model's order, CSV lines end in `\n`, NULL is `\N` in a text column and an empty field in any other, JSON is canonical and YAML dumped with fixed options, and no file carries a timestamp or an absolute path, so the same pack, lock and compiler give the same bytes and the same model hash (`kge/compiler/emit.py`).

## 8. Profiles and layout

**Built (#39)** in the demo, [`demos/kge/`](../../../../demos/kge/README.md), whose folder the paths below are relative
to. One platform build serves every client. Each client's pack, compiled artifacts, graph store and runtime overlay sit
in folders of its own, and which client a runtime process serves is chosen by config at deployment, never per request.
The dependency runs one way, client to platform: no platform file names a client.

```
meta-model.yaml                     platform: node and edge types, semver (1.2.0)
kge/compiler/                           platform: pack.py, lock.py, checks.py, emit.py, compile.py
kge/landing/zone.py                     platform: the landing zone's contract
kge/app.py kge/services/ kge/semantics/ kge/graph/ kge/pipeline/ kge/eval/ ui/     platform: the runtime, the same code for every client
clients/<client>/                   domain: pack.yaml, pack.lock, baselines/ (and a fixture profile's fixture/)
deploy/profiles.yaml                deployment: profile id -> pointers only
deploy/<client>/identities.yaml     deployment: principals bound to the pack's role templates
build/                              generated, git-ignored
  <estate>/certification/<client>/runs/         certification's own store (R1), in the client's simulated estate
  landing/<client>/releases/<release_id>/       the landing zone (R2), and current -> the latest release
  artifacts/<client>/<model_hash>/              a compiled artifact:
    graph-model.yaml, manifest.json               the schema, and the manifest
    catalogue.yaml                                the derived discovery catalogue and optional enrichment
    ddl/kuzu.cypher                               the DDL, in the kuzu dialect
    data/nodes/<Type>.csv                         one per node type the pack or its lock fills
    data/edges/<EDGE>__<From>__<To>.csv           one per edge type and pair of ends
    access/roles.json                             the role templates, bound to identities at load
    gates.json                                    the pack's post-load gates, where it declares them (#40)
  artifacts/<client>/current                    -> <model_hash>, swapped atomically on release
  stores/<client>/<model_hash>/graph.ladybug    a graph per artifact loaded, with its LOADED marker (GATE, SIGNOFF, RELEASED: #40)
  stores/<client>/overlay/                      the runtime overlay: the steward decisions' journal
  stores/<client>/run/                          the run state of the process that serves the profile
```

The issue's three repositories are folders of one for now: the platform's, `clients/` and `deploy/`. Moving a client's
pack to a repository of its own is an edit to the registry, which the exit test makes by pointing one profile's
`clients` outside the demo folder. Compiled artifacts and graph stores are generated, like everything under `build/`.
A client's estate, the simulated systems certification reads (`<estate>` above), is the folder that holds its
warehouses: `build/` itself for Meridian, so its certification store is `build/certification/meridian/runs/`, and
`build/thornbury/` for Thornbury, a fixture profile, whose store is `build/thornbury/certification/thornbury/runs/`.

**The artifact.** `make compile` (`kge/compiler/compile.py`) reads `meta-model.yaml`, the client's pack,
`clients/<client>/pack.yaml` with the files it includes, and its lock, `pack.lock` beside it, and nothing else: no
landing, no warehouse and no graph. It checks the pack (pack-contract §5.1), and on any finding lists each one and writes
nothing; otherwise it writes `artifacts/<client>/<model_hash>/`, laid out as above, and prints that path. Its
`manifest.json` names the `format`, the `client` (the pack's own, which is its folder's name), the pack's `title`, the
`pack` (id, version and hash), the `lock_hash`, the `meta_model_version`, the `compiler_version`, the `target` (the DDL
dialect), the `params` (the embedding dimension and the id scheme), the `files` with the sha256 of each, the `counts`, the
`load_order` and the `model_hash`: the sha256 over every other file's path and sha256. Node and edge files are named by
the types the graph loads them under (`Unit`, `Tbl`, `Col` and the rest), one edge file per pair of ends of a rel group.
The same pack, lock and compiler give the same bytes and the same hash (§7). The loader, `kge/graph/load.py`, refuses an
artifact whose client is not the profile's, or the landing's.

**Release and `current`.** `make load` loads an artifact into a fresh graph of its own,
`stores/<client>/<model_hash>/graph.ladybug`, with the landed release and the deployment's identities, runs the pipeline
on it, which replays the runtime overlay, and writes its `LOADED` marker last. It never opens another hash's folder, so
the graph being served, and its lock, are left alone: a candidate is loaded beside it. `make release` (`kge/graph/release.py`)
swaps `artifacts/<client>/current` to the hash, refusing one whose graph is not `LOADED` or whose artifact
is not whole: a new link renamed over the old one with `os.replace`, so `current` names one hash or the other at every
instant. The command-line runtime reads `current` when it starts, so a command-line release takes effect on restart. A
release from the page switches that serving process immediately, and each Cloud Run service follows on its next status
poll. Releasing an old hash again needs no load, since old folders stay. `make graph` is the lock's check, `make compile`, `make load` and
`make release` in a row. **Built (#40):** the post-load checks and a steward sign-off sit between load and release:
`make margin HASH=` writes the candidate's `GATE`, `make sign-off PRINCIPAL=` its `SIGNOFF` (a principal whose role may
perform certification, on a passed check), and `make release` refuses a hash without both unless it was released before
(`RELEASED`, or the hash currently served); a reload drops all three with `LOADED`. Any release requires every
artifact-named warehouse version and every unit warehouse. A rollback also requires its kept pack and successful
reconstruction of its onboarded units. `make graph` and `make demo` release without the gate and sign-off and say so.

**The graph store.** Ladybug, a maintained fork of Kuzu with the same Python API and Cypher, and the only store (the
owner, 20 Oct 2026: Kuzu, archived upstream after 0.11.3, was removed with its switch). `kge/adapters/graphstore/ladybug.py` is the only
module that imports it. The graph is `graph.ladybug` in the hash's folder. It loads the DDL in the `kuzu` dialect, the
Cypher dialect's name, kept: the pack's `compile: {store: kuzu}`, the manifest's `target`. Copying with `null_strings`
set to `\N` keeps an empty text field apart from NULL.

**Profiles.** The registry is `deploy/profiles.yaml`: a `default`, and per profile id the `client` and pointers only,
each relative to the file: `clients`, `artifacts`, `store` and `landing` (folders that hold the client's own, in a folder
named for it), `warehouses`, `identities`, and `baseline` (the A/B baseline, or none). `kge/pipeline/profile.py`
resolves the profile a process serves: `PROFILE`, else the registry's default, with `PROFILES` naming another registry.
It checks the registry whole first: every profile id a slug and each client id a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters, no profile's folders shared with or nested in another
root, its own or another profile's, and the artifact each profile's `current` names compiled for its client. An unknown
profile stops every command, naming the known ones. Two decisions shape this: one runtime process per client, chosen by
config at deployment, rather than one router that serves many (the issue's first open decision: the owner's choice,
made in a working session and not yet recorded on #39); and so the registry is deployment config, not the platform's
(the second, built as the default until the owner confirms it): the owner's R8 has profiles be "config-level
changes only, picked at deployment time", and the platform never names a client, so a file that lists the clients cannot
sit in it. The layout's optional platform `profiles.yaml` is therefore left out. A request never names a profile: each run
config carries the process's, which the run trace and every A/B run record ([runtime-pipeline.md
§4](runtime-pipeline.md#run-config)).

**The runtime overlay (R1).** What stewards decide is kept in the client's graph store, not in the graph and not in the
pack or the landing: an append-only journal, `stores/<client>/overlay/decisions.jsonl` (`kge/pipeline/overlay.py`), each
record written once its graph writes commit and synced to disk. A record is keyed by stable ids: the client, the model
hash of the artifact it was decided on, who decided, when and what, the human tag written (or the reject request waiting
for a second steward), a snapshot of the machine tag decided, the chunk and the sha256 of its masked text, and, for an
accepted candidate term, the term and its defining document. Every load replays it: `kge/graph/load.py` the accepted terms,
`kge/pipeline/run.py` the decisions, merging, so replay twice changes nothing. A record whose chunk, document or term is
gone, or whose chunk's text changed, is an orphan, reported and never attached, and a journal holding another client's
records is refused whole. The overlay holds steward decisions, and never certification: R1 keeps AI certification apart,
with a store of its own, `<estate>/certification/<client>/runs/`, and what it landed is read from the landing on every
load (§3.3, §4). The pipeline's tags need no overlay: they are derived again from the landed chunks under the same ids,
so they survive a recompile without it. Nor does it hold chunk text, vectors or access edges, which come back from the
landing and the access mirror. `make reset-overlay` sets the journal aside, kept in `overlay/archive/`.

**The meta-model as built.** `meta-model.yaml` 1.3.0 has 26 node types and 45 edge types, each with its logical name,
the name it is loaded under, a layer (harvested, curated, access or runtime), an origin (the lock, the pack, the landing,
the deployment, the platform, the compiler or the pipeline) and typed properties, from which `kge/graph/schema.py` builds
the DDL. Version 1.0.0 is §3.2's v1 as built. A minor version only adds:

| Version | Adds |
|---|---|
| 1.1.0 | The query-semantics layer. Nodes `Context` (a metric variant a term resolves to), `Filter`, `RowSelector`, `Join` (loaded as `JoinRule`), `Acronym`, `Entity`, `Relation`, `OntologyConcept`, `Golden` (a golden question's id and set version only) and `CuratedSource` (the curated layer's own tables, such as a certified client cross-reference, which no unit's query sees). Edges `RESOLVES_TO`, `SPECIALIZES`, `USES_FACT`, `CERTIFIED_BY`, `REQUIRES_FILTER`, `SELECTS_ROWS_WITH`, `TARGETS`, `CONFLICTS_WITH`, `REQUIRES_JOIN`, `JOIN_FROM`, `JOIN_TO`, `IMPLEMENTS`, `ABBREVIATED_AS`, `SPELLED_WITH`, `REPRESENTS`, `IDENTIFIED_BY`, `REL_FROM`, `REL_TO`, `MAPPED_TO`, `DERIVED_FROM`, `NOT_JOINABLE_TO`, `HAS_CURATED_TABLE` and `CERTIFIED_JOIN`; policy kinds `scd2` and `release_driver` for time semantics |
| 1.2.0 | `Vocabulary`: the rest of the question parser's vocabulary (value vocabularies, predicates, intents and functional mailboxes), and the pack's title on `GraphModel` |
| 1.3.0 | The industry standards. `OntologyConcept` gains its definition, sub-graph, standard, standard version, licence and source, so the platform's preloaded reference concepts (`standards/preload.yaml`) load into every client's graph; `MAPPED_TO` gains a third end, `Policy`, and its SKOS relation and note |

Operational processes `drafting`, `building`, `improving` and `accepting` are listed in `kge/pipeline/access.py`. S14 accepts role grants from that list or the meta-model's `Process` constants. They do not change meta-model 1.3.0. The `knowledge_engineer` role holds `drafting`, `building`, `improving` and `querying`; the business team accepts, and stewards certify, sign off and release.

`CERTIFIED_JOIN` (Source to Source, with assertion provenance) sits beside `NO_JOIN`: a curated join fact between two
sources, through a declared join on a curated cross-reference, that replaces their no-join fact. Meridian's pack replaces
retail-wealth's that way (asserted by the Data Architecture Board on 2026-10-10, note "certified client
cross-reference"), the owner's Option B: the pack says which fact it replaced, rather than dropping it silently, and the
other five pairs keep their `NO_JOIN` facts.
`NOT_JOINABLE_TO` (a column to a table) refines `NO_JOIN` at the column level, and the runtime still reads `NO_JOIN`.
Harvested foreign keys still load as join paths; the compiler lists each one no declared join covers as a join
candidate, so harvest proposes and the pack declares.

**The exit gate.** Two profiles run side by side from one platform build: `meridian`, the demo's own, and `thornbury`, a
fixture profile whose estate is built from files (`kge/estate/from_files.py`), with no Python of the client's. Each answers
from its own graph, a token of one is refused by the other, each lists only its own principals and runs, and a steward
decision on one moves only its own overlay. `tests/integration/test_profiles.py` holds this, and `tests/unit/test_client_nouns.py` holds
that no platform line names a client. **Built (#40):** `tests/unit/test_onboarding.py` onboards a third client from its
pack alone, through the steps of [pack-contract.md §7](pack-contract.md#7-onboarding), the post-load checks and a
steward's sign-off among them.
