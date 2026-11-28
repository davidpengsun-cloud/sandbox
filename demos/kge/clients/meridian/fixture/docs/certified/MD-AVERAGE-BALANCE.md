---
title: 'Metric Definition: Average Balance'
kind: metric_definition
owner: Group Finance Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** Mean end-of-day ledger balance over the calendar month.

Mean of `daily_balance.ledger_balance` over the calendar month, per account, then summed or averaged as the question requires.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Retail Banking | daily_balance | ledger_balance | day | USD | calendar |
