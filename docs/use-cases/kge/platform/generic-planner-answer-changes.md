# Generic Ask planner: answer changes

This inventory explains every answer #316 moved, each under the owner decision that approved it. The historical parity snapshot and the Agent seals are kept; no question or fixture figure was edited to make a result pass.

## Owner decisions

- The approved generic-planner design replaces model SQL with checked plans and a deterministic compiler, requires complete coverage, and authorises the Agent contract migration.
- The owner’s 6 November 2026 bug-fix decision (“This fixes bugs, so I am good”) covers the stated coverage, population, addition, no-join and declaration corrections below.
- Step 7b restores governed definition paraphrases to certified citations and approves the control-arm coverage and shared access guards.
- Earlier #39 Option B and #176 physical-schema changes remain separately attributed; they are not new #316 decisions.

## Parity: every stated field move

There are 131 #316 field moves and 20 earlier field moves, across 52 result/arm keys. A field move is not an additional question. The reference remains the unchanged 183-result historical snapshot.

### the #39 Option B update: Retail and Wealth join through the certified client cross-reference, so retail-wealth is no refused join; the graph preset refuses the other five pairs it refused before, as G021 expects

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G021:graph | joins_refused | ["retail-corporate","sme-corporate","corporate-wealth","retail-sme","retail-wealth","sme-wealth"] | ["retail-corporate","sme-corporate","corporate-wealth","retail-sme","sme-wealth"] |

### the #39 Option B update: Retail and Wealth join through the certified client cross-reference, so retail-wealth is no refused join; the graph preset refuses the other five pairs it refused before, so G030's expected retail-corporate, sme-corporate and sme-wealth still hold

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G030:graph | joins_refused | ["retail-corporate","sme-corporate","corporate-wealth","retail-sme","retail-wealth","sme-wealth"] | ["retail-corporate","sme-corporate","corporate-wealth","retail-sme","sme-wealth"] |

### pack 1.5.0 (#176): Wealth's tables hold physical names in SAGE's style (quarterly_fee_revenue is QTR_FEE_REV_FACT_1, its revenue_usd REV_AM; household is HSHLD_DIM_1), and the graph-off presets, which are handed no curated metadata and find a column by its name's words, no longer find Wealth's revenue, assets under advice or review columns by those words; the graph preset reads the business names from the pack and keeps every figure

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G017:catalog | ok | true | false |
| golden:G017:schema | behaviour | "figure" | "abstain" |
| golden:G017:schema | figure | 2506757.26 | null |
| golden:G017:schema | ok | true | false |
| golden:G018:catalog | ok | true | false |
| golden:G018:schema | behaviour | "figure" | "abstain" |
| golden:G018:schema | figure | 150.0 | null |
| golden:G020:schema | behaviour | "figure" | "abstain" |
| golden:G020:schema | figure | 14.0 | null |
| golden:G020:schema | ok | true | false |
| golden:G025:schema | joins_refused | [] | null |
| golden:G025:schema | parts | {"corporate":18669131.39,"wealth":2506757.26} | null |
| heldout:H005:catalog | ok | true | false |
| heldout:H005:schema | behaviour | "figure" | "abstain" |
| heldout:H005:schema | figure | 2506757.26 | null |
| heldout:H005:schema | ok | true | false |
| heldout:H008:schema | joins_refused | [] | null |
| heldout:H008:schema | parts | {"corporate":18669131.39,"wealth":2506757.26} | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Total retail revenue for June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G001:schema | behaviour | "figure" | "abstain" |
| golden:G001:schema | figure | 336.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. were active. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G003:schema | behaviour | "figure" | "abstain" |
| golden:G003:schema | figure | 0.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Average ledger balance across retail accounts in June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G005:schema | behaviour | "figure" | "abstain" |
| golden:G005:schema | figure | 1008.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. A predicate on sme.covenant.breached_flag has no declared role for every selected context. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G011:schema | behaviour | "figure" | "abstain" |
| golden:G011:schema | figure | 8.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Corporate revenue in USD for fiscal quarter FY26-Q1': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G013:schema | behaviour | "figure" | "abstain" |
| golden:G013:schema | figure | 18669131.39 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Total corporate credit exposure at 30 June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G014:schema | behaviour | "figure" | "abstain" |
| golden:G014:schema | figure | 40.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. A predicate on corporate.rating.internal_grade has no declared role for every selected context. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G016:schema | behaviour | "figure" | "abstain" |
| golden:G016:schema | figure | 8.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. missing compatible period binding. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G017:catalog | behaviour | "figure" | "abstain" |
| golden:G017:catalog | figure | 2506757.26 | null |
| golden:G018:catalog | behaviour | "figure" | "abstain" |
| golden:G018:catalog | figure | 1830296649.93 | null |
| golden:G020:catalog | behaviour | "figure" | "abstain" |
| golden:G020:catalog | figure | 14.0 | null |
| golden:G020:catalog | ok | true | false |
| golden:G025:catalog | behaviour | "sum_without_verdict" | "abstain" |
| golden:G025:catalog | figure | 22697136.69 | null |
| golden:G025:catalog | joins_refused | [] | null |
| golden:G025:catalog | parts | {"corporate":20190379.43,"wealth":2506757.26} | null |
| heldout:H005:catalog | behaviour | "figure" | "abstain" |
| heldout:H005:catalog | figure | 2506757.26 | null |
| heldout:H008:catalog | behaviour | "sum_without_verdict" | "abstain" |
| heldout:H008:catalog | figure | 22697136.69 | null |
| heldout:H008:catalog | joins_refused | [] | null |
| heldout:H008:catalog | parts | {"corporate":20190379.43,"wealth":2506757.26} | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. The schema calendar heuristic does not establish the requested quarter. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G021:catalog | behaviour | "sum_without_verdict" | "abstain" |
| golden:G021:catalog | figure | 24902512.7 | null |
| golden:G021:catalog | joins_refused | [] | null |
| golden:G021:catalog | parts | {"corporate":21478456.31,"retail":39983.72,"sme":953824.62,"wealth":2430248.05} | null |
| golden:G030:catalog | behaviour | "parts_with_reason" | "abstain" |
| golden:G030:catalog | joins_refused | [] | null |
| golden:G030:catalog | parts | {"corporate":null,"retail":40483.98,"sme":899746.55,"wealth":null} | null |
| golden:G042:catalog | behaviour | "sum_without_verdict" | "abstain" |
| golden:G042:catalog | figure | 24902512.7 | null |
| golden:G042:catalog | joins_refused | [] | null |
| golden:G042:catalog | ok | false | true |
| golden:G042:catalog | parts | {"corporate":21478456.31,"retail":39983.72,"sme":953824.62,"wealth":2430248.05} | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. The requested measure is not certified for every named source. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G021:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G021:schema | figure | 23387956.66 | null |
| golden:G021:schema | joins_refused | [] | null |
| golden:G021:schema | parts | {"corporate":20002867.99,"retail":1016.0,"sme":953824.62,"wealth":2430248.05} | null |
| golden:G022:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G022:schema | figure | 321466.65 | null |
| golden:G022:schema | joins_refused | [] | null |
| golden:G022:schema | parts | {"retail":336.0,"sme":321130.65} | null |
| golden:G026:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G026:schema | figure | 59.0 | null |
| golden:G026:schema | joins_refused | [] | null |
| golden:G026:schema | parts | {"retail":0.0,"sme":59.0} | null |
| golden:G027:schema | behaviour | "figure" | "abstain" |
| golden:G027:schema | figure | 32290.55 | null |
| golden:G030:schema | behaviour | "parts_with_reason" | "abstain" |
| golden:G030:schema | joins_refused | [] | null |
| golden:G030:schema | parts | {"corporate":null,"retail":1040.0,"sme":899746.55,"wealth":null} | null |
| golden:G042:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G042:schema | figure | 23387956.66 | null |
| golden:G042:schema | joins_refused | [] | null |
| golden:G042:schema | ok | false | true |
| golden:G042:schema | parts | {"corporate":20002867.99,"retail":1016.0,"sme":953824.62,"wealth":2430248.05} | null |
| heldout:H007:schema | behaviour | "sum_without_verdict" | "abstain" |
| heldout:H007:schema | figure | 321466.65 | null |
| heldout:H007:schema | joins_refused | [] | null |
| heldout:H007:schema | parts | {"retail":336.0,"sme":321130.65} | null |

### #316 owner decisions, 2026-11-06: the undeclared Corporate customer population requires clarification on governed arms; smaller baseline vocabularies may abstain.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G023:catalog | behaviour | "sum_without_verdict" | "abstain" |
| golden:G023:catalog | figure | 240.0 | null |
| golden:G023:catalog | joins_refused | [] | null |
| golden:G023:catalog | parts | {"corporate":40.0,"retail":200.0} | null |
| golden:G023:graph | behaviour | "parts_with_reason" | "clarify" |
| golden:G023:graph | joins_refused | ["retail-corporate"] | null |
| golden:G023:graph | parts | {"corporate":40.0,"retail":200.0} | null |
| golden:G023:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G023:schema | figure | 240.0 | null |
| golden:G023:schema | joins_refused | [] | null |
| golden:G023:schema | parts | {"corporate":40.0,"retail":200.0} | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. No column resolves the requested term in this arm. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G024:catalog | behaviour | "sum_without_verdict" | "abstain" |
| golden:G024:catalog | figure | 240.0 | null |
| golden:G024:catalog | joins_refused | [] | null |
| golden:G024:catalog | parts | {"corporate":40.0,"retail":200.0} | null |
| golden:G024:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G024:schema | figure | 240.0 | null |
| golden:G024:schema | joins_refused | [] | null |
| golden:G024:schema | parts | {"corporate":40.0,"retail":200.0} | null |
| golden:G028:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G028:schema | figure | 210.0 | null |
| golden:G028:schema | joins_refused | [] | null |
| golden:G028:schema | parts | {"sme":60.0,"wealth":150.0} | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Combined Corporate and Wealth revenue for FY26-Q1': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G025:schema | behaviour | "sum_without_verdict" | "abstain" |
| golden:G025:schema | figure | 21175888.65 | null |

### #316 owner decisions, 2026-11-06: unsupported addition abstains.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G029:catalog | behaviour | "refuse" | "abstain" |
| golden:G029:catalog | joins_refused | ["corporate-wealth"] | null |
| golden:G029:graph | behaviour | "refuse" | "abstain" |
| golden:G029:graph | joins_refused | ["corporate-wealth"] | null |
| golden:G029:schema | behaviour | "refuse" | "abstain" |
| golden:G029:schema | joins_refused | ["corporate-wealth"] | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Delinquency rate for retail lending in June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| golden:G044:schema | behaviour | "figure" | "abstain" |
| golden:G044:schema | figure | 3840.0 | null |
| golden:G044:schema | ok | false | true |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'What did retail bring in as revenue during June 2025?': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H002:schema | behaviour | "figure" | "abstain" |
| heldout:H002:schema | figure | 336.0 | null |

### #316 owner decisions, 2026-11-06: the declared active-client phrase resolves to 59 SME clients.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H003:catalog | figure | 0.0 | 59 |
| heldout:H003:catalog | ok | false | true |
| heldout:H003:graph | figure | 0.0 | 59 |
| heldout:H003:graph | ok | false | true |
| heldout:H003:schema | figure | 0.0 | 59 |
| heldout:H003:schema | ok | false | true |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Corporate credit exposure in total as at 30 June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H004:schema | behaviour | "figure" | "abstain" |
| heldout:H004:schema | figure | 40.0 | null |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Sum Corporate and Wealth revenue for FY26-Q1': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H008:schema | behaviour | "sum_without_verdict" | "abstain" |
| heldout:H008:schema | figure | 21175888.65 | null |

### #316 owner decisions, 2026-11-06: the no-join relationship is refused; a baseline without its required entity interpretation abstains.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H009:graph | behaviour | "abstain" | "refuse" |
| heldout:H009:graph | joins_refused | null | ["sme-wealth"] |
| heldout:H009:graph | ok | false | true |
| heldout:H009:schema | behaviour | "sum_without_verdict" | "abstain" |
| heldout:H009:schema | figure | 210.0 | null |
| heldout:H009:schema | joins_refused | [] | null |
| heldout:H009:schema | parts | {"sme":60.0,"wealth":150.0} | null |

### #316 owner decisions, 2026-11-06: the no-join relationship is refused.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H010:graph | behaviour | "abstain" | "refuse" |
| heldout:H010:graph | joins_refused | null | ["retail-corporate"] |
| heldout:H010:graph | ok | false | true |

### #316 owner decision 2026-11-06 ("This fixes bugs, so I am good"): coverage/check blocks the previous numeric interpretation. I cannot apply 'Delinquency rate for retail in June 2025': no certified measure or declared entity resolves the complete request. No figure is given.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H016:schema | behaviour | "figure" | "abstain" |
| heldout:H016:schema | figure | 3840.0 | null |
| heldout:H016:schema | ok | false | true |

### #316 step 7b requires governed definition paraphrases to cite certified documents again through the same definition path as public Ask; control baselines are retained. H012 now passes; H013 still misses the expected policy citation.

| Result / arm | Field | Before | After |
|---|---|---|---|
| heldout:H012:graph | ok | false | true |
| heldout:H012:graph | behaviour | "abstain" | "cite_certified" |
| heldout:H013:graph | behaviour | "abstain" | "cite_certified" |

## Step 7b: H012 and H013

On the graph arm, H012 and H013 move from `abstain` to `cite_certified`. H012 also changes `ok` from false to true. H013 still lacks the expected policy citation, so the held-out acceptance result remains 15/16. These are definition decisions, not numeric improvements.

## Agent contract migration: every manifest key

`tests/fixtures/agent_query_plan_v1_manifest.jsonl` records 464 keys. Of these, 187 change their answer summary; the other 277 retain the summary but migrate the typed plan, prompt/stage trace, evidence or serialised contract. The historical `agent_f75605a.json` is preserved. The existing manifest’s decision text is reproduced as each group heading below; each key is listed, including retained numeric results.

### Approved by the owner in #316 step 7b: control-arm coverage, plan checks and shared access guards block wrong answers ("This fixes bugs, so I am good"). Additional control-model outcome move listed: only physical schema, complete keys and the baseline calendar are available; the full proposal must pass the common coverage/declaration check. Former SQL guesses are no longer executable. Agent contract migration: typed plans/history, deterministic compiler, per-arm evidence and new prompt/stage traces replace SQL writing.

| Key | Before | After |
|---|---|---|
| golden:G001:control:bad_marker | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:birth_date_match | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:careful | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:constant_figure | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:invented_number | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:key_name_match | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:missing_certification | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:outer_join_match | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:restricted_column | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:scaled_figure | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:second_alias_match | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G001:control:undeclared_join | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G002:control:careful | {"behaviour":"figure","cited":["STD-FISCAL-CAL"],"figure":32721.92,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32721.92}],"parts":{"retail":32721.92},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G003:control:careful | {"behaviour":"figure","cited":["DRAFT-002"],"figure":176.0,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176.0}],"parts":{"retail":176.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G008:control:careful | {"behaviour":"figure","cited":["NOTE-001"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G009:control:careful | {"behaviour":"figure","cited":["DRAFT-002"],"figure":59.0,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"sme":59.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G013:control:careful | {"behaviour":"figure","cited":["DRAFT-001"],"figure":20190379.43,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43}],"parts":{"corporate":20190379.43},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G014:control:careful | {"behaviour":"figure","cited":["EM-002"],"figure":40.0,"figures":[{"column":"exposure.exposure_id","term":null,"unit":"corporate","value":40.0}],"parts":{"corporate":40.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G021:control:careful | {"behaviour":"sum_with_verdict","cited":["EM-002"],"figure":80119691.71,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65},{"column":"quarterly_revenue.revenue_eur","term":null,"unit":"corporate","value":79785273.39}],"parts":{"corporate":79785273.39,"retail":13287.67,"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G022:control:careful | {"behaviour":"sum_with_verdict","cited":["STD-FISCAL-CAL"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G025:control:careful | {"behaviour":"figure","cited":["XLS-005"],"figure":18669131.39,"figures":[{"column":"quarterly_revenue.revenue_eur","term":null,"unit":"corporate","value":18669131.39}],"parts":{"corporate":18669131.39},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G026:control:careful | {"behaviour":"sum_with_verdict","cited":["WIKI-001"],"figure":235.0,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176.0},{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"retail":176.0,"sme":59.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G027:control:careful | {"behaviour":"figure","cited":["STD-FISCAL-CAL"],"figure":32290.55,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32290.55}],"parts":{"retail":32290.55},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G030:control:careful | {"behaviour":"parts_with_reason","cited":["STD-FISCAL-CAL"],"figure":null,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":40483.98},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":899746.55},{"column":"quarterly_revenue.revenue_eur","term":null,"unit":"corporate","value":null}],"parts":{"corporate":null,"retail":40483.98,"sme":899746.55},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W02:control:careful | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":60.0,"figures":[{"column":"client.client_no","term":null,"unit":"sme","value":60.0}],"parts":{"sme":60.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W05:control:careful | {"behaviour":"figure","cited":["DRAFT-002"],"figure":350.0,"figures":[{"column":"account.account_id","term":null,"unit":"retail","value":350.0}],"parts":{"retail":350.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W08:control:careful | {"behaviour":"figure","cited":["DD-RETAIL"],"figure":0.0,"figures":[{"column":"account.account_id","term":null,"unit":"retail","value":0.0}],"parts":{"retail":0.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W11:control:careful | {"behaviour":"figure","cited":["XLS-002"],"figure":24.0,"figures":[{"column":"account.account_id","term":null,"unit":"retail","value":24.0}],"parts":{"retail":24.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W14:control:careful | {"behaviour":"figure","cited":["NOTE-002"],"figure":12.0,"figures":[{"column":"home branch","term":null,"unit":"retail","value":12.0}],"parts":{"retail":12.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W15:control:careful | {"behaviour":"figure","cited":["NOTE-002"],"figure":200.0,"figures":[{"column":"customer.segment_code","term":null,"unit":"retail","value":200.0}],"parts":{"retail":200.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB4:control:careful | {"behaviour":"figure","cited":["PROC-RECON"],"figure":350.0,"figures":[{"column":"account.account_id","term":null,"unit":"retail","value":350.0}],"parts":{"retail":350.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB5:control:careful | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER-V1"],"figure":200.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0}],"parts":{"retail":200.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H002:control:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H004:control:careful | {"behaviour":"figure","cited":["NOTE-003"],"figure":40.0,"figures":[{"column":"exposure.exposure_id","term":null,"unit":"corporate","value":40.0}],"parts":{"corporate":40.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H007:control:careful | {"behaviour":"sum_with_verdict","cited":["STD-FISCAL-CAL"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H008:control:careful | {"behaviour":"figure","cited":["STD-FISCAL-CAL"],"figure":18669131.39,"figures":[{"column":"quarterly_revenue.revenue_eur","term":null,"unit":"corporate","value":18669131.39}],"parts":{"corporate":18669131.39},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### Agent contract migration: typed plans/history, deterministic compiler, per-arm evidence and new prompt/stage traces replace SQL writing; the answer values remain the retained baseline.

| Key | Before | After |
|---|---|---|
| golden:G001:control:rules | {"behaviour":"figure","cited":["EM-002"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G001:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":null,"rows":null} |
| golden:G001:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G002:control:rules | {"behaviour":"figure","cited":["STD-FISCAL-CAL"],"figure":32721.92,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32721.92}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G002:treatment:careful | {"behaviour":"figure","cited":["MD-NET-INTEREST-INCOME"],"figure":32721.92,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32721.92}],"parts":{"retail":32721.92},"rows":null} | {"behaviour":"figure","cited":["MD-NET-INTEREST-INCOME"],"figure":32721.92,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32721.92}],"parts":null,"rows":null} |
| golden:G002:treatment:rules | {"behaviour":"figure","cited":["MD-NET-INTEREST-INCOME"],"figure":32721.92,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32721.92}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G003:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G003:treatment:careful | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":176.0,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176.0}],"parts":{"retail":176.0},"rows":null} | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":176,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176}],"parts":null,"rows":null} |
| golden:G003:treatment:rules | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":176,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G008:control:rules | {"behaviour":"figure","cited":["NOTE-001"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G008:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} |
| golden:G008:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G009:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G009:treatment:careful | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59.0,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"sme":59.0},"rows":null} | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":null,"rows":null} |
| golden:G009:treatment:rules | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G013:control:rules | {"behaviour":"figure","cited":["DRAFT-001"],"figure":20190379.43,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G013:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":20190379.43,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43}],"parts":{"corporate":20190379.43},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":20190379.43,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43}],"parts":null,"rows":null} |
| golden:G013:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":20190379.43,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G014:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G014:treatment:careful | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":{"corporate":440185374.74},"rows":null} | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":null,"rows":null} |
| golden:G014:treatment:rules | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G017:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G017:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G017:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"wealth":2506757.26},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":null,"rows":null} |
| golden:G017:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G018:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G018:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G018:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G018:treatment:rules | {"behaviour":"figure","cited":["MD-ASSETS-UNDER-ADVICE"],"figure":1830296649.93,"figures":[{"column":"assets_under_advice.aua_usd","term":null,"unit":"wealth","value":1830296649.93}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G020:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G020:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G020:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G020:treatment:rules | {"behaviour":"figure","cited":[],"figure":14,"figures":[{"column":"suitability_review.review_id","term":null,"unit":"wealth","value":14}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G021:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G021:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-CORPORATE","DD-RETAIL","DD-SME","DD-WEALTH","MD-REVENUE"],"figure":null,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":39983.72},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":953824.62},{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"retail":39983.72,"sme":953824.62,"wealth":2506757.26},"rows":null} | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":39983.72},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":953824.62},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"retail":39983.72,"sme":953824.62,"wealth":2506757.26},"rows":null} |
| golden:G021:treatment:rules | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":39983.72},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":953824.62},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"retail":39983.72,"sme":953824.62,"wealth":2506757.26},"rows":null} | Same answer summary; contract/trace migration |
| golden:G022:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G022:treatment:careful | {"behaviour":"sum_with_verdict","cited":["DD-RETAIL","DD-SME","MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | {"behaviour":"sum_with_verdict","cited":["MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} |
| golden:G022:treatment:rules | {"behaviour":"sum_with_verdict","cited":["MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | Same answer summary; contract/trace migration |
| golden:G024:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G024:control:rules | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G024:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G024:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G025:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G025:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-CORPORATE","DD-WEALTH","MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} |
| golden:G025:treatment:rules | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} | Same answer summary; contract/trace migration |
| golden:G026:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G026:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-RETAIL","DD-SME","MD-ACTIVE-CUSTOMER"],"figure":null,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176.0},{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"retail":176.0,"sme":59.0},"rows":null} | {"behaviour":"parts_with_reason","cited":["MD-ACTIVE-CUSTOMER"],"figure":null,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176},{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":{"retail":176,"sme":59},"rows":null} |
| golden:G026:treatment:rules | {"behaviour":"parts_with_reason","cited":["MD-ACTIVE-CUSTOMER"],"figure":null,"figures":[{"column":"customer_activity.is_active_flag","term":null,"unit":"retail","value":176},{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":{"retail":176,"sme":59},"rows":null} | Same answer summary; contract/trace migration |
| golden:G027:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G027:treatment:careful | {"behaviour":"sum_with_verdict","cited":["DD-RETAIL","DD-SME","MD-NET-INTEREST-INCOME"],"figure":841146.67,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32290.55},{"column":"monthly_client_revenue.nii","term":null,"unit":"sme","value":808856.12}],"parts":{"retail":32290.55,"sme":808856.12},"rows":null} | {"behaviour":"sum_with_verdict","cited":["MD-NET-INTEREST-INCOME"],"figure":841146.67,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32290.55},{"column":"monthly_client_revenue.nii","term":null,"unit":"sme","value":808856.12}],"parts":{"retail":32290.55,"sme":808856.12},"rows":null} |
| golden:G027:treatment:rules | {"behaviour":"sum_with_verdict","cited":["MD-NET-INTEREST-INCOME"],"figure":841146.67,"figures":[{"column":"monthly_revenue.interest_income","term":null,"unit":"retail","value":32290.55},{"column":"monthly_client_revenue.nii","term":null,"unit":"sme","value":808856.12}],"parts":{"retail":32290.55,"sme":808856.12},"rows":null} | Same answer summary; contract/trace migration |
| golden:G028:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G028:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G028:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G028:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G030:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G030:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-CORPORATE","DD-RETAIL","DD-SME","DD-WEALTH","MD-REVENUE"],"figure":null,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":40483.98},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":899746.55},{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20046400.76},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2404440.18}],"parts":{"corporate":20046400.76,"retail":40483.98,"sme":899746.55,"wealth":2404440.18},"rows":null} | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20046400.76},{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":40483.98},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":899746.55},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2404440.18}],"parts":{"corporate":20046400.76,"retail":40483.98,"sme":899746.55,"wealth":2404440.18},"rows":null} |
| golden:G030:treatment:rules | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20046400.76},{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":40483.98},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":899746.55},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2404440.18}],"parts":{"corporate":20046400.76,"retail":40483.98,"sme":899746.55,"wealth":2404440.18},"rows":null} | Same answer summary; contract/trace migration |
| golden:G031:control:careful | {"behaviour":"cite_certified","cited":["EM-001","MD-ACTIVE-CUSTOMER-V1","MD-CUSTOMER-ATTRITION","POL-METRIC-CERT","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-001","MD-CUSTOMER-ATTRITION","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G031:control:rules | {"behaviour":"cite","cited":["EM-001","MD-CUSTOMER-ATTRITION","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G031:treatment:careful | {"behaviour":"cite_certified","cited":["DD-RETAIL","DD-SME","MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G031:treatment:rules | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G032:control:careful | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-FISCAL-YEAR","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","MD-FISCAL-YEAR","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G032:control:rules | {"behaviour":"cite","cited":["EM-003","MD-FISCAL-YEAR","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G032:treatment:careful | {"behaviour":"cite_certified","cited":["DD-CORPORATE","DD-RETAIL","MD-FISCAL-YEAR","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["DD-CORPORATE","DD-RETAIL"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G032:treatment:rules | {"behaviour":"cite_certified","cited":["DD-CORPORATE","DD-RETAIL"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G033:control:careful | {"behaviour":"cite_certified","cited":["EM-005","MD-FISCAL-YEAR","NOTE-006","NOTE-008","STD-FISCAL-CAL"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["MD-FISCAL-YEAR","NOTE-006","NOTE-008"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G033:control:rules | {"behaviour":"cite","cited":["MD-FISCAL-YEAR","NOTE-006","NOTE-008"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G033:treatment:careful | {"behaviour":"cite_certified","cited":["DD-WEALTH","MD-FISCAL-YEAR","STD-FISCAL-CAL"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G033:treatment:rules | {"behaviour":"cite_certified","cited":["DD-WEALTH","MD-FISCAL-YEAR","STD-FISCAL-CAL"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G034:control:bad_marker | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:birth_date_match | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:careful | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:constant_figure | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:invented_number | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:key_name_match | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:missing_certification | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:outer_join_match | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:restricted_column | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:rules | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G034:control:scaled_figure | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:second_alias_match | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:control:undeclared_join | {"behaviour":"cite_certified","cited":["EM-003","EM-005","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G034:treatment:careful | {"behaviour":"cite_certified","cited":["MD-PERIOD-END-FX-RATE","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G034:treatment:rules | {"behaviour":"cite_certified","cited":["MD-PERIOD-END-FX-RATE","MD-REVENUE","STD-FX","STD-TAGGING"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G035:control:careful | {"behaviour":"cite_certified","cited":["DRAFT-001","EM-002","MD-CUSTOMER-ATTRITION","MD-REVENUE","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-001","EM-002","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G035:control:rules | {"behaviour":"cite","cited":["DRAFT-001","EM-002","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G035:treatment:careful | {"behaviour":"cite_certified","cited":["DD-CORPORATE","DD-RETAIL","DD-SME","DD-WEALTH","MD-CUSTOMER-ATTRITION","MD-REVENUE","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-CUSTOMER-ATTRITION","MD-REVENUE","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G035:treatment:rules | {"behaviour":"cite_certified","cited":["MD-CUSTOMER-ATTRITION","MD-REVENUE","PROC-RECON"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G036:control:careful | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","POL-METRIC-CERT","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G036:control:rules | {"behaviour":"cite","cited":["MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G036:treatment:careful | {"behaviour":"cite_certified","cited":["DD-RETAIL","DD-SME","MD-ACTIVE-CUSTOMER","POL-METRIC-CERT","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","POL-METRIC-CERT","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G036:treatment:rules | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","POL-METRIC-CERT","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G037:control:careful | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","POL-DATA-CLASS","POL-METRIC-CERT","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["MD-CUSTOMER-ATTRITION","POL-DATA-CLASS","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G037:control:rules | {"behaviour":"cite","cited":["MD-CUSTOMER-ATTRITION","POL-DATA-CLASS","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G037:treatment:careful | {"behaviour":"cite_certified","cited":["MD-CUSTOMER-ATTRITION","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G037:treatment:rules | {"behaviour":"cite_certified","cited":["MD-CUSTOMER-ATTRITION","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G038:control:careful | {"behaviour":"cite_certified","cited":["EM-004","EM-005","MD-ASSETS-UNDER-ADVICE","NOTE-003","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-004","NOTE-003","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G038:control:rules | {"behaviour":"cite","cited":["EM-004","NOTE-003","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G038:treatment:careful | {"behaviour":"cite_certified","cited":["MD-ASSETS-UNDER-ADVICE","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G038:treatment:rules | {"behaviour":"cite_certified","cited":["MD-ASSETS-UNDER-ADVICE","STD-SUITABILITY"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G039:control:careful | {"behaviour":"cite_certified","cited":["EM-001","MD-PERIOD-END-FX-RATE","POL-DATA-CLASS","STD-DOCUMENT-READING","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["MD-PERIOD-END-FX-RATE","POL-DATA-CLASS","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G039:control:rules | {"behaviour":"cite","cited":["MD-PERIOD-END-FX-RATE","POL-DATA-CLASS","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G039:treatment:careful | {"behaviour":"cite_certified","cited":["POL-DATA-CLASS","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G039:treatment:rules | {"behaviour":"cite_certified","cited":["POL-DATA-CLASS","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G040:control:careful | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-011","MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-011","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G040:control:rules | {"behaviour":"cite","cited":["DRAFT-002","EM-011","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G040:treatment:careful | {"behaviour":"cite_certified","cited":["DD-RETAIL","DD-SME","MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","MD-PERIOD-END-FX-RATE","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","MD-PERIOD-END-FX-RATE","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G040:treatment:rules | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-ACTIVE-CUSTOMER-V1","MD-PERIOD-END-FX-RATE","POL-METRIC-CERT"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G041:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G041:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G041:treatment:careful | {"behaviour":"abstain_route_steward","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G041:treatment:rules | {"behaviour":"abstain_route_steward","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G043:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G043:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G043:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G043:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:control:bad_marker | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:control:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:control:invented_number | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:control:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G045:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W01:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W01:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W01:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W01:treatment:rules | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":null,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":null}],"parts":null,"rows":{"EAST":12371673.22,"WEST":8831519.54}} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W02:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W02:treatment:careful | {"behaviour":"figure","cited":[],"figure":4753885.36,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":4753885.36}],"parts":{"wealth":4753885.36},"rows":null} | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":4753885.36,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":4753885.36}],"parts":null,"rows":null} |
| golden_wealth_v1:W02:treatment:rules | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":4753885.36,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":4753885.36}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W03:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W03:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W03:treatment:careful | {"behaviour":"figure","cited":[],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":{"wealth":6069905.64},"rows":null} | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":null,"rows":null} |
| golden_wealth_v1:W03:treatment:rules | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W04:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W04:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W04:treatment:careful | {"behaviour":"figure","cited":[],"figure":7396101.83,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":7396101.83}],"parts":{"wealth":7396101.83},"rows":null} | {"behaviour":"figure","cited":[],"figure":7396101.83,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":7396101.83}],"parts":null,"rows":null} |
| golden_wealth_v1:W04:treatment:rules | {"behaviour":"figure","cited":[],"figure":7396101.83,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":7396101.83}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W05:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W05:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W05:treatment:rules | {"behaviour":"figure","cited":[],"figure":241,"figures":[{"column":"account.account_id, account.source_system","term":null,"unit":"wealth","value":241}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:bad_marker | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:birth_date_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:constant_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:invented_number | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:key_name_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:missing_certification | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:outer_join_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:restricted_column | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:scaled_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:second_alias_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:control:undeclared_join | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W06:treatment:careful | {"behaviour":"figure","cited":[],"figure":16910957.72,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":16910957.72}],"parts":{"wealth":16910957.72},"rows":null} | {"behaviour":"figure","cited":[],"figure":16910957.72,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":16910957.72}],"parts":null,"rows":null} |
| golden_wealth_v1:W06:treatment:rules | {"behaviour":"figure","cited":[],"figure":16910957.72,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":16910957.72}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W07:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W07:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W07:treatment:careful | {"behaviour":"figure","cited":[],"figure":17080976.76,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":17080976.76}],"parts":{"wealth":17080976.76},"rows":null} | {"behaviour":"figure","cited":[],"figure":17080976.76,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":17080976.76}],"parts":null,"rows":null} |
| golden_wealth_v1:W07:treatment:rules | {"behaviour":"figure","cited":[],"figure":17080976.76,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":17080976.76}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W08:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W08:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W08:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W09:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W09:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W09:treatment:careful | {"behaviour":"figure","cited":[],"figure":5868354.54,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5868354.54}],"parts":{"wealth":5868354.54},"rows":null} | {"behaviour":"figure","cited":[],"figure":5868354.54,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5868354.54}],"parts":null,"rows":null} |
| golden_wealth_v1:W09:treatment:rules | {"behaviour":"figure","cited":[],"figure":5868354.54,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5868354.54}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W10:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W10:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W10:treatment:careful | {"behaviour":"figure","cited":[],"figure":5920237.28,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5920237.28}],"parts":{"wealth":5920237.28},"rows":null} | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":5920237.28,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5920237.28}],"parts":null,"rows":null} |
| golden_wealth_v1:W10:treatment:rules | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":5920237.28,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":5920237.28}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W11:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W11:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W11:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W12:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W12:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W12:treatment:careful | {"behaviour":"figure","cited":[],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":{"wealth":6069905.64},"rows":null} | {"behaviour":"figure","cited":[],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":null,"rows":null} |
| golden_wealth_v1:W12:treatment:rules | {"behaviour":"figure","cited":[],"figure":6069905.64,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":6069905.64}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W13:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W13:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W13:treatment:careful | {"behaviour":"abstain_with_reason","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W13:treatment:rules | {"behaviour":"abstain_with_reason","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W14:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W14:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W14:treatment:rules | {"behaviour":"figure","cited":["DD-WEALTH"],"figure":null,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":null}],"parts":null,"rows":{"MIDWEST":3755170.97,"NORTHEAST":12371673.22,"PACIFIC":5076348.57}} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W15:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W15:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W15:treatment:rules | {"behaviour":"figure","cited":[],"figure":null,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":null}],"parts":null,"rows":{"AFFLUENT":14178114.93,"PRIVATE":7025077.83}} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W16:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W16:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W16:treatment:careful | {"behaviour":"figure","cited":[],"figure":7641422.24,"figures":[{"column":"aum_month_end.aum_usd","term":null,"unit":"wealth","value":7641422.24}],"parts":{"wealth":7641422.24},"rows":null} | {"behaviour":"figure","cited":[],"figure":7641422.24,"figures":[{"column":"aum_month_end.aum_usd","term":null,"unit":"wealth","value":7641422.24}],"parts":null,"rows":null} |
| golden_wealth_v1:W16:treatment:rules | {"behaviour":"figure","cited":[],"figure":7641422.24,"figures":[{"column":"aum_month_end.aum_usd","term":null,"unit":"wealth","value":7641422.24}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W18:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W18:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W18:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W18:treatment:rules | {"behaviour":"figure","cited":[],"figure":null,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":null}],"parts":null,"rows":{"100":16910957.72,"200":4292235.04}} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W19:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W19:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W19:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W19:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-DOB:control:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-DOB:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-DOB:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-EMAIL:control:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-EMAIL:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB-EMAIL:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1-R:control:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1-R:treatment:careful | {"behaviour":"blocked_access","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1-R:treatment:rules | {"behaviour":"blocked_access","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1:control:rules | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB1:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB4:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB4:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB4:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB5:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB5:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB5:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB6:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB6:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB6:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB6:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H001:control:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} |
| heldout:H001:control:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H001:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} |
| heldout:H001:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H002:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H002:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":{"retail":13287.67},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":null,"rows":null} |
| heldout:H002:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":13287.67,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H004:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H004:treatment:careful | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":{"corporate":440185374.74},"rows":null} | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":null,"rows":null} |
| heldout:H004:treatment:rules | {"behaviour":"figure","cited":["MD-CREDIT-EXPOSURE"],"figure":440185374.74,"figures":[{"column":"exposure.ead_eur","term":null,"unit":"corporate","value":440185374.74}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H005:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H005:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H005:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"wealth":2506757.26},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":null,"rows":null} |
| heldout:H005:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H007:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H007:treatment:careful | {"behaviour":"sum_with_verdict","cited":["DD-RETAIL","DD-SME","MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | {"behaviour":"sum_with_verdict","cited":["MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} |
| heldout:H007:treatment:rules | {"behaviour":"sum_with_verdict","cited":["MD-REVENUE"],"figure":334418.32,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":13287.67},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"retail":13287.67,"sme":321130.65},"rows":null} | Same answer summary; contract/trace migration |
| heldout:H008:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H008:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-CORPORATE","DD-WEALTH","MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} |
| heldout:H008:treatment:rules | {"behaviour":"parts_with_reason","cited":["MD-REVENUE"],"figure":null,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":20190379.43},{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"corporate":20190379.43,"wealth":2506757.26},"rows":null} | Same answer summary; contract/trace migration |
| heldout:H011:control:bad_marker | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:birth_date_match | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:careful | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:constant_figure | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:invented_number | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:key_name_match | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:missing_certification | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:outer_join_match | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:restricted_column | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:rules | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H011:control:scaled_figure | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:second_alias_match | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:undeclared_join | {"behaviour":"cite_certified","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1","NOTE-001","WIKI-001"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:treatment:careful | {"behaviour":"cite_certified","cited":["DD-RETAIL","DD-SME","MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:treatment:rules | {"behaviour":"cite_certified","cited":["MD-ACTIVE-CUSTOMER","MD-CUSTOMER-ATTRITION","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H012:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H013:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H014:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H014:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H014:treatment:careful | {"behaviour":"abstain_route_steward","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H014:treatment:rules | {"behaviour":"abstain_route_steward","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H015:control:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H015:control:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H015:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H015:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Approved by the owner in #316 step 7b: control-arm coverage, plan checks and shared access guards block wrong answers ("This fixes bugs, so I am good"). Additional control-model outcome move listed: only physical schema, complete keys and the baseline calendar are available; the full proposal must pass the common coverage/declaration check. Former SQL guesses are no longer executable. Approved pack declaration: use its population, aggregation, filters, relationships and period on the governed treatment; the control retains its physical schema baseline.

| Key | Before | After |
|---|---|---|
| golden:G004:control:careful | {"behaviour":"figure","cited":["EM-005"],"figure":12.0,"figures":[{"column":"home branch","term":null,"unit":"retail","value":12.0}],"parts":{"retail":12.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G005:control:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":5889.23,"figures":[{"column":"daily_balance.ledger_balance","term":null,"unit":"retail","value":5889.23}],"parts":{"retail":5889.23},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G006:control:careful | {"behaviour":"figure","cited":["MD-CUSTOMER-ATTRITION"],"figure":40.0,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":40.0}],"parts":{"retail":40.0},"rows":null} | {"behaviour":"figure","cited":["MD-CUSTOMER-ATTRITION"],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} |
| golden:G007:control:careful | {"behaviour":"figure","cited":["XLS-006"],"figure":6.0,"figures":[{"column":"product.product_family","term":null,"unit":"retail","value":6.0}],"parts":{"retail":6.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G010:control:careful | {"behaviour":"figure","cited":["MD-DELINQUENCY-RATE"],"figure":3246.0,"figures":[{"column":"repayment.days_past_due","term":null,"unit":"sme","value":3246.0}],"parts":{"sme":3246.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G011:control:careful | {"behaviour":"figure","cited":["DRAFT-006"],"figure":8.0,"figures":[{"column":"covenant.breached_flag","term":null,"unit":"sme","value":8.0}],"parts":{"sme":8.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:bad_marker | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:birth_date_match | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:careful | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:constant_figure | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:invented_number | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:key_name_match | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:missing_certification | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:outer_join_match | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:restricted_column | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:scaled_figure | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:second_alias_match | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G012:control:undeclared_join | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":321130.65,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":321130.65}],"parts":{"sme":321130.65},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G015:control:careful | {"behaviour":"figure","cited":["NOTE-002"],"figure":40.0,"figures":[{"column":"counterparty.lei_code","term":null,"unit":"corporate","value":40.0}],"parts":{"corporate":40.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G016:control:careful | {"behaviour":"figure","cited":["STD-FISCAL-CAL"],"figure":40.0,"figures":[{"column":"counterparty.counterparty_ref","term":null,"unit":"corporate","value":40.0}],"parts":{"corporate":40.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB3-W:control:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H006:control:careful | {"behaviour":"figure","cited":["EM-003"],"figure":40.0,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":40.0}],"parts":{"retail":40.0},"rows":null} | {"behaviour":"figure","cited":["EM-003"],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} |

### Approved pack declaration: use its population, aggregation, filters, relationships and period on the governed treatment; the control retains its physical schema baseline.

| Key | Before | After |
|---|---|---|
| golden:G004:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G004:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3615.55,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":3615.55}],"parts":{"retail":3615.55},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3615.55,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":3615.55}],"parts":null,"rows":null} |
| golden:G004:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3615.55,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":3615.55}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G005:control:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":5889.23,"figures":[{"column":"daily_balance.ledger_balance","term":null,"unit":"retail","value":5889.23}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G005:treatment:careful | {"behaviour":"figure","cited":["MD-AVERAGE-BALANCE"],"figure":5889.23,"figures":[{"column":"daily_balance.ledger_balance","term":null,"unit":"retail","value":5889.23}],"parts":{"retail":5889.23},"rows":null} | {"behaviour":"figure","cited":["MD-AVERAGE-BALANCE"],"figure":5889.23,"figures":[{"column":"daily_balance.ledger_balance","term":null,"unit":"retail","value":5889.23}],"parts":null,"rows":null} |
| golden:G005:treatment:rules | {"behaviour":"figure","cited":["MD-AVERAGE-BALANCE"],"figure":5889.23,"figures":[{"column":"daily_balance.ledger_balance","term":null,"unit":"retail","value":5889.23}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G006:control:rules | {"behaviour":"figure","cited":["MD-CUSTOMER-ATTRITION"],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G006:treatment:careful | {"behaviour":"figure","cited":[],"figure":14.0,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14.0}],"parts":{"retail":14.0},"rows":null} | {"behaviour":"figure","cited":[],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} |
| golden:G006:treatment:rules | {"behaviour":"figure","cited":[],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G007:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G007:treatment:careful | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":428.32,"figures":[{"column":"monthly_revenue.fee_income","term":null,"unit":"retail","value":428.32}],"parts":{"retail":428.32},"rows":null} | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":428.32,"figures":[{"column":"monthly_revenue.fee_income","term":null,"unit":"retail","value":428.32}],"parts":null,"rows":null} |
| golden:G007:treatment:rules | {"behaviour":"figure","cited":["MD-FEE-INCOME"],"figure":428.32,"figures":[{"column":"monthly_revenue.fee_income","term":null,"unit":"retail","value":428.32}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G010:control:rules | {"behaviour":"figure","cited":["MD-DELINQUENCY-RATE"],"figure":60,"figures":[{"column":"repayment.days_past_due","term":null,"unit":"sme","value":60}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G010:treatment:careful | {"behaviour":"figure","cited":[],"figure":60.0,"figures":[{"column":"repayment.days_past_due","term":null,"unit":"sme","value":60.0}],"parts":{"sme":60.0},"rows":null} | {"behaviour":"figure","cited":["DD-SME"],"figure":60,"figures":[{"column":"repayment.days_past_due","term":null,"unit":"sme","value":60}],"parts":null,"rows":null} |
| golden:G010:treatment:rules | {"behaviour":"figure","cited":["DD-SME"],"figure":60,"figures":[{"column":"repayment.days_past_due","term":null,"unit":"sme","value":60}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G011:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G011:treatment:careful | {"behaviour":"figure","cited":[],"figure":8.0,"figures":[{"column":"covenant.breached_flag","term":null,"unit":"sme","value":8.0}],"parts":{"sme":8.0},"rows":null} | {"behaviour":"figure","cited":[],"figure":8,"figures":[{"column":"covenant.breached_flag","term":null,"unit":"sme","value":8}],"parts":null,"rows":null} |
| golden:G011:treatment:rules | {"behaviour":"figure","cited":[],"figure":8,"figures":[{"column":"covenant.breached_flag","term":null,"unit":"sme","value":8}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G012:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G012:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G012:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":114998.84,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":114998.84}],"parts":{"sme":114998.84},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":114998.84,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":114998.84}],"parts":null,"rows":null} |
| golden:G012:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":114998.84,"figures":[{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":114998.84}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G015:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G015:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3073419.79,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":3073419.79}],"parts":{"corporate":3073419.79},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3073419.79,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":3073419.79}],"parts":null,"rows":null} |
| golden:G015:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":3073419.79,"figures":[{"column":"quarterly_revenue.revenue_usd_converted","term":null,"unit":"corporate","value":3073419.79}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G016:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G016:treatment:careful | {"behaviour":"figure","cited":[],"figure":8.0,"figures":[{"column":"rating.internal_grade","term":null,"unit":"corporate","value":8.0}],"parts":{"corporate":8.0},"rows":null} | {"behaviour":"figure","cited":[],"figure":8,"figures":[{"column":"rating.internal_grade","term":null,"unit":"corporate","value":8}],"parts":null,"rows":null} |
| golden:G016:treatment:rules | {"behaviour":"figure","cited":[],"figure":8,"figures":[{"column":"rating.internal_grade","term":null,"unit":"corporate","value":8}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G019:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G019:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G019:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":751247.99,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":751247.99}],"parts":{"wealth":751247.99},"rows":null} | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":751247.99,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":751247.99}],"parts":null,"rows":null} |
| golden:G019:treatment:rules | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":751247.99,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":751247.99}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3-W:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3-W:treatment:careful | {"behaviour":"blocked_access","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3-W:treatment:rules | {"behaviour":"blocked_access","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB3:treatment:careful | {"behaviour":"figure","cited":[],"figure":4985720.27,"figures":[{"column":"wealth.TRAN_FACT_1.TRAN_AM","term":null,"unit":"wealth","value":4985720.27}],"parts":{"wealth":4985720.27},"rows":null} | {"behaviour":"figure","cited":[],"figure":4985720.27,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":4985720.27}],"parts":null,"rows":null} |
| golden_wealth_v1:XB3:treatment:rules | {"behaviour":"figure","cited":[],"figure":4985720.27,"figures":[{"column":"flow.amount_usd","term":null,"unit":"wealth","value":4985720.27}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H006:control:rules | {"behaviour":"figure","cited":["EM-003"],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H006:treatment:careful | {"behaviour":"figure","cited":[],"figure":14.0,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14.0}],"parts":{"retail":14.0},"rows":null} | {"behaviour":"figure","cited":[],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} |
| heldout:H006:treatment:rules | {"behaviour":"figure","cited":[],"figure":14,"figures":[{"column":"complaint.complaint_id","term":null,"unit":"retail","value":14}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Corporate customers is unresolved; clarify instead of adding different populations.

| Key | Before | After |
|---|---|---|
| golden:G023:control:bad_marker | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:birth_date_match | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:careful | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:constant_figure | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:invented_number | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:key_name_match | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:missing_certification | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:outer_join_match | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:restricted_column | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:scaled_figure | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:second_alias_match | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:control:undeclared_join | {"behaviour":"sum_with_verdict","cited":["POL-METRIC-CERT"],"figure":280.0,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"contact.customer_id","term":null,"unit":"corporate","value":80.0}],"parts":{"corporate":80.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G023:treatment:careful | {"behaviour":"parts_with_reason","cited":["DD-CORPORATE","DD-RETAIL"],"figure":null,"figures":[{"column":"customer.customer_id","term":null,"unit":"retail","value":200.0},{"column":"counterparty.counterparty_ref","term":null,"unit":"corporate","value":40.0}],"parts":{"corporate":40.0,"retail":200.0},"rows":null} | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### Corporate customers is unresolved; clarify instead of adding different populations. Step 9a: name the unresolved population and replace its whole source scope when selecting the declared entity.

| Key | Before | After |
|---|---|---|
| golden:G023:control:rules | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G023:treatment:rules | {"behaviour":"clarify","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### No supported typed addition of different measures; abstain without operands.

| Key | Before | After |
|---|---|---|
| golden:G029:control:careful | {"behaviour":"figure","cited":["NOTE-002"],"figure":160.0,"figures":[{"column":"exposure.exposure_id","term":null,"unit":"corporate","value":160.0}],"parts":{"corporate":160.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G029:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G029:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G029:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Different cause from the step 7b control guard approval: shared document-only definitions hold plain retrieval without invoking the SQL-era malformed JSON mock. No coverage or access guard caused this move; listed for manager review, not marked approved.

| Key | Before | After |
|---|---|---|
| golden:G034:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["EM-003","EM-005","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H011:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite","cited":["DRAFT-002","EM-005","MD-ACTIVE-CUSTOMER-V1"],"figure":null,"figures":[],"parts":null,"rows":null} |

### Approved by the owner in #316 step 7b: control-arm coverage, plan checks and shared access guards block wrong answers ("This fixes bugs, so I am good"). Additional control-model outcome move listed: only physical schema, complete keys and the baseline calendar are available; the full proposal must pass the common coverage/declaration check. Former SQL guesses are no longer executable. Agent contract migration: typed plans/history, deterministic compiler, per-arm evidence and new prompt/stage traces replace SQL writing. Retain the owner-approved margin re-baseline in clients/meridian/baselines/issue-316.json.

| Key | Before | After |
|---|---|---|
| golden:G042:control:careful | {"behaviour":"sum_with_verdict","cited":["EM-002"],"figure":20996676.33,"figures":[{"column":"monthly_revenue.total_revenue_usd","term":null,"unit":"retail","value":39983.72},{"column":"monthly_client_revenue.revenue_usd","term":null,"unit":"sme","value":953824.62},{"column":"quarterly_revenue.revenue_eur","term":null,"unit":"corporate","value":20002867.99}],"parts":{"corporate":20002867.99,"retail":39983.72,"sme":953824.62},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G044:control:careful | {"behaviour":"figure","cited":["NOTE-001"],"figure":3840.0,"figures":[{"column":"product.rate_bps","term":null,"unit":"retail","value":3840.0}],"parts":{"retail":3840.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H016:control:careful | {"behaviour":"figure","cited":["NOTE-001"],"figure":3840.0,"figures":[{"column":"product.rate_bps","term":null,"unit":"retail","value":3840.0}],"parts":{"retail":3840.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### Agent contract migration: typed plans/history, deterministic compiler, per-arm evidence and new prompt/stage traces replace SQL writing; the answer values remain the retained baseline. Retain the owner-approved margin re-baseline in clients/meridian/baselines/issue-316.json.

| Key | Before | After |
|---|---|---|
| golden:G042:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G042:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G042:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G044:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G044:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden:G044:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H016:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H016:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H016:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Approved by the owner in #316 step 7b: control-arm coverage, plan checks and shared access guards block wrong answers ("This fixes bugs, so I am good"). Additional outcome move listed: the shared access/restricted-read guard refuses before a model call; the SQL-era mock abstained. This aligns the existing guard, with no query or private rows.

| Key | Before | After |
|---|---|---|
| golden:G045:control:birth_date_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:constant_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:key_name_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:missing_certification | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:outer_join_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:restricted_column | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:scaled_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:second_alias_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden:G045:control:undeclared_join | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB-DOB:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB-EMAIL:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB1-R:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### No declared product population supports the revenue threshold; abstain.

| Key | Before | After |
|---|---|---|
| golden_figures_v1:F04:control:careful | null | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_figures_v1:F04:control:rules | null | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_figures_v1:F04:treatment:careful | null | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_figures_v1:F04:treatment:rules | null | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### AUM plus AUA is an unsupported addition of different measures; abstain.

| Key | Before | After |
|---|---|---|
| golden_wealth_v1:W17:control:bad_marker | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:birth_date_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:constant_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:invented_number | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:key_name_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:malformed_json | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:missing_certification | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:outer_join_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:restricted_column | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:scaled_figure | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:second_alias_match | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:control:undeclared_join | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:treatment:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W17:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### NNA plus revenue is an unsupported addition of different measures; abstain.

| Key | Before | After |
|---|---|---|
| golden_wealth_v1:W20:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W20:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:W20:treatment:careful | {"behaviour":"figure","cited":["MD-REVENUE"],"figure":2506757.26,"figures":[{"column":"quarterly_fee_revenue.revenue_usd","term":null,"unit":"wealth","value":2506757.26}],"parts":{"wealth":2506757.26},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:W20:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### The cross-unit comparison and share is incomplete; abstain without partial figures.

| Key | Before | After |
|---|---|---|
| golden_wealth_v1:XB2:control:careful | {"behaviour":"figure","cited":["NOTE-002"],"figure":0.0,"figures":[{"column":"client.client_no","term":null,"unit":"sme","value":0.0}],"parts":{"sme":0.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB2:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| golden_wealth_v1:XB2:treatment:careful | {"behaviour":"figure","cited":[],"figure":111354177.52,"figures":[{"column":"wealth.AUM_MNTH_END_FACT_1.AUM_AM","term":null,"unit":"wealth","value":111354177.52}],"parts":{"wealth":111354177.52},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| golden_wealth_v1:XB2:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Approved by the owner in #316 step 7b: control-arm coverage, plan checks and shared access guards block wrong answers ("This fixes bugs, so I am good"). Additional control-model outcome move listed: only physical schema, complete keys and the baseline calendar are available; the full proposal must pass the common coverage/declaration check. Former SQL guesses are no longer executable. The approved SME active-client declaration counts 59.

| Key | Before | After |
|---|---|---|
| heldout:H003:control:careful | {"behaviour":"figure","cited":["EM-003"],"figure":59.0,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"sme":59.0},"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### The approved SME active-client declaration counts 59.

| Key | Before | After |
|---|---|---|
| heldout:H003:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H003:treatment:careful | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59.0,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59.0}],"parts":{"sme":59.0},"rows":null} | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":null,"rows":null} |
| heldout:H003:treatment:rules | {"behaviour":"figure","cited":["MD-ACTIVE-CUSTOMER"],"figure":59,"figures":[{"column":"client_activity.active_flag","term":null,"unit":"sme","value":59}],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### The SME–Wealth no-join declaration refuses matching.

| Key | Before | After |
|---|---|---|
| heldout:H009:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H009:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H009:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H009:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### The Retail–Corporate no-join declaration refuses matching by a similar key name.

| Key | Before | After |
|---|---|---|
| heldout:H010:control:careful | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H010:control:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H010:treatment:careful | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H010:treatment:rules | {"behaviour":"refuse","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |

### Different cause from the step 7b control guard approval: this definition paraphrase is not recognised by the retained physical-schema control baseline. The SQL-era mock cited documents; the shared control abstains. The required step 7b fix applies to governed public Ask and treatment only. Listed for manager review, not marked approved.

| Key | Before | After |
|---|---|---|
| heldout:H012:control:careful | {"behaviour":"cite_certified","cited":["EM-005","MD-FISCAL-YEAR","STD-FISCAL-CAL","WIKI-002","XLS-002"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H013:control:careful | {"behaviour":"cite_certified","cited":["DD-WEALTH","EM-003","MD-PERIOD-END-FX-RATE","POL-METRIC-CERT","STD-FX"],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} |

### Required #316 step 7b definition fix: explanatory and permission paraphrases use the same document-only definition path as public Ask. Treatment cites certified documents again, without numeric interpretation or execution.

| Key | Before | After |
|---|---|---|
| heldout:H012:treatment:careful | {"behaviour":"cite_certified","cited":["MD-FISCAL-YEAR","STD-FISCAL-CAL"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H012:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["MD-FISCAL-YEAR","STD-FISCAL-CAL"],"figure":null,"figures":[],"parts":null,"rows":null} |
| heldout:H013:treatment:careful | {"behaviour":"cite_certified","cited":["DD-WEALTH","MD-PERIOD-END-FX-RATE"],"figure":null,"figures":[],"parts":null,"rows":null} | Same answer summary; contract/trace migration |
| heldout:H013:treatment:rules | {"behaviour":"abstain","cited":[],"figure":null,"figures":[],"parts":null,"rows":null} | {"behaviour":"cite_certified","cited":["DD-WEALTH","MD-PERIOD-END-FX-RATE"],"figure":null,"figures":[],"parts":null,"rows":null} |

## Golden fixture expectations

Compared with `origin/develop`, every changed expectation in the committed fixtures is listed below. Reformatting and the list-to-versioned-object wrapper do not change answers.

### demos/kge/clients/meridian/fixture/golden.json

Issue #316 owner decisions: G023 clarifies the undeclared Corporate customer population; G029 abstains on unsupported addition.

| Item | Before expectation | After expectation |
|---|---|---|
| G023 | {"behaviour":"parts_with_reason","parts":{"retail":200,"corporate":40},"commensurable":false,"reason":"Different populations: people and legal entities. No join exists; contact.customer_id is a CRM number.","joins_refused":["retail-corporate"]} | {"behaviour":"clarify","no_figure":true,"reason":"Corporate customers is unresolved: customer names only the declared Retail entity."} |
| G029 | {"behaviour":"refuse","reason":"Different measures, different currencies, different meanings. Not a question the graph will sum.","joins_refused":["corporate-wealth"]} | {"behaviour":"abstain","no_figure":true,"reason":"The checked plan has no general addition operation for different measures; no operand is returned alone."} |

### demos/kge/clients/meridian/fixture/golden_figures_v1.json

Owner-approved #316 complete coverage and declaration corrections.

| Item | Before expectation | After expectation |
|---|---|---|
| F04 | {"behaviour":"parts_with_reason"} | {"behaviour":"abstain","no_figure":true} |

### demos/kge/clients/meridian/fixture/golden_wealth_v1.json

Issue #316 owner decisions: unsupported additions and undeclared dual-client share abstain.

| Item | Before expectation | After expectation |
|---|---|---|
| W17 | {"behaviour":"refuse","reason":"Assets under management and assets under advice are different measures: they are not added."} | {"behaviour":"abstain","no_figure":true,"reason":"The checked plan has no general addition operation for different measures; no operand is returned alone."} |
| W20 | {"behaviour":"refuse","reason":"Net new assets and revenue are different measures: they are not added."} | {"behaviour":"abstain","no_figure":true,"reason":"The checked plan has no general addition operation for different measures; no operand is returned alone."} |
| XB2 | {"behaviour":"parts_with_reason","commensurable":false,"reason":"Deposits (retail) and AUM (wealth) are different measures: the parts and the AUM share, not a total.","parts":{"retail":252002.68,"wealth":101566960.12},"ratio":0.9975} | {"behaviour":"abstain","no_figure":true} |

## G005 certified document declaration

`clients/meridian/fixture/docs/certified/MD-AVERAGE-BALANCE.md` changes the certified table’s grain, currency and calendar from `n/a / n/a / n/a` to `day / USD / calendar`. This was made for G005’s approved average-balance declaration. It is a certified source edit and is named separately from compiler behaviour and golden-expectation changes. G005’s accepted figure remains unchanged.

## Round 9d figures

The new test-only declared premium-per-policy ratio and the flow/snapshot alignment now execute; the percent-change oracle now gives `(30 − 20) / 20 = 0.5` instead of the incorrectly compiled `30 − 20 / 20 = 29`. These are new regression cases, not changes to committed golden figures. The current golden, parity and margin outcomes are recorded in the worker check report.

## Round 9d policy refusal decisions

Review item 8 explicitly requires the Planner to refuse the pack’s restricted phrases independently of core routing. Five existing native-budget cases therefore move from `abstain` to `refuse`, with no figure in either case and zero answer jobs unchanged:

- Number of complaints in June 2025 older than 30 days.
- Credit exposure for June 2025 on facilities older than 5 years.
- Days past due older than 90 days in June 2025.
- Fee income for June 2025 by age band.
- NNA for March 2025 as reported, and the age of the data.

Meridian’s restricted-date policy explicitly declares `older than` and `age` as phrases for the restricted attribute. This round follows that declaration; it does not infer an alternative meaning from the subject of the sentence. These are refusal changes under review item 8, not golden-expectation or figure changes.

## Round 9d current Agent diagnostic

Review item 1 removes the default Context classifier’s numeric exception for the word “clients”. For `golden_wealth_v1:XB1:treatment:rules`, behaviour stays `abstain`, with no figure or execution. The old message, “The question counts nothing the pack declares an entity, and Assets under management is a figure, not a count of its rows”, is replaced by “The requested measure is not certified for every named source. No figure is given.” The legacy Context-only `context` diagnostic is also removed. This is a pack/checker diagnostic, not an English population override.

Only this key’s current `agent_query_plan_v1.json` seal is updated, with its previous current seal and this decision retained in the manifest’s `round9d` field. The historical `agent_f75605a.json` and default Context seal are unchanged. Standalone model prompts retain their existing schema; the added deletion state is exposed only with numeric history. `round9d-agent-diagnostic-change.json` preserves the before/after diagnostic evidence.

## Round 9e correction to the five policy near misses

The round 9d refusal paragraph above is superseded. The five questions are the existing another-sense cases in `test_cross_unit.py`; all return to abstention, with no figure and zero warehouse executions:

| Question | Why a restricted-data refusal is not justified |
|---|---|
| Number of complaints in June 2025 older than 30 days | The elapsed age of a complaint is not a customer's birth date. |
| Credit exposure for June 2025 on facilities older than 5 years | Facility vintage is not personal age. |
| Days past due older than 90 days in June 2025 | Delinquency duration is not personal age. |
| Fee income for June 2025 by age band | The existing near-miss case does not resolve a restricted column or a population for its unsupported breakdown; the planner must abstain without choosing a personal-data interpretation. |
| NNA for March 2025 as reported, and the age of the data | Data freshness is not personal age. |

The vocabulary independently checks the request against the same unit-sensitive restricted-name rule as governed Ask. A standalone request for birthdate, and a cross-unit request for Retail clients older than 60, still refuse before interpretation. Bancassurance policies older than one year still abstain rather than inheriting Retail's personal-data restriction. No expected figure, golden file, parity statement, baseline or answer seal is changed in round 9e.
