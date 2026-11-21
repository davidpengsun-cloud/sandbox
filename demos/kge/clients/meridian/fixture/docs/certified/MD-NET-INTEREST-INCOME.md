---
title: 'Metric Definition: Net Interest Income'
kind: metric_definition
owner: Group Finance Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** Interest earned less interest paid, in the reporting currency, for the period.

Interest earned less interest paid. Certified in Retail (`monthly_revenue.interest_income`) and SME (`monthly_client_revenue.nii`), both monthly, USD, calendar.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Retail Banking | monthly_revenue | interest_income | month | USD | calendar |
| SME Banking | monthly_client_revenue | nii | month | USD | calendar |
