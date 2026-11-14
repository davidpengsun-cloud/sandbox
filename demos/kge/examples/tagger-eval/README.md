# Tagger evaluation

From `demos/kge`, after the Python setup in the demo README:

```sh
make tagger-eval
```

This one command evaluates `RuleTagger` and the low, medium and high model tiers
with the offline mock. It writes `build/tagger-eval/result.json` (inputs, model
identities, proposals, per-passage matches and counts) and `report.md`. The unit
lane runs the same command implementation in CI without a built graph. No model
quality claim is implied by a mock run. The report applies the owner’s
[agreed targets](targets.json) separately to rules and model arms. The current
mock fails the candidate targets, so this command returns nonzero while
still writing the completed report.
The [recorded mock counts](mock-report.md) were produced with this command and
the set digest shown in that report.

`set.json` contains invented emails, notes and memos with fixed passage
boundaries and manually authored expectations. TE-001 includes the Digital
Engagement Score failure shape from Meridian's EM-004, rewritten as invented
material. The examples cover prose definitions, casing, repeated definitions,
repeated mentions, existing glossary definitions and headings that are not
metrics. They contain no personal or client data.

A candidate is a defined metric absent from the supplied glossary. A mention
is a glossary term that the passage discusses. The unit counted is a unique
(passage, label) pair, once per repeat, not every textual occurrence. Case and
whitespace are ignored; synonyms and near spellings are not. Candidate counts
include repeated definitions in different passages. Every expected pair absent
from the proposals is missed; every unexpected proposed pair is wrong. The
JSON keeps the actual proposals and matched, missed and wrong labels, so an
owner can audit individual disagreements. This small set measures only these
examples, not production recall.

The mock reads only the tagger prompt, with literal mention matching and colon
definitions. It does not read expected labels. Its identical tier scores prove
routing and scoring, not equivalence between real models. The normal pipeline's
mock tagger still uses rules; this evaluation explicitly wraps each centrally
selected model with the unchanged `ModelTagger`. Error sentinels stop a run
instead of becoming apparent quality failures. Privacy-withheld passages are
counted and reported; the supplied set has none.

## Pilot run preparation

`pilot.json` copies the pilot's model tiers from `infra/pilot/variables.tf`.
The owner’s [2 November 2026 live Meridian results](results/2026-11-02-meridian-live.md)
select medium as the pilot default. Those results cover the cleared Meridian
corpus, distinct from this small invented set. The baseline and model bars
live in `targets.json`; the scorer applies them on every run. Surface recall
uses expected glossary labels present as whole-word, case-insensitive text.
Passages may declare SQL column/table names in `identifiers`; proposing one,
or an underscore-separated identifier, fails the model bar.

1. Review the set and agree minimum found and maximum wrong counts separately
   for candidates and mentions, the repeat count, and the existing pack's
   V01–V04 gates. Run the offline command first to obtain `set_sha256` from
   `result.json`. In a local `targets.json`, record the actual agreement:

   ```json
   {
     "agreed_by": "owner's agreed identifier",
     "agreed_at": "date of agreement on issue 218",
     "set_sha256": "digest from result.json",
     "repeats": 1,
     "candidates": {"min_found": 0, "max_wrong": 0},
     "mentions": {"min_found": 0, "max_wrong": 0}
   }
   ```

   The zeroes illustrate the schema only; replace them with the agreed counts.
   They are totals across all repeats. The command refuses a different set or
   repeat count. These optional count limits supplement the agreed rate targets; they cannot relax them.

2. For an offline A/B rehearsal, build the local stack as the demo README says
   (`MODEL_PROVIDER=none make graph pipeline`), stop the API, then run:

   ```sh
   make tagger-eval TAGGER_EVAL_ARGS='--ab --out build/tagger-eval-ab'
   ```

   This runs the existing reference agent with graph off and on, first on rules,
   then on the configured mock tiers, on the same served graph. Each run keeps
   the harness manifest, records, summary and reports. The top-level JSON and
   report record V01–V04 for both runs. V03 is reported only. No release gate
   marker is written. A graph tagged by rules remains tagged by rules: this
   answering A/B does not claim to compare graphs rebuilt by different taggers.

   In the recorded rehearsal, rules pass the gates; the mock misses G018 and
   G020, so its within-unit V01 is 90% against 100% and the command fails. This
   is a measured limitation of the existing answering mock, not tagger quality
   evidence. The evaluation reports it; it does not relax the pack's gate.

3. After merge and deployment, the owner runs the image's `tagger-eval` job on
   the pilot. Follow [Tagger evaluation on the pilot](../../kit/INSTALL.md#tagger-evaluation-on-the-pilot)
   for the exact Cloud Run commands, target upload and result download. The
   image ships `set.json`; no checkout is needed. `LIVE=yes` uses the installed
   model settings, including operator overrides, through `model_for`; it does
   not apply the reference snapshot in `pilot.json`. `AB=yes` uses the served
   graph with the installed answering and context tiers, alongside rules.
   The job saves each execution under `runs/<client>/tagger-eval/<run>/` in its
   configured store, including completed quality failures and incomplete-run
   status. The passage evaluation never changes the graph.

4. Preserve the full output directory, agreed targets and graph provenance in
   the issue's evidence. Record the owner's live result in this repository
   before choosing a default. Review every false positive and miss. Exit 0
   means all agreed targets and measured gates passed; 1 means at least one
   failed; 2 means the run could not complete. A mock verdict is a plumbing check, not model quality evidence. A run that cannot complete may leave earlier output in place; a
   completed run records its evidence even when a target or gate fails. Use a
   new output directory for each run and check the exit status.
   These exit codes are the Python command's; Make returns its own nonzero code
   when that command fails.

The live benchmark and its evidence limits are recorded beside this set. The
answering A/B’s V01–V04 remain separate from passage quality. Merging publishes
the next KGE kit. Existing pilots apply the new Terraform to take medium as
the tagger tier; explicit overrides still win.
