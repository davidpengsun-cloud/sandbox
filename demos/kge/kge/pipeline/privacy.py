"""Personal data in text: the detectors, masking and redaction, shared by certification, the pipeline and the queue.

Five detectors find what the gate prohibits: an email address, a date of
birth, a nine-digit national identifier, a card number and a health phrase.
Each match has a value (personal_data:email and so on) and a redaction: what
matched, without the data. A mailbox that names a function, not a person, is
not personal data, and redacts to itself, so it stays in clear wherever it is
shown. Which mailboxes do is the client's to say, in its pack
(terminology.functional_mailboxes): certification reads them there, and the
pipeline and the queue from the graph. Given none, every address is a person's.

mask replaces every match in a text by its redaction and says what it found.
AI certification masks each document before it lands (kge/estate/certify.py), so
no raw personal data reaches the landing zone, the graph or a trace. Masking
and redaction are idempotent: a redaction is never matched again. The steward
queue still redacts what it shows, as a second line.

column_findings finds the same families in a table's columns, for onboarding's
governance sub-graph (kge/estate/onboard/drafting.py): from a column's name and comment, and
from its sampled values where a detector above matches them. Each finding is a
candidate restricted-data policy that a business team accepts and a steward
certifies, never a policy by itself. The cost is asymmetric: a personal column
left unrestricted can reach an answer, while a restricted column that was not
personal costs one rejection in review, so the name patterns lean towards
flagging. Keys are never flagged: a restricted column may not be joined or
filtered on (kge/compiler/checks.py S23, S24), and a key identifies a row, not a
person's attribute.
"""
from __future__ import annotations

import re

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
DOB_RE = re.compile(r"date of birth\s+\d{1,2}\s+\w+\s+\d{4}", re.I)
NATIONAL_ID_RE = re.compile(r"\b\d{9}\b")
CARD_RE = re.compile(r"\b\d{4} \d{4} \d{4} \d{4}\b")
HEALTH_RE = re.compile(r"\b(diagnosed with|medical (?:condition|history)|suffers from|prescribed)\b", re.I)
DETECTORS = ((EMAIL_RE, "personal_data:email"), (DOB_RE, "personal_data:date_of_birth"), (NATIONAL_ID_RE, "personal_data:national_id"),
             (CARD_RE, "payment_card"), (HEALTH_RE, "special_category:health"))
CONFIDENCE = 0.9
FAMILY = "privacy"

NO_MAILBOXES: frozenset[str] = frozenset()
REDACTED = re.compile(r"\[(?:9-digit identifier redacted|card number redacted|name redacted|redacted|[a-z_]+ phrase: [^\[\]\n]*)\]")


def assess_privacy(value: str, evidence: str, role_mailboxes=NO_MAILBOXES) -> str:
    if value == "personal_data:email" and "@" in (evidence or ""):
        return "likely_functional" if evidence.split("@", 1)[0].lower() in role_mailboxes else "likely_personal"
    return "likely_personal"


def redact(value: str, evidence: str, role_mailboxes=NO_MAILBOXES) -> str:
    """What matched, without the data. A role mailbox is not personal data and is shown. Redacting a redaction changes nothing.
    A special-category match shows its trigger phrase, never the condition."""
    ev = evidence or ""
    if REDACTED.fullmatch(ev):
        return ev
    if value == "personal_data:email" and "@" in ev:
        local, domain = ev.split("@", 1)
        return ev if local.lower() in role_mailboxes else f"{local[:1]}{'*' * max(len(local) - 1, 3)}@{domain}"
    if value == "personal_data:date_of_birth":
        return "date of birth [redacted]"
    if value == "personal_data:national_id":
        return "[9-digit identifier redacted]"
    if value == "payment_card":
        return "[card number redacted]"
    if value == "personal_data:name":
        return "[name redacted]"
    if value.startswith("special_category"):
        return f"[{value.split(':', 1)[-1]} phrase: {ev}]"
    return "[redacted]"


def mask(text: str, detectors=DETECTORS, role_mailboxes=NO_MAILBOXES) -> tuple[str, list[dict]]:
    """text with every match of every detector replaced by its redaction, and the findings: one per match,
    {family, value, evidence (redacted), confidence, start, end}, with offsets into text. Where two matches overlap,
    the earlier and longer is replaced; each is still found. A role mailbox is found and stays in clear. A detector
    whose pattern has a group named v masks only that group."""
    done = [m.span() for m in REDACTED.finditer(text)]
    found = []
    for pattern, value in detectors:
        group = "v" if "v" in getattr(pattern, "groupindex", {}) else 0
        for m in pattern.finditer(text):
            start, end = m.span(group)
            if start < end and not any(a <= start and end <= b for a, b in done):
                found.append((start, end, value, redact(value, text[start:end], role_mailboxes)))
    found.sort(key=lambda f: (f[0], f[0] - f[1]))
    out, at = [], 0
    for start, end, _, evidence in found:
        if start >= at:
            out += [text[at:start], evidence]
            at = end
    out.append(text[at:])
    return "".join(out), [{"family": FAMILY, "value": value, "evidence": evidence, "confidence": CONFIDENCE, "start": start, "end": end}
                          for start, end, value, evidence in found]


COLUMN_DETECTORS = (
    (re.compile(r"(?:^|_)(?:e_?mail|email_address)(?:_|$)|\be-?mail address\b", re.I), "personal_data:email"),
    (re.compile(r"(?:^|_)(?:dob|birth_?date|date_of_birth|birthday)(?:_|$)|\bdate of birth\b", re.I), "personal_data:date_of_birth"),
    (re.compile(r"(?:^|_)(?:ssn|nino|national_id|national_insurance|tax_id|passport(?:_no|_number)?)(?:_|$)|"
                r"\b(?:social security|national insurance|passport) number\b", re.I), "personal_data:national_id"),
    (re.compile(r"(?:^|_)(?:card_number|card_no|pan|iban)(?:_|$)|\bcard number\b", re.I), "payment_card"),
    (re.compile(r"(?:^|_)(?:diagnosis|medical|health_condition)(?:_|$)|\b(?:diagnosis|medical history)\b", re.I),
     "special_category:health"),
    (re.compile(r"^(?:name|full_name|first_name|last_name|given_name|family_name|surname|middle_name|"
                r"(?:customer|client|holder|policyholder|member|contact|person|employee|insured|beneficiary)_name)$|"
                r"\b(?:full|first|last|given|family) name\b|\bsurname\b", re.I), "personal_data:name"),
)
COLUMN_TYPES = ("VARCHAR", "TEXT", "STRING", "CHAR", "DATE")


def column_findings(name: str, comment: str | None, data_type: str, values=()) -> list[str]:
    """The privacy families a column holds, by its name, its comment and a sample of its values: in COLUMN_DETECTORS'
    order, each once. A column of a type that cannot hold a person's attribute is none."""
    if not str(data_type or "").upper().startswith(COLUMN_TYPES):
        return []
    found = [value for pattern, value in COLUMN_DETECTORS if pattern.search(name) or (comment and pattern.search(comment))]
    for text in values:
        for pattern, value in DETECTORS:
            if isinstance(text, str) and pattern.search(text) and value not in found:
                found.append(value)
    return found
