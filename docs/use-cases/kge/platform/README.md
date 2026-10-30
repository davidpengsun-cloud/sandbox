# KGE platform design drafts

**Status: drafts for discussion, October 2026.** Nothing here is a use-case scenario or a build commitment. These files design a domain-agnostic text-to-SQL and knowledge graph platform, generalised from the [Meridian demo](../../../../demos/kge/README.md). They make no code or demo changes.

The design splits the work three ways: a platform repository (runtime services and a compiler), a domain repository (the pack, authored as data by the business team), and a compiled graph model loaded into a graph store.

How the split is laid out per client, as the demo builds it for issue #39 (the platform's files, each client's pack in `clients/<client>/`, the profiles registry in deployment config, and each client's compiled artifacts, graph store and runtime overlay in folders of its own), is in [graph-model-spec.md §8, profiles and layout](graph-model-spec.md#8-profiles-and-layout).

How a new client is onboarded, from an empty folder to a released graph, and how a later pack edit goes through the same gate, is the checklist in [pack-contract.md §7, onboarding](pack-contract.md#7-onboarding), as the demo builds it for issue #40.

| File | What it is |
|---|---|
| [`pack-contract.md`](pack-contract.md) | What a valid, compilable pack must contain: sections, validation rules (structural, compile, semantic), the conflicts with today's demo structure, and the onboarding checklist. Draft v0.4 |
| [`pack.example.yaml`](pack.example.yaml) | A compilable pack skeleton populated with an excerpt of the Meridian estate |
| [`graph-model-spec.md`](graph-model-spec.md) | The standalone graph model: platform meta-model, pack extensions, layers, readiness and tiers, compile flow, and the per-client profiles and layout. Draft v0.3 |
| [`graph-model.example.yaml`](graph-model.example.yaml) | The compiled model the Meridian pack would produce, with an illustrative runtime overlay |
| [`runtime-pipeline.md`](runtime-pipeline.md) | The six runtime stages mapped onto the demo code, where the gates sit, a stage interface contract, and the A/B as built in the demo. Draft v0.2 |
| [`implementation-plan.md`](implementation-plan.md) | Phased plan, A/B experiment design, query set, scoring, risks and open decisions, with what the demo now builds. Draft v0.4 |

Related: the [AI readiness certification brief](../ai-readiness-certification.md) and its [logical view](../ai-readiness-certification-logical.md), and the [governance architecture brief](../governance_kg_architecture.md).
