---
title: "Data dictionary: Fares and Ticketing"
kind: data_dictionary
unit: ticketing
owner: Fares Data Steward
version: "1.2"
published_at: "2025-02-03"
reviewed_at: "2025-06-10"

---
The Fares and Ticketing warehouse holds smartcards and the journeys tapped with them. `journey.fare_usd` is the fare charged, in USD, on the day in `journey.tapped_on`; a concession fare is charged at the concession rate, so no discount is held apart.

`journey.vehicle_ref` is the vehicle number as the driver keyed it at sign-on, with or without its fleet prefix. It is never checked against Fleet Engineering's register, so it is no key: the warehouse does not join to Fleet Engineering's.

A cardholder's contact address is personal data and is restricted (FRT-PRIV-01).
