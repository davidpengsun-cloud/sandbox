---
title: 'Metric Definition: Net New Assets'
kind: metric_definition
owner: Wealth Data Steward
version: '1.0'
published_at: '2026-11-10'
reviewed_at: '2026-11-10'
planted: []
---
**Definition.** Money clients bring to the firm less money they take away, in USD, over released months: eligible flows only, with test accounts left out. A move between a client's own accounts is not new money.

Test accounts are identified by `ACCT_DIM2_1.TEST_ACCT_IN`. The NNA contexts use the account's domicile branch and household key, branch region and division columns, and the client-code rollup's segment levels. Branch and division come from the current branch row. Plain year to date starts in January; fiscal year to date uses the declared fiscal year.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Wealth Management | TRAN_FACT_1 | TRAN_AM | day | USD | fiscal_april |
