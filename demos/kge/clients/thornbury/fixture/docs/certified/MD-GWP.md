---
title: "Metric definition: Gross Written Premium"
kind: metric_definition
unit: underwriting
owner: Underwriting Data Steward
version: "1.0"
published_at: "2025-01-15"
reviewed_at: "2025-06-02"

---
**Definition.** Gross Written Premium is the premium written on policies in a period, before reinsurance and before any return of premium. It is booked on the day the premium is written: at binding, and again for each endorsement that adds premium.

It is certified in one warehouse, Commercial Underwriting, on `premium.written_premium_usd`, by the day it was booked (`premium.booked_on`), in USD, on the calendar year. A month's figure is the sum of the premium booked in it.

Gross Written Premium is not earned premium, and it is never added to a claims figure: the two warehouses share no key, and a loss ratio is the actuaries' product, not a sum.
