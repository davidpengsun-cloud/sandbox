# sandbox

A scratch repository for experiments and prototypes.

## KGE demo

[`demos/kge/`](demos/kge/) is a clean copy of the knowledge-graph demo and its discovery portal ([`demos/kge/portal/`](demos/kge/portal/)). It was taken unchanged from `davidpengsun-cloud/fde` at commit `e59f878`, the commit fde released as KGE kit 0.9.19 (tag `kge-kit-v0.9.19`). That release brings:

- the SAGE wealth data, with Meridian's pack at 1.5.0;
- the portal's Explore;
- Ask always on;
- the portal on Red Hat's signed UBI Node base.

Every file under `demos/kge/` is byte-identical to that commit; nothing in it has been edited. [`kit/kge-0.9.19/`](kit/kge-0.9.19/) holds that release's published files, with its prebuilt images. Start with its README.

To run the demo locally, start with [`demos/kge/README.md`](demos/kge/README.md). It covers the `kge` command (Python only, no `make` needed), the portal, the release kit and the Terraform (`demos/kge/infra/`). To deploy on Docker Compose or Google Cloud, use the kit and its `docs/INSTALL.md`. `demos/kge/kit/` holds what a kit is built from.

The demo's tests also read a few files from outside `demos/kge/`. These are copied here, byte-identical to the same commit:

- `docs/use-cases/kge/platform/`, the platform docs that the doc tests check;
- `tools/check_disclosure.py`, `tools/disclosure-allow.txt`, `tools/check_no_comments.py` and `tools/check_prose.py`.

A few tests also read the source repository's GitHub workflows and agent skills. Those are not copied, because workflows at this repository's root would run here, so those tests do not pass in this copy.
