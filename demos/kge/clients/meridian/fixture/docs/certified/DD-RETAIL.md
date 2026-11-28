---
title: Retail Banking Data Dictionary
kind: data_dictionary
unit: retail
owner: Retail Banking Data Office
version: '3.2'
published_at: '2025-02-01'
reviewed_at: '2025-06-02'
planted: []
---
Certified data dictionary for the Retail Banking warehouse. The customer-like key in this warehouse is `customer_id`. It joins another unit only by the certified client cross-reference.

## calendar_month

| Column | Type | Description |
|---|---|---|
| month_start | DATE | First day of the calendar month |
| calendar_year | INTEGER | Calendar year |
| calendar_quarter | VARCHAR | Calendar quarter, e.g. 2025-Q2 |

## branch

| Column | Type | Description |
|---|---|---|
| branch_id | INTEGER | Branch identifier |
| branch_name | VARCHAR | Branch trading name |
| region_code | VARCHAR | Sales region code |
| opened_on | DATE | Date the branch opened |

## product

| Column | Type | Description |
|---|---|---|
| product_id | INTEGER | Product identifier |
| product_name | VARCHAR | Product marketing name |
| product_family | VARCHAR | Current account, savings, lending, card |
| rate_bps | INTEGER | Headline rate in basis points |
| fee_monthly | DECIMAL(10,2) | Monthly account fee in USD |

## customer

| Column | Type | Description |
|---|---|---|
| customer_id | INTEGER | Retail customer identifier |
| first_name | VARCHAR | Customer first name |
| last_name | VARCHAR | Customer last name |
| date_of_birth | DATE | Customer date of birth |
| email | VARCHAR | Customer email address |
| segment_code | VARCHAR | Marketing segment code |
| branch_id | INTEGER | Home branch |
| opened_on | DATE | Date the relationship opened |

Joins: `branch_id` references `branch.branch_id`

## account

| Column | Type | Description |
|---|---|---|
| account_id | INTEGER | Account identifier |
| customer_id | INTEGER | Owning customer |
| product_id | INTEGER | Product held |
| status_code | VARCHAR | OPEN, DORMANT or CLOSED |
| opened_on | DATE | Date opened |
| closed_on | DATE | Date closed, null while open |
| currency_code | VARCHAR | Account currency, always USD in retail |

Joins: `customer_id` references `customer.customer_id`; `product_id` references `product.product_id`

## transaction

| Column | Type | Description |
|---|---|---|
| txn_id | BIGINT | Transaction identifier |
| account_id | INTEGER | Account posted to |
| posted_on | DATE | Posting date |
| amount | DECIMAL(14,2) | Signed amount in account currency |
| txn_type | VARCHAR | DEBIT or CREDIT |
| channel_code | VARCHAR | BRANCH, ATM, ONLINE, MOBILE |
| merchant_category | VARCHAR | Merchant category code where applicable |
| counterparty_bank | VARCHAR | Institution on the other side of a transfer; MERIDIAN for an account held here |
| counterparty_account | BIGINT | Account number on the other side of a transfer, at that institution |

Joins: `account_id` references `account.account_id`

## monthly_revenue

| Column | Type | Description |
|---|---|---|
| revenue_id | INTEGER | Row identifier |
| account_id | INTEGER | Account the revenue accrues to |
| month_start | DATE | Calendar month |
| interest_income | DECIMAL(14,2) | Net interest income for the month, USD |
| fee_income | DECIMAL(14,2) | Fee income for the month, USD |
| total_revenue_usd | DECIMAL(14,2) | Interest plus fees, USD, calendar month |

Joins: `account_id` references `account.account_id`; `month_start` references `calendar_month.month_start`

## daily_balance

| Column | Type | Description |
|---|---|---|
| balance_id | BIGINT | Row identifier |
| account_id | INTEGER | Account |
| as_of | DATE | Balance date |
| ledger_balance | DECIMAL(14,2) | End-of-day ledger balance |
| available_balance | DECIMAL(14,2) | End-of-day available balance |

Joins: `account_id` references `account.account_id`

## customer_activity

| Column | Type | Description |
|---|---|---|
| activity_id | INTEGER | Row identifier |
| customer_id | INTEGER | Customer |
| month_start | DATE | Calendar month |
| login_count | INTEGER | Digital logins in the month |
| txn_count | INTEGER | Customer-initiated transactions in the month |
| is_active_flag | BOOLEAN | True where txn_count is at least one in the month |

Joins: `customer_id` references `customer.customer_id`; `month_start` references `calendar_month.month_start`

## complaint

| Column | Type | Description |
|---|---|---|
| complaint_id | INTEGER | Complaint identifier |
| customer_id | INTEGER | Complainant |
| raised_on | DATE | Date raised |
| category | VARCHAR | Complaint category |
| resolved_on | DATE | Date resolved, null while open |

Joins: `customer_id` references `customer.customer_id`

Transfer legs: `transaction.counterparty_bank` (MERIDIAN) and `transaction.counterparty_account`. Cross-reference: `client_xref`, `customer_id` to `household_id`.
