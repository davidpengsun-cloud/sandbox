---
title: "Data dictionary: Claims"
kind: data_dictionary
unit: claims
owner: Claims Data Steward
version: "2.1"
published_at: "2025-03-01"
reviewed_at: "2025-05-20"

---
The Claims warehouse holds claimants, claims and the payments that settle them. A claim is open until its `closed_on` is set. `payment.paid_usd` is indemnity paid, in USD, on the day in `payment.paid_on`; reserves and expenses are not here.

`claim.policy_ref` is the policy number as the claimant reported it. It is never checked against Commercial Underwriting's policies, so it is no key: the warehouse does not join to Commercial Underwriting's.

A claimant's contact address is personal data and is restricted (TMC-PRIV-01). Notices of loss go to the first-notice mailbox, fnol@thornbury.example, which is a team's, not a person's.
