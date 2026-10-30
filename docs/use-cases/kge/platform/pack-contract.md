# Pack Contract — draft v0.4

The compiled pack's discovery metadata crosses into portals through the separate, versioned [catalogue provider contract](catalogue-contract.md).

Changelog: **Issue #117 (24 Oct 2026)** makes every client id a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters. **Issue #55 (18 Oct 2026)** adds per-unit learnable (`team_<unit>`) and held-out (`heldout_<unit>`) question sets (§4.12), the LangGraph improvement job (`kge/improve/flow.py`, `make improve`) and its governed proposal lifecycle (`clients/<client>/proposals/open/proposal.yaml`), and version snapshots with rollback (`build/artifacts/<client>/versions/<model_hash>/`, `GET /versions`, §7); lines marked **Built (#55)** state what the demo now does. v0.4 (issue #40, 16 Oct 2026) adds §7, onboarding: the checklist a new client goes through, steps 1 to 9, each with the command that runs it and what shows it done, and the rules a client's data must meet that no check states, found onboarding a client from scratch. A line marked **Built (#40)** says what the demo now does. v0.3 (issue #39, 14 Oct 2026) requires `client` and `meta_model_version` in the manifest, keeps a pack in `clients/<client>/`, namespaces extensions by the client (`x_<client>_`), and adds the checks for the client id and meta-model compatibility (S00, S03), the optional query-semantics sections of meta-model 1.1 with their checks (§4.13, S23–S28), certified joins, the rest of the parser's vocabulary (S29) and identities kept in deployment (§4.10). A line marked **Built (#39)** says what the demo's compiler now does. The onboarding section is issue #40's. v0.2 adds `policies.tiers` and `policies.readiness` (the AI-readiness tier design, same tag model, additive only), validation rules S16–S21 and V05, and conflicts 23–28. v0.1 was the first draft.

Status: draft for discussion. Grounded in the Meridian Governance Graph demo on
`origin/develop` @ `01836f6` (`docs/use-cases/kge/demo/` there, now [`demos/kge/`](../../../../demos/kge/README.md)). Every section below
says which hardcoded thing in the demo it replaces.

## 1. What a pack is

A pack is the **domain repo's source of truth**: plain data files, authored and
owned by the business team, that the platform compiler turns into a loadable
graph model with no human in the loop. A pack contains **no executable code**:
no Python, no regex that encodes control flow, no SQL other than (a) measure
calculation expressions and (b) test reference queries, both of which are
parsed and checked, never `exec`'d as code paths.

Three repos:

| Repo | Owns | Never owns |
|---|---|---|
| platform | meta-model, compiler, runtime services (parser, planner, SQL gate, retrieval, steward workflow, readiness engine, eval harness, UI shell) | any domain noun: unit names, term names, column names, document ids |
| domain | the pack (this contract) + domain models | Python |
| graph store | compiled artifact (`graph-model-spec.md`) + runtime overlay (tags, decisions) | authoring |

**Built (#39):** in the demo the three are folders of one repository, laid out per client: the platform's files, each
client's pack in `clients/<client>/`, and the compiled artifacts and graph stores under the git-ignored `build/`
([graph-model-spec.md §8](graph-model-spec.md#8-profiles-and-layout)). AI certification keeps a store of its own, apart
from all three (the owner's R1), and the graph reads what it landed.

## 2. Layout

```
pack/
  pack.yaml               # manifest (REQUIRED) — may inline every section below
  schema/extensions.yaml  # domain graph extensions (OPTIONAL)
  sources.yaml            # physical sources (REQUIRED)
  glossary.yaml           # terms (REQUIRED)
  terminology.yaml        # aliases, synonyms, value vocabularies (REQUIRED, may be empty lists)
  calendars.yaml          # period semantics (REQUIRED if any measure has a period)
  measures.yaml           # calculation definitions (REQUIRED)
  mappings.yaml           # column -> term, certification status (REQUIRED)
  policies.yaml           # governance policies as data (OPTIONAL; platform defaults apply)
  access.yaml             # roles and data scopes (OPTIONAL; see §4.10)
  tests.yaml              # names the versioned question sets (REQUIRED)
  units/<unit>/draft.yaml # onboarding records merged into the pack (GENERATED)
  pack.lock               # harvested tables, columns and keys (GENERATED, committed)
  catalogue.yaml          # optional descriptive enrichment for the derived discovery catalogue
```

A section may be inlined in `pack.yaml` or split into files listed under
`manifest.includes`. A section is kept in one file: a second file holding the same section is an S01 finding. The
compiler also merges every `units/<unit>/draft.yaml` into the pack's supported draft locations, including sources,
glossary, measures, mappings, aliases and test sets. Draft state contributes to the pack hash. `pack.lock` is canonical
JSON with no timestamp or embedded hash; the compiled manifest records its `lock_hash`.

The compiler creates one discovery product for every table in every onboarded unit without requiring `catalogue.yaml`.
It derives columns and types, certified term mappings, table lineage, row and storage facts, dates, governance status and
deployment ownership from the pack, lock, compiled model and identities. An optional `catalogue.yaml` adds descriptive
text, badges, hierarchy names, baseline activity and ratings, and business-facing field names by product id. It cannot
name an unknown table, column or principal. A pack without it still releases a complete catalogue and the portal works
from onboarding alone.

**Location (v0.3).** A pack lives in `clients/<client>/`, the folder named by its client id, which the manifest's
`client` must equal (S00). Where `clients/` is, is the deployment's: the profiles registry, `deploy/profiles.yaml`, points
each profile at the folder holding its client's pack, so moving a pack to a repository of its own is an edit to the
registry. **Built (#39):** Meridian's pack is `clients/meridian/pack.yaml`, every section inline, with `pack.lock` beside
it and the client's A/B baselines in `baselines/`; a fixture profile's folder holds its `fixture/` too
(`clients/thornbury/`). The question sets are not files of the pack (§4.12).

## 3. Required vs optional

| Section | Req. | Replaces in demo |
|---|---|---|
| `manifest` | R | nothing today (no versioning); `kge/graph/schema.py` imports `EMBED_DIM` from code. v0.3: `client` and `meta_model_version` required |
| `schema` (meta-model version + extensions) | R (version) / O (extensions) | `kge/graph/schema.py` `NODE_TABLES`, `REL_TABLES` |
| `sources` | R | `kge/estate/spec.py` `UNITS`, `UNIT_LABEL`, `UNIT_KEY`, `SCHEMAS`; DuckDB constraint harvest (`harvest` in `kge/graph/load.py`; since #39 certification lands each catalogue, `kge/estate/certify.py:catalogue`, and `kge/compiler/lock.py` freezes it) |
| `no_join` | R if >1 source | `kge/estate/spec.py` `NO_JOIN_FACTS`. v0.3: a top-level section (v0.2 had `sources[].no_join`) |
| `curated_sources` (v0.3) | O | nothing at `01836f6`: the curated layer's own tables, such as a certified client cross-reference, which no source's query sees |
| `certified_joins`, `not_joinable` (v0.3) | O | nothing at `01836f6`: a certified join that replaces a pair's no-join fact, and a no-join fact refined to a column (§4.13) |
| `glossary` | R | `kge/estate/spec.py` `GLOSSARY` |
| `terminology` | R | `kge/services/ask/engine/core.py` `ALIASES`, `UNIT_WORDS`, `FILTER_PATTERNS` (value lists), `DEFINITIONAL` (phrases), `MONTHS`; `ROLE_MAILBOXES` |
| `calendars` | R* | `kge/services/ask/engine/core.py` `period_clause`, `fiscal_from_calendar`, `calendar_from_fiscal`; `kge/pipeline/sqlguard.py` `PERIOD_COLUMNS` |
| `period_bindings` | R* | the column-name sniffing in `period_clause` (§4.6); a top-level section beside `calendars` |
| `measures` | R | `kge/estate/spec.py` `MEASURE_METADATA`; aggregation heuristics in `kge/services/ask/engine/core.py:plan_unit` (`_id/_no/_ref` suffix ⇒ count, BOOLEAN ⇒ count) |
| `mappings` | R | `kge/estate/spec.py` `CERTIFIED_MAPPINGS` |
| `policies` | O | restricted attributes and the `scd2` and `release_driver` time policies |
| `access` | O | `kge/estate/spec.py` `PRINCIPALS`, `PROCESSES`, `PURPOSES` (split, see §4.10) |
| `tests` | R | names versioned question sets as `{name, version, items?}`; their items are build files read by `kge/eval/sets.py` |
| `acronyms`, `entities`, `relations`, `ontology`, `lineage`, `filters`, `row_selectors`, `joins`, `contexts` (v0.3) | O | nothing at `01836f6`: the query-semantics layer of meta-model 1.1 (§4.13) |
| `ontology_mappings` (issue #57) | O | nothing: mappings to the platform's preloaded industry standards, meta-model 1.3 (§4.13) |

**Built (#39):** the compiler requires `manifest`, `schema`, `sources`, `glossary`, `terminology`, `measures`,
`mappings` and `tests` (S01), and `no_join` with more than one source; every other section is optional, `calendars` and
`period_bindings` needed once a measure names a calendar (S09). `tests` names the versioned question sets (§4.12).

## 4. Sections

### 4.1 manifest
`client` (required, v0.3: the client id, a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, equal to the pack's folder name), `meta_model_version`
(required, v0.3: the semver version of the meta-model the pack was written for), `id` (slug), `version` (semver),
`title`, `owners[]`, `platform: {meta_model: "^1.0", compiler: ">=0.1"}`,
`compile: {embedding_dim, store: kuzu, id_scheme: v1}`, `gates: {golden_min_pass: {...}, control_margin: {...}}`.
The issue's rule: each pack declares `client` and `meta_model_version`, and its extra types live under the client's
namespace. S00 checks both are present and well formed; S03 checks the meta-model version against the installed
meta-model and the `platform` range. **Built (#39):** Meridian's manifest is `client: meridian`,
`meta_model_version: 1.3.0`, `platform: {meta_model: "^1.3", compiler: ">=0.4"}`, and
`schema: {meta_model: "1.3"}`; the compiled artifact's manifest names
the client, and the loader refuses an artifact whose client is not the deployment profile's. `title` is the API's and the
page's. In `compile: {store: kuzu}` the store names the DDL dialect, which Ladybug, the demo's only graph
store, loads.

### 4.2 schema
`meta_model: "1.0"` pins the platform meta-model (`graph-model-spec.md` §3).
`extensions:` may add domain node/edge types and namespaced properties
(`x_<client>_*`, v0.3: namespaced by the manifest's client id, a hyphen written as an underscore). May not remove or
retype meta-model elements.

### 4.3 sources
One entry per physical data domain (demo: `Unit`). Fields: `id`, `label`,
`entity_key` (demo `UNIT_KEY`; the cohort planner in `kge/services/ask/engine/combined.py` needs it),
`connection` (dialect + ref, never credentials) and optional `harvest`. Tables are not pack fields. For each harvested
source, `pack.lock` holds its tables, columns (`name`, `type`, `description`, `ordinal`), primary keys and foreign keys
(`columns`, `ref_table`, `ref_columns`). `make lock` freezes the catalogue that certification landed; the compiler reads
no landing or warehouse and compiles from the pack and lock.
`no_join[]`: curated assertions of **absent** joins between sources
(`between: [a, b], asserted_by, asserted_on, note`). Never inferred; absence
from a re-harvest is not falsity (demo comment on `NO_JOIN_FACTS`).
`certified_joins[]` (v0.3): a pair that does join, through a certified cross-reference, has a certified join fact
(`between: [a, b], join, asserted_by, asserted_on, note`) in place of its no-join fact, which it replaces, never
deletes silently; the pair has no `no_join` entry (S27, §4.13). **Built (#39):** Meridian's retail-wealth pair, the
owner's Option B; the other five pairs keep their no-join facts. The lock, `pack.lock`, freezes each harvested source's
catalogue as AI certification landed it (`kge/compiler/lock.py`), never a warehouse read live.

### 4.4 glossary
The section is a list of terms, each with `id` (stable slug — demo keys `Term` by **name**, which breaks on
rename), `name`, `domain`, `steward`, `definition`, `status: certified|candidate`,
`defined_by[]` (document ids), `version`, `effective_from`.

### 4.5 terminology
Pure vocabulary, consumed by the platform parser:
- `aliases[]`: each has `phrase` and exactly one of `term`, `source`, `candidate`, `group_by`, `entity`, `time_mode` or `period`.
- `value_vocabularies[]`: `{column, values[]}` or `{column, pattern, prefix?, after?, before?}`. `pattern` is one of `upper_2`, `upper_2_3`, `upper_4_plus` or `upper_letter`.
- `predicates[]`: `{phrases[], with?, column, op, value?}` e.g. "unresolved" ⇒ `complaint.resolved_on IS NULL`.
- `intents[]`: `{phrases[], intent: definitional|count|avg}` replacing `DEFINITIONAL` and the "how many/average" regexes.
- `functional_mailboxes[]` (demo `ROLE_MAILBOXES`).
Regexes are **not** allowed; patterns are a closed platform enum.
**Built (#39):** Meridian's pack 1.2.0 fills every slot, and since meta-model 1.2.0 the runtime reads them from the
graph (`Alias` and `Vocabulary` nodes) with the matching algorithms unchanged, so no platform file holds the client's
phrases, values or mailboxes. S11 checks the aliases, S29 the rest.

### 4.6 calendars
`calendars[]`: `{id: calendar|fiscal_april, year_start_month, quarter_label}`, where `quarter_label` contains `{n}`.
`period_bindings[]` per table: which column carries the period and its kind
(`month_start` ⇒ month, `fiscal_quarter` ⇒ fiscal quarter, `as_of` ⇒ snapshot,
`*_on` ⇒ event date) plus the calendar join table if any. Replaces the
column-name sniffing in `period_clause` and `PERIOD_COLUMNS`.
**Built (#39):** `period_bindings` is a top-level section, `{table, column, kind, calendar, rollup?: {table, column}}`
(S09), and the runtime reads it on the graph context only; the other arms keep the name heuristic, so the control is
handed no curated metadata.

### 4.7 measures
`measures[]`: `id`, `term` (glossary id), `column` (`source.table.column` in the lock), `expression` (one read-only
aggregate over columns of that column's table, e.g. `sum(monthly_revenue.total_revenue_usd)`),
`aggregation: sum|count|count_distinct|avg|ratio|last_snapshot`, `grain`, `currency`, `calendar`, and `conversion`
(`none` or text such as `period_end_rate_from_EUR`), plus optional `snapshot: bool`,
`population` (for count measures: which entity is counted; demo derives
"different populations" from a `flag` suffix in `commensurable`).

### 4.8 mappings
`mappings[]`: `{column: source.table.column, term, certified: bool, certified_by?, certified_on?}`. The compiler writes
`generator: human` on the emitted mapping edge; it is not a pack field.
Only certified mappings enter the allowlist the SQL gate builds (`kge/pipeline/sqlguard.py:build_allowlist`).

### 4.9 documents (landed content, not a pack section)
Documents are read when the landed release is loaded. A top-level `documents` key is an S01 finding.

### 4.10 access (optional) — split decision
- **Platform owns** processes and what each permits. Meta-model 1.3.0 declares `tagging`, `certification` and `querying`; `kge/pipeline/access.py` lists the operational processes `drafting`, `building`, `improving` and `accepting`. S14 accepts a process from either list. Operational grants do not add meta-model constants. `accepting` maps to `/onboard/accept`; the new-client business role declares it. Existing roles still accept when they write both terms and mappings without certification.
- **Pack owns** role templates: `roles[]: {id, label, reads: [source ids] | all, writes?: [terms|mappings], processes[]}` and required `purposes[]`.
- **Deployment owns** principal identities and role bindings (IdP groups), never committed to the pack.
The demo mixes all three in `kge/estate/spec.py:PRINCIPALS`. **Built (#39):** split as above. The pack's `access` holds
the role templates and purposes; the compiled artifact carries them as access/roles.json, not a graph table; the
deployment binds its principals to them in deploy/<client>/identities.yaml (`deploy/meridian/identities.yaml`), and the
load joins the two into the access mirror (`Principal`, `CAN_READ`, `CAN_WRITE`, `MAY_PERFORM`). S14 checks the roles.
The deployment also names, as `asker`, the querying identity a command asks as when it names none (`make eval`,
`make agent`, `make ab`), which the load checks is one of its querying identities: no platform file names a client's
principal (`kge/pipeline/profile.py:Profile.asker`).

### 4.11 policies (optional; platform defaults apply)
The compiler accepts three policy lists. Their ids share one namespace:
- `restricted_attributes[]`: `{id, action: refuse, cite, columns[], version?, phrases?: [{column, phrases[]}]}`. A
  restricted column is refused by the SQL gate; each phrase belongs to one of that policy's columns (S22).
- `scd2[]`: `{id, table, valid_from, valid_to, is_current, valid_to_inclusive, version?}`.
- `release_driver[]`: `{id, table, period, release_date, freeze_date, released, version?}`.

The other policy kinds proposed in earlier drafts are not pack fields; S01 refuses them.

### 4.12 tests
The pack's `tests.sets` names the versioned sets it is scored on (`{name, version, items?}`:
golden v2, heldout v1, golden_wealth v1) without restating them, so the A/B baselines stay comparable; restating them
in the pack is not supported. The set items are files in the build. A context's golden questions are named by
id from a set listed here (S25). **Built (#55):** an onboarded unit may supply `clients/<client>/units/<unit>/questions.json`
(`team_<unit>_v1`, learnable by `kge/improve/gather.py`) and `clients/<client>/units/<unit>/heldout.json` (`heldout_<unit>_v1`,
never read by `kge/improve/gather.py`, scored during A/B and reported alongside `heldout_v1` in V03 without gating).

### 4.13 query semantics (optional; meta-model 1.1)
v0.3, from the issue's query-semantics addendum: contexts, filters and row selectors, typed joins, acronyms, entities,
relations and ontology concepts, golden questions, lineage and time semantics are generic, so the meta-model holds the
types and each pack supplies the instances. Every section is optional, so a pack written for meta-model 1.0 stays valid,
and the governance layer stays and sits on top. Each is checked before compile:
- `curated_sources[]`: `{id, label, connection, harvest}`, the curated layer's own sources, such as a certified client
  cross-reference. Their catalogue is frozen in the lock after the sources', and no source's query sees their tables.
- `certified_joins[]`: `{between: [a, b], join, asserted_by, asserted_on, note}` (§4.3). Two declared, distinct sources
  that are no `no_join` pair, each pair once, through a declared join on a cross-reference between them; every
  cross-reference join has one (S27).
- `not_joinable[]`: `{column, table, asserted_by, asserted_on, note}`, a no-join fact refined to a column, of a source
  that has a no-join fact with the table's (S26).
- `joins[]`: `{id, kind: key|calendar_rollup|hierarchy|cross_reference, from, to, condition, alias?, through?,
  implements?}`, typed and declared, never inferred. The condition is a conjunction of read-only conditions on the two
  tables' columns, equating a column of the one with a column of the other, and never on a restricted column; a join
  across two sources is a cross-reference through a curated source's table, and implements a declared relation (S23).
  A harvested foreign key is a candidate only: the compiler lists each one no declared join covers, and reports a
  composite one until a join covers it (S05).
- `filters[]` and `row_selectors[]`: `{id, label, table, predicate, conflicts_with?}`, a row selector with
  `mode: exclude` (a fact row it matches is left out) or `include` (kept only where it matches). Read-only conditions on
  their tables' columns (a row selector's also on the facts it is matched against), no restricted column, and conflicts
  declared both ways (S24).
- `contexts[]`: `{id, label, description, term, measure, fact, filters?, row_selectors?, joins?, reaches?, golden?}`, the
  metric variants a term resolves to. A declared term, a declared measure on the context's fact table, joins that each
  follow from the fact or a join before them, filters on tables it always reaches, and golden questions by id from a set
  `tests.sets` names (S25). A row selector on another source's table selects that source's side of a certified
  cross-reference the context reaches, matched against its far table, and applies only across it (S25).
- `acronyms[]`, `entities[]`, `relations[]`, `ontology[]` and `lineage[]`: the abbreviations and spellings of terms and
  columns, the entities and their keys, the relations between entities, ontology concepts, and the tables a table derives
  from. Each names declared terms and entities, and tables and columns in the lock (S26).
- `ontology_mappings[]` (meta-model 1.3, issue #57): the steward's mappings of a declared term, entity or restricted-data
  policy to a concept the platform preloads (`standards/preload.yaml`), by its IRI, with a SKOS relation (`exactMatch`,
  `closeMatch`, `broadMatch`, `narrowMatch` or `relatedMatch`, read with the concept as its object), an optional note, and
  who certified it and when. A mapping to an IRI the preload does not hold is refused: widening the preload is a platform
  update (S30).
- `policies.scd2[]` and `policies.release_driver[]`: time semantics as policies, `scd2` a table's versions
  (`valid_from`, `valid_to`, `is_current`) and `release_driver` the table that says when a period is frozen and released.
  Their tables and columns are in the lock, one of each kind a table (S28).

**Built (#39):** Meridian's pack 1.1.0 fills these for the Wealth expansion (meta-model 1.1.0), and the pack as written for
1.0.0 still compiles against today's meta-model. The runtime answers a term that resolves to a context from the context's filters, joins and time
rules (`kge/semantics/query.py`), and joins Retail and Wealth only through the certified join.

## 5. Validation

### 5.1 Structural (pre-compile; any failure ⇒ not compilable)
- S00 (v0.3) The manifest names `client`, a client id made of a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, equal to the pack's folder name, and `meta_model_version`, a semver version.
- S01 Every section and nested value has the shape in `kge/compiler/checks.py`'s field tables; YAML is plain and no value is the literal `\N` reserved for artifact NULL.
- S02 Ids are unique per kind. General ids match `^[a-z0-9][a-z0-9_.-]*$`, sources and curated sources match `^[a-z][a-z0-9_-]*$`, and acronyms match `^[A-Z][A-Z0-9_]*$`. Term names are unique; policies share an id namespace, as do filters and row selectors. Term ids are stable.
- S03 `manifest.platform.meta_model` satisfied by the installed meta-model. v0.3: the installed meta-model is of the major version `manifest.meta_model_version` names, at its minor or later, and `schema.meta_model` pins that version too.
- S04 Extensions only add; namespaced `x_<client>_` (v0.3; v0.2 had the pack's id); no collision with meta-model names.
- S05 Every FK names a declared table+column on both ends, same source, compatible types. An uncovered composite key is a finding and stops compilation; uncovered non-composite keys are listed separately as join candidates (§4.13).
- S06 Every `no_join` names two declared, distinct sources; pairs unique (unordered).
- S07 Every mapping hits a declared column and a declared term.
- S08 Every measure: `term` declared; `column` is in the lock and carries no other measure; its read-only scalar aggregate names that column and reads only columns of its table.
- S09 Every measure names a declared calendar and its table has a period binding, for every allowed grain.
- S10 Currency codes are ISO 4217; a conversion's source currency is read from text such as `period_end_rate_from_EUR` and differs from the measure's currency.
- S11 Every alias references the declared target its kind requires; a phrase appears once, whatever its target or case. Predicates and value vocabularies are checked by S29.
- S14 Every role `reads` names declared sources; `writes` ⊆ {terms, mappings}; processes from the meta-model constants or the platform operational list.
- S22 (v0.3) Every restricted column is in the lock and listed once within its policy; separate policies may restrict the same column. Each phrase is for one of its policy's columns, names a word, and is given once.
- S23 (v0.3) Declared joins: tables in the lock; a condition that is a conjunction of read-only conditions on their columns, equating a column of the one with a column of the other, with types that join and no restricted column; a table joined to itself is aliased; a join across two sources is a cross-reference through a curated source's table and implements a declared relation (§4.13).
- S24 (v0.3) Filters and row selectors: a table in the lock, a read-only condition on its columns, no restricted column, conflicts declared both ways.
- S25 (v0.3) Contexts: a declared term, a declared measure on the fact table, declared filters, row selectors and joins; each join follows from the fact or a join before it; each filter's table is one the context always reaches; its golden questions are items of a set the pack names.
- S26 (v0.3) Acronyms, entities, relations, lineage, columns not joinable and ontology concepts name declared terms and entities, and tables and columns in the lock; a column not joinable to a table belongs to a source that has a no-join fact with the table's.
- S27 (v0.3) Certified joins: two declared, distinct sources that are no no-join pair, each pair once, through a declared cross-reference between them; every cross-reference is a certified join's.
- S28 (v0.3) Time policies: an `scd2` policy's and a release driver's table and columns are in the lock, one of each kind a table.
- S29 (v0.3) The rest of the parser's vocabulary (meta-model 1.2): a value vocabulary's and a predicate's column is in the lock and not restricted; a value vocabulary gives values or a pattern the platform knows, not both; a predicate gives a value where its operator takes one; each intent once; each functional mailbox a mailbox's local part, once.
- S30 (issue #57) Ontology mappings (meta-model 1.3): each maps one declared term, entity or restricted-data policy to a concept the platform preloads, by its IRI, with a SKOS relation, certified by a steward on a date; a subject maps to a concept once; and a concept the pack declares itself takes no preloaded concept's id.

**Built (#39):** `kge/compiler/checks.py` runs S00 to S11, S14 and S22 to S30 before `kge/compiler/compile.py` writes anything
(`make check`, `make compile`), lists every finding as `<rule> <path>: <message>` and exits 2 on any. A finding in a
section's shape stops only the checks that read that section. Not checked there: S12, which the loader checks when the
documents land (fail closed: a warning, and no edge); S13 and S15, since the pack names its question sets and does not
restate them (§4.12); and S16 to S21, tiers and readiness (the plan's Phase 4). `tests/unit/test_compiler.py` and
`tests/unit/test_query_semantics.py` hold each check with a pack changed to fail it.

**Not run before compile:** S12's document DEFINES rows are checked when documents land; a missing document produces a
warning and no edge, while `superseded_by` targets and cycles are not checked. S13 and S15 are not run because the pack
names question sets rather than their questions. S16, S17, S18, S19, S20 and S21 are not run; tiers and readiness remain
the plan's Phase 4.

### 5.2 Compile (deterministic)
- C01 Same pack + same lock + same compiler ⇒ byte-identical artifact (sorted rows, canonical YAML, fixed id scheme).
- C02 `reference_sql` executes read-only against the declared sources and returns one scalar or one row; values are frozen into the artifact.
- C03 Every certified measure column passes the platform SQL gate's allowlist check in isolation.

### 5.3 Semantic (post-compile, against the loaded artifact)
- V01 Golden set, the reference agent with the graph on (treatment), ≥ `gates.golden_min_pass` per kind.
- V02 Control margin: treatment beats the same agent with the graph off (control) by ≥ `gates.control_margin` on cross and shelf.
- V03 Held-out set scored and reported, never gated (demo `kge/eval/run_golden.py`).
- V04 Every graph-on answer in every set, including held-out, cites only landed documents that are AI-ready and gold or silver.

- V05 (v0.2) After load: every `Tier` row matches the pack; no Document or Column has `tier` set while `ai_ready = false`; every `ai_certification_tier` tag's `value` equals its `AT_TIER` target. Checked on the runtime overlay too, if one is present.

V05's tier claim is retained pending the owner's answer to [issue #118, Q11](https://github.com/davidpengsun-cloud/fde/issues/118).
The current implementation uses V05 for the onboarding review instead; this draft claim is not an implemented tier check.

The compiler runs S00–S11, S14 and S22–S30, plus C01; it does not run C02 or C03. The artifact has no `status`.
A candidate is a model hash whose graph is `LOADED` but which `current` does not name. Release requires a passed `GATE`
and `SIGNOFF`, unless the hash was released before or release is explicitly ungated.

**Built (#39):** C01: `tests/unit/test_compiler.py` compiles the pack again in another process, from another folder and with
another hash seed, and finds the same bytes and the same model hash. C02 and C03 wait for the questions in the pack
(§4.12). The V rules are not a gate the compiler sets: the demo runs its A/B and the margin check after a load
(`make margin`), and holds the rule presets' golden and held-out results to a snapshot (`make parity`). A loaded graph is
served only once `make release` points the client's `current` at it.

**Built (#40):** the compiler writes the manifest's `gates` into the artifact as `gates.json` (S01 holds each to a kind
of question and a share from 0 to 1), and `make margin` (`kge/eval/margin.py`) checks a loaded graph against its own
artifact's: V01 and V02 on the golden set per kind, against `golden_min_pass` and `control_margin`, with the reference
agent's treatment (graph on) and control (graph off) as the arms; V03 reported, never failing; V04 on every graph-on
answer, held-out included. Its verdict is the `GATE` marker beside the graph's `LOADED`, which a reload drops. A steward
whose role may perform certification signs a passed candidate off (`make sign-off PRINCIPAL=`, the `SIGNOFF` marker), and
`make release` (`kge/graph/release.py`) refuses a hash not both checked and signed off, so a failure leaves a candidate; a
hash released before is released again with no new sign-off. `make graph` and `make demo`, the developer's bootstrap,
release without the gate and say so.

## 6. Conflicts with the current demo structure

Paths relative to `docs/use-cases/kge/demo/` on `origin/develop` @ `01836f6`.

**Domain logic in Python that must become pack data + platform algorithm**
1. *Question parsing is regex logic.* `kge/services/ask/engine/question.py:parse` uses `ALIASES`, `UNIT_WORDS`, `MONTHS`, `FILTER_PATTERNS`, `DEFINITIONAL`, plus inline regexes ("across the bank", "how many", "plus", "which … also"). Vocabulary → `terminology`; the matching algorithm stays platform. Open question: free regex in packs is code by another name, so this draft allows only a closed set of pattern kinds.
2. *Hardcoded "specials".* `Engine.answer` branches on `restricted_lookup`, `key_name_join`, `no_conversion_standard`, `two_measures` and `cross_unit_entity`. These name `retail`/`corporate`, document ids `DD-CORPORATE`/`DD-RETAIL`, and policy ids `POL-DATA-CLASS`/`STD-FX` in literal strings. They become `policies` + `Policy` nodes.
3. *Period semantics live in code.* `period_clause` sniffs column names (`month_start`, `fiscal_quarter`, `as_of`, `*_on` minus `resolved_on/closed_on/end_on`). `fiscal_from_calendar` hardwires an April fiscal year. `sqlguard.PERIOD_COLUMNS` duplicates the list. These become `calendars` + `period_bindings`.
4. *SQL templates and aggregation heuristics.* `plan_unit` picks `count(*)` for BOOLEAN columns or `_id/_no/_ref` suffixes. `Combined.plan` builds cohort CTEs keyed on `UNIT_KEY`. Templates stay platform; aggregation becomes `measures[].aggregation`.
5. *Commensurability heuristic.* `commensurable()` treats `flag`-suffixed columns as different populations and cites `PROC-RECON` inline. This becomes `measures[].population` + `policies.commensurability`.
6. *Readiness derivation.* In `kge/pipeline/tagger.py:derive_readiness`, the classification order, the privacy regexes (`EMAIL_RE`, `DOB_RE`, `NATIONAL_ID_RE`, `CARD_RE`, `HEALTH_RE`) and the citable rule are domain or regulatory policy. The algorithm stays platform; thresholds and detectors move to pack `policies`, from closed enums.
7. *Steward dual-control.* `Graph.steward_decide` (second steward needed to reject `likely_personal`; `ROLE_MAILBOXES` in `core.py`) is a platform workflow. Its trigger list and the mailbox vocabulary are pack data.
8. *Platform imports the domain.* `kge/services/ask/engine/core.py` imports `UNITS` from `estate.spec`, and `combined.py` imports `UNIT_KEY`/`UNIT_LABEL`. Runtime must read these from the compiled model instead.

**Data that sits in the wrong place**
9. *Principals and access.* `kge/estate/spec.py:PRINCIPALS` mixes identities, roles and processes. The split: processes → platform, role templates → pack, identities → deployment.
10. *`writes: mappings` isn't wired.* `kge/graph/load.py:load_principals` only turns `"terms"` into `CAN_WRITE → Term`. No permission covers mappings, and no flow writes them.
11. *`PROPOSES` (Tag → Col) is declared in `kge/graph/schema.py` but never written.* It's kept in the meta-model as reserved for column-term proposals.
12. *Measure metadata is flattened onto `Col`* (`grain/currency/calendar/conversion`, plus `certified`, which duplicates `MAPS_TO.certified`). A `Measure` node is proposed.
13. *`Term` is keyed by `name`*, so a rename breaks every edge. The spec keys it by a stable id.
14. *The synthetic estate and expected values are generated.* `kge/estate/generate.py`, `kge/estate/documents.py` and `kge/estate/golden.py`/`heldout.py` compute expected figures by querying the generated warehouses. In a pack, tests carry `reference_sql` or literals, and the generator becomes a separate fixture tool, not part of the pack. `certified_by`/`certified_on` for mappings are also filled in by `generate.py` (term steward, fixed `2024-11-15`), not taken from the source data.
15. *Harvest vs declare.* `harvest` in `kge/graph/load.py` reads DuckDB's catalogue at load time. To compile deterministically, the harvest must be snapshotted into `pack.lock`.
16. *UI hardcoding.* `ui/index.html` has the `CHIPS` sample questions (Meridian wording), the arm list, the purposes list and the eval kinds. These should come from the manifest/tests via an API endpoint.
17. *Embedding dimension.* `kge/graph/schema.py` imports `DIM` (128) from `kge/adapters/model/embed.py`. It becomes a compile parameter.

**Runtime and operations**
18. *Rebuild discards the runtime layer.* `make graph` reloads from scratch and drops steward decisions (demo README). The runtime overlay (Chunk/Tag/SUPERSEDES/DEFINES-by-steward) must survive a recompile, keyed on stable ids.
19. *Single-process graph store lock* (Kuzu's then, Ladybug's now). The API holds the file lock, so graph, pipeline and test runs need the API stopped (`kge/adapters/graphstore/base.py:GraphLocked`, which Ladybug's store raises; the BigQuery store, `GRAPH_STORE=bigquery`, has no such lock). Loading a new artifact needs a versioned directory plus an atomic swap, or a server store.
20. *Migrations in code.* `load_principals` runs `CREATE … IF NOT EXISTS` for `Process`/`MAY_PERFORM` "for graphs built before processes existed". Versioned artifacts with `meta_model_version` replace ad-hoc migration.
21. *Per-row `CREATE` loading* in `load.py` gets replaced by `COPY FROM` of the emitted CSVs.
22. *The arms are platform, but they're hardwired to feature flags* (`ARMS` in `core.py`: glossary, join_graph, curated_facts, shelves). These map to meta-model layers, so a pack's A/B arms are layer subsets. That fits, but it should be declared in the manifest, not code.

**AI-readiness tiers (v0.2)**
23. *The demo's `ai_readiness` tag carries the three-level chunk value, not yes/no.* `kge/pipeline/state.py:rederive` writes `family: ai_readiness`, `value ∈ {embed-prohibited, retrieval-internal, citable}`, `model: readiness-v2` on one tag per chunk (`{chunk}/readiness`). In the logical view, Tag 1 is yes/no on the Document or Column. That is a name collision under "same tag model, additive only". Proposal: treat the chunk tag as the legacy carrier of `Chunk.gate_level`, and validate Tag 1 only on Document/Column targets. Renaming the chunk family, for example to `gate_level`, would need a demo change and is out of scope.
24. *There's no Tag 2, no Tier and no `redacted` state in the demo.* `derive_readiness` jumps straight from privacy to `embed-prohibited`, so `gate_level = redacted` never occurs, and citability is decided by shelf in the chunk value. Logical view decision 1 maps `citable` and `retrieval-internal` → clear. The demo stays unchanged.
25. *Who writes the new fields.* The demo pipeline (`kge/pipeline/tagger.py`, `kge/pipeline/run.py`) is not modified. Tag 1, Tag 2, `AT_TIER`, `DERIVED_UNDER`, `gate_level` and the denormalised `ai_ready`/`tier` are written by the upstream certification producer (logical view §4.3–§4.5) and confirmed by the steward gate. The demo keeps writing `Chunk.readiness`.
26. *`TAGGED` is Chunk → Tag only today* (`kge/graph/schema.py`). Tag 1 and Tag 2 need Document and Column sources, so it becomes a Kuzu REL GROUP. The demo's Cypher (`MATCH (c:Chunk)-[:TAGGED]->(t:Tag)`) keeps working, but any unlabelled `TAGGED` match would now also see document and column tags.
27. *The steward queue doesn't know Tag 1 or Tag 2.* `Graph.steward_queue` and `steward_decide` accept only `candidate_term` and `privacy` (they raise `ValueError` for other families), and decisions are accept/reject only. The logical view §5 needs confirm, reject, revise down and revise input, with no manual upward revision. That is a platform workflow extension, not pack data.
28. *Tags have no lifecycle fields.* Demo tags have `created_at` but no `effective_from`, `effective_to`, `version`, `policy_version` or `bypass`, and nothing expires or re-derives on a policy change. The lifecycle set is added; existing demo tags would load with `effective_from = created_at` and `version = prompt_version or model`. That backfill is a proposal and not settled.

**Built (#39).** What the demo now does about these:
- 1: the parser's vocabulary is the pack's `terminology` (aliases, value vocabularies, predicates, intents and functional mailboxes), read from the graph; the matching algorithms stay platform, and patterns are a closed platform enum (`kge/compiler/vocabulary.py`).
- 2: left in `kge/services/ask/engine/core.py` for issue #40 (the owner's Question 5 default); see **Built (#40)** below.
- 3: on the graph context `period_clause` reads the pack's calendars and period bindings; the other arms keep the name heuristic, so the control is handed no curated metadata.
- 7: the mailbox vocabulary is the pack's (`functional_mailboxes`); the dual-control trigger is still code.
- 8: nothing in `kge/app.py`, `kge/services/`, `kge/semantics/`, `kge/eval/`, `kge/graph/` or `kge/pipeline/` imports from `kge/estate/`: the engine reads its domain from the graph (`Graph.domain`), one client per process.
- 9: split as §4.10 has it: role templates in the pack, identities in the deployment, processes the platform's.
- 12: `Measure` nodes load from the pack; the measure fields stay on `Col` as well, denormalised, so the runtime's Cypher runs unchanged.
- 13: `Term` has a stable `id` slug beside its name; the graph still keys it by name.
- 15: AI certification lands each warehouse's catalogue (never its rows), `make lock` freezes it into `pack.lock`, and the compile reads only the lock.
- 16: the page's title is the pack's, and its sample questions are the profile's golden set (`GET /golden`, the question text alone).
- 17: the pack's `compile: {embedding_dim}` sizes the DDL, and the loader refuses an artifact whose dimension is not the encoder's.
- 18: steward decisions are journaled in the client's graph store and replayed by every load ([graph-model-spec.md §8](graph-model-spec.md#8-profiles-and-layout)).
- 19: each artifact loads into a graph of its own, in a folder named by its model hash, beside the one served, and `make release` swaps the client's `current` pointer atomically; the store keeps its one-process lock.
- 20: the artifact names its meta-model version, and the loader refuses one it does not load; `load_principals` still creates the process tables for a graph that lacks them.
- 21: the loader copies the compiled CSVs; `kge/graph/legacy.py` keeps the per-row loader as the parity oracle.

**Built (#40).** The specials that named the client read facts the graph already holds (the owner's Question 18 default), with no new pack section:
- 2: a question that says *join* and names a column the pack declares not joinable (`not_joinable`, `NOT_JOINABLE_TO`) is refused on the no-join fact between the column's unit and the table's, and a definitional one cites the two units' data dictionaries, which the graph finds by their units (`Graph.data_dictionaries`); a figure asked in an ISO 4217 code typed in capitals that no certified measure is in, or converts from (S10's `<rate>_from_<currency>`), abstains. Each reason is worded from the graph: no line names `DD-CORPORATE`, `DD-RETAIL` or `STD-FX`, and the no-client-nouns gate, `tests/unit/test_client_nouns.py`, allows no line.
- 5: `commensurable()` no longer cites `PROC-RECON`; the `flag` suffix that marks a population stays code (the owner's Question 19 default).

## 7. Onboarding

A client is onboarded from its pack alone (issue #40). The business team supplies the pack as data, with no Python; the
compiler runs with no human in the loop; and a client is configuration, picked at deployment (the owner's R8): its folder
`clients/<client>/`, its identities in `deploy/<client>/identities.yaml` and its entry in the profiles registry,
`deploy/profiles.yaml`. Each command below runs for one profile (`PROFILE=<client>`), in the demo's folder,
[`demos/kge/`](../../../../demos/kge/README.md).

| # | Step | Who | Run | Done when |
|---:|---|---|---|---|
| 1 | Create the profile | platform team | `clients/<client>/pack.yaml`, its manifest naming `client` (the folder's name) and `meta_model_version`; `deploy/<client>/identities.yaml`; the client's entry in `deploy/profiles.yaml`. The compilable template, [`pack.example.yaml`](pack.example.yaml), gives the sections; [Meridian's pack](../../../../demos/kge/clients/meridian/pack.yaml) is a fuller worked example | `make profile PROFILE=<client>` prints the client's folders, the registry accepted whole; S00 and S03 pass at step 4 |
| 2 | Fill the pack | business team | the sections in the step-2 checklist below, as YAML; for a simulated estate, its files in `clients/<client>/fixture/` (`kge/estate/from_files.py`) | the client's folders hold data alone (YAML, JSON, CSV, Markdown and the lock); every section the compiler requires is there, which `make check` holds at step 4 (S01) |
| 3 | Freeze the sources | platform team | `make estate` (the estate built and certified, a release landed), then `make lock` | `pack.lock` written beside the pack and committed; `make lock` again writes nothing (`"written": false`), and `git status clients/<client>` stays clean |
| 4 | Structural checks | compiler | `make check` | the lock holds what landed, and the pack has no findings (S00 to S11, S14, S22 to S30; §5.1): it prints the artifact's path, not compiled yet, and names the rules it does not check, exit 0. Otherwise it lists each finding, `<rule> <path>: <message>`, exit 2 |
| 5 | Compile | compiler | `make compile` | it prints `build/artifacts/<client>/<model_hash>/`; run again, the same path, "already compiled, unchanged" (C01) |
| 6 | Load a candidate | business-team or steward principal on the page; no principal on the command line | `make load` | the hash's graph is `LOADED` (`build/stores/<client>/<model_hash>/`) and `current` is as it was; `make release` is refused, not through the onboarding gate |
| 7 | Post-load checks | business-team or steward principal on the page; no principal on the command line | `make margin HASH=<model_hash>`, beside the API | against the artifact's `gates.json` (the pack's `manifest.gates`): V01 and V02 per kind, V03 reported, V04, and the no-fall check where the profile names a baseline (else, or with `BASELINE=`, "no-fall check: skipped"); "passed: every post-load check against the pack's gates", exit 0, and its `GATE` says passed. A failure names the kind, the threshold and the questions, exit 1, and leaves a candidate |
| 8 | Sign-off, then release | steward on the page; only sign-off names a principal on the command line | `make sign-off PRINCIPAL=<steward>`, then `make release` | the sign-off names the steward (`SIGNOFF`; a principal whose role may not perform certification is refused); `make release` says "current is now <model_hash>". A page release switches that serving process immediately; each Cloud Run service follows on its next status poll; a CLI release is served after restart. Without a passed `GATE` and its sign-off the release is refused, exit 2, current as it was, unless the hash was released before |
| 9 | A term, measure or alias added later | business team | a pack edit, then steps 4 to 8, `make check` to `make release` (step 3 first, `make lock`, if a source changed). Meridian's AUM is the example: pack 1.3.0, its term, measure and context | the done criteria of steps 4 to 8, with the change in `clients/<client>/` alone: no platform file changes; its questions answered (AUM: W16 and W17 pass) |

**Built (#40).** Fellmoor Regional Transit, an invented regional bus operator, was onboarded through steps 1 to 8 with no
platform change: its pack, lock and fixture in `clients/fellmoor/`, its identities in `deploy/fellmoor/identities.yaml`
and its registry entry, data alone. `tests/unit/test_onboarding.py` takes a copy of it through the steps with the platform's
own commands and serves it, and the demo README's Demo script runs them by hand (its step 9). For step 9, assets under
management (AUM) was added to Meridian's pack later by a pack edit alone (pack 1.3.0: a term, its phrases and acronym, a
measure, a period binding, two declared joins and a context) and released through steps 4 to 8 with no platform change,
which the Demo script's step 6 shows; its step 4 takes a one-line alias edit to Meridian's pack through steps 4 to 8, and
its step 5 a one-line edit that fails step 7 and stays a candidate. `make graph` and `make demo` stay the developer's bootstrap: they compile, load and
release in one go, with no post-load check and no sign-off, and say so ("released without the onboarding gate").

**Built (#55).** For step 9, `make improve` (`kge/improve/flow.py:run_improve`, or `POST /onboard/run` with `step: improve`)
learns from the business units' content, the unmatched question log (`build/stores/<client>/run/unmatched.jsonl`) and the
A/B misses (`team_<unit>` and golden sets, never reading `heldout` or `heldout_<unit>` before `judge`), proposes governed
pack edits with a minor version bump, compiles and loads the candidate in an isolated workspace, runs the A/B and margin
gate beside the live graph, keeps the candidate only when learnable score improves and no question regresses, judges the
held-out sets once, and writes `clients/<client>/proposals/open/proposal.yaml`. A business owner accepts the proposal
items (`python -m kge.estate.onboard accept --proposal open`) and a steward certifies them (`certify --proposal open`), which writes
the certified alias into `clients/<client>/pack.yaml`; `make margin HASH=<candidate_hash>`, `make sign-off` and
`make release` then promote the candidate. Every release writes a version record (`build/artifacts/<client>/versions.jsonl`)
and keeps a pack snapshot (`build/artifacts/<client>/versions/<model_hash>/`, and with `STORE_URL` the store's
`versions/<model_hash>/`, exposed at `GET /versions` and in the Onboard tab's
Versions table), and releasing a previously released hash when the store's pack holds no unreleased edits
(`kge/graph/history.py:check_rollback_allowed`) rolls `current` back and restores its kept `pack.yaml` without a new sign-off
(Demo script step 13).

Any release requires every artifact-named warehouse version and every unit warehouse. A rollback also requires the kept
pack and successful reconstruction of its onboarded units. The hash named by `current` counts as released before even if it has no `RELEASED`
marker. A snapshot is taken only when the store's pack and lock match the artifact, otherwise from a matching candidate.

**The step-2 checklist.** What the business team fills, by the items issue #40 lists, and what holds each:

| Item | In the pack | Held by |
|---|---|---|
| Sources | `sources` (§4.3); `no_join` for a pair with no key between them, and `not_joinable` for a column that invites the join (§4.13) | the lock (step 3); S05, S06, S26 |
| Glossary | `glossary` (§4.4): each term with its steward, status and defining documents | S02 |
| Terminology and aliases | `terminology` (§4.5): aliases, value vocabularies, predicates, intents and functional mailboxes | S11, S29 |
| Calendars | `calendars` and `period_bindings` (§4.6) | S09 |
| Measures: expression, filter, grain, currency, how values combine over time | `measures` (§4.7); a filter only through a context (§4.13, and the rule below) | S08, S09, S10; S25 |
| Column-term mappings | `mappings` (§4.8), certified by whom and when | S07 |
| Policies and tiers | `policies.restricted_attributes` (§4.11). Tiers: none in the pack. Certification grades each document upstream (the owner's R1), and how a tier is cited is the platform's rule | S22 |
| Access roles | `access` (§4.10): role templates, which the deployment's identities are bound to | S14 |
| Golden and held-out questions | the golden set in `clients/<client>/fixture/golden.json` and the held-out set, paraphrases of golden questions, each naming the one it rewords (`paraphrase_of`), in `clients/<client>/fixture/heldout.json` (their items as `kge/eval/sets.py` reads them), each named in `tests.sets` (§4.12), and the golden set's thresholds in `manifest.gates` (§4.1). `kge/estate/from_files.py` copies both into the estate; step 7 holds the golden set to the gates and reports the held-out set, never gating it (V03): Fellmoor's three paraphrases | S01 (the gates); the questions at step 7, since S13 and S15 are not checked (§5.1) |

**What a client's data must meet that no check states**, each the platform as built, and each met by Fellmoor's data:
- *Candidate terms.* A question on a term the glossary does not hold is routed to the steward (`abstain_route_steward`)
  only when the pack names the term, `{phrase, candidate: <Name>}`, and an uncertified document writes the name as two to
  four capitalised words followed by a colon (*Farebox Recovery Ratio:*), the one form the pipeline's rule tagger reads
  as a candidate (`kge/pipeline/tagger.py:CANDIDATE_RE`). Written any other way, the question abstains, routed to no one, and
  a golden question that expects the route fails step 7.
- *Definitional questions.* A question asking for a definition opens with a phrase of the pack's `definitional` intent,
  matched at the question's start (`kge/semantics/vocabulary.py`): Fellmoor's intents add *can journeys* for *Can journeys be
  joined to fleet vehicles on vehicle_ref?*.
- *Filter values.* A value a question filters by is matched as written, capitals and all, after one of its vocabulary's
  `after` phrases where it gives them: *fare class CONCESSION*, *depot RIVERSIDE* (`kge/semantics/vocabulary.py:value_filter`).
- *Refusals and abstentions read from data* (§6, item 2). A question that joins on a key's name is refused on the
  no-join fact when it says *join* and names a column the pack declares `not_joinable`; a figure asked in a currency no
  certified measure is in, or converts from, abstains when the code is written in capitals (*in EUR*).
- *Measures.* A term with no context is planned from its certified column: summed, or counted where the column is
  BOOLEAN or its name ends in `_id`, `_no` or `_ref` (`kge/services/ask/engine/core.py:plan_unit`). A measure's `expression` and
  `aggregation` answer a question only through a context (`kge/semantics/query.py`), so a filtered measure needs one (§4.13),
  and a column summed as a figure is named with none of those endings.
- *Citable documents.* Graph on, the tier rule cites a certified document only with an owner, a version and a
  publication date in its front matter: gold, the authority, when reviewed within 90 days of certification's as-of date,
  silver otherwise; without them it lands bronze, which the tier rule never cites (`kge/estate/certify.py`). A unit's data
  dictionary is the document with `kind: data_dictionary` and `unit: <source id>`, which a definitional question on a
  refused join cites (`Graph.data_dictionaries`).
- *The tagging identity.* The deployment binds `pipeline_tagger` to a role whose processes include `tagging`: the
  pipeline runs as it and stops when it is no tagging identity (`kge/pipeline/run.py`), and `tests/unit/test_client_nouns.py`
  holds every deployment to it.
- *Ids.* The client's id and its sources', roles' and principals' ids are no other client's and no word a platform file
  uses: the no-client-nouns gate (`tests/unit/test_client_nouns.py`) reads them from every pack and deployment and fails on
  any platform line that says one.
- *Folders.* The registry entry puts the client's warehouses in a folder of its own (`../build/<client>/warehouses`, as
  Thornbury's and Fellmoor's are): the registry is checked whole, and a profile's folder shared with or nested in another
  root refuses the registry, which stops every profile (`kge/pipeline/profile.py`).
- *A simulated estate is a fixture.* `make estate` builds the client's estate from its fixture only when
  `clients/<client>/fixture/schema.yaml` is there; otherwise it runs the demo's own generator, `kge/estate/generate.py`,
  which builds Meridian's estate (`kge/estate/spec.py` reads Meridian's pack), as the Makefile's `ESTATE_FROM` chooses.
