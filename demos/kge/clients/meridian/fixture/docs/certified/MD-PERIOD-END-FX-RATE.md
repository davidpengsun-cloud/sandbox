---
title: 'Metric Definition: Period-End FX Rate'
kind: metric_definition
owner: Group Finance Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2024-12-02'
planted: []
---
**Definition.** The exchange rate on the last day of the period, used to convert Corporate revenue to USD.

The EUR to USD rate on the last day of the fiscal quarter, as recorded in `quarterly_revenue.fx_rate_period_end`. Average-rate conversion is not used for revenue.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Corporate & Institutional | quarterly_revenue | fx_rate_period_end | n/a | n/a | n/a |
