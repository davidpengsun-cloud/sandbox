# Generic Ask planner

| Document control | Value |
|---|---|
| Status | Approved by the owner and implemented in #316 (PR #318). The answers that moved, and the decision behind each, are in [the answer changes](generic-planner-answer-changes.md) |
| Requirement | Issue #316, including #313's no-silent-drop requirement |
| Basis | Code and pack declarations in this worktree; the supplied gap report checked against those files |
| Meta-model | 1.4.0, additive: `Alias.sources` lets a pack declare a source set such as "bank" (owner-approved 2026-11-06). Packs on 1.3.0 still load |
| Dependency | Start implementation after #276 merges if it changes any of the same files; preserve its scope contract |
| Acceptance boundary | Offline codebase tests; the GCP agent owns live acceptance after the KGE kit publishes |

The model fills a plan describing what to compute. A deterministic check resolves the plan against the released pack and the person's access. A deterministic compiler renders the checked plan, and the existing SQL gate checks every warehouse statement before execution. The model supplies no SQL, expressions, joins or policy predicates.

This document proposes runtime types, validation and compilation, rather than a larger rules parser. All paths below are relative to `demos/kge/` unless labelled repository paths. File and line citations describe the current code, not proposed modules. The worked requests are new illustrations using pack declarations; they are not the owner's external question set.

Expected parity moves are limited to A1 cases: incomplete interpretations that currently return figures become no-figure clarification or abstention. All other existing golden answers remain unchanged. Pin parity, margin and golden runs to `MODEL_PROVIDER=none`; use recorded mock plans for the model-contract and capability tests. Portal UI changes are out of scope; this design changes the KGE API and KGE `ui/` only.

## Today

| Responsibility | Current implementation and verified reference | Consequence |
|---|---|---|
| HTTP and MCP bodies | `kge/services/ask/models.py:42`, `kge/services/ask/routes.py`, `kge/services/ask/mcp.py`; capability logic in `kge/services/ask/service.py` | Ask has question, purpose, arm, stages and products; Agent has question, purpose and arm. Neither carries conversation history |
| Ask routing | `kge/services/ask/engine/combined.py:179`, `:203`, `:210`, `:260` | Parse first; an unreadable range or a non-cohort shape goes back to `Engine.answer`. The model can add terms or repair an unread period, not fill a general plan |
| Mock exclusion | `kge/services/ask/engine/combined.py:168`; `kge/services/assist/service.py:167` | Ask removes the mock; Assist's routing model removes it too. Today's mock tests do not exercise a model-produced general Ask plan |
| Parsed request | `kge/services/ask/engine/question.py:511`; `kge/semantics/query.py:156` | `Asked` carries a period object, time mode, latest flag, lists of grouping columns, units, restrictions, terms and filters, one entity/id pair, one counted entity and one aggregation |
| Actual grouping | `kge/services/ask/engine/question.py:569`; `kge/semantics/query.py:683` | Although `Asked.group_by` is a list, parsing truncates it and planning stops at the first reachable column |
| Context selection | `kge/services/ask/engine/core.py:883`, `:891`; `kge/semantics/query.py:213` | Select the first context. `Plan` holds one context, one select expression, one grouping expression, join strings, predicates, allowlist and an optional decision |
| Measure versus count | `kge/semantics/query.py:696` | A distinct entity count replaces the context's measure. It cannot accompany that measure |
| Missing structure | `Asked` and `Plan`, above; result decoding at `kge/services/ask/engine/core.py:928` | No measure list, dimension list with roles and levels, range/YTD, comparisons, derived values, ranking, window specification or previous-plan edit |
| Temporal joins | `kge/semantics/query.py:278`, `:313`, `:817` | `add_join` renders the declared condition and then adds `version()` unconditionally for an SCD table. The allowlist also prepares uniform time-mode windows. A join's own dating can therefore conflict with the added window |
| Path choice | `kge/semantics/query.py:290`, `:353` | The first path to a physical table wins; aliased self-joins are excluded from reachability. Two business roles for the same table cannot be distinguished reliably |
| Release and execution | `kge/services/ask/engine/core.py:844`, `:860`, `:912` | Gate and execute a release lookup, then gate and execute the answer; an unreleased or unbooked requested month stops the answer |
| Assist | `kge/services/assist/service.py:218`, `:247` | Routes to catalogue, search or Ask; it has no previous-plan input |
| Agent | `kge/services/ask/engine/agent.py:418`, `:457`, `:1018` | A loop over context, planning, gate and rendering. Its configured model currently returns SQL; its rules mode uses the engine |

### The silent-drop path

The parser collects glossary matches and selects the first term at `question.py:532–544`. It can keep a recognized period while losing the rest of a request's shape. A grouping list becomes one column; the context path selects one context; a count becomes the only select expression. Finally, `query.py:715–721` tests other glossary terms using the words of applied columns and tables. `query.py:737–747` additionally checks unaccounted ALL-CAPS codes. Neither guard accounts for all lower-case modifiers or operators. The context path can consequently return a validly gated total for an incomplete interpretation.

This is separate from SQL safety: an allowed query can answer the wrong question. Fixing it requires a complete interpretation before compilation, including on the rules path. The existing note at `query.py:630`, “period ignored”, must also become a failed plan check whenever the question specifies that period.

## The proposed plan

### Types and ownership

Use existing Pydantic support for immutable, strict models in a proposed `kge/semantics/plan.py`. All records forbid extra fields. References are opaque identifiers from the vocabulary bundle, never snippets of SQL. Dates use ISO dates; decimal values use canonical decimal strings rather than binary floating-point input. The following algebra is the normative model shape; `list[T]`, tagged unions and nullable fields translate directly to Pydantic and generated JSON Schema. Every field shown is required; an empty list or explicit null expresses absence.

```text
Id = string
Date = ISO-date string
Decimal = finite decimal string
Scalar = {kind: text, value: string} | {kind: boolean, value: boolean}
  | {kind: integer, value: integer} | {kind: decimal, value: Decimal}
  | {kind: date, value: Date} | {kind: null}
Mode = current | as_reported
Grain = day | month | calendar_quarter | fiscal_quarter | year | fiscal_year

Span = {turn: Id, start: integer, end: integer}
Name = {id: Id, evidence: list[Span]}
Period = {kind: Grain, value: string, calendar: Name}
Time =
  {kind: single, period: Period}
  | {kind: range, first: Period, last: Period}
  | {kind: ytd, through: Period, calendar: Name}
  | {kind: latest_released, calendar: Name}
  | {kind: all_available, calendar: Name}

DimensionBinding = {context: Name, column: Name, path: list[Name],
  level: integer | null}
Dimension =
  {kind: column, id: Id, bindings: list[DimensionBinding]}
  | {kind: hierarchy, id: Id, bindings: list[DimensionBinding]}
  | {kind: time, id: Id, calendar: Name, grain: Grain}

Measure =
  {kind: measure, id: Id, measure: Name, context: Name,
   mode: Mode, filters: list[Predicate]}
  | {kind: distinct_count, id: Id, entity: Name, context: Name,
     mode: Mode, filters: list[Predicate]}

Operand = {kind: column, name: Name, path: list[Name]}
  | {kind: output, id: Id}
Predicate =
  {kind: compare, id: Id, left: Operand,
   op: eq | ne | lt | le | gt | ge, value: Scalar, evidence: list[Span]}
  | {kind: in, id: Id, left: Operand,
     values: list[Scalar], evidence: list[Span]}
  | {kind: null, id: Id, left: Operand,
     is_null: boolean, evidence: list[Span]}
  | {kind: all | any, id: Id, children: list[Predicate], evidence: list[Span]}
  | {kind: not, id: Id, child: Predicate, evidence: list[Span]}
  | {kind: membership, id: Id, entity: Name, relation: Name,
     context: Name, filters: list[Predicate], evidence: list[Span]}

Comparison =
  {kind: time_mode, id: Id, inputs: list[Id], base: Mode,
   other: Mode, evidence: list[Span]}
  | {kind: previous_period | previous_year, id: Id,
     inputs: list[Id], evidence: list[Span]}
  | {kind: explicit_period, id: Id, inputs: list[Id],
     other: Time, evidence: list[Span]}
  | {kind: contexts, id: Id, left: Id, right: Id, evidence: list[Span]}

Derived =
  {kind: ratio | percent_change, id: Id,
   numerator: Id, denominator: Id, evidence: list[Span]}
  | {kind: share, id: Id, input: Id, remove_dimensions: list[Id],
     remove_filters: list[Id], evidence: list[Span]}
  | {kind: running_total, id: Id, input: Id,
     partition_by: list[Id], order_by: Id, evidence: list[Span]}

Order = {output: Id, direction: asc | desc, nulls: last, evidence: list[Span]}
Obligation = {id: Id, spans: list[Span],
  kind: name | filter | time | mode | dimension | count | comparison
        | derived | order | limit | membership,
  targets: list[Id], state: applied | unresolved | ambiguous}

QueryPlan = {schema: query-plan/v1, layout: aligned | separate,
  measures: list[Measure],
  dimensions: list[Dimension], filters: list[Predicate], time: Time,
  comparisons: list[Comparison], derived: list[Derived],
  order: list[Order], limit: integer | null, obligations: list[Obligation]}
```

`Scalar` always uses the tagged records above; no scalar is interpreted as an expression. Dimension and output ids belong to this plan. Names for contexts, measures, entities, columns, paths, relations and calendars belong to the vocabulary bundle. Time-comparison output ids are deterministically namespaced as `<comparison-id>/<input-id>/base` and `/other`; a context comparison exposes `<comparison-id>/base` and `/other` from its left and right measure outputs. A derived value may reference those or previous derived outputs. Cycles and missing outputs fail validation. Obligation targets name node ids, or the reserved slots `time`, `limit` and `measure-id/mode`; all must resolve. A limit needs its own obligation evidence even though it is a scalar field.

Period values have a validated form per kind: an ISO date for day, year-month for month, a calendar year and quarter for calendar quarter, and the declared calendar's year/quarter labels for fiscal periods. Fiscal labels resolve with the existing calendar convention and start month; a fiscal-year label is not assumed to name its starting calendar year. Dates and period labels are never inferred from identifiers or read as arbitrary expressions. `all`/`any` predicates have nonempty children, `in` has nonempty values, and ordered limits and hierarchy levels are positive integers. Every plan has a measure or count output; a derived-only plan is invalid.

The global predicates apply to every selected measure; local predicates define an individual measure's population. Applying a global predicate to only one compatible branch is forbidden. Column operands are legal before aggregation; output operands are legal after aggregation. A boolean predicate cannot mix those phases. Thresholds on aggregates therefore compile after grouping. Counts use every component of the entity's declared key; they do not replace a sum or default to counting rows.

Hierarchy level counts the hierarchy joins from the selected leaf role, not a guessed substring in a column name. Each hierarchy binding's positive `level` must agree with its declared joins and terminal column; a column binding has null level. A flattened level column is simply a column dimension. `path` distinguishes roles such as an account's office and an advisor's office, even when both end at the same table. Bindings give each participating context its own path to the same dimension: a flow and a snapshot need different paths to the same region. The checker proves those roles are semantically aligned; it never joins on equal-looking display text. Time dimensions use the measure's period binding and calendar rather than a model-selected date column.

`layout=aligned` requires every dimension to bind every participating context and produces one shared result table. `layout=separate` is valid only for an explicit request for independent figures; it produces a labelled panel per context with its applicable dimensions. A dimension intended to apply to every panel cannot be omitted where a path is missing. Comparisons or arithmetic across panels still require a checked relationship and compatible grain; a separate layout is no workaround for no-join policy. The model cannot change layout merely to salvage part of a request.

`time` is never silently absent. `all_available` is explicit in the checked plan and answer text, retaining today's valid unqualified totals. An unread date, a missing year on a written month, or a requested time grain unsupported by the binding cannot become `all_available`. YTD starts at the selected calendar's year boundary and ends at the specified period; fiscal YTD uses that calendar's start month. A range has inclusive endpoints at one grain on one calendar. Day predicates compile as a half-open interval ending on the following day; monthly release checks still cover every intersected required reporting month.

`CheckedPlan`, in a proposed `kge/semantics/plancheck.py`, is an internal frozen record. It contains the normalized plan, resolved objects, selected role paths, output types/currencies, policy requirements, release requirements, coverage certificate and release/access/scope identity. Only the checker constructs it. The compiler accepts this type, never a model reply or an `Asked`. Policy clauses and SQL are absent from the model-facing schema.

### Conversation history

Add an optional `history` list to Ask, AgentAsk and Assist. Each item is `{turn_id, question, plan, clarification, binding}`: `plan` is a prior normalized `QueryPlan` or null; `clarification` is `{spans, slot, choices: [{id, label}]}` or null; and `binding` is `{release_identity, access_fingerprint, products, purpose}`. Each choice id names a candidate in the readable vocabulary. The caller sends history; no new conversation database, identity supplied by the caller, or server-side session setting is needed. A follow-up's newest applicable prior plan is the edit base. History and its claimed binding are untrusted input, not evidence that an access check passed.

The model reply is a tagged union:

```text
PlanReply =
  {kind: propose, base_turn: Id | null, plan: QueryPlan}
  | {kind: clarify, spans: list[Span], candidates: list[Name]}
  | {kind: abstain, spans: list[Span], missing: list[string]}
```

A follow-up proposes the full replacement plan with `base_turn` naming its checked antecedent; a new explicit question uses null. Preserve unchanged node ids and inherit their obligations with the original turn spans by node id. Compare the replacement with its base deterministically: each changed or removed node needs evidence in the follow-up, and removing an output also requires removing or replacing its dependent expressions. Recheck the whole plan. The model cannot implicitly drop a previous filter, broaden products, change purpose or select another principal. Ambiguous “instead” or “those” gets a clarification. There is no patch or separate edit union.

Requests and responses bind history to the current released artifact and access/scope fingerprint outside the model's plan. Recompute the binding from the current request and snapshot; never accept a claimed fingerprint as authorization. A changed release or grant requires re-resolution; a stale or now-inaccessible base is rejected without disclosing its contents. Store no SQL or result cells in history. Return a normalized plan only after redaction, along with a turn id and its binding. Bound history length and total text with shared constants and reject overflow rather than truncating it. The exact bounds are implementation limits, covered by contract tests, not new deployment settings.

Validate every history entry against the current pack and the person's access and scope, including entries not selected as the base and every clarification choice. Sanitize all question text, names, labels, choices and plans before disclosing any history to the model. A claimed binding never exempts an entry from these checks; invalid or inaccessible content stops disclosure even when that entry would otherwise be unused.

## Worked capability examples

### Example basis

The notation below abbreviates the exact types above: `M` is a measure output, `C` a distinct-count output, `D` a dimension, `T` the time selection and `F` a predicate. Each row specifies the requested interpretation and expected compilation or decision; it claims no measured answer value. Periods and ranking limits are illustrative inputs.

| Example input | Meridian value | Thornbury value |
|---|---|---|
| Single month / comparison anchor | `2025-06` | `2025-06` |
| Range endpoints, inclusive | `2025-04` through `2025-06` | `2025-04` through `2025-06` |
| YTD calendar and endpoint | `fiscal_april`, through `2025-06`; start follows that calendar | `calendar`, through `2025-06`; start follows that calendar |
| Previous period | `2025-05` for a monthly flow; a snapshot comparison must use an available declared snapshot grain | `2025-05` |
| Previous year | `2024-06`; missing release/snapshot means abstain | `2024-06`; missing observations remain unknown |
| Illustrative numeric threshold | `{kind: decimal, value: "100000"}` | `{kind: decimal, value: "1000"}` |
| Illustrative rank limit | 3 | 2 |
| Direction | `desc` for top; `asc` for bottom | `desc` for top; `asc` for bottom |

These are plan inputs, not fixture totals. A period absent from an existing fixture is a negative case; positive arithmetic tests may add synthetic rows in their isolated test fixture without changing a released pack or golden answer. In the following shorthand, a dimension's stated path becomes its context-specific `DimensionBinding`; a separate-figures request sets `layout=separate`, otherwise it uses `aligned`.

| Pack | Existing declarations used | Boundary |
|---|---|---|
| Meridian | `aum`, `nna`, `nna_client_directed`; `wealth.assets-under-management`, `wealth.net-new-assets`; `wealth_household`, `wealth_account`; the office/region/division joins and `segment_parent`; `wealth-month-end-release` | Present contexts support these roles, but not every possible context or path. In particular, `sage_account_office` is declared globally and omitted from current context reaches |
| Thornbury | `gross-written-premium`, `paid-loss`; `underwriting.premium`, `underwriting.policy`, `underwriting.insured`; `claims.payment`, `claims.claim`; calendar and event-date bindings | Its current pack has no authored contexts, entities, hierarchy joins, SCD policy or release driver. Its `no_join` explicitly separates underwriting and claims |

For a legacy measure without a context, normalize a fact-only context in memory from the certified measure, its value columns and period binding. This introduces no new business rule. A measure expression spanning tables needs authored context joins; harvested foreign keys remain candidates, never executable authority. This makes Thornbury's fact-only totals and temporal calculations positive cases without editing its pack.

For tests of joined dimensions and counts on Thornbury, construct an in-memory pack variant over its existing locked tables: declare the premium-to-policy and policy-to-insured joins using their complete foreign-key equalities, a context for Gross Written Premium reaching them, and entities keyed by `policy_no` and `insured_id`. Where a hierarchy example uses the policy-to-insured rollup, explicitly author that join as a hierarchy; do not infer a hierarchy from its foreign key. Call these **declared variants** below. Their identifiers can use existing qualified table names. They are test data, not proposed changes to the shipped pack. Do not turn a `no_join` into a join. Positive SCD/release tests use separate synthetic version rows and policies; the unmodified Thornbury pack must continue to abstain on certified-time requests.

### Measures, dimensions, periods and joins

| Capability | Meridian worked interpretation | Thornbury worked interpretation |
|---|---|---|
| General checked plan | Assets under management at a June quarter end: `M=wealth.assets-under-management/context=aum`, `T=single month`; checker resolves the release policy, compiler uses its fact and calendar join | Gross Written Premium for a June month: `M=gross-written-premium`, normalized fact-only context, `T=single month`; filter `underwriting.premium.booked_on` using its event binding |
| Several measures | Put `nna` and `nna_client_directed` in separate measure outputs at the same period. Preserve each context's filters; aggregate branches independently before aligning them | Put Gross Written Premium and Paid Loss in one request. Return `parts_with_reason` as independent panels, each with its own time binding; do not add them or imply a join. A ratio between them abstains under the pack's no-join assertion |
| Several dimensions | `M=aum`; `D=wealth.RGN_DIM_1.RGN_CD` via household/advisor/office/region, plus month grain; group at both dimensions | Declared variant: `M=gross-written-premium`; `D=underwriting.policy.line_code` and `underwriting.insured.segment_code` via the declared paths. The shipped pack abstains naming the missing declared path |
| Hierarchy level | `M=aum`; `D=wealth.DIVSN_DIM_1.DIVSN_CD` via `aum_household`, `household_advisor`, `advisor_office`, `office_region`, `region_division`; level agrees with the hierarchy portion of this path | Declared variant: `M=gross-written-premium`; roll from policy to insured using the declared relationship, then group `underwriting.insured.segment_code`. Requesting an additional hierarchy level abstains because none is declared; this is not a hierarchy inferred from table names |
| Aliased hierarchy | `M=nna`; dimension on the `parent_segment` role via `segment_parent`. The alias is a distinct path node, not an already-visited physical table | Ask for a parent of `underwriting.insured.segment_code`: abstain, naming the absent hierarchy. The pack has only that segment field |
| Time grain and range | `M=nna_client_directed`; month dimension; `T=range` across released spring months. Compile all requested months, never just an endpoint | `M=paid-loss`; month dimension from `claims.payment.paid_on`; `T=range` across the same calendar months. Group the event dates by month |
| YTD | `M=nna_client_directed`; `T=ytd` through a released month on its declared fiscal or calendar year, stated explicitly in the plan | `M=gross-written-premium`; `T=ytd` through a named month on `calendar`; start at the calendar's year boundary |
| Single period and snapshot | `M=aum`; a quarter-end reporting month selects the snapshot, rather than summing multiple snapshots over a quarter | `M=gross-written-premium`; one month sums booked events. Asking for a snapshot of this event measure requires clarification |
| Per-join dating | Declared temporal test variant: fact read at the release date; `flow_account` condition includes account validity on `last_day(TRAN_FACT_1.DW_TRAN_DT)`; office condition declares current. Each role keeps that condition, without a second time-mode predicate | Current pack: event-date binding only, no SCD window invented. A current-versus-certified request abstains because no freeze/release rule exists |
| Distinct count beside measure | `M=aum` plus `C=wealth_household/context=aum`; count distinct `HSHLD_DK` alongside the measure. `wealth_account` counts use the complete composite key when reachable | Declared variant: Gross Written Premium plus distinct `underwriting.policy` by `policy_no`, through its declared join. Count and premium remain separate outputs |
| Numeric threshold | `M=aum`, region dimension, output predicate `aum > threshold`; apply after aggregation, then rank | `M=gross-written-premium`; row predicate `written_premium_usd > threshold` applies before aggregation. If the request instead thresholds the total, use an output predicate after aggregation |
| Top and bottom N | `M=aum`, region dimension; descending measure order for top, ascending for bottom; positive limit; dimension keys break ties deterministically | Declared variant: Gross Written Premium by `line_code`, the same order and limit rule. The shipped pack abstains on the missing path rather than ranking a different field |

### Comparisons, derived values and follow-ups

| Capability | Meridian worked interpretation | Thornbury worked interpretation |
|---|---|---|
| Current versus certified | “Certified” has no declared time-mode alias or certified-reference context in the current pack: clarify its meaning, with no figure. If the person chooses the declared “as reported” meaning, `M=aum` expands current and as-reported branches over the same released quarter end. If a declared variant supplies separate current/reference contexts, use `Comparison.contexts`, never equate certification to a time mode by English convention | Gross Written Premium with the same comparison: abstain, naming the missing release/freeze policy or certified-reference context. Never substitute the event date for certification. A declared context comparison can instead compare the existing Gross Written Premium and Paid Loss as separate figures, while preserving their no-join boundary |
| Period over period | `M=nna_client_directed`; comparison `previous_period`; return current and preceding calendar month after checking both releases | `M=gross-written-premium`; compare the selected calendar month with its predecessor using `booked_on` |
| Year over year | `M=aum`; `previous_year` compares corresponding quarter-end snapshots. If the prior period is not booked, abstain naming it | `M=paid-loss`; compare matching calendar periods using `paid_on`; an empty observed period is not silently assumed to be a certified zero |
| Percent change | Derive `percent_change` from a comparison's base and other outputs, with numerator meaning current and denominator meaning baseline | Apply the identical typed operation to Gross Written Premium's current and previous-period outputs |
| Share of total | `M=aum` by region; `share` removes the region dimension for the denominator and retains all access, policy, time and other filters | Declared variant: Gross Written Premium by line; remove the line dimension only. A selected-line share of all lines must explicitly remove that user filter as well |
| Ratio | `M=nna_client_directed` divided by `M=nna`, compatible populations and currency, expressed as a typed ratio with a checked denominator | Declared variant: Gross Written Premium divided by distinct policies gives premium per policy. Paid Loss divided by Gross Written Premium abstains under `no_join`; “Loss Ratio” remains a candidate, not a certified measure |
| Running total | `M=nna_client_directed`, month dimension; accumulate in month order, partition by any retained region dimension | `M=paid-loss`, month dimension; accumulate paid events. No running total over a snapshot measure unless its pack declares that operation meaningful |
| Follow-up edit | Previous `aum` by region and month; “use division instead” replaces the region dimension and its path, retains time and filters, and rechecks the complete plan | Previous Gross Written Premium monthly total; “use the previous year instead” replaces time, retains the measure and predicates. A follow-up to a prior clarification resolves only the named ambiguous slot |
| Ambiguous words | “office” without an account/advisor role cannot choose between `sage_account_office` and `advisor_office` when both are permitted by a declared variant. Clarify the role | “line_code” without a measure or unit has candidates on `underwriting.policy` and `claims.claim`. Clarify the readable choice; do not choose the first column |
| Backend independence | The same checked `aum` plan feeds `AUTHORED` SQL to the warehouse port; no engine dialect branch | The same compiler and port handle Gross Written Premium. Changing the pack or warehouse selects data and adapters, not a separate planner |
| Job ceiling | Several measures and comparison branches stay inside one answer statement after one batched release lookup | Fact-only Gross Written Premium needs one answer statement. Missing declarations stop before warehouse execution; variants retain a fixed query count |

All dimensions, conditions, time periods and derived inputs are checked before any branch executes. A multi-panel response is allowed only when it completely answers a request for separate figures. An invalid requested relationship, ratio or comparison cannot be turned into independent figures as a partial answer.

B9's reproducible ambiguity test uses “office” or `line_code` with the declared test variants above. “Retail” as both a unit and a segment is an illustrative collision, not a reproducible case on the shipped Meridian pack.

## The plan check

### Resolution and access

Build an immutable vocabulary bundle from the existing per-release graph/domain snapshot. It contains the person's readable, product-scoped certified measures, contexts, entities, dimension columns, complete declared role paths, calendars, period bindings, value vocabularies, aliases and candidate terms. It describes permitted operations and grain/currency constraints. Pack definitions are data, never prompt instructions. Do not send unrestricted warehouse schemas, inaccessible alternatives, rows, private values or policy-protected columns to the model. Policy enforcement retains a private denial index so a restricted request is refused without adding those columns to the model's vocabulary.

Resolution collects candidates across aliases, certified term names, readable column/business names, entity labels and declared values. It does not flatten candidates into one dictionary entry. Collapse two names only when they resolve to the same semantic object and role. A source alias and a dimension value with the same words remain different candidates. Context, role and product scope can disambiguate only when the written request or a checked prior turn supplies them; ordinal and string length are not tie-breakers for meaning.

Preserve the existing `scope.check` refusal precedence (`kge/services/ask/engine/combined.py:203–206`): when a word denotes a readable value in one place and an unreadable unit in another, the unit-access refusal comes first, before ambiguity handling or model disclosure. Do not clarify with inaccessible alternatives.

Validate every name, filter value and operand against the bundle; every path against its context; every column against policy and product scope; every measure against certification; every involved unit against `join_access`; and every cross-unit relationship against certified joins and no-join facts. For undeclared members, do not query distinct values interactively: clarify or abstain. Numeric/date literals must match their column types and the words/numerals supplied. User-authored local predicates can narrow, never remove context policy predicates. Recheck purpose and all inherited history names on each request.

| Outcome | Rule | Wording |
|---|---|---|
| Clarify | More than one permitted meaning, or a specific missing choice the person can supply | Add `clarify` behavior and a typed clarification with the exact unresolved phrase, affected slot and readable alternatives. Ask a short question, such as “By ‘office’, do you mean the account's office or the advisor's office?” |
| Abstain | Missing measure, path, calendar, operation or declaration; no complete checked interpretation; unavailable requested period | Name the unapplied phrase and the missing capability. “I cannot apply ‘…’: the pack declares no …. No figure is given.” Route a candidate term to the steward using the existing behavior where appropriate |
| Refuse | Access or policy denies the requested read or relationship | Preserve existing refusal semantics and role-based next steps; do not reveal inaccessible candidate names |
| Execute | Every obligation resolves, every operation is legal and every requested result is representable | Produce a checked plan; no answer figure exists yet |

Use `wording.Hidden` for all new reason, choice and plan fields, not just `answer_text`. Clarification and abstention contain no result cells, SQL execution, citations invented by the model, or speculative numbers. Dates and choices supplied by the question may appear as inputs; they are not figures. Every next step names a role, never a person.

### No silently dropped words

Coverage is a prerequisite to a figure on **every** numeric path, including `/answer`, `Engine.plan_unit` (`kge/services/ask/engine/core.py:411`), `Engine.linked_plan` (`:934`) and its linked-count execution, the legacy cohort path and the Agent's rules mode. A refusal may stop earlier. A failed coverage check may never fall through to another numeric path.

The proposed `kge/semantics/coverage.py` builds an independent obligation ledger before rules or model planning. Retain raw turn text and offsets; normalization keeps an offset map. Match complete vocabulary phrases, dates, literals and the platform's closed operation grammar, including conjunctions, negation, comparison, count, ratio, denominator scope, ordering and limit. Do not use today's broad `COMMON` set to discard substantive words. Only narrowly specified question scaffolding and punctuation can be neutral. Every remaining span is unresolved. An unfamiliar lower-case modifier is as blocking as an unfamiliar code.

The rules interpreter emits both a candidate plan and its consumed spans. The model supplies evidence spans and target ids, but cannot classify arbitrary spans as harmless or self-certify coverage. The checker rebuilds name/operator/literal obligations, validates exact spans and grammatical operators against the proposed nodes, and requires every substantive span to have an applied target. It also checks reverse coverage: every plan node needs a written request, an inherited checked obligation, or an explicit documented default. A comparison must own both operands, a count its entity, and a share its denominator scope. A target pointing to a valid but unrelated measure does not discharge an obligation.

This is deliberately conservative. Arbitrary natural-language semantic equivalence is not provable from a model's JSON. New paraphrases without a grounded coverage mapping get a clarification or abstention, even if the model thinks it understood them. The guarantee is that no unchecked or unapplied obligation reaches execution; it is not a claim of infallible language understanding. Adversarial tests must try both omitted spans and falsely “applied” spans. The generic model expands the plan shapes, not permission to ignore text.

For A1, tests mutate simple known requests by adding each required component: another mode and a time grain; a share qualifier and denominator; another measure, dimension and count; opposing flow directions; a ratio and entity count; an undeclared planning baseline. On rules and the mock, each incomplete interpretation returns no figures and names the unresolved component. These cases must remain negative on the present pack where the complete requested meaning is undeclared. Fully declared positive variants exercise the same operators separately; recognizing an operator alone never authorizes its inputs.

## The compiler

### Relational construction

Place the runtime compiler in a proposed `kge/semantics/compile.py`, beside the check and types, not in the batch pack compiler. It consumes resolved pack objects and a `CheckedPlan`; it imports no HTTP or service models. It returns statements, per-statement allowlists, typed result columns and lineage back to each output. It executes nothing. Use the existing sqlglot dependency to build and transform ASTs rather than concatenate model text.

| Stage | Construction and invariant |
|---|---|
| Resolve periods | Materialize requested and comparison windows from calendars and bindings. Latest means latest common released period for aligned operands; incompatible calendars require an explicit alignment or abstention |
| Resolve roles | Build nodes keyed by declared path and alias, not just physical table. Keep complete composite join keys. Multiple possible roles require clarification |
| Base relations | One filtered relation per distinct context, population, time window and mode. Apply standing filters, selectors, restricted-column exclusions, release constraints and unit access before aggregation |
| Several measures | Combine measures in one aggregation only when fact, row population, joins and grain are identical. Otherwise aggregate in separate CTEs at the checked output dimensions, then align the aggregates. Never join raw facts to multiply measures |
| Alignment | Use a dimension spine from grouped keys and null-safe joins of aggregates. Shared dimension identity must be proved by identical declared roles or a declared mapping. Unrelated contexts become labelled panels only for a request for separate results |
| Counts | Count whole entity keys before alignment. Preserve distinctness across fact duplication and use `EXISTS` for membership. Null-key semantics are explicit; a missing component does not become a separate entity |
| Comparisons | Expand base/other branches at the same dimensional grain, including prior-year/period windows, current/as-reported modes and explicitly selected context pairs. Map prior-period and prior-year observations onto the base period keys before alignment: May's value aligns with June's key for a June month-over-month result. Retain each branch's source period for lineage. A certified reference is a separate context unless the pack declares it to mean an as-reported time mode. Check releases and joins for every branch before running any |
| Arithmetic | Preserve existing commensurability rules, separate parts and verdicts. `ratio = numerator / NULLIF(denominator, zero)`; `percent_change = (numerator - denominator) / NULLIF(denominator, zero)` with percentage formatting. Round only final presentation values. No general add or difference derived operation is introduced |
| Shares | Compile a second aggregate with the specified user dimensions/filters removed. Never remove access, context filters, selectors or release predicates. Use a window denominator only when it is equivalent to that checked population |
| Windows | Compute running totals after base aggregation over a time spine, partitioned by the checked dimensions with an explicit ordered unbounded-preceding-to-current-row frame. Alongside `SUM`, cumulatively count unknown required observations over the identical frame. Emit null whenever that count is nonzero, carrying the contributing null reasons through every affected cumulative value; `SUM` alone would skip nulls. Oracle: `[10, unknown, 5]` yields `[10, null, null]`; an observed zero instead yields `[10, 10, 15]` |
| Final selection | Apply aggregate thresholds at their checked stage, derived outputs, deterministic order and limit. Ranking defaults to exact N with complete dimension keys breaking ties; a request for “with ties” needs a supported explicit operation or clarification |

Do not sum a stock measure over successive snapshots. Use its declared snapshot/grain/population metadata and period binding to select the required snapshot within each output period. Reject a running total or other non-additive rollup unless the declaration permits it. Distinct counts are recomputed at the requested grouping grain, not added across groups or periods. Ratio checks include compatible currencies, time windows and populations; they cannot bypass no-join or commensurability policy. A missing prior observation and a zero denominator are separate cases and carry separate null reasons.

Comparison oracles must cover overlapping source ranges: for base May–July, prior-period April–June aligns April→May, May→June and June→July exactly once, retaining separate base/other values where source months overlap. Prior-year May–July aligns the previous year's matching months to those same base keys. A fiscal-calendar oracle crosses the declared year boundary and checks its period labels and predecessor mapping, rather than shifting display labels or assuming January starts the year. Explicit-period comparisons need a checked one-to-one mapping at the requested grain or abstain.

### Join dating and policies

Render each complete declared `Join.condition`. A key-only condition is not a declared temporal rule. Determine temporal authority **per role/table occurrence** from its AST: predicates on that role's SCD validity columns or current flag establish its date/current rule. When the join supplies that temporal constraint, add no conflicting time-mode predicate for that occurrence. A role with no such constraint receives the time mode's SCD policy: current flag for current; validity at the release freeze date for as-reported. The fact has no incoming join and follows its applicable policy. A table without an SCD policy receives no invented version filter.

The predicate recognizer must establish a complete supported temporal constraint, including open-ended and inclusive/exclusive validity. A partial bound, an unrecognized temporal expression or conflicting constraints is a pack/check error, never permission to omit dating. Calendar/release dependencies come before predicates that reference them. A declared condition may express month-end dating using the source fact's date, already permitted by the condition parser; it need not reference an unrelated third table. Apply the same rule inside selectors and cross-reference semi-joins, with aliases kept distinct.

For example, a declared account join can include validity at the last day of the flow's transaction date; an office join can require its current flag. In an as-reported branch the fact still uses the release date, but neither account nor office is additionally forced to that date. Today's key-only pack joins retain their present time-mode behavior; this change does not silently rewrite pack dating. Test variants establish mixed dating and alias-role cases.

Preserve all standing filters and include/exclude row selectors. Cross-unit entity membership uses only the certified cross-reference, its certification predicate, its declared dating and the far side's selectors. Use semi-joins so a matched fact counts once. A no-join assertion blocks a requested row/entity relationship. Aggregate-only combinations still use the existing commensurability rules: preserve currently permitted totals over independent units without inventing entity matches. Cross-context arithmetic that needs an undeclared common population or denominator basis abstains. In Thornbury, the no-join assertion prevents a policy/claim alignment and there is no certified Loss Ratio basis; separate panels cannot manufacture either. They never pretend populations are joined or commensurable.

### Release rule and SQL gate

Batch all distinct release-driver/window requirements in a single release statement, with tagged rows for each requirement. It checks missing and unreleased periods, including all comparison operands; the planner must not drop an unavailable month or substitute a later date. Its allowlist includes only release-policy columns. No-driver event measures use the declared event binding; they do not gain a certified-time comparison. For `latest_released`, choose an eligible shared period before compiling aligned outputs.

Render one answer statement containing all CTEs, windows and panels, with stable output ids. Independent panels use typed tagged rows so different dimension shapes need no unsafe fact join. Both statements pass the existing static SQL gate and warehouse dry run on their own allowlists before execution. The gate verifies context-required predicates in each base scope, not their mere appearance somewhere in a CTE. Extend its CTE/window lineage checks where necessary; do not broadly allow all columns or aggregates because a query came from this compiler. Gate tests must catch a dropped selector in one comparison branch and a policy present only in the denominator.

Update `_required_problems` and its `windowed` check (`kge/pipeline/sqlguard.py:658–677`; `windowed` at `:615`) with join dating. `allowlist.required[j.id]["window"]` is derived from the SCD2 policy (`kge/semantics/query.py:856–858`); it does not require an extra compiler-generated predicate when the join's own complete validity predicate already satisfies that window. Accept that declared predicate on the correct alias, including a source-date month-end expression, while rejecting partial bounds, wrong aliases and missing branch windows.

The authored SQL dialect remains `AUTHORED`. `WarehouseSet` handles routing, translation and dry runs; callers never read a backend, provider or dialect name. Reuse `Engine.run_plan`'s safety and deadline handling through a generic execution helper. Gate or warehouse failures keep their current refusal/abstention/transient-error contracts. There is no model SQL-repair retry.

### BigQuery jobs per request

The following counts are a static reading of the present execution path, not a live pilot measurement. They describe a warmed release/catalogue snapshot with valid SQL and no transport retries. BigQuery dry runs are listed separately from executed query jobs, but both count in the request's API-call budget. Startup, cold-cache reads, retrieval and evidence jobs are measured separately in a full-request budget; they may not be hidden by counting only warehouse executions.

| Numeric path | Executed queries today | Executed queries proposed | Dry runs today | Dry runs proposed |
|---|---:|---:|---:|---:|
| Fact-only total, no release driver | 1 | 1 | 1 | 1 |
| Context total with release driver and explicit period or latest selection | 2 | 2 | 2 | 2 |
| Unqualified context total, even with a release driver | 1 | 1 | 1 | 1 |
| General multi-measure/comparison answer with a release driver, using today's context path as the baseline | 2 | 2 | 2 | 2 |
| Clarify, abstain or access refusal before release lookup | 0 | 0 | 0 | 0 |

`Plan.release_query()` skips the lookup when neither a period nor latest is requested (`kge/semantics/query.py:447–452`). Preserve that behavior for unqualified totals. The cohort path already compiles an answer query; its evidence and mapping reads remain part of its measured request budget. The upgrade must not introduce a lookup per measure, context, hierarchy level, value or comparison. Vocabulary is built once per release from already fetched domain/semantics data. Model validation and retries perform no warehouse reads. At most one batched release query and one answer query are allowed, irrespective of plan width. This is within the issue's ceiling of **one additional executed query per answer beyond today's equivalent path**; the intended common-case increase is zero. No additional executed-query allowance is used for member discovery.

Pin whole-request counts with the BigQuery graph/retrieval/warehouse stand-ins: record the existing baseline for each simple, cohort, scoped and cold/warm scenario before replacing it; assert the new full-request total is at most baseline plus the issue's allowance. Also assert exact warehouse and dry-run counts from the table. Count retries separately from logical jobs and test their existing adapter bounds. Existing snapshot/access zero-query budgets remain mandatory. A cold request gets only the existing documented cache-fill costs, never a new per-measure cost.

## Model contract

Use `model_for("context")` for Ask planning, `model_for("answer")` for the reference Agent and `model_for("assist")` for Assist's source routing, through the existing `ModelProvider.json` port (`kge/ports.py:608`). Keep adapter retry/deadline/trace ownership. Assist routes the request; it does not fill a second independent numeric plan. No new provider setting, model client, dependency or port method is needed.

The versioned `query-plan-v1` prompt contains the current question, bounded conversation history, the access-filtered vocabulary bundle, the generated `PlanReply` schema and coverage obligations with source spans. It includes pack definitions and legal role paths but no SQL fragments, physical connection strings or unrestricted schemas. Identifiers must be copied from that bundle. Instructions prohibit adding names, dropping constraints, choosing among ambiguous meanings, or returning an answer figure. Prompt and vocabulary hashes go through the existing run trace; raw prompts, private rows and history text do not.

| Step | Contract |
|---|---|
| Decode | `json()` gives an object. Validate the discriminated schema strictly, including finite numbers, bounded lists, unique ids, a full proposed plan and its nullable `base_turn`. SQL, expression and free-form executable fields are invalid |
| Check | Resolve the full plan and coverage independently. An unresolved meaning becomes a typed clarification/abstention, not a partially executable plan |
| Repair | Allow one additional model response for malformed schema or a mechanically invalid reference. Supply structured error codes, affected spans and only permitted choices. Never retry to persuade the model past ambiguity, access or missing pack semantics |
| Exhaustion | Valid but incomplete meaning gives no figures. Persistent malformed output or a configured provider/JSON failure follows `no_fallback`/`NoFallback` and the existing cloud error contract, rather than silently answering by rules |
| Render | Figures, citations and null reasons come from execution. Optional wording goes through the existing number/citation checks; the model cannot create or modify result cells |

Remove the mock special cases from Combined and Assist. A factory's absent model remains the rules-only case; configured providers, including the mock, obey the same contract. The engine never compares `provider`, inspects a model class, or searches for a mock mode. Provider identity is used for trace labels only through the existing seam.

Extend the mock adapter with a separate plan-prompt handler. Tests supply recorded **plans**, keyed by prompt version, normalized question, vocabulary hash and history/base-plan hash; do not record SQL or live model answers. Store recordings in proposed `tests/fixtures/query-plan-v1/`, outside the semantics and engine trees. The shared test fixture loader reads those files and injects their keyed mapping into the mock adapter's constructor; the provider factory's default mock has an empty mapping. No filesystem discovery in the engine, deployment setting or new dependency is needed. Replay valid full-plan proposals (including follow-ups with `base_turn`), clarification and abstention replies, plus adversaries that omit a span, invent a context, mislabel an operation, lose a filter, request a restricted name, or return SQL.

An unrecorded plan prompt returns a typed `abstain` naming the missing recording key: never a figure, never rules, and never today's generic `careful` SQL response. Test the default empty mapping and a missing key in a populated mapping. Existing SQL-prompt adversaries remain only for legacy baseline tests until their replacement tests are in place. Pin `make parity`, `make margin`, eval's golden runs and other golden-suite runs to `MODEL_PROVIDER=none`. Before removing the mock exclusions, migrate the seven existing test files listed in build step 5: tests of rules behavior explicitly select none; model-path tests inject recordings and assert model-plan execution, or the missing-recording abstention. Add recorded Assist routing replies where a test needs numeric delegation. These suites must stay green when mock is no longer silently treated as absent.

## Rules path and fixed English rules

Rules continue to handle completely consumed simple requests: a single certified measure/context, a known single period or existing unqualified total, one declared dimension, known value predicates, a declared entity count, and definitions/catalogue routing. Preserve the existing narrow cohort capability only where its complete intent maps to the same checked plan; do not grow it to cover all combinations. Rules emit a `QueryPlan` and coverage, never an executable bypass. Unsupported shapes go to the shared plan check; with a model configured it can request a proposal, and with no model it abstains naming the unsupported span. A configured model's failure is not the same as no configured model.

| Current fixed rule in `kge/services/ask/engine/question.py` | Replacement |
|---|---|
| `:545`, customers/clients become `__population__` | Resolve a declared entity, measure population or explicit pack alias. No declaration means clarify/abstain. Do not infer a population from these English nouns |
| `:548`, bank/group/enterprise imply all units | Explicit source aliases and the request's checked scope. “All” can select all readable units only when it modifies a declared source scope; an unexplained organization noun is unresolved |
| `:594`, email address/date-of-birth patterns imply restricted lookup | Pack restricted-attribute policies, their phrases, column/business names and the private denial index. Apply before model disclosure and before numeric planning |
| `:600`, “plus” and multiple terms imply a special refusal | Parse an operator obligation. Separate requested measures become measure outputs; arithmetic needs a typed derived operation and commensurability check. Never select the first term and ignore another |
| `:602`, “which … also” implies cross-unit entity logic | Resolve the named entities and a pack-declared relation/certified join into a membership predicate. No declared relationship means abstain/refuse as its policies require |

Generic syntax for comparison, conjunction and arithmetic belongs to the platform. The domain objects those words act on belong to the pack. This removes fixed English business meaning without making every English phrase a pack alias.

## Meta-model decision

Owner decision (6 November 2026, #316): add meta-model 1.4.0 with an optional source-set alias. Meridian declares *bank*, *group* and *enterprise* as naming all four units. This additive prerequisite keeps older supported packs compatible. The runtime plan schema retains its own version. Public Ask wiring and removal of the fixed English rule remain the next step.

| Declaration | Verified basis | Design conclusion |
|---|---|---|
| Platform version | `meta-model.yaml:41` | Additive 1.4.0; older supported packs still load |
| Join condition and alias | `meta-model.yaml:271–284`; parsed by `kge/compiler/checks.py:1060`, `:1131` | Can already express key and date/current predicates and self-join roles. The compiler must honor them; no `as_of` property is needed |
| SCD policy | `meta-model.yaml:208–219`; `policies.scd2` in Meridian | Its params already identify validity/current columns and endpoint convention. Runtime checks combine this with the join condition to decide whether fallback dating applies |
| Alias | `meta-model.yaml:196–207`, `:405`; `kge/compiler/checks.py:910` | One phrase has one explicit target or, since 1.4.0, a non-empty `sources` list of distinct declared sources, exclusive with all single-target kinds. S11 still rejects duplicate phrases; a source set is one meaning |
| Ambiguity | Alias plus glossary/column/entity labels and value vocabularies | A runtime phrase-to-candidates index can detect a source/segment or role collision without duplicate Alias nodes. Preserve candidates across namespaces, including those that match a canonical alias. A new duplicate-alias authoring feature is unnecessary for the required clarification behavior |
| General plan | Existing Context/Measure/Entity/Join/Policy/Calendar and Vocabulary data | Measures, hierarchy roles, filters and temporal operators compose at runtime. No graph node for a request, comparison or window is needed |

Thornbury's missing context/entity/release declarations are pack capability boundaries, not evidence of a meta-model gap. A person may author richer declarations later through the existing contract; this issue must not manufacture them from warehouse names. The only meta-model addition is the 1.4.0 source-set alias. No kit version or pinned image change is part of this design.

## Ask, Assist, Agent and contracts

All numeric requests enter a common planner coordinator proposed in `kge/services/ask/engine/planner.py`. It obtains the current scoped bundle, interprets with rules or the configured model, invokes the check, compiles, gates, executes and binds evidence. Services call the Ask package's public functions; Assist must not import the new engine module directly. Shared request/history types live below services in semantics; HTTP/MCP models wrap them. No upward import or layer exception is required.

| Surface | Integration and impact |
|---|---|
| Ask | Replace shape routing and model SQL generation in Combined with the coordinator. Keep definitions and catalogue decisions; the general numeric plan covers both context and cohort cases. Add optional history input, normalized plan/turn binding and clarification output |
| Answer | Remains the rules baseline and its existing arms. Apply coverage and the same check/compiler to its numeric interpretation. Do not leave a first-term or cohort bypass that can return a partial figure |
| Assist | Keep catalogue/search routing and their sources contract. Pass history and products unchanged to Ask for numeric follow-ups. A valid previous numeric plan makes a follow-up eligible for Ask; it does not override an explicit new metadata question |
| Agent treatment | Fetch the governed vocabulary through the public context capability, ask the model for the same plan contract, and use the common checker/compiler. Replace the SQL-writing step and gate-repair loop. Model output never supplies SQL |
| Agent control | Retain the schema-context experimental arm and its label, but use typed plans and the same safety check. Its smaller context can lead to abstention where a governed context is missing. Do not expose hidden graph vocabulary to the control prompt just to improve its score |
| Context | Preserve the existing default context response. Add an opt-in plan-vocabulary request/response field for the new Agent path, filtered to the principal and products. It is not a new unauthenticated context endpoint |
| HTTP and MCP | Generate matching schemas from the shared models. Add `clarify` to the shared behavior enum and explicit typed clarification fields. Malformed request/history remains a validation error; ambiguity is a successful no-figure response |
| Numeric output | Retain `figure`, `rows` and existing combined fields for old scalar/single-dimension shapes. Add `result` with typed columns, stable output ids, cells, dimensions, per-cell citation references and null reasons for general shapes. Never flatten several dimensions into string dictionary keys |
| Renderer and UI | Templates render every result shape and clarification before optional model wording. Ask/Assist/Agent preserve the same checked result. KGE `ui/` stores and resends history and renders alternatives and structured results through the KGE API |
| Run config | Preserve accepted stage values for compatibility, but document `sql_source=model`/`planner+model` as interpretation choices feeding the deterministic compiler. Trace `planner=query-plan-v1` and the interpretation source explicitly; the old labels must no longer imply model-authored SQL |

`Figure.kind` and its documentation need a derived/count-compatible description; general rows belong in `result`, not forced into scalar figures. Each output retains lineage to the relevant context/measure/entity and its evidence. A ratio binds both operands, a comparison both branches. Rendering uses those bindings and the existing shelf/tier/purpose rules, never a convenient unrelated citation.

### Sealed fixtures and golden suites

| Existing fixture or suite | Expected design impact |
|---|---|
| `tests/fixtures/context_f75605a.json`, checked by `tests/test_context.py` | No intentional change: retain the default `/context` package and its existing serialization. New plan vocabulary is opt-in. Do not add fields to legacy `Plan` merely for the new coordinator |
| `tests/fixtures/agent_f75605a.json`, checked by `tests/test_agent_loop.py` | A deliberate contract migration is required for the switched Agent: prompt versions, model-call traces, plan/gate steps and some decisions change. The digest includes these fields. Preserve the historical fixture; replace assertions for the new path with a separately named, reviewed plan-contract fixture and an explicit before/after manifest. Do not regenerate the historical seal to make tests pass |
| `tests/fixtures/spec_portal_ask_contract.json` | Preserve metadata-only expectations and no-figure behavior; no portal contract extension in this issue |
| Existing golden, wealth, document, held-out and cross-pack sets | Keep their data and questions; run on `MODEL_PROVIDER=none`. Only A1 cases are expected to move from figures to no-figure decisions, each explained in the PR. Mixed join dating is exercised on declared test variants; other existing answers remain unchanged. Do not change a golden expectation to accommodate a regression |
| Recorded plan fixtures, proposed | New deterministic recordings for the synthetic capability matrix. They are not replacements for the owner's external/live question file |

The owner approves the Agent contract migration as part of this checkpoint before implementation. Existing suites must still pass as suites: historical assertions remain meaningful for the retained default context contract, and Agent assertions explicitly target the approved new contract. Keeping the obsolete SQL-producing Agent callable solely to satisfy a digest would contradict the requirement that the model never writes SQL.

## Test plan

New file names in this section are proposals. Tests use the existing dependency set, packs and fixture infrastructure, `MODEL_PROVIDER=none` or `mock`, and no live model. Test-only declared variants stay under fixtures or in-memory builders, never in runtime code. Each capability example above gets an expected checked plan, compilation assertion and, where fixture rows support it, an independently written numerical oracle. Missing data/declarations are tested as missing, rather than reported as a successful positive example.

| File | Required coverage |
|---|---|
| Proposed `tests/unit/test_query_plan.py` | Strict schema and JSON Schema agreement; all tagged variants, output references, cycles, full proposals with nullable `base_turn`, finite decimal values, invalid dates/ranges, all time forms, dimensional levels, complete composite counts, order/limit |
| Proposed `tests/unit/test_plancheck.py` | Every name against pack/access/product scope; wrong context for measure; candidate terms; ambiguous aliases versus labels/values/roles using “office” or `line_code` declared variants; hidden alternatives; unreadable-unit refusal before readable-value ambiguity; policy denial; incompatible grains, currencies and populations; unsupported hierarchy; missing period binding; invalid global/local predicates |
| Proposed `tests/unit/test_plan_coverage.py` | Every A1 component on rules and recorded plans; lower-case unknowns, all-caps codes, negation, compound breakdowns, opposing directions, numeric thresholds, count beside a measure, share/ratio, baseline comparisons; falsely claimed coverage and blank/overlapping spans; no figure and no numeric bypass |
| Proposed `tests/unit/test_plan_compiler.py` | ASTs for multiple contexts/dimensions, complete join conditions and composite keys, alias paths, mixed join dating and fallback, filters/selectors, snapshot versus flow grain, grouped counts, CTE alignment; prior-period/year key mapping with overlapping ranges and fiscal boundaries; zero/missing denominator, denominator scope; cumulative unknown count and reason propagation: `[10, unknown, 5]` → `[10, null, null]`, observed zero → `[10, 10, 15]`; ranking and ties |
| Proposed `tests/unit/test_plan_model.py` | Versioned prompt and scoped vocabulary; no SQL field; fixture loading/injection, replay keys and history hashes; full-plan/follow-up/clarify/abstain recordings; missing recording returns typed abstain naming the key with no figures, rules or SQL fallback; schema retry bound; malformed/provider errors; no provider branching, no live credentials |
| Proposed `tests/unit/test_plan_history.py` | Full replacement proposals with `base_turn`; obligations inherited by node id; explicit evidence for changed/removed nodes; stale release; changed access/scope/purpose; forged plan; absent or ambiguous antecedent; dependent-output removal; bounded input; every entry and clarification choice validated and sanitized before model disclosure, including unused entries; no result cells or secrets in history |
| `tests/unit/test_query_semantics.py`, `tests/integration/test_semantics.py` | Existing semantics plus independently authored mixed-dating oracle. Required predicates on every branch; self-join hierarchy and alternate role paths; no fact fanout |
| Proposed `tests/integration/test_generic_planner.py` | Meridian and Thornbury matrix above on rules and mock; positive declared variants; raw pack abstentions; several measure outputs, dimensions, ranges/YTD, every comparison/derived operation, overlapping-range/fiscal alignment oracles, cumulative unknown/observed-zero numerical oracles, thresholds/ranking and follow-up chains |
| Proposed `tests/integration/test_planner_jobs.py` | Baseline and proposed full-request BigQuery job counts with stand-ins, cold and warm, multiple contexts/measures/periods; exact warehouse/dry-run counts; no per-member lookup; no growth with plan width; invalid-plan zero executions |
| Proposed `tests/unit/test_plan_gate.py`; existing `tests/unit/test_composite_joins.py`, `tests/unit/test_data_governance.py`, `tests/unit/test_bigquery.py` | CTE/window lineage, nested aliases, distinct composite keys, branch-specific predicates and gate refusal; a join's own complete validity predicate satisfies its SCD2 window without fallback dating; missing/partial/wrong-alias windows refuse; authored SQL translation/dry run; no unsupported dialect branch |
| `tests/unit/test_client_nouns.py` | Extend the current scan specifically over all of `kge/semantics/` and `kge/services/ask/engine/`: source/unit ids, table and column identifiers, term names and ids, aliases and member vocabulary derived from every existing pack/lock. No pack-specific exceptions |
| `tests/unit/test_client_nouns.py`, additional planted cases | Prove the scan detects a pack name in code, identifier, prompt, docstring and assembled literal. Treat generic schema keys/operators separately from domain literal values; prevent broad allowances for common words. Include transformed test-pack names so a hard-coded original cannot pass merely by matching the fixture |
| `tests/unit/test_question_contract.py`, `tests/unit/test_question_periods.py`, `tests/integration/test_ask_parsing.py`, `tests/integration/test_ask_period_contract.py`, `tests/integration/test_ask_model_period.py` | Existing accepted periods and invalid-date handling; no fallback from range or partial interpretation to a total; migrate narrow model-period tests to the full plan contract without losing their negative guards |
| `tests/unit/test_assist_contract.py`, `tests/integration/test_assist.py`, `tests/integration/test_ask_scope.py`, `tests/unit/test_product_scope_contract.py` | Clarify/result/history across HTTP/MCP; metadata routing unchanged; source list, scope and denial redaction; follow-ups use the previous plan and cannot escape product scope |
| `tests/integration/test_portal_ask_contract.py`, `tests/test_context.py`, `tests/test_agent_loop.py`, `tests/integration/test_combined.py`, `tests/integration/test_answer_text.py` | Sealed-contract treatment above; template completeness, citations for every output, numeric wording guard, configured model failure and Agent's shared compiler |
| `tests/unit/test_principal_ids.py`, `tests/integration/test_graphstore_twin.py`, `tests/integration/test_assist_joins_bigquery.py`, `tests/integration/test_ask_budget.py` | Existing access/query-count/deadline contracts; no new graph calls per measure; same whole-request budget and transient errors |
| `tests/unit/test_layers.py`, `tests/unit/test_no_comments.py`, `tests/unit/test_api_surface.py` | No upward imports, provider/dialect branches or Python comments; new behavior and schemas agree across transports |
| KGE `ui/` coverage in the existing e2e lane, extended for this plan | Send prior plans with follow-ups; resolve a clarification slot; render several dimensions/measures/nulls and readable alternatives; clear incompatible history on scope change |

The no-pack-names test covers all new compiler/check/model-prompt code in the required trees. SQL examples and recordings containing pack names belong only in test data. This check supplements the existing broad platform scan; it must not gain a special exception for this issue. Audit the existing files too: pack-specific illustrations in Python docstrings, such as the current schema examples in `semantics/query.py`, move into tests or this design rather than receiving a scan exemption. Generic protocol/type names remain platform vocabulary; a literal domain value is never exempt just because it is also an English word.

For the build, run focused pytest and ruff after each step, then the code checks required by kge-checks: lint, unit and integration suites, parity, margin and repository checks; KGE route/UI changes also need the e2e lane. Use Ladybug locally and the existing BigQuery offline stand-ins. Pin parity, margin and golden runs to `MODEL_PROVIDER=none`. Preserve all accepted golden results except the A1 no-figure decisions, with a per-case explanation in the PR. No local live UAT is added: the issue assigns live shape/pack/reference acceptance to the GCP agent after kit publication.

## Build sequence

Each step adds passing tests with its implementation. New components stay unused by public routes until their integration is complete; do not land a failing test or a temporary numeric bypass. Independent verification/review remains the manager's orchestration responsibility. The current design checkpoint changes only this document.

| Order | Result left green | Files expected to change during implementation |
|---:|---|---|
| 1 | Typed plan, clarification, result and full follow-up proposal models round-trip; legacy paths untouched | New semantics plan/history types; proposed unit plan/history tests and fixture builders |
| 2 | Scoped vocabulary, multimap resolution and coverage independently checked | New semantics coverage/check modules; `engine/graphview.py`, `question.py` helpers; plan-check/coverage/client-nouns/layer tests. Do not enable a route until its complete coverage mapping passes |
| 3 | Compiler and gate support complete checked plans without execution from the model | New semantics compile module; targeted helpers in `semantics/query.py`, `pipeline/sqlguard.py`; compiler, gate and BigQuery unit tests; independent in-process fixture oracles |
| 4 | At most one release lookup and one answer statement through existing ports | Engine execution helper and planner coordinator; warehouse port/adapters only if shared result metadata requires it; planner-jobs, principal, graph-twin, semantics and generic integration tests |
| 5 | Mock exercises plan contract; missing recording abstains by key; parity/margin/golden runs explicitly use none and suites remain green | New engine plan prompt/reader; `adapters/model/mock.py`; new plan recordings and test loader/injection; model tests; parity/margin/eval golden runner provider pinning. Migrate `tests/unit/test_assist_contract.py`, `tests/unit/test_gcp_native.py`, `tests/unit/test_model_path.py`, `tests/unit/test_query_dialect.py`, `tests/integration/test_ask_scope.py`, `tests/integration/test_assist_joins_bigquery.py` and `tests/integration/test_agent_mock.py` to explicit rules expectations or recorded model plans/routing replies before removing Combined/Assist mock exclusions |
| 6 | Ask and rules baseline use coverage/check/compiler; fixed English meaning rules removed | `engine/combined.py`, `core.py`, `question.py`, `config.py`, `wording.py`, reading/rendering helpers; Ask service/models/routes/MCP; parsing, period, scope, cohort and answer-text tests |
| 7 | Agent and Assist share the plan and history contract | `engine/agent.py`, `context.py`, Ask package exports and context models; Assist service/models/routes/MCP; approved new Agent contract fixture and manifest; default context seal and portal metadata seal unchanged |
| 8 | KGE UI follow-ups, clarification and general result tables work through public APIs | KGE `ui/` callers and renderers; KGE Python API/e2e tests; API/README contract documentation |
| 9 | Final requirement matrix and local gate pass on the integrated diff | All focused and golden suites, parity/margin, repository checks, KGE browser checks, independent review. Explain every changed answer. No fixture re-recording, baseline reset or release action merely to obtain green checks |

Before every later push the manager runs the mandatory local gate and obtains independent Fable sign-off on that exact diff. CI attests the same head. This checkpoint is a draft design for approval, not a request to merge or to live-test. A later merge that changes the demo publishes its next KGE kit under the repository's release rules; agents do not number or initiate that publication.

## Risks and owner decisions

| Risk or decision | Proposed treatment |
|---|---|
| Arbitrary paraphrases cannot be proved complete from a model's claimed spans | Fail closed on ungrounded text. Review the coverage obligation tests as carefully as SQL oracles; some novel wording will clarify before it answers |
| Pack expressiveness versus missing pack instances | Keep the compiler generic and show missing-declaration abstentions on shipped packs. Use explicit test variants for positive joined/count/mixed-time cases; do not infer business joins or add certified definitions silently |
| General plans can expose grain or fanout errors hidden by a scalar result | Aggregate by checked populations first; prove alignment; use independent numerical oracles and full-branch gate checks |
| Agent contract migration | Owner approval needed for replacing the old SQL-writing Agent's sealed assertions with a reviewed plan-contract fixture and before/after manifest, while preserving the historical file and default context seal |
| Clarification and history/result API additions | Owner approval needed for the proposed additive `clarify`, `history`, normalized-plan binding and structured `result` contract. No conversation persistence service is proposed |
| Portal server history pass-through | Owner decision: whether the portal server should pass `history` through to KGE. Treat any portal server or portal UI implementation and its tests as separate later work; this issue delivers the KGE API and KGE `ui/` contract only |

There is no remaining decision about model-written SQL, extending rules to every shape, the live-acceptance owner or the meta-model version. Those are settled by the issue or established above. Approval of this document authorizes the proposed runtime placement and contracts; it does not authorize a pack change, cloud action or release operation.
