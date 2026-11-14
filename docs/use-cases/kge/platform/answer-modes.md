# Answer modes: certified first, then Gemini mode

| Document control | Value |
|---|---|
| Status | Draft for the owner's approval. The ten open questions were decided by the owner on 2026-11-13 (§12). Nothing here is built yet; the build issues are in §13 |
| Requirement | Issue #462, with the owner's direction of 2026-11-13. Supersedes #457 |
| Basis | Code on `develop` at `1f296a7`; the Meridian examples as the issue gives them |
| Related | [question-shapes.md](question-shapes.md) (#445, approved 2026-11-13): its lifecycle, takeover rule (Q15), maturity metrics (M1 to M8) and decisions (§11) hold unchanged here, except for two decisions of 2026-11-13: D7 (the ownership rule in a separate issue, A5, before B6) and D8 (example matches served where the coded rules decline, before `graph_first`) |
| Scope | The order in which Ask tries its planners; Gemini mode and the change to the word check; the three outcomes and their fields on HTTP and MCP; the score and its threshold; feedback and the route to certification; MCP as the agents' front door; what each screen shows; consistency with #415 and #445; the decisions; the build issues |
| Out of scope | Building any of it; the CX Agent Studio agent's own configuration; moving Ask's orchestration onto LangGraph |

Ask answers a question from the release's certified vocabulary and plans, and only then asks the model. A question whose every word is certified and whose rules plan passes the checks is answered **certified**, with no model call. Any other question goes to **Gemini mode**: the model proposes a plan, may read a word the release has not certified onto a meaning it has, and the answer is labelled as **the model's reading**, with that reading in plain words and a **score** computed from evidence. Where the plan does not pass, Ask is **not sure**: it gives a suggested question and no figure. Ask stays stateless and returns everything in one reply: the figure, the label, the reading, whether a new word was read, and a flag saying whether the person should confirm before seeing the figure. Confirming, and any other human-in-the-loop step, is the calling agent's, per use case (D1, D2). Agents reach Ask through MCP; pages stay on HTTP, with the same fields.

All paths below are relative to `demos/kge/` unless they start with `docs/`. File and line citations describe `develop` at `1f296a7`. Every threshold, count and time in this document is a planning estimate until measured.

## 1. Today

### 1.1 The order since #415

| Step | Where | What it does |
|---|---|---|
| Model or rules | `kge/services/ask/engine/planpublic.py:143-145` (`selected_answer`) | With a model configured for `resolution` or `sql_source` (`kge/services/ask/engine/config.py:10-16`, `uses_model` at `:64`), the interpreter is the model; otherwise the rules |
| Interpretation | `planpublic.py:159-174` (`interpret`) | Calls `planmodel.read` when a model is configured, else the rules planner (`rules`, `:156-158`: `planrules.followup` or `planrules.read`) |
| Rules floor | `planpublic.py:175-176` (`floor`), `:191-193` | The rules plan is passed as the fallback |
| Check and fall back | `kge/services/ask/engine/planner.py:344-365` (`answer`) | The model's plan is checked first (`prepare`, `:383-420`); only if it is refused is the rules plan prepared and used (`:348-359`), and the refusal is recorded (`:360`, and `planpublic.py:195-198` as the trace step `model_plan_refused`) |
| Model call | `kge/services/ask/engine/planmodel.py:471-513` (`read`) | Up to two attempts (`:487`), the second carrying the first one's reply-validation errors. A reply still invalid on the second attempt (`:508`), or any other failure of the call (`:510`), goes to `no_fallback` (`kge/adapters/model/errors.py:174-190`): on the pilot's cloud path it stops the request with `NoFallback`; off it, it returns, the read ends as an abstain and the rules plan is used |

So a question the rules plan in about a second still waits for the model, and the model's plan wins wherever both pass.

### 1.2 The word check

`ground_plan` (`kge/semantics/coverage.py:1024`) replaces the model's evidence with the rules planner's ledger: "Selections remain untrusted; their certificates come from the rules planner's ledger." `check_plan` then calls `coverage` (`kge/semantics/plancheck.py:453-455`, `coverage.py:758`), which refuses a plan when:

| Rule | Where |
|---|---|
| A ledger piece of kind `unknown` (a word in no certified table) is applied | `coverage.py:811-812`; unknown pieces are made at `:235`, `:361`, `:368` |
| A piece is not applied to any plan node | `coverage.py:890` |
| An operator piece's operation is not one of its target's operations | `coverage.py:846-871` |
| A comparison or derived node is not owned by a piece whose operation is `comparison` or the node's own kind | `coverage.py:894-899` |

The word tables are fixed at import: `SCAFFOLD` (filler, `coverage.py:16-18`) and `OPERATORS` (`:19-41`). `minus` is in neither, so "certified NNA minus current NNA for March 2025" ends at `:811` ("No answer: minus"). `their` is both filler and `membership` (`:18`, `:20`), and a difference needs the two-context comparison as its base (`plancheck.py:426`) which no word in the third example owns (`coverage.py:894-899`), so "… with their difference" ends as "No answer: their; difference".

### 1.3 How agents and pages reach KGE

| Caller | Today | Where |
|---|---|---|
| MCP, context tools | `get_context`, `retrieve`, `map_term` at `/mcp` | `kge/services/ask/mcp.py:21-52`; mounted at `kge/app.py:416` |
| MCP, answer tools | `ask`, `list_runtimes` at `/mcp/answer` | `mcp.py:55-76`; `kge/app.py:418` |
| MCP, assistance | `assist` at `/mcp/assist` | `kge/services/assist/mcp.py`; `kge/app.py:422` |
| MCP transport | Stateless streamable HTTP answering in JSON; a bearer token is required and a cookie never accepted | `kge/services/http.py:311-318` (`mcp_server`), `:263-269` (`mcp_principal`) |
| MCP `ask` | Runs synchronously on a worker thread, no background run | `mcp.py:83-98` (`tool`) |
| HTTP `/ask` | Synchronous, streamed, or a background question run (`background=true`), followed at `GET /ask/runs/{run_id}` | `kge/services/ask/routes.py:63-108`, `:134-157` |
| Question runs | Time limit 900 s by default, at most 1,200 s; poll every 2 s; the first reply waits 5 s before going to the background | `kge/pipeline/questionrun.py:23-24`, `:27-28`, `:31` |
| Reference agent | LangGraph loop (`kge/services/ask/engine/agent.py:297-308`). Fetches the plan vocabulary through `POST /context`, over HTTP at `CONTEXT_URL` or in-process over ASGI; then runs the shared coordinator in its own process (`plan_step`, `agent.py:924-931`) | `kge/services/ask/engine/context.py:130-153` (`post`), `:156-166` (`in_process`, which imports `kge.app`) |
| The layer exception | `("kge.services.ask.engine.context", "kge.app")`: "the sample agent posts /context to the API's own app in this process, by design" | `tests/unit/test_layers.py:59`; kept honest by `test_every_exception_is_still_needed` (`:172`) |
| Portal catalogue agent | The portal's Ask chain calls KGE `POST /assist` over HTTP, forwarding the person's cookie or bearer header; on an unreachable KGE it answers from the portal catalogue with the portal's own model, inside one 295 s chain | `portal/server/src/knowledge.js:9`, `:241`; `portal/server/src/kge.js:3-9`, `:19`; `portal/server/src/catalogue-agent.js:71` |

MCP client code exists in `context.py` only for the module's command-line diagnostic: `fetch` (`context.py:190`, with `--via mcp`) opens a streamable HTTP session to `/mcp` and calls `get_context`. The `Agent` class does not use it; it reaches KGE through `post`.

### 1.4 What the checks run on

| Check | Model | Where |
|---|---|---|
| `make parity` | None: `MODEL_PROVIDER=none` | `Makefile:393-394` |
| `make margin` and its baseline | None; a baseline must have run on rules | `Makefile:387-388`; `kge/eval/margin.py:134` |
| `make grounding-eval` | Recorded model replies, no live call: arm C (rules), arm A (model alone), arm B (model first with the rules floor, the #415 path). It fails if B loses an item C got right (`b_loses_to_c`) or B gives a wrong figure | `kge/eval/grounding.py:345-440`; `tests/eval/test_grounding_eval_gate.py` |
| The frozen agent test | Rules and the mock model's modes; each answer and its run trace (model calls included) sealed | `tests/test_agent_loop.py:66-72`, `:158-187` |

## 2. The pattern this follows

Semantic-layer question answering, as several analytics products and open-source text-to-SQL frameworks practise it, has six parts. Each maps onto something KGE has or this design adds.

| Part of the pattern | In general | Here |
|---|---|---|
| The model never writes SQL | The model selects metrics, dimensions and filters from a governed semantic layer; a compiler writes the query | Already so: `planmodel.py:22-31` forbids SQL; `kge/semantics/compile.py` and the SQL gate (`kge/pipeline/sqlguard.py`) write and check it |
| Verified answers first | A repository of questions with verified queries is consulted before the model | The rules planner, and approved worked examples (#445's `examples[]`), before any model call (§4) |
| The model for the rest, with the closest verified examples | Few-shot prompting with the nearest verified questions | Gemini mode, given the closest approved worked examples (§5.2) |
| Answers labelled | A verified answer is marked as such; any other is marked as generated | Certified, or the model's reading (§6) |
| The interpretation in plain words | The product shows how it read the question rather than only a confidence number | The reading, with each new word and its meaning (§6) |
| Clarification and feedback | Ask when unsure; people mark answers right or wrong, and an administrator promotes good ones to verified | Not sure, with a suggested question (§6); the `feedback` call and the route to a certified phrase (§8) |

**Nothing hand-rolled.** Each part uses what the platform or a framework already provides.

| Need | What provides it |
|---|---|
| Agent protocol | The official MCP SDKs: the Python server KGE already runs (`mcp.server.fastmcp`) and its client, used today only by `context.py`'s diagnostic, and the TypeScript SDK in the portal server |
| Agent orchestration | LangGraph, as the reference agent and the improvement job already use it |
| Long questions | The question-run store behind `background=true` (`kge/pipeline/questionrun.py`) |
| Closest examples | The `Embeddings` port (`kge/ports.py:772`) for the question's vector; the approved examples' vectors are held with the served release in memory and ranked in process (§5.2), a sort over a few hundred vectors rather than a search engine |
| Feedback records | The object store port, through an append-only log in the form of #445's `ShadowLog` |
| Learning from feedback | The improvement job (`kge/improve/flow.py`) and #445's proposal lifecycle (B4) |
| Certification | The onboarding review transitions (`kge/estate/onboard/review.py`) and the steward role |

Two pieces are this platform's own code because nothing outside it knows its checks: the score (§7) and the phrasing of a plan as a question (§5.4). Both are small and deterministic.

## 3. Assumptions

| # | Assumption | How it is tested |
|---:|---|---|
| H1 | A wrong figure costs at least 100 declines (question-shapes.md A4, decided as Q6). It governs every threshold in §7. The one rule not held to it is D1's: a reading with no new word is shown at once, on the owner's decision (§7.2) | Owner's decision already recorded; restated, not reopened |
| H2 | On the Meridian pilot most questions are answered by the rules, so rules first removes the model call from most requests | A1's build issue counts model calls per request on the golden, held-out and pilot UAT sets before and after |
| H3 | The rules plan a question in about a second (the issue's figure); a model call takes several | A1's build issue measures the median and 95th percentile per path on the pilot |
| H4 | A plan whose every name, context, measure, join, calendar, period and predicate value passes `check_plan`, and whose SQL the gate passes, gives a certified figure for what the plan says, whoever proposed it | Already the basis of #415's model path; unchanged here |
| H5 | An agent platform's tool call has a time limit shorter than Ask's longest questions; the business users' agent's exact limit is not known yet | Not tested by Ask: under D2 each agent handles a long wait for its own use case (§9.2) |
| H6 | Recorded model replies in the grounding eval are representative enough to calibrate the score's first threshold, and live feedback then extends them | §7.3: at least 300 judged readings at a score before a reading with a new word at that score is shown without confirmation |

## 4. The order (requirement 1)

### 4.1 What counts as a full match

A question is a **full match**, answered **certified** with no model call, when all of these hold:

1. The rules-planner port proposes a plan: today `planrules.followup` or `planrules.read` (`planpublic.py:156-158`); after #445's B2, the coded or graph adapter at the stage its group holds, including the decline takeover (question-shapes.md §4.3, Q15).
2. Every meaningful ledger piece of the question is a certified word: no piece of kind `unknown`, so the word check passes without a reading.
3. `check_plan` accepts the plan (`planner.py:417`), it compiles, and the SQL gate passes every statement.

Where 1 and 2 hold and `check_plan` accepts the plan, but the compiler or the SQL gate refuses it, the answer is a **certified decline**, as today on the rules path; it does not go to Gemini mode. Every word is certified, so the model would plan from the same words, and a second dry run would add a warehouse job to a question already refused (§13.3, the jobs budget).

A plan served under #445's decline takeover is labelled certified. The label rests on the release, not on a review: every word in it is certified, and Q15's four conditions hold for it in this release event (question-shapes.md §4.3), which include that each born-in-graph shape it used passed 100% of its tests with 0 wrong figures against approved `reference_sql` and that each mirrored shape it used has M1 at 100% and M2 at 0. Steward review of takeovers comes after serving, as observations marked `takeover`, and only for a shape's first two release events; a takeover reviewed as wrong suspends that shape's takeovers. Context shows the plan as answered by the graph planner under the takeover (§10).

### 4.2 Matching an approved worked example

An approved worked example is #445's `examples[]` entry: a question and the `query-plan/v1` plan a person approved for it, with `approved_by` and `approved_on` (question-shapes.md §3.1, S35). A question matches an example when the two are equal after normalisation: case folded, whitespace collapsed, and trailing punctuation removed. The example's approved plan is then checked against the question exactly as a rules plan is (step 3 above), with its evidence rebuilt from the question's own ledger by `ground_plan`.

The match is looked up inside the graph adapter of the rules-planner port, not as a separate tier, so the caller never branches on where a plan came from. That keeps it consistent with V06, which already requires the graph planner, given an example's question, to propose exactly the approved plan: an exact match is the cheapest way to that plan, recorded as "verified example `<id>`" in the trace. Drafts at `specify`, without approval, are never matched.

**When a match is served (D8).** Under #445's stage rules alone, a group at `shadow` serves the graph plan only where the coded planner declines and all four takeover conditions hold (question-shapes.md §4.3), so "verified example first" would hold in full only from `graph_first`. The owner decided otherwise: where the coded planner declines, an exact match is served at any stage, once that example replays (V06) in the release event. It is recorded as an observation marked `takeover` and reviewed as Q15 sets out, and it is never served over a coded answer, so #445's rule that the coded plan is served on a disagreement holds. It is labelled certified (§4.1).

Example matching depends on #445's B1 (the section) and B2 (the graph adapter); it is in build issue A4 (§13).

### 4.3 What is tried next, and the setting

| Order | Step | Model call |
|---:|---|---|
| 1 | The rules-planner port (with exact example matching after A4) | No |
| 2 | Full match (§4.1) | No: answered certified |
| 3 | Gemini mode (§5), if a model is configured | Yes, one call of up to two attempts |
| 4 | Not sure (§6), with a suggested question | No further call |

| Setting | Values | Default | Effect |
|---|---|---|---|
| `ASK_ORDER` | `rules_first`, `model_first` | `rules_first` | `model_first` keeps the #415 order: the model's plan is checked first and the rules plan is the floor. Under either order the labels mean the same thing: a served plan is certified only if it equals the rules plan after #445's normalisation and every word is certified |

`ASK_ORDER` is read as `settings().ASK_ORDER` in `kge/pipeline/settings.py`, documented in `.env.example` and the kit's image settings (`kit/INSTALL.md`), and an unknown value stops start-up and names the setting, as the kge-code skill requires. With no model configured, as in `make parity`, `make margin` and local runs, the order has no effect: step 3 is skipped.

### 4.4 The examples under the new order

The "After A1" column is expected, not observed: it depends on the phrasing of §5.4, not yet built, and on what the model replies. Each row is one of A1's acceptance cases.

| Question (Meridian) | Today | After A1 | After #445 B6 |
|---|---|---|---|
| NNA for Q1 2025 by region | Answered, after the model call | Certified, with no model call | Certified |
| certified NNA minus current NNA for March 2025 | "No answer: minus" | Not sure, suggesting "certified NNA vs current NNA for March 2025, difference", which the rules answer certified (§5.4) | Certified: B6 adds `minus` as a phrase and the two-context difference template, once A5 widens the ownership rule (D7) |
| certified NNA and current NNA for March 2025, with their difference | "No answer: their; difference" | Not sure, with the same suggestion: `their` is a certified word with another meaning, which Gemini mode may not re-read (§5.3) | Certified, through the template's slot reference (question-shapes.md §8.3), once A5 widens the ownership rule (D7) |

## 5. Gemini mode (requirement 2)

### 5.1 When it runs

Gemini mode runs when the rules-planner port does not give a full match and a model is configured. It is one call to `planmodel.read`, with the same two attempts (`planmodel.py:487`); the second attempt is for a reply that fails parsing or schema validation, not for a plan the checks refuse. On the pilot's cloud path, a reply still invalid on the second attempt (`:508`) or a call that fails (`:510`) stops the request with `NoFallback`, a 503, as today; it is never turned into "not sure", which would hide an outage as a decline. Off the cloud path `no_fallback` returns (`kge/adapters/model/errors.py:174-190`) and Gemini mode ends as not sure. A plan that parses but that `check_plan` refuses (`planner.py:417`) gets no second attempt: under `rules_first` the rules have already declined, so the answer is not sure, with a certified phrasing as its suggestion where §5.4 finds one. Under `rules_first` a certified answer never depends on the model being reachable.

### 5.2 The closest approved examples

| Choice | Proposal | Why |
|---|---|---|
| Which examples | Approved worked examples only (`approved_by` present), of shapes above `specify` | Drafts are not verified answers |
| How many | The 3 closest | A planning estimate; raised only if the grounding eval shows a gain |
| How found | Each approved example's question is embedded when the release is made, and the vectors are held with the served release in process memory, loaded with the graph from the `WorkedExample` nodes (question-shapes.md §3.2), as per-request access data has been held since #181. The question is embedded by the `Embeddings` port and the examples are ranked in process by cosine similarity; ties broken by overlap of the ledger's operations and slot kinds. Only vectors of the embedding model in use are compared, as the chunk search already requires (`embedding_model`, `kge/adapters/retrieval/bigquery.py:351`) | **Zero warehouse jobs per request.** The `Retrieval` port is not used: it searches chunks, not examples, and on the pilot (`RETRIEVAL_BACKEND=bigquery`, `kit/INSTALL.md:372`, `:1131`) each call is a `VECTOR_SEARCH` job on BigQuery (`search`, `bigquery.py:317`). The embedding call is a model call, small beside the one it precedes. The jobs budget in A1's and A4's checks holds this (§13.3) |
| Per-person access | An example is a candidate only if every unit its plan names is one the person may read (`readable_units`), filtered in memory before ranking | The prompt never carries a plan over data the person cannot see |
| Where in the prompt | A separate `examples` field of the payload, not inside the vocabulary bundle | Inside `bundle` (`planmodel.py:56-68`) they would change `digest(public)` and so the `recording_key` (`planmodel.py:95`), and every recorded reply would stop replaying. In a field of its own the key holds, so a recorded reply made before A4 replays the reply given without examples: the recordings cannot show the examples' effect. A4 therefore re-records the model replies with a live model on the owner's run, and the effect is measured there (§11.3, §13.3). The prompt's hash, recorded in the run trace (`planmodel.py:490`), moves |

This is the "separate issue" question-shapes.md's Q8 left open: worked examples given to the model planner. It is build issue A4.

### 5.3 Reading a new word

The model's reply gains an optional list of readings: each names the span of one word the ledger marked `unknown` and the meaning the model gives it. The rules:

1. **Only an uncertified word is read.** A reading's span must be exactly one `unknown` piece. A certified word keeps its certified meaning, even where the model would read it otherwise: `their` is `membership` until a steward certifies something else.
2. **Only onto a certified meaning.** At launch, a meaning is one operation from the platform's closed list, the operations of `OPERATORS` (`coverage.py:19-41`) and #445's `phrases[]`. A declared name (a measure, context, column or value) is not a meaning a word may take live; the owner decided operations only (D3, §12).
3. **Few words.** At most 2 readings per question, a planning estimate. More, and the question is not sure.
4. **No new values.** A reading never supplies a filter value, number, period or entity key; those still come from literal and time pieces the existing checks hold (`coverage.py:828-845`, `:872-874`).

The **word check changes in one place**. Before `coverage` runs, each accepted reading becomes a ledger piece for that request only: an operator piece with the reading's operation, on the reading's span, in place of the `unknown` piece. `coverage` then runs unchanged: the operator rule (`:846-871`) requires a target whose operations include the reading's, the ownership rule (`:894-899`) still requires every comparison and derived node to be owned, and `:890` still requires every piece to be applied. The overlay is built in `coverage.py` beside `ledger`, read by `ground_plan` and `coverage` through one argument, and applies to model plans and to plans replayed from history, whose readings travel in the history entry's `readings` (§6); the rules planner never produces readings, so its path is untouched.

**What does not change.** `check_plan` (names, contexts, joins, calendars, periods, predicate values, change pairs at `plancheck.py:426`), the compiler, and the SQL gate are applied to the reading's plan exactly as to a rules plan. **Why the figures remain certified:** every element of the plan that decides which rows are read and how they are combined is a certified declaration the checks hold, and the compiler is deterministic. The only uncertified element is the word-to-operation mapping, and that is shown to the person in words, labelled as the model's (§6).

A consequence for "minus": its reading as `difference` passes the operator rule, but the difference needs a two-context comparison (`plancheck.py:426`) that no piece owns, so `coverage.py:894-899` still refuses the plan. That is correct under the rules as they stand, and the person still gets a certified answer through the suggested question (§5.4). The same ownership rule also refuses the plan of #445's born-in-graph two-context difference template, which forbids a comparison word (question-shapes.md §3.1, §8.3): B6 needs a rule that a `difference` or `percent_change` piece owns the two-context comparison its derived node requires when the question names exactly two contexts. That rule is code in `coverage.py`, while #445 approved B6 as pack and preload data only (question-shapes.md §9), so the owner decided it lands in a separate issue, A5, after A1, B2 and B5 and before B6 (D7, §12). This document does not add the rule to Gemini mode alone.

### 5.4 Phrasing a plan as a certified question

Gemini mode renders the model's plan, whether or not it passed the checks, as a question in certified words: the release's phrases for each operation (`vs` for a comparison, `difference` for a difference), its declared labels for names, and its period words. It then runs the rules-planner port on that phrasing, CPU only. If the rules propose the same plan after #445's normalisation, the phrasing is a **certified phrasing**: asking it returns a certified answer. This is used twice: as score evidence E4 (§7), and as the suggested question of a not-sure answer (§6). The renderer covers the plan blocks the rules can compose; for any other block it returns nothing and the evidence is simply absent.

## 6. The three outcomes and their fields (requirement 3)

| Outcome | When | Figure | Label shown |
|---|---|---|---|
| `certified` | A full match (§4.1) | Yes, or a certified decline (a period not released, no join path), as today | Certified |
| `reading` | Gemini mode's plan passed every check with at most 2 readings | Yes, always in the reply; `confirm_first` says whether the caller should have the person confirm the reading before showing it (D1, §7.2) | The model's reading |
| `not_sure` | Anything else Gemini mode returns, or a rules decline with no model configured | No | Not sure |

A refusal for access (`planpublic.py:148`) is answered before any planner and carries no outcome; its `behaviour` stays `refuse`.

New fields, on `POST /ask`, `POST /answer`, the MCP `ask` tool and the question-run replies, and, with no change to `/assist` (D6), on `POST /assist` and the MCP `assist` tool when they route to Ask, since they pass Ask's reply through (§9.4); all optional and kept by `compact=true`:

| Field | Outcomes | Content |
|---|---|---|
| `answer_mode` | All three | `certified`, `reading` or `not_sure` |
| `answered_by` | All three | `rules`, `graph`, `graph_takeover`, `verified_example:<id>`, or `model:<provider:model>` |
| `reading` | `reading` | The plan in plain words, for example "certified NNA vs current NNA for March 2025, and their difference, reading 'minus' as difference" |
| `new_words` | `reading`, `not_sure` | Each `{word, meaning, meaning_label}`: "minus", `difference`, "difference" |
| `score` | `reading`, `not_sure` | 0 to 100 |
| `score_evidence` | `reading`, `not_sure` | Each item of §7.1 with its value and points |
| `new_word` | `reading` | `true` where the reading reads at least one new word |
| `confirm_first` | `reading` | `true` where the caller should have the person confirm the reading before showing its figure: the reading has a new word and its score is below `ASK_READING_THRESHOLD`, which is `off` by default (§7.2). Ask computes it from the reply alone and holds nothing; acting on it is the caller's (D2) |
| `confirmed_text` | `reading` with `confirm_first` | The full answer text, figure included, for the caller to show once the person agrees; `answer_text` is then figure-free (below) |
| `suggested_question` | `not_sure` | A certified phrasing (§5.4) if one exists; else the declared measures, as `planpublic.py:297-298` already offers |
| `answer_id` | All three | The turn id Ask already computes for every answer (`planpublic.py:141`), and the `turn_id` of its history entry where it has one, for `feedback`. It is a deterministic hash of the question, the run config, the binding and the history, so the same question asked again with the same binding and history has the same id. That is acceptable: no identity is kept, and two records with one id are counted as two |
| `receipt` | All three, declines included | The encrypted, opaque, sanitised record `feedback` takes (§8.1): the fields §8.2 keeps, and nothing else |

`behaviour`, `answer_text` and every existing field keep their meanings, so parity's compared fields (pass, behaviour, figure, parts, refused joins) do not move. `answer_text` begins with the label and, for a reading, the reading, so an agent that relays only `answer_text` still shows both. **One exception, so that relaying cannot leak a figure:** where `confirm_first` is true, `answer_text` holds no figure: it is the label, the reading and the question "Is this what you meant?", and the full wording, figure included, is in a new field, `confirmed_text`, to show once the person agrees. The structured figure fields are still in the reply for the caller to hold. No answer today has `confirm_first` (today's word check refuses every new word, §1.2), so no existing `answer_text` changes. Context renders the label once, from `answer_mode`, and shows `answer_text` with that leading label removed (A2). The fields are declared on `AskResponse` and `AnswerResponse` in `kge/services/ask/models.py`, and `response_view` (`kge/services/ask/service.py:40-72`) keeps them in compact mode.

**Readings in history.** Today Ask writes a history entry only for a clarification (`plan` empty, `planpublic.py:287`) and for an executed plan (`:330`); an abstain carries none, and `HistoryEntry` (`kge/semantics/plan.py:472-477`) has no field for readings, so a plan resting on a reading could not be rechecked from history: `coverage` would refuse its word at `coverage.py:811`. A1 therefore gives `HistoryEntry` a `readings` field: the accepted readings, each a span and an operation, empty by default; the entry of an executed reading (`planpublic.py:330`) carries them. A follow-up that takes such an entry as its base turn has them rechecked through the same overlay as a model reply's (§5.3) (history is untrusted and rechecked every turn, `agent.py:1-8`), inherits them, and is itself labelled a reading, never certified. A caller that forges a history plan or its readings gains nothing the checks do not already allow, since a forged reading is held to §5.3's rules like a model's.

**Confirming is the caller's (D2).** Ask keeps no state for a person's confirmation and has no confirm step: a reading's figure is in the reply, with `new_word` and `confirm_first`, and the agent or page decides, per use case, whether to show the reading first and the figure after the person agrees. The reference agent, the portal and Context follow `confirm_first` by default (§9, §10); the `ask` tool's description tells any other agent to (§9.1). A person's confirmation reaches KGE only as a `feedback` verdict (§8.1).

## 7. The score (requirement 4)

### 7.1 Evidence, not the model's confidence

The model's own confidence is never read. The score is computed from what the checks observed.

| # | Evidence | Points | How it is measured |
|---:|---|---:|---|
| E1 | Word coverage: certified pieces over meaningful pieces | 0 to 30 | The ledger, after readings |
| E2 | Each new word maps onto a certified meaning | Gate | A reading that does not is refused (§5.3); the answer is not sure, score 0 |
| E3 | Reply valid first time | 0 or 20 | 20 if the model's first reply passed parsing and schema validation (`planmodel.py:487-507`), 0 if only the second attempt did. It does not measure `check_plan`, which runs later (`planner.py:417`) with no second attempt: a plan it refuses ends the answer as not sure (§5.1) with score 0, so a `reading` has always passed it |
| E4 | Agreement with the rules plan | 0, 10 or 30 | 30 if the certified phrasing's rules plan equals the model's plan (§5.4); 10 if the rules decline the phrasing; a different rules plan makes the answer not sure, score 0 |
| E5 | Earlier certified readings | 0 or 20 | 20 if the closest approved worked example's plan has the same block signature as the reading's (§5.2). 0 before A4 |

E5 is partly circular: the closest example is given to the model (§5.2) and then scored for agreement, so agreement partly measures that the model followed it. It is kept because following an approved plan is the behaviour wanted, but its 20 points are the first item §7.3's calibration may reweigh or drop.

"Earlier certified readings" are read as the release's approved worked examples, because Ask is stateless and reads only the release. A confirmation in the feedback log is not certified: it never changes a score on the request path. It becomes evidence only once a steward certifies it and a release carries it (§8).

The points are a planning estimate. They make the score explainable, item by item, in Context; they are not a probability until calibrated (§7.3).

### 7.2 The cost ratio and the threshold

A wrong figure costs at least 100 declines (H1). Showing a reading's figure at once has expected cost 100 × p, where p is the share of such readings that are wrong; withholding it costs at most one decline, and less where a certified phrasing or a confirmation recovers the answer in one more turn. So a figure may be shown at once only where p < 1%: **readings with a new word at or above the threshold must be at least 99% right.**

| Setting | Values | Default | Effect |
|---|---|---|---|
| `ASK_READING_THRESHOLD` | `off`, or a whole number from 0 to 100 | `off` | Sets `confirm_first` (§6): a reading with a new word has `confirm_first: true` below the threshold, and always while it is `off`; at or above it, `false`. A reading with no new word has `confirm_first: false` at every value. Ask returns the figure either way; the threshold only advises the caller |

**The owner's decision (D1).** A reading whose every word is certified, with only the composition new to the rules, is shown at once, as #415 serves it today. A reading with a new word waits for the person's confirmation until §7.3's calibration supports a threshold, and is then shown at once at or above it, per release. The first part is not governed by the 100:1 bar: it rests on the evidence #415 shipped on, the grounding gate's 0 wrong figures on arm B, which may hold fewer than §7.3's 300 items. The owner decided it knowing that. Under D2 the waiting is the calling agent's or page's policy, following `confirm_first`, not a step inside Ask.

### 7.3 Calibration

With no failures in n judged readings, the 95% upper bound on the error rate is about 3/n. To show it is below 1%, n must be at least 300. A threshold value may therefore be set only where at least 300 judged readings with a new word at or above it show no wrong figure. Judged readings come from the grounding eval's recorded replies with approved `reference_sql`, and from live readings marked "not what I meant" or confirmed and then reviewed by a steward. The grounding eval reports, per score band, readings, right and wrong (§11.3). Until 300 exist at some score, the threshold stays `off`.

## 8. Feedback and certification (requirement 5)

### 8.1 The feedback call

| Item | Proposal |
|---|---|
| Call | `POST /ask/feedback` and the MCP tool `feedback` at `/mcp/answer`, beside `ask`, as the signed-in person with the `querying` role |
| Body | The answer's `receipt` (below) and `verdict`: `confirmed` or `not_what_i_meant`. No history and no question text are sent |
| On which answers | Any outcome, declines included: every answer carries a receipt, whether or not it has a history entry. "Not what I meant" on a certified answer is the strongest signal of a mis-certified word or shape |
| Free text | None. Text a person types is where private data enters a log; a steward who needs the words writes a test question (question-shapes.md §4.4) |
| Reply | 202, with nothing read back |

**The answer receipt.** Every answer of every outcome, a decline with no history entry included, carries a `receipt`: exactly the record §8.2 keeps, made by Ask when it answers, already sanitised, and **encrypted**, so it is opaque to the caller. It holds the release id, `answer_id`, `answer_mode`, `answered_by`, the score and its evidence, the normalised plan (entity key values replaced by their type), and for each new word its operation and keyed hash, made then from the question in hand; never the question text or a word.

| Item | Proposal |
|---|---|
| Encryption | Randomised authenticated encryption: AES-256-GCM with a fresh random 96-bit nonce per receipt, from the `cryptography` library (`AESGCM`), so two receipts of one question differ and nothing in a receipt can be read or compared by the caller. `cryptography` is already installed, pinned at 50.0.2 in `constraints.txt:15`; A1 declares it as a direct dependency. Nothing is hand-rolled |
| Key | Derived with HKDF-SHA256 (the same library) from `session_key()` (`kge/services/identity/service.py:114-126`), which always returns a key: `SESSION_SECRET`, else the run state's own session secret. The fixed purpose label `answer-receipt` keeps the receipt key apart from the session-signing key. Local runs therefore get feedback too |
| Check | `feedback` decrypts and authenticates the receipt and checks its release id; a receipt that fails is refused with a 400. It writes the receipt's fields and the verdict as the record |
| Rotation | Rotating `session_key_version` on the pilot changes the derived key, so receipts issued before it no longer decrypt: feedback given late, across a rotation, gets a 400 |

A caller can therefore neither forge the metadata, nor read a word hash out of a receipt, nor make the log keep anything Ask did not put in it, and Ask stays stateless: nothing is held between the answer and the feedback. The frozen agent test seals the whole answer less the run trace and the graph path (`tests/test_agent_loop.py:66-73`, `:81`); a random receipt would move every seal on every run, so A1 excludes `receipt` from the sealed record as those two are.

### 8.2 What is kept, and for how long

Kept in an append-only feedback log, written through a port with an object-store adapter for the pilot and a local-file adapter for the demo, in the form of #445's `ShadowLog`. If B2 lands first, the feedback record is a second record kind on that port rather than a new one.

| Kept | Not kept |
|---|---|
| Release id, time, verdict, `answer_mode`, `answered_by`, score and its evidence | The question text |
| The normalised plan: declared names, operations and typed scalars, with any entity key value replaced by its type, as question-shapes.md §4.4 does | The principal: no identity, as Q12 and R7 there decide |
| For each new word: its meaning (the operation) and a keyed hash of the normalised word | The new word itself, in any form a person can read; any figure or row |

**Why the word is not kept.** A new word is by definition outside the release's vocabulary, which is where a counterparty's or a client's name lands (a question naming a counterparty, then "minus"). The privacy detectors (`kge/pipeline/privacy.py`) do not detect names, and #445 allows only the pack's own vocabulary as evidence in a log (question-shapes.md R7, Q4). So the log keeps only the operation and an HMAC-SHA256 of the normalised word under a secret key: equal words give equal hashes, so confirmations of one word can be counted, but the log names no word. **Per-word grouping is server-only.** No route, view or job output shows a hash, a group id, a per-word count, a count of distinct words, or any signal that crosses a per-word threshold; everything shown is aggregated per operation (§8.3). Under D9 the hashes feed nothing shown to anyone; they are kept so that a later decision of the owner's can use per-word evidence without a change to what is logged.

| Setting | Values | Default | Effect |
|---|---|---|---|
| `ASK_FEEDBACK_KEY` | A secret of at least 32 bytes. On Google Cloud a third Secret Manager secret per client, `kge-<client>-feedback-key`, beside the session key and the passwords (`kit/INSTALL.md`, Mounts and secrets), made by Terraform with no value. It is mounted only when the new Terraform variable `feedback_key_version` names a version: null (the default) mounts nothing, so the setting stays unset. Every other entry of `secret_env` is mounted on every revision (`infra/modules/run/main.tf:66-75`), and a mount of a secret with no version is refused by Cloud Run, so the mount must be optional for an upgrading pilot's apply to succeed with no new step. When set, the variable names an explicit numeric version, never `latest`, as `session_key_version` must (`infra/pilot/variables.tf:26-33`). In Compose, in `.env`, as the session key is | Unset | The key of the word hash. Unset, a record keeps the operation and no hash, so feedback is still counted per operation but not per word. Changing the key starts the counts per word afresh |

The hash is only as private as the key: whoever holds it can test a guessed word, which would tell them whether someone asked about a name they guessed. So the key is read by the service alone, to hash a word when an answer is given (§8.1), and the hash leaves the service only inside an encrypted receipt or the log. Nothing offers a word lookup: no route takes a word and says whether it matches a group. The improvement job reads the log but never the key, and its outputs carry nothing per word. **The residual side channel**, a steward who asks with a guessed name, confirms it, and watches a count move, is closed by showing only per-operation aggregates: the steward's own feedback moves the operation's count by one whether or not anyone else asked that word. A per-word view, a lookup, group ids or a per-word threshold signal would reopen it: each would disclose to the steward role whether anyone asked about a guessed name, beyond what question-shapes.md Q12 lets stewards see. D9 shows none of them.

Kept for two release events plus review, as decided for shadow observations (question-shapes.md Q4); a record a proposal cites is kept until that proposal is decided. Because no identity is kept, counts may include repeats by one person; that inflates nothing that matters, since a count only proposes and a steward certifies.

### 8.3 The steward view

A steward who wants to look sees, beside #445's disagreement review (B3, `GET /shapes/disagreements`), `GET /readings`, open to the steward role alone, as question-shapes.md §4.4 rules for observations: per operation, the number of readings, confirmations and "not what I meant", and the normalised plans those readings produced (declared names, operations and typed scalars, as #445's observations hold); no word, no hash and no group id. To take an operation's readings forward, the steward writes a test question using the word they believe people use for it, learnt from the people who ask (question-shapes.md §4.4), and records a `propose` on that operation, citing the test question. The service does not check the word against any hash, so the action reveals nothing about what was asked. A wrong guess costs nothing: the proposal is tested and certified on its own test question (§8.4); the counts only gave the reason to look. Only certification certifies a word, and only a steward certifies.

**What is optional, and what is not.** The owner's direction makes steward review optional to use. The view stays optional for watching readings and for the template route (`missing_template`, §8.4). It is required to take a new word towards a phrase: since the log keeps no word (§8.2), only a steward's test question can supply it. That is the price of not keeping the word, which the owner accepted (D9, §12). Certification always needed a steward.

### 8.4 From a reading to a certified phrase

1. **Gather.** The improvement job's gather step (`kge/improve/gather.py`, called at `kge/improve/flow.py:105-106`) also reads the feedback log for the served release.
2. **Diagnose.** The job adds the per-operation counts to the steward view (§8.3); it shows nothing per word, and it cannot propose a phrase, since it does not know any word. A `propose` a steward has recorded on an operation, with a test question naming the word, is diagnosed `missing_phrase` (question-shapes.md §4.2, the ask skill's outcomes). A confirmed reading with no new word, a composition the rules lack, is `missing_template`.
3. **Propose.** `missing_phrase` becomes an add-only `phrases[]` entry, `{phrase: minus, operation: difference}`, the word taken from the steward's test question, within B4's widened allowlist (`kge/improve/edits.py`); that test question joins the candidate's tests. `missing_template` goes the template route of question-shapes.md §4.2, with tests a person writes first.
4. **Keep.** The candidate is tested against the A/B harness and kept only with no fall (`kge/improve/abtest.py:225`).
5. **Certify.** V05 holds the gate until a business user accepts and the steward certifies (question-shapes.md §4.2, step 4); the next release carries the phrase.
6. **After release.** The word is certified, so a question using it is no longer a reading. If the rules can also compose it, it is a full match, certified with no model call.

## 9. MCP as the agents' front door (requirement 6)

### 9.1 The tools

Every tool runs as the person whose bearer the MCP request carries (`mcp_principal`, `http.py:263-269`), with that person's readable units and roles; no agent calls with a service identity of its own.

| Mount | Tool | Description, as the agent sees it |
|---|---|---|
| `/mcp/answer` | `ask` | "Answer a business question with governed figures. Show answer_text to the person as given; it starts with the label (Certified, The model's reading, or Not sure). For a reading, show the reading. Where confirm_first is true, answer_text holds no figure and asks whether the reading is what the person meant: show it, and show confirmed_text only once the person agrees; never show the figure fields before then. For Not sure, offer suggested_question. Never compute or change a figure." |
| `/mcp/answer` | `feedback` (new) | "Record whether an answer was what the person meant: verdict confirmed or not_what_i_meant, with the answer's receipt as given. Call it only when the person says so." |
| `/mcp` | `get_context` | Unchanged: the context package for a question |
| `/mcp` | `retrieve` | Unchanged: documents the person may read |
| `/mcp` | `map_term` | Unchanged: a term's certified columns, documents and contexts |

`list_runtimes` and the other mounts stay as they are.

### 9.2 Long questions within a tool time limit

The MCP servers answer in JSON over stateless HTTP (`http.py:311-318`), so a tool cannot stream progress, and `ask` runs to the end in one call (`mcp.py:83-98`). The owner decided that Ask stays stateless and that any human-in-the-loop step belongs to the agent layer, per use case (D2). So Ask adds no run tracking and no follow-up tool for MCP: each `ask` call returns its whole answer at once. An agent whose platform's tool time limit is shorter than a question can take handles that wait itself, for its use case: by telling the person, narrowing the question, or sending them to Context. The question-run store behind HTTP `background=true` (`kge/pipeline/questionrun.py`) stays as it is for pages and is not extended for agents. Rules first shortens most questions, since the rules answer them with no model call (H2, H3).

### 9.3 The reference agent onto MCP

| Today | After A3 |
|---|---|
| `context.py` posts `/context` over HTTP or to the app in process (`:156-166`, importing `kge.app`) | The agent's context step calls `get_context`, on both arms (the tool already takes `control` or `treatment`, `mcp.py:29`), through an MCP client session built on the SDK, as `context.py`'s diagnostic `fetch` does today; the transport is given to the agent by its caller |
| Treatment arm, model mode: `plan_step` runs the coordinator in the agent's process with the agent's model (`agent.py:924-931`) | `plan_step` calls the `ask` tool with `arm=graph` and `stages` of `renderer: template`, so the reply's wording makes no model call: a `template+model` renderer would add one (below), and the agent keeps Ask's `answer_text` (`agent.py:953`); Ask's own configuration, its order and Gemini mode decide the plan, not the agent's model |
| Treatment arm, rules mode: `rules_step` calls `self.e.ruled` in process (`agent.py:938-941`) | `rules_step` calls the `ask` tool too. It passes `stages` naming every stage without the model (the `stages` argument, `mcp.py:61`): `resolution: glossary`, `context: graph`, `sql_source: planner`, `citation_rule: shelves`, `renderer: template`. These are the agent's `treatment` config (`config.py:108`) except the citation rule: `/ask` refuses any rule but `shelves` (`config.py:93-94`, a 400 through `service.py:139-141`). Ask's own citations in its reply are therefore by shelf; the agent's evidence step keeps citing by tier itself (`cite_tiers`, `agent.py:1002`), so the agent's answer keeps tier citations, and any citation that moves is listed in A3's manifest. All five stages are needed: with a model configured, `/ask` starts from `default_config`, which adds the model to every stage that can take it, the renderer included (`service.py:134`, `combined.py:136-143`), and `stages` replaces only the stages it names (`models.py:33-41`, `service.py:136`); a renderer left at `template+model` would make a model call in `phrase` (`planpublic.py:521`, `combined.py:355-367`). So Ask makes no model call whatever its configuration, and rules mode stays rules (§4.3, step 3 skipped) |
| Control arm (graph off), both modes: the same steps, on the agent's `control` config (`kge/services/ask/engine/config.py:106-109`) | Stays in process. Ask has no control arm: its arms are `schema`, `catalog` and `graph` (`config.py:100-104`), and the control arm is the experiment's graph-off baseline, not an agent's route to KGE. Its steps call the engine directly, which needs no layer exception; only its context step moves to `get_context`. The owner decided so (D10, §12) |
| Evidence step (`agent.py:943-961`) | Unchanged, in the agent's process, on the fields `ask` returns for the treatment arm |
| Layer exception `("kge.services.ask.engine.context", "kge.app")` | Removed: `engine/` imports nothing above it. The `/agent` route (`routes.py:165`) and the tests pass an in-process transport built in the routes layer, which reaches the app object through `served` (`kge/services/http.py:223-231`, under that file's existing exception); `test_every_exception_is_still_needed` then requires the entry's removal |

**The frozen agent test after A3.** Today it holds every question under `rules` and each mock mode (`tests/test_agent_loop.py:172-173`, `{"rules", *modes()}`), each run as `Agent(eng, llm=llm(mode))` (`:176`), so the agent's mock model drives the plan. After A3 the treatment arm's plan comes from Ask, so the agent's `llm` no longer drives it. A3 keeps the structure's meaning by moving the mode to Ask: for each mode, the test serves the app in process with Ask's model set to the mock in that mode (none for `rules`), and passes the agent a transport to it. The control-arm keys keep `Agent(eng, llm=llm(mode))` as today. Every treatment-arm seal moves, because the plan now arrives through `ask` with A1's fields and its trace; control-arm seals do not move beyond what A1's manifest already lists. A3's manifest lists every key, as A1's does (§11.3), and the owner approves the moves.

The agent stays a LangGraph loop (`agent.py:297-308`), and it still writes no SQL.

### 9.4 The portal's catalogue agent onto MCP

The portal server's Ask chain is the catalogue agent's call into KGE (`knowledge.js:241`). It moves from `POST /assist` to the `assist` tool at `/mcp/assist`, through the official TypeScript MCP SDK, keeping the chain's deadline (`askChain`, `knowledge.js:9`) as the tool call's deadline. Its fallback to the portal catalogue on an unreachable KGE is unchanged. The portal's pages (search, catalogue, sign-in, and Context through the portal server) stay on HTTP.

`mcp_principal` never accepts a cookie, and the portal forwards the person's cookie today (`kge.js:3-9`). The portal must therefore carry the person to MCP as a bearer: the owner decided the portal forwards the person's own KGE token from the shared sign-in, in `x-kge-authorization`, which `mcp_principal` already reads (`http.py:266`) (D4). The build issue confirms the sign-in can hand the portal that token; if it cannot, it stops and asks.

`/assist` itself is unchanged (D6). It already passes Ask's whole reply through (`{**out, ...}`, `kge/services/assist/service.py:268-270`, and `AssistResponse` allows extra fields, `models.py:43-44`), so the new fields, `confirm_first` among them, reach the portal with no change to `/assist`. The portal's Ask chat (`portal/ui/src/components/askportal/Chatbot.js`) follows `confirm_first` by default: it shows the reading and asks the person to confirm before it shows the figure, holding the reply in the page meanwhile.

## 10. What each screen shows (requirement 7)

| Item | Context Ask (internal: data team, stewards, dev team) | An agent's UI and the portal's screens (business users) |
|---|---|---|
| Label | Yes | Yes |
| Reading and new words | Yes | Yes |
| Suggested question | Yes | Yes |
| Score | Yes, with each evidence item and its points | No |
| How answered (`answered_by`), the order setting and the threshold | Yes | No |
| The refused model plan and why, when the rules served | Yes, as the trace's `model_plan_refused` step already shows | No |
| Confirm before the figure | Where `confirm_first` is true, the figure-free `answer_text` is shown with a Confirm button, and `confirmed_text` with the figure after it, by default | The same, by default, as each agent's policy per use case (D2) |
| Feedback buttons | Confirmed, Not what I meant | As the agent offers them, through `feedback` |

Context's changes stay within its existing components and tokens, as the portal's information architecture is frozen: the label and the reading in `portal/ui/src/pages/Context/Answer.js`, the score and how the question was answered in `BehindAnswer.js` ("Behind the answer", always shown since #440), feedback and the confirm step in `Ask.js` and their state in `contextState.js`, held in the page, not in Ask. The portal server passes the new fields through unchanged.

## 11. Consistency with what exists (requirement 8)

### 11.1 #445's lifecycle, takeover and maturity metrics

| #445 element | Under this design |
|---|---|
| Stages and the served rules path (question-shapes.md §4.3) | Unchanged, except that D8 adds exact example matches, where the coded planner declines, as a second kind of takeover (§4.2). Step 1 of the order (§4.3) is the rules-planner port as #445 defines it, at whatever stage each group holds |
| Decline takeover (Q15) | A takeover plan is a full match and labelled certified on the release's tests and Q15's four conditions (§4.1); steward review of takeovers follows serving, as Q15 sets it |
| Shadow observations (§4.4) | Unchanged. Both rules adapters run on every question that reaches the rules path, which under `rules_first` is every question; Gemini mode is not a rules adapter and adds no observation |
| M1 to M8 | Unchanged: they compare the two rules adapters and their tests. A reading, confirmed or not, counts towards no shape's metric |
| Worked examples (§3.1, S35, V06) | Used as #445 defines them, for exact matching (§4.2) and as the model's closest examples (§5.2), which answers Q8's "separate issue". Exact matches are served where the coded planner declines, at any stage once they replay (D8, §4.2) |
| Phrases (§8.1) | A certified reading becomes a `phrases[]` entry through B4's allowlist (§8.4). It is a new phrase, not part of the frozen baseline, and is judged as question-shapes.md §8.1 says |
| Two-context difference (§8.3) | Needs the ownership rule at `coverage.py:894-899` widened (§5.3). That is code, and B6 was approved as pack and preload data only, so it lands in A5, before B6 (D7) |

### 11.2 Statelessness and per-person access

Ask keeps no conversation: history travels with the caller and is rechecked every turn, readings included (§6). Ask holds nothing for a person's confirmation or for a long wait: those are the caller's (D2). The feedback log is written, never read on the request path. The question-run store keeps a background run's state for its asker alone, as today (`service.py:518-551`). Per person, end to end: the agent passes the person's bearer; `mcp_principal` resolves it; the vocabulary, the closest examples and the plan are held to that person's readable units; the gate checks every statement as that person.

### 11.3 What reversing part of #415 does to the checks

Rules first reverses #415's order for questions the rules answer; it keeps #415's model path, with its grounding and checks, for the rest.

| Check | Effect | Treatment |
|---|---|---|
| `make parity` | **None, and blind to it.** Parity pins `MODEL_PROVIDER=none` (`Makefile:394`), so it never ran the model first and does not run Gemini mode. The new fields are not among parity's compared fields | Parity stays as the rules path's proof. Under #445's stage rules, A4's exact example matching serves a plan where the coded planner declines only under the takeover, whose moves are already stated in `parity-stated.json` by the B issues. Under D8, A4 also turns coded declines on approved example questions into certified figures, and states each of those moves in `parity-stated.json` itself |
| Margin baseline | **None, and blind to it.** The margin and its baseline run on rules (`Makefile:388`, `margin.py:134`) | Unchanged |
| `make grounding-eval` | **Sees it.** Arm B is the #415 path | A new arm R: the served order under `ASK_ORDER=rules_first`, with readings scored by score band. R must not lose an item C got right (by construction, R serves C's plan wherever C has one), and must not lose an item B got right (`r_loses_to_b`, in the pattern of `b_loses_to_c`, `kge/eval/grounding.py:410`): where B and C both answer with different plans, rules first serves C's, and if C's figure is the wrong one the gate fails rather than serving it labelled certified. Each item where B and C answer with different plans is reported, with both plans; must give no wrong figure at or above the threshold, and must make no model call on an item C answered. **Jobs budget**, using the eval's existing `jobs_per_request` and `job_budget_failures` (`kge/eval/grounding.py:412-421`, `:436`), per job kind (`JOB_KINDS`, `:310`) and in total: R makes no more jobs than C on an item the rules answered or declined certified; elsewhere no more than C and A together; and on every item no more `retrieval` jobs than C, so the closest examples add none. An item over budget fails the gate. B stays while `model_first` remains a setting |
| Frozen agent seals (`tests/test_agent_loop.py`) | **Every seal moves.** Each sealed answer gains `answer_mode` and `answered_by` (`receipt` is excluded from the sealed record, §8.1), and the seal digests the whole answer (`:66-72`, `:81`). In the mock modes, the model call disappears from the run trace wherever the rules give a full match, and `versions.interpretation` reads `rules`. The prompt hash moves wherever the closest examples enter the prompt (A4). The `rules` keys' answers are otherwise identical | A migration manifest for #462, in the pattern of `agent_query_plan_402_vocabulary_manifest.json`: per key, the seal before and after and the hash of the answer normalised: the new fields, the run trace and `versions.interpretation` removed, and the label and reading that §6 puts at the start of `answer_text` stripped. The label change is then verified on its own, per key: the new `answer_text` must be exactly the label for its `answer_mode`, then the reading where there is one, then the old `answer_text`. That hash must be unchanged for every `rules` key, and for every mock key where the rules now serve and the model's plan had equalled the rules plan; the moves in those keys' trace and `versions.interpretation` are expected and listed as such. Any other move, a mock key whose served plan changed among them, is listed with its reason for the owner's approval. A3 then changes how the test runs, not only the seals: the mode moves from the agent's model to Ask's, and every treatment-arm seal moves again, with A3's own manifest (§9.3) |
| Recorded replies | **The key holds, so the recordings go stale for A4.** The closest examples sit outside the recording key (§5.2), so after A4 a recorded reply made without examples still replays | A4 re-records the model replies with a live model on the owner's run, with the examples in the prompt; the new recordings replace the old under the same keys, and the examples' effect is measured on that run by comparing arm R before and after. The recording key holds no arm or order (`planmodel.py:95`), and the examples enter every model planning call under either order, so the new recordings change arms A and B too: the grounding gate's arm-B checks (`b_loses_to_c`, no wrong figure) are re-run on them, and the evidence D1's no-new-word rule rests on is re-measured there. A later change to a client's approved examples needs the same re-recording (R10) |

## 12. Decisions (requirement 9)

The owner decided the ten open questions on 2026-11-13. Each decision is now part of the design; D*n* answers the earlier open question O*n*.

| # | Question | Decision | Where it applies |
|---:|---|---|---|
| D1 | Does a reading show its figure at once, or wait for the person's confirmation? | Split: at once where every word is certified and only the composition is new to the rules, as #415 serves it today; wait for confirmation where the reading has a new word; later, at once above the threshold once §7.3's calibration supports one. Under D2 the waiting is the calling agent's and Context's default policy, following `confirm_first`, not a step inside Ask. The no-new-word part is not held to the 100:1 bar; the owner decided it knowing it rests on #415's evidence | §6, §7.2, §10, R1, R3 |
| D2 | The agent tool time limit, and where human-in-the-loop steps live | "Ask is stateless service. Let's move any HITL per our design to agent layer per use case." Ask adds no run tracking and no follow-up tool; each `ask` call returns everything at once: the figure, the label, the reading, whether a new word was read, and the receipt. Confirming a reading and handling a long wait within a platform's tool limit belong to each agent, per use case. The existing question-run store is kept for pages and not extended. No `confirm` request field is added: the figure is already in the reply, so Ask has nothing to release on confirmation | §6, §9.1, §9.2, §11.2, R7 |
| D3 | Which meanings a new word may take | Operations only | §5.3, R2 |
| D4 | How the portal carries the person to MCP | The person's own KGE token, forwarded by the portal in `x-kge-authorization` | §9.4, A3 |
| D5 | A fourth build issue | A4 is raised separately, so that A1 to A3 start now | §13 |
| D6 | The portal's wait through `/assist` | `/assist` is left as it is; A1 changes nothing in it. Its routing model call stays | §9.4, §13 |
| D7 | Where the widened ownership rule lands | A separate issue, A5, after A1, B2 and B5 and before B6; B6 stays pack and preload data only | §5.3, §11.1, §13 |
| D8 | Whether an exact example match is served before its group reaches `graph_first` | Where the coded planner declines, an exact match is served at any stage once that example replays (V06) in the release event; never over a coded answer | §4.2, §11.1, §11.3, A4 |
| D9 | How a new word reaches a phrase | The log keeps the operation and a keyed hash only; a steward writes the test question that supplies the word. Stewards see per-operation aggregates only | §8.2, §8.3, §8.4, R5 |
| D10 | The reference agent's control arm after A3 | It stays in the agent's process | §9.3 |

## 13. The build issues (requirement 10)

### 13.1 The issues

| # | Proposed title | Scope | Depends on | Main files |
|---:|---|---|---|---|
| A1 | Ask answers certified first, then Gemini mode with a labelled reading and a score, on HTTP and MCP | `ASK_ORDER` and `ASK_READING_THRESHOLD`; readings in the model's reply and the per-request ledger overlay; the certified phrasing; the score; the three outcomes and their fields; `readings` on the history entry; `new_word`, `confirm_first` and the figure-free `answer_text` of a reading that awaits confirmation (§6); `feedback` on HTTP and MCP and its log, keeping a keyed hash of each new word; the encrypted answer receipt (AES-256-GCM from `cryptography`, declared as a direct dependency in `pyproject.toml`, key derived from `session_key()`), excluded from the agent seals; `new_word` and `confirm_first`; the `ask` tool's description; grounding-eval arm R; the agent seal manifest | None in #445 | `kge/services/ask/engine/planpublic.py`, `planner.py`, `planmodel.py`, a new `kge/services/ask/engine/answermode.py` (the order, the score and the phrasing; the name avoids the existing `reading.py`, which words an answer's fields), `kge/semantics/coverage.py`, `kge/semantics/plan.py` (the reply's readings, and `readings` on `HistoryEntry`), `kge/services/ask/models.py`, `service.py`, `routes.py`, `mcp.py`, `kge/ports.py` and a feedback-log adapter, `pyproject.toml` (`cryptography`), `kge/pipeline/settings.py` (`ASK_ORDER`, `ASK_READING_THRESHOLD`, `ASK_FEEDBACK_KEY`), `.env.example`, `kit/INSTALL.md` (the settings, the third secret, adding its version and setting `feedback_key_version`; an upgrade needs no step until then), `infra/modules/secrets/`, `infra/modules/run/main.tf`, `infra/pilot/main.tf`, `variables.tf`, `example.tfvars` and `tests/pilot.tftest.hcl` (the feedback-key secret, `feedback_key_version` and the optional mount), `kge/eval/grounding.py` (arm R and its jobs budget), `tests/fixtures/` (the seal manifest), `tests/test_agent_loop.py` |
| A2 | Context shows how each answer was reached: label, reading, score and feedback | §10's Context column, within the existing components; the portal server passing the fields and `feedback` through | A1 | `portal/ui/src/pages/Context/Answer.js`, `BehindAnswer.js`, `Ask.js`, `contextState.js` and their tests; `portal/server/src/app.js` |
| A3 | Agents reach KGE through MCP: the reference agent and the portal's catalogue agent | The reference agent on `get_context` and `ask` (§9.3), the layer exception removed; the portal's Ask chain on the `assist` tool with the person's bearer (§9.4, D4); the portal's Ask screen following `confirm_first`; the agent's seal manifest | A1 | `kge/services/ask/engine/context.py`, `agent.py`, `kge/services/ask/routes.py`, `tests/unit/test_layers.py`, `tests/test_agent_loop.py`, `tests/fixtures/`; `portal/server/src/knowledge.js`, `kge.js`, `package.json`, `portal/ui/src/components/askportal/Chatbot.js` and its tests |
| A4 | Verified examples first and as the model's closest examples; confirmed readings to certified phrases | Exact example matching in the graph adapter, served where the coded planner declines (§4.2, D8); the approved examples' vectors made with the release and held in memory, and the closest examples in the prompt (§5.2); E5; the grounding eval's replies re-recorded with a live model on the owner's run; the feedback log read by the improvement job, `missing_phrase` and `missing_template` from readings, the optional steward view (§8.3, §8.4) | A1, #445 B1, B2, B4, B5 (file overlap, §13.2) | `kge/services/ask/engine/planmodel.py`, the graph-planner module B2 adds, `answermode.py`, `kge/graph/release.py` and `kge/graph/load.py` (the examples' vectors), `kge/eval/grounding.py`, `kge/improve/gather.py`, `kge/improve/rules.py`, `kge/services/quality/routes.py` |

Under D7 a fifth issue follows: **A5, the ownership rule for a two-context difference**, a `difference` or `percent_change` piece owning the two-context comparison its derived node requires when the question names exactly two contexts, in `kge/semantics/coverage.py` with its unit tests; it moves no rules answer, since the coded planner does not compose such a plan, but in Gemini mode it lets a reading such as "minus" as `difference` pass where it is not sure today (§4.4), and A5's pull request lists each such change. B6 depends on it.

### 13.2 Order with #445's issues

Two issues that change the same files are dependent (AGENTS.md), so the order is set by file overlap as well as by logic.

| Issue | Shares files with | Order |
|---|---|---|
| A1 | B1 (`kit/INSTALL.md`), B2 (`planpublic.py`, `planner.py`, `coverage.py`), B5 (`coverage.py`, `planmodel.py`) | A1 first: it removes the pilot's wait on every question, and B2's shadow running serves nothing new. B1 and B2 then start from A1's merge |
| A2 | No B issue (B3 changes `Quality.js`, a different file) | In parallel with any B issue, after A1 |
| A3 | No B issue | In parallel with A2, after A1 |
| A4 | B2's graph-planner module, B4 (`kge/improve/`), B5 (`planmodel.py`) | After B2, B4 and B5 |
| A5 | A1, B2 and B5 (`coverage.py`); B6 depends on it by logic | After A1, B2 and B5; before B6 |

So: A1; then A2, A3 and #445's B1 in parallel; B2 after B1; A4 after B2, B4 and B5; A5 after A1, B2 and B5 and before B6. Every one of these changes files under `demos/kge/`, so each merge publishes the next KGE kit, and each pull request says so. This document changes only `docs/` and publishes none.

### 13.3 Expected answer changes and checks

The checks are those the kge-checks skill gives for demo code: `make lint`, `make test-unit`, `make test-integration`, `make parity`, `make margin REPEATS=1`, `make grounding-eval` for a planner change, and the repository checks; the portal UI tests for A2 and the portal server tests for A3.

| # | Expected answer changes | Checks beyond those |
|---:|---|---|
| A1 | Questions the rules answer lose their model call. **Figures may move where today's path and the rules both answer with different plans:** today the model's plan wins (arm B); under `rules_first` the rules plan is served and labelled certified. A1's pull request lists every item whose served figure moves, with both plans, and arm R must lose no item arm B got right (§11.3). **No figure the pilot shows today is withheld:** questions the model answers today and the rules do not stay answered as readings, with their figures in the reply. Today's word check refuses any plan with a new word (§1.2), so none of them has one and all keep their figures at once (D1). Each is listed in the pull request, with the count from the recorded replies | Grounding-eval arm R as §11.3 states it, with its jobs budget per kind; model calls and warehouse jobs per request before and after on the golden, held-out and pilot UAT sets; the seal manifest; MCP tool tests for `ask` and `feedback` as two different people; `confirm_first` per D1 at `ASK_READING_THRESHOLD=off` and at a value, and no figure in `answer_text` wherever it is true; a live test on the pilot by the owner, since parity cannot see the model path |
| A2 | None | Portal UI tests for each outcome |
| A3 | None in figures; the agent's plans now come from Ask's configuration | The agent's seal manifest; `test_layers.py` with the exception gone; the portal's Ask over MCP as two people with different readable units; for a reading with `confirm_first`, the reference agent and the portal chat show no figure before the person agrees, and a relay of `answer_text` alone shows none |
| A4 | Under D8, coded declines on approved example questions become certified figures, each stated in `parity-stated.json`. The closest examples may change which plans the model proposes; the recorded replies cannot show it (§11.3), so it is measured on the owner's run | The grounding eval's model replies re-recorded with a live model on the owner's run, and arm R compared before and after, by score band; arm R's jobs budget, with no more `retrieval` jobs than C on any item; the improvement job's tests from a confirmed reading to a held proposal |
| A5 | No rules answer moves. In Gemini mode, readings such as "minus" as `difference` with two contexts named go from not sure to a reading; each is listed in the pull request | `coverage.py` unit tests for the rule, both ways (two contexts named, and not); grounding-eval arm R before and after |

## 14. Risks

| # | Risk | Treatment |
|---:|---|---|
| R1 | A reading's figure is wrong and reaches a report | A reading with a new word carries `confirm_first: true` until §7.3's calibration supports a threshold, and the reference agent, the portal and Context ask the person before showing its figure (D1). Ask cannot enforce that: under D2 the figure is in the reply and an agent that ignores `confirm_first` shows it unconfirmed; the `ask` tool's description says to follow it, and the build issues test that their own agents do. A reading with no new word is shown at once on #415's evidence, which may rest on fewer than 300 items and is not held to the 100:1 bar (§7.2); the owner decided so. A reading is always labelled, with any word and its meaning shown |
| R2 | A reading changes what a figure measures | Readings map onto operations only (D3); every name, value and period still passes the existing checks |
| R3 | Figures the pilot shows today, from model plans the rules cannot make, are lost at launch | They stay answered as readings, figures in the reply; none reads a new word, so none waits for confirmation (D1). A1 lists each, with the count from the recorded replies |
| R4 | Parity and the margin pass while the model path regresses | They are blind to it by design (§11.3); arm R of the grounding eval and the owner's live test cover it |
| R5 | The feedback log exposes what people ask, a counterparty's or client's name above all, since new words are where names land | No question text, no identity, entity keys replaced by type, and no new word: only its operation and a keyed hash (§8.2). The hash reaches a caller only inside an encrypted, randomised receipt, so no caller can read or compare it (§8.1). The key is read by the service alone, never by the improvement job; stewards see per-operation aggregates only, with no hash, group id or per-word count, so neither a lookup nor feedback on a guessed word shows whether anyone asked it (§8.2, §8.3; D9) |
| R6 | Feedback is gamed to certify a word | Counts only propose; the A/B keep decision and a steward's certification decide (§8.4) |
| R7 | An agent's tool call times out on a long question | Each agent handles the wait for its use case (D2, §9.2); rules first removes the model call from most questions (H2), and A1 measures the 95th percentile per path (H3) |
| R8 | An agent calls KGE with its own identity instead of the person's | MCP takes a person's bearer only; the build issues test two people with different readable units through each agent |
| R9 | The order setting is left at `model_first` and the pilot keeps waiting | The default is `rules_first`, and Context shows the setting on every answer (§10) |
| R10 | The grounding eval replays model replies recorded before a client's approved examples changed, and reports an effect it did not measure | The examples sit outside the recording key, so this cannot be detected from the key; A4 re-records on the owner's run, and any later change to a client's approved examples is followed by the same re-recording before the eval's results are cited (§11.3) |
| R11 | The closest-example lookup adds warehouse work to every Gemini-mode request | The examples' vectors are held in memory with the release and ranked in process, with no `Retrieval` call (§5.2); arm R's jobs budget fails the gate on any item over it (§11.3) |
| R12 | The feedback key's secret is mounted before it has a version, and the pilot's apply deploys a revision Cloud Run refuses | The mount is optional: `feedback_key_version` null, the default, mounts nothing; a version is named only after one is added. A1's Terraform tests cover both (§8.2) |
