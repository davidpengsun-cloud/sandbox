# Demo Review and Access Design

A short review of the local demo, and the design of the access rule added on the `feature/run-kge-demo` branch. Line references are to the code at the commit that adds this file.

## Code review findings

Seven findings, grouped by what they put at risk. The runtime dependency on pandas (the graph loader needs it) was already fixed on this branch in commit `987396c`.

### Correctness

1. **A partial result is reported as a valid sum.** `kge/services/ask/engine/core.py:495-496` treats fewer than two usable parts as a "single figure" and calls it commensurable, and `kge/services/ask/engine/core.py:622-626` then sums whatever parts came back. "Combined Retail and Corporate revenue for June 2025" returns 13,287.67, the Retail figure alone, as `sum_with_verdict` with the verdict "single figure", while Corporate abstained because its revenue is quarterly. Fix: return `parts_with_reason` whenever any requested part is missing.
2. **"in YYYY" filters have no upper bound.** `kge/services/ask/engine/core.py:417-418` (snapshot tables with `as_of`) and `kge/services/ask/engine/core.py:420-421` (tables with an `_on` date) emit only `>= YYYY-01-01`, so "in 2024" reads 2025 data and balance snapshots are summed across every later date. The monthly branch at `kge/services/ask/engine/core.py:385-386` is bounded correctly. The golden set shares the mistake at `kge/estate/golden.py:64` (`paid_on>='2025-01-01'`), so the eval rewards the bug.

### Governance and privacy

3. **The optional model tagger sends raw text out before the privacy gate.** `kge/pipeline/tagger.py:119-131` builds the prompt from the full chunk text, personal data included, and posts it to the configured model. The rules propose privacy tags first, but nothing stops a flagged chunk from leaving.
4. **The steward queue shows real personal data.** The POL-COMPLAINTS privacy tags (an email address and a date of birth) sit next to the POL-RETENTION false positive (a functional mailbox) with the raw evidence exposed (`kge/services/ask/engine/core.py:175-180`, served unauthenticated at `/steward/queue`). Rejecting the real item re-derives readiness and embeds the personal data (`kge/services/ask/engine/core.py:201-213`). See the scenario note below.
5. **Accepting a candidate term turns an uncertified email into a certified citation.** `kge/services/ask/engine/core.py:195-200` creates the term and a `DEFINES` edge from EM-004, and `kge/services/ask/engine/core.py:629-635` cites every defining document without checking its shelf.
6. **Access control is advisory.** Every request names its own principal. `steward_decide` (`kge/services/ask/engine/core.py:182`) never consults `CAN_WRITE` and does not validate the decision value, so any caller can accept terms.

### State integrity

7. **Rerunning the pipeline wipes steward decisions.** `kge/pipeline/run.py:71-72` detach-deletes every machine tag and every chunk, which orphans the human tags that superseded them and resets the queue. Related: steward decisions set readiness by hand (`kge/services/ask/engine/core.py:208-211`) instead of re-deriving it from the live tags, so the Restricted classification is ignored.

## Proposed design

### An access-governance layer over the metadata

"AG layer" is David's shorthand; the code does not use the term. Read it as an access-governance layer that sits over the metadata graph: the `Principal` nodes with their `CAN_READ` edges to `Unit` and `CAN_WRITE` edges to `Term` (`kge/graph/schema.py:28, 42-43`, loaded by `load_principals` in `kge/graph/load.py`), plus the checks that consult those edges. Today the graph holds the mirror and one check reads it; the layer's job is to make every read, combination and write go through such a check, with the principal taken from the session rather than the request body.

### The join-time access rule

A join or combination across warehouses proceeds only if the requester can read every warehouse involved. In this code a "join" across warehouses is a federated combination: `Engine.answer` plans one query per warehouse and combines the parts in Python (summed, or reported as parts). No SQL joins two warehouses, and the curated no-join facts refuse the literal joins.

- The rule is `Graph.join_access` (`kge/services/ask/engine/core.py:110-121`). It reads the existing `CAN_READ` mirror, so it reuses the access step that was already in `Engine.answer` rather than adding a parallel one.
- It is called at `kge/services/ask/engine/core.py:585`, after the term is resolved to its warehouses and before `plan_unit` builds or runs any SQL. No warehouse is queried when it fails, not even the readable ones.
- A blocked combination returns `behaviour: blocked_access`, `denied_warehouses`, `allowed_warehouses`, `warehouses_involved`, an empty `sql_run` and a reason. A single-warehouse denial is still a `refuse`, now carrying `denied_warehouses` too.
- The UI shows `blocked_access` as a red badge with the denied warehouses and the reason.

### Two users

- `admin` reads all four warehouses: Retail, SME, Corporate and Wealth.
- `steward` reads Retail and Wealth only.

Both are declared in `kge/estate/spec.py` and synced into the graph by `python3 -m kge.graph.load --build build --principals-only`. On "Combined Retail and SME revenue for June 2025" admin gets the summed figure and steward gets `blocked_access` with `denied_warehouses: ["sme"]`; on "Combined Retail and Wealth revenue for Q2 2025" steward gets the same parts as admin.

```
.venv/bin/python -m pytest tests -q      # tests/integration/test_join_access.py
.venv/bin/python scripts/access_demo.py  # both users, three questions
```

## The planted personal data in the steward queue

The POL-COMPLAINTS personal data is a deliberately planted scenario. It exists to show the gate and the steward loop working: the rules caught it before embedding, the chunk has no vector, and a human has to decide. That part works as intended.

Two things still need fixing even in a demo. The queue shows the raw evidence unredacted, so the personal data is on screen and in the API response for anyone who asks. And a wrong Reject embeds real personal data, because rejecting the last privacy tag re-embeds the chunk. The queue should redact evidence, and rejecting a privacy tag on a chunk that carries real personal data should require a second check.

## Fixes applied

All eleven findings of the full review, and the small items, were fixed in four batches on the same branch. The line references above describe the code before these fixes. After every batch the graph arm of the golden set held 20 of 20, 10 of 10, 10 of 10 and 5 of 5, and the access tests passed.

1. **Correctness** (findings 1, 4 and 7 of the full review). A combination with any part missing returns `parts_with_reason` with `missing_parts` and no total, in every arm. A term defined only by an accepted uncertified email abstains and names the source instead of citing it. "in YYYY" is bounded on both sides, and on snapshot tables a sum reads the year's last snapshot; `kge/estate/golden.py` uses the same bounds (its figures are unchanged on this estate).
2. **Pipeline** (3, 5 and 6). The model tagger never receives a chunk the gate would prohibit. Readiness is re-derived in `kge/pipeline/state.py` from every live tag, for the pipeline and for steward decisions alike, so an independent Restricted classification holds the gate. Reruns keep chunks, re-attach repeated proposals to their tags and never detach human decisions.
3. **Environment and access** (8, 9 and 10). A missing `sentence-transformers` is an error, dimensions are reduced by a seeded projection, and retrieval only compares vectors from one model. `.env` is loaded. The graph lock is explained by name and process id, and the golden set can run through the API. Identity comes from a bearer token or session cookie, never the request body, and three processes are bound in the graph and checked in code: tagging (`pipeline_tagger`, `admin`), certification (`steward`, `steward_glossary`, `admin`, with `CAN_WRITE` for terms) and querying (analysts, `steward`, `admin`).
4. **Queue, control and small items** (2 and 11). The queue redacts personal data, marks each privacy item as likely personal or functional, and a reject of likely-personal data needs a second, different steward. The scorer checks unit and measure column, parts, uncertified citations and exact abstentions. Sixteen held-out paraphrases are reported apart; the graph arm answers 9 of them.
5. **Held-out parser gaps.** DOB lookups are refused like email (`restricted_lookup` → POL-DATA-CLASS). `unresolved` is treated as the open-complaints filter (`resolved_on IS NULL`), so H006 and H015 pass on the graph arm.


Two judgement calls. The stricter scorer exposed a labelling inconsistency in golden item G020, whose expected columns listed the filter column first; they now list the measure first, as every other item does, and the figure is unchanged. Other held-out misses (wording the parser still does not read as join/definitional) remain visible rather than patched to the held-out wording.
