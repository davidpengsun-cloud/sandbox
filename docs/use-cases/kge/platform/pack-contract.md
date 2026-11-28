# Pack Contract — draft v0.4

The compiled pack's discovery metadata crosses into portals through the separate, versioned [catalogue provider contract](catalogue-contract.md).

Changelog: **Issue #699 (26 Nov 2026)** has onboarding warn where a pack names question sets and declares no `tests.showcase` (§4.12); no pack field changes. **Issue #620 (21 Nov 2026)** adds `tests.showcase`, the questions the portal offers first, by id from a set `tests.sets` names (§4.12), and its check S38 (§5.1); `GET /golden` serves them, and no platform or portal code names a question, a period or a category for it. **Issue #515 (21 Nov 2026)**, B2.2 of #450, has the question lexer set the markers `from_to`, `asks_one` and `measure_part`, the last on a predicate the pack flags `part: true` (§4.5, §4.14), and S33 and the preload refuse `retired` for a shape that replaces `planshapes.compose:ranking` or `planshapes.parts`, the coded builders no marker reproduces (§5.1); no pack field changes. **Issue #514 (21 Nov 2026)**, B2.1 of #450, freezes the coded phrase table as baseline v1 in the preload, version 1.3.0: 364 rows, each with `baseline: true`, which every graph loads as `Phrase` rows and `MARKS` edges and which the question lexer reads (§4.14). **Issue #513 (19 Nov 2026)**, B1.6 of #449, adds F13, a term's codes and `broader` edges checked against the rows, at upload and again at onboarding's compile step (§4.4, §5.1). **Issue #512 (19 Nov 2026)**, B1.5 of #449, adds S37, the structure of a term's `is_a`, `broader` and `codes` (§5.1). **Issue #511 (19 Nov 2026)**, B1.4 of #449, adds a term's `is_a`, `broader` and `codes` (§4.4), checked by S01 and compiled as `IS_A`, `BROADER` and `CODED_AS`, and `ON_COLUMN` from each value, predicate and code/name-pair vocabulary entry to its column. **Issue #510 (19 Nov 2026)**, B1.3 of #449, tightens S33 as the owner's F9 to F11 state: a `before` constraint in reverse order separates two shapes when both require both its operands (F9), a shape cites each test item once (F10), and no two shapes, pack or preload, replace one coded builder (F11) (§4.14, §5.1). S34 stays a schema check, and what a plan means is V06's, after load. It also refuses a phrase with a leading or trailing space (S32), a `one_of` whose every name the shape forbids, and a slot reference beyond the slot's count, and an unknown block no longer adds an unused-slot finding (S33). **Issue #509 (19 Nov 2026)**, B1.2 of #449, adds the up-front check F12 on the set items a shape cites (§4.14, §5.1), the approval fields `reference_sql_approved_by` and `reference_sql_approved_on` on a set item (§4.12), and the preload's minimum of alternate phrasings, version 1.2.0 (§4.14). **Issue #508 (18 Nov 2026)**, B1.1 of #449, adds meta-model 1.6.0's optional question-shape sections, `phrases`, `shapes`, `recipes`, `clarifications`, `shape_tests` and `examples`, and an optional `part: true` on a predicate (§4.5, §4.14), their checks S32 to S36 (§5.1), and the platform's preload in format 2 (§4.14). **Issue #117 (24 Oct 2026)** makes every client id a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, so that each of its service accounts' ids (`kge-<client>-svc`, `-job` and `-improve`) fits Google's 30 characters. **Issue #55 (18 Oct 2026)** adds per-unit learnable (`team_<unit>`) and held-out (`heldout_<unit>`) question sets (§4.12), the LangGraph improvement job (`kge/improve/flow.py`, `make improve`) and its governed proposal lifecycle (`clients/<client>/proposals/open/proposal.yaml`), and version snapshots with rollback (`build/artifacts/<client>/versions/<model_hash>/`, `GET /versions`, §7); lines marked **Built (#55)** state what the demo now does. v0.4 (issue #40, 16 Oct 2026) adds §7, onboarding: the checklist a new client goes through, steps 1 to 9, each with the command that runs it and what shows it done, and the rules a client's data must meet that no check states, found onboarding a client from scratch. A line marked **Built (#40)** says what the demo now does. v0.3 (issue #39, 14 Oct 2026) requires `client` and `meta_model_version` in the manifest, keeps a pack in `clients/<client>/`, namespaces extensions by the client (`x_<client>_`), and adds the checks for the client id and meta-model compatibility (S00, S03), the optional query-semantics sections of meta-model 1.1 with their checks (§4.13, S23–S28), certified joins, the rest of the parser's vocabulary (S29) and identities kept in deployment (§4.10). A line marked **Built (#39)** says what the demo's compiler now does. The onboarding section is issue #40's. v0.2 adds `policies.tiers` and `policies.readiness` (the AI-readiness tier design, same tag model, additive only), validation rules S16–S21 and V05, and conflicts 23–28. v0.1 was the first draft.

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
It derives columns and types, certified term mappings, table lineage, dates, governance status and
deployment ownership from the pack, lock, compiled model and identities. Row counts and storage sizes come from the
released warehouse, once per release; they are absent from the schema-only lock and compiled catalogue. An optional `catalogue.yaml` adds descriptive
text, badges, hierarchy names, baseline activity and ratings, and business-facing field names by product id. It cannot
name an unknown table, column or principal. A pack without it still releases a complete catalogue and the portal works
from onboarding alone.

**Use cases (#495, #599).** Use cases say what assets are used for. Since #599 each is defined once, in a top-level
`use_cases` list in `catalogue.yaml`, and a product lists the ids of its use cases under its own `use_cases`, in the
order it shows them. A definition has these keys and no others; `title`, `objective` and `url` are the fields of the
Open Data Product Specification's `product.details.<language>.useCases[].useCase` (ODPS v4.0) under the catalogue's own names:

| `catalogue.yaml` | ODPS v4.0 `useCase` | Required | Rule |
|---|---|---|---|
| `id` | | yes | non-empty text with no spaces, defined once in the list |
| `title` | `useCaseTitle` | yes | non-empty text |
| `objective` | `useCaseDescription` | yes | non-empty text, one or two lines |
| `domain`, `owner` | | no | non-empty text; the owner is a role |
| `url` | `useCaseURL` | no | an address written `https://`, with a host and no space |
| `kpi` | | no | `name` (required), `definition`, `grain`, `unit`, each non-empty text |
| `example_questions` | | no | a list of non-empty text |

`validate` in `kge/compiler/catalogue.py` refuses a definition that breaks a rule, an id defined twice
(`catalogue.yaml:use_cases[2].id: uc-x is defined twice`), a product that lists an id the list does not define
(`catalogue.yaml:products[3].use_cases[0]: retail.accounts names unknown use case uc-x`) and a product that lists an id
twice (`... lists use case uc-x twice`). A product may still give an entry inline in the #495 shape (`title`,
`description`, optional `url`, checked by #495's rules); for one release that is a warning, not a finding
(`... gives a use case inline; define it under use_cases and list its id (inline entries are accepted for one release)`).
An inline entry is refused as listed twice when the product also lists its id, before or after it, or gives the same
title inline again; two different inline titles whose ids are alike (`A/B` and `a b`) are both accepted, and the portal
shows each as its own card.
More than three entries on a product is a warning too (`warnings` there): the pack check (`check-pack`, onboarding's
input check and `compile`) prints both and passes; an asset's Use cases tab shows every one, in the order declared. `resolve` turns a product's list into resolved objects (`id`, `title`,
`objective` and whichever optional keys the definition gives, an absent one omitted), and `use_case_entry` turns an
inline or released #495 entry into one: its id is `uc-` and its title in lower case with each run of other characters a
hyphen (a title with no letter a to z or digit: its characters' code points in hex, joined by hyphens), its objective
its description. `derive` and the portal publish's snapshot resolve, so the compiled catalogue,
the published cards and the [catalogue contract](catalogue-contract.md) carry resolved objects per product and never the
top-level list, and the catalogue service resolves a release or card made in the #495 shape as it reads it.
`use_cases` is one of the `DESCRIPTIVE` fields, so it is compiled into the release's catalogue only where a product
declares it (a pack without it compiles as before), publishes with the release or `onboard portal`, and reaches the
portal at its next catalogue refresh, which stores it as JSON text in its products table. The portal shows it as text
and does not edit it.

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
| `count_metrics` (issue #474) | O | distinct counts of declared entity keys and context populations (§4.7) |
| `derived_measures` (issue #474) | O | named formulas over declared physical measures or count metrics (§4.7), compiled through typed arithmetic |
| `banded_dimensions` (issue #474) | O | labelled numeric ranges declared in the pack (§4.5), compiled as `CASE WHEN` |
| `mappings` | R | `kge/estate/spec.py` `CERTIFIED_MAPPINGS` |
| `policies` | O | restricted attributes and the `scd2`, `release_driver` and `partitioned` time policies |
| `access` | O | `kge/estate/spec.py` `PRINCIPALS`, `PROCESSES`, `PURPOSES` (split, see §4.10) |
| `tests` | R | names versioned question sets as `{name, version, items?}`; their items are build files read by `kge/eval/sets.py`. Its optional `showcase` (issue #620), `{set, items}`, the questions the portal offers first, replaces the golden-20 category `GET /golden` matched in code (§4.12) |
| `acronyms`, `entities`, `relations`, `ontology`, `lineage`, `filters`, `row_selectors`, `joins`, `contexts` (v0.3) | O | nothing at `01836f6`: the query-semantics layer of meta-model 1.1 (§4.13) |
| `ontology_mappings` (issue #57) | O | nothing: mappings to the platform's preloaded industry standards, meta-model 1.3 (§4.13) |
| `runtimes`, `default_runtimes` (issue #347) | O | nothing: external runtime connection and table/column mappings against the reference lock, and the default ordered list of runtime ids, meta-model 1.5 (§4.13) |
| `phrases` (issue #508) | O | the static operation phrases of `kge/semantics/coverage.py` (`OPERATORS`), which stay platform code for the coded rules until B7; since preload 1.3.0 they are baseline v1 in the graph, which the question lexer reads: a phrase that marks one of the platform's operations, or filler, meta-model 1.6 (§4.14) |
| `shapes` (issue #508) | O | the plan-building branches of `kge/services/ask/engine/planshapes.py`, which stay code in 1.6.0: a question template over the platform's slot kinds and operations, meta-model 1.6 (§4.14) |
| `recipes` (issue #508) | O | nothing: a named composition of the platform's building blocks (`kge/semantics/blocks.py`) that a shape contributes, meta-model 1.6 (§4.14) |
| `clarifications` (issue #508) | O | the clarification branches of the coded planner: a rule over the parsed question naming a platform clarification text, meta-model 1.6 (§4.14) |
| `shape_tests` (issue #508) | O | nothing: a shape's test items, from a set `tests.sets` names, meta-model 1.6 (§4.14) |
| `examples` (issue #508) | O | nothing: a question and the `query-plan/v1` plan a person approved for it, meta-model 1.6 (§4.14) |

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
`meta_model_version: 1.4.0`, `platform: {meta_model: "^1.4", compiler: ">=0.4"}`, and
`schema: {meta_model: "1.4"}`; the compiled artifact's manifest names
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
(`columns`, `ref_table`, `ref_columns`). `onboard` refreshes this schema lock; `make lock` remains for local use. The compiler reads
no landing or warehouse and compiles from the pack and lock.
`no_join[]`: curated assertions of **absent** joins between sources
(`between: [a, b], asserted_by, asserted_on, note`). Never inferred; absence
from a re-harvest is not falsity (demo comment on `NO_JOIN_FACTS`).
`certified_joins[]` (v0.3): a pair that does join, through a certified cross-reference, has a certified join fact
(`between: [a, b], join, asserted_by, asserted_on, note`) in place of its no-join fact, which it replaces, never
deletes silently; the pair has no `no_join` entry (S27, §4.13). **Built (#39):** Meridian's retail-wealth pair, the
owner's Option B; the other five pairs keep their no-join facts. The lock, `pack.lock`, freezes each harvested source's
schema before compilation (`kge/compiler/lock.py`); serving never refreshes the lock.

### 4.4 glossary
The section is a list of terms, each with `id` (stable slug — demo keys `Term` by **name**, which breaks on
rename), `name`, `domain`, `steward`, `definition`, `status: certified|candidate`,
`defined_by[]` (document ids), `version`, `effective_from`.

Since meta-model 1.6 (issue #511), a term may also declare business terms, codes and hierarchy, which compile into edges
over existing types, never into new nodes
([graph-model-spec.md §8](graph-model-spec.md#8-profiles-and-layout)):

- `is_a[]`: the glossary ids of the classes the term is a member of ("North is a line"), each
  once; compiled as `IS_A`, member to class.
- `broader[]`: the glossary ids of the terms it sits under, each once; compiled as `BROADER`, narrower to broader, whose
  direction gives the order of levels.
- `codes[]`: `{column, code}`, how the data identifies the term: `column` as `<source>.<table>.<column>` in the lock,
  `code` as quoted text, each pair once; compiled as `CODED_AS` (Term → Column, with its `code`). Several codes, on one
  column or several, are alternatives (OR); a composite code is out of scope. A member with `is_a` and no `codes` is
  legal.

```yaml
glossary:
  - {id: line, name: Line, ...}
  - {id: north, name: North (line), ..., is_a: [line], codes: [{column: ops.unit.level_2, code: "N1"}]}
  - {id: north-east, name: North East (area), ..., broader: [north], codes: [{column: ops.unit.level_3, code: "N1E"}]}
```

`is_a` and `broader` take term ids, since the graph keys a term by its name, which is unique across the pack: qualify a
member's name, such as "Core (segment)". All three are lists, a scalar is refused, and none is allowed on a pack
written for a meta-model before 1.6.0 (S01). Whether each reference resolves, and the rest of their structure, is S37's (§5.1);
whether the rows agree with them is F13's (§5.1), on upload and again when onboarding compiles. Member terms
are read at run time like any term, so a client pack declares none in B1: they are exercised by planted test packs only.
Every value, predicate and code/name-pair vocabulary entry (§4.5) compiles into `ON_COLUMN` to its column, with its
role (`value`, `predicate`, `code` or `name`).

### 4.5 terminology
Pure vocabulary, consumed by the platform parser:
- `aliases[]`: each has `phrase` and exactly one of `term`, `source`, `candidate`, `group_by`, `entity`, `time_mode`, `period`, `column` or `column_wording`. Since meta-model 1.4.0, `sources` is an alternative: a non-empty list of distinct declared source ids, exclusive with every single-target kind. Its phrase names that complete set, not a choice among its members. Meridian pack 1.7.0 declares *bank*, *group* and *enterprise* as its four sources. Packs written for 1.3.0 and earlier supported versions load unchanged; authoring `sources` requires `manifest.meta_model_version: 1.4.0` or later.
- `concepts[]`: `{id, wordings[], <target>, qualified_terms?: [{noun, term}]}` declares a concept's target once, with every wording that names it. The target is exactly one of the alias target kinds above. The compiler emits the wordings as ordinary aliases; no parser change is needed for another concept or wording. Optional `qualified_terms` names a noun and its declared glossary term once: every `<wording> <noun>` becomes an alias for that term. Nouns must be nonempty, targets must be declared, and every generated phrase obeys the same collision checks as direct aliases. Concept ids are unique, and a wording may appear only once across concepts and aliases, ignoring case and surrounding whitespace. Check-pack refuses repeated wordings within one concept and wordings assigned to different concepts. A pack-generated equivalence test asks every wording in postfix, prefix and separated-prefix templates and compares the plan and figures.
- `value_vocabularies[]`: `{column, values[], whole?}` or `{column, pattern, prefix?, after?, before?}`. `pattern` is one of `upper_2`, `upper_2_3`, `upper_4_plus` or `upper_letter`. `whole` labels a measure's unsplit figure: a question that splits a measure by two or more of the column's values (inflows and outflows) is answered with each part and the whole under this label (Meridian's flow direction declares `Net`). It needs listed values and a non-empty label.
- `code_name_pairs[]`: `{code, name, members: [{code, name}]}` binds a code column, a name column and their declared members. A bare member name resolves to its code only when the member and readable code value are unique. An explicit name-column selector retains its literal name filter; ambiguous or unreadable mappings grant no code read.
- An alias with `column_wording: source.table.column` references that checked column without changing its displayed name. Use it for another wording of an existing measure's amount, rather than `column`, which supplies a display name too. It grants no additional column access or metric population. Concept wordings can target `column_wording` in the same way.
- `predicates[]`: `{phrases[], with?, column, op, value?, values?}` e.g. "unresolved" ⇒ `complaint.resolved_on IS NULL`. `op` is one of `eq`, `ne`, `gt`, `ge`, `lt`, `le`, `is_null`, `is_not_null`, `is_true`, `in`, `not_in`, `contains` or `starts_with`. `eq`, `ne`, `gt`, `ge`, `lt` and `le` take one text or numeric `value` compatible with the column. `is_null`, `is_not_null` and `is_true` take neither `value` nor `values`. Since meta-model 1.6, a predicate may carry `part: true`, flagging the phrase as a part of a measure, such as inflows and outflows, for the lexer's `measure_part` marker (§4.14), which the lexer sets since #515 (B2.2 of #450); no planner reads it yet, and S29 refuses it on an earlier pack.
- `contains` and `starts_with` take one nonblank text `value` on a text column. Matching is case sensitive and literal: `contains` matches anywhere, `starts_with` only at the beginning, and neither matches null. `check-pack` refuses nontext or empty values and LIKE wildcards `%` and `_`. For example, `{phrases: [initial labels], column: ledger.events.label, op: starts_with, value: Al}` declares that phrase's exact column, operator and literal. Quoting and escape characters are preserved by the compiler. The plan checks and SQL gate refuse a different operator, value or column.
- `in` and `not_in` name a group of values under one phrase with `values[]`: two or more, all text or all numbers, each once, and no `value`; no other operator takes `values`. For example, `{phrases: [coastal region, coastal regions], column: wealth.BRCH_DIM_1.RGN_CD, op: in, values: ['NORTHEAST', 'PACIFIC']}` makes "NNA for Jan 2025 for coastal region" read `RGN_CD IN ('NORTHEAST', 'PACIFIC')`. The same declaration with `phrases: [outside coastal region]` and `op: not_in` excludes those codes. An exclusion keeps rows with no value: `NOT (RGN_CD IN ('NORTHEAST', 'PACIFIC')) OR RGN_CD IS NULL`; the answer says those rows are kept. Adding another group is a pack change followed by onboarding; the scratch onboarding acceptance test exercises that path to a checked candidate.
- A question may also name two or more of one column's declared values, joined by "and", "or" or commas, with the filter's noun once (singular or plural) or with each value: "Northeast and Pacific regions", "Northeast or Pacific region" and "Northeast region and Pacific region" each read as one `IN` on that column. The noun is the words the column's `eq` phrases share before or after the words that name a value (`NORTHEAST region`, `PACIFIC region`: *region*). A list that reads either way ("branch BOS and NYC branch") is not merged at all. "Excluding", "except", "other than" or "not in" followed by one or more declared values excludes them with the same null-preserving filter as `not_in`; lists use the same joining words. Words inside a declared name belong to that name, so a measure named with "excluding" does not add an exclusion.
- Values of different columns stay separate filters. Two declared filters joined by "or" read as one `any` junction, keeping rows matching either filter once. "And", or no joining word, requires all the filters. Mixed "and" and "or" without clear grouping asks the person to clarify the grouping.
- `intents[]`: `{phrases[], intent: definitional|count|avg}` replacing `DEFINITIONAL` and the "how many/average" regexes.
- `functional_mailboxes[]` (demo `ROLE_MAILBOXES`).
Regexes are **not** allowed; patterns are a closed platform enum.

Meridian's reporting-basis wordings select the declared time mode of the named metric. Its finance NNA contexts read
`F_NNA_FIN` by `DW_TRAN_MNTH_END_DT`, without an `FRZN_IN` filter, matching the approved golden SQL. The words
"certified" and "frozen" do not add a finance-row filter. Current and restated select the counterpart time mode.
The reporting concept declares `qualified_terms: [{noun: NNA, term: certified-nna}]`, so every prefix such as
"published NNA" names the finance metric. Postfix "NNA … as reported" selects transaction NNA's reported mode,
preserving the approved W09 transaction reference SQL. These metric populations remain distinct.
**Built (#39):** Meridian's pack 1.2.0 fills every slot, and since meta-model 1.2.0 the runtime reads them from the
graph (`Alias` and `Vocabulary` nodes) with the matching algorithms unchanged, so no platform file holds the client's
phrases, values or mailboxes. S11 checks the aliases, S29 the rest.

Top-level `banded_dimensions[]` declares labelled numeric ranges. A question such as "Revenue by amount band"
groups by the declared label:

```yaml
banded_dimensions:
  - id: amount_bands
    label: amount band
    column: ledger.events.amount
    ranges:
      - {label: Small, min: null, max: 1000}
      - {label: Medium, min: 1000, max: 10000}
      - {label: Large, min: 10000, max: null}
```

Each range includes `min` and excludes `max`; null means an unbounded endpoint. The first minimum and final maximum
must be null, so the declaration covers all numeric values. Inner endpoints are finite numbers in ascending order,
with each minimum equal to the previous maximum. Labels are nonblank and unique without regard to case.
`check-pack` refuses a nonnumeric or restricted column, gaps, overlaps, unsorted ranges and duplicate labels.
Null source values retain a null grouping label. The compiler uses a `CASE WHEN` over the declared column; plans
refer to the declaration by id and the SQL gate refuses changed boundaries or labels. Adding a band or a text
filter instance requires only pack data and onboarding.

### 4.6 calendars
`calendars[]`: `{id, year_start_month, quarter_label, spine?, current_period?}`, where `quarter_label` contains `{n}`.
`spine` declares a complete day calendar as `{table: source.table, column: date_column, attributes?: {...}, recurrences?: [...]}`.
The date column must be the table's single-column primary key and have type `DATE`. Finite requested ranges derive
day, week, month, quarter and year spines from its days; a partial month table is not a substitute. Optional `attributes` maps attribute ids, such as `week_of_month`,
`month_of_year` or `year_quarter`, to fully qualified text or numeric `source.table.column` names in the
spine's source. Calendar attributes may
live on another table in that source, while `spine.table` remains the complete day spine. `check-pack` checks the
references, types and date key. Adding an attribute needs pack data and onboarding.
The spine applies to its declared source; another source sharing the calendar retains its existing period generation.

Optional `spine.recurrences[]` declares recurring values as `{attribute, grain, cycle, values: [{value, phrases: [...]}]}`.
The attribute names an entry in `spine.attributes`; `grain` is its observation grain and `cycle` is the coarser time
breakdown. Both use the plan's grains (`day`, `week`, `month`, `calendar_quarter`, `fiscal_quarter`, `year`, `fiscal_year`).
For example, `{attribute: month_of_year, grain: month, cycle: year, values: [{value: 6, phrases: [June, Jun]}]}`
lets “every June, 2023 to 2025” apply the declared month value and group by year. A declaration of
`week_of_month`, with `grain: week`, `cycle: month`, and `{value: 2, phrases: [week 2]}`, lets “week 2 of each month”
filter the calendar's week index and group by month. These are ordinary declared column filters and time dimensions;
each context must declare its path to the attribute table. Attribute values match the column's scalar type, and each
attribute, value and wording is declared once. A month-grain measure declines a recurring week filter. A named range
still requires its observations to be released; Ask does not silently omit unavailable years.

`current_period` is an explicit `YYYY-MM-DD` anchor declared by the pack. A relative request uses its stated period
first, then this declared anchor; without either it asks for a period. It never reads the machine's current date.
For example, `current_period: "2025-07-15"` describes data booked through that day. Fiscal periods derive from
`year_start_month` and `quarter_label`, with no fiscal-year or fiscal-quarter column required. ISO week-year derives
from the spine date, never from a calendar-year attribute: 30 December 2024 belongs to ISO week 1 of 2025.

Month ranges ("January through September 2025", "Jan-Sep 2025"), quarter ranges ("Q1 through Q3 2025",
"first three quarters of 2025") and year, quarter or month to date use those same calendars. A breakdown, such as
"by month", stays within the requested range. A context's optional `ytd_calendar` supplies the year start for plain
year to date; an explicitly fiscal request uses the declared fiscal calendar. This can differ from a measure's
calendar metadata, so keep the context's business definition when authoring it.
A to-date cutoff includes its final day; month and quarter to date start in the month or quarter containing that day.
The declared current period does not make an open month released.

`period_bindings[]` per table: which column carries the period and its kind
(`month_start` ⇒ month, `fiscal_quarter` ⇒ fiscal quarter, `as_of` ⇒ snapshot,
`*_on` ⇒ event date) plus the calendar join table if any. Replaces the
column-name sniffing in `period_clause` and `PERIOD_COLUMNS`.
**Built (#39):** `period_bindings` is a top-level section, `{table, column, kind, calendar, rollup?: {table, column}}`
(S09), and the runtime reads it on the graph context only; the other arms keep the name heuristic, so the control is
handed no curated metadata.

Optional `week_start` is one of `monday`, `tuesday`, `wednesday`, `thursday`, `friday`, `saturday` or `sunday`;
omission means Monday. Optional `spine: {table, column}` names the calendar's day table and its DATE column:

```yaml
calendars:
  - id: calendar
    year_start_month: 1
    quarter_label: "{yyyy}-Q{n}"
    week_start: monday
    spine: {table: ledger.calendar_day, column: day}
```

The spine's column must be the table's complete primary key in the lock. Populate one row for every day across the
supported range; `check-pack` verifies the table, DATE type and key. Day, week, month, calendar quarter/year and fiscal
quarter/year breakdowns derive their keys from that date and the declared calendar. The spine includes every period
in a bounded requested range; an unbounded breakdown spans its first through last observed periods, including gaps.
"By week" and "weekly" return week-start dates. Monday weeks follow ISO week boundaries, so derive the week-year
from the date: 30 December 2024 belongs to ISO week 1 of 2025. Never pair a week number with the date's calendar year.
A measure with month-grain observations cannot be broken down by day or week. Calendar content changes require onboarding
and `check-pack`; they introduce no new setting or image change.

### 4.7 measures
`measures[]`: `id`, `term` (glossary id), `column` (`source.table.column` in the lock), `expression` (one read-only
aggregate over columns of that column's table, e.g. `sum(monthly_revenue.total_revenue_usd)`),
`aggregation: sum|count|count_distinct|avg|min|max|median|ratio|last_snapshot`, `grain`, `currency`, `calendar`, and `conversion`
(`none` or text such as `period_end_rate_from_EUR`), plus optional `snapshot: bool`,
`population` (for count measures: which entity is counted; demo derives
"different populations" from a `flag` suffix in `commensurable`).

Optional `empty: zero` or `empty: 'null'` controls a period with no observations; quote `'null'` so YAML retains text.
The row remains present. Without this field, existing behaviour applies: a bounded released additive measure shows
zero; other missing observations remain null. An explicit null stays unknown in cumulative or moving calculations.

Optional `aggregations` lists the question-selectable operations: `sum`, `count`, `avg`, `min`, `max` and `median`.
When absent, only the measure's existing `aggregation` is allowed. A supplied list is nonempty, contains no duplicates,
and includes that default. Alternatives reuse the declared aggregate's row expression and optional `FILTER`; compound
or multiple-aggregate expressions, `DISTINCT` and `*` cannot declare alternate operations. `check-pack` reports those
declarations before compilation. "Average" or "mean", "minimum", "lowest" or "smallest", "maximum", "highest" or
"largest", and "median" select the corresponding declared operation; an undeclared choice is refused. A breakdown
retains the existing ranking meaning of "highest" and "lowest". Median selects the exact central observations and
interpolates as `FLOAT64`, with the same numeric precision as average.
Aggregation words apply to their named output. A question can combine a measure with declared entity counts in either
order; the measure retains its declared aggregation, while each count uses its declared entity's complete key and
population. A question-wide aggregation is only the default for outputs that name none. Applicable question filters
remain on both outputs; a count metric's local filters apply only to that count. A declared count measure remains one output.
A leading entity count can name a measure as its population; coordinating the count and measure asks for both outputs.

Optional `derived_measures[]` declares `{id, term, formula: {kind, numerator, denominator}}`. `term` names a glossary
term; the id is unique across physical measures, count metrics and derived measures. `kind` is `ratio`, `difference`,
`percent_change` or `share`, the closed arithmetic operations of the plan language. Each operand names a declared physical measure or count metric id; nested formulas
are refused. For example, with the glossary term and both physical inputs already declared:

```yaml
derived_measures:
  - id: ledger.net_income
    term: net_income
    formula: {kind: difference, numerator: ledger.income, denominator: ledger.cost}
```

Asking for the term expands its exact formula, retaining each input's declared aggregation, filters and time rules.
Inputs must resolve to compatible populations and currencies and an unambiguous context; otherwise the answer gives
the reason or asks which context is intended. A model cannot replace a declared operand or operation. Ratio and percent
change return null with `zero_denominator` when the denominator is zero. A missing input remains null.

`moving_average_lengths` declares the measure's allowed window lengths as distinct positive integers
in periods, for example `moving_average_lengths: [2, 3]`. Without it, moving averages are refused; lengths absent
from the declaration are also refused. For example, "3-month moving average of
revenue by month" averages the current month and up to two preceding months within the requested period. Another
breakdown restarts the window per value. A missing contributing observation makes that window unknown.
The compiler emits this policy separately from terminology, preserving existing value identifiers.
"Top 3 branches by revenue in each region" ranks the declared branch dimension within every declared region,
with a deterministic dimension tie break and at most three rows per region.

A formula may bind an input population explicitly with `contexts: {numerator: income_context, denominator: cost_context}`
beside `formula`. Each context must declare the corresponding physical input measure. A count operand keeps its
count metric's declared population. For a share, both context bindings name the same population. Bound populations
are used even when the person asks only for the metric's name; a model cannot substitute another context. Leave the
bindings out only when the input populations are unambiguous.

A distinct entity count is a declared metric in `count_metrics`. Give it a unique `id`, a glossary `term`, and an
`entity` whose complete key is declared. An optional `context` fixes the counted population to that context's fact,
filters, joins and version rules; without it, the population is the entity's own table. Optional `filters` apply to
this count output only, each with a qualified `column`, `op` and comparison `value`. The closed operators are `eq`,
`ne`, `gt`, `ge`, `lt`, `le`, `contains`, `starts_with`, `is_null` and `is_not_null`; null tests take no value.
Text matches require a text column and a literal under the same rules as terminology predicates. A count
declaration grants its local text filters only to that count output. Local filters read the entity's table and
cannot read restricted columns. For example:

```yaml
count_metrics:
  - {id: branch_household_count, term: branch_household_count, context: branch_sales, entity: account_household,
     filters: [{column: ledger.account.household_id, op: gt, value: 0}]}
```

A count needs its declared metric even when the question names the entity rather than the metric's glossary name.
An undeclared count or an ambiguous count population is refused with a reason. Sum and count outputs keep separate
aggregations; asking for one cannot change another output's operation.

A share is declared in `derived_measures` with `formula: {kind: share, numerator: ledger.income, denominator: ledger.income}`.
Both operands name the same metric: the denominator removes the requested breakdown and retains the filters. A ratio,
share, difference or percent change can also name a declared count metric as an operand. Structural share and ratio
wordings select those declarations; they cannot invent a metric that the pack lacks. The count and share wordings,
like all other business wordings, belong in the pack and reach the candidate through onboarding.

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
The compiler accepts four policy lists. Their ids share one namespace:
- `restricted_attributes[]`: `{id, action: refuse, cite, columns[], version?, phrases?: [{column, phrases[]}]}`. A
  restricted column is refused by the SQL gate; each phrase belongs to one of that policy's columns (S22).
- `scd2[]`: `{id, table, valid_from, valid_to, is_current, valid_to_inclusive, version?}`.
- `release_driver[]`: `{id, table, period, release_date, freeze_date, released, version?}`.
- `partitioned[]` (meta-model 1.5): `{id, table, column, version?}`. The column is in `pack.lock` and is a `date` or `timestamp`, one per table (S28).

The other policy kinds proposed in earlier drafts are not pack fields; S01 refuses them.

### 4.12 tests
The pack's `tests.sets` names the versioned sets it is scored on (`{name, version, items?}`:
golden v2, heldout v1, golden_wealth v1) without restating them, so the A/B baselines stay comparable; restating them
in the pack is not supported. The set items are files in the build. A context's golden questions are named by
id from a set listed here (S25), and so are a shape's tests, `shape_tests[].tests` (S33, §4.14): the sets stay outside the pack.
Since issue #509 a set item may record that a person approved its `reference_sql`: `reference_sql_approved_by`, who, and
`reference_sql_approved_on`, the date (`YYYY-MM-DD`), both or neither, on the item, its template or the suite's defaults.
`kge/eval/sets.py` reads them and passes them through to the item as written; scoring never reads them. They are fields
of the set file, not of the pack: the sets stay outside the pack, and F12 reads them for the items a shape cites (§4.14). **Built (#55):** an onboarded unit may supply `clients/<client>/units/<unit>/questions.json`
(`team_<unit>_v1`, learnable by `kge/improve/gather.py`) and `clients/<client>/units/<unit>/heldout.json` (`heldout_<unit>_v1`,
never read by `kge/improve/gather.py`, scored during A/B and reported alongside `heldout_v1` in V03 without gating).

Since issue #620 the pack may declare its showcase questions, `tests.showcase: {set, items}`: the questions the portal
offers first (Cerebro's "Try one of these"), by id from a set `tests.sets` names and lists the items of, no held-out
set, each item once, in the order the portal shows them (S38). Each environment's pack names its own, from a set on its
own data's periods: Meridian's names sage v4's S06 to S25, the owner's golden 20 on the sample. `GET /golden` serves the
questions of those items under that set's `golden20`, where the set's file is at the version `tests.sets` names, and
leaves out any the person may not read; every other set's `golden20` is empty, and so is every set's where the pack
declares none: KGE serves no showcase the pack does not declare. Since issue #699 onboarding's up-front check warns, and
refuses nothing, where `tests.sets` names a question set and the tests section has no `showcase` entry (a pack uploaded
before #620, say): `onboard: warning: pack.yaml:tests: ...`, saying what is empty without it and how to add it. It reads
the pack through the pack store on each call, as it reads the sets from the estate, so the
declaration and the set files it names are of the same onboarding, and parses it once per content; a pack store that
cannot be read is a 503.
No platform or portal code names a question, a period or a category for it. The question text stays in the set's file;
the pack holds the ids alone, as a context's golden questions are held.

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
- `joins[]`: `{id, kind: key|calendar_rollup|hierarchy|cross_reference, from, to, condition, cardinality, type?, validity_window?, alias?, through?,
  implements?}`, typed and declared, never inferred. The condition is a conjunction of read-only conditions on the two
  tables' columns, equating a column of the one with a column of the other, and never on a restricted column; a join
  across two sources is a cross-reference through a curated source's table, and implements a declared relation (S23).
  A harvested foreign key is a candidate only: the compiler lists each one no declared join covers, and reports a
  composite one until a join covers it (S05).
  Every join requires `cardinality: one-to-one|one-to-many`, from its `from` side to its `to` side after the declared
  condition, validity window and version policy apply. `one-to-one` means a source row matches at most one target;
  `one-to-many` means it may match several. For a versioned target, include the context's current or as-of policy
  when checking this declaration; historical versions can repeat the same key. The compiler and SQL gate refuse a sum or count repeated by a
  `one-to-many` join unless the measure is aggregated at its own grain in a subquery or CTE before joining.
  `type` is `inner` (the default), `left`, `right` or `full`.
  An explicit type that would discard a missing optional role required by a null-keeping exclusion is refused;
  declare `left` or `full` for that role.
  Optional `validity_window` names fully qualified `{date, effective, expiry}` columns and adds the inclusive
  condition `date BETWEEN effective AND expiry` to `ON`.
  The bounds belong to one joined side; the date can belong to the other side or a table the context has already
  reached. All three columns must be declared dates and unrestricted. Identical type, condition and window
  declarations are duplicates; separately named joins may declare different types or windows over the same keys.
  The SQL gate requires the declared type, complete key condition and exact window on both reference and served
  queries. A missing or altered window, changed type or condition, undeclared join or derived-table outer join is refused.
  For example, `{id: event_member, kind: key, from: ledger.events, to: ledger.members,
  condition: events.member_id = members.member_id, cardinality: one-to-one, type: left,
  validity_window: {date: ledger.events.occurred, effective: ledger.members.effective, expiry: ledger.members.expiry}}`.
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
  update (S30). Since meta-model 1.6 the preload is format 2, which also holds the platform's side of question shapes (§4.14).
- `policies.scd2[]`, `policies.release_driver[]` and `policies.partitioned[]`: time semantics as policies, `scd2` a table's versions
  (`valid_from`, `valid_to`, `is_current`), `release_driver` the table that says when a period is frozen and released, and
  `partitioned` (meta-model 1.5) a table's date or timestamp partition column. Their tables and columns are in the lock, one of each kind a table (S28).
- `runtimes[]` and `default_runtimes[]` (meta-model 1.5, issue #347): external execution runtimes (`{id, label, connection: {dialect, location, execution_identity?, billing_project?}, tables?, absent?}`) mapped one-to-one against the reference lock and pinned into `pack.lock`, plus the ordered list of runtime ids a query or suite uses when it names none (S31). The internal reference runtime (`reference`) needs no entry.
- `count_metrics[]` (issue #474): distinct complete entity-key counts, optionally bound to a declared context and local
  filters (§4.7). S08 checks the glossary term, unique id, entity key, reachable population and filter declarations.
- `derived_measures[]` (issue #474): named `ratio`, `difference`, `percent_change` or `share` formulas over declared physical
  measure or count metric ids (§4.7). S08 checks the glossary term, unique id, closed operator, input declarations and optional context bindings.
- `banded_dimensions[]` (issue #474): labelled, contiguous half-open ranges over a declared numeric column (§4.5).
  S29 checks the unrestricted column, ordered bounds and unique labels; the compiler renders the declaration as `CASE WHEN`.

The question-shape sections of meta-model 1.6, `phrases`, `shapes`, `recipes`, `clarifications`, `shape_tests` and
`examples`, are in §4.14.

**Built (#39):** Meridian's pack 1.1.0 fills these for the Wealth expansion (meta-model 1.1.0), and the pack as written for
1.0.0 still compiles against today's meta-model. The runtime answers a term that resolves to a context from the context's filters, joins and time
rules (`kge/semantics/query.py`), and joins Retail and Wealth only through the certified join.

### 4.14 question shapes (optional; meta-model 1.6)
Issue #508 (B1.1 of #449), from the owner-approved design [question-shapes.md](question-shapes.md) §3. How a question's
words combine into a query plan becomes pack data: phrases that mark an operation, question shapes (templates over what
the parsed question holds), the recipes of building blocks a shape contributes, clarification rules, each shape's tests
and its worked examples. All six sections are optional and need `manifest.meta_model_version` 1.6.0 or later: on an
earlier pack, the rule that owns a section reports it as needing meta-model 1.6.0 or later. They compile into the graph
(graph-model-spec §8). Since issue #514 the question lexer reads `phrases`, the preload's and a pack's, for coverage and the readable-word check; the
coded rules read a table equal to the coded one until B7, and nothing at run time reads the other five, so no answer moves
from them; the coded planner (`kge/services/ask/engine/planshapes.py`) still builds every plan.

| Section | Fields | Checks |
|---|---|---|
| `phrases[]` | `{phrase, operation}` or `{phrase, filler: true}`: a lower-case word or phrase that marks one operation from the preload's list, or is filler | S01, S32 |
| `shapes[]` | `{id, label, slots: [{name, kind, count?}], requires, forbids?, constraints?, recipe, replaces?, stage}` | S01, S02, S33 |
| `recipes[]` | `{id, blocks: [{block, id, ...parameters}]}` | S01, S02, S34 |
| `clarifications[]` | `{id, when: {requires, forbids?, slots?, constraints?}, message}`, where `message` is the id of a platform clarification text | S01, S02, S36 |
| `shape_tests[]` | `{shape, tests: {set, items}}`: items of a set `tests.sets` names, as a context's golden questions are (§4.12); a shape cites each item once | S01, S33 |
| `examples[]` | `{id, shape, question, plan, approved_by?, approved_on?}`: a question and the `query-plan/v1` plan a person approved for it; `approved_on` is a date | S01, S02, S35 |

The platform's closed lists are code (`kge/compiler/standards.py`) or platform data (`standards/preload.yaml`); a pack
uses them and adds to none:

- **Slot kinds:** `measure`, `context`, `entity`, `dimension`, `period`, `range`, `number`, `comparison_op` and
  `direction`.
- **Stages:** `specify`, `shadow`, `graph_first` and `retired`.
- **Operations:** the preload's `operations`, the 51 the coded lexer marks. `requires` and `forbids` name operations and
  markers.
- **Markers:** `from_to`, `asks_one` and `measure_part`. The names are fixed in 1.6.0; the lexer sets them since #515
  (B2.2 of #450), `measure_part` on a predicate the pack flags `part: true` (§4.5). The coded planner reads none of them.
- **Position constraints:** `{before: [a, b]}`, `{after: [a, b]}` and `{adjacent: [a, b]}`, each operand an operation, a
  marker or a slot kind.
- **Coded builders:** the preload's `coded_builders`, one per plan-building branch of the coded planner, written
  `planshapes.<function>[:<branch>]` (for example `planshapes.compose:contexts`). A shape's `replaces` names one. Each builder is replaced by one shape at
  most, across the preload and the pack (S33); the preload refuses two of its own shapes replacing one builder.
- **Clarification texts:** the preload's `clarification_texts`, each `{id, text}` in the coded planner's own words.

Where design §3.1 is silent, 1.6.0 decides:

- A `requires` entry is a name or `{one_of: [two or more names]}`, and `requires` may be `[]`. `forbids` lists plain
  names.
- A slot's `count` is a whole number n, or `{min, max}` with at least one bound and min at most max; an absent count is 1.
  A slot kind a shape does not declare is unconstrained in it. A slot with a count of 0 (or `{max: 0}`) excludes its
  kind: the recipe never uses it, and it is not reported unused.
- A shape declares each slot kind once, since a question's words fill slots by kind; its `count` says how many. A recipe
  gives a slot without an index (`$context`) only where the slot takes at most one, or to a parameter that takes a list.
- A clarification rule's `when.slots` is optional and maps a slot kind to a count of the same form.
- An "or" in a clarification condition is written as two rules with the same `message`.
- A preload shape is used by a pack when the pack cites it in `shape_tests` or `examples`. S33 then holds the pack to the
  same tests and worked example as for a shape of its own.
- An operand is required by a shape when it is a plain `requires` name, or a slot kind the shape declares with a count
  of at least 1 (an absent count is 1); a `one_of` member is not required. Of the position constraints only `before`
  separates two shapes, and only in reverse order on operands both shapes require (F9; EM decision, 18 Nov 2026):
  `before[a, b]` also holds on a question with no `b`. `after` and `adjacent` never separate two shapes.

**Recipes.** A recipe composes the building blocks the platform's catalogue lists, `kge/semantics/blocks.py`, which is
generated from `plan.py`'s node kinds when it loads: at 1.6.0, `measure`, `distinct_count`, `column`, `hierarchy`,
`time`, `band`, `compare`, `in`, `null`, `all`, `any`, `not`, `membership`, `single`, `range`, `ytd`, `latest_released`,
`all_available`, `time_mode`, `previous_period`, `previous_year`, `explicit_period`, `contexts`, `ratio`,
`percent_change`, `difference`, `share`, `running_total`, `moving_average`, `order` and `limit`. A block's `id` is
unique in its recipe, is not `time` or `limit`, and holds no `/`. Each other key of a block is one of its parameters, of
the type the block's model gives it:

- a slot reference, `$<slot>` or `$<slot>[i]`, i from 0 in the order the question names them;
- a typed literal: a scalar `{kind, value}`, a period, an enum value, a number or a boolean;
- an id the pack declares, for a parameter that takes a name;
- another block's id in the same recipe, or `<block id>/<output>` for a comparison's output (`cmp/base`).

A slot fills a parameter only of a type its kind fits:

| Slot kind | Fills a parameter that takes |
|---|---|
| `measure`, `context`, `dimension` | a declared name, or another node's id |
| `entity` | a declared name |
| `period` | a period, or a time |
| `range` | a time |
| `number` | a scalar, or a whole or positive number |
| `comparison_op` | an operator, from an enum that offers `gt` |
| `direction` | a direction, from an enum that offers `desc` |

References form no cycle, and with placeholder slots the recipe expands into a `query-plan/v1` that the plan's own models
accept (S34). That is a schema check only: what a plan means is checked after load (V06, from B3), and the compiler never imports
the plan checker (F9). A recipe of one `previous_period` comparison and no time block, for example, expands to a
comparison over all available time: it passes S34, and is refused later, "time comparison needs bounded periods".
A shape for two named contexts and their difference, and its recipe:

```yaml
recipes:
  - id: two-context-difference
    blocks:
      - {block: contexts, id: cmp, left: "$context[0]", right: "$context[1]"}
      - {block: difference, id: d, numerator: cmp/base, denominator: cmp/other}
      - {block: single, id: t, period: $period}
shapes:
  - id: two-context-difference
    label: Two contexts and their difference
    slots: [{name: context, kind: context, count: 2}, {name: period, kind: period, count: {min: 0, max: 1}}]
    requires: [difference]
    forbids: [comparison]
    recipe: two-context-difference
    stage: specify
```

**Worked examples.** An example's plan validates as `query-plan/v1` (strict: no extra field, no SQL and no expression),
and every name in it resolves against the pack (S35). Some ids are made by the runtime from declarations and the lock,
so a name resolves when it is one of these, where a column is a lock column `source.table.column`:

| Name in the plan | Resolves when it is |
|---|---|
| context | a `contexts` id; `schema:<column>:<sum, avg or count>`; `schema:<column>:…:<column>:count`, with two or more columns; `context:<measure>` for a measure form below other than `<…>/measure`; or `cohort:` before any of these |
| measure | a `measures` id; `mapped:<column>:<sum, avg or count>`; `count:<entities id>`; or `<schema context>/measure` |
| entity | an `entities` id, or `schema:entity:<lock table>` |
| relation (membership) | a `joins` id |
| calendar | a `calendars` id, or `schema_calendar` |
| band | a `banded_dimensions` id |
| declaration | a `derived_measures` or `count_metrics` id |
| column | a lock column |
| join path entry | a `joins` id |

These are the forms the runtime makes at 1.6.0, as `kge/compiler/checks.py` holds them; a later slice that adds
one widens this table with it. Above `specify`, an example names both `approved_by` and `approved_on`. At `specify`, an
example without either is a draft: no finding, and it counts towards no metric (its `WorkedExample` node is marked
`draft`). Naming one without the other is a finding at any stage.

**The preload, format 2.** The platform's base phrases, shapes, recipes and clarification rules are in the preload
(`standards/preload.yaml`, format 2, version 1.3.0), which loads into every graph as the preloaded concepts do. Version
1.3.0 holds baseline v1: the coded planner's phrase table, each operation phrase with its operation and each filler word
and phrase with `filler: true`, every row marked `baseline: true`. Four words, `in`, `over`, `their` and `which`, keep
both coded readings, an operation and filler; only a baseline phrase may name both, and every other phrase names
exactly one. The base shapes, recipes and clarification rules are still empty. A pack extends the preload and never
redefines it: a pack phrase is not a preloaded phrase (S32), and a pack's shape, recipe and clarification rule take ids
of their own (S33, S34, S36). Version 1.2.0 added `shape_test_items.min_also_asked_as: 3`, the fewest alternate
phrasings a shape's test item has, read and checked by `kge/compiler/standards.py`.

**Test items (F12, issue #509).** The up-front check (`kge/estate/upfront.py`, run by `check-pack` and by onboarding
before anything is written) holds each item a shape cites, in its `shape_tests` entries, to four things; an item no shape cites is not read
(F10). It is an item of its set file, not one of its expanded phrasings. It has at least the preload's `min_also_asked_as` distinct alternate phrasings,
its `also_asked_as` and `added_phrasings` counted together, a phrasing that repeats the question or another (ignoring
case and spacing) counted once or not at all. Its approval of its reference SQL is well formed at any stage:
naming one of `reference_sql_approved_by` and `reference_sql_approved_on` without the other, a blank approver or a
date not `YYYY-MM-DD` is a finding. And where it has reference SQL, read from the item's top level first, else from its
`expect`, and a shape that tests it is above `specify`, it is approved. At `specify` a missing approval is a draft, not a
finding: it is printed as a warning, `F12 <file>:<entry>: draft: ...`, and refuses nothing. Holding drafts out of the
metrics, V05's part, is B4's (#452).

**Coupled groups.** Two shapes, pack or preload, can match the same question unless a name one forbids is a plain
required name of the other, every member of a `one_of` group of one is forbidden by the other, for some slot kind both
declare their count ranges do not intersect, or one has a `before` constraint whose reverse the other has on operands
both require (F9; an operand is required as "Where design §3.1 is silent" says). `after` and `adjacent` separate no
shapes. A clarification rule separates such a pair when its requires, forbids, slot counts and constraints hold on every
question both could match.
A pair that can match the same question and is not separated is coupled, and must contribute disjoint plan nodes: no
section and block in common, and not both a time or a limit (S33). A clash between a pack shape and a preloaded
one is reported at the pack's shape, and a clash between two of the pack's at the later one. The compiler lists the coupled groups, the connected
groups of coupled shapes with every shape in one, in the artifact's `graph-model.yaml` as `shape_groups`; each `Shape`
node carries its group's first id as `coupled_group`, and `make compile` prints the count when the pack or the preload
declares a shape. No two shapes, pack or preload, replace one coded builder (F11), and a shape cites each test item
once (F10).

**Data, not code** (design §7). A pack declares no code, no SQL, no expression, no regular expression and no building
block of its own. A question that needs a calculation the catalogue cannot express is a platform issue for a new block,
written once for every client.

## 5. Validation

### 5.1 Structural (pre-compile; any failure ⇒ not compilable)
- S00 (v0.3) The manifest names `client`, a client id made of a lower-case letter, then lower-case letters, digits and hyphens, 16 characters at most, equal to the pack's folder name, and `meta_model_version`, a semver version.
- S01 Every section and nested value has the shape in `kge/compiler/checks.py`'s field tables; YAML is plain and no value is the literal `\N` reserved for artifact NULL. Since meta-model 1.6 the field tables cover the question-shape sections too (§4.14): a phrase names exactly one of `operation` and `filler: true`; a slot's kind and a shape's stage are on the platform's lists; a count is a whole number, or `{min, max}` with min at most max; `when.slots` maps slot kinds to counts; a `requires` entry is a name or `{one_of: [two or more names]}`; a constraint is one of `before`, `after` or `adjacent` with two operands; a recipe's block is a mapping with a `block` and an `id`; a predicate's `part` is a boolean; a term's `is_a` and `broader` are non-empty lists of term ids, each id once, and its `codes` a non-empty list of `{column, code}` with a text code, each pair once, none of them on an earlier pack (§4.4).
- S02 Ids are unique per kind. General ids match `^[a-z0-9][a-z0-9_.-]*$`, sources and curated sources match `^[a-z][a-z0-9_-]*$`, and acronyms match `^[A-Z][A-Z0-9_]*$`. Term names are unique; policies share an id namespace, as do filters and row selectors. Term ids are stable. Shapes, recipes, clarification rules and worked examples take ids too (since 1.6).
- S03 `manifest.platform.meta_model` satisfied by the installed meta-model. v0.3: the installed meta-model is of the major version `manifest.meta_model_version` names, at its minor or later, and `schema.meta_model` pins that version too.
- S04 Extensions only add; namespaced `x_<client>_` (v0.3; v0.2 had the pack's id); no collision with meta-model names.
- S05 Every FK names a declared table+column on both ends, same source, compatible types. An uncovered composite key is a finding and stops compilation; uncovered non-composite keys are listed separately as join candidates (§4.13).
- S06 Every `no_join` names two declared, distinct sources; pairs unique (unordered).
- S07 Every mapping hits a declared column and a declared term.
- S08 Every measure: `term` declared; `column` is in the lock and carries no other measure; its read-only scalar aggregate names that column and reads only columns of its table; an optional `certified_in` names a declared runtime (`reference` or one in `runtimes`) that holds the measure's table (meta-model 1.5).
- S09 Every measure names a declared calendar and its table has a period binding, for every allowed grain.
- S10 Currency codes are ISO 4217; a conversion's source currency is read from text such as `period_end_rate_from_EUR` and differs from the measure's currency.
- S11 Every alias references the declared target its kind requires; a phrase appears once, whatever its target or case. A `sources` alias has a non-empty list of distinct declared sources and no single target (since 1.4.0). Predicates and value vocabularies are checked by S29.
- S14 Every role `reads` names declared sources; `writes` ⊆ {terms, mappings}; processes from the meta-model constants or the platform operational list.
- S22 (v0.3) Every restricted column is in the lock and listed once within its policy; separate policies may restrict the same column. Each phrase is for one of its policy's columns, names a word, and is given once.
- S23 (v0.3) Declared joins: required cardinality, a supported join type, tables in the lock and any validity window over declared date columns; a condition that is a conjunction of read-only conditions on their columns, equating a column of the one with a column of the other, with types that join and no restricted column; a table joined to itself is aliased; a join across two sources is a cross-reference through a curated source's table and implements a declared relation (§4.13).
- S24 (v0.3) Filters and row selectors: a table in the lock, a read-only condition on its columns, no restricted column, conflicts declared both ways.
- S25 (v0.3) Contexts: a declared term, a declared measure on the fact table, declared filters, row selectors and joins; each join follows from the fact or a join before it; each filter's table is one the context always reaches; its golden questions are items of a set the pack names.
- S26 (v0.3) Acronyms, entities, relations, lineage, columns not joinable and ontology concepts name declared terms and entities, and tables and columns in the lock; a column not joinable to a table belongs to a source that has a no-join fact with the table's.
- S27 (v0.3) Certified joins: two declared, distinct sources that are no no-join pair, each pair once, through a declared cross-reference between them; every cross-reference is a certified join's.
- S28 (v0.3) Time policies: an `scd2` policy's, a release driver's and a `partitioned` policy's table and columns are in the lock, one of each kind a table; a `partitioned` column is a `date` or `timestamp` (meta-model 1.5).
- S29 (v0.3) The rest of the parser's vocabulary (meta-model 1.2): a value vocabulary's and a predicate's column is in the lock and not restricted; a value vocabulary gives values or a pattern the platform knows, not both; a predicate gives a value where its operator takes one, and an `in` or `not_in` predicate two or more `values`, all text or all numbers, each once, and no `value`, while no other operator takes `values`; `contains` and `starts_with` require a text column and one nonblank text value with no LIKE wildcards; each intent once; each functional mailbox a mailbox's local part, once; a predicate's `part` only since meta-model 1.6. Numeric `banded_dimensions` use unrestricted numeric columns, complete ordered contiguous ranges with finite inner bounds and distinct nonblank labels.
- S30 (issue #57) Ontology mappings (meta-model 1.3): each maps one declared term, entity or restricted-data policy to a concept the platform preloads, by its IRI, with a SKOS relation, certified by a steward on a date; a subject maps to a concept once; and a concept the pack declares itself takes no preloaded concept's id.
- S31 (issue #347) Runtimes (meta-model 1.5): each runtime declares its connection (`dialect` and `location`), maps only tables and columns in `pack.lock` with compatible column types, lists each reference table and column at most once, maps no two tables of a source or columns of a table to the same runtime name, and takes no `'reference'` id; `default_runtimes` is a non-empty list of distinct declared runtime ids (`reference` or one in `runtimes`).
- S32 (issue #508) Phrases (meta-model 1.6): each lower case, with no leading or trailing space (refused in the preload too), and given once, ignoring case; it marks an operation the preload lists, or is filler; it is not a preloaded phrase, and not also an alias, an acronym or a grouping phrase of the pack, whose meaning would then depend on which table is read first.
- S33 (issue #508) Shapes and shape tests (meta-model 1.6): slot names given once, slot kinds, markers and position constraints from the platform's lists, and `requires` and `forbids` naming known operations and markers, no name nor every name of a `one_of` both required and forbidden; a recipe the pack or the preload declares, whose slot references name declared slots, within their counts, of a kind that fits the parameter, and every declared slot used but one with a count of 0; `replaces` naming a coded builder the preload lists, which no other shape, pack or preload, replaces, and a shape that replaces a builder no lexer marker reproduces (`planshapes.compose:ranking` and `planshapes.parts`, since #515) never at `retired`; an id no preloaded shape has. Every shape the pack declares or uses from the preload has `shape_tests` citing at least one item of a set `tests.sets` names, each cited once per shape, and a worked example in this pack. Two shapes that can match the same question contribute disjoint plan nodes, or are separated by a `forbids`, their slot counts, a clarification rule, or by `before` constraints in reverse order on operands both shapes require (F9); the coupled groups are listed (§4.14).
- S34 (issue #508) Recipes (meta-model 1.6): blocks from the platform's catalogue (`kge/semantics/blocks.py`), with unique ids that are not `time` or `limit` and hold no `/`, and parameters of the types each block declares; a literal name the pack declares; references that form no cycle; the recipe expands with placeholder slots into a valid `query-plan/v1`; an id no preloaded recipe has. Schema only (F9): what a plan means is V06's, after load.
- S35 (issue #508) Worked examples (meta-model 1.6): a shape the pack or the preload declares; a plan that validates as `query-plan/v1`, every name in it one the pack declares or the runtime makes from its declarations and lock (§4.14); `approved_by` and `approved_on` for a shape above `specify`, both or neither at `specify`, where an example without them is a draft that counts towards no metric.
- S36 (issue #508) Clarification rules (meta-model 1.6): `requires`, `forbids`, slot kinds and constraint operands on the platform's lists; a `message` naming a clarification text the preload lists; an id no preloaded rule has.
- S37 (issue #512) Business terms' structure (meta-model 1.6), over the pack and the lock: each `is_a` and `broader` names another declared term, and each code's column is in the lock and not restricted; `is_a` and `broader` each form no cycle; a term has at most one broader term in a classification, the classification being the broader term's `is_a` class, so two broader terms that share a class, or that both lack `is_a`, are refused; a member's coded column (a term with a class of its own or a `broader` ancestor's, all its references resolving) maps (`mappings`) to its `is_a` class or a `broader` ancestor's class; a term and its broader term coded on different tables have a declared `hierarchy` or `key` join between those tables, through which F13 compares their rows; a member's name (a term with `is_a`) is no `value_vocabularies` value and no predicate phrase, ignoring case; and no code contradicts another: the same column and code is not given another name by a `code_name_pairs` member, or coded by another term, and a `code_name_pairs` member with the term's name, or an alias of it, ignoring case, has a code the term gives on that column. A column's mapping to a classification term is uncertified. A coded term and an agreeing `code_name_pairs` member can coexist through B2. The shadow planner gives the term precedence, and the coded planner retains the vocabulary. B7 (issue #455) refuses coexistence when the coded readers retire.
- S38 (issue #620) Showcase questions: `tests.showcase` names a set `tests.sets` names and lists the items of, no held-out set (`heldout` or `heldout_<name>`), and at least one item, each one of that set's listed items and given once.
- F12 (issue #509) Shape test items, in the up-front check (`kge/estate/upfront.py`) rather than the compiler, since it reads the set files, so a finding refuses the upload, not the compile: each item a shape cites, in its `shape_tests` entries, is an item of its set file with at least three distinct alternate phrasings (`also_asked_as` and `added_phrasings` together; the preload's `min_also_asked_as`); `reference_sql_approved_by` and `reference_sql_approved_on` both or neither, well formed; and, where a shape that tests it is above `specify`, its reference SQL approved. At `specify` an unapproved item is a draft warning, not a finding (§4.14).
- F13 (issue #513) Business terms against the rows (meta-model 1.6), not a compiler rule, since it reads rows: every code a term declares is a value of its column in some row, and every `broader` edge agrees with the rows, compared through the two terms' coded columns, each of the narrower term's by itself, with the several codes a term gives alternatives, on one column and across the columns of one table: where the broader term is coded on several columns of the table a row reaches, the row agrees when any of them holds one of its codes, and is compared, and reported, once. On two columns of one table a row holding one of the narrower term's codes holds one of the broader term's in the same row; across two tables, and for two terms coded on the same column (a parent-child table), through the first `hierarchy` or `key` join declared directly between the tables (for one column, from the table to itself under an `alias`, the alias naming the broader, parent row), read as the column equalities its condition holds, each joined row does, inner, as the SQL gate joins, so a row whose join key matches no row is not compared; a row whose join key is NULL has no broader row, and so no broader code, and disagrees. Where both terms are coded on one column of the table the broader term's columns are on (a member and its parent both coded on a dimension's key, the member also on a fact column joined to it or on another column of the dimension), the row reached is the member's own, so the route goes on through that table's join to itself (on one table, through that join alone), and the member's parent row is compared for every broader column on that table, a NULL key at either step being no broader row; without that join to itself the edge cannot be compared. An edge no such join lets F13 compare is a finding, not passed over. Values compare as their column's type reads them: a number by its value (`1.50` is `1.5`, `01` is `1`), a boolean as true or false, a date and a timestamp by their value, each written in any form the estate's loader accepts (`2026-1-5`, `2026-01-05T10:00:00`), anything else as its text, each type name the estate accepts (`INT8`, `FLOAT4`, `BOOL`, ...) read as its type, the same at upload (the fixture's declared types) and at onboarding (the warehouse's). A code on a restricted column is S37's, and F13 reads none of its rows. A NULL broader code means no broader term, so a row with the narrower term's code and a NULL beside it disagrees. Every row is read, historical versions too: a current-version condition in the join and a validity window are not read. It runs twice. At upload, in the up-front check (`kge/estate/upfront.py`, which `check-pack` and onboarding's input check run), over the fixture's CSVs, touching no warehouse; a unit the fixture holds no rows for is no finding but a line, `check-pack: not checked here: F13 <unit>: ...`. And at onboarding's compile step, after the compile and before anything is loaded, over each unit's live warehouse version through the estate's warehouses and the SQL gate (`kge/estate/term_warehouse.py`), with one `GROUP BY` of the narrower and broader codes for each narrower column and the broader columns one join route reaches (through a join, with the rows whose join key is NULL, at either step, in the same query) and one `GROUP BY` for each coded column in no same-row pair, each over the rows holding a declared code, beside the warehouse's description of each coded table, so the number of queries is the pack's, never the rows'; a finding there, or a query the gate refuses, refuses the onboard at its compile step with exit 2, and nothing is loaded. A pack that declares no code sends no query and prints nothing.

**Built (#39):** `kge/compiler/checks.py` runs S00 to S11, S14 and S22 to S38 before `kge/compiler/compile.py` writes anything
(`make check`, `make compile`), lists every finding as `<rule> <path>: <message>` and exits 2 on any. A finding in a
section's shape stops only the checks that read that section. Not checked there: S12, which the loader checks when the
documents land (fail closed: a warning, and no edge); S13 and S15, since the pack names its question sets and does not
restate them (§4.12); and S16 to S21, tiers and readiness (the plan's Phase 4). `tests/unit/test_compiler.py`,
`tests/unit/test_query_semantics.py` and, for business terms, `tests/unit/test_term_structure.py` hold each check with a pack
changed to fail it.

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

The compiler runs S00–S11, S14 and S22–S38, plus C01; it does not run C02 or C03. The artifact has no `status`.
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
4. *Typed numeric interpretation.* `planrules.read` emits a typed plan from scoped declarations. `Planner` checks coverage, entity keys and declared paths before `compile_plan` builds SQL. Aggregation is declared by `measures[].aggregation`; harvested keys remain candidates.
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
16. *UI hardcoding.* Context’s React screens use `/golden` for sample questions and `/presets` for arms. Purposes and evaluation kinds retain the API’s fixed vocabulary.
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
- 15: AI certification lands each warehouse's catalogue (never its rows), `onboard` refreshes its schema into `pack.lock` (`make lock` remains for local use), and the compile reads only the lock.
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

On the pilot, jobs own the schema lock and people own the pack: no person runs `lock`, or `seed` for a fixture client. `onboard` rebuilds a changed fixture, certifies and lands it, refreshes tables, columns, types and keys, then compiles and checks. The steward sees its differences and rebuild reason before sign-off. Rows and storage sizes come from the released warehouse, not the lock. Row-only changes reload the same hash and require fresh sign-off. BigQuery fixture versions stay separate in the existing dataset; the release identity pins them, and `release PREVIOUS=yes` restores the previous ledger identity even at the same hash. Restart the service after release or rollback. The steps below also expose the individual local commands.

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
| 4 | Structural checks | compiler | `make check` | the lock holds what landed, and the pack has no findings (S00 to S11, S14, S22 to S38; §5.1): it prints the artifact's path, not compiled yet, and names the rules it does not check, exit 0. Otherwise it lists each finding, `<rule> <path>: <message>`, exit 2 |
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
| Numeric bands | `banded_dimensions` (§4.5): labelled half-open ranges over an unrestricted numeric column | S29 |
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
