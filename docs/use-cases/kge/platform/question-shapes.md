# Question shapes as pack data

| Document control | Value |
|---|---|
| Status | Approved by the owner on 2026-11-13. Nothing here is built yet; the build issues are in §9 |
| Requirement | Issue #445, with the owner's direction of 2026-11-13 |
| Basis | Code, skills and pack declarations on `develop` at `306a98b`; the Meridian examples and figures as the issue gives them |
| Meta-model | 1.6.0, additive. The issue names 1.5, but `meta-model.yaml:46` is already 1.5.0 (the runtime subgraph and the partitioned policy, #347), so these sections are the next minor version. Packs on 1.2.0 to 1.5.0 still load |
| Scope | Pack sections and their checks, the lifecycle in the onboarding skills, shadow running, maturity metrics, a shapes view in Quality, the first two shapes, and the build issues. No product code changes in this issue |
| Out of scope | Building any of it; moving Ask onto LangGraph; packs carrying code |
| Related | [answer-modes.md](answer-modes.md) (#462): the order in which Ask tries the rules, worked examples and the model, and how the model's readings are labelled, scored and certified |

Ask's business vocabulary is pack data compiled into the graph. How a question's words combine into a plan is code. This document moves the second into the pack as data, through the onboarding skills, with the planner's code kept as the fallback until each shape has proven itself against agreed metrics. The pack never carries code: when a question needs a calculation the platform's building blocks cannot express, that is a platform issue for a new building block, written once for every client.

All paths below are relative to `demos/kge/` unless they start with `docs/`. File and line citations describe `develop` at `306a98b`. Every threshold in this document is a planning estimate until measured.

## 1. Today

### 1.1 Where the planner's parts live

| Planner part | Where it is | What it is |
|---|---|---|
| Operation words | `kge/semantics/coverage.py:16-41` | `SCAFFOLD`, `SCAFFOLD_PHRASES` and `OPERATORS`: 107 phrases mapped to 41 operations (`vs` to `comparison`, `above` to `gt`, `top` to `desc`, `year to date` to `ytd`), fixed at import |
| Question-dependent lexing | `kge/semantics/coverage.py:84-140` (`_ledger`) | Rules that change the table per question: `which` is dropped when a ranking word appears (`:117`), `net` becomes an operation when inflows or outflows appear (`:125`), grouping nouns join the names. Numbers are read by two patterns: `literal` (`:138`, no thousands separators) and `amount` (`:139`, separators only after `$`) |
| Question shapes | `kge/services/ask/engine/planshapes.py` (158 lines): `compose` (`:40-115`) and `parts` (`:118-158`); `kge/services/ask/engine/planrules.py` (762 lines): `read` (`:369-679`) | One branch per shape: period range, explicit-period comparison, previous period or year, two-context comparison with difference and percent change, ratio, top or bottom N, coordinated measure parts |
| Building blocks | `kge/semantics/plan.py` | The `query-plan/v1` node kinds: measure and distinct count, column and time dimensions, predicates (compare, in, null, all, any, not, membership) on a column or an output, comparisons (time mode, previous period or year, explicit period, contexts), derived values (ratio, percent change, difference, share, running total), order and limit |
| Plan checks | `kge/semantics/plancheck.py` (`check_plan`, `:221`), `kge/semantics/coverage.py` (`coverage`, `:758`; `operations`, `:539`; `ground_plan`, `:1024`) | Every meaningful span of the question must be applied to a plan node; uninterpreted text prevents execution |
| SQL compiler and gate | `kge/semantics/compile.py` (`Builder`, `:323`; `compile_plan`, `:900`); `kge/pipeline/sqlguard.py` (`gate`, `:863`) | Deterministic SQL from a checked plan; every statement gated before it runs |

Two planners fill a plan. With a model configured, the model proposes a plan (`kge/services/ask/engine/planmodel.py`) and the rules planner (`planrules.read`) is the floor when the model's plan is refused (`planpublic.py:157`, `:175`; `planner.py:344-365`). Without a model, the rules planner is the only one. Both plans go through the same check, compiler and gate. The operation table feeds both paths: the rules planner builds from it, and the coverage check reads it to decide whether a model plan applied every span. It also feeds the readable-words check (`kge/semantics/planvocabulary.py:217`).

### 1.2 The three examples

| Question | Today | Cause in the code |
|---|---|---|
| certified NNA and current NNA for March 2025, with their difference | "No answer: their; difference" | `compose` builds a context comparison only when `comparison` is among the operations (`planshapes.py:76`), and derives a difference only for a comparison (`:83-89`); `their` is both filler and `membership` (`coverage.py:18`, `:20`) |
| certified NNA minus current NNA for March 2025 | "No answer: minus" | `minus` is not in `OPERATORS` |
| how many accounts had certified NNA over 1,000,000 in January 2025 | "No answer: over; 1; 000" | `over` is not an operation word; `1,000,000` without `$` is split at its commas (`coverage.py:138-139`); and no plan shape counts the entities whose own total passes a threshold |

The third needs more than a phrase. An output predicate (`OutputOperand`, `plan.py:188`, compiled after grouping at `compile.py:449` and `:778`) can already list the accounts whose certified NNA is over 100,000, one row each. No node counts those rows: the totals the compiler adds (`compile.py:212-244`) sum an output over a breakdown, and a distinct count counts entities over fact rows, not over a grouped and filtered result. That is a missing building block.

### 1.3 What the pack and the onboarding already give

| Existing piece | Where | Reused for |
|---|---|---|
| Pack sections and their shapes | `kge/compiler/checks.py:121-195` (`SECTIONS`, `REQUIRED`, `FILLS`) | The new sections' shapes |
| Compiler rules S00 to S31 | `kge/compiler/checks.py:1-62`, registered in `check()` at `:1876-1919` | New rules S32 to S35 |
| Onboarding input rules | `kge/estate/upfront.py`, `CHECKS` at `:460`; question sets at `:383-410` | New rule F12 on test phrasings and approval |
| Standards preload | `standards/preload.yaml` (format 1, version 1.0.0), read by `kge/compiler/standards.py` | The base operation phrases and the base templates and recipes |
| Graph emit | `kge/compiler/emit.py`, `NODES` at `:380-417`, `EDGES` at `:418-475` | New node and edge types |
| Question sets | `kge/eval/sets.py`; `also_asked_as` expanded at `:141-153` into `<id>_alt<n>` items; `reference_sql` in `expect` (`:48-86`) | A shape's tests |
| Margin gate | `kge/eval/margin.py`: V01 to V05, the GATE marker, `runs/<run_id>/` | Per-shape agreement at build |
| Parity | `kge/eval/parity.py`, `clients/meridian/baselines/parity-*.json` | Proof that a mirror moves no answer |
| A/B and improve | `kge/eval/harness.py`, `kge/improve/abtest.py` (`keep_decision`, `:225`), `kge/improve/rules.py`, `kge/improve/edits.py` | Proposals from misses, kept only with no fall |
| Golden scoring | `kge/eval/run_golden.py`, `reference_sql` truth per runtime (`:280-372`) | Wrong-figure counts |
| Quality | KGE `kge/services/quality/routes.py` (`/golden`, `/ab`, `/eval`); portal `portal/ui/src/pages/Context/Quality.js` | The shapes view |

Three facts differ from the issue's wording. No golden item records who approved its `reference_sql`: approval is a step in `skills/author-pack/SKILL.md:168`, not a field. No rule sets a minimum number of `also_asked_as` phrasings. And the improve job may edit only add-only `terminology.aliases` (`kge/improve/edits.py:1-7`). The design adds the first two and widens the third.

## 2. Assumptions

| # | Assumption | How it is tested |
|---:|---|---|
| A1 | The plan algebra in `plan.py` is the complete set of building blocks a pack may compose; a pack adds none | Check S34 refuses any block the platform catalogue does not list |
| A2 | Shadow running compares checked plans, not figures, on the live path, so it adds no warehouse job and no model call per request | B2's acceptance counts warehouse jobs per request before and after (zero added) |
| A3 | Two plans that agree after normalisation (ids, evidence spans and obligation ids removed, order-insensitive lists sorted) produce the same figures, since the compiler is deterministic | Parity and golden runs on both paths in B2 and B6 show no figure moves where plans agree |
| A4 | A wrong figure costs at least 100 times a decline: a decline sends the person to a steward; a wrong figure can reach a report. The ratio is a planning assumption for the owner to confirm, and it sets every threshold in §5 | Owner's confirmation before B3 |
| A5 | "over" means strictly greater than; "at least" (`ge`) already exists for the inclusive reading | The threshold count's worked examples, approved by a person |
| A6 | A golden item with four phrasings (the question and three `also_asked_as`) exercises a template's slots enough to judge it; the minimum is a planning estimate | Raise it if the shadow log shows live phrasings the tests missed |
| A7 | Live shadow volume on the Meridian pilot reaches enough questions per shape over two releases for a 99% agreement figure to mean something; a shape with fewer than 100 live questions in the window is reported as "not enough evidence", not as mature | The shapes view reports the count beside the rate |

## 3. Pack sections in meta-model 1.6.0 (requirement 1)

### 3.1 The sections

All six are optional, so every pack written for 1.2.0 to 1.5.0 still compiles (S03). A base set of phrases, shapes, recipes and clarification rules ships in the standards preload (format 2), which is platform data loaded into every graph, as the ontology concepts are today; a pack extends it and never redefines it.

| Section | Holds | Example (Meridian) |
|---|---|---|
| `phrases[]` | `{phrase, operation}` or `{phrase, filler: true}`: a word or phrase and the one operation it marks, from the platform's closed list of operations (the 41 in §1.1 and any a building block adds) | `{phrase: minus, operation: difference}` |
| `shapes[]` | A question shape as a template: `{id, label, slots[], requires, forbids?, constraints?, recipe, replaces?, stage}`. A slot is `{name, kind, count?}` with a kind from the platform's closed list: `measure`, `context`, `entity`, `dimension`, `period`, `range`, `number`, `comparison_op`, `direction`. `requires` names the operations and markers the question must carry | `threshold-count`: slots measure, entity, comparison_op, number, period; requires `count` and one of `gt`, `ge`, `lt`, `le` |
| `recipes[]` | A named composition of building blocks with parameters: `{id, blocks: [{block, id, ...parameters}]}`. A parameter is a slot reference (`$measure`, or `$context[0]` for the first context in the question), a typed scalar, an id the pack declares, or another block's id in the same recipe | `per-entity-threshold-count`: one `per_entity` measure block with `measure: $measure`, `entity: $entity`, `having: {op: $comparison_op, value: $number}` and `reduce: count` |
| `clarifications[]` | A clarification rule over the parsed question, evaluated before any template is matched and independent of which templates would match: `{id, when: {requires, forbids?, slots, constraints?}, message}`, where `message` names one of the platform's clarification texts | `contexts-or-periods-2`: requires `comparison`, slots `context: 2` and `period: 2`, constraint `{before: [comparison, period]}`; message "Please identify whether to compare the contexts or their periods." |
| `shape_tests[]` | The pack's tests for a shape, its own or a preload one: `{shape, tests: {set, items}}` | `{shape: threshold-count, tests: {set: golden_shapes_v1, items: [...]}}` |
| `examples[]` | A checked worked example: `{id, shape, question, plan, approved_by, approved_on}`, where `plan` is a `query-plan/v1` document, the plan a person approved for that question | "how many accounts had certified NNA over 250,000 in January 2025" with its approved plan |

`replaces` names the coded builder a shape mirrors (for example `planshapes.compose:contexts`), from a list the platform publishes in the preload. `stage` is one of `specify`, `shadow`, `graph_first` and `retired` (§4.3). A worked example's plan is data: identifiers, typed scalars and node kinds, never SQL or an expression (`plan.py:17-18` forbids extra fields and is strict). Tests and worked examples are always the pack's, since they name a client's terms and figures; a preload shape carries none, and each pack that uses it attaches its own through `shape_tests[]` and `examples[]`.

**Matching.** A template is selected by what the ledger found: the operations present, how many names of each slot kind, and any position constraints it declares. It never matches word patterns, which keeps it from becoming a pattern language, code by another name.

- **Slot order.** Slots are bound in the order their spans appear, so a recipe names `$context[0]` and `$context[1]`. That is how the ratio's numerator and denominator (`planshapes.py:91-100`) and a context comparison's left and right (`:81`) are ordered now.
- **Slot counts.** A slot's `count` is a number or a range (`{min: 0, max: 1}`). Two periods the lexer has already put in one window (the piece's `window` field, `coverage.py:390-399`: "January to March", "between January and March") fill one `range` slot, not two `period` slots, so a template can tell a range from two separate periods by counts alone.
- **Exclusions.** `forbids` names operations or markers the question must not carry, so two templates that differ by one word do not both match.
- **Position constraints**, from a closed list, as data: `{before: [a, b]}` (the first piece of `a` ends before the first piece of `b` starts, or there is no `b`), `{after: [a, b]}`, and `{adjacent: [a, b]}` (no piece between them). Each of `a` and `b` is an operation, a marker or a slot kind. A template may declare them, and so may a clarification rule.
- **Lexer markers.** Some coded branches read raw text, not the ledger. B2 adds a marker for each to the ledger, as platform code in the lexer that both adapters share. The coded planner ignores the markers, so they move nothing it builds.

| Coded branch | Raw text it reads today | Marker in B2 | Template constraint in B6 |
|---|---|---|---|
| The `from … to` swap of a period comparison (`planshapes.py:66-67`) | `from` before the first period and `to` or `through` between them | `from_to`, set where the lexer has read "from X to Y" as a change (`changing`, `coverage.py:376-383`) | Requires `from_to` |
| Ranking (`planshapes.py:101-114`) | `top N` or `bottom N`, and `which … most` for a limit of 1, after the lexer has dropped `which` (`coverage.py:117`) | A `number` slot marked adjacent to its direction word, and an `asks_one` marker where `which` opens a superlative question | `{adjacent: [direction, number]}`, or requires `asks_one` with a default limit of 1 |
| Measure parts (`planshapes.py:118-158`) | The literal words `inflows`, `outflows` and `net` | A `measure_part` marker on any pack predicate the pack flags `part: true` (a new optional field on `terminology.predicates`, B1); Meridian flags its inflow and outflow predicates | Requires `measure_part` |

**Context or period comparison, branch by branch.** Every place the code decides between a context comparison, a period comparison and a range is below, with the data that reproduces it. Rows 1 to 3 are the lexer, which stays platform code that both adapters share. Rows 12 and 13 are slot binding, which also stays (§4.3).

| # | Branch | What it decides | As data | Example question |
|---:|---|---|---|---|
| 1 | `coverage.py:376-383` (`changing`) | Two periods with `from` before the first are a change between them when a difference or percent-change word comes before the first period, or when `compare` comes before it with at most one named context or time mode there and a difference or percent-change word anywhere. The change word then becomes a `comparison` operation carrying difference and percent change, and the periods stay apart | Lexer, unchanged; B2 adds the `from_to` marker | NNA change from April 2025 to June 2025 |
| 2 | `coverage.py:386-388` (`comparison_to`) | "compare … X to Y" without `from` keeps the periods apart | Lexer, unchanged | compare NNA for March 2025 to April 2025 |
| 3 | `coverage.py:390-399` (windows) | "X to Y", "X through Y" or "between X and Y", with neither 1 nor 2, is one window | Lexer, unchanged; the window fills a `range` slot | NNA from January 2025 to March 2025 |
| 4 | `planshapes.py:48-49` (`context_comparison`) | Two named contexts and a comparison word that ends before the first period, or with no period. It feeds row 5 only | The constraint `{before: [comparison, period]}` in row 5's rules | (feeds row 5) |
| 5 | `planshapes.py:51-52` | Under row 4, a previous-period word, or two periods outside one window, could compare either the contexts or the periods: clarify | Two clarification rules, evaluated on the parsed question before any template: `contexts-or-periods-2` (requires `comparison`; slots `context: 2`, `period: 2`; `{before: [comparison, period]}`) and `contexts-or-previous` (requires `comparison` and one of `previous_period` or `previous_year`; slot `context: 2`; the same constraint). Both return "Please identify whether to compare the contexts or their periods." A ranged question fills a `range` slot, not two `period` slots, so `contexts-or-periods-2` does not fire on it, as at `:50-51`. `contexts-or-previous` has no period slot condition, so it fires with a range as well, as `:51` does | certified NNA vs current NNA for March 2025 and April 2025 |
| 6 | `planshapes.py:56-64` (range) | Two periods in one window become a range time; an unordered range or one across grains is a clarification | `period-range`: slot `range: 1`. The ordering check is the `Range` block's own validation (`plan.py:124-128`), returned as the platform's clarification "Please give an ordered range at one period grain." | NNA between January 2025 and March 2025 |
| 7 | `planshapes.py:65-70` (explicit period) | Two periods outside one window become the first period's time and an explicit comparison with the second, with both difference and percent change (`:86`) | `period-comparison`: slot `period: 2`, `forbids: [from_to]`; recipe base `$period[0]`, other `$period[1]` | NNA for March 2025 vs April 2025; compare NNA for March 2025 and April 2025 |
| 8 | `planshapes.py:66-67` (swap) | After row 1, the base is the "to" period | `period-change-from-to`: slot `period: 2`, requires `from_to`; recipe base `$period[1]`, other `$period[0]` | NNA change from April 2025 to June 2025 |
| 9 | `planshapes.py:71-75` (relative) | With no comparison yet, a previous-period or previous-year word adds that comparison; both together clarify | Templates `previous-period` and `previous-year`; a clarification rule `one-comparison-period` (requires both operations) returns "Please identify one comparison period." | NNA for March 2025 vs the previous year |
| 10 | `planshapes.py:76-82` (contexts) | With no comparison yet, a comparison word compares two measures on two named contexts in the order named, wherever the word sits; otherwise clarify | `two-context-comparison`: requires `comparison`, slot `context: 2`, `period: {min: 0, max: 1}`, `range: {min: 0, max: 1}`; recipe left `$context[0]`, right `$context[1]`; clarification rules `comparison-contexts` (requires `comparison`, forbids the previous-period operations, slots `period: {max: 1}` and `context: {max: 1}`, or `context: {min: 3}`) return "Please identify both comparison contexts." | certified NNA vs current NNA for March 2025; for March 2025, certified NNA vs current NNA |
| 11 | `planshapes.py:83-89` (derived) | A context comparison derives a difference or percent change only when that word is present; a period comparison derives both | `two-context-difference` and `two-context-percent-change` each require the comparison, their own operation and two contexts, and contribute only their derived node. Rows 7 and 8's recipes carry both derived nodes | certified NNA vs current NNA for March 2025, difference |
| 12 | `planrules.py:401-405` | The two contexts of a comparison join the terms only when a comparison word is present | Slot binding; B2 moves `shared_contexts` there and runs it for any template with two context slots | (all of rows 5, 10 and 11) |
| 13 | `planrules.py:380-386` | A parse problem on two period endpoints is cleared, and the first endpoint becomes the period | Slot binding, unchanged | NNA from January 2025 to March 2025 |

Rows 6 and 10 compose: a range with a context comparison is one plan ("certified NNA vs current NNA from January 2025 to March 2025"). The born-in-graph two-context difference of B6 (§8.3) requires `difference`, two contexts and `forbids: [comparison]`, so it never overlaps row 11.

With these, every branch of `compose` and `parts` is expressible as data, and B6 mirrors them without planner code. If B2's ledger-equality test shows that a marker cannot be added without changing a coded result, that branch stays coded, keeps its fallback and is never retired, and B6's pull request says which and why.

**Composition.** Templates compose, as the coded branches do. `compose` adds time-grain dimensions (`planshapes.py:53`), then a range or an explicit-period comparison, then a previous-period comparison, then a ratio, then a ranking (`:101`), and `parts` runs afterwards (`planrules.py:627`). So one question can be a ranking, a breakdown by month and a range at once. Every template a ledger matches contributes its nodes to one plan, applied in the platform's fixed block order: dimensions, time, comparisons, derived values, order and limit, then measure parts. Where two readings cannot be told apart, a clarification rule fires first and returns its text, the same "Please identify…" texts `compose` returns today, taken from a platform list; rules are evaluated on the parsed question before any template, so a rule never depends on which templates match. A question counts towards every shape it used.

### 3.2 In the graph

| New type | Layer and origin | From |
|---|---|---|
| `Phrase` node, `MARKS` edge to an `Operation` node | curated, pack (and platform for the base set) | `phrases[]` and the preload |
| `Shape` node with its stage; `USES` edge to `Recipe`; `TESTED_BY` edge to `Golden` | curated, pack | `shapes[]` |
| `Recipe` node holding its blocks as a JSON property | curated, pack | `recipes[]` |
| `WorkedExample` node; `EXAMPLE_OF` edge to `Shape` | curated, pack | `examples[]` |
| `ClarificationRule` node | curated, pack (and platform for the base set) | `clarifications[]` and the preload |
| `ShapeStatus` node; `STATUS_OF` edge to `Shape` | runtime, pipeline | The fold job in §6 |

The `Golden` node keeps holding ids only, never a question or SQL (`meta-model.yaml:333-342`). A worked example holds its question and plan, as the pack does; neither is a figure.

### 3.3 What the checks enforce

| Rule | When | Enforces |
|---|---|---|
| S01 | Compile | Each new section's shape, from the field tables added to `SECTIONS` |
| S32 phrases | Compile | A phrase is non-empty and lower case; its operation is on the platform's list; it does not redefine a preload phrase; it is not also an alias, an acronym or a grouping phrase of the pack, whose meaning would then depend on which table is read first |
| S33 shapes | Compile | Slot kinds, markers and position constraints on the platform's lists; `requires` and `forbids` name known operations and markers; the recipe is declared; every slot the recipe references is declared and every declared slot is used; every shape the pack declares or uses from the preload has `shape_tests` citing items of a set `tests.sets` names, at least one (as S25 does for contexts), and at least one worked example in this pack; `replaces`, when present, names a coded builder the preload lists; two shapes that can match the same ledger either contribute disjoint plan nodes or are separated by a clarification rule or a `forbids`; the coupled groups of §4.3 are computed and listed |
| S34 recipes | Compile | Each block is in the platform's block catalogue (`kge/semantics/blocks.py`, new, generated from `plan.py`'s node kinds); each parameter is of the type the block declares; block references form no cycle; the recipe expands with placeholder slots into a valid `QueryPlan` (`plan.py:390-429`) |
| S35 examples | Compile | The plan validates as `query-plan/v1`; every name in it is declared in the pack; the shape is declared; `approved_by` and `approved_on` are present for a shape above `specify`. At `specify` an example may be a draft without them, and counts towards no metric |
| S36 clarification rules | Compile | `requires`, `forbids`, slots and constraints on the platform's lists; `message` names a platform clarification text; ids unique across the preload and the pack |
| F12 test items | Upload (`check-pack`, `kge/estate/upfront.py`) | Each item a shape cites has at least three `also_asked_as` phrasings (A6) and, where it carries `reference_sql`, a `reference_sql_approved_by` and `reference_sql_approved_on`. For a shape at `specify`, a missing approval is reported as a draft, not a failure, and V05 holds the gate on it |
| V06 examples replay | Post-load (`kge/eval/margin.py`) | The graph planner, given each worked example's question, proposes exactly the approved plan after normalisation, and `check_plan` accepts it; where the shape `replaces` a coded builder, the coded planner proposes the same plan |
| V07 stage | Post-load | A candidate that raises a group's stage carries a maturity record meeting §5's thresholds for every shape in the group, and every shape in a group declares the same stage; a candidate that lowers one needs none |

The sets stay outside the pack, as `pack-contract.md` §4.12 requires; F12 reads the set files through `kge/eval/sets.py`, which gains the two approval fields in its suite form and its field table (`kit/INSTALL.md:955-980`).

## 4. The lifecycle (requirements 2 and 3)

### 4.1 When planner code is written

| Situation | What is written | Where |
|---|---|---|
| Before B2 merges (the graph cannot hold shapes) | A new shape is coded in the planner, tests first | `planshapes.py`, `planrules.py`, with its golden items |
| After B2 merges, a new shape the blocks can express | Pack data only: tests, then template and recipe, then phrases | The pack (or the preload, for a general shape) |
| After B2 merges, a shape the blocks cannot express | One new general building block in platform code, once for every client, then the pack's recipe uses it | `plan.py`, `plancheck.py`, `compile.py`, `coverage.py`, `blocks.py`; the pack |
| A shape coded before B2 | Mirrored as a template; its code stays as the fallback until the template meets §5, then the code is retired. Coded shapes never grow again | Preload or pack; then a retirement change to the planner |

### 4.2 The lifecycle in the onboarding skills

| Skill | Today (`skills/<name>/SKILL.md`) | Change |
|---|---|---|
| author-pack | Drafts a UAT suite: ids, `expected_behavior`, `reference_sql` a person approves, `also_asked_as`, templates and matrix (`:162-176`) | Tests first: for each new question form, golden items with at least three `also_asked_as` phrasings and a person-approved `reference_sql`, recorded in the two approval fields. Then the template's slots and `requires`, the recipe from the block catalogue, a worked example per item with its approved plan, and any new phrases. The skill names the closed lists (slot kinds, blocks, operations) and says that anything outside them goes to the platform route in §8 |
| ask | Diagnoses a miss and explains the smallest plausible pack correction, with a missing alias or an uncertified mapping as examples (`:28`); no fixed outcomes | Every diagnosis ends in one outcome from a fixed list. Pack fixes: `missing_alias`, `unmapped_column`, `missing_phrase`, `missing_template` (a shape the blocks can express), `template_mismatch` (a template matched and built the wrong plan). Platform: `missing_building_block`, `missing_join`. Steward: `unclear_measure`. The outcome and its evidence (the ledger's unapplied spans, the matched or unmatched templates from the run trace) are handed to improve or drafting |
| build-and-gate | Reports the margin result, gate, run id and per-set summaries; `fell`, `not checked` and `gate failed` block (`kge/services/onboard/routes.py:147-153`) | The margin report gains a `shapes` list: per shape, its stage, tests passed of total, wrong figures, worked examples replayed, and (for a shape that `replaces` code) the template's agreement with the planner on the tests and in live shadow. A shape below the bar for its requested stage blocks, as `fell` does |
| improve | Proposes add-only aliases from misses (`kge/improve/rules.py`, `edits.py`), kept only with no fall (`abtest.py:225`) | Also proposes add-only phrases, and new templates from repeated misses with the same ledger signature, each with the misses as candidate tests. A template proposal is held for review: V05 already holds the gate until each proposal item is accepted and certified (`margin.py:250-296`), and V07 holds a template at `specify` until its tests have approved `reference_sql` |
| land-and-draft | Lands tables and drafts the unit for business acceptance and steward certification | Drafting may propose phrases found in the unit's question list, as candidates only; it drafts no template or recipe, since those need tests a person approves first |

`kge/improve/rules.py` gains the outcomes above in place of `needs_code`, which today sends every unmatched shape to the platform team. `kge/improve/edits.py`'s allowlist widens to add-only `phrases[]`, and to add-only `shapes[]`, `recipes[]`, `shape_tests[]` and `examples[]` at `stage: specify`, plus one added stem in `manifest.inputs.sets`, one added entry in `tests.sets`, and one new set file at the candidate's set root (below); nothing else changes.

A template proposal carries its candidate tests, so S33 holds on the candidate pack:

1. The misses that share the ledger signature become items of a new question set, `shapes_<id>_v1`, in the suite form `kge/eval/sets.py` reads. Each item has the miss as its question, the other misses of the same signature as `also_asked_as`, and a drafted `reference_sql` with no approval.
2. **The candidate is self-contained.** The candidate pack is a copy of the client folder (`kge/improve/edits.py:360`), at `clients/<client>/proposals/<id>/<client>/` (`kge/improve/abtest.py:68-94`). It declares the new stem in its `manifest.inputs.sets`, so I03 accepts it (`kge/estate/upfront.py:119-121`), names it in `tests.sets`, and cites its items in `shape_tests[]`. Its set root holds a complete snapshot: every set the pack declares, copied from wherever the pack's declared fixture path puts it (a nested path is allowed, as in `tests/unit/test_upfront_check.py:477`) or from the estate for a pack without a fixture, plus the new set. The candidate's fixture path is the one its own manifest declares. `sync_proposal_pack_to_store` (`abtest.py:83-93`) persists the snapshot with the rest of the candidate, and `proposal.yaml` lists the new set as a review item.
3. The candidate adds one drafted worked example per item, without approval.
4. F12 and S35 report the missing approvals as drafts, and V05 holds the gate until a business user accepts the proposal, a person approves each `reference_sql` and each worked example, and the steward certifies. V07 keeps the template at `specify` until then.
5. **Acceptance or rejection.** Approval promotes; rejection removes. `abtest.discard` (`abtest.py:175`) keeps its job of dropping only the temporary graph and chunks, including in `test_candidate`'s `finally` (`:203`), so the tests survive for review. B4 adds a `reject` transition beside `accept` and `certify` (`kge/estate/onboard/review.py:261-268`). It runs when a reviewer rejects the proposal or the job keeps no candidate, marks the record rejected, and removes `proposals/<id>/` locally and in the packstore, as `clear_open_proposals` already does (`kge/improve/flow.py:74-90`). On acceptance, certification today copies only `pack.yaml` into the pack (`review.py:194`), and the release later deletes the proposal folder (`kge/graph/history.py:1074`). So B4 adds a promotion step to certification: the approved set file is written to the pack's real set root together with the `pack.yaml` that declares it, and certification fails, restoring both, if either write fails.
6. Held-out records are never used as candidate tests: `kge/improve/gather.py` already drops them.

This document fixes the requirements and invariants of the proposal lifecycle and leaves the exact call sites to B4:

| # | Invariant |
|---:|---|
| P1 | The candidate is self-contained: its pack, its declarations and a complete snapshot of every set it declares live under its proposal folder |
| P2 | Every check and evaluation of the candidate reads its sets and declarations from the candidate, never from the profile's pack or estate |
| P3 | Nothing lands in the shared estate or the pack's real set root until the proposal is accepted |
| P4 | Acceptance promotes the declaration and the set file together, atomically: both land, or neither does and the pack is restored |
| P5 | Rejection, or a run that keeps no candidate, removes both the declaration and the file; temporary graph and chunk cleanup removes neither |
| P6 | Without a candidate, every reader reads exactly what it reads today |

P2 means the candidate's pack and set root must reach every reader. The readers known today:
- the up-front checks' `_set_paths` (`upfront.py:88-99`);
- `margin.py`'s `missing` (`:145`) and its set choice (`:450`, `:480`);
- the harness's `run` (`kge/eval/harness.py:283`) and `choose_sets`.

B4's first task is to trace every reader of sets or declarations from `test_candidate` down, and its pull request lists them. Today `test_candidate` (`abtest.py:192-205`) changes only the graph hash, which is why P2 is not met now.

### 4.3 Stages, and which answer is served

The seam is inside `planrules.read`. Most of it (`:369-564` and `:569-626`: contexts, units, counts, membership, dimensions, calendar, filters and the entity selector) binds the question's names to declarations. That is slot binding: it stays platform code, and both adapters use it. Only the two shape steps, the calls to `planshapes.compose` (`:565`) and `planshapes.parts` (`:627`), are what a template replaces. One binding step lives in `planshapes.py` today: `shared_contexts` (`:11-27`) gives a bare "current" side the noun of the other side, and is reached only when a comparison word is present (`planrules.py:401`). B2 moves it into slot binding, run whenever a template needs two context slots, so a template without "vs" can bind both sides.

The coded and the graph planner are two adapters of one rules-planner port, so the caller (`planpublic.py:157`) never branches on which runs. The graph adapter matches the ledger against the templates, expands the matched recipe with the slot bindings, and returns a `Propose`, `Clarify` or `Abstain`, as `planrules.read` does. Both run on every question that reaches the rules path, and, while any shape is below `retired`, on every question so that shadow evidence accrues even when the model's plan is served. Both are CPU-only; neither calls the model or the warehouse.

| Group's stage | Served from the rules path | The other path | Logged |
|---|---|---|---|
| `specify` | Coded (or a decline, if none exists) | Graph template not run live; its worked examples and tests run at build | Build-time results only |
| `shadow` | Coded; the graph's plan only under the decline takeover | Graph runs, not served except under the takeover | An observation for every comparison; review evidence for each disagreement and each takeover |
| `graph_first` | Graph, when it proposes and agrees with coded or coded declines. Where both propose different plans, coded is served | Coded runs; it is the fallback when the graph declines | As `shadow`, with each fallback marked |
| `retired` | Graph | Coded builders of this group removed | An observation for every question the shape matched, and each graph decline |

**Coupled groups.** Templates compose (§3.1), so one question can use several shapes, and the stage table above selects a whole plan from one adapter. Stages therefore apply to groups, not to single shapes. Two shapes are coupled when some parsed question can match both and no clarification rule or `forbids` separates them; S33 computes this from `requires`, `forbids` and the slot counts. A group is a connected set of coupled shapes, listed by the compiler. Every shape in a group:
- holds one stage;
- advances only when each of its shapes meets that stage's thresholds in §5;
- retires together with the rest of the group, in one change that removes all the group's coded builders.

A question is served at the stage of the group of the shapes it used, so a question never mixes a retired builder with a shadow-stage one. A born-in-graph shape coupled with coded shapes joins their group, with one exception, the decline takeover below. In today's code the branches of `compose` and `parts` combine freely (time grain, range, comparisons, ratio, ranking, parts), so they are expected to form one group. That makes retirement of the coded shapes all at once, not shape by shape; the owner accepted that consequence (Q14).

Serving coded on a disagreement during `graph_first` follows A4: the coded plan has its figures held by parity and the golden sets, and a disagreement at that stage means the bar was met on evidence that did not cover this question. The graph stage still adds value at `graph_first`: questions the coded planner declines are answered.

**Decline takeover.** A born-in-graph shape answers questions the coded planner cannot, so holding it to its group's `shadow` stage would keep declining them for no safety gain. While its group is at `shadow`, the graph's plan is served for a question only when all of these hold:
1. The coded planner declined the question (abstain or clarify), so there is no coded plan to disagree with.
2. The graph's plan used at least one born-in-graph shape, and every such shape has met the bar in Q2: 100% of its tests passed and 0 wrong figures against approved `reference_sql`, in this release event.
3. Every other shape the plan used, each a mirror of a coded shape, has M1 at 100% and M2 at 0 on its tests in this release event, and its worked examples replay (V06).
4. No clarification rule fires on the question.

Otherwise the coded decline is served. Each takeover is recorded as an observation marked `takeover` and goes to steward review, every one, for the shape's first two release events (Q2). A takeover reviewed as wrong suspends takeovers for that shape until a release event whose tests include the question. This is a proposal for the owner (Q15). Without it, a born-in-graph shape coupled to the coded shapes answers only once its group reaches `graph_first`.

A shape born in the graph (written after B2, with no coded builder) has no planner to agree with. The proposal is that it goes from `specify` to `graph_first` when its tests pass with zero wrong figures against approved `reference_sql`, and that every live question it answers in its first two releases is reviewed by the steward. That redefines the issue's agreement metric for such shapes; the owner approved it (Q2).

### 4.4 How comparisons are recorded

**Observations.** Every comparison is recorded, whether or not the planners agree, because M3, M5 and M6 need totals, agreements and both planners' declines. One observation per compared question holds:

- the release id being served, a fresh observation id and the time;
- the shapes the graph matched (or `none`), and the coded shapes, attributed by the plan's node-kind signature (the recipe each coded branch maps to), so the coded planner needs no new labels;
- each planner's outcome kind (`propose`, `clarify` or `abstain`);
- the comparison result: `agree` (both propose the same normalised plan, or both decline with the same outcome kind), or one of three kinds of disagreement: plans differ; one proposes and the other declines; both decline with different kinds;
- which planner was served, and whether it was a fallback.

A question that used several shapes counts towards each. The observations are append-only records written through a new `ShadowLog` port, with an object-store adapter for the pilot and a local-file adapter for the demo, so no request writes to the graph store or the warehouse. The fold job (§6) sums them per release and shape into durable counters on `ShapeStatus`: compared, agreed, each kind of disagreement, graph declines, coded declines, fallbacks. M3 is agreed over compared, M5 is compared, and M6 sets graph declines against coded declines, all over the same observations. The run trace (`kge/services/ask/engine/runtrace.py`) carries the observation id in a `shadow` block, and the Map's trace (`kge/services/ask/engine/contextpath.py:323`, `plan_refusal`) shows which planner's plan was served, as it now shows a rules replacement.

**Releases.** Two things are kept apart. Each observation records the **release id being served**, the `release` field of the served identity (`kge/adapters/released.py:97-100`). A release makes a new id (`kge/graph/release.py:607`), but a rollback restores the saved identity and its id unchanged (`_rollback_release`, `release.py:343-383`, as `test_old_figure_until_release_then_new_then_ledger_rollback` asserts). So the release id says which release served the question, not when it started serving.

The metrics count **release events** instead. Every release, re-release of the same hash and rollback appends one entry to the release history, `versions.jsonl` (`kge/graph/history.py:39`, written by `record_release`, `:975`, called at `release.py:637` for a release and `:382` for a rollback). Each entry carries its time (`released_at`) and whether it is a rollback (`rollback_of`). The fold job reads the history in that order. It assigns each observation to the event in force at the observation's time, and checks that the observation's release id is the one that event served.

M7's "consecutive" means adjacent release events in the history. A rollback event breaks a run, whatever it restores, and so does an event on which a shape falls short, including on M5's volume.

**Review evidence for a disagreement.** The question-run store cannot carry review: a record exists only for a background request (`kge/services/ask/routes.py:110`, `kge/services/ask/service.py:219`), and only its asker can read it, on the same release and access (`service.py:239-243`). So a disagreement's observation carries its own evidence, chosen to need no new permission:

| Kept | Not kept |
|---|---|
| Both normalised plans: names of declared terms, contexts, measures, columns, calendars and periods, operations, and typed scalars | The question text |
| The ledger's pieces as kinds and operations, with the span positions each plan applied | Any figure, row or SQL result: neither planner reaches the warehouse |
| The two outcomes, the matched shapes and the served side | The principal: the observation keeps no identity, only the readable units the plans name |

A scalar compared with an entity key column (an account number in an entity selector, `planrules.py:616-626`) is replaced by its type, so the evidence names no customer. What remains is the pack's own vocabulary and the shape of the question, which a steward already sees in the pack they certify.

A steward reviews through the existing steward role, the one that certifies onboarding items (`steward`, `kge/services/onboard/routes.py:192`), on new routes beside Quality's: `GET /shapes/disagreements` and `POST /shapes/disagreements/{id}` to record an outcome (graph right, coded right, both wrong, needs a test). No other role reads observations. When a disagreement can only be judged from the words, the steward writes a test question that reproduces both plans, and that item joins the shape's tests (§5); the asker's own words are never retrieved. Whether the asker may opt to share a question with a steward is settled by decision Q12.

### 4.5 Reuse of the harness for the comparisons

| Harness | Comparison it makes | Change |
|---|---|---|
| Golden sets (`kge/eval/run_golden.py`) | Each shape's test items, with every `also_asked_as` phrasing, on both adapters; figures against approved `reference_sql` | Items carry the shape the graph matched; scoring adds per-shape agreement and wrong-figure counts |
| Parity (`kge/eval/parity.py`) | Every golden and held-out result, field by field, against the frozen snapshot | A rules-planner setting picks the adapter. A mirrored template at `shadow` serves nothing, so it may move no result. A shape that serves new answers (a born-in-graph shape reaching `graph_first`, or the threshold count) moves only the results its tests name, each stated in `parity-stated.json` with its reason |
| Margin (`kge/eval/margin.py`) | The candidate's sets at build | V06 and V07, and the `shapes` section of the report |
| A/B (`kge/eval/harness.py`, `kge/improve/abtest.py`) | Before and after a proposal, item by item | `keep_decision` also requires no shape's agreement to fall |

## 5. Maturity metrics (requirement 4)

Set before work starts, and strict because of A4. All are planning estimates until measured on the pilot. The retire column is the issue's proposal (M1 to M4, M6, M7 and M8). Two parts are this design's additions for the owner to accept or strike: M5, the minimum live volume, and the whole `graph_first` column, the intermediate stage.

| # | Metric | Threshold to advance to `graph_first` | Threshold to retire | Measured by |
|---:|---|---:|---:|---|
| M1 | Agreement with the coded planner on the shape's tests, every phrasing | 100% | 100% | Golden run, both adapters |
| M2 | Wrong figures on the shape's tests (figure differs from approved `reference_sql`) | 0 | 0 | `run_golden.py` |
| M3 | Agreement with the coded planner on live questions in shadow | ≥ 99% | ≥ 99% | Shadow log |
| M4 | Live disagreements reviewed by a steward, each with an outcome | 100% | 100% | Shadow log review state |
| M5 | Live questions of the shape in the window (A7) | ≥ 100 | ≥ 100 | Shadow log |
| M6 | Graph declines as a share of the shape's live questions, against the coded planner's | No higher | No higher | Shadow log |
| M7 | Consecutive releases, each holding M1 to M6 on its own observations | 1 | 2 | Shapes view, per release event in history order (§4.4) |
| M8 | Owner's sign-off | Not needed | Required, recorded in the retirement change | Pull request |

Every disagreement counts against M3, whichever side the reviewer finds right; each reviewed one also becomes a new test item. A disagreement where the graph was wrong resets M7.

## 6. The shapes view in Quality (requirement 5)

Quality today has four panels in the portal (golden results, the A/B harness, the release gate, baselines) and reads files: run directories, `eval.json` and the GATE marker (`kge/services/quality/service.py:152-320`). The shapes view is a fifth panel on the same page; the page's layout and the portal's information architecture stay as they are.

| Column | Source |
|---|---|
| Group, shape, label, `replaces` | `Shape` node; the group as the compiler lists it |
| Stage | `Shape` node (the pack's declared stage, one per group) |
| Tests passed of total; wrong figures; examples replayed | `ShapeStatus`, from the release's margin run |
| Live questions; agreement; disagreements open and reviewed; graph and coded declines | `ShapeStatus`, from the shadow log |
| Releases held | `ShapeStatus` across release events, in history order |
| Ready for | Per group: the next stage whose thresholds in §5 hold for every shape in it, or the shape and metric that stop it |

"Kept in the graph" works as follows: the stage is pack data, compiled. The metrics are `ShapeStatus` nodes in the runtime layer, origin pipeline, the layer the meta-model already gives to what is written while a graph is in use (`meta-model.yaml:121`, `:420-432`). A fold job, run by build-and-gate after the margin run and on a schedule, reads the margin run and the shadow log and writes `ShapeStatus` per shape and release event. Ask's engine reads its graph once at start (`kge/services/ask/engine/core.py:126-170`) and never reads `ShapeStatus`, so the request path is unchanged. The KGE API serves the view at `GET /shapes` in `kge/services/quality/routes.py`; the portal panel reads it.

## 7. The data-not-code line (requirement 6)

| A pack may declare | A pack may not declare |
|---|---|
| Phrases mapped to an operation on the platform's list | A new operation, or a regular expression |
| Templates whose slots are of the platform's slot kinds, matched on operations, slot counts, the platform's lexer markers and the closed position constraints (`before`, `after`, `adjacent`) | Word patterns, conditions or a matching language |
| Recipes composed of the platform's blocks, with slot references, typed scalars and declared ids as parameters | SQL, an expression, a function, or a block the catalogue does not list |
| Worked examples: a question and a `query-plan/v1` plan a person approved | A plan with names the pack does not declare |
| Its stage request for each shape | A stage beyond what V07's evidence allows |

The route for a missing building block:

1. ask diagnoses `missing_building_block` and attaches the evidence: the question, its unapplied spans, and the nearest recipe that fails.
2. author-pack writes the tests first, golden items with approved `reference_sql`, so the platform issue arrives with its acceptance.
3. The platform team raises an issue for the block, general for all clients. The block is added to `plan.py`, `plancheck.py`, `compile.py`, `coverage.operations` (`coverage.py:539`), the model planner's instructions (`planmodel.py:20`) and the block catalogue, and released with the platform.
4. The pack's recipe uses the block once the platform release is deployed; the shape starts at `specify`.

## 8. The first shapes through it (requirement 7)

### 8.1 Operation phrases

The phrase table moves first because it carries no shape logic: it proves the mirror. B2 copies the static entries of `SCAFFOLD`, `SCAFFOLD_PHRASES` and `OPERATORS` (`coverage.py:16-41`, 107 operation phrases) into the preload's base phrases. `_ledger` and `planvocabulary.py:217` read the table from the vocabulary the graph supplies instead of the module constant. The question-dependent rules (`coverage.py:94-137`: `which`, `net`, grouping nouns) stay in the lexer as platform code; they decide how a question is read, not what a phrase means. `their` stays filler and keeps its `membership` reading, as today, until the two-context difference template (B6) gives it a slot.

**A frozen baseline.** The 107 phrases copied in B2 are the preload's phrase baseline, version 1, marked `baseline: true`; the baseline never changes. The coded adapter reads only the baseline, from the module constant until B7 and then from the preload, so it stays the comparison point. The graph adapter, the coverage check and the readable-words check read the full table: the baseline plus every phrase added since.

**New phrases before retirement.** Phrases may be added to the preload's base set, and to a pack, at any time after B2, without waiting for retirement: B5 adds `over` and B6 adds `minus`. A new phrase is not part of the mirror.
- An observation whose graph-side ledger used a non-baseline phrase is marked `new_phrase`. It is compared against the coded planner like any other observation and counted in its own `new_phrase` counter; it is not counted towards a mirrored shape's M1, M3 or M6.
- A question that turns on a new phrase is judged as a born-in-graph shape is: on its tests and approved `reference_sql`, with live answers reviewed (Q2).
- A new phrase also changes what the coverage check accepts from a model plan, so its pull request states the answer moves in `parity-stated.json` (§9.1).

Acceptance for the mirror: on every golden and held-out phrasing, the ledger from the baseline equals the ledger from the constant, piece for piece; parity shows no moved result; the shadow log shows 100% agreement on baseline-only questions on the pilot for two consecutive releases. Then B7 retires the constant's entries and the coded adapter reads the baseline from the preload.

### 8.2 The threshold count

A new building block, per-entity aggregation, and its recipe and template.

| Part | Design |
|---|---|
| Lexer | `literal` reads a number with thousands separators (`1,000,000`) as one literal, as `amount` does after `$`. Platform code, in B5 |
| Phrase | `over` marks `gt`, in the preload's base set: it is general, not Meridian's |
| Block | `per_entity`, a new measure output kind beside `distinct_count` in `MeasureOutput` (`plan.py:268`): `{kind: per_entity, id, measure: Name, context: Name, mode: Mode, filters: list[Predicate], entity: Name, path: list[Name], having: list[{op, value: Scalar}], reduce: count}`. It aggregates the measure per entity, by every component of the entity's declared key, keeps the entities whose total passes every `having` test, and reduces them. As a measure output, the per-entity totals are never a visible output, and plan.py's rule that every plan has a measure or count output holds. `reduce` starts with `count` only; another reduction is a later block if a pack's tests need it |
| Check | `plancheck.py`: the entity is declared with a complete key; a declared role path reaches its table from the context; each `having` value is a number of the measure's type |
| Compile | `compile.py`'s `Builder`: an inner aggregate grouped by the entity key, the output predicate after grouping (as `:778` does now), and an outer `COUNT(*)`. A count over no qualifying entities returns the figure 0, not a decline. The gate checks the statement unchanged; B5 confirms that `sqlguard.check` accepts the nested aggregate and adds a gate test for it |
| Recipe | `per-entity-threshold-count`: one `per_entity` block (`measure: $measure`, `entity: $entity`, `having: {op: $comparison_op, value: $number}`, `reduce: count`); the period slot binds the plan's time as slot binding does today |
| Template | `threshold-count`: slots `measure`, `entity`, `comparison_op`, `number`, `period`; requires `count` and one comparison operation |

Acceptance, Meridian, January 2025, with the issue's warehouse-checked answers. Each row is a golden item with three `also_asked_as` phrasings and an approved `reference_sql`, and a worked example with its approved plan.

| Question (January 2025, certified NNA per account) | Answer |
|---|---:|
| Accounts with certified NNA (the per-entity population) | 154 |
| Accounts with certified NNA over 1,000,000 | 0 |
| Accounts with certified NNA over 100,000 | 45 |
| Accounts with certified NNA over 250,000 | 12 |

The 0 must come back as the figure 0. A decline on that item counts as a failed test, not a safe outcome.

When B5 lands, no mirrored template exists yet, so the threshold count is a group of its own. It is served at `graph_first` once it meets Q2's bar. After B6, if it couples with the mirrored shapes, it joins their group and keeps answering through the decline takeover (§4.3).

### 8.3 Which phase closes which example

| Example | B2 (phrases mirrored) | B5 (threshold count) | B6 (mirrored shapes and two-context difference) |
|---|---|---|---|
| certified NNA and current NNA, with their difference | Not closed; B2 gives slot binding the two-context step it needs | Not closed | Closed by the two-context difference template, which consumes `their` as its slot reference, served through the decline takeover (§4.3; Q15) |
| certified NNA minus current NNA | Not closed: `minus` alone marks a difference with no comparison to derive it from (`planshapes.py:86`) | Not closed | Closed by the same template, the same way; `minus` added as a phrase |
| how many accounts had certified NNA over 1,000,000 | Not closed | Closed: lexer, `over`, block, recipe, template | Already closed |

## 9. The build issues (requirement 8)

One issue per phase, in this order. Two issues that change the same files are dependent (AGENTS.md), and the order below respects that.

| # | Proposed title | Scope | Depends on | Main files |
|---:|---|---|---|---|
| B1 | Shape sections in the pack: phrases, shapes, recipes, clarification rules, shape tests and worked examples (meta-model 1.6.0) | The six sections, `ShapeStatus` and the other node types, S32 to S36, F12, the preload's format 2 with the base phrases, the block catalogue, the approval fields in the sets, the optional `part` flag on `terminology.predicates`. No runtime change; parity unchanged | None | `meta-model.yaml`, `kge/compiler/checks.py`, `kge/compiler/emit.py`, `kge/compiler/standards.py`, `standards/preload.yaml`, `kge/semantics/blocks.py` (new), `kge/estate/upfront.py`, `kge/eval/sets.py`, `kge/graph/load.py`, `kit/INSTALL.md`, `docs/use-cases/kge/platform/pack-contract.md` |
| B2 | Graph rules planner beside the coded one, in shadow; operation phrases mirrored | The rules-planner port with coded and graph adapters (the graph one composing templates in block order, §3.1), the shadow comparison and `ShadowLog`, the phrase table read from the graph, `shared_contexts` moved into slot binding, the lexer markers (`from_to`, `asks_one`, `measure_part`) and the `range` slot, observations for every comparison through `ShadowLog`, the run-trace `shadow` block. Zero added warehouse jobs per request | B1 | `kge/semantics/coverage.py`, `kge/semantics/planvocabulary.py`, `kge/services/ask/engine/core.py`, `planpublic.py`, `planrules.py`, `planshapes.py`, `planner.py`, `runtrace.py`, `contextpath.py`, a new graph-planner module in `kge/services/ask/engine/`, `kge/ports.py`, a `ShadowLog` adapter |
| B3 | Shape maturity in the margin gate and a shapes view in Quality | V06, V07, per-shape scoring in golden, parity and A/B, the margin report's `shapes`, the fold job and its counters, `GET /shapes`, the steward review routes for disagreements, the portal panel | B2 | `kge/eval/margin.py`, `run_golden.py`, `parity.py`, `kge/improve/abtest.py`, `kge/services/onboard/service.py`, `routes.py`, `kge/services/quality/routes.py`, `service.py`, `portal/ui/src/pages/Context/Quality.js` |
| B4 | The onboarding skills carry the shape lifecycle | The skill changes in §4.2, the diagnosis outcomes, the widened improve allowlist, the proposal lifecycle meeting invariants P1 to P6 (§4.2): a self-contained candidate with a complete set snapshot, the candidate's pack and set root passed to every reader of sets or declarations, promotion on certification, and a separate `reject` step; the onboarding guide | B1, B3 | `skills/author-pack/SKILL.md` and its examples, `skills/ask/SKILL.md`, `skills/build-and-gate/SKILL.md`, `skills/improve/SKILL.md`, `skills/land-and-draft/SKILL.md`, `kge/improve/rules.py`, `kge/improve/edits.py`, `kge/improve/abtest.py`, `kge/improve/flow.py`, `kge/estate/onboard/review.py`, `kge/estate/upfront.py`, `kge/eval/margin.py`, `kge/eval/harness.py`, `kge/eval/sets.py`, and any further reader the trace finds; `kit/ONBOARDING.md` |
| B5 | Threshold count: per-entity aggregation block, its recipe and template, on Meridian | The lexer's grouped numbers, `over`, the `per_entity` block through plan, check, compile, coverage and the model planner's instructions, the recipe and template, Meridian's tests and worked examples; acceptance in §8.2 | B2, B3 | `kge/semantics/coverage.py`, `plan.py`, `plancheck.py`, `compile.py`, `blocks.py`, `kge/services/ask/engine/planmodel.py`, `standards/preload.yaml`, `clients/meridian/pack.yaml`, a Meridian question set, gate tests for the nested aggregate |
| B6 | Mirror the coded shapes as templates; add the two-context difference | One template and recipe per branch of `compose` and `parts`, each with `replaces` and its position constraints, and the clarification rules of §3.1, in the preload, at `shadow`; Meridian's `shape_tests` and worked examples for each, including combined questions; the two-context difference template with `minus`, born in the graph. Pack and preload data only. The mirrored templates move no answer; the two-context difference moves the declines its tests name into figures (§9.1) | B4, B5 | `standards/preload.yaml`, `clients/meridian/pack.yaml`, Meridian question sets |
| B7 | Retire the coded shapes that meet the bar | For each coupled group the shapes view marks ready and the owner signs off: remove all the group's branches from `compose` and `parts` in one change, and the phrase constants once mirrored; when the last group goes, the coded adapter and the two calls in `planrules.read` (`:565`, `:627`) go too. Slot binding stays. Raised when the first shape is ready; one pull request for the shapes ready at that time | B6, two releases of evidence, M8 | `kge/services/ask/engine/planshapes.py`, `planrules.py`, `kge/semantics/coverage.py` |

B4 and B5 can run in parallel: they change no file in common. B5 touches `coverage.py` after B2 and before B7, so those three are sequential. Every build issue changes files under `demos/kge/`, so each merge publishes the next KGE kit, and each pull request says so. This document changes only `docs/` and publishes none.

### 9.1 Expected answer changes and checks

The checks are those `.claude/skills/kge-checks/SKILL.md` gives for each kind of change. Every build issue changes demo code, a pack or the meta-model, so every one runs demo code's checks: `make lint`, `make test-unit`, `make test-integration`, `make parity`, `make margin REPEATS=1` and the repository checks. A pack or meta-model change adds `make onboard` and `make check compile` first; a planner, plan-gate or evaluation change adds `make grounding-eval`.

| # | Expected answer changes | Checks beyond demo code's |
|---:|---|---|
| B1 | None: new optional sections, no runtime reads them | Pack and meta-model: `make onboard`, `make check compile`; every pack in `clients/` still compiles |
| B2 | None: the graph adapter runs in shadow only; the ledger from the graph table equals today's on every phrasing | Planner: `make grounding-eval`; warehouse jobs per request counted before and after (zero added) |
| B3 | None: evaluation and reporting only | Evaluation: `make grounding-eval`; the portal UI tests for the Quality panel (`npm test` in `portal/ui`); the steward-only access to the review routes |
| B4 | None: skills, diagnosis outcomes, the improve allowlist and the proposal lifecycle; a margin run without the new arguments reads what it reads today | Evaluation: `make grounding-eval`; the improve job's, review transitions' and up-front checks' focused tests, one per invariant P1 to P6, including a fixtureless candidate, a nested fixture path, a failed promotion that restores the pack, and a rejected proposal leaving nothing in the estate or the packstore; the onboarding guide tests the kge-checks skill names |
| B5 | The Meridian threshold-count questions, declined today, return the four figures in §8.2; numbers with thousands separators are read whole, so questions that declined on a split number may now answer. Each move is stated in `parity-stated.json` | Pack, meta-model and planner: all of the above; the gate test for the nested aggregate; the recorded model plans against the new schema (R8) |
| B6 | Mirrored templates: none, since their group enters at `shadow`. The two-context difference: through the decline takeover (§4.3), examples 1 and 2 of §1.2 and the tests it is given move from "No answer" to figures as soon as B6's release passes its tests, each stated in `parity-stated.json`. If the owner declines the takeover (Q15), they stay "No answer" until the group reaches `graph_first`, and B6's acceptance is the tests passing on the graph adapter at build, with no served move | Pack: `make onboard`, `make check compile`; parity with exactly the stated moves; M1 at 100% on every mirrored shape's tests |
| B7 | None: only shapes whose live answers already agree are retired | Planner: `make grounding-eval`; parity unchanged |

## 10. Risks

| # | Risk | Treatment |
|---:|---|---|
| R1 | A template matches a question the coded planner read differently, and the graph's plan is served with a wrong figure | A mirrored template's plan is served only once its group is at `graph_first`, after M1 to M7, and on a disagreement there the coded plan is served (§4.3); every disagreement is reviewed (M4). The one exception is the decline takeover (§4.3), which depends on the owner accepting Q15. It serves a graph plan at `shadow` only where the coded planner declined, so there is no coded figure to contradict. Its conditions: every born-in-graph shape in the plan has passed all its tests with 0 wrong figures; every mirrored shape in it has M1 at 100% and M2 at 0 in this release event; no clarification rule fires. Its safeguard: every takeover is reviewed for a shape's first two release events, and one reviewed as wrong suspends takeovers for that shape until a release event whose tests include the question |
| R2 | Templates grow into a pattern language, code in data form | Matching is on operations and slot counts only (§3.1); S33 refuses overlapping templates; anything more is a building block |
| R3 | The phrase mirror changes the coverage check for model plans, so model answers move | B2's acceptance is ledger equality on every phrasing and no parity move |
| R4 | The shadow run slows the request path | Both planners are CPU-only; B2 measures the added time per request on the golden set and reports it in the pull request |
| R5 | Too few live questions per shape for M3 to mean anything | M5 and A7: the view reports "not enough evidence" instead of a rate |
| R6 | Retiring a coded shape breaks another client whose pack has no template for it | Mirrored templates go in the preload, loaded into every graph; retirement needs M1 and M2 on every client pack in the repository (Q3) |
| R7 | Shadow observations expose what people ask beyond what today's permissions allow | The observations keep no question text and no identity; a disagreement keeps only the pack-vocabulary evidence in §4.4, with entity key values replaced by their type (Q4) |
| R8 | The new `per_entity` kind changes the model planner's plan schema, and the pilot serves a live model, so answers outside this shape can move; parity pins `MODEL_PROVIDER=none` and would not see it | B5 runs the recorded model plans and the model-contract tests against the new schema, and the owner's live test on the pilot covers the golden questions on the model path before the kit is accepted |
| R9 | A combination of templates builds a plan no coded branch would, or misses one it would | B6's tests include combined questions (ranking with a month breakdown and a range, for example), and M1 runs on them |

## 11. Decisions

The owner approved every proposal below on 2026-11-13. Each is now part of the design.

| # | Question | Proposal |
|---:|---|---|
| Q1 | The issue names meta-model 1.5; the platform is already at 1.5.0 | Number these sections 1.6.0 |
| Q2 | A shape born in the graph has no planner to agree with | Advance it on its tests (100% pass, 0 wrong figures against approved `reference_sql`) and review every live answer for two releases |
| Q3 | Mirrored templates of coded shapes: preload (every client) or each pack | Preload, since the coded shapes are platform-wide; packs add their own |
| Q4 | Where disagreement records live and for how long | `ShadowLog` on the object store: observations with no question text or identity, kept for the two-release window plus review |
| Q5 | Serving on a disagreement at `graph_first` | Serve coded, log for review |
| Q6 | The cost ratio in A4 | A wrong figure costs at least 100 declines |
| Q7 | Minimum phrasings per test item | Three `also_asked_as` (four phrasings) |
| Q8 | Should worked examples also be given to the model planner as examples | Not in this design; a separate issue if wanted. [answer-modes.md §5.2](answer-modes.md#52-the-closest-approved-examples) (#462) proposes that design |
| Q9 | "over" as strictly greater than | Yes; "at least" stays inclusive |
| Q10 | Should a reviewed disagreement where the graph was right still count against M3 | Yes, as written in §5; excluding it would loosen the issue's 99% |
| Q11 | M5 and the `graph_first` stage are additions to the issue's metrics | Keep both: M5 stops a rate on a handful of questions, and `graph_first` lets the graph answer what coded declines before code is removed |
| Q12 | May an asker opt to share a disagreeing question's words with the steward | Not by default: review uses the pack-vocabulary evidence and steward-written test questions (§4.4); an opt-in is a separate issue if wanted |
| Q13 | The shapes view is a portal UI change, and the portal's layout is otherwise frozen | Add it as a fifth panel on the existing Quality page with the existing components and tokens, no new page or navigation; confirm before B3 starts |
| Q14 | Coupled groups make the coded shapes advance and retire together, probably as one group, so no single coded builder is retired early | Accept: it is the simplest rule that never mixes a retired builder with a shadow one in one plan. The alternative, falling back per contribution within one plan, is not proposed |
| Q15 | Serve a born-in-graph shape's plan while its group is at `shadow`, when the coded planner declines (the decline takeover, §4.3) | Accept, with its four conditions and review of every takeover; otherwise the two-context difference, and after B6 the threshold count if it couples with the mirrored shapes, answer only once their group reaches `graph_first` |
