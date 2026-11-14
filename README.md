# sandbox

A scratch repository for experiments and prototypes.

## KGE demo

[`demos/kge/`](demos/kge/) is a clean copy of the knowledge-graph demo and its portal ([`demos/kge/portal/`](demos/kge/portal/)). It is KGE kit 0.12.6's code. 0.12.6 changes no behaviour: the KGE and portal images no longer carry the Kerberos libraries, so their known security findings leave the images and the image scan passes again.

0.12.5 made Context's **Graph** and **Trace** tabs follow the question live:

- each stage shows as it starts and, with its time, as it ends; the stage under way, and Runtime while its query runs, show as running;
- each graph node and edge arrives once, a later change as its own event, and a long question's later passes send only what changed;
- a background question's first reply streams at once, so the tabs fill without waiting for the answer.

Answers, figures and final traces are unchanged.

0.12.4 reworked Context's Ask screen:

- **a pinned header:** the title and subtitle stay at the top, with an **Examples ▾** dropdown beside them instead of examples in the conversation. Two portal settings, `PORTAL_CONTEXT_ASK_TITLE` and `PORTAL_CONTEXT_ASK_SUBTITLE`, change the title and subtitle; unset, they read as before;
- **one reading width and one left edge** for every answer, with nothing scrolling sideways down to phone width;
- **Behind the answer is always shown,** with one title, one Copy and one way to start a new conversation; question bubbles hold just the question;
- **readable figures:** amounts with two decimals, right-aligned, and readable column and period names.

0.12.3 changed no behaviour: inside the API, the Ask service's routes now handle HTTP alone, and its streamed and background answers share one event recorder.

0.12.2 added one change to 0.12.1:

- **the portal's catalogue agent sees more of the catalogue:** each asset's type (table, view, document, connector), table, path, format, freshness (last refresh, refresh cadence, last quality run), badges, lineage and classification, and each field's quality rules, data type and PII flag. It sends only the assets that best match the question, within a fixed size, and a date or number in an answer must come from the item it cites.

0.12.1 added one change to 0.12.0:

- **portal search finds a table by its ID or name:** typing a product's exact ID (`sales.orders`) or table name (`orders`) returns those products alone, every product sharing that table name included, and a product whose name equals the query ranks first.

Since 0.11.37, 0.12.0 brought:

- **the portal's Ask falls back in one order, KGE, then a catalogue agent, then the catalogue rules:** when KGE is unavailable, the portal's agent answers from the catalogue a person may read (owners, fields, what a field means, which product holds what), cites what it used and never gives a figure. It needs a model id (`PORTAL_ASK_MODEL`); without one, the rules answer, matching product names loosely. An admin can steer the agent from the Admin page;
- **owner answers use the page's labels,** "Data domain owner" and "Data steward", at every layer;
- **Search opens its results page at once,** and searches once;
- **the Data quality tab lists rules first,** and `PORTAL_DQ_FILTERS=off` hides its four filter buttons; wrapped text in the table grids keeps normal line spacing;
- **long questions answer in the background:** a question whose BigQuery job runs for minutes keeps running, and any of 5–10 API instances can report its progress and answer;
- **the rules path reads every reference question shape:** breakdowns by month and their combinations, month ranges, comparisons such as certified against current, top and bottom N, period against period, ratios and shares, inflows and outflows;
- **table rows in documents are kept** as passages on every onboarding path.

[`kit/kge-0.12.6/`](kit/kge-0.12.6/) holds the 0.12.6 release kit, with its prebuilt images. Start with its README.

To run the demo locally, start with [`demos/kge/README.md`](demos/kge/README.md). It covers the `kge` command (Python only, no `make` needed), the portal, the release kit and the Terraform (`demos/kge/infra/`). To deploy on Docker Compose or Google Cloud, use the kit and its `docs/INSTALL.md`. `demos/kge/kit/` holds what a kit is built from.

### Testing locally

The whole local gate is `make full-check`, run from `demos/kge/`, as [`demos/kge/README.md`](demos/kge/README.md) describes under Tests: the unit and integration lanes, parity, the margin, the walkthrough and the coverage check. The doc tests read `docs/use-cases/kge/platform/` and `tools/`, which are kept here.

A few unit tests check CI workflow and repository files that are not part of this repository, so they fail here: `tests/unit/test_kit.py`, `test_image_scan.py`, `test_checks.py` and `test_pre_commit.py`, and one test each in `test_lanes.py`, `test_client_id.py`, `test_public_api_docs.py` and `test_baselines.py`. Every other test runs.
