# Runtime Pipeline — draft v0.2

Status: draft. Maps David's six runtime stages onto the Meridian demo as it is
(`docs/use-cases/kge/demo/` on `origin/develop` @ `01836f6`, read-only, now [`demos/kge/`](../../../../demos/kge/README.md)), and
says what each stage should read from the pack ([pack-contract.md](pack-contract.md) v0.4)
and the compiled graph model ([graph-model-spec.md](graph-model-spec.md) v0.3).
Paths below are relative to the demo folder.

**v0.2, 11 Oct 2026 (issue #38).** The demo now runs graph off against graph on through one pipeline:
a run config in place of the three fixed arms, a reference text-to-SQL agent with a control and a
treatment arm, a trace per run, tier-aware retrieval and citation from an upstream certification
stand-in, and an A/B harness. [§4](#4-the-ab-as-built-in-the-demo-issue-38) states the merge point,
the arms, the run config and the run trace as built. Elsewhere, a line marked **Built (#38)** says
what the demo now does where the v0.1 text about `01836f6` no longer holds; the rest of v0.1 stands,
and the §3 contract stays planned for the platform. No model key is configured and no real model
has been run: the A/B baseline is on rules, and the model path is proven on an offline mock, whose
runs are labelled `mock`, never presented as a model's.

**Issue #39, 14 Oct 2026.** The runtime now serves one profile per process, chosen at deployment, and reads its client's
domain from the compiled pack ([graph-model-spec.md §8](graph-model-spec.md#8-profiles-and-layout)). The run config
gains the profile ([§4](#run-config)): fixed by the process, recorded in the run trace and the A/B manifest, never per
request. A line marked **Built (#39)** says what else the demo now reads from the graph where a stage below says it
should.

**Issue #55, 18 Oct 2026.** An offline LangGraph improvement job (`kge/improve/flow.py`, `make improve`) learns from the
business units' content (column descriptions, documents, candidate terms), the page's unmatched question log
(`build/stores/<client>/run/unmatched.jsonl`), and the latest A/B misses (`team_<unit>` and golden sets, never reading
`heldout` or `heldout_<unit>` sets before `judge`), proposes governed pack edits with a semver bump, compiles and loads
the candidate in an isolated workspace, runs the A/B and margin gate beside the live graph, and writes the proposal to
`clients/<client>/proposals/open/proposal.yaml` for business acceptance and steward certification before release.

## 0. The paths today

| Path | Entry | Code | Model use |
|---|---|---|---|
| Rule-based | `POST /answer` (any arm) | `kge/services/ask/engine/core.py:Engine.answer` | none |
| Combined | `POST /ask` (graph arm only) | `kge/services/ask/engine/combined.py:Combined.ask`. Falls back to `Engine.answer` (with `combined: false`) unless the question is a single-unit cohort with ≥2 certified terms and no special | optional (`kge/adapters/model/providers.py:make_llm`): term recovery, SQL fallback, phrasing |
| Reference agent (**Built (#38)**) | `POST /agent` (`control` or `treatment`), `make agent` | `kge/services/ask/engine/agent.py:Agent.ask`. With no model: the planner and template through `Engine.answer` on the arm's run config, then `Agent.evidence` | with a model, it writes every query and may phrase the answer; `MODEL_PROVIDER=mock` runs that path on the offline mock (`kge/adapters/model/mock.py`); none configured, so it runs on rules |
| Improvement job (**Built (#55)**) | `make improve`, `POST /onboard/run` (`step: improve`) | `kge/improve/flow.py:run_improve` (8-node LangGraph `StateGraph`: `gather` → `diagnose` → `propose` → `build` → `test` → `keep` → `judge` → `hand_over`) | optional (`kge/adapters/model/providers.py:make_llm`, prompt `improve-propose-v1`): proposes JSON edits from misses and unit content, validated by `kge/improve/edits.py` (one retry on failure); `MODEL_PROVIDER=mock` runs `kge/adapters/model/mock.py`; none configured, so `kge/improve/rules.py` proposes deterministically |

The eval (`kge/eval/run_golden.py`, `/eval`) only exercises `/answer`. Both paths
return one flat dict mixing intermediate state (`trace`, `plan`, `parts`) with
the final answer. There is no typed hand-off between stages.

**Built (#38):** `/eval` still scores the three presets, now as the caller. The A/B harness
(`kge/eval/harness.py`, `make ab`) runs the presets through `Engine.answer` and the agent's two arms
through `Agent.ask`, in-process. All three paths still return a flat dict, each with a `run_trace`
beside it (§4).

## 1. Stage by stage

### Stage 1: Intent & entity resolution
**(a) Today.**
- `/answer`: `question.py:parse` → `Request` (term, terms_found, units, all_units, period, filters, agg, definitional, special, candidate). Then the dispatch in `Engine.answer` acts as the intent router: `special` branches, `definitional` → `cite`, `__population__`, otherwise figure.
- `/ask`: `Combined.ask` reuses `parse`, adds `understand` (model, glossary-bounded), then classifies terms with `combined.py:role` (flag/event/measure) to decide cohort vs measure.
**(b) Hardcoding: high.**
- `ALIASES`, `UNIT_WORDS`, `MONTHS`, `FILTER_PATTERNS` (product names, `TEAM-x`, codes), `DEFINITIONAL`, plus inline regexes ("across the bank", "plus", "which … also", "email address|dob").
- `special` names are domain cases.
- `role()` infers semantics from column suffixes (`_id/_no/_ref`) and BOOLEAN type.
- `measure_agg` infers avg from the word "average".
**(c) Should read.**
- `Alias` nodes (`kind` term/source/scope/intent/predicate/value/candidate) via `ALIAS_OF`.
- `Term` (by id), `Source.label`, `Calendar` for period parsing.
- `Measure.aggregation` and `population` instead of `role()`/`measure_agg`.
- `Policy{restricted_attribute}` for refusals by attribute.
**(d) Gaps.**
- No explicit intent taxonomy: intent is the implicit union of `definitional`, `special`, `agg` and cohort-vs-measure.
- Entity resolution returns names, not graph ids.
- No confidence or ambiguity output: it either matches or abstains.
- The model step (`understand`) exists only on `/ask`.
- Held-out misses (demo README: "still unresolved", "match", "explain", DOB) are all failures of this stage. **Built (#38):** the demo README now names today's: "active during", "match" and "line up", "explain" and "is it acceptable". The parser at `01836f6` already read "still unresolved" and a date of birth (since #29); that README's list was out of date.

**Built (#38):** resolution is a stage of the run config (§4). `parse(question, terms, aliases=False)`
drops `ALIASES` and the customer population, so `resolution=none` maps no term at all and
`resolution=words` leaves the planner the question's own words (`question_words`, matched against the
warehouse schema by `Engine.guess`): the agent's control. At #38 the alias list was code.

**Built (#39):** the alias list is the pack's: `Alias` nodes, read from the graph once, when the engine starts
(`Graph.domain`), into `ALIASES` and `UNIT_WORDS`, with the same longest-first matching. So is the rest of the parser's
vocabulary, the value lists, predicates, intents and functional mailboxes (`Vocabulary` nodes, which `kge/semantics/vocabulary.py`
turns into the parser's patterns). A term that resolves to a `Context` names it, and the parser reads time modes, entity
ids and group-bys from the pack's aliases. **Built (#40):** the specials that named the client read the graph's facts: a
join on a column the pack declares not joinable (`NOT_JOINABLE_TO`), and a currency no certified measure is in or converts
from (`Graph.domain`). **Built (#55):** when `POST /answer` or `POST /ask` on the `graph` arm or `POST /agent` on the `treatment` arm
returns `abstain` with no glossary term recognised, `kge/app.py` appends a privacy-filtered
record (`at`, `path`, `arm`, `principal`, `question`, `reason`; never questions matching `kge/pipeline/privacy.py`'s
personal-data detectors) to `build/stores/<client>/run/unmatched.jsonl` so the
offline improvement job (`kge/improve/gather.py`) can propose aliases or terms for unmatched phrasing.

### Stage 2: Context generation (SQL context + unstructured context)
**(a) Today.** Interleaved with stage 3, not a separate stage.
- SQL context: `Engine.resolve` (graph arm → `Graph.implementations`, certified `MAPS_TO`; schema arm → name-guessing over `Warehouses.columns`); `Graph.join_path`, `column_owners`, `table_columns`, `no_join`.
- On `/ask`: `Combined.spec`, `key_table`/`key_of` (customer-key path via `UNIT_KEY`), and `sqlguard.build_allowlist` (certified cols + declared join keys + period columns). `Allowlist.as_prompt` is the only serialised "SQL context", and it's used only by `generate`.
- Unstructured context: none up-front. Documents are fetched after execution (stage 4).
**(b) Hardcoding: medium.** Graph lookups are generic Cypher templates over the schema. But `__population__` hardcodes four `(table, key)` pairs in `Engine.answer`, `UNIT_KEY` comes from `kge/estate/spec.py`, and `PERIOD_COLUMNS` is fixed.
**(c) Should read.**
- `Measure` + `COMPUTED_FROM` + `MEASURE_OF` (instead of measure props on `Col`).
- `PERIOD_COLUMN` + `Calendar`, `Source.entity_key`, `JOINS_TABLE`, `NO_JOIN`.
- `Column.ai_ready`/`tier` (only tier-allowed columns in the allowlist).
- `Policy{commensurability}`. Unstructured: `Term`←`DEFINES`←`Document` with `tier`.
**(d) Gaps.**
- No `ContextBundle` object: context is rebuilt ad hoc inside planning.
- No per-arm context builder: arms are boolean flags checked deep in `period_clause`, `plan_unit` and `commensurable`.
- Unstructured context isn't available to planning (e.g. a definition's version can't inform the plan).
- The allowlist ignores readiness and tier.

**Built (#38):** the reference agent builds its context as a step of its own, per arm, before any
SQL is planned: `Agent.context`, on its control steps (the warehouse schema and plain vector search) or
its treatment steps (the graph's context package, with the definitions by tier and the window each was
in force). Each returns a text block for the prompt, its sources numbered, and what the gate may admit;
it is not the typed `ContextBundle`. Since #48 the context workflow (`kge/services/ask/engine/context.py`) runs those steps
on LangGraph, a node per step, and `POST /context` and the MCP server mounted beside it in `kge/app.py`
serve the package to any agent. The reference agent is one of them: its loop is a LangGraph graph of its
own, and it gets each arm's package through `POST /context` with the principal's token, in its own process
unless `CONTEXT_URL` names a context service. `/answer` and `/ask` still build context inside planning,
and the allowlist still ignores readiness and tier.

**Built (#39):** the units, their labels and customer keys (`Source.entity_key`) and the restricted columns come from the
graph, and the period rule on the graph context reads `PERIOD_COLUMN` and `Calendar` (`Engine.period_clause`); the schema
and catalog arms and the agent's control keep the name heuristic, so the control is handed no curated metadata.

### Stage 3: SQL planning / validation / execution
**(a) Today.**
- `/answer`: `Engine.plan_unit` (template SQL per unit, joins via `Graph.join_path`), `Engine.period_clause`, executed by `Warehouses.run`. Combining happens in Python: `commensurable`, sum vs parts. **No `sqlguard` gate on this path.**
- `/ask`: `Combined.plan` (one CTE query, planner `cohort-v1`) → `sqlguard.gate` (read-only, tables, allowlisted columns, declared joins, `EXPLAIN` dry run) → on failure `Combined.generate` (model, 2 tries, same gate) → `con.execute`.
**(b) Hardcoding: high in the semantics, low in the gate.**
- `period_clause` sniffs `month_start`, `fiscal_quarter`, `as_of`, `*_on`, and hardwires an April fiscal year (`fiscal_from_calendar`).
- `plan_unit` infers count from suffixes.
- `commensurable` uses a `flag` suffix and the literal `PROC-RECON`. **Built (#40):** it no longer names `PROC-RECON`; the `flag` suffix stays.
- `sqlguard` is generic apart from `PERIOD_COLUMNS`.
**(c) Should read.**
- `Measure.expression`/`aggregation`/`snapshot`, `Calendar`, `PERIOD_COLUMN`.
- `Policy{commensurability, conversion, cross_source}`.
- The allowlist from certified `MAPS_TO` ∩ tier-allowed columns.
**(d) Gaps.**
- Two planners with different guarantees: the gate covers `/ask` only.
- Text-to-SQL is a fallback only, with no switch to force it for A/B.
- No plan object separate from execution; no latency or row-count capture.
- Cross-unit combining isn't a plan step.

**Built (#38):** `Engine.plan_unit` sends every `/answer` query through `sqlguard.gate` before it runs:
on the graph context the certified columns, filter columns, declared join keys and period columns; on
the schema context the warehouse schema (`schema_allowlist`); restricted personal data in neither. A
refused query never reaches the warehouse (`tests/integration/test_gate_answer.py`). `sql_source=model` makes the
agent's model write every query, through the same gate with one more rule for model SQL (`figure=True`:
the value is an aggregate of a column the context gives as a measure, over the warehouse's own tables in one
`SELECT`: no CTE, subquery, `VALUES`, `UNNEST`, `LATERAL`, sample or `random()`) and one retry carrying the gate's
reasons. The run trace times `plan`, `gate` and `execute`; there is still no plan object and no row count.

**Built (#39):** on the graph context, a term that resolves to a `Context` is answered from it (`kge/semantics/query.py`): its
measure over its fact table, its filters, its row selectors as `NOT EXISTS` (or `EXISTS`, to include), the declared joins
it renders, its time rules and the release rule, and the question's own conditions, counts and terms, each applied or no
figure given. The gate admits the context's own allowlist, and Retail and Wealth are read together only through the
certified join, in a session the runtime attaches (`Warehouses.attached`), a row selector on the far unit's table applied
to its side alone. Every other term plans as before.

### Stage 4: Unstructured chunk retrieval and binding
**(a) Today.**
- `Graph.retrieve`: exact cosine over chunks filtered by `embedding IS NOT NULL`, `$purpose IN c.purposes`, readable units and shelf. It also reports the "score-first, filter-after" loss.
- `/answer`: `Engine.cite` (definitional questions only) uses `defining_docs`, superseded handling and `uncertified_mentions`.
- `/ask`: `Combined.sources` builds `citations` / `not_cited` / `withheld` from DEFINES, certified MENTIONS and vector hits, then binds by term (`f["cite"] = first citation whose terms include f["term"]`).
**(b) Hardcoding: medium.**
- Shelf (`certified`/`uncertified`) and `readiness <> 'embed-prohibited'` are the gates.
- `cite` adds superseded docs when the question matches `\b(2023|2024)\b|\bversion\b`, a hardcoded year regex.
- Excerpt windows are fixed in size.
**(c) Should read.**
- `Chunk.gate_level` (embed) + `Document.ai_ready`/`tier` + `Tier.allows`: Bronze is shown and not cited, Silver is cited with attribution, Gold is cited as the authority.
- `SUPERSEDED_BY`, `DEFINES`, `MENTIONS`. Readable `Source`s via role → `CAN_READ`. Purpose from `access.purposes`.
**(d) Gaps.**
- **No tier-aware retrieval or citation**: shelf stands in for tier.
- Binding is term-level only: the figure ↔ document link is "first doc mentioning the same term". There is no binding of a figure to the measure definition version or the column's data-dictionary entry.
- `/answer` figure answers carry no documents at all.
- No fail-closed check on Tag 1 (there's no `ai_ready` yet).

**Built (#38):**
- Tags 1 and 2 come from upstream: `kge/estate/certify.py`, a stand-in for AI certification, lands one row per document (AI-ready yes or no; tier gold, silver or bronze; the window it was in force) in a landing zone of its own, as `certification.json` in a release under `build/landing/<client>/releases/<release_id>/` (since #39, with the masked documents and the warehouses' catalogues the graph ingests from). `kge/graph/load.py` reads them onto each `Document` and fails closed: no landed row, no AI readiness. The graph never writes back.
- `Graph.retrieve` takes a `tiers` filter: only AI-ready documents at the tiers named. `citation_rule=tiers` (`Engine.cite_tiers`) is the treatment's citation rule: gold cited as the authority, silver with attribution (owner and version), bronze listed as a reference and never cited, each document for the period it was in force. The candidate and steward paths are not tier-filtered. The presets and `/ask` still cite by shelf.
- The agent binds each figure to its own definition: the citation that defines the term certified on the column the gate found the figure measures (`bind` in `kge/services/ask/engine/agent.py`), of two the one in force for the period asked, failing that one about its unit. A bronze source offered as a citation is rejected (`Agent.cite`). On rules the planner's figures go through the same step (`Agent.evidence`), so the agent's figure answers carry their documents. `/answer` figure answers still carry none, and `/ask` still binds by term.

**Built (#39):** a definitional question on a term no document defines (the Wealth terms, certified in the pack's
glossary and through their contexts) is answered from the pack's glossary under both citation rules
(`Engine.from_glossary`, behaviour `glossary_definition`): the term's definition and steward, labelled as the
glossary's, citing nothing. What the question retrieved is listed as references, never cited as the term's definition or
as the authority. The control's retrieval rule is unchanged.

### Stage 5: Answer synthesis
**(a) Today.**
- `/answer`: the tail of `Engine.answer` builds the structured result (`behaviour` ∈ figure, sum_with_verdict, sum_without_verdict, parts_with_reason, refuse, blocked_access, abstain, abstain_route_steward, cite, cite_certified).
- `/ask`: the end of `Combined.ask` assembles `figures` + `citations` + `not_cited` + `withheld` (`behaviour: combined`).
**(b) Hardcoding: low-medium.** The behaviour vocabulary is generic and good. The reason strings embed domain ids (`POL-DATA-CLASS`, `STD-FX`, `PROC-RECON`, `DD-CORPORATE`). **Built (#40):** none does: a restricted refusal cites the pack's policy (#39), a key-name join refusal is worded from the no-join fact and cites the units' data dictionaries the graph finds, and a currency abstention is worded from the certified measures.
**(c) Should read.** Refusal and abstention reasons from `Policy` (cite the policy's governing `Document`). Tier labels per citation from `Tier`.
**(d) Gaps.**
- No `Answer` schema shared by both paths: field names differ (`figure` vs `figures`, `cite` list of ids vs `citations` objects).
- Synthesis and rendering are fused on `/ask` (`template` builds the facts sentence).
- The behaviour enum isn't declared anywhere except the scorer and the UI's `badge`/`HAS_SOURCES`.

**Built (#38):** the agent's contract declares the behaviours a model may return (`BEHAVIOURS` in
`kge/services/ask/engine/agent.py`), and the harness groups every behaviour into four classes, answer, parts, refuse and
abstain (`CLASSES` in `kge/eval/metrics.py`). There is still no shared `Answer` schema.

### Stage 6: Human-readable response generation
**(a) Today.**
- `/ask`: `Combined.template` (deterministic sentence), then `Combined.phrase` (optional model rewrite, rejected if any number isn't in `allowed_numbers` or a `[n]` marker doesn't exist).
- `/answer`: no text is generated server-side. `ui/index.html` `renderAnswer` / `renderCombined` / `renderSources` / `renderDetail` compose it in JS.
**(b) Hardcoding: high in wording.** `template` says "customers", "met the … definition", and treats the `average`/`total` prefixes as English rules. The UI hardcodes `CHIPS`, arms, purposes, and "reads all four".
**(c) Should read.** Labels from `Term.name`, `Source.label`, `Measure` unit/currency. Locale and wording templates are platform. Tier badges come from `Tier`.
**(d) Gaps.**
- Two renderers (server template vs UI JS) for two paths.
- The numeric guard exists only for `/ask`.
- No response contract for non-UI consumers (agents/MCP).
- No per-audience rendering (analyst vs steward vs agent).

**Built (#38):** the agent renders server-side (`template` in `kge/services/ask/engine/agent.py`, a third renderer) and
runs the number check on all of a model's wording, its reason and its answer: wording with a number
not in the result, a `[n]` that names no citation, or a document id its context did not give or said
not to use (`documents_in`), is not used, and the template stands (`Agent.render`). Any run of digits is a number, signed or glued to letters or a hyphen, unless it
is inside a name the answer carries whole: a label of the question's, or the id of a document its
context named (`numbers` and `names` in `kge/services/ask/engine/combined.py`, `carried` in `kge/services/ask/engine/agent.py`). The run trace records the outcome (`number_check`). `/answer` still generates no
text.

**Built (#39):** the page's title is the pack's, and its sample questions are the profile's golden set (`GET /golden`,
the question text alone), so a page shows no other client's wording.

## 2. Where access and readiness gates sit today

| Check | Stage | Where | Both paths? |
|---|---|---|---|
| Identity may query (`MAY_PERFORM querying`) | 1→3 boundary | `Engine.answer`, `Combined.ask` (`Graph.may`); API `needs("querying")` in `kge/app.py` | yes |
| Restricted attribute refusal | 1 | `special == restricted_lookup` (regex) | yes (via fallback) |
| Read every warehouse involved (join-time rule) | 2→3 boundary, before any SQL | `Graph.join_access` in `Engine.answer` / `Combined.ask` | yes |
| No cross-source join (`NO_JOIN`) | 3 | `Engine.answer` (curated_facts arm), `Combined.ask` (>1 unit) | yes |
| SQL gate (allowlist, declared joins, dry run) | 3 | `sqlguard.gate` | **`/ask` only**. **Built (#38):** every path; `/answer` in `Engine.plan_unit`, the agent's model SQL with the figure rule too |
| Purpose + readable unit filter on chunks | 4 | `Graph.retrieve`, `Combined.sources` (`$purpose IN c.purposes`, `d.unit IN $units`) | yes |
| Privacy gate (no embedding) | pipeline, read at 4 | `kge/pipeline/tagger.py:derive_readiness` → `c.embedding` null; `withheld` in `sources` | yes |
| Certified-only citation | 4 | shelf checks in `cite` / `sources` | yes |
| Numbers-in-text guard | 6 | `Combined.phrase` / `allowed_numbers` | `/ask` only. **Built (#38):** the agent too (`Agent.render`) |
| Tag 1 / Tag 2 (ai_ready, tier) | — (built: 4) | **not implemented**. **Built (#38):** landed by `kge/estate/certify.py`, read by `kge/graph/load.py` (fail closed), applied at 4 by `Graph.retrieve(tiers=...)` and `Engine.cite_tiers` | the treatment (`citation_rule=tiers`); the presets and `/ask` cite by shelf |

**Proposed placement:**
- Access stays at the stage 1→2 boundary, so the bundle is built only from readable sources. Join-time access is re-checked on the plan's actual sources before execution.
- The readiness gate is applied once, in stage 2 (`ContextBundle` contains only `ai_ready` assets, with tier attached).
- The citation policy (Tier `allows`) is enforced in stage 4 binding.
- The numeric guard runs in stage 6 for every renderer.

**Built (#38), in the agent:** the treatment's context holds only the sources the tier rule retrieves,
AI-ready and tagged with their tier, and names what was left out and why; the control's holds only
units the principal may read. Join-time access is checked on the units the model's plan reads, before
any query is gated or run, and on the treatment also on the units the question needs (those it names,
or else every unit its term is certified in), which its package names, so there is no partial figure. The citation policy is enforced when citations are bound (`Agent.cite`), and a reply that answers
from the sources with none left it may cite is an abstention (`Agent.execute`).
The number check runs on every model wording the agent uses.

## 3. Minimal stage interface contract (platform repo)

All stages are pure functions of `(input, GraphModel, Principal, RunConfig)`.
`RunConfig` carries `arm` (per-stage toggles), `model` and `planner` ids. Every
output carries `trace[]`, `versions{}` and `timings_ms`.

```
QueryInput ─► [1 resolve] ─► ResolvedQuery ─► [2 context] ─► ContextBundle
          ─► [3 plan/validate/execute] ─► ExecutedPlan
          ─► [4 retrieve/bind] ─► BoundEvidence
          ─► [5 synthesise] ─► Answer ─► [6 render] ─► Response
```

| Type | Fields (minimal) | Replaces |
|---|---|---|
| **QueryInput** | `question`, `principal`, `purpose`, `run_config{arm, model, planner, stage_toggles}` | `Ask` body in `kge/app.py` |
| **ResolvedQuery** | `intent` enum[figure, cohort_measures, combine_sources, definition, lookup_restricted, unknown]; `terms[{term_id, span, via: rule\|alias\|model, confidence}]`; `sources[]`, `scope: explicit\|all`; `period{kind, value, calendar_id}`; `predicates[{column_id, op, value}]`; `aggregation`; `candidates[]` (unknown phrases for the steward); `refusal?` (policy id) | `Request` + `special` + `role()` |
| **ContextBundle** | `sql{per source: allowlist(columns, joins, period columns), measures[{measure_id, term_id, column_ids, aggregation, grain, currency, calendar, conversion, tier}], join_paths, no_join[]}`; `docs{definitions[{doc_id, term_id, tier, superseded_by}], mentions[]}`; `access{readable_sources, denied_sources}`; `excluded[{asset_id, why: not_ai_ready\|tier\|access\|purpose}]` | `implementations`, `build_allowlist`, `key_of`, scattered graph calls |
| **ExecutedPlan** | `parts[{source, sql, sql_source: planner\|model, gate{passed, problems, checks}, attempts[], join_path, value(s), rows, latency_ms}]`; `combination{method: single\|sum\|parts, commensurable, reasons, policy_id}`; `status: ok\|abstain\|refuse` | `plan_unit` dicts, `Combined.plan/generate`, `commensurable` |
| **BoundEvidence** | `citations[{n, doc_id, chunk_id, tier, allows, excerpt, bound_to: [figure_id \| term_id], why}]`; `shown_not_used[{doc_id, why: superseded\|uncertified\|bronze}]`; `withheld[{doc_id, why: gate_level\|purpose\|access}]`; `retrieval{index_returned, post_filter_lost, embedding_model}` | `Combined.sources`, `Engine.cite`, `Graph.retrieve` |
| **Answer** | `behaviour` (one shared enum: the current union), `figures[{id, measure_id, value, unit, currency, cite_ns}]`, `parts`, `verdict`, `reason`, `policy_refs[]`, `evidence` (BoundEvidence), `plan` (ExecutedPlan ref) | the two different dicts from `/answer` and `/ask` |
| **Response** | `text`, `rendered_by: template\|model:<id>`, `guard{stray_numbers, bad_markers}`, `blocks[]` (structured render for UI/agents), `locale` | `template`/`phrase` + UI JS |

**A/B per stage.** `run_config.stage_toggles` replaces `ARMS` booleans:
- Stage 1: `{rules, rules+model, model}`.
- Stage 2: `{schema, catalog, graph}` (which layers feed the bundle).
- Stage 3: `{planner, model, planner→model}`.
- Stage 4: `{off, shelf, tier}`.
- Stage 6: `{template, model}`.

The eval harness compares any two `RunConfig`s on the same golden set, scoring
`Answer` (unchanged scorer) plus `timings_ms` and `gate` outcomes.

**Built (#38):** the demo's `RunConfig` is these toggles, one field per stage (§4). The seven types
and the pure stage functions stay planned: answers are still flat dicts, with the run trace beside
them. The harness (`kge/eval/harness.py`) runs five named configs, the three presets and the agent's two
arms, rather than any two; the golden scorer is unchanged, the measures beside it are in
`kge/eval/metrics.py`, and the timings and gate outcomes come from the run trace.

## 4. The A/B as built in the demo (issue #38)

What the demo runs for issue #38's A/B, graph off against graph on, on its sample text-to-SQL use
case. The agent that writes the SQL is ours, built for this test (`kge/services/ask/engine/agent.py`); the graph only
provides it context. The demo README's [reference agent](../../../../demos/kge/README.md#the-reference-agent),
[run config](../../../../demos/kge/README.md#the-run-config) and [run trace](../../../../demos/kge/README.md#the-run-trace)
sections have the commands.

### Arms

| Arm | Run config (`AGENT_ARMS` in `kge/services/ask/engine/config.py`) | What the agent is given before the merge point |
|---|---|---|
| `control`, graph off | resolution `words`, context `schema`, sql_source `planner`, citation_rule `retrieval`, renderer `template` | `Agent.context`, its control steps (`Agent.schema`, `Agent.search`): the scope (the units the principal may read); each readable warehouse's schema from its own catalogue (tables, columns and types, primary and foreign keys); a plain vector search over chunks (`Graph.retrieve`, top 5) with no shelf or tier filter. No term resolution |
| `treatment`, graph on | resolution `glossary`, context `graph`, sql_source `planner`, citation_rule `tiers`, renderer `template` | `Agent.context`, its treatment steps (`kge/services/ask/engine/context.py`), the graph's context package: each term with its definition, steward and certified columns, and the units the question names that it is not certified in; the columns by role (measure, filter, period, keys); the declared joins of each unit in play; the no-join pairs and whether parts may be added; the scope; for one customer group with figures over it, `/ask`'s shape, the graph's decision, which gives no figure and which the code holds whatever the reply (`Agent.group`); what not to use (restricted columns, definitions not in force for the period, documents not AI-ready); the sources by tier, gold and silver citable and bronze for reference only; provenance (the certification policy, the graph model hash) |

These are the configs on rules. With a model configured, `Agent.config` sets `sql_source=model` and
`renderer=template+model` on both arms, and `resolution=none` on the control, whose model reads the
question raw; the label then names what changed, for example
`control[resolution=none,sql_source=model,renderer=template+model]`.

The three arms of v0.1, `schema`, `catalog` and `graph`, are kept flag for flag as presets of the
same run config (`PRESETS`; `ARMS` is an alias). They stay the rule-based baseline behind `/answer`,
as the owner decided on #38 (§5), and the harness runs them beside the agent's two arms.

### The merge point

Before it, the arms differ only in what they are given: resolution and context, the sources the
context offers to cite among it. From it on, both run the same code. With a model, or the mock:

1. **Prompt.** One versioned template per task (`PROMPTS`): `agent-sql-v3` plans from the question and the context, `agent-phrase-v1` words the answer from the figures and citations. Since #39, `agent-sql-v3` lets a query read two units through a declared certified join, each table named with its unit, and adds `abstain_with_reason`. The two arms' first SQL prompts differ only in the block between `<context>` and `</context>`: the run trace records, for each SQL call, a hash of the prompt less that block and a hash of the block, and on the first call the first hash is the same in both arms. A retry appends the gate's reasons after the question, outside the block, so its hash matches across arms only when both arms were refused for the same reasons.
2. **Contract.** `read_reply` checks the reply, `{behaviour, sql: {unit: query}, citations: [n], answer_text}`. One that is not a JSON object, or breaks the contract, is an abstention, and nothing runs.
3. **Access.** `Graph.join_access` on the units the plan reads, before anything is gated.
4. **Gate.** `sqlguard.gate` on every query with the figure rule (`figure=True`), one retry carrying the gate's reasons, then an abstention. The same code in both arms; what it admits is the arm's context, the schema (`schema_allowlist`) or the certified columns (`build_allowlist`), and restricted columns in neither.
5. **Execute and bind.** The queries run on their units' warehouses. `Agent.cite` keeps only sources the context numbered and may cite, and `bind` ties each figure to its own definition (Stage 4 above).
6. **Render.** `Agent.render`: the template always, the model's wording only if it passes the number check, which the run trace records as `number_check`.

On rules, with no model configured, the planner reads the arm's run config, so context reaches into
planning (Stage 2 above) and the shared code starts at the gate. Both arms plan and render through
`Engine.answer` and pass the same gate in `Engine.plan_unit`; each planned figure then goes through the
same evidence step (`Agent.evidence`: the arm's context, built as a model would be given it, then
`Agent.cite` and `bind`). There is no model wording, so `number_check` is null. Such a run is labelled
`rules` (`sql_source` planner, renderer template) and a mock run `mock`; neither is ever presented as a
model's.

### Run config

`RunConfig` (`kge/services/ask/engine/config.py`) is frozen: an arm and one value per stage, from `STAGES`, and the profile the process
serves. A value that is `model` or ends in `+model` has the model take part in that stage.

| Field | Values | v0.1 §3 toggle |
|---|---|---|
| `arm` | A preset, `schema`, `catalog` or `graph`; or an agent arm, `control` or `treatment` | `arm` |
| `resolution` | `none`: no term mapping. `words`: none either; the planner guesses a column from the question's own words against the warehouse schema. `names`: the parser's alias list, then a column guessed by its name. `glossary`: glossary terms and the alias list, mapped to certified columns. `glossary+model`: and the model may add glossary terms the rules missed | Stage 1, `{rules, rules+model, model}` |
| `context` | `schema`: the warehouse catalogue, and the gate admits the schema. `graph`: join paths, measure metadata, the no-join facts and routing a candidate term to the steward, and the gate admits certified columns | Stage 2, `{schema, catalog, graph}`; v0.1's `catalog` is `resolution=glossary` on `context=schema` |
| `sql_source` | `planner`: rules write the SQL. `planner+model`: the model writes it when the planner cannot (`/ask`). `model`: the agent's model writes every query. Each through the same gate | Stage 3, `{planner, model, planner→model}` |
| `citation_rule` | `retrieval`: a definition cites what retrieval returns. `shelves`: the certified, current shelf is cited, the rest shown and not used. `tiers`: the tier rule (Stage 4 above) | Stage 4, `{off, shelf, tier}` |
| `renderer` | `template`: the structured result or its template. `template+model`: the model phrases it, and wording with a number not in the result is not used | Stage 6, `{template, model}` |
| `profile` | The profile the process serves, fixed by the process at deployment: `PROFILE`, else the registry's default (`deploy/profiles.yaml`), resolved once when the process starts, and never per request: it is no stage, and a `stages` override that names it is a 400. The run trace and every A/B run's manifest record it, and a baseline of another profile's is put beside nothing | none; issue #39 |

**Validation.** `RunConfig.problems(path, has_model)` says why a config cannot run on a path
(`answer`, `ask` or `agent`), before anything is asked:
- a value its stage does not take, or an arm the path does not know;
- a model stage with no model configured: refused, never answered by rules instead;
- a model stage on `/answer`, which is the rule-based baseline;
- `sql_source=model` anywhere but the agent, and `planner+model` on the agent;
- `context=graph` without `resolution=glossary`, since its gate admits certified columns only;
- on `/ask`, anything but `context=graph` and `citation_rule=shelves`.

`/answer` and `/ask` take an arm (a preset) and an optional `stages` override (`Stages` in
`kge/app.py`, which refuses an unknown field with a 422); a config that cannot run is a 400 listing
its problems. A config that differs from its arm is labelled `arm[k=v,...]`, in stage order
(`RunConfig.label`). `GET /presets` lists the presets, the values each stage takes, the paths each
preset runs on, and the agent's arms with what runs them; `/agent` takes `control` or `treatment`.
Unlike v0.1 §3, `model`, `planner` and `seed` are not fields: the model and its seed come from the
environment (`MODEL_PROVIDER`, `MODEL_NAME`, `MODEL_SEED`), and the run trace records them.

### Run trace

Every `/answer`, `/ask` and `/agent` response carries `run_trace` (`kge/services/ask/engine/runtrace.py`), and the
harness keeps one in each record. It holds ids, hashes, timings and counts, never chunk text or a
prompt, and lives in a `ContextVar`, so two requests served at once never share one.

| Field | What it records |
|---|---|
| `run_id`, `path`, `started_at` | The run, its endpoint (`answer`, `ask` or `agent`) and when it started |
| `profile` | The profile the process serves, chosen at deployment and never per request: `PROFILE`, else the registry's default (`deploy/profiles.yaml`; `meridian` in the demo). The run config carries it, set by the process |
| `graph` | `model`, the graph model hash: a sha256 of the graph schema's DDL (`kge/graph/schema.py`) and its curated layers in a fixed order, the certification each document landed with among them (`fingerprint`). `legacy_model`: the same over the DDL and layers as they were before issue #39, which the A/B baselines compare. `artifact`: the model hash of the compiled artifact the graph was loaded from. `overlay`: a sha256 of what the pipeline and the stewards added at run time (chunks, tags, decisions, mentions, proposals). `store`: the graph store's engine and version |
| `llm` | The model's identity: provider, model, temperature 0, seed (`MODEL_SEED`, default 7, where the provider takes one), the mock's mode on the mock, and a sha256 of those settings, never the key. Null when no model ran |
| `config` | The run config the answer ran on, with its label |
| `agent` | For `/agent`: the arm, what ran it (`rules`, `mock` or `model`) and the prompt versions. Null otherwise |
| `stages`, `total_ms`, `tokens` | Milliseconds, model calls and tokens per stage (`understand`, `resolve`, `access`, `plan`, `gate`, `execute`, `retrieve`, `bind`, `judge`, `render`), and in all |
| `llm_calls` | Each model call: its stage, the model asked and the model that served, tokens in and out, milliseconds; for the agent, the prompt's version and its hashes: for an SQL call, of the prompt less its context block (`prompt_hash`) and of the block (`context_hash`); for the phrase call, which has no context block, of the prompt alone |
| `retrieved` | Each chunk read: document, chunk, how it was found, its document's tier and AI readiness as landed, score |
| `citations`, `bound` | Each citation by number, document and chunk; the document and chunk each figure is bound to |
| `number_check` | Whether model wording passed the number check, with how many numbers not in the result and `[n]` markers naming no citation it threw out, and, on the agent, documents named that its context did not give or withheld (`bad_documents`); null when no model wording was checked |

The harness keeps the run trace in each record (`records.jsonl` in the profile's runs, `build/stores/<client>/run/runs/<run_id>/`), and its
manifest names the profile, the graph's hashes and the model's identity for the whole run. The
A/B margin and its check after a graph load are in
[implementation-plan.md §6](implementation-plan.md#6-scoring-and-reporting).

## 5. Open points
- Whether `/answer` should be retired, or kept as the "rule planner" stage-3 implementation behind the same contract. Proposal: keep it as an implementation, so the eval stays comparable. **Decided (owner, #38):** kept as the rule-based baseline. The three presets run unchanged behind the run config, and the agent's rule-based fallback runs on the same engine (`Engine.answer`).
- Where intent taxonomy extensions live. Proposal: platform enum; packs can't add intents, only aliases that map to them.
- Graph store concurrency: the single-process lock (Kuzu's then, Ladybug's now) means stages can't run against a model being reloaded (pack-contract conflict 19). **Built (#38):** the harness and `make margin` open the graph in-process, so the API is stopped while they run; each says so when it finds the graph locked. **Built (#39):** a model is no longer reloaded in place: each compiled artifact loads into a graph of its own beside the one served (`make load`), and a release (`make release`) takes effect when the API restarts.
