---
title: Wealth Management Data Dictionary
kind: data_dictionary
unit: wealth
owner: Wealth Management Data Office
version: '3.2'
published_at: '2025-02-01'
reviewed_at: '2025-06-02'
planted: []
---
Certified data dictionary for the Wealth Management warehouse. The customer-like key in this warehouse is `household_id`. It joins another unit only by the certified client cross-reference.

## fiscal_calendar

| Column | Type | Description |
|---|---|---|
| fiscal_quarter | VARCHAR | Fiscal quarter label, e.g. FY25-Q1 |
| fiscal_year | INTEGER | Fiscal year, starting 1 April |
| quarter_start | DATE | First day of the fiscal quarter |
| quarter_end | DATE | Last day of the fiscal quarter |

## advisor

| Column | Type | Description |
|---|---|---|
| advisor_id | INTEGER | Advisor identifier |
| advisor_name | VARCHAR | Advisor name |
| office_code | VARCHAR | Office |
| licence_class | VARCHAR | Licence class held |

## household

| Column | Type | Description |
|---|---|---|
| household_id | INTEGER | Household identifier |
| household_name | VARCHAR | Household display name |
| primary_advisor_id | INTEGER | Primary advisor |
| risk_profile | VARCHAR | CAUTIOUS, BALANCED, GROWTH |
| onboarded_on | DATE | Onboarding date |

Joins: `primary_advisor_id` references `advisor.advisor_id`

## portfolio

| Column | Type | Description |
|---|---|---|
| portfolio_id | INTEGER | Portfolio identifier |
| household_id | INTEGER | Owning household |
| mandate_type | VARCHAR | DISCRETIONARY or ADVISORY |
| base_currency | VARCHAR | Portfolio base currency, USD |
| inception_on | DATE | Inception date |

Joins: `household_id` references `household.household_id`

## holding

| Column | Type | Description |
|---|---|---|
| holding_id | BIGINT | Row identifier |
| portfolio_id | INTEGER | Portfolio |
| as_of | DATE | Valuation date |
| instrument_id | VARCHAR | Instrument identifier |
| quantity | DECIMAL(18,4) | Units held |
| market_value | DECIMAL(16,2) | Market value in base currency |

Joins: `portfolio_id` references `portfolio.portfolio_id`

## quarterly_fee_revenue

| Column | Type | Description |
|---|---|---|
| fee_id | INTEGER | Row identifier |
| household_id | INTEGER | Household |
| fiscal_quarter | VARCHAR | Fiscal quarter |
| advisory_fee_usd | DECIMAL(14,2) | Advisory fees, USD |
| product_fee_usd | DECIMAL(14,2) | Product fees, USD |
| revenue_usd | DECIMAL(14,2) | Advisory plus product fees, USD, fiscal quarter |

Joins: `household_id` references `household.household_id`; `fiscal_quarter` references `fiscal_calendar.fiscal_quarter`

## assets_under_advice

| Column | Type | Description |
|---|---|---|
| aua_id | INTEGER | Row identifier |
| household_id | INTEGER | Household |
| as_of | DATE | Valuation date, quarter end |
| aua_usd | DECIMAL(16,2) | Assets under advice, USD |

Joins: `household_id` references `household.household_id`

## suitability_review

| Column | Type | Description |
|---|---|---|
| review_id | INTEGER | Review identifier |
| household_id | INTEGER | Household reviewed |
| reviewed_on | DATE | Review date |
| outcome_code | VARCHAR | SUITABLE, ACTION_REQUIRED, ESCALATED |
| reviewer_id | INTEGER | Reviewing advisor |

Joins: `household_id` references `household.household_id`; `reviewer_id` references `advisor.advisor_id`

Expansion tables: `month_end`, `division`, `region`, `office`, `client_segment`, `ref_source_system`, `ref_flow_type`, `account` and `flow` (SCD2: `valid_from`, `valid_to`, `is_current`), `nna_exclusion`, `household_member`, `aum_month_end`. Cross-reference: `client_xref`, outside this warehouse, `household_id` to `customer_id`. Physical names since 1.5.0: the data catalogue's.

## division

| Column | Type | Description |
|---|---|---|
| division_code | VARCHAR | Division code |
| division_name | VARCHAR | Division name |
| valid_from | DATE | First day this version of the row holds |
| valid_to | DATE | Last day this version of the row holds, null while it is current |
| is_current | BOOLEAN | True for the version that holds today |

## region

| Column | Type | Description |
|---|---|---|
| region_code | VARCHAR | Region code |
| region_name | VARCHAR | Region name |
| division_code | VARCHAR | Division the region rolls up to |
| valid_from | DATE | First day this version of the row holds |
| valid_to | DATE | Last day this version of the row holds, null while it is current |
| is_current | BOOLEAN | True for the version that holds today |

## client_segment

| Column | Type | Description |
|---|---|---|
| segment_code | VARCHAR | Segment code |
| segment_name | VARCHAR | Segment name |
| parent_segment_code | VARCHAR | Parent segment, null at the top level |
| level | INTEGER | Level in the segment hierarchy, 1 at the top |

## ref_flow_type

| Column | Type | Description |
|---|---|---|
| flow_type_code | VARCHAR | Flow type code |
| description | VARCHAR | What the flow type covers |
| direction | VARCHAR | IN or OUT |
| nna_counts | BOOLEAN | True where flows of this type count towards net new assets |

## household_member

| Column | Type | Description |
|---|---|---|
| household_id | INTEGER | Household |
| member_id | INTEGER | Member of the household |
| member_role | VARCHAR | PRIMARY, JOINT or DEPENDENT |

Joins: `household_id` references `household.household_id`

## SRCE_SYS_1

| Column | Type | Description |
|---|---|---|
| SRCE_SYS_ID | BIGINT | Source system id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| SRCE_SYS_NM | VARCHAR | Source system name |
| INSERT_TS | TIMESTAMP | Insert timestamp |
| AFLT_ACCT_CMPY_CD | VARCHAR | Affiliate account company code |
| INSERT_USER_NM | VARCHAR | Insert user name |
| MODIFY_TS | TIMESTAMP | Modify timestamp |
| MODIFY_USER_NM | VARCHAR | Modify user name |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## MONTH_DIM_1

| Column | Type | Description |
|---|---|---|
| CLDR_EOM_DT | DATE | Calendar eom date |
| CLDR_EOM_TS | TIMESTAMP | Calendar eom timestamp |
| YEAR_MONTH | BIGINT | Year month |
| YEAR_QTR | VARCHAR | Year quarter |
| BUSN_EOM_TS | TIMESTAMP | Business eom timestamp |
| BUSN_EOM_DT | DATE | Business eom date |
| MNTH_START_CLDR_DT | DATE | Month start calendar date |
| TIME_PRD_GRAIN_CD | VARCHAR | Time period grain code |
| FIRST_MUB_WRK_DAY_OF_MNTH_DT | DATE | First mub work day of month date |
| MNTH_NM | VARCHAR | Month name |
| MNTH_CD | VARCHAR | Month code |
| MNTH_YYYY_TX | VARCHAR | Month yyyy tax |
| MNTH_OF_YEAR_CT | BIGINT | Month of year ct |
| DW_BRKRG_MNTH_FRM_TODAY_CT | BIGINT | DW brokerage month form today ct |
| FIRST_EXCH_OPEN_DAY_OF_MNTH_DT | DATE | First exchange open day of month date |
| LAST_EXCH_OPEN_DAY_OF_MNTH_DT | DATE | Last exchange open day of month date |
| TOT_CLDR_DAY_OF_MNTH_NR | BIGINT | Total calendar day of month number |
| TOT_EXCH_OPEN_DAY_OF_MNTH_NR | BIGINT | Total exchange open day of month number |
| TOT_BANK_OPEN_DAY_OF_MNTH_NR | BIGINT | Total bank open day of month number |
| TOT_MUB_WRK_DAY_OF_MNTH_NR | BIGINT | Total mub work day of month number |
| TOT_MUB_NON_WRK_DAY_OF_MNTH_NR | BIGINT | Total mub non work day of month number |
| QRTR_START_CLDR_DT | DATE | Quarter start calendar date |
| QRTR_TIME_PRD_GRAIN_CD | VARCHAR | Quarter time period grain code |
| YEAR_START_CLDR_DT | DATE | Year start calendar date |
| YEAR_TIME_PRD_GRAIN_CD | VARCHAR | Year time period grain code |
| TOT_MNTH_EXCH_OPEN_DAY_CT | BIGINT | Total month exchange open day ct |
| FIN_MTRC_RLSE_DT | DATE | Financial metric release date |

## BRCH_DIM_1

| Column | Type | Description |
|---|---|---|
| BRCH_CD | VARCHAR | Branch code |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| BRCH_NM | VARCHAR | Branch name |
| BRCH_OPEN_DT | DATE | Branch open date |
| BRCH_CLOS_DT | DATE | Branch close date |
| BRCH_TYPE_CD | VARCHAR | Branch type code |
| BRCH_STAT_CD | VARCHAR | Branch status code |
| BRCH_ORG_ID | VARCHAR | Branch organization id |
| COST_CENTR_CD | VARCHAR | Cost center code |
| PARNT_BRCH_CD | VARCHAR | Parent branch code |
| PARNT_BRCH_NM | VARCHAR | Parent branch name |
| PARNT_BRCH_ORG_ID | VARCHAR | Parent branch organization id |
| RGN_CD | VARCHAR | Region code |
| RGN_NM | VARCHAR | Region name |
| RGN_BRCH_EXCTV_NM | VARCHAR | Region branch executive name |
| RGN_BRCH_EXCTV_MUB_PRSN_ID | VARCHAR | Region branch executive mub person id |
| BRCH_MGR_MUB_PRSN_ID | VARCHAR | Branch manager mub person id |
| BRCH_MGR_NM | VARCHAR | Branch manager name |
| MKT_LEADER_MUB_PRSN_ID | VARCHAR | Market leader mub person id |
| MKT_LEADER_NM | VARCHAR | Market leader name |
| FRNCHS_BUSN_DIRTR_MUB_PRSN_ID | VARCHAR | Franchise business director mub person id |
| FRNCHS_BUSN_DIRTR_NM | VARCHAR | Franchise business director name |
| NEAREST_CMPY_MNGD_BRCH_CD | VARCHAR | Nearest company managed branch code |
| AREA_CD | VARCHAR | Area code |
| AREA_NM | VARCHAR | Area name |
| BRCH_DIVSN_CD | VARCHAR | Branch division code |
| BRCH_DIVSN_NM | VARCHAR | Branch division name |
| PHON_ID | VARCHAR | Phone id |
| EMAIL_ADDR_TX | VARCHAR | Email address tax |
| ADDR_LINE1_TX | VARCHAR | Address line1 tax |
| ADDR_LINE2_TX | VARCHAR | Address line2 tax |
| CITY_NM | VARCHAR | City name |
| STATE_CD | VARCHAR | State code |
| POSTL_CD | VARCHAR | Postal code |
| BRCH_SRCE_SYS_NM | VARCHAR | Branch source system name |
| SRCE_SYS_ID | BIGINT | Source system id |
| INSERT_TS | TIMESTAMP | Insert timestamp |
| INSERT_USER_NM | VARCHAR | Insert user name |
| MODIFY_TS | TIMESTAMP | Modify timestamp |
| MODIFY_USER_NM | VARCHAR | Modify user name |
| DW_AUTO_SIGNUP_IN | VARCHAR | DW auto signup indicator |
| BRCH_ORIG_CD | VARCHAR | Branch original code |
| DW_CREAT_TS | TIMESTAMP | DW create timestamp |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## ACCT_DIM2_1

| Column | Type | Description |
|---|---|---|
| DW_ACCT_ID | BIGINT | DW account id |
| SRCE_SYS_ID | BIGINT | Source system id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| SRCE_ACCT_ID | VARCHAR | Source account id |
| TXPYR_CORP_CUST_ID | BIGINT | Taxpayer corporate customer id |
| HSHLD_DK | BIGINT | Household key |
| LAST_RCV_HSHLD_DK | BIGINT | Last receive household key |
| CUST_GRP_ID | BIGINT | Customer group id |
| LAST_RCV_CUST_GRP_ID | BIGINT | Last receive customer group id |
| ACCT_DOMCL_BRCH_CD | VARCHAR | Account domicile branch code |
| CLNT_CD | VARCHAR | Client code |
| EFF_CLNT_CD | VARCHAR | Effective client code |
| ACCT_DETL_PROD_CD | VARCHAR | Account detail product code |
| ACTIV_CD | VARCHAR | Activity code |
| ACCT_ACTIV_CD | VARCHAR | Account activity code |
| ACCT_STAT_CD | VARCHAR | Account status code |
| ACCT_CT | BIGINT | Account ct |
| STATE_CD | VARCHAR | State code |
| POSTL_CD | VARCHAR | Postal code |
| ACCT_OPEN_DT | DATE | Account open date |
| ACCT_OPEN_TM | VARCHAR | Account open time |
| ACCT_OPEN_TS | TIMESTAMP | Account open timestamp |
| ACCT_CLOS_DT | DATE | Account close date |
| PRIM_TXPYR_ID | VARCHAR | Primary taxpayer id |
| HSHLD_OPEN_DT | DATE | Household open date |
| FIRM_HSHLD_OPEN_DT | DATE | Firm household open date |
| ACCT_NM | VARCHAR | Account name |
| TEST_ACCT_IN | VARCHAR | Test account indicator |
| INTRN_ACCT_IN | VARCHAR | Internal account indicator |
| INTRN_ACCT_SUB_TYPE_CD | VARCHAR | Internal account sub type code |
| SMRY_ACCT_IN | VARCHAR | Smry account indicator |
| MSTR_ACCT_IN | VARCHAR | Master account indicator |
| SUB_ACCT_IN | VARCHAR | Sub account indicator |
| DUM_ACCT_IN | VARCHAR | Dum account indicator |
| NEW_ACCT_FRAUD_IN | VARCHAR | New account fraud indicator |
| TXPYR_CUST_ID | BIGINT | Taxpayer customer id |
| ACCT_LOB_CD | VARCHAR | Account lob code |
| FA_MSTR_ACCT_ID | BIGINT | Fa master account id |
| EFF_FA_MSTR_ACCT_ID | BIGINT | Effective fa master account id |
| EFF_FA_MSTR_ACCT_DT | DATE | Effective fa master account date |
| BT_MSTR_ACCT_ID | BIGINT | Bt master account id |
| K4_MSTR_ACCT_ID | BIGINT | K4 master account id |
| ADV_ID | BIGINT | Advisor id |
| FIRM_ID | BIGINT | Firm id |
| SRCE_ACCT_RGSTR_TYPE_CD | VARCHAR | Source account registration type code |
| CASH_PRCDS_CSTDY_CD | VARCHAR | Cash proceeds custody code |
| CASH_PRCDS_CSTDY_DESC_TX | VARCHAR | Cash proceeds custody description tax |
| CASH_DIST_CSTDY_CD | VARCHAR | Cash distribution custody code |
| CASH_DIST_CSTDY_DESC_TX | VARCHAR | Cash distribution custody description tax |
| CASH_DIST_MAIL_SCHED_CD | VARCHAR | Cash distribution mail schedule code |
| CASH_DIST_MAIL_SCHED_DESC_TX | VARCHAR | Cash distribution mail schedule description tax |
| CASH_ACCT_FUND_INSTR_CD | VARCHAR | Cash account fund instruction code |
| CASH_STK_CSTDY_CD | VARCHAR | Cash stock custody code |
| CASH_STK_CSTDY_DESC_TX | VARCHAR | Cash stock custody description tax |
| CASH_ACCT_SEC_INSTR_CD | VARCHAR | Cash account security instruction code |
| CASH_ACCT_SEC_INSTR_DESC_TX | VARCHAR | Cash account security instruction description tax |
| CASH_ACCT_PRCDS_INSTR_DESC_TX | VARCHAR | Cash account proceeds instruction description tax |
| CASH_ACCT_DIV_INSTR_DESC_TX | VARCHAR | Cash account dividend instruction description tax |
| LGCY_CAMP_CD | VARCHAR | Legacy camp code |
| VIP_CD | VARCHAR | Vip code |
| APPR_OPTN_LVL_CD | VARCHAR | Approval option level code |
| APPR_OPTN_LVL_DESC_TX | VARCHAR | Approval option level description tax |
| BRKR_DEALR_ACCT_ID | BIGINT | Broker dealr account id |
| CROSS_SYS_PRIM_ACCT_IN | VARCHAR | Cross system primary account indicator |
| ACCT_RELT_DBS_IN | VARCHAR | Account relationship dbs indicator |
| ACCT_RELT_STKPLN_IN | VARCHAR | Account relationship stkpln indicator |
| CS_INTRN_IN | VARCHAR | Cs internal indicator |
| CS_STKPLN_CATG_CD | VARCHAR | Cs stkpln category code |
| ACCT_OPEN_30_DAY_EXPR_DT | DATE | Account open 30 day expiry date |
| ACCT_OPEN_90_DAY_EXPR_DT | DATE | Account open 90 day expiry date |
| ACCT_OPEN_180_DAY_EXPR_DT | DATE | Account open 180 day expiry date |
| ACCT_OPEN_365_DAY_EXPR_DT | DATE | Account open 365 day expiry date |
| ACCT_OPEN_1_MNTH_EXPR_DT | DATE | Account open 1 month expiry date |
| ACCT_OPEN_3_MNTH_EXPR_DT | DATE | Account open 3 month expiry date |
| ACCT_OPEN_6_MNTH_EXPR_DT | DATE | Account open 6 month expiry date |
| ACCT_OPEN_12_MNTH_EXPR_DT | DATE | Account open 12 month expiry date |
| MUB_SRVC_ORG_ID | VARCHAR | Mub service organization id |
| STKPLN_ACCT_PRIM_CMPY_ID | BIGINT | Stock plan account primary company id |
| STKPLN_ACCT_PRIM_CMPY_NM | VARCHAR | Stock plan account primary company name |
| STKPLN_ACCT_PRIM_CMPY_TICKER_CD | VARCHAR | Stock plan account primary company ticker code |
| CS_ACCT_PRIM_MSTR_ACCT_ID | BIGINT | Cs account primary master account id |
| CS_ACCT_PRIM_FIRM_ID | BIGINT | Cs account primary firm id |
| CS_ACCT_PRIM_FIRM_NM | VARCHAR | Cs account primary firm name |
| ADV_SRVC_SEGMT_NM | VARCHAR | Advisor service segment name |
| ADV_SRVC_SUB_SEGMT_NM | VARCHAR | Advisor service sub segment name |
| ADV_SRVC_IN | VARCHAR | Advisor service indicator |
| TOP_LVL_SEGMT_NM | VARCHAR | Top level segment name |
| PROD_SEGMT_NM | VARCHAR | Product segment name |
| ACCT_ORIG_CD | VARCHAR | Account original code |
| ACCT_ORIG_DETL_CD | VARCHAR | Account original detail code |
| LGCY_ASSET_MVMNT_CD | VARCHAR | Legacy asset movement code |
| LGCY_SHARED_CUST_ROLE_TYPE_CD | VARCHAR | Legacy shared customer role type code |
| EFF_ACCT_OPEN_DT | DATE | Effective account open date |
| EFF_HSHLD_OPEN_DT | DATE | Effective household open date |
| JRSDCTN_CUST_ID | BIGINT | Jurisdiction customer id |
| JRSDCTN_CNTRY_CD | VARCHAR | Jurisdiction country code |
| JRSDCTN_STATE_CD | VARCHAR | Jurisdiction state code |
| ACCT_OMS_TYPE_NM | VARCHAR | Account oms type name |
| ACCT_OMS_SW_DT | DATE | Account oms sw date |
| ACCT_TRADR_SEGMT_NM | VARCHAR | Account trader segment name |
| ACCT_RETL_SEGMT_NM | VARCHAR | Account retail segment name |
| TAX_ADVTGD_IN | VARCHAR | Tax advtgd indicator |
| ASSET_TIER_SEGMT_NM | VARCHAR | Asset tier segment name |
| DW_ACCT_DK | BIGINT | DW account key |
| TXPYR_CUST_DK | BIGINT | Taxpayer customer key |
| FA_MSTR_ACCT_DK | BIGINT | Fa master account key |
| EFF_FA_MSTR_ACCT_DK | BIGINT | Effective fa master account key |
| BT_MSTR_ACCT_DK | BIGINT | Bt master account key |
| K4_MSTR_ACCT_DK | BIGINT | K4 master account key |
| ADV_DK | BIGINT | Advisor key |
| BRKR_DEALR_ACCT_DK | BIGINT | Broker dealr account key |
| CS_ACCT_PRIM_MSTR_ACCT_DK | BIGINT | Cs account primary master account key |
| JRSDCTN_CUST_DK | BIGINT | Jurisdiction customer key |
| DW_CREAT_TS | TIMESTAMP | DW create timestamp |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

Joins: `SRCE_SYS_ID` references `SRCE_SYS_1.SRCE_SYS_ID`

## TRAN_FACT_1

| Column | Type | Description |
|---|---|---|
| DW_TRAN_ID | BIGINT | DW transaction id |
| ACCT_SRCE_SYS_ID | BIGINT | Account source system id |
| DW_ACCT_ID | BIGINT | DW account id |
| DW_ACCT_DK | BIGINT | DW account key |
| DW_TRAN_DT | DATE | DW transaction date |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| MNTH_END_DT | DATE | Month end date |
| DW_TRAN_TYPE_CD | VARCHAR | DW transaction type code |
| DW_TRAN_DIRCTN_CD | VARCHAR | DW transaction direction code |
| TRANS_SRCE_SYS_ID | BIGINT | Transaction source system id |
| ITEM_ISS_ID | BIGINT | Item issue id |
| RULE_SET_SUFF_ID | BIGINT | Rule set suffix id |
| TRAN_EXEC_DT | DATE | Transaction execution date |
| TRAN_SETTL_DT | DATE | Transaction settlement date |
| TRAN_SHR_QY | DECIMAL(18,4) | Transaction share qy |
| TRAN_EXEC_PRICE_PER_SHR_AM | DECIMAL(16,2) | Transaction execution price per share amount |
| TRAN_AM | DECIMAL(16,2) | Transaction amount |
| TRAN_ROST_TYPE_CD | VARCHAR | Transaction rost type code |
| TRAN_ROST_ID | BIGINT | Transaction rost id |
| TRAN_ADD_TS | TIMESTAMP | Transaction add timestamp |
| TRAN_PROC_TS | TIMESTAMP | Transaction process timestamp |
| OFSET_ACCT_ID | BIGINT | Offset account id |
| TRAN_EFF_DT | DATE | Transaction effective date |
| TRAN_EXEC_TS | TIMESTAMP | Transaction execution timestamp |
| OFSET_SRCE_SYS_ID | BIGINT | Offset source system id |
| NNA_IN | VARCHAR | Net new assets indicator |
| NAF_IN | VARCHAR | Naf indicator |
| FIN_MTRC_RLSE_FRZN_IN | VARCHAR | Financial metric release frozen indicator |
| TXPYR_CUST_DK | BIGINT | Taxpayer customer key |
| HSHLD_DK | BIGINT | Household key |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

Joins: `DW_TRAN_TYPE_CD` references `TRAN_TYPE_1.DW_TRAN_TYPE_CD`; `ACCT_SRCE_SYS_ID` references `SRCE_SYS_1.SRCE_SYS_ID`; `MNTH_END_DT` references `MONTH_DIM_1.CLDR_EOM_DT`

## CLIENT_CD_ROLLUP_1

| Column | Type | Description |
|---|---|---|
| CLNT_CD | VARCHAR | Client code |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| FILL_PARNT_HIER_LVL_NR | BIGINT | Fill parent hierarchy level number |
| SEGMT_LVL_ID1_INODE_IN | BIGINT | Segment level id1 inode indicator |
| SEGMT_LVL_ID1_FILL_IN | BIGINT | Segment level id1 fill indicator |
| SEGMT_LVL_ID1 | VARCHAR | Segment level id1 |
| SEGMT_LVL_NM1 | VARCHAR | Segment level nm1 |
| SEGMT_LVL_SHRT_NM1 | VARCHAR | Segment level short nm1 |
| SEGMT_LVL_ULTRA_SHRT_NM1 | VARCHAR | Segment level ultra short nm1 |
| SEGMT_LVL_SORT_SEQ_NR1 | BIGINT | Segment level sort sequence nr1 |
| SEGMT_LVL_ID2_INODE_IN | BIGINT | Segment level id2 inode indicator |
| SEGMT_LVL_ID2_FILL_IN | BIGINT | Segment level id2 fill indicator |
| SEGMT_LVL_ID2 | VARCHAR | Segment level id2 |
| SEGMT_LVL_NM2 | VARCHAR | Segment level nm2 |
| SEGMT_LVL_SHRT_NM2 | VARCHAR | Segment level short nm2 |
| SEGMT_LVL_ULTRA_SHRT_NM2 | VARCHAR | Segment level ultra short nm2 |
| SEGMT_LVL_SORT_SEQ_NR2 | BIGINT | Segment level sort sequence nr2 |
| SEGMT_LVL_ID3_INODE_IN | BIGINT | Segment level id3 inode indicator |
| SEGMT_LVL_ID3_FILL_IN | BIGINT | Segment level id3 fill indicator |
| SEGMT_LVL_ID3 | VARCHAR | Segment level id3 |
| SEGMT_LVL_NM3 | VARCHAR | Segment level nm3 |
| SEGMT_LVL_SHRT_NM3 | VARCHAR | Segment level short nm3 |
| SEGMT_LVL_ULTRA_SHRT_NM3 | VARCHAR | Segment level ultra short nm3 |
| SEGMT_LVL_SORT_SEQ_NR3 | BIGINT | Segment level sort sequence nr3 |
| SEGMT_LVL_ID4_INODE_IN | BIGINT | Segment level id4 inode indicator |
| SEGMT_LVL_ID4_FILL_IN | BIGINT | Segment level id4 fill indicator |
| SEGMT_LVL_ID4 | VARCHAR | Segment level id4 |
| SEGMT_LVL_NM4 | VARCHAR | Segment level nm4 |
| SEGMT_LVL_SHRT_NM4 | VARCHAR | Segment level short nm4 |
| SEGMT_LVL_ULTRA_SHRT_NM4 | VARCHAR | Segment level ultra short nm4 |
| SEGMT_LVL_SORT_SEQ_NR4 | BIGINT | Segment level sort sequence nr4 |
| SEGMT_LVL_ID5_INODE_IN | BIGINT | Segment level id5 inode indicator |
| SEGMT_LVL_ID5_FILL_IN | BIGINT | Segment level id5 fill indicator |
| SEGMT_LVL_ID5 | VARCHAR | Segment level id5 |
| SEGMT_LVL_NM5 | VARCHAR | Segment level nm5 |
| SEGMT_LVL_ULTRA_SHRT_NM5 | VARCHAR | Segment level ultra short nm5 |
| SEGMT_LVL_SHRT_NM5 | VARCHAR | Segment level short nm5 |
| SEGMT_LVL_SORT_SEQ_NR5 | BIGINT | Segment level sort sequence nr5 |
| SEGMT_LVL_ID6_INODE_IN | BIGINT | Segment level id6 inode indicator |
| SEGMT_LVL_ID6_FILL_IN | BIGINT | Segment level id6 fill indicator |
| SEGMT_LVL_ID6 | VARCHAR | Segment level id6 |
| SEGMT_LVL_NM6 | VARCHAR | Segment level nm6 |
| SEGMT_LVL_SHRT_NM6 | VARCHAR | Segment level short nm6 |
| SEGMT_LVL_ULTRA_SHRT_NM6 | VARCHAR | Segment level ultra short nm6 |
| SEGMT_LVL_SORT_SEQ_NR6 | BIGINT | Segment level sort sequence nr6 |
| SEGMT_LVL_ID7_INODE_IN | BIGINT | Segment level id7 inode indicator |
| SEGMT_LVL_ID7_FILL_IN | BIGINT | Segment level id7 fill indicator |
| SEGMT_LVL_ID7 | VARCHAR | Segment level id7 |
| SEGMT_LVL_NM7 | VARCHAR | Segment level nm7 |
| SEGMT_LVL_SHRT_NM7 | VARCHAR | Segment level short nm7 |
| SEGMT_LVL_ULTRA_SHRT_NM7 | VARCHAR | Segment level ultra short nm7 |
| SEGMT_LVL_SORT_SEQ_NR7 | BIGINT | Segment level sort sequence nr7 |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## DATE_DIM_1

| Column | Type | Description |
|---|---|---|
| CALENDAR_DATE | DATE | Calendar date |
| CALENDAR_EOD_DATE_TS | TIMESTAMP | Calendar eod date timestamp |
| CALENDAR_DATE_TS | TIMESTAMP | Calendar date timestamp |
| YEAR_MONTH | BIGINT | Year month |
| YEAR_VAL | BIGINT | Year value |
| DAY_OF_YEAR | BIGINT | Day of year |
| DAY_OF_WEEK | VARCHAR | Day of week |
| DAY_OF_WEEK_NUMBER | BIGINT | Day of week number |
| WEEK_NUMBER | BIGINT | Week number |
| MONTH_DAY_NUMBER | BIGINT | Month day number |
| MONTH_NAME | VARCHAR | Month name |
| MONTH_NUMBER | BIGINT | Month number |
| CALENDAR_MONTH_END_DAY | BIGINT | Calendar month end day |
| QUARTER_NUMBER | BIGINT | Quarter number |
| CALENDAR_QRTR_END_DAY | BIGINT | Calendar quarter end day |
| FISCAL_WEEK_END_DAY | BIGINT | Fiscal week end day |
| FISCAL_MONTH_END_DAY | BIGINT | Fiscal month end day |
| FISCAL_QRTR_END_DAY | BIGINT | Fiscal quarter end day |
| FISCAL_YEAR_END_DAY | BIGINT | Fiscal year end day |
| BUSINESS_WEEK_END_DAY | BIGINT | Business week end day |
| BUSINESS_MONTH_END_DAY | BIGINT | Business month end day |
| BUSINESS_QRTR_END_DAY | BIGINT | Business quarter end day |
| BUSINESS_YEAR_END_DAY | BIGINT | Business year end day |
| HR_PAY_PERIOD_END_DAY | BIGINT | Hr pay period end day |
| HR_MONTH_END_DAY | BIGINT | Hr month end day |
| HR_QRTR_END_DAY | BIGINT | Hr quarter end day |
| HR_YEAR_END_DAY | BIGINT | Hr year end day |
| HOLIDAY | BIGINT | Holiday |
| WEEK_OF_MONTH_NUMBER | BIGINT | Week of month number |
| EXCH_OPEN_IN | BIGINT | Exchange open indicator |
| SETTL_DAY_IN | BIGINT | Settlement day indicator |
| FED_RSRV_BANK_OPEN_IN | BIGINT | Fed reserve bank open indicator |
| TIME_PRD_GRAIN_CD | VARCHAR | Time period grain code |
| PREV_EXCH_OPEN_DT | DATE | Previous exchange open date |
| NXT_EXCH_OPEN_DT | DATE | Next exchange open date |
| RPTG_EXCH_OPEN_DT | DATE | Reporting exchange open date |
| PREV_MUB_WRK_DT | DATE | Previous mub work date |
| NEXT_MUB_WRK_DT | DATE | Next mub work date |
| PREV_MUB_WRK_FRI_DT | DATE | Previous mub work fri date |
| NEXT_MUB_WRK_FRI_DT | DATE | Next mub work fri date |
| MUB_WRK_MTD_CT | BIGINT | Mub work mtd ct |
| MUB_WRK_QTD_CT | BIGINT | Mub work qtd ct |
| MUB_WRK_YTD_CT | BIGINT | Mub work ytd ct |
| MNTH_START_CLDR_DT | DATE | Month start calendar date |
| MNTH_TIME_PRD_GRAIN_CD | VARCHAR | Month time period grain code |
| QRTR_START_CLDR_DT | DATE | Quarter start calendar date |
| QRTR_TIME_PRD_GRAIN_CD | VARCHAR | Quarter time period grain code |
| YEAR_START_CLDR_DT | DATE | Year start calendar date |
| YEAR_TIME_PRD_GRAIN_CD | VARCHAR | Year time period grain code |
| EXCH_DAY_CT | BIGINT | Exchange day ct |
| MNTH_END_CLDR_DT | DATE | Month end calendar date |
| CLDR_1_MOST_RCNT_DT | DATE | Calendar 1 most recent date |
| CLDR_2_MOST_RCNT_DT | DATE | Calendar 2 most recent date |
| SPCL_HLDY_NM | VARCHAR | Special holiday name |
| FIN_MTRC_PBLSHNG_DT_IN | VARCHAR | Financial metric publishing date indicator |

## D_ACCT_SEGMT

| Column | Type | Description |
|---|---|---|
| DW_ACCT_SEGMT_ID | BIGINT | DW account segment id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| DW_SEGMT_GRP_ID | BIGINT | DW segment group id |
| ELT_ID | BIGINT | Elt id |
| SEGMT_NM | VARCHAR | Segment name |
| SEGMT_DESC_TX | VARCHAR | Segment description tax |
| SEGMT_LVL_TX | VARCHAR | Segment level tax |
| SEGMT_START_DT | DATE | Segment start date |
| SEGMT_END_DT | DATE | Segment end date |
| SEGMT_ACTIV_IN | VARCHAR | Segment activity indicator |
| INCL_IN_DATA_LOAD_IN | VARCHAR | Include indicator data load indicator |
| MNGD_INVSTG_IN | VARCHAR | Managed invstg indicator |
| RPT_SEGMT_NM | VARCHAR | Report segment name |
| INSERT_TS | TIMESTAMP | Insert timestamp |
| INSERT_USER_NM | VARCHAR | Insert user name |
| MODIFY_TS | TIMESTAMP | Modify timestamp |
| MODIFY_USER_NM | VARCHAR | Modify user name |
| APPR_USER_TS | TIMESTAMP | Approval user timestamp |
| APPR_USER_NM | VARCHAR | Approval user name |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## F_ACCT_SEGMT_ENRLMT

| Column | Type | Description |
|---|---|---|
| DW_ACCT_ID | BIGINT | DW account id |
| SRCE_SYS_ID | BIGINT | Source system id |
| DW_SEGMT_GRP_ID | BIGINT | DW segment group id |
| DW_ACCT_SEGMT_ID | BIGINT | DW account segment id |
| ACCT_ENRLMT_EFF_DT | DATE | Account enrlmt effective date |
| ACCT_ENRLMT_EXPR_DT | DATE | Account enrlmt expiry date |
| ACCT_ENRLMT_ACTIV_IN | VARCHAR | Account enrlmt activity indicator |
| SEGMT_NM | VARCHAR | Segment name |
| SEGMT_GRP_NM | VARCHAR | Segment group name |
| DW_SRCE_TBL_NM | VARCHAR | DW source table name |
| DW_CREAT_TS | TIMESTAMP | DW create timestamp |
| DW_UPDT_TS | TIMESTAMP | DW update timestamp |

Joins: `DW_ACCT_SEGMT_ID` references `D_ACCT_SEGMT.DW_ACCT_SEGMT_ID`

## D_CORE_OFFER

| Column | Type | Description |
|---|---|---|
| CORE_OFFER_DK | BIGINT | Core offer key |
| OFFER_START_DT | DATE | Offer start date |
| OFFER_END_DT | DATE | Offer end date |
| OFFER_AVAL_FOR_NEW_ENRLMT_IN | VARCHAR | Offer available for new enrlmt indicator |
| OFFER_TYPE_CD | VARCHAR | Offer type code |
| OFFER_NM | VARCHAR | Offer name |
| OFFER_DESC_TX | VARCHAR | Offer description tax |
| SRVC_OFFER_DK | BIGINT | Service offer key |
| OFFER_SRVC_CD | VARCHAR | Offer service code |
| OFFER_SRVC_NM | VARCHAR | Offer service name |
| MNGD_INVSTG_SRVC_CD | VARCHAR | Managed invstg service code |
| MNGD_INVSTG_SRVC_NM | VARCHAR | Managed invstg service name |
| SRVC_GRP_NM | VARCHAR | Service group name |
| SRVC_OFFER_FIN_CD | VARCHAR | Service offer financial code |
| SRVC_CD | VARCHAR | Service code |
| SRVC_NM | VARCHAR | Service name |
| SRVC_TYPE_CD | VARCHAR | Service type code |
| SRVC_TYPE_NM | VARCHAR | Service type name |
| ALL_SRVC_CD | VARCHAR | All service code |
| ALL_SRVC_NM | VARCHAR | All service name |
| MNGD_INVSTG_IN | VARCHAR | Managed invstg indicator |
| RETL_INDPNDNT_IN | VARCHAR | Retail independent indicator |
| TRADE_SRVC_IN | VARCHAR | Trade service indicator |
| ENT_CD | VARCHAR | Ent code |
| SPC_IN | VARCHAR | Spc indicator |
| MAS_IN | VARCHAR | Mas indicator |
| SMP_IN | VARCHAR | Smp indicator |
| PRSNL_TRUST_IN | VARCHAR | Personal trust indicator |
| AUTO_PORT_TYPE_CD | VARCHAR | Automated portfolio type code |
| SMP_TYPE_CD | VARCHAR | Smp type code |
| TRADE_SRVC_TYPE_CD | VARCHAR | Trade service type code |
| MUB_CHRMN_CRCL_TYPE_CD | VARCHAR | Mub chrmn crcl type code |
| AMM_TYPE_CD | VARCHAR | Amount type code |
| SAN_TYPE_CD | VARCHAR | San type code |
| OFFER_ID | BIGINT | Offer id |
| SGP_IN | VARCHAR | Sgp indicator |
| MWA_IN | VARCHAR | Mwa indicator |
| SP_IN | VARCHAR | Sp indicator |

## CORE_OFFER_ENRLMT_1

| Column | Type | Description |
|---|---|---|
| ACCT_ID | BIGINT | Account id |
| ACCT_DK | BIGINT | Account key |
| OFFER_ORDER_ID | BIGINT | Offer order id |
| DW_EFF_DT | DATE | DW effective date |
| OFFER_ENRLMT_DT | DATE | Offer enrlmt date |
| OFFER_ENRLMT_EXPR_DT | DATE | Offer enrlmt expiry date |
| CUST_GRP_ID | BIGINT | Customer group id |
| OFFER_ID | BIGINT | Offer id |
| CORE_OFFER_DK | BIGINT | Core offer key |
| OFFER_NM | VARCHAR | Offer name |
| OFFER_ENRLMT_STAT_CD | VARCHAR | Offer enrlmt status code |
| OFFER_ENRLMT_BAL_AM | DECIMAL(16,2) | Offer enrlmt balance amount |
| DW_CREAT_TS | TIMESTAMP | DW create timestamp |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

Joins: `CORE_OFFER_DK` references `D_CORE_OFFER.CORE_OFFER_DK`

## BANK_DEP_ACCT_ADDR_1

| Column | Type | Description |
|---|---|---|
| DW_BANK_ACCT_ID | BIGINT | DW bank account id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| DEP_ACCT_NR | BIGINT | Deposit account number |
| DEP_ACCT_NR_DK | BIGINT | Deposit account number key |
| MAIL_ADDR_LINE_1_TX | VARCHAR | Mail address line 1 tax |
| MAIL_ADDR_LINE_2_TX | VARCHAR | Mail address line 2 tax |
| MAIL_ADDR_LINE_3_TX | VARCHAR | Mail address line 3 tax |
| MAIL_ADDR_LINE_4_TX | VARCHAR | Mail address line 4 tax |
| MAIL_CITY_NM | VARCHAR | Mail city name |
| MAIL_STATE_CD | VARCHAR | Mail state code |
| MAIL_POSTL_CD | VARCHAR | Mail postal code |
| MAIL_CNTRY_CD | VARCHAR | Mail country code |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |
| DW_CREAT_TS | TIMESTAMP | DW create timestamp |

## LGCY_ACCT_1

| Column | Type | Description |
|---|---|---|
| ACCT_ID | BIGINT | Account id |
| ACCT_DK | BIGINT | Account key |
| DW_EFF_DT | DATE | DW effective date |
| DW_EFF_TS | TIMESTAMP | DW effective timestamp |
| DW_EXPR_DT | DATE | DW expiry date |
| DW_EXPR_TS | TIMESTAMP | DW expiry timestamp |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| LGCY_ACCT_BRCH_CD | VARCHAR | Legacy account branch code |
| ACCT_ACTIV_CD | VARCHAR | Account activity code |
| CLNT_CD | VARCHAR | Client code |
| LGCY_ACCT_TYPE_CD | VARCHAR | Legacy account type code |
| ACCT_RGSTR_TYPE_CD | VARCHAR | Account registration type code |
| CASH_MRGN_IRA_KEO_TYPE_CD | VARCHAR | Cash margin ira keo type code |
| ALT_ACCT_ID | BIGINT | Alt account id |
| ALT_ACCT_DK | BIGINT | Alt account key |
| SPCL_ACCT_CD | VARCHAR | Special account code |
| POSTL_CD | VARCHAR | Postal code |
| POSTL_PLUS_4_CD | BIGINT | Postal plus 4 code |
| TXPYR_ID | BIGINT | Taxpayer id |
| TAX_ID_FRMT_IN | VARCHAR | Tax id format indicator |
| CASH_ACCT_SCTY_INSTR_CD | VARCHAR | Cash account security instruction code |
| CASH_ACCT_FUND_INSTR_CD | VARCHAR | Cash account fund instruction code |
| MRGN_ACCT_SCTY_INSTR_CD | VARCHAR | Margin account security instruction code |
| MRGN_ACCT_FUND_INSTR_CD | VARCHAR | Margin account fund instruction code |
| ACCT_OPEN_DT | DATE | Account open date |
| NSCC_CLR_ID | BIGINT | Nscc clearing id |
| NSCC_CLR_LOC_CD | VARCHAR | Nscc clearing location code |
| EXT_CHECK_HIST_IN | VARCHAR | Ext check history indicator |
| MRKT_CAMP_CD_INACT_CD | VARCHAR | Market camp code inactive code |
| ACCT_INSTN_CLS_CD | VARCHAR | Account institution class code |
| MMF_RQST_CD | VARCHAR | Mmf request code |
| MMF_TAX_XMPT_STATE_FUND_CD | VARCHAR | Mmf tax exempt state fund code |
| MMF_INVST_CD | VARCHAR | Mmf investment code |
| MMF_MIN_REQ_CD | VARCHAR | Mmf min request code |
| MMF_DIV_DIST_IN | VARCHAR | Mmf dividend distribution indicator |
| MMF_ACCT_TYPE_IN | VARCHAR | Mmf account type indicator |
| PRIM_CD | VARCHAR | Primary code |
| MMF_PEND_STAT_CD | VARCHAR | Mmf pending status code |
| MUB_MUTL_FUND_PROD_CD | VARCHAR | Mub mutual fund product code |
| CEF_ORIG_MMF_CD | VARCHAR | Cef original mmf code |
| ACCT_ORIG_MMF_CD | VARCHAR | Account original mmf code |
| FIRST_MUTL_FUND_DT | DATE | First mutual fund date |
| ACCT_TAX_RPT_CD | VARCHAR | Account tax report code |
| TAX_ID_AVAL_IN | VARCHAR | Tax id available indicator |
| DUP_CFN_CD | VARCHAR | Dup cfn code |
| DUP_STMT_CD | VARCHAR | Dup statement code |
| ACCT_OPTN_MRGN_LIMIT_NR | BIGINT | Account option margin limit number |
| MRGN_DR_INT_CD | VARCHAR | Margin debit interest code |
| STOCK_LOAN_REBAT_RT | DECIMAL(18,4) | Stock loan rebat rate |
| STOCK_BRROW_REBAT_RT | DECIMAL(18,4) | Stock borrow rebat rate |
| CR_INT_RATE_IN | VARCHAR | Credit interest rate indicator |
| CR_INT_ROST_CD | VARCHAR | Credit interest rost code |
| MRGN_MAINT_REQ_IN | VARCHAR | Margin maintenance request indicator |
| MRGN_LMT_CD | VARCHAR | Margin limit code |
| OPTN_APPR_LVL_CD | VARCHAR | Option approval level code |
| OPTN_PEND_RQST_CD | VARCHAR | Option pending request code |
| OPTN_LVL_ENTER_DT | DATE | Option level enter date |
| RGSTR_OPTN_REP_ID | VARCHAR | Registration option representative id |
| RGSTR_OPTN_MUB_PRSN_ID | VARCHAR | Registration option mub person id |
| RGSTR_OPTN_MUB_PRSN_DK | BIGINT | Registration option mub person key |
| RGSR_OPTN_REP_APPR_DT | DATE | Registration option representative approval date |
| OPTN_DSCLSR_SENT_DT | DATE | Option disclosure sent date |
| OPTN_MAINT_REQ_CD | VARCHAR | Option maintenance request code |
| OPTN_LMT_CD | VARCHAR | Option limit code |
| ACCT_BUSN_EXT_ID | BIGINT | Account business ext id |
| ACCT_MSTR_BKUP_TX | VARCHAR | Account master backup tax |
| VIP_CD | VARCHAR | Vip code |
| LGCY_ACCT_RSTR_1_CD | VARCHAR | Legacy account restriction 1 code |
| LGCY_ACCT_RSTR_2_CD | VARCHAR | Legacy account restriction 2 code |
| LGCY_ACCT_RSTR_3_CD | VARCHAR | Legacy account restriction 3 code |
| LGCY_ACCT_RSTR_4_CD | VARCHAR | Legacy account restriction 4 code |
| LGCY_ACCT_RSTR_5_CD | VARCHAR | Legacy account restriction 5 code |
| S1_ACCT_IN | VARCHAR | S1 account indicator |
| S2_ACCT_PROD_CD | VARCHAR | S2 account product code |
| BLKD_ACCT_IN | VARCHAR | Blocked account indicator |
| IRA_KEO_TRUST_SIGN_DT | DATE | Ira keo trust sign date |
| KEO_PLAN_TAX_ID | BIGINT | Keo plan tax id |
| DIV_RNVST_IN | VARCHAR | Dividend reinvest indicator |
| OPTN_FIN_SRVC_IN | VARCHAR | Option financial service indicator |
| ACCT_OPEN_CD | VARCHAR | Account open code |
| COMM_DISCT_RT | DECIMAL(18,4) | Commission discount rate |
| COMM_SCHED_TBL_TX | VARCHAR | Commission schedule table tax |
| ACCT_INVST_OBJ_CD | VARCHAR | Account investment obj code |
| ACCT_EFT_CD | VARCHAR | Account eft code |
| AUTO_JRNL_CD | VARCHAR | Auto journal code |
| CALL_BACK_PHON_CD | VARCHAR | Call back phone code |
| INST_ACCT_IN | VARCHAR | Institutional account indicator |
| MRKT_CAMP_IN | VARCHAR | Market camp indicator |
| NEW_ACCT_FORM_ID | BIGINT | New account form id |
| SPCL_HAND_INSTR_ID | BIGINT | Special hand instruction id |
| LEAD_SRCE_CD | VARCHAR | Lead source code |
| ACCT_OPEN_TRML_ID | VARCHAR | Account open trml id |
| LEAD_QUAL_CD | VARCHAR | Lead qualified code |
| ACCT_OPEN_USER_ID | VARCHAR | Account open user id |
| ACCT_OPEN_MUB_PRSN_ID | VARCHAR | Account open mub person id |
| ACCT_OPEN_MUB_PRSN_DK | BIGINT | Account open mub person key |
| PROMO_CD | VARCHAR | Promo code |
| NAL_PKG_NODEF_CD | VARCHAR | Nal package nodef code |
| FEE_DISB_IN | VARCHAR | Fee disbursement indicator |
| INVST_TRADR_IN | VARCHAR | Investment trader indicator |
| DTC_INSTN_AUTO_INPUT_CD | BIGINT | Dtc institution auto input code |
| DTC_CLEAR_ID | BIGINT | Dtc clear id |
| DTC_INSTN_AGENT_BANK_CD | BIGINT | Dtc institution agent bank code |
| NEW_ACCT_LEAD_DATE_NODEF_DT | DATE | New account lead date nodef date |
| WEEK_ACCT_LEAD_INACT_DT | DATE | Week account lead inactive date |
| PREV_CONV_ACCT_ID | VARCHAR | Previous conv account id |
| PREV_CONV_ACCT_DK | BIGINT | Previous conv account key |
| NAPOTCSYM_NODEF_TX | VARCHAR | Napotcsym nodef tax |
| TXPYR_BIRTH_DATE_TX | VARCHAR | Taxpayer birth date tax |
| ACCT_OCCUP_CD | VARCHAR | Account occupation code |
| NEW_ACCT_CW_NODEF_DT | DATE | New account cw nodef date |
| PROXY_MAIL_IN | VARCHAR | Proxy mail indicator |
| ACCT_HIST_IN | VARCHAR | Account history indicator |
| UNSECR_MAIL_IN | VARCHAR | Unsecured mail indicator |
| DUP_ACCT_TYPE_IN | VARCHAR | Dup account type indicator |
| CUST_INFO_CHG_DT | DATE | Customer info chg date |
| MINOR_TRF_STATE_CD | VARCHAR | Minor transfer state code |
| MINOR_AGE_NR | BIGINT | Minor age number |
| SCND_NSCC_CD | VARCHAR | Second nscc code |
| ACCT_CONV_DT | DATE | Account conv date |
| NEW_SCRN_CHG_IN | VARCHAR | New scrn chg indicator |
| SEGMT_ID | VARCHAR | Segment id |
| HSHLD_ACCT_ID | BIGINT | Household account id |
| HSHLD_ACCT_DK | BIGINT | Household account key |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## INS_SEC_PROD_CD_1

| Column | Type | Description |
|---|---|---|
| CUSIP_ID | VARCHAR | Cusip id |
| NSCC_ID | BIGINT | Nscc id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| NSCC_NM | VARCHAR | Nscc name |
| PROD_NM | VARCHAR | Product name |
| CRIER_PPTRY_PROD_1_CD | VARCHAR | Crier pptry product 1 code |
| CRIER_PPTRY_PROD_2_CD | VARCHAR | Crier pptry product 2 code |
| THRD_PRTY_PROD_ID | BIGINT | Third party product id |
| PROD_TYPE_NM | VARCHAR | Product type name |
| PROD_TYPE_CD | VARCHAR | Product type code |
| THRD_PRTY_ID | BIGINT | Third party id |
| SRVC_ONLY_IN | VARCHAR | Service only indicator |
| EFF_START_DT | TIMESTAMP | Effective start date |
| EFF_END_DT | TIMESTAMP | Effective end date |
| INSERT_TS | TIMESTAMP | Insert timestamp |
| INSERT_USER_NM | VARCHAR | Insert user name |
| MODIFY_TS | TIMESTAMP | Modify timestamp |
| MODIFY_USER_NM | VARCHAR | Modify user name |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## INS_CONTR_1

| Column | Type | Description |
|---|---|---|
| CONTR_ID | VARCHAR | Contract id |
| NSCC_ID | BIGINT | Nscc id |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| PROD_CUSIP_ID | VARCHAR | Product cusip id |
| CONTR_STAT_CD | VARCHAR | Contract status code |
| END_RCV_CMPY_ID | VARCHAR | End receive company id |
| END_RCV_CMPY_TYPE_CD | VARCHAR | End receive company type code |
| CONTR_GRP_NR | VARCHAR | Contract group number |
| ORIG_CONTR_ID | VARCHAR | Original contract id |
| CONTR_DIST_ACCT_ID | VARCHAR | Contract distribution account id |
| IRS_QLFY_CD | VARCHAR | Irs qualify code |
| CONTR_COMM_OPTN_CD | VARCHAR | Contract commission option code |
| CONTR_COMM_EXT_CD | VARCHAR | Contract commission ext code |
| CONTR_STATE_CD | VARCHAR | Contract state code |
| FUND_TRF_RSTR_IN | VARCHAR | Fund transfer restriction indicator |
| FUND_TRF_RSTR_REAS_CD | VARCHAR | Fund transfer restriction reason code |
| CONTR_NON_ASSGN_ENDRSMT_IN | VARCHAR | Contract non assigned endrsmt indicator |
| CONTR_PROD_TYPE_CD | VARCHAR | Contract product type code |
| MVA_IN | VARCHAR | Mva indicator |
| PROD_SHR_CLS_CD | VARCHAR | Product share class code |
| COMM_SCHED_ID | VARCHAR | Commission schedule id |
| CONTR_FEES_IN | VARCHAR | Contract fees indicator |
| ORIG_CNTCT_NR | VARCHAR | Original contact number |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

Joins: `PROD_CUSIP_ID` references `INS_SEC_PROD_CD_1.CUSIP_ID`

## TRAN_FACT_EXCL_MFCLR_G

| Column | Type | Description |
|---|---|---|
| DW_TRAN_ID | BIGINT | DW transaction id |
| ACCT_SRCE_SYS_ID | BIGINT | Account source system id |
| DW_ACCT_ID | BIGINT | DW account id |
| DW_TRAN_DT | DATE | DW transaction date |
| DW_EFF_DT | DATE | DW effective date |
| DW_EXPR_DT | DATE | DW expiry date |
| CURR_IN | BIGINT | Current indicator |
| RCV_IN | BIGINT | Receive indicator |
| MNTH_END_DT | DATE | Month end date |
| DW_TRAN_TYPE_CD | VARCHAR | DW transaction type code |
| DW_TRAN_DIRCTN_CD | VARCHAR | DW transaction direction code |
| TRAN_SRCE_SYS_ID | BIGINT | Transaction source system id |
| ITEM_ISS_ID | BIGINT | Item issue id |
| RULE_SET_SUFF_ID | BIGINT | Rule set suffix id |
| TRAN_EXEC_DT | DATE | Transaction execution date |
| TRAN_SETTL_DT | DATE | Transaction settlement date |
| TRAN_SHR_QY | DECIMAL(18,4) | Transaction share qy |
| TRAN_EXEC_PRICE_PER_SHR_AM | DECIMAL(16,2) | Transaction execution price per share amount |
| TRAN_AM | DECIMAL(16,2) | Transaction amount |
| TRAN_ROST_TYPE_CD | VARCHAR | Transaction rost type code |
| TRAN_ROST_ID | BIGINT | Transaction rost id |
| TRAN_ADD_TS | TIMESTAMP | Transaction add timestamp |
| TRAN_PROC_TS | TIMESTAMP | Transaction process timestamp |
| OFSET_ACCT_ID | BIGINT | Offset account id |
| TRAN_EFF_DT | DATE | Transaction effective date |
| TRAN_EXEC_TS | TIMESTAMP | Transaction execution timestamp |
| OFSET_SRCE_SYS_ID | BIGINT | Offset source system id |
| NNA_IN | VARCHAR | Net new assets indicator |
| NAF_IN | VARCHAR | Naf indicator |
| FIN_MTRC_RLSE_FRZN_IN | VARCHAR | Financial metric release frozen indicator |
| DW_LOAD_TS | TIMESTAMP | DW load timestamp |

## F_NNA_FIN

| Column | Type | Description |
|---|---|---|
| DW_TRAN_ID | BIGINT | DW transaction id |
| ACCT_SRCE_SYS_ID | BIGINT | Account source system id |
| TRANS_SRCE_SYS_ID | BIGINT | Transaction source system id |
| DW_ACCT_ID | BIGINT | DW account id |
| DW_ACCT_DK | BIGINT | DW account key |
| DW_TRAN_DT | DATE | DW transaction date |
| DW_TRAN_MNTH_END_DT | DATE | DW transaction month end date |
| TRAN_EXEC_DT | DATE | Transaction execution date |
| TRAN_EXEC_TS | TIMESTAMP | Transaction execution timestamp |
| TRAN_SETTL_DT | DATE | Transaction settlement date |
| TRAN_EFF_DT | DATE | Transaction effective date |
| DW_TRAN_TYPE_CD | VARCHAR | DW transaction type code |
| CLIENT_DIRECTED_NNA_IN | VARCHAR | Client directed net new assets indicator |
| DW_TRAN_DIRCTN_CD | VARCHAR | DW transaction direction code |
| MUB_SEC_ID | BIGINT | Mub security id |
| ITEM_ISS_ID | BIGINT | Item issue id |
| RULE_SET_SUFF_ID | BIGINT | Rule set suffix id |
| NNA_SHR_QY | DECIMAL(18,4) | Net new assets share qy |
| NNA_EXEC_PRICE_PER_SHR_AM | DECIMAL(16,2) | Net new assets execution price per share amount |
| NNA_AM | DECIMAL(16,2) | Net new assets amount |
| FRZN_IN | VARCHAR | Frozen indicator |
| TXPYR_CUST_DK | BIGINT | Taxpayer customer key |
| HSHLD_DK | BIGINT | Household key |

## nna_exclusion

| Column | Type | Description |
|---|---|---|
| exclusion_id | INTEGER | Exclusion identifier |
| account_id | INTEGER | Account excluded, on its source system |
| source_system | VARCHAR | Source system of the account |
| reason_code | VARCHAR | Why the account is excluded |
| effective_from | DATE | First day the exclusion applies |
| effective_to | DATE | Last day the exclusion applies, null while it is open |

## aum_month_end

| Column | Type | Description |
|---|---|---|
| household_id | INTEGER | Household |
| month_end_date | DATE | Month end; a quarter end, when holdings are valued |
| aum_usd | DECIMAL(18,2) | Assets under management, USD: the market value of the household's holdings on the date |

Joins: `household_id` references `household.household_id`; `month_end_date` references `MONTH_DIM_1.CLDR_EOM_DT`
