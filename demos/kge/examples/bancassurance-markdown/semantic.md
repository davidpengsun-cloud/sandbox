# Bancassurance semantic model

The `premium` table holds written premiums. Text outside the documented table
shape is prose: a model may propose from it, while the rules skip it.

## premium

| column | type | description | key |
|---|---|---|---|
| premium_id | BIGINT | Premium identifier | primary |
| accounting_date | DATE | Accounting date | |
| premium_amount | DECIMAL(18,2) | Written premium | |

`written_premium` is the sum of `premium_amount`, in GBP, by `accounting_date`, counting positive amounts only: refunds are excluded. It is also called gross written premium.
