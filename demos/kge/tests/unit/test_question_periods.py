"""Calendar input must resolve precisely or carry a plain refusal before an answer is planned."""
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.question import Period, UNPLACED_RANGE, parse


DOMAIN = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, ENTITY_WORDS={"household": "household", "account": "account"})


@pytest.mark.parametrize("phrase", [
    "since March 2025", "June 2025 compared with March 2025", "from April 2025 to June 2025",
    "YTD June 2025", "June 2025 and July", "as at 31 June 2025", "2025-02-31", "2025-13",
    "July and June 2025", "since March", "Juni 2025 and June 2025", "Juni 2025 since March 2025",
    "Juni 2025 and revenue of 2024", "first half of 2025 and June 2025", "June 2025 and first half of 2025",
])
def test_model_point_cannot_replace_a_final_rules_verdict(phrase):
    question = f"Lumen mass for {phrase}"
    baseline = parse(question, ["Lumen Mass"], DOMAIN)
    repaired = parse(question, ["Lumen Mass"], DOMAIN, period=Period("month", "2025-06-01"))
    assert baseline.problem
    assert repaired.problem == baseline.problem
    assert repaired.period is None and repaired.special == "invalid_period"
    assert repaired.period_by == "rules"


@pytest.mark.parametrize("phrase", ["Juni 2025", "active 2025"])
def test_model_point_can_repair_a_wholly_unread_period(phrase):
    period = Period("month", "2025-06-01")
    req = parse(f"Lumen mass for {phrase}", ["Lumen Mass"], DOMAIN, period=period)
    assert req.period == period and req.problem is None and req.period_by == "model"


@pytest.mark.parametrize("phrase,kind,value,reason", [
    ("2025", "month", "2025-06-01", "grain"),
    ("2025", "calendar_quarter", "2025-Q2", "grain"),
    ("H1 2025", "calendar_quarter", "2025-Q2", "grain"),
    ("first half of 2025", "month", "2025-06-01", "grain"),
    ("H2 2025", "year", "2025", "grain"),
    ("H3 2025", "calendar_quarter", "2025-Q2", "grain"),
    ("FY25", "month", "2025-06-01", "grain"),
    ("CY2025oops", "calendar_quarter", "2025-Q2", "grain"),
    ("quarter Q5 2025", "month", "2025-06-01", "grain"),
    ("month Juni 2025", "year", "2025", "grain"),
    ("week Juni 2025", "month", "2025-06-01", "grain"),
    ("Juni 2025", "month", "2024-06-01", "year"),
    ("Juni ２０２５", "calendar_quarter", "2024-Q2", "year"),
    ("Juni 2025 for household 1999", "month", "2025-06-01", "year"),
])
def test_model_reading_preserves_written_year_and_grain(phrase, kind, value, reason):
    req = parse(f"Lumen mass {phrase}", ["Lumen Mass"], DOMAIN, period=Period(kind, value))
    assert req.period is None and reason in req.problem
    assert req.special == "invalid_period"


def test_model_can_place_a_bare_year_as_a_year():
    period = Period("year", "2025")
    req = parse("Lumen mass 2025", ["Lumen Mass"], DOMAIN, period=period)
    assert req.period == period and req.problem is None and req.period_by == "model"


@pytest.mark.parametrize("question", ["Please show Lumen mass 2025", "Lumen mass 2025 please", "Lumen mass active 2025",
                                      "Lumen mass 2025.", "Lumen mass 2025?", "Lumen mass 2025!"])
def test_extra_question_words_do_not_make_a_bare_year_a_month(question):
    domain = SimpleNamespace(ALIASES={"active": "Lumen Mass"}, UNIT_WORDS={})
    req = parse(question, ["Lumen Mass"], domain, period=Period("month", "2025-06-01"))
    assert req.period is None and "grain" in req.problem


def test_a_conjunction_between_measures_is_not_a_second_period():
    period = Period("month", "2025-06-01")
    req = parse("Lumen mass in Juni 2025 and luminosity", ["Lumen Mass", "Luminosity"], DOMAIN, period=period)
    assert req.period == period and req.problem is None and req.period_by == "model"


@pytest.mark.parametrize("question", ["Lumen mass", "Lumen mass for the specified period", "household 2025"])
def test_model_cannot_invent_an_unasked_period(question):
    req = parse(question, ["Lumen Mass"], DOMAIN, period=Period("month", "2025-06-01"))
    assert req.period is None and req.problem is None
    assert req.period_by == "none"


@pytest.mark.parametrize("suffix", ["", " in Juni 2025", " in 2025"])
def test_model_cannot_attach_a_period_to_a_definition(suffix):
    import re

    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, INTENTS={"definitional": re.compile("What is", re.I)})
    question = "What is Revenue?" + suffix
    baseline = parse(question, ["Revenue"], domain)
    req = parse(question, ["Revenue"], domain, period=Period("month", "2025-06-01"))
    assert req.definitional and req.period == baseline.period and req.problem == baseline.problem
    assert req.period_by != "model"

RANGE_POINT_FORMS = [
    "since March 2025", "from April 2025 to June 2025", "between April 2025 and June 2025",
    "June 2025 compared with March 2025", "June 2025 compared to March 2025",
    "until June 2025", "through June 2025", "June 2025 to date", "June 2025 YTD",
    "YTD June 2025", "year to date June 2025", "June 2025 year to date",
    "so far this year as at 30 June 2025", "over the last 3 months as at 30 June 2025",
    "June 2025 vs March 2025", "June 2025 versus March 2025", "June 2025 vs. March 2025",
    "March 2025 to June 2025", "since 2025-03-01", "since Q1 2025", "since FY26-Q1",
    "from 1 April 2025 to 30 June 2025", "between 1 May 2025 and 31 May 2025",
    "since 2025", "YTD", "year to date", "so far this year", "over the last 3 months",
    "since the beginning of March 2025", "until the end of June 2025", "June 2025 through today",
    "until 2025", "through 2025", "between 2024 and 2025",
    "June 2025 to 2026", "June 2025 to now",
]

UNREAD_RANGE_FORMS = list(dict.fromkeys([
    *[phrase.replace("June", "Juni").replace("March", "März").replace("April", "Avril").replace("May", "Mai")
      for phrase in RANGE_POINT_FORMS],
    "Juni 2025 and Juli 2025", "Juni 2025 and Juli", "Juni 2025 or Juli 2025",
    "Juni 2025 then Juli 2025",
    "Juni 2025 vs Juli 2025", "Juni 2025 compared with Mai 2025", "since Juni 2025",
    "Juni 2025 to Juli 2025", "Juni to Juli 2025", "between Juni and Juli 2025",
    "Juni 2025 Juli 2025", "Juni 2025 or Juli", "Juni 2025 then Juli",
    "Juni 2025, and Juli", "Juni 2025,and Juli", "Juni 2025 and/or Juli",
]))


@pytest.mark.parametrize("phrase", UNREAD_RANGE_FORMS)
def test_wholly_unread_ranges_are_not_model_repairable(phrase):
    req = parse(f"Lumen mass {phrase}", ["Lumen Mass"], DOMAIN)
    assert not req.model_period_allowed
    assert req.period is None and req.problem



@pytest.mark.parametrize("phrase", RANGE_POINT_FORMS)
def test_range_cues_never_place_an_endpoint(phrase):
    req = parse(f"Lumen mass {phrase}", ["Lumen Mass"], DOMAIN)
    assert req.period is None and req.problem == UNPLACED_RANGE
    assert req.special == "invalid_period"


@pytest.mark.parametrize("phrase,expected", [
    ("1 May 2025 to 31 May 2025", Period("as_of", "2025-05-31")),
    ("from 1 May 2025 to 31 May 2025", Period("as_of", "2025-05-31")),
    ("1 February 2024 to 29 February 2024", Period("as_of", "2024-02-29")),
    ("FY26-Q1", Period("fiscal_quarter", "FY26-Q1")),
    ("quarter to 30 June 2025", Period("calendar_quarter", "2025-Q2")),
    ("to 30 June 2025", Period("as_of", "2025-06-30")),
    ("Q2 2025", Period("calendar_quarter", "2025-Q2")),
    ("June 2025 to be reported", Period("month", "2025-06-01")),
    ("from retail in June 2025", Period("month", "2025-06-01")),
    ("retail versus corporate in June 2025", Period("month", "2025-06-01")),
    ("through the retail channel in June 2025", Period("month", "2025-06-01")),
])
def test_existing_intervals_and_unrelated_cues_keep_their_reading(phrase, expected):
    req = parse(f"Lumen mass {phrase}", ["Lumen Mass"], DOMAIN)
    assert req.period == expected and req.problem is None


@pytest.mark.parametrize("date", ["31 June 2025", "31 Feb 2025", "99 Jan 2025", "0 Jan 2025", "29 Feb 2025"])
@pytest.mark.parametrize("prefix", ["as at ", "on ", "", "to "])
def test_impossible_dates_are_a_request_problem(date, prefix):
    req = parse(f"Lumen mass {prefix}{date}", ["Lumen Mass"], DOMAIN)
    assert req.problem == f"That date does not exist: {date.lower()}."
    assert req.period is None
    assert req.special == "invalid_period"


@pytest.mark.parametrize("period", ["Junes 2025", "June 2025s", "June 20255", "June 0000", "Jun 25", "June2025", "June 2025.5"])
def test_malformed_months_require_a_valid_period(period):
    req = parse(f"Lumen mass for {period}", ["Lumen Mass"], DOMAIN)
    assert req.problem == f"Please give a valid period: {period.lower()}."
    assert req.period is None


@pytest.mark.parametrize("space", [chr(n) for n in range(0x3100) if chr(n).isspace()])
@pytest.mark.parametrize("period,expected", [
    ("June 2025", Period("month", "2025-06-01")),
    ("as at 29 Feb 2024", Period("as_of", "2024-02-29")),
    ("Q2 2025", Period("calendar_quarter", "2025-Q2")),
])
def test_every_unicode_whitespace_reads_like_a_space(space, period, expected):
    question = "Lumen mass for " + period.replace(" ", space)
    req = parse(question, ["Lumen Mass"], DOMAIN)
    assert req.problem is None
    assert req.period == expected
    assert req.question == question


COMMON_MONTH_FORMS = [
    "June, 2025", "June of 2025", "Jun-2025", "06/2025", "2025-06", "June '25",
    "June\u200b 2025", "Ju\u200bne 2025", "June 20\u200b25",
]


@pytest.mark.parametrize("period", COMMON_MONTH_FORMS)
def test_common_month_forms_keep_the_named_period(period):
    req = parse(f"Lumen mass for {period}", ["Lumen Mass"], DOMAIN)
    assert req.problem is None
    assert req.period == Period("month", "2025-06-01")


@pytest.mark.parametrize("period", ["June", "June someday", "June, unknown", "20255", "2025s", "13/2025", "2025-13",
                                  "June 2025 and July", "June 2025 and 20255", "June 31 2025", "June\u200b2025"])
def test_unread_period_tokens_never_become_all_time(period):
    req = parse(f"Lumen mass for {period}", ["Lumen Mass"], DOMAIN)
    assert req.problem and req.special == "invalid_period"
    assert req.period is None


@pytest.mark.parametrize("word", ["market", "mayor", "decade", "decline", "novel", "septic", "augmented"])
def test_month_prefixes_are_ordinary_words(word):
    req = parse(f"Lumen mass by {word} 2025", ["Lumen Mass"], DOMAIN)
    assert req.problem is None
    assert req.period is None


@pytest.mark.parametrize("digits", ["２０２５", "٢٠٢٥", "۲۰۲۵", "२०२५", "𝟚𝟘𝟚𝟝"])
def test_unicode_decimal_years_are_ascii(digits):
    req = parse(f"Lumen mass for June {digits}", ["Lumen Mass"], DOMAIN)
    assert req.problem is None
    assert req.period == Period("month", "2025-06-01")


def test_modal_may_is_not_a_month_token():
    import re

    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, INTENTS={"definitional": re.compile("May sensitive", re.I)})
    req = parse("May sensitive data be published?", [], domain)
    assert req.period is None and req.problem is None


@pytest.mark.parametrize("period,expected", [
    ("as at ３０ June ２０２５", Period("as_of", "2025-06-30")),
    ("Q２ ٢٠٢٥", Period("calendar_quarter", "2025-Q2")),
    ("FY２６-Q１", Period("fiscal_quarter", "FY26-Q1")),
    ("０６/２０２５", Period("month", "2025-06-01")),
])
def test_unicode_decimal_digits_are_normalised_across_period_shapes(period, expected):
    req = parse(f"Lumen mass {period}", ["Lumen Mass"], DOMAIN)
    assert req.period == expected and req.problem is None


@pytest.mark.parametrize("period", ["May 2025", "May of 2025", "May '25"])
def test_a_month_before_the_verb_be_keeps_its_period(period):
    req = parse(f"{period} figures to be reported", [], DOMAIN)
    assert req.problem is None and req.period == Period("month", "2025-05-01")


def test_a_definitions_bare_year_selects_document_versions():
    import re

    from kge.services.ask.engine.question import asked_window

    domain = SimpleNamespace(ALIASES={}, UNIT_WORDS={}, INTENTS={"definitional": re.compile("Which version", re.I)})
    req = parse("Which version applies to ２０２４ reporting?", [], domain)
    assert req.period is None and req.problem is None
    assert asked_window(req, domain) == ("2024-01-01", "2024-12-31")


def test_an_initial_may_without_a_year_cannot_give_an_all_time_figure():
    req = parse("May Lumen mass to be reported", ["Lumen Mass"], DOMAIN)
    assert req.problem and req.period is None


ROUND3_PERIODS = [
    *[(text, Period("as_of", "2025-06-30")) for text in [
        "as at 30 June 2025", "as at 30/06/2025", "as at 30/6/2025", "30/06/2025", "30/6/2025",
        "as at 30th June 2025", "as at the 30th of June 2025", "30th June 2025", "2025-06-30",
        "as at 2025-06-30", "06/30/2025", "30.06.2025", "June 30, 2025",
    ]],
    *[(text, Period("month", "2025-06-01")) for text in [
        *COMMON_MONTH_FORMS, "June,2025", "JUNE 2025", "Ｊｕｎｅ 2025", "June\u00a02025", "Jun. 2025", "June 2025",
    ]],
    ("Sept. 2025", Period("month", "2025-09-01")), ("Sept 2025", Period("month", "2025-09-01")),
    *[(text, Period("calendar_quarter", "2025-Q2")) for text in [
        "Q2 2025", "2025 Q2", "2025-Q2", "Q2 of 2025", "Q2, 2025", "2Q 2025", "the second quarter of 2025", "2Q25", "Q2-2025",
        "quarter to 30 June 2025",
    ]],
    ("FY26-Q1", Period("fiscal_quarter", "FY26-Q1")),
    ("fiscal quarter FY26-Q1", Period("fiscal_quarter", "FY26-Q1")),
    ("CY2025", Period("year", "2025")),
    *[(text, "unreadable") for text in [
        "H1 2025", "the first half of 2025", "Q2 FY25", "FY25", "June 25", "June 31 2025",
        "Juni 2025", "Ju\u200dne 2025", "Ju\u00adne 2025", "December 9999", "31 Dec 9999", "0001-01", "0000-01",
        "13/2025", "2025-13", "as at 31 Feb 2025", "June2025", "06/07/2025", "June 2025 and H2", "Q5 2025",
        "Q2oops 2025", "FY26oops", "CY2025oops", "H1oops 2025",
        "quarter to 5000", "quarter to date", "June2025s", "Jun2025x", "in 1899", "in 2100", "in 9999",
    ]],
]


@pytest.mark.parametrize("text,expected", ROUND3_PERIODS)
def test_round3_period_grammar(text, expected):
    req = parse(f"Lumen mass for {text}", ["Lumen Mass"], DOMAIN)
    if expected == "unreadable":
        assert req.period is None and req.special == "invalid_period" and req.problem
    else:
        assert req.problem is None
        assert req.period == expected


@pytest.mark.parametrize("text", [
    "balance over 5000", "balance over 1000", "balance over 10000", "household 5001", "account 70001",
    "balance over 2025", "household 1999", "account 20001", "balance over 20001", "1st product", "2nd place",
])
def test_round3_entity_ids_and_thresholds_are_not_periods(text):
    req = parse(f"Lumen mass for {text}", ["Lumen Mass"], DOMAIN)
    assert req.problem is None and req.period is None


@pytest.mark.parametrize("cue", ["in", "for", "during", "as at", "of", "CY"])
@pytest.mark.parametrize("year", [1900, 1999, 2025, 2099])
def test_years_require_a_date_context(cue, year):
    req = parse(f"Lumen mass {cue} {year}", ["Lumen Mass"], DOMAIN)
    assert req.problem is None and req.period == Period("year", str(year))


def test_entity_id_does_not_override_an_as_at_date():
    req = parse("Lumen mass for household 5001 at 30 June 2025", ["Lumen Mass"], DOMAIN)
    assert req.problem is None and req.period == Period("as_of", "2025-06-30")
