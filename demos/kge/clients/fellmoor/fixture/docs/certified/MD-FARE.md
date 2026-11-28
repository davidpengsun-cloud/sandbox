---
title: "Metric definition: Fare Revenue"
kind: metric_definition
unit: ticketing
owner: Fares Data Steward
version: "1.0"
published_at: "2025-01-10"
reviewed_at: "2025-05-28"

---
**Definition.** Fare Revenue is the fares charged for journeys in a period, after any concession and before any refund. It is booked on the day the card is tapped on board.

It is certified in one warehouse, Fares and Ticketing, on `journey.fare_usd`, by the day of the tap (`journey.tapped_on`), in USD, on the calendar year. A month's figure is the sum of the fares tapped in it.

Fare Revenue is never added to a fleet figure, and never divided by one: the two warehouses share no key, and a farebox recovery ratio is no certified measure.
