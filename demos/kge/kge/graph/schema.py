"""The graph schema, in the property-graph terms the repository uses.

Physical elements (units, tables, columns) are mirrored from the warehouses and
regenerable. Terms and certified mappings are human. The no-join fact between
units is curated once and never inferred. Each document carries what upstream
AI certification landed for it (ai_ready, tier and the cert_ fields, from the
landed release's certification.json); the graph reads that and never writes it.
One Release node names the landed release the graph was loaded from.
Chunks, mentions and tags arrive from the pipeline; a human correction is a Tag
at confidence 1.0 with a SUPERSEDES edge to the tag it retires. Embeddings sit
on the chunk, and a chunk the gate prohibits has no embedding at all.

The same DDL, give or take type names, declares the graph over BigQuery tables
or in Spanner Graph. That portability is the point of writing it in GQL.

The types are the platform's meta-model (meta-model.yaml), and DDL is built
from it, in the Cypher dialect named kuzu, which Ladybug loads: each type under
the name the graph has always had, its properties in the meta-model's order.
The builder is the compiler's (kge/compiler/emit.py), which writes the same DDL into
a compiled artifact's ddl/kuzu.cypher; the loader runs the artifact's
(kge/graph/load.py), which fills the newer types too (Measure, Calendar, Alias,
Policy, Term's id slug, and the GraphModel the graph was compiled as). The DDL
as it was before issue #39 is frozen in kge/graph/legacy.py, beside the loader it
replaced: the run trace's legacy model digest is taken over it.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from kge.pipeline.settings import ROOT
from kge.compiler import emit
from kge.adapters.model import DIM as EMBED_DIM

META_MODEL = ROOT / "meta-model.yaml"
prop, ends, ddl = emit.prop, emit.ends, emit.ddl


def meta_model(path: Path = META_MODEL) -> dict:
    """The meta-model, as written."""
    return yaml.safe_load(Path(path).read_text())


DDL = ddl(meta_model(), EMBED_DIM)
