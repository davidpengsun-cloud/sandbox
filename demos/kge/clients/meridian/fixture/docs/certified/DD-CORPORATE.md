---
title: Corporate & Institutional Data Dictionary
kind: data_dictionary
unit: corporate
owner: Corporate & Institutional Data Office
version: '3.2'
published_at: '2025-02-01'
reviewed_at: '2025-06-02'
planted: []
---
Certified data dictionary for the Corporate & Institutional warehouse. The customer-like key in this warehouse is `counterparty_ref`. It does not join to any other business unit's warehouse.

## fiscal_calendar

| Column | Type | Description |
|---|---|---|
| fiscal_quarter | VARCHAR | Fiscal quarter label, e.g. FY25-Q1 |
| fiscal_year | INTEGER | Fiscal year, starting 1 April |
| quarter_start | DATE | First day of the fiscal quarter |
| quarter_end | DATE | Last day of the fiscal quarter |

## counterparty

| Column | Type | Description |
|---|---|---|
| counterparty_ref | VARCHAR | Counterparty reference |
| counterparty_name | VARCHAR | Legal entity name |
| lei_code | VARCHAR | Legal entity identifier |
| country_code | VARCHAR | Country of incorporation |
| sector_code | VARCHAR | Sector classification |
| onboarded_on | DATE | Onboarding date |

## contact

| Column | Type | Description |
|---|---|---|
| contact_id | INTEGER | Contact identifier |
| counterparty_ref | VARCHAR | Counterparty the contact belongs to |
| customer_id | INTEGER | CRM contact number. Not the retail customer identifier, despite the name |
| contact_name | VARCHAR | Contact name |
| contact_email | VARCHAR | Contact email |
| role_code | VARCHAR | TREASURER, CFO, OPERATIONS |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`

## deal

| Column | Type | Description |
|---|---|---|
| deal_id | INTEGER | Deal identifier |
| counterparty_ref | VARCHAR | Counterparty |
| product_code | VARCHAR | FX_FWD, IRS, LOAN, BOND |
| notional_eur | DECIMAL(16,2) | Notional in EUR |
| trade_on | DATE | Trade date |
| maturity_on | DATE | Maturity date |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`

## exposure

| Column | Type | Description |
|---|---|---|
| exposure_id | INTEGER | Row identifier |
| counterparty_ref | VARCHAR | Counterparty |
| as_of | DATE | Measurement date |
| ead_eur | DECIMAL(16,2) | Exposure at default, EUR |
| pd_bps | INTEGER | Probability of default, basis points |
| lgd_pct | DECIMAL(5,2) | Loss given default, per cent |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`

## quarterly_revenue

| Column | Type | Description |
|---|---|---|
| qrev_id | INTEGER | Row identifier |
| counterparty_ref | VARCHAR | Counterparty |
| fiscal_quarter | VARCHAR | Fiscal quarter |
| revenue_eur | DECIMAL(16,2) | Revenue in EUR for the fiscal quarter |
| fx_rate_period_end | DECIMAL(10,6) | EUR to USD rate at quarter end |
| revenue_usd_converted | DECIMAL(16,2) | Revenue converted to USD at the period-end rate |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`; `fiscal_quarter` references `fiscal_calendar.fiscal_quarter`

## limit_utilisation

| Column | Type | Description |
|---|---|---|
| util_id | INTEGER | Row identifier |
| counterparty_ref | VARCHAR | Counterparty |
| as_of | DATE | Measurement date |
| limit_eur | DECIMAL(16,2) | Approved limit, EUR |
| utilised_eur | DECIMAL(16,2) | Utilised amount, EUR |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`

## rating

| Column | Type | Description |
|---|---|---|
| rating_id | INTEGER | Row identifier |
| counterparty_ref | VARCHAR | Counterparty |
| rated_on | DATE | Rating date |
| internal_grade | VARCHAR | Internal rating grade |
| agency_grade | VARCHAR | External agency grade where available |

Joins: `counterparty_ref` references `counterparty.counterparty_ref`
