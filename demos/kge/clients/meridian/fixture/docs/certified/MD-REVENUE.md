---
title: 'Metric Definition: Revenue'
kind: metric_definition
owner: Group Finance Data Steward
version: '1.3'
published_at: '2024-11-15'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** Income earned from customers in a period, before costs. Each business unit reports it at its own grain, currency and calendar; see the implementation metadata.

Revenue is certified in four warehouses and is **not summable across them** without Finance's reconciliation. Retail and SME report monthly, in USD, on the calendar year. Corporate reports quarterly in EUR, converted to USD at the period-end rate, on a fiscal year starting 1 April. Wealth reports quarterly in USD on the same fiscal year. An enterprise revenue figure is produced by Group Finance each quarter and nowhere else.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Retail Banking | monthly_revenue | total_revenue_usd | month | USD | calendar |
| SME Banking | monthly_client_revenue | revenue_usd | month | USD | calendar |
| Corporate & Institutional | quarterly_revenue | revenue_usd_converted | quarter | USD | fiscal_april |
| Wealth Management | QTR_FEE_REV_FACT_1 | REV_AM | quarter | USD | fiscal_april |
