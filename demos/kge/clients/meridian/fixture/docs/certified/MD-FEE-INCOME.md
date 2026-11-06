---
title: 'Metric Definition: Fee Income'
kind: metric_definition
owner: Group Finance Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** Fees and commissions charged to customers in the period.

Fees and commissions. Certified in Retail (`monthly_revenue.fee_income`) and SME (`monthly_client_revenue.fees`), both monthly, USD, calendar.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Retail Banking | monthly_revenue | fee_income | month | USD | calendar |
| SME Banking | monthly_client_revenue | fees | month | USD | calendar |
