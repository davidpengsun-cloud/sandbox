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
# golden_wealth_v1, as #39's, kept beside it, was. #38's, BEFORE, recorded before #39 changed anything, is kept byte for
# byte as the before: make ab BASELINE=$(PACK)/baselines/issue-38.json SETS=golden,heldout puts a run beside it, and
# make check-estate holds the build's set files to it.
BASELINE ?= $(call profile,baseline)
BEFORE ?= $(PACK)/baselines/issue-38.json

ITEM ?= all
PROPOSAL ?=

.PHONY: access setup-client demo profile estate seed check-estate certify lock check compile load sign-off release graph pipeline reset-overlay reset-onboard reset-client client brackenridge-db serve eval grounding-eval eval-api agent ab baseline margin eval-pre parity golden principals tokens passwords test test-unit test-integration test-e2e coverage-check full-check full-check-leg _full-check-leg test-durations lint demo-script screenshots clean source rows draft accept certify-draft onboard improve kit kit-notices onboard-status portal portal-build

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
# had, to its rows (ESTATE, frozen), and build/heldout.json and golden v1 (which golden v2 names) to the files the A/B
# baseline ran, #38's (BEFORE), by sha256. It lists what was added, and fails naming each table or set that moved.
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

grounding-eval:
	MODEL_PROVIDER=none MODEL_HIGH_PROVIDER=none MODEL_MEDIUM_PROVIDER=none MODEL_LOW_PROVIDER=none $(PY) -m pytest tests/eval -m eval -q -s

eval-api:
	$(PY) -m kge.eval.run_golden --api $(API) --arm $(ARM) $(AS)

# The reference agent (kge/services/ask/engine/agent.py) on Q, as PRINCIPAL (else the deployment's asker): AGENT_ARM=control (graph off),
# treatment (graph on) or both. Its loop runs on LangGraph, and each arm's context comes through the context contract:
# POST /context with the principal's own token, in this process, or at CONTEXT_URL (the environment, else .env).
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
# beside the graph's LOADED, which a reload drops. Beside the profile's, #40's, each set is the one it ran,
# golden_wealth_v1 among them, so every score is checked on each. Beside #38's (BASELINE=$(PACK)/baselines/issue-38.json, on golden v1): the
# margin of golden v2's pass rate on the 43 questions v2 did not change, from the baseline's passes per question;
# golden_wealth_v1, which #38's did not run, is run and reported as new, with nothing to check it against. A baseline of
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
# each figure its reference SQL's result and none a naive reading's (the build stops on one that is). Golden v2 is v1
# with retail-wealth dropped from G021's and G030's refused joins (the certified client cross-reference, issue #39),
# and its file declares the change, so make ab and make margin still put its other 43 questions beside a baseline on
# v1. make ab, make margin and make parity refuse a set file older than the version the code runs, naming this target;
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

test-integration:
	$(if $(SHARD),@test -s .test_durations || { echo 'make test-integration: run make test-durations first' >&2; exit 2; },@true)
	@mkdir -p $(BUILD)/test-reports
	@rm -f $(BUILD)/test-reports/integration.xml
	@$(PY) -m pytest $$($(PY) tests/lanes.py integration) -q -m integration -n auto --dist=loadgroup $(SPLIT_ARGS) --cov --cov-append --cov-report= --junitxml=$(BUILD)/test-reports/integration.xml; result=$$?; \
	$(PY) tests/timing.py tests integration $(BUILD)/test-reports/integration.xml || exit 1; exit $$result

test-e2e:
	@$(PY) -c 'import pytest, pytest_cov' 2>/dev/null || { echo 'make test-e2e needs pytest and pytest-cov: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
ifeq ($(filter-out 1/%,$(PART)),)
	$(PY) -m pytest tests/e2e -q -m e2e --cov --cov-append --cov-report=
	node --test tests/e2e/demo_ports.test.cjs
endif
	COVERAGE_PROCESS_START="$(CURDIR)/.coveragerc" $(MAKE) --no-print-directory demo-script PART=$(PART)

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
# On CI (Checks' KGE full check on a pull request into develop that is not a draft, and the Release kit workflow before a kit
# publishes) the same work runs as legs side by side, each on a runner of its own: make full-check-leg LEG=<leg>, one of
# FULL_LEGS, then make coverage-check MIN=70 over every leg's coverage data. A leg starts its coverage data afresh and
# builds what it needs: unit-i/N is make test-unit SHARD=i/N (no build); integration-i/N is make graph pipeline, then
# make test-integration SHARD=i/N; parity-margin is make graph pipeline parity margin REPEATS=1 (measured by nothing,
# as in the row); e2e-i/N is make graph pipeline, then make test-e2e PART=i/N, the walkthrough's i-th part (the lane's
# tests with part 1). A leg whose lane is measured fails if it leaves no coverage data. An unknown leg is refused.
# PREBUILT=1 uses a downloaded build only with CI=true (set by GitHub); local legs still build.
PREBUILT ?=
FULL_LEGS := unit-1/6 unit-2/6 unit-3/6 unit-4/6 unit-5/6 unit-6/6 integration-1/10 integration-2/10 integration-3/10 integration-4/10 integration-5/10 integration-6/10 integration-7/10 integration-8/10 integration-9/10 integration-10/10 parity-margin e2e-1/4 e2e-2/4 e2e-3/4 e2e-4/4
full-check-leg:
	+@KGE_TIMING_REPORT="$(if $(filter unit-% integration-%,$(LEG)),$(BUILD)/test-reports/$(firstword $(subst -, ,$(LEG))).xml,)" $(PY) tests/timing.py leg "$(LEG)" $(MAKE) --no-print-directory _full-check-leg LEG="$(LEG)"

_full-check-leg:
	@case ' $(FULL_LEGS) ' in *' $(LEG) '*) [ -n '$(LEG)' ] ;; *) false ;; esac || { echo "make full-check-leg: LEG is one of $(FULL_LEGS), not '$(LEG)'" >&2; exit 2; }
	$(if $(filter unit-% integration-%,$(LEG)),@test -s .test_durations || { echo 'make full-check-leg: run make test-durations first' >&2; exit 2; },@true)
	@$(PY) -c 'import coverage' 2>/dev/null || { echo 'make full-check-leg needs coverage: pip install -r requirements-dev.txt -c constraints.txt' >&2; exit 2; }
	$(PY) -m coverage erase
ifeq ($(filter unit-%,$(LEG)),)
	@echo '== the demo, built'
ifeq ($(CI):$(PREBUILT),true:1)
	@test -f "$(GRAPH)" || { echo "make full-check-leg: the prebuilt graph is missing" >&2; exit 2; }
else
	$(MAKE) --no-print-directory graph pipeline
endif
endif
ifneq ($(filter unit-%,$(LEG)),)
	@echo '== unit lane, shard $(patsubst unit-%,%,$(LEG))'
	$(MAKE) --no-print-directory test-unit SHARD=$(patsubst unit-%,%,$(LEG))
else ifneq ($(filter integration-%,$(LEG)),)
	@echo '== integration lane, Ladybug, shard $(patsubst integration-%,%,$(LEG))'
	$(MAKE) --no-print-directory test-integration SHARD=$(patsubst integration-%,%,$(LEG))
else ifeq ($(LEG),parity-margin)
	@echo '== parity and margin'
	$(MAKE) --no-print-directory parity
	$(MAKE) --no-print-directory margin REPEATS=1
else ifneq ($(filter e2e-%,$(LEG)),)
	@echo '== E2E lane, part $(patsubst e2e-%,%,$(LEG))'
	$(MAKE) --no-print-directory test-e2e PART=$(patsubst e2e-%,%,$(LEG))
endif
ifneq ($(LEG),parity-margin)
	@if ls .coverage.* >/dev/null 2>&1; then $(PY) -m coverage combine --append -q; fi
	@test -f .coverage || { echo 'make full-check-leg: the $(LEG) leg left no coverage data' >&2; exit 1; }
endif

full-check:
	@echo '== the demo, built'
	$(MAKE) --no-print-directory graph pipeline
	@echo '== unit lane'
	$(MAKE) --no-print-directory test-unit
	@echo '== integration lane, Ladybug'
	$(MAKE) --no-print-directory test-integration
	@echo '== parity and margin'
	$(MAKE) --no-print-directory parity
	$(MAKE) --no-print-directory margin REPEATS=1
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
