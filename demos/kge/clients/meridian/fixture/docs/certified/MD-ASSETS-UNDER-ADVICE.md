---
title: 'Metric Definition: Assets Under Advice'
kind: metric_definition
owner: Wealth Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** Market value of client assets on which the firm gives advice, at quarter end, USD.

Measured at fiscal quarter end from `assets_under_advice.aua_usd`. Includes advisory and discretionary mandates. Excludes assets held away.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Wealth Management | AUA_FACT_1 | AUA_AM | quarter_end | USD | fiscal_april |
