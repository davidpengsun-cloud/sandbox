# Meridian live tagger results — 2 November 2026

Source: the owner’s live Vertex AI answer for issue #218. The run covered all
83 cleared Meridian chunks (44 citable, 39 retrieval-internal), against all 49
glossary terms. The privacy gate withheld four embed-prohibited chunks.
There were 92 reference mentions: 63 exact surface matches and 29 verified
semantic mentions. Settings: temperature 0, seed 7, maxOutputTokens 8192,
12 workers, VERTEX_REGION global, with KGE’s RFC 8259 JSON retry.

| Tagger / tier | Model | Wall time (12 workers) | Mentions found | Mention recall (92 references) | Surface recall (63 exact) | Mention wrong-find rate | Candidates proposed |
|---|---|---:|---:|---:|---:|---:|---|
| **`RuleTagger`** | `rules-v1` | `0.0s` | `63` | `68.5%` (`63/92`) | **`100.0%`** (`63/63`) | **`0.0%`** (`0` FP) | `1` (`Digital Engagement Score` in `EM-004#0`; misses `NOTE-001#1`, `DRAFT-001#0`, `DRAFT-004#0`) |
| **`low` tier** | `gemini-3.5-flash-lite` | `7.3s` | `59` | `58.7%` (`54/92`) | `68.3%` (`43/63` — drops `20` exact surface matches!) | `8.5%` (`5` FP) | **`82` proposed (`78` wrong finds = `95.1%` FP!)** — tags SQL column/table names in Data Dictionaries (`branch_id`, `customer_id`, `household_id`, `monthly_revenue`) as candidate terms, and needed JSON retry on `2` chunks (`DD-WEALTH#3`, `DRAFT-007#0`) |
| **`medium` tier** | `gemini-3.8-flash` | `91.9s` | `86` | **`93.5%`** (`86/92`) | **`90.5%`** (`57/63`) | **`0.0%`** (`0` FP) | **`5` proposed (`0` wrong finds)** — finds `Digital Engagement Score` in both `EM-004#0` and `NOTE-001#1`, plus `Total revenue across the bank` / `enterprise revenue` in `DRAFT-001#0`, `MD-REVENUE#1`, `PROC-RECON#0`; `0` JSON retries |
| **`high` tier** | `gemini-3.1-pro-preview` | `112.7s` | `111` | **`100.0%`** (`92/92`) | **`96.8%`** (`61/63`) | `17.1%` (`19` tangential FP) | `29` proposed (finds `Digital Engagement Score`, `Enterprise Revenue`, and `Rating Migration` in `DRAFT-004#0`, but also proposes `20+` broad concepts/columns like `Calendar Year`, `Portfolio`, `Advisor`, `ESCALATED`) |

Low’s candidate wrong-find rate was 95.1% (78 of 82). Medium’s was 0% (0 of 5).
High proposed more than 20 broad concepts or columns, including Calendar Year,
Portfolio, Advisor and ESCALATED; an exact wrong-candidate count was not supplied.

The owner concluded that medium meets the [agreed targets](../targets.json)
and low does not, selecting medium as the pilot default. High exceeds the
15% mention wrong-find limit. The answer did not supply candidate reference
denominators or candidate recall fractions; this record preserves the owner’s
conclusion without inventing those measurements.

This corpus differs from the small invented set beside this file. No raw
per-passage output or live answering A/B V01–V04 result accompanied the answer;
this record does not claim those gates passed. The evaluator reports its
answering A/B separately, as the [evaluation guide](../README.md#pilot-run-preparation)
describes. Changing how ModelTagger handles exact matches is issue #228.
