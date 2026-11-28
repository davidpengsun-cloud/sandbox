# One command builds the synthetic estate, certifies it upstream, loads the
# graph, runs the tagging pipeline and serves the API. Everything generated
# lands in build/, which is git-ignored: nothing synthetic is ever committed.
#
# Every step runs for one profile, PROFILE, chosen at deployment. The profiles
# registry, deploy/profiles.yaml, names each profile's client and points at
# where that client's things are (kge/pipeline/profile.py); the steps read their
# paths from it, and one runtime process serves one profile.
#
# Each step builds what it needs only if it is missing: `make graph` loads the
# release certification already landed rather than regenerating anything, and
# `make pipeline` loads the graph first if there is none. `make estate` always
# regenerates, and certifies what it generated; `make certify` re-runs the
# certification alone. Certification lands what it certified for the profile's
# client in the landing zone, build/landing/<client>/releases/<release_id>/ with
# current pointing at it, and keeps its run record in its own store in the
# profile's estate, <estate>/certification/<client>/runs/ (the estate is the
# folder that holds the profile's warehouses, deploy/profiles.yaml: build/ for
# Meridian). The graph and the pipeline read the
# landed release and nothing else of the estate. `make onboard` refreshes the warehouse
# schema in clients/<client>/pack.lock before compiling. `make lock` stays for local use.
# `make check` checks the lock and the pack and writes nothing; `make compile`
# checks the pack and its lock against the platform's meta-model and compiles
# them into a graph artifact, build/artifacts/<client>/<model_hash>/. `make load`
# loads the artifact the pack compiles to (or HASH's), with the landed release,
# into a fresh graph of its own in the client's graph store,
# build/stores/<client>/<model_hash>/graph.<store>, runs the pipeline on it and
# marks it LOADED: a candidate beside the graph being served, whose folder it
# never touches. `make margin HASH=<hash>` checks it (its GATE) and
# `make sign-off PRINCIPAL=<steward>` signs off one that passed (its SIGNOFF),
# onboarding's steps 7 and 8. `make release` swaps the client's current pointer,
# build/artifacts/<client>/current, to that hash (or HASH), refusing one that is
# not LOADED, or not checked and signed off unless it was released before; the
# runtime reads current when it starts. `make graph` is the lock's check,
# compile, load and release in a row, released without the onboarding gate (the
# developer's bootstrap, as `make demo`), and it says so. What stewards decide is the
# runtime overlay: journaled in the client's graph store,
# build/stores/<client>/overlay/decisions.jsonl, and replayed by every
# `make graph` (the terms they accepted) and `make pipeline` (the decisions),
# so a decision outlives the graph; `make reset-overlay` sets the journal aside.
# What the process serving the profile writes as it runs (the tokens, the API's
# pid, the profile and address it serves, the A/B runs, the pipeline's stats, the
# eval, and pilot sign-in's password hashes, passwords and session key) is the profile's run state,
# build/stores/<client>/run/.
# The API holds the file lock of the graph it serves: stop it before graph, pipeline, agent, ab, margin, parity or test.
# make load, make margin HASH=<hash> and make sign-off of a candidate, and make release, run beside it.

PY ?= python3
KGE ?= $(PY) -m kge.cli
# kge, the steps' one definition (kge/cli/), and the settings given on make's command line, each one word as given, empty
# or quoted; the environment reaches it as it is.
kge_quote = '$(subst ','"'"',$(1))'
KGE_ARGS = $(strip $(foreach name,$(filter-out KGE,$(.VARIABLES)),$(if $(filter command line,$(origin $(name))),$(call kge_quote,$(name)=$($(name))))))
# Line coverage (.coveragerc): one data file for every lane's processes, here, whatever folder a child runs in, and the
# demo's own files measured from it, by absolute path. MIN is make coverage-check's threshold, the full check's 70.
# COVERAGE_CORE sysmon measures through Python's sys.monitoring (3.12 and later, which the demo needs): line coverage at
# a fraction of the default tracer's cost, the same lines counted, so a full check's leg is not slowed many times over.
COVERAGE_FILE := $(CURDIR)/.coverage
KGE_COVERAGE_SOURCE := $(CURDIR)
COVERAGE_CORE ?= sysmon
export COVERAGE_FILE KGE_COVERAGE_SOURCE COVERAGE_CORE
MIN ?= 70
SHARD ?=
# The profile: PROFILE (the environment or the command line, else .env), else the registry's default (PROFILES names
# another registry). Read once, as kge/pipeline/profile.py reads it, so Make and the scripts agree: its id is exported to
# every step, and each step resolves it again and stops on one it does not know, naming the known profiles. If Python
# cannot say (no venv yet, or a profile or registry it refuses), nothing is set from it here and each step says why.
PROFILE ?=
# PROFILE_READS: every other setting kge/pipeline/profile.py --make reads (the registry; the backends that choose the graph
# file and the BigQuery sync), passed to it as given here, so make pipeline GRAPH_STORE=bigquery resolves what its steps will.
PROFILE_READS := PROFILES WAREHOUSE_BACKEND RETRIEVAL_BACKEND GRAPH_STORE
passed = $(if $(filter environment command line,$(origin $(1))),$(1)='$($(1))' )
PROFILE_SAYS := $(shell $(if $(PROFILE),PROFILE='$(PROFILE)' )$(foreach name,$(PROFILE_READS),$(call passed,$(name)))$(PY) -m kge.pipeline.profile --make 2>/dev/null)
profile = $(patsubst $(1)=%,%,$(filter $(1)=%,$(PROFILE_SAYS)))
PROFILE := $(or $(PROFILE),$(call profile,id))
ifneq ($(PROFILE),)
export PROFILE
endif
# Where the profile's things are, as kge/pipeline/profile.py --make says, relative to this folder where they are under it:
# the client's own folder (its pack, its lock and its baselines: the parity and estate snapshots, taken before issue #39
# changed anything, and the A/B baselines); the simulated estate (its warehouses, documents, question sets and summary);
# the client's current landed release; its graph store (the graph, overlay/ and run/) and graph; and its A/B runs.
# The simulated estate: Meridian's is generated from clients/meridian/pack.yaml (kge/estate/generate.py; kge/estate/spec.py
# reads it); a client whose folder holds a fixture, $(PACK)/fixture/, has its estate built from those files instead
# (kge/estate/from_files.py), with no Python of the client's. ESTATE_FROM is which, for make estate, make golden and the
# estate make demo starts with.
# Then the client's compiled artifacts, and the model hash its current pointer names: the graph the runtime serves is that
# hash's, GRAPH, in its folder of the client's graph store (with none released, GRAPH is the current pointer itself, which
# make graph makes): graph.ladybug on the local stack, graph.bigquery, the pointer to its tables, where GRAPH_STORE is
# bigquery (kge/adapters/graphstore/). HASH names another, a candidate or an old one, for make load, make release, make pipeline and make
# margin; without it make load and make release take the hash the pack compiles to now, and the others current's.
PACK := $(call profile,pack)
ESTATE_DIR := $(call profile,estate)
LANDED := $(call profile,landed)
STORE := $(call profile,store)
RUNS := $(call profile,runs)
MARGIN_STATUS ?= $(dir $(RUNS))margin.status
ARTIFACTS := $(call profile,artifacts)
CURRENT := $(call profile,current)
GRAPH := $(if $(STORE),$(if $(CURRENT),$(STORE)/$(CURRENT)/$(call profile,graphfile),$(ARTIFACTS)/current))
HASH ?=
# The steps' own logic (each client's paths, the BigQuery sync, the load's steps, the portal's needs and its token) is
# kge's, kge/cli/: each target below that a person runs is one call to it, with the settings given on make's command line.
ESTATE_FROM := $(if $(wildcard $(PACK)/fixture/schema.yaml),-m kge.estate.from_files,-m kge.estate.generate)
# Everything generated, every profile's: make clean removes it.
BUILD := build
HOST ?= 127.0.0.1
PORT ?= 8000
RELOAD ?=
DEMO_MODE ?=
# make demo-script: Thornbury's port beside Meridian's PORT, whether to show the browser, and Playwright for Node, found in
# NODE_DEPS or among Node's global packages, else installed into NODE_DEPS at PLAYWRIGHT. The script defaults PORT2/3
# to 8001/8002, or reuses PORT sequentially when the runner assigns PORTAL_PORT.
PORT2 ?=
PORT3 ?=
HEADED ?=
# make demo-script and make test-e2e: PART=i/N runs the i-th of the walkthrough's N parts alone (tests/e2e/demo_script.cjs's
# PARTS), as the full check's e2e legs do; without it, the whole walkthrough.
PART ?=
PLAYWRIGHT ?= 1.56.1
NODE_DEPS := $(BUILD)/node
# make portal and make demo: the discovery portal's port, 90 above the page's (8090 beside 8000, 8091 beside Thornbury's
# 8001), and the KGE page it calls, which make serve serves on PORT.
PORTAL_PORT ?= $(shell expr $(PORT) + 90 2>/dev/null)
KGE_URL ?= http://127.0.0.1:$(PORT)
PORTAL_CATALOGUE_FILE ?=
API ?= http://$(HOST):$(PORT)
ARM ?= all
AGENT_ARM ?= both
# Who make eval, make agent and make ab ask as: PRINCIPAL, else the querying identity the profile's deployment names (asker,
# in deploy/<client>/identities.yaml), which each script reads; no client's principal is named here. Who make sign-off
# signs off as: PRINCIPAL, which it needs.
PRINCIPAL ?=
AS := $(if $(PRINCIPAL),--as $(PRINCIPAL))
Q ?= Total retail revenue for June 2025
REPEATS ?= 3
ARMS ?= all
SETS ?= all
RUN ?=
NAME ?=
PARITY ?= $(PACK)/baselines/parity-4d9d5c9.json
# The owner's stated changes to the 183 since then, each naming its result, field, value and why: the #39 Option B update,
# and #316's owner-approved interpretation and coverage/check corrections. make parity fails on any other move.
PARITY_STATED ?= $(PACK)/baselines/parity-stated.json
# The estate's tables as 4d9d5c9 generated them, before issue #39 added to them: per table, its columns, row count and a
# checksum of its rows (kge/estate/check_estate.py). make check-estate holds the build to it.
ESTATE ?= $(PACK)/baselines/estate-4d9d5c9.json
# The A/B baseline make ab and make margin put runs beside, and make baseline records into: the profile's (the
# registry's baseline), issue #402's, three repeats on rules with every declared set and the owner's 2026-11-10 release approval.
# Historical issue #40 ran every arm on rules, on golden v2, heldout v1 and
# golden_wealth_v1, as #39's, kept beside it, was. #38's, recorded before #39 changed anything, is kept as the before:
# make ab BASELINE=$(PACK)/baselines/issue-38.json SETS=golden,heldout puts a run beside it.
BASELINE ?= $(call profile,baseline)
# The committed set files make check-estate holds the build's heldout.json and golden.json to, byte for byte; git keeps
# their history.
SET_FILES ?= $(PACK)/fixture

ITEM ?= all
PROPOSAL ?=

.PHONY: access setup-client demo profile estate seed check-estate certify lock check compile load sign-off release graph pipeline reset-overlay reset-onboard reset-client client brackenridge-db serve eval golden20 golden20-baseline examples grounding-eval capability-matrix eval-api agent ab baseline margin eval-pre parity golden principals tokens passwords test test-unit test-integration extended test-e2e e2e-full coverage-check local-gate gate-coverage gate-stamp-check gate-select infra-tests kit-tests doc-tests portal-unit-tests gate-overrides gate-legs parity-margin full-check full-check-leg repository-checks portal-tests _full-check-leg test-durations lint demo-script screenshots clean source rows draft accept certify-draft onboard improve kit kit-notices onboard-status portal portal-build

access:
	$(KGE) access $(KGE_ARGS)

setup-client:
	$(KGE) setup-client $(KGE_ARGS)

demo:
	$(KGE) demo $(KGE_ARGS)

# The profile every step runs for, and where its things are; an unknown profile stops it, naming the known ones.
profile:
	$(KGE) profile $(KGE_ARGS)

$(GRAPH):
	$(KGE) graph $(KGE_ARGS)

# Regenerate the sample estate locally; fixture clients rebuild changed inputs during onboard.
estate seed:
	$(KGE) estate $(KGE_ARGS)

source:
	$(KGE) source $(KGE_ARGS)

rows:
	$(KGE) rows $(KGE_ARGS)

draft:
	$(KGE) draft $(KGE_ARGS)

accept:
	$(KGE) accept $(KGE_ARGS)

certify-draft:
	$(KGE) certify-draft $(KGE_ARGS)

improve:
	$(KGE) improve $(KGE_ARGS)

# Rebuild a changed fixture, refresh the lock, compile, load and check. A rebuilt current hash reloads too.
onboard:
	$(KGE) onboard $(KGE_ARGS) $(if $(FROM),"FROM=$(FROM)")

onboard-status:
	$(KGE) onboard-status $(KGE_ARGS)

# The estate is added to, never changed: the Wealth expansion and the client cross-reference (#39) come from seeded
# streams of their own, after every draw the four units take. This holds each table 4d9d5c9 had, over the columns it
# had, to its rows (ESTATE, frozen), and build/heldout.json and build/golden.json to the committed set files (SET_FILES),
# byte for byte. It lists what was added, and fails naming each table or set that moved.
# Reads the warehouses and the set files, never the graph.
check-estate:
	$(KGE) check-estate $(KGE_ARGS)

# The upstream AI certification stand-in, alone: reads the documents and the warehouses' catalogues, never the graph,
# and lands a release for the profile's client (the same estate lands the same release, and writes nothing new).
certify:
	$(KGE) certify $(KGE_ARGS)

# Local use: freeze the landed catalogue (tables, columns, keys; never a warehouse's rows) of each source the pack harvests, its
# curated sources after the units (the client cross-reference), into the client's pack.lock, $(PACK)/pack.lock, the
# client's own and committed. Writes nothing if the lock already holds it.
lock:
	$(KGE) lock $(KGE_ARGS)

# The checks alone, writing nothing: the lock against the landed catalogue, then the pack against the platform's meta-model
# and its lock (kge/compiler/compile.py --check), which prints the path its artifact has, or would have, and says whether it is
# compiled yet. It lands nothing first: where nothing has landed, the lock's check says so, naming make certify, the pack
# is checked all the same, and make check fails. It fails if either check does.
check:
	$(KGE) check $(KGE_ARGS)

# Check the client's pack ($(PACK)/pack.yaml and the files it includes) against the platform's meta-model and its lock,
# and compile them into the profile's artifacts, build/artifacts/<client>/<model_hash>/, printing that path. Reads
# meta-model.yaml, the pack and pack.lock, nothing else: no landing, no warehouse, no graph. On any finding it lists each
# one (<rule> <path>: <message>), exits 2 and writes nothing. The same pack and lock give the same hash and bytes, and a
# second compile writes nothing. On stderr it says how many harvested foreign keys no declared join covers (join
# candidates, listed in the artifact's graph-model.yaml). make graph compiles the pack this way, then loads the artifact.
compile:
	$(KGE) compile $(KGE_ARGS)

# A candidate: the artifact the pack compiles to (make compile must have written it), or HASH's, loaded beside the graph
# being served. kge/graph/load.py checks the artifact whole (each file's hash, the meta-model version, the embedding dimension,
# the client: the profile's), then builds a fresh graph of the artifact's own, in the folder of the client's graph store its
# model hash names, from it, the landed release and the profile's identities (deploy/<client>/identities.yaml), the terms
# stewards accepted replayed from the client's overlay journal; the pipeline then runs on it, replaying the decisions; and
# last, kge/graph/release.py --mark-loaded finds it whole and writes the folder's LOADED marker for this store. It never opens
# another hash's folder, so the graph the API serves, and its lock, are left alone (reloading the served hash itself needs
# the API stopped). A finding, an artifact it will not load, or a journal holding another client's records stops it before
# the graph is touched. It releases nothing.
load:
	$(KGE) load $(KGE_ARGS)

# Onboarding's step 8 (issue #40): the steward's sign-off on the candidate make margin HASH=<hash> passed, the hash the
# pack compiles to, or HASH. kge/graph/release.py --sign-off writes the SIGNOFF marker beside its LOADED and GATE, once its
# graph is LOADED, its GATE passed, and the graph lets PRINCIPAL perform certification (the pack's roles, bound to the
# deployment's identities: no principal is named here); refused otherwise, nothing written. A reload drops it with GATE.
# Releases nothing. Runs beside the API.
sign-off:
	$(KGE) sign-off $(KGE_ARGS)

# Swap the client's current pointer to the hash the pack compiles to, or HASH (an old one, say: its folders stay), once
# kge/graph/release.py finds its artifact whole, its graph LOADED on this store, and it through the onboarding gate: its GATE
# passed (make margin HASH=) and signed off on that check (make sign-off), unless it was released before (its RELEASED
# marker: a rollback needs no new sign-off); refused otherwise, current as it was. The swap is atomic (a new link renamed
# over the old one). The API reads current when it starts: restart it to serve the hash.
# PREVIOUS=yes is passed through KGE_ARGS to select served.json's previous identity, including the same hash.
release:
	$(KGE) release $(KGE_ARGS)

# make check, compile, load and release in a row. The lock is checked first: if the landed catalogue has moved, it stops
# with "pack.lock is stale: run make lock", naming each difference, and loads nothing. Then the pack is compiled, the
# artifact loaded as a candidate (make load) and released, without the onboarding gate (--ungated: no make margin HASH=,
# no make sign-off), as make demo releases what it builds: the developer's bootstrap, which says "released without the
# onboarding gate". Steward decisions survive it: the terms they accepted are replayed from the client's overlay journal
# as it loads, and the decisions by the pipeline.
graph:
	$(KGE) graph $(KGE_ARGS)

# Tags, the gate and embeddings for the landed chunks, then the runtime overlay: every steward decision journaled for
# the client replayed under its own ids, and each one whose document, chunk or match is gone, or whose text changed,
# reported as an orphan (stderr, build/stores/<client>/overlay/orphans.json, the run state's pipeline_stats.json) and
# never attached. On the graph current names, or HASH's; make load runs it on the candidate, so make graph has run it.
pipeline:
	$(KGE) pipeline $(KGE_ARGS)

# Set the runtime overlay aside: the client's steward-decision journal moves to
# build/stores/<client>/overlay/archive/<utc>.jsonl, kept, and the next make graph and make pipeline start from a fresh
# queue (make reset-overlay graph pipeline, with the API stopped). Opens no graph.
reset-overlay:
	$(KGE) reset-overlay $(KGE_ARGS)

# Remove exactly the pack inputs and generated estate files one onboarding-owned
# unit added, certify the remaining estate, lock it, and release the graph it compiles.
# The last unit's reset drops kept packs and invalidates the load, gate, sign-off
# and release marks of every other hash of this client; those hashes must be loaded
# and approved again.
# The reset record is kept with the unit in the pack store, so the same target works
# on a local checkout and STORE_URL. Nothing
# to reset, or a unit onboarding stopped before its reset record (its state only),
# succeeds with no certify or release; a unit onboarding did not make is refused.
reset-onboard:
	$(KGE) reset-onboard $(KGE_ARGS)

client:
	$(KGE) client $(KGE_ARGS)

reset-client:
	$(KGE) reset-client $(KGE_ARGS)

brackenridge-db:
	$(PY) examples/brackenridge/build_database.py

# The page and the API (kge/app.py), the context service among it: POST /context and the MCP server at /mcp (kge/services/ask/engine/context.py).
# RELOAD=--reload while editing; off by default so one process holds the graph. The profile first: an unknown one stops
# it before anything is served. SIGNIN=passwords (the command line, the environment or .env) is the pilot's sign-in: a
# person signs in with a username and the password make passwords made, and sessions survive a restart; beside it,
# DEMO_MODE=1 signs in by name, from this machine only. The API is make's own child (exec), so a stop sent to make
# alone, as docker stop sends one to the image's first process, reaches it and it shuts down as on Ctrl-C.
serve:
	$(KGE) serve $(KGE_ARGS)

# The discovery portal (portal/), from this checkout, beside the page make serve runs (SIGNIN=passwords): its UI built and
# its server started on PORTAL_PORT, calling KGE at KGE_URL, with its SQLite database in build/portal/. Each npm ci runs
# only when its lockfile is newer than the install it made, or there is none; the UI is rebuilt only when a source of it
# changed. It needs Node.js 22.12 or later and npm, and refuses before installing anything without them. The server is
# make's own child (exec), as make serve's API is.
portal:
	$(KGE) portal $(KGE_ARGS)

portal-build:
	$(KGE) portal-build $(KGE_ARGS)

# ARM=<preset> runs one arm of the control; the presets are in kge/services/ask/engine/config.py (GET /presets).
eval:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(KGE) eval $(KGE_ARGS)

capability-matrix:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(PY) -m pytest -q -s tests/integration/test_capability_matrix.py -p no:cacheprovider

# The owner's 20 golden NNA questions as a hard gate (tests/integration/test_golden20.py, issue #474): every item of the
# SAGE set's v3 (S06 to S25) with each of its phrasings, asked through the Ask path on the sample and compared with its
# reference SQL's table; it prints "N of 20 answer correctly" and each failure with the layer that refused it.
# HELDOUT=<file> also scores held-out phrasings, a JSON list of {"id", "phrasings"}: "N of 20 on held-out phrasings".
# The golden-20 baseline (kge/estate/golden20.py): each golden item's reference SQL on the sample, its rows and headline
# figure, and whether the SQL gate's reference path passes it as written, written to clients/meridian/baselines/golden20.json.
# make golden20 compares each answer with it and fails if it is stale.
golden20-baseline:
	MODEL_PROVIDER=none $(PY) -m kge.estate.golden20 --write

# AI-Generated mode's verified examples (kge/estate/examples.py): each golden question with the plan the rules certify
# for it, written to clients/<client>/baselines/examples.json for the on-mode prompt; tests/integration/test_examples.py fails if stale.
examples:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(PY) -m kge.estate.examples --write

golden20:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none GOLDEN20_HELDOUT=$(HELDOUT) $(PY) -m pytest tests/integration/test_golden20.py -q -p no:randomly -rfEX

grounding-eval:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(PY) -m pytest tests/eval -m eval -q -s

eval-api:
	$(PY) -m kge.eval.run_golden --api $(API) --arm $(ARM) $(AS)

# The reference agent (kge/services/ask/engine/agent.py) on Q, as PRINCIPAL (else the deployment's asker): AGENT_ARM=control (graph off),
# treatment (graph on) or both. Its loop runs on LangGraph, and each arm's context comes through the context contract:
# POST /context with the principal's own token, in this process.
# On rules unless a model is configured; MODEL_PROVIDER=mock runs its model path on the offline mock (MOCK_MODE for an
# adversary). Each answer says which: rules, model or mock.
agent:
	$(PY) -m kge.services.ask.engine.agent --arm $(AGENT_ARM) $(AS) "$(Q)"

# The A/B harness (kge/eval/harness.py): ARMS (all, or a comma list of the presets schema, catalog, graph and the agent's
# control, treatment) on SETS (all: golden, heldout, then each further set the estate holds, <name>_v<n>.json, as
# golden_wealth_v1, the Wealth expansion's; or a comma list of them), REPEATS times, as PRINCIPAL (else the deployment's
# asker), or as the principal
# an item names (a join-time access refusal of golden_wealth_v1's). Offline: the agent arms run on rules unless a model
# is configured (MODEL_PROVIDER=mock for the mock), their context through POST /context as make agent's. Records, run traces and the report (report.md, report.html) land in
# the profile's runs, $(RUNS)/<run_id>/, the report beside BASELINE (the profile's, committed;
# BASELINE=$(PACK)/baselines/issue-38.json for #38's, BASELINE= for none); it prints, and the report shows, each set's
# passes per category, graph off and graph on, the baseline sets beside the expansion.
ab:
	$(KGE) ab $(KGE_ARGS)

# Record a finished A/B run as the baseline later runs are put beside: RUN=<run_id> from the profile's runs, into
# BASELINE (the profile's, committed; another with BASELINE=<file>), named NAME (default: the file's name).
baseline:
	$(KGE) baseline $(KGE_ARGS)

# The post-load check (kge/eval/margin.py), after make graph and make pipeline: the agent's control and treatment on rules,
# REPEATS times, as the baseline's principal (else the deployment's asker), one verdict on the graph. Against the gates
# the loaded artifact holds (gates.json, the pack's manifest.gates): graph on's golden pass rate per kind at least
# golden_min_pass (V01), graph on less graph off at least control_margin (V02), the held-out set reported and never
# failing (V03), and no graph-on answer citing an uncertified or off-tier document (V04). And the margin (treatment less
# control, per score and kind) beside BASELINE's, where there is one; with none (BASELINE= too, as for make ab), the
# gates are checked alone. Fails naming each check, item, score and kind that failed: a score and kind whose margin fell
# by more than its spread across repeats, or that the baseline measured and is n/a now. Its verdict is the GATE marker
# beside the graph's LOADED, which a reload drops. Beside the profile's, each set is the one it ran (its name, version
# and items run), so every score is checked on each. A set changed since is reported by its counts alone: git history
# is the record of how a set changed, and a set changed without the baseline re-recorded in the same change fails
# tests/unit/test_baselines.py. Beside #38's (BASELINE=$(PACK)/baselines/issue-38.json), golden has changed since, so
# it is reported by its counts alone; golden_wealth_v1, which #38's did not run, is run and reported as new, with
# nothing to check it against. A baseline of
# another profile's is refused. kge/eval/margin.py names no client's file: it checks against the baseline it is given,
# BASELINE here. HASH=<hash> checks a candidate make load loaded, on its own graph, before make release serves it, while
# the API serves current's.
eval-pre:
	$(KGE) eval-pre $(KGE_ARGS)

margin:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(KGE) margin $(KGE_ARGS)

# Preset parity (kge/eval/parity.py): the three presets on the golden and held-out items, each of the 183 results (pass,
# behaviour, figure, parts and refused joins) beside the snapshot PARITY, frozen before issue #39. Each move the owner
# stated (PARITY_STATED) is named as stated; any other result that moved fails it, named.
parity: | $(GRAPH)
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(PY) -m kge.eval.parity --snapshot $(PARITY) $(if $(wildcard $(PARITY_STATED)),--stated $(PARITY_STATED))

# The question sets alone, again from the warehouses on disk: build/golden.json, golden v2, build/heldout.json,
# heldout v1, and build/golden_wealth_v1.json (kge/estate/golden_wealth.py), the owner's Wealth and cross-unit questions,
# each figure its reference SQL's result and none a naive reading's (the build stops on one that is). make ab, make
# margin and make parity refuse a set file older than the version the code runs, naming this target;
# a build made before golden_wealth_v1 runs the other two until this target writes it. A client with a fixture has its
# golden set copied from it (kge/estate/from_files.py --golden-only).
golden:
	$(PY) $(ESTATE_FROM) --golden-only

# Re-sync the graph's access mirror alone, after an edit to deploy/<client>/identities.yaml or to the pack's roles: the
# identities bound to the role templates of the pack of the client the graph was loaded for, chunks and decisions kept.
principals:
	$(PY) -m kge.graph.load --principals-only

tokens:
	$(PY) -m kge.services.identity.cli

# Pilot sign-in's passwords (SIGNIN=passwords): a new one for each person, a principal bound to querying or certification,
# every earlier one replaced; none for a machine principal. Their salted hashes in the run state's passwords.json, the
# passwords themselves once in passwords.txt beside it, which only you can read; neither printed. The API reads them at
# each sign-in: no restart.
passwords:
	$(KGE) passwords $(KGE_ARGS) $(if $(ROTATE),"ROTATE=$(ROTATE)")

# The tests need requirements-dev.txt, which requirements.txt leaves out.
test:
	@$(PY) -c 'import pytest' 2>/dev/null || { echo 'make test needs pytest: pip install -r requirements-dev.txt' >&2; exit 2; }
	$(PY) -m pytest tests -q

# The unit and integration lanes run once under xdist; SHARD=i/N selects a pytest-split group.
# Their JUnit reports name every test over its lane's budget.
SPLIT_ARGS = $(if $(SHARD),--splits $(word 2,$(subst /, ,$(SHARD))) --group $(word 1,$(subst /, ,$(SHARD))) --splitting-algorithm least_duration,)

test-unit:
	$(if $(SHARD),@test -s .test_durations || { echo 'make test-unit: run make test-durations first' >&2; exit 2; },@true)
	@mkdir -p $(BUILD)/test-reports
	@rm -f $(BUILD)/test-reports/unit.xml
	@$(PY) -m coverage erase
	@$(PY) -m pytest $$($(PY) tests/lanes.py unit) -q -m unit -n auto $(SPLIT_ARGS) --cov --cov-append --cov-report= --junitxml=$(BUILD)/test-reports/unit.xml; result=$$?; \
	$(PY) tests/timing.py tests unit $(BUILD)/test-reports/unit.xml || exit 1; exit $$result

# The integration lane's gate tier, the must-have integration tests (owner, 2026-11-16): every integration-lane test but
# the extended tier tests/lanes.py EXTENDED names. It runs nightly (owner, 2026-11-17), no leg of the local gate or CI.
test-integration:
	$(if $(SHARD),@test -s .test_durations || { echo 'make test-integration: run make test-durations first' >&2; exit 2; },@true)
	@mkdir -p $(BUILD)/test-reports
	@rm -f $(BUILD)/test-reports/integration.xml
	@$(PY) -m pytest $$($(PY) tests/lanes.py integration) -q -m 'integration and not extended' -n auto --dist=loadgroup $(SPLIT_ARGS) --cov --cov-append --cov-report= --junitxml=$(BUILD)/test-reports/integration.xml; result=$$?; \
	$(PY) tests/timing.py tests integration $(BUILD)/test-reports/integration.xml || exit 1; exit $$result

# The integration lane's extended tier (tests/lanes.py EXTENDED) on make graph pipeline's build: the nightly job's run,
# not a pull request's and not a leg of the local gate. SHARD=i/N runs the i-th of N duration-balanced groups.
extended:
	$(if $(SHARD),@test -s .test_durations || { echo 'make extended: run make test-durations first' >&2; exit 2; },@true)
	@mkdir -p $(BUILD)/test-reports
	@rm -f $(BUILD)/test-reports/extended.xml
	@$(PY) -m pytest $$($(PY) tests/lanes.py integration) -q -m extended -n auto --dist=loadgroup $(SPLIT_ARGS) --junitxml=$(BUILD)/test-reports/extended.xml; result=$$?; \
	$(PY) tests/timing.py tests integration $(BUILD)/test-reports/extended.xml || exit 1; exit $$result

test-e2e:
	@$(PY) -c 'import pytest, pytest_cov' 2>/dev/null || { echo 'make test-e2e needs pytest and pytest-cov: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
ifeq ($(filter-out 1/% smoke,$(PART)),)
	$(PY) -m pytest tests/e2e -q -m e2e --cov --cov-append --cov-report=
	node --test tests/e2e/demo_ports.test.cjs
endif
	COVERAGE_PROCESS_START="$(CURDIR)/.coveragerc" $(MAKE) --no-print-directory demo-script PART=$(PART)

# The whole walkthrough, all four parts in a row, with the E2E lane's tests: the nightly job's run (owner, 2026-11-16),
# not a pull request's, whose one e2e leg is e2e-smoke (PART=smoke, steps 1 and 2).
e2e-full:
	$(MAKE) --no-print-directory test-e2e PART=

# Refresh per-test durations from both complete lanes; a failed run leaves the committed file intact.
test-durations:
	@rm -f $(BUILD)/test-durations.tmp
	@mkdir -p $(BUILD)
	$(PY) -m pytest $$($(PY) tests/lanes.py unit) -q -m unit -n auto --cov --cov-append --cov-report= --store-durations --durations-path=$(BUILD)/test-durations.tmp
	$(PY) -m pytest tests -q -m integration -n auto --dist=loadgroup --cov --cov-append --cov-report= --store-durations --durations-path=$(BUILD)/test-durations.tmp
	mv $(BUILD)/test-durations.tmp .test_durations

# The lanes' coverage data combined, reported, written as HTML and XML into build/coverage/, and held to MIN percent.
coverage-check:
	@$(PY) -c 'import coverage' 2>/dev/null || { echo 'make coverage-check needs coverage: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
	@if ls .coverage.* >/dev/null 2>&1; then $(PY) -m coverage combine --append -q; fi
	@test -f .coverage || { echo 'make coverage-check: no coverage data: run the test lanes first' >&2; exit 2; }
	$(PY) -m coverage html -q -d $(BUILD)/coverage/html
	$(PY) -m coverage xml -q -o $(BUILD)/coverage/coverage.xml
	$(PY) -m coverage report --fail-under=$(MIN)

# The full check (README's Tests): the demo built, the unit lane, the integration lane on Ladybug (the local stack's
# only graph store; the pilot runs on BigQuery, not the local stack), preset parity, the onboarding
# margin, the browser walkthrough, then the three lanes' combined line coverage held to MIN percent. The API stopped;
# in a row, as make full-check runs it here, it takes about an hour.
#
# On CI the same work runs as legs side by side, each on a runner of its own: make full-check-leg LEG=<leg>, then make
# coverage-check MIN=70 over every leg's coverage data. Checks runs READY_LEGS on a pull request into develop when it is
# ready (the same legs as FULL_LEGS; the integration lane runs nightly, outside the gate and CI), WALKTHROUGH_LEGS when it changes the portal alone or the
# kit's Terraform alone, and FULL_LEGS on a run by hand and the weekly audit; the Release kit workflow runs FULL_LEGS
# before a kit tag's kit publishes, or a merge's whose ready run it cannot verify. A leg starts its coverage data afresh
# and builds what it needs: unit-i/N is make test-unit SHARD=i/N (no build); the integration lane (make test-integration,
# its gate tier, and make extended, its extended tier) is no leg: it runs nightly (owner, 2026-11-17);
# parity-margin is make graph pipeline parity-margin (parity, margin REPEATS=1 and golden20; measured by nothing, as in
# the row); e2e-smoke is make graph pipeline, then make test-e2e PART=smoke, the walkthrough's steps 1 and 2 with the
# lane's tests (make e2e-full, the whole walkthrough, is the nightly job's). A leg whose lane is measured fails if it leaves no coverage data. An unknown leg is refused.
# PREBUILT=1 uses a downloaded build only with CI=true (set by GitHub); local legs still build.
PREBUILT ?=
FULL_LEGS := unit-1/6 unit-2/6 unit-3/6 unit-4/6 unit-5/6 unit-6/6 parity-margin e2e-smoke
READY_LEGS := unit-1/6 unit-2/6 unit-3/6 unit-4/6 unit-5/6 unit-6/6 parity-margin e2e-smoke
WALKTHROUGH_LEGS := unit-1/6 unit-2/6 unit-3/6 unit-4/6 unit-5/6 unit-6/6 e2e-smoke
full-check-leg:
	+@KGE_TIMING_REPORT="$(if $(filter unit-%,$(LEG)),$(BUILD)/test-reports/$(firstword $(subst -, ,$(LEG))).xml,)" $(PY) tests/timing.py leg "$(LEG)" $(MAKE) --no-print-directory _full-check-leg LEG="$(LEG)"

_full-check-leg:
	@case ' $(FULL_LEGS) $(READY_LEGS) ' in *' $(LEG) '*) [ -n '$(LEG)' ] ;; *) false ;; esac || { echo "make full-check-leg: LEG is one of $(sort $(FULL_LEGS) $(READY_LEGS)), not '$(LEG)'" >&2; exit 2; }
	$(if $(filter unit-%,$(LEG)),@test -s .test_durations || { echo 'make full-check-leg: run make test-durations first' >&2; exit 2; },@true)
	@$(PY) -c 'import coverage' 2>/dev/null || { echo 'make full-check-leg needs coverage: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
	$(PY) -m coverage erase
ifeq ($(filter unit-%,$(LEG)),)
	@echo '== the demo, built'
ifeq ($(CI):$(PREBUILT),true:1)
	@test -f "$(GRAPH)" || { echo "make full-check-leg: the prebuilt graph is missing" >&2; exit 2; }
else ifneq ($(GATE_BUILT),)
	@test "$(GATE_BUILT)" = "$$(git rev-parse HEAD)" && test -f "$(GRAPH)" || { echo "make full-check-leg: the local gate's build is not of HEAD" >&2; exit 2; }
else
	$(MAKE) --no-print-directory graph pipeline
endif
endif
ifneq ($(filter unit-%,$(LEG)),)
	@echo '== unit lane, shard $(patsubst unit-%,%,$(LEG))'
	$(MAKE) --no-print-directory test-unit SHARD=$(patsubst unit-%,%,$(LEG))
else ifeq ($(LEG),parity-margin)
	@echo '== parity, margin and the golden 20'
	$(MAKE) --no-print-directory parity-margin
else ifneq ($(filter e2e-%,$(LEG)),)
	@echo '== E2E lane, part $(patsubst e2e-%,%,$(LEG))'
	$(MAKE) --no-print-directory test-e2e PART=$(patsubst e2e-%,%,$(LEG))
endif
ifneq ($(LEG),parity-margin)
	@if ls .coverage.* >/dev/null 2>&1; then $(PY) -m coverage combine --append -q; fi
	@test -f .coverage || { echo 'make full-check-leg: the $(LEG) leg left no coverage data' >&2; exit 1; }
endif

parity-margin:
	$(MAKE) --no-print-directory parity
	$(MAKE) --no-print-directory margin REPEATS=1
	$(MAKE) --no-print-directory golden20

# The local gate, run before every push: the lint, the repository checks, the portal's server and UI tests, then the
# full check (the unit lane, parity and the margin, the walkthrough and the coverage
# gate), with no model. LOCAL_GATE_LEGS is the one list of its legs, in the order it runs them, each with the paths from
# the repository's root that can change its result ('.' the whole repository): the push check requires a stamp for every
# leg on its paths (tests/gate_stamp.py reads the list from the pushed commit's Makefile). The full check runs as the
# legs CI runs it in, FULL_LEGS (make full-check-leg LEG=<leg>), on one make graph pipeline per run, made before the
# first leg that needs it and again only if HEAD moves (GATE_BUILT names the commit it was made from), then
# gate-coverage, which combines the coverage data each measured leg kept when it passed on the same inputs and holds it
# to MIN percent. Declare the paths conservatively: the unit and integration tests, parity and the margin, and the
# repository checks read files across the repository; the lint, the portal's tests and the walkthrough read only the
# demo's folder. LEG=<leg> runs one leg alone (several, space separated, in turn). Started
# on a clean tree, each leg that passes stamps its paths' content in HEAD (build/gate-stamps/<leg>/<key>), so a later
# commit that changes none of them keeps the stamp, and make gate-stamp-check says whether HEAD holds a stamp for every
# leg. A run narrowed by an override stamps nothing: GATE_OVERRIDES names each variable of GATE_WATCHED set on the
# command line or in the environment, and any other variable given on the command line but PY and the leg selector LEG
# (the command passes those on to the demo's commands, KGE_ARGS).
LOCAL_GATE_LEGS := lint:demos/kge repository-checks:. portal-tests:demos/kge \
  unit-1/6:. unit-2/6:. unit-3/6:. unit-4/6:. unit-5/6:. unit-6/6:. \
  parity-margin:. e2e-smoke:demos/kge gate-coverage:.
# The legs a change needs (tests/gate_stamp.py select, make gate-select), which make gate-stamp-check then requires: read from
# the Makefile at the change's merge base with origin/develop, so a change cannot widen its own selection. Each path the
# change touches (git diff-tree -r --no-renames from that merge base) takes the class of the first GATE_PATHS
# pattern it matches (* matches across /), and the legs are the union of those of every class it touched, gate legs in
# LOCAL_GATE_LEGS order, then LOCAL_GATE_NARROW's. The full gate runs for a path no pattern matches, an empty change, no
# merge base, a table that does not read, a symbolic link or submodule on either side of the change, and any change to
# this Makefile, tests/gate_stamp.py, .pre-commit-config.yaml or the checks, kit and kit-demo workflows, and whenever
# the union holds every leg of LOCAL_GATE_LEGS. A class in GATE_ALONE stays narrow only when every changed path lies
# under its folder (none does today: a change across types runs the union). gate-coverage runs only where every leg it
# measures (FULL_LEGS but parity-margin) runs. LOCAL_GATE_NARROW's legs run the unit test files that read one folder;
# make local-gate runs them only when LEG names them, every leg never needs them, and a selection with all six unit
# legs drops them, as the unit legs run those files.
LOCAL_GATE_NARROW := infra-tests:. kit-tests:. doc-tests:. portal-unit-tests:.
GATE_PATHS := full:.github/workflows/checks.yml full:.github/workflows/kit.yml full:.github/workflows/kit-demo.yml \
  full:.pre-commit-config.yaml full:demos/kge/Makefile full:demos/kge/tests/gate_stamp.py \
  portal-ui:demos/kge/portal/ui/* portal-server:demos/kge/portal/server/* portal:demos/kge/portal/* \
  infra:demos/kge/infra/* kit:demos/kge/kit/* \
  full:demos/kge/kge/* full:demos/kge/tests/* \
  demo-readme:demos/kge/README.md read-docs:demos/kge/CONTRIBUTING.md read-docs:demos/kge/REPORT.md read-docs:demos/kge/docs/* \
  full:demos/* infra:*.tf \
  read-docs:docs/use-cases/kge/* docs:docs/* \
  read-docs:.claude/skills/* read-docs:CONTRIBUTING.md shared:tools/* shared:.gitignore shared:.github/* full:.claude/* \
  docs:*.md
GATE_CLASSES := \
  portal-ui:lint,portal-tests,e2e-smoke,portal-unit-tests \
  portal-server:lint,portal-tests,e2e-smoke,portal-unit-tests \
  portal:lint,repository-checks,portal-tests,unit-1/6,unit-2/6,unit-3/6,unit-4/6,unit-5/6,unit-6/6,e2e-smoke,gate-coverage \
  infra:lint,repository-checks,infra-tests \
  kit:lint,repository-checks,portal-tests,kit-tests \
  read-docs:lint,repository-checks,doc-tests \
  demo-readme:lint,repository-checks,portal-tests,doc-tests,e2e-smoke \
  shared:lint,repository-checks,unit-1/6,unit-2/6,unit-3/6,unit-4/6,unit-5/6,unit-6/6 \
  docs:lint,repository-checks
GATE_ALONE :=
# The test files each narrow leg runs: every unit test that names the folder or files, or imports from a test module
# that does (followed transitively), which tests/unit/test_gate_stamp.py holds the lists to. The portal's own tests read
# the kit and the demo's README too, so those classes run portal-tests. The demo's README is read by the walkthrough's tests too
# (tests/e2e/test_demo_script.py), which need the built demo, so a README change runs e2e-smoke beside doc-tests.
INFRA_TESTS := tests/unit/test_api_surface.py tests/unit/test_checks.py tests/unit/test_client_id.py \
  tests/unit/test_engineer_skills.py tests/unit/test_forced_rules_env.py tests/unit/test_gate_stamp.py \
  tests/unit/test_infra.py tests/unit/test_kit.py tests/unit/test_tagger_eval.py tests/unit/test_terraform_runs.py
KIT_TESTS := tests/unit/test_agent_openapi.py tests/unit/test_api_surface.py tests/unit/test_check_pack.py \
  tests/unit/test_client_id.py tests/unit/test_compose_acceptance_fixes.py tests/unit/test_document_reader.py \
  tests/unit/test_engineer_skills.py tests/unit/test_eval_release_job.py tests/unit/test_gate_stamp.py \
  tests/unit/test_image.py tests/unit/test_image_scan.py tests/unit/test_improve_unit.py tests/unit/test_infra.py \
  tests/unit/test_kge_helper.py tests/unit/test_kit.py tests/unit/test_layers.py tests/unit/test_objstore.py \
  tests/unit/test_onboard_round3.py tests/unit/test_onboard_safety.py tests/unit/test_portal_image.py \
  tests/unit/test_posture.py tests/unit/test_pr3_guidance.py tests/unit/test_question_deadlines.py \
  tests/unit/test_question_runs.py tests/unit/test_release_guidance.py tests/unit/test_role_playbooks.py \
  tests/unit/test_roles_docs.py tests/unit/test_seed_success.py tests/unit/test_test_drive.py \
  tests/unit/test_third_party_notices.py tests/unit/test_tracing.py tests/unit/test_upgrade_reload.py
DOC_TESTS := tests/unit/test_api_surface.py tests/unit/test_ask_skill_outcomes.py tests/unit/test_asking.py tests/unit/test_authoring_skills.py \
  tests/unit/test_baselines.py \
  tests/unit/test_check_pack.py tests/unit/test_checks.py tests/unit/test_chunking_tables.py \
  tests/unit/test_client_id.py tests/unit/test_client_nouns.py tests/unit/test_compiler.py \
  tests/unit/test_compose_acceptance_fixes.py tests/unit/test_context_page.py tests/unit/test_ddl.py \
  tests/unit/test_developer_reference.py tests/unit/test_document_reader.py tests/unit/test_endpoint_tags.py \
  tests/unit/test_engineer_skills.py tests/unit/test_estate_declared.py tests/unit/test_eval_release_job.py \
  tests/unit/test_fixture_rebuild.py tests/unit/test_fixture_warehouse_versions.py tests/unit/test_from_files.py \
  tests/unit/test_gate_stamp.py tests/unit/test_image.py tests/unit/test_image_scan.py \
  tests/unit/test_improve_unit.py tests/unit/test_infra.py tests/unit/test_kge_helper.py tests/unit/test_kit.py \
  tests/unit/test_lock.py tests/unit/test_lock_versions.py tests/unit/test_measure_declarations.py \
  tests/unit/test_measure_variants.py tests/unit/test_meta_model.py tests/unit/test_model_path.py \
  tests/unit/test_model_period.py tests/unit/test_nothing_released.py tests/unit/test_objstore.py \
  tests/unit/test_onboard_api.py tests/unit/test_onboard_atomic.py tests/unit/test_onboard_from.py \
  tests/unit/test_onboard_round3.py tests/unit/test_onboard_safety.py tests/unit/test_onboard_unit.py \
  tests/unit/test_onboard_write_lease.py tests/unit/test_onboarding.py tests/unit/test_onboarding_guide.py \
  tests/unit/test_plan_models_matrix.py tests/unit/test_platform_docs.py tests/unit/test_portal_onboard.py \
  tests/unit/test_pr3_guidance.py tests/unit/test_pre_commit.py tests/unit/test_prose_paths.py \
  tests/unit/test_public_api_docs.py tests/unit/test_query_semantics.py tests/unit/test_release_guidance.py \
  tests/unit/test_role_playbooks.py tests/unit/test_roles_docs.py tests/unit/test_runtime_qualified_names.py \
  tests/unit/test_runtimes_model.py tests/unit/test_sample_fixture.py tests/unit/test_seed_success.py tests/unit/test_shape_fold_wiring.py \
  tests/unit/test_spec_port.py tests/unit/test_test_drive.py tests/unit/test_upfront_check.py \
  tests/unit/test_upgrade_reload.py tests/unit/test_versions.py
PORTAL_UNIT_TESTS := tests/unit/test_access_api.py tests/unit/test_access_cli.py tests/unit/test_access_table.py \
  tests/unit/test_api_surface.py tests/unit/test_baselines.py tests/unit/test_cerebro_abstention.py tests/unit/test_bigquery_sync_order.py \
  tests/unit/test_brand.py tests/unit/test_catalogue.py tests/unit/test_checks.py tests/unit/test_cli.py \
  tests/unit/test_cli_servers.py tests/unit/test_compiler.py tests/unit/test_developer_reference.py \
  tests/unit/test_document_reader.py tests/unit/test_endpoint_tags.py tests/unit/test_engineer_skills.py \
  tests/unit/test_estate_declared.py tests/unit/test_eval_stages.py tests/unit/test_fixture_rebuild.py \
  tests/unit/test_fixture_warehouse_versions.py tests/unit/test_from_files.py tests/unit/test_gate_stamp.py tests/unit/test_golden20_questions.py \
  tests/unit/test_image.py tests/unit/test_image_base_probe.py tests/unit/test_image_scan.py \
  tests/unit/test_improve_unit.py tests/unit/test_infra.py tests/unit/test_kit.py tests/unit/test_layers.py \
  tests/unit/test_lock_refresh.py tests/unit/test_lock_versions.py tests/unit/test_measure_declarations.py \
  tests/unit/test_model_gateway_langgraph.py tests/unit/test_model_quota.py \
  tests/unit/test_measure_variants.py tests/unit/test_onboard_atomic.py tests/unit/test_onboard_from.py \
  tests/unit/test_onboard_owns_lock.py tests/unit/test_portal_image.py tests/unit/test_portal_onboard.py \
  tests/unit/test_pre_commit.py tests/unit/test_prose_paths.py tests/unit/test_query_semantics.py \
  tests/unit/test_question_deadlines.py tests/unit/test_question_runs.py tests/unit/test_roles_source.py \
  tests/unit/test_runtime_qualified_names.py tests/unit/test_runtimes_model.py tests/unit/test_shape_fold_wiring.py tests/unit/test_tracing.py \
  tests/unit/test_telemetry_cloud.py tests/unit/test_telemetry_port_boundary.py \
  tests/unit/test_ui_tokens.py
GATE_LEG_NAMES :=$(foreach entry,$(LOCAL_GATE_LEGS),$(firstword $(subst :, ,$(entry))))
GATE_NARROW_NAMES :=$(foreach entry,$(LOCAL_GATE_NARROW),$(firstword $(subst :, ,$(entry))))
GATE_RUN_LEGS = $(or $(strip $(LEG)),$(GATE_LEG_NAMES))
GATE_WATCHED := SHARD PART MIN SPLIT_ARGS REPEATS PREBUILT HEADED GATE_BUILT INFRA_TESTS KIT_TESTS DOC_TESTS PORTAL_UNIT_TESTS
GATE_OVERRIDES = $(sort $(foreach name,$(GATE_WATCHED),$(if $(filter command line environment override,$(origin $(name))),$(name))) \
  $(foreach name,$(filter-out PY LEG,$(.VARIABLES)),$(if $(filter command line,$(origin $(name))),$(name))))
local-gate: export MODEL_PROVIDER = none
local-gate: export GATE_OVERRIDES := $(GATE_OVERRIDES)
local-gate:
	@for leg in $(GATE_RUN_LEGS); do case ' $(GATE_LEG_NAMES) $(GATE_NARROW_NAMES) ' in *" $$leg "*) ;; \
	  *) echo "make local-gate: LEG names legs of $(GATE_LEG_NAMES) $(GATE_NARROW_NAMES), not '$$leg'" >&2; exit 2 ;; esac; done
	+@built=; for leg in $(GATE_RUN_LEGS); do \
	  $(PY) tests/gate_stamp.py start $$leg || exit 1; \
	  target=$$leg data=; \
	  case ' $(FULL_LEGS) ' in *" $$leg "*) data=.coverage; \
	    case $$leg in unit-*) ;; *) head=$$(git rev-parse HEAD) || exit 1; \
	      if [ "$$built" != "$$head" ]; then fresh=; $(PY) tests/gate_stamp.py clean && fresh=$$head; \
	        $(MAKE) --no-print-directory graph pipeline || exit 1; built=$$fresh; fi ;; esac; \
	    target="full-check-leg LEG=$$leg GATE_BUILT=$$built" ;; esac; \
	  $(PY) tests/timing.py leg local-$$leg $(MAKE) --no-print-directory $$target && \
	  $(PY) tests/gate_stamp.py finish $$leg $$data || exit 1; \
	done

# The full check's coverage gate over the legs' kept data: each measured leg of FULL_LEGS must have passed on HEAD's
# inputs (tests/gate_stamp.py data), and their data combined is held to MIN percent.
gate-coverage:
	@$(PY) -c 'import coverage' 2>/dev/null || { echo 'make gate-coverage needs coverage: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
	$(PY) -m coverage erase
	@$(PY) tests/gate_stamp.py data $(filter-out parity-margin,$(FULL_LEGS)) > $(BUILD)/gate-coverage.list
	@n=0; while read -r kept; do n=$$((n + 1)); cp "$$kept" ".coverage.gate-$$n"; done < $(BUILD)/gate-coverage.list
	$(MAKE) --no-print-directory coverage-check MIN=$(MIN)

# The narrow legs: the unit test files that read the Terraform (INFRA_TESTS), the kit (KIT_TESTS), the documentation the
# tests read (DOC_TESTS) or the portal's UI and server (PORTAL_UNIT_TESTS), as the unit lane runs them.
infra-tests:
	MODEL_PROVIDER=none $(PY) -m pytest $(INFRA_TESTS) -q -m unit -n auto

kit-tests:
	MODEL_PROVIDER=none $(PY) -m pytest $(KIT_TESTS) -q -m unit -n auto

doc-tests:
	MODEL_PROVIDER=none $(PY) -m pytest $(DOC_TESTS) -q -m unit -n auto

portal-unit-tests:
	MODEL_PROVIDER=none $(PY) -m pytest $(PORTAL_UNIT_TESTS) -q -m unit -n auto

gate-stamp-check:
	@$(PY) tests/gate_stamp.py check

gate-select:
	@$(PY) tests/gate_stamp.py select

gate-overrides:
	@echo '$(GATE_OVERRIDES)'

gate-legs:
	@echo '$(LOCAL_GATE_LEGS)'

# The repository checks, as the Checks workflow's repository job runs them, from the repository's root.
repository-checks:
	cd ../.. && for check in figures structure disclosure links decks prose no_comments; do $(PY) tools/check_$$check.py || exit 1; done
	cd ../.. && $(PY) tools/drawio_to_svg.py --check

# The portal's server and UI tests, each from a clean npm ci of its own lockfile, as the Checks workflow's portal job.
portal-tests:
	npm ci --prefix portal/server && npm test --prefix portal/server
	npm ci --prefix portal/ui && npm test --prefix portal/ui

full-check:
	@echo '== the demo, built'
	$(MAKE) --no-print-directory graph pipeline
	@echo '== unit lane'
	$(MAKE) --no-print-directory test-unit
	@echo '== integration lane, Ladybug'
	$(MAKE) --no-print-directory test-integration
	@echo '== parity, margin and the golden 20'
	$(MAKE) --no-print-directory parity
	$(MAKE) --no-print-directory margin REPEATS=1
	$(MAKE) --no-print-directory golden20
	@echo '== E2E lane'
	$(MAKE) --no-print-directory test-e2e
	@echo '== combined coverage'
	$(MAKE) --no-print-directory coverage-check MIN=$(MIN)

# ruff over the demo's Python, as ruff.toml says (the KGE tests job runs it).
lint:
	@$(PY) -m ruff --version >/dev/null 2>&1 || { echo 'make lint needs ruff: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
	$(PY) -m ruff check .

# The README's Demo script, driven end to end in a browser: tests/e2e/demo_script.cjs, Playwright for Node in Chromium, not
# part of make test. It runs each step's commands as a presenter does (make demo, make compile load, make margin HASH=,
# make sign-off, make release, make graph, make reset-overlay graph, Thornbury's make demo), clicks through the page and
# checks what each step says the audience sees: Meridian on PORT, Thornbury beside it on PORT2 (PORT2=$(PORT) runs the
# two one after the other). Run it with the API stopped and a fresh steward queue; it puts the pack, the release and the
# overlay back as it found them. HEADED=1 shows the browser; PART=i/N runs the i-th of its parts alone. It needs Node 22.12
# or later; Playwright is found in $(NODE_DEPS) or among Node's global packages, else installed into $(NODE_DEPS) once,
# at PLAYWRIGHT, and its Chromium downloaded (to Playwright's own cache), with the system libraries it needs when CI is
# set (a CI runner).
demo-script:
	@node -e 'const [a,b] = process.versions.node.split(".").map(Number); process.exit(a > 22 || (a === 22 && b >= 12) ? 0 : 1)' 2>/dev/null || { echo 'make demo-script needs Node.js 22.12 or later: https://nodejs.org' >&2; exit 2; }
	@NODE_PATH="$(CURDIR)/$(NODE_DEPS)/node_modules:$$(npm root -g 2>/dev/null)" node -e 'require("playwright")' 2>/dev/null || { \
	  echo "Playwright $(PLAYWRIGHT) for Node into $(NODE_DEPS), and its Chromium: once"; \
	  npm install --prefix $(NODE_DEPS) --no-save --no-package-lock --no-audit --no-fund playwright@$(PLAYWRIGHT) && \
	  { $(NODE_DEPS)/node_modules/.bin/playwright install $(if $(CI),--with-deps )chromium || \
	    { echo "Playwright's system packages could not be installed; installing Chromium alone"; \
	      $(NODE_DEPS)/node_modules/.bin/playwright install chromium; }; }; }
	NODE_PATH="$(CURDIR)/$(NODE_DEPS)/node_modules:$$(npm root -g 2>/dev/null)" PY="$(PY)" HOST=$(HOST) PORT=$(PORT) $(if $(filter environment command line,$(origin PORTAL_PORT)),PORTAL_PORT="$(PORTAL_PORT)") $(if $(TEST_PORT),TEST_PORT="$(TEST_PORT)") PORT2=$(PORT2) PORT3=$(PORT3) HEADED=$(HEADED) PART=$(PART) node tests/e2e/demo_script.cjs

screenshots:
	$(MAKE) demo-script SCREENSHOTS=1

# The release kit (kit/build.sh), what a platform team installs the pilot from with no repository: the Terraform zip (infra/,
# the Compose files and the invented example content), the image tarballs with their digests and bill of materials, and
# the standalone install guide, in build/kit/kge-<VERSION>/ with SHA256SUMS. KIT_OUT names another parent folder,
# IMAGES=0 leaves the images out (no Docker), and BUILD_FLAGS goes to docker build as given. It refuses an image that
# trusts a certificate authority given at build time (one built behind a TLS-intercepting proxy, which sets SSL_CERT_FILE
# or the like): LOCAL_ONLY=1 lets it through, into build/kit/kge-<VERSION>-local-only/ with a LOCAL-ONLY note, a kit for
# local tests that is never shipped. The shippable kit is the Release kit workflow's (.github/workflows/kit.yml).
kit:
	VERSION="$(VERSION)" KIT_OUT="$(KIT_OUT)" IMAGES="$(IMAGES)" BUILD_FLAGS='$(BUILD_FLAGS)' LOCAL_ONLY="$(LOCAL_ONLY)" PY="$(PY)" sh kit/build.sh

# The kit's third-party notices check, which the Release kit workflow runs before it publishes a kit: the image's
# dependencies installed (requirements.txt under constraints.txt), then THIRD_PARTY_NOTICES.md checked against them, failing
# when it is stale (kit/generate_third_party_notices.py, which runs on Linux x86-64 alone, the image's platform).
kit-notices:
	$(PY) -m pip install --quiet -r requirements.txt -c constraints.txt
	$(PY) kit/generate_third_party_notices.py --check
	node kit/generate_npm_notices.mjs --check

clean:
	rm -rf $(BUILD)

# Regenerate the committed Meridian fixture from the sample generator.
.PHONY: sample-fixture
sample-fixture:
	$(PY) -m kge.estate.sample_fixture

.PHONY: tagger-eval
tagger-eval:
	$(PY) -m kge.eval.tagger $(TAGGER_EVAL_ARGS)

.PHONY: fold
fold:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(KGE) fold $(KGE_ARGS)
