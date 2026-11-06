---
title: SME Banking Data Dictionary
kind: data_dictionary
unit: sme
owner: SME Banking Data Office
version: '3.2'
published_at: '2025-02-01'
reviewed_at: '2025-06-02'
planted: []
---
Certified data dictionary for the SME Banking warehouse. The customer-like key in this warehouse is `client_no`. It does not join to any other business unit's warehouse.

## calendar_month

| Column | Type | Description |
|---|---|---|
| month_start | DATE | First day of the calendar month |
| calendar_year | INTEGER | Calendar year |
| calendar_quarter | VARCHAR | Calendar quarter |

## relationship_manager

| Column | Type | Description |
|---|---|---|
| rm_id | INTEGER | Relationship manager identifier |
| rm_name | VARCHAR | Relationship manager name |
| team_code | VARCHAR | Coverage team |

## client

| Column | Type | Description |
|---|---|---|
| client_no | INTEGER | SME client number |
| legal_name | VARCHAR | Registered legal name |
| registration_no | VARCHAR | Company registration number |
| industry_code | VARCHAR | Industry classification code |
| rm_id | INTEGER | Covering relationship manager |
| onboarded_on | DATE | Onboarding date |

Joins: `rm_id` references `relationship_manager.rm_id`

## facility

| Column | Type | Description |
|---|---|---|
| facility_id | INTEGER | Facility identifier |
| client_no | INTEGER | Borrowing client |
| facility_type | VARCHAR | TERM_LOAN, OVERDRAFT, INVOICE_FINANCE |
| limit_amount | DECIMAL(14,2) | Approved limit |
| currency_code | VARCHAR | Facility currency, USD |
| start_on | DATE | Facility start |
| end_on | DATE | Facility maturity |

Joins: `client_no` references `client.client_no`

## drawdown

| Column | Type | Description |
|---|---|---|
| drawdown_id | INTEGER | Drawdown identifier |
| facility_id | INTEGER | Facility drawn |
| drawn_on | DATE | Drawdown date |
| amount | DECIMAL(14,2) | Amount drawn |

Joins: `facility_id` references `facility.facility_id`

## repayment

| Column | Type | Description |
|---|---|---|
| repayment_id | INTEGER | Repayment identifier |
| facility_id | INTEGER | Facility repaid |
| paid_on | DATE | Payment date |
| amount | DECIMAL(14,2) | Amount paid |
| days_past_due | INTEGER | Days past the scheduled date, zero when on time |

Joins: `facility_id` references `facility.facility_id`

## monthly_client_revenue

| Column | Type | Description |
|---|---|---|
| rev_id | INTEGER | Row identifier |
| client_no | INTEGER | Client |
| month_start | DATE | Calendar month |
| nii | DECIMAL(14,2) | Net interest income, USD |
| fees | DECIMAL(14,2) | Fee income, USD |
| revenue_usd | DECIMAL(14,2) | NII plus fees, USD, calendar month |

Joins: `client_no` references `client.client_no`; `month_start` references `calendar_month.month_start`

## client_activity

| Column | Type | Description |
|---|---|---|
| activity_id | INTEGER | Row identifier |
| client_no | INTEGER | Client |
| month_start | DATE | Calendar month |
| payments_count | INTEGER | Outbound payments in the month |
| active_flag | BOOLEAN | True where payments_count is at least one |

Joins: `client_no` references `client.client_no`; `month_start` references `calendar_month.month_start`

## covenant

| Column | Type | Description |
|---|---|---|
| covenant_id | INTEGER | Covenant identifier |
| facility_id | INTEGER | Facility the covenant attaches to |
| covenant_type | VARCHAR | LEVERAGE, INTEREST_COVER, MIN_LIQUIDITY |
| threshold | DECIMAL(10,4) | Covenant threshold |
| tested_on | DATE | Last test date |
| breached_flag | BOOLEAN | True where the last test breached |

Joins: `facility_id` references `facility.facility_id`
