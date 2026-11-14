---
title: 'Metric Definition: Active Customer'
kind: metric_definition
owner: Customer Data Steward
version: '2.0'
published_at: '2025-01-01'
reviewed_at: '2025-05-15'
planted: []
---
**Definition.** A customer with at least one customer-initiated transaction in the calendar month. Logins alone do not count. Version 2, effective 1 January 2025.

Version 2, effective 1 January 2025. A customer is active in a calendar month when `customer_activity.txn_count` (Retail) or `client_activity.payments_count` (SME) is at least one. Digital logins do not make a customer active. Version 1 counted any login in the trailing 90 days and is withdrawn.

## Certified implementations

| Unit | Table | Column | Grain | Currency | Calendar |
|---|---|---|---|---|---|
| Retail Banking | customer_activity | is_active_flag | n/a | n/a | n/a |
| SME Banking | client_activity | active_flag | n/a | n/a | n/a |
