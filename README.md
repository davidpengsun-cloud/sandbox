# sandbox

A scratch repository for experiments and prototypes.

## KGE demo

[`demos/kge/`](demos/kge/) is a clean copy of the knowledge-graph demo and its portal ([`demos/kge/portal/`](demos/kge/portal/)). It was taken unchanged from `davidpengsun-cloud/fde` at commit `9868955`, the commit fde released as KGE kit 0.11.8 (tag `kge-kit-v0.11.8`). Since 0.11.3, the previous copy here, it brings:

- the Discover home subtitle from a setting, defaulting to "Search curated data assets, see how fresh and trustworthy each one is, and request access from the people who own it.";
- asset pages by object type: documents and connectors (API/MCP, shown as Connector) without the stand-in table's records, storage, columns, schema or lineage, and a refresh cadence, N/A for views;
- a description on each DQ rule, rules without a fail percentage, several rules per column, and a PII column (Yes or blank) in place of column classification;
- each asset's path, data format, APM ID and schema supplied through the pack's catalogue, falling back to the derived values;
- Ask on an asset's page answering from readable certified sources, naming where the answer came from, with only access or an ungovernable figure stopping it;
- a join question on a product page listing the declared joins that touch it.

Every file under `demos/kge/` is byte-identical to that commit; nothing in it has been edited. [`kit/kge-0.11.8/`](kit/kge-0.11.8/) holds that release's published files, with its prebuilt images. Start with its README.

To run the demo locally, start with [`demos/kge/README.md`](demos/kge/README.md). It covers the `kge` command (Python only, no `make` needed), the portal, the release kit and the Terraform (`demos/kge/infra/`). To deploy on Docker Compose or Google Cloud, use the kit and its `docs/INSTALL.md`. `demos/kge/kit/` holds what a kit is built from.

The demo's tests also read a few files from outside `demos/kge/`. These are copied here, byte-identical to the same commit:

- `docs/use-cases/kge/platform/`, the platform docs that the doc tests check;
- `tools/check_disclosure.py`, `tools/disclosure-allow.txt`, `tools/check_no_comments.py` and `tools/check_prose.py`.

A few tests also read the source repository's GitHub workflows and agent skills. Those are not copied, because workflows at this repository's root would run here, so those tests do not pass in this copy.
