#!/usr/bin/env python3
"""Disclosure guard for every committed file in this repository.

CLAUDE.md states two rules this script enforces mechanically:

  * "No client PII, counterparty data, credentials, or internal hostnames in
    any committed file."
  * "Client names stay generalised until a document is scoped to a signed
    engagement."

The repository is client-facing material. A leak here is not a build failure —
it is a document sent to the wrong reader. So the guard runs on every pull
request, and it is written to be quiet enough to survive that: a check that
cries wolf gets disabled, and a disabled disclosure guard is worse than none.

Five families of finding, each a named rule:

  email        addresses outside an explicit allowlist and the example domains
  credential   AWS and Google key shapes, bearer tokens, JWTs, private key
               headers, connection strings carrying a password, and assignments
               that put a literal value on a secret-shaped name
  hostname     *.internal, *.corp, *.local, *.intranet, and RFC 1918 addresses
               with or without a port
  identifier   IBANs (checksum-validated), US SSNs, payment card numbers (Luhn-
               validated), and unbroken digit runs long enough to be an account
               number rather than a figure
  institution  real banks and insurers named where this repository uses its
               fictional reference institutions — Meridian Union Bank,
               Thornbury Mutual Casualty — or a generalised description

Precision, rather than recall, is the design constraint. Concretely:

  * "$1,187,000" and "25,000" are figures, not account numbers: the digit-run
    rule only sees unbroken runs, and ignores numbers inside HTML attributes,
    which is where the cost model's slider bounds live.
  * "§10.1" and "10.3" are section numbers: the RFC 1918 rule requires a full
    dotted quad with valid octets.
  * "*.local.md" in .gitignore is a glob: the internal-hostname rule requires
    the suffix to end the hostname.
  * Collibra, Snowflake, Actimize, Verafin, Oracle FCCM, NICE, SAS, IVANS,
    Braintrust, Langfuse and Databricks are tooling, not clients, and are not
    on the institution list. Names that are also ordinary English — Chase,
    Discover, Travelers, Ally — only count when a financial qualifier follows
    them, so "Week 1 — Discover: baseline" stays quiet.

Anything the rules still get wrong is exempted by line in
``tools/disclosure-allow.txt``, with a reason. There is no ``--fix``: a
credential is revoked and a client name is rewritten by a person who knows
which, and a script that edited either would only hide the problem.

This file and the allowlist are not scanned. A file that defines the patterns
necessarily contains them.

Findings print as "path:line: message" so GitHub annotates the offending line.
Matched text is redacted in the output — a CI log is not a safe place to
reprint a key.

Usage::

    python3 tools/check_disclosure.py            # every tracked file
    python3 tools/check_disclosure.py PATH...    # named files
    python3 tools/check_disclosure.py --list     # rules and scope, then exit 0

Exit 0 when nothing fires, 1 otherwise. Standard library only. No network.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional

# Output is pinned to UTF-8 rather than the process locale: these scripts echo
# document content, which carries em dashes and box-drawing characters, and a
# runner under a C locale would otherwise fail on the report rather than on the
# content it is reporting.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join("tools", "disclosure-allow.txt")
SELF = os.path.join("tools", "check_disclosure.py")

# The guard's own source states every pattern it looks for, and the allowlist
# quotes the text it exempts. Scanning either reports the specification.
UNSCANNED = {SELF, ALLOWLIST}

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}

# Suffixes worth reading as text. Everything else is skipped as a binary or an
# export; a NUL byte is treated as binary whatever the suffix says.
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".html", ".htm", ".css", ".js", ".json",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".py", ".sh", ".csv", ".xml",
    ".svg", ".drawio", ".lock", ".tf", ".tfvars", ".hcl", ".tfbackend",
    ".editorconfig", ".gitignore", ".gitattributes",
}


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str          # repository-relative
    line: int          # 1-based
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.message}"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def redact(text: str, keep: int = 4) -> str:
    """Show enough of a match to find it in the file, not enough to use it."""
    text = " ".join(text.split())
    if len(text) <= keep:
        return "*" * len(text)
    return f"{text[:keep]}... ({len(text)} chars)"


def redact_email(text: str) -> str:
    """Keep the domain — that is what tells a reviewer whose address it is."""
    local, _, domain = text.partition("@")
    head = local[:1] if local else ""
    return f"{head}{'*' * max(len(local) - 1, 1)}@{domain}"


# ---------------------------------------------------------------------------
# Validators — the arithmetic that separates a real identifier from a figure
# ---------------------------------------------------------------------------


def iban_valid(candidate: str) -> bool:
    """ISO 13616 mod-97 check. Rejects the many strings shaped like an IBAN."""
    raw = candidate.replace(" ", "").upper()
    if not 15 <= len(raw) <= 34:
        return False
    rotated = raw[4:] + raw[:4]
    digits = ""
    for char in rotated:
        if char.isdigit():
            digits += char
        elif "A" <= char <= "Z":
            digits += str(ord(char) - 55)
        else:
            return False
    return int(digits) % 97 == 1


def luhn_valid(candidate: str) -> bool:
    """Mod-10 check. A grouped sixteen-digit figure that is not a card fails."""
    digits = [int(c) for c in candidate if c.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def octets_valid(address: str) -> bool:
    """A dotted quad whose octets are all 0-255. '10.1.2.999' is not an address."""
    parts = address.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and int(part) <= 255 for part in parts)


# ---------------------------------------------------------------------------
# Rule 1 — email addresses
# ---------------------------------------------------------------------------

# The domain must end the address. A "." continues it only with a letter or
# digit on its far side, so an address that ends a sentence is still caught.
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"[A-Za-z0-9._%+-]{1,64}"
    r"@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9-])(?!\.[A-Za-z0-9])"
)

# Addresses that are documentation or repository metadata rather than a person
# a client could be contacted through. RFC 2606 reserves the example domains.
EMAIL_ALLOWED_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu",
    "invalid", "test", "localhost",
}
EMAIL_ALLOWED_ADDRESSES = {
    "noreply@anthropic.com",          # commit trailer, git metadata
    "noreply@github.com",             # commit trailer, git metadata
}


MOCK_SERVICE_ACCOUNT_RE = re.compile(
    r"(?:"
    r"stand-in@your-(?:pilot-)?project-id|"
    r"service-0@gs-project-accounts|"
    r"bq-0@bigquery-encryption|"
    r"service-0@gcp-sa-stand-in"
    r")\.iam\.gserviceaccount\.com"
)


def placeholder_service_account(address: str, path: str) -> bool:
    """Whether a Terraform test uses one of the mock provider's stand-ins."""
    return (
        path.endswith(".tftest.hcl")
        and MOCK_SERVICE_ACCOUNT_RE.fullmatch(address) is not None
    )


def scan_email(text: str, path: str) -> Iterator[tuple[int, str, str]]:
    for match in EMAIL_RE.finditer(text):
        address = match.group(0)
        lowered = address.lower()
        # Generated upstream licence texts reproduce maintainers' public notices verbatim. Exempt email addresses in
        # that one output, while every credential, identifier, hostname and institution scanner still reads the file.
        if path == "demos/kge/THIRD_PARTY_NOTICES.md":
            continue
        if lowered in EMAIL_ALLOWED_ADDRESSES:
            continue
        domain = lowered.partition("@")[2]
        if domain in EMAIL_ALLOWED_DOMAINS or domain.rsplit(".", 1)[-1] in {
            "invalid", "test", "localhost", "example",
        }:
            continue
        # Terraform mock providers need syntactically valid service-account
        # addresses. Exempt only the five forms committed in their test files;
        # the same address in documentation or production Terraform is caught.
        if placeholder_service_account(lowered, path):
            continue
        yield (
            match.start(),
            "email",
            f"email address {redact_email(address)} — no contact address for a "
            f"named individual belongs in a committed file",
        )


# ---------------------------------------------------------------------------
# Rule 2 — credential shapes
# ---------------------------------------------------------------------------

# Long-lived AWS identifiers. The prefixes are the documented ones; the body is
# exactly sixteen upper-case alphanumerics.
AWS_KEY_RE = re.compile(
    r"(?<![A-Z0-9])(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|APKA|AROA)"
    r"[0-9A-Z]{16}(?![A-Z0-9])"
)

GOOGLE_KEY_RE = re.compile(r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![A-Za-z0-9_-])")

SLACK_TOKEN_RE = re.compile(r"\bxox[abeoprs]-[0-9A-Za-z-]{10,}")

GITHUB_TOKEN_RE = re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}")

BEARER_RE = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}")

JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")

# Written so the pattern does not match its own source: the character class
# sits where the key type would.
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")

# scheme://user:password@host — a connection string with the password in it.
# The password must not be empty and must not be a bare port number.
CONNECTION_STRING_RE = re.compile(
    r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,64}:(?P<secret>[^\s:/@]{1,128})@[^\s/]{1,128}"
)

# An assignment putting a literal on a secret-shaped name. Prose such as
# "one scoped, least-privilege credential per tool" has no assignment and does
# not match; neither does a placeholder or an environment lookup.
SECRET_ASSIGN_RE = re.compile(
    r"\b(?P<name>api[_-]?key|apikey|secret[_-]?key|client[_-]?secret|secret|"
    r"pass(?:word|wd)?|pwd|access[_-]?token|auth[_-]?token|refresh[_-]?token|"
    r"private[_-]?key|aws[_-]?secret[_-]?access[_-]?key)"
    r"\s*[:=]\s*"
    r"(?P<quote>[\"'])(?P<value>[^\"'\n]{6,})(?P=quote)",
    re.IGNORECASE,
)

# Values that are obviously not a credential: placeholders, references and
# instructions to supply one.
PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"[<{\[(].*|"                       # <your-key>, {{ secret }}, ${VAR}
    r"\$.*|"                            # $SECRET, ${SECRET}
    r"%[sd].*|"                         # printf placeholder
    r"\**|"                             # ****
    r"[xX]{3,}|"                        # xxxxxx
    r"(?:your|my|the)[_\- ].*|"
    r"(?:changeme|change_me|placeholder|redacted|example|dummy|sample|"
    r"none|null|nil|true|false|todo|tbd|unset|omitted)"
    r")$",
    re.IGNORECASE,
)
ENV_REFERENCE_RE = re.compile(
    r"(os\.environ|process\.env|getenv|secrets?\.|vault|keyring|\$\{)", re.IGNORECASE
)


def scan_credential(text: str, path: str) -> Iterator[tuple[int, str, str]]:
    simple: tuple[tuple[re.Pattern[str], str], ...] = (
        (AWS_KEY_RE, "AWS access key identifier"),
        (GOOGLE_KEY_RE, "Google API key"),
        (SLACK_TOKEN_RE, "Slack token"),
        (GITHUB_TOKEN_RE, "GitHub token"),
        (BEARER_RE, "bearer token"),
        (JWT_RE, "JSON web token"),
        (PRIVATE_KEY_RE, "private key block"),
    )
    for pattern, label in simple:
        for match in pattern.finditer(text):
            yield (
                match.start(),
                "credential",
                f"{label} {redact(match.group(0))} — revoke it, then remove it",
            )

    for match in CONNECTION_STRING_RE.finditer(text):
        secret = match.group("secret")
        if secret.isdigit():        # scheme://host:port@... is not a password
            continue
        yield (
            match.start(),
            "credential",
            f"connection string carrying a password "
            f"{redact(match.group(0), keep=12)} — revoke it, then remove it",
        )

    for match in SECRET_ASSIGN_RE.finditer(text):
        value = match.group("value").strip()
        if PLACEHOLDER_RE.match(value) or ENV_REFERENCE_RE.search(value):
            continue
        yield (
            match.start(),
            "credential",
            f"{match.group('name')} assigned a literal value "
            f"{redact(value)} — revoke it, then remove it",
        )


# ---------------------------------------------------------------------------
# Rule 3 — internal hostnames and RFC 1918 addresses
# ---------------------------------------------------------------------------

# The suffix must end the hostname. This keeps the rule off the ".local.md"
# glob in .gitignore and off any "something.corp.example" prose. A "." after
# the suffix continues the name only when a letter or digit follows it, past any
# underscores or hyphens, so "db1.corp._tcp" is skipped while a hostname that
# ends a sentence, in underscore italics or not, is still caught.
INTERNAL_HOST_RE = re.compile(
    r"(?<![A-Za-z0-9._-])"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+"
    r"(?P<suffix>internal|corp|local|intranet|lan)"
    r"(?![A-Za-z0-9_-])(?!\.[_-]*[A-Za-z0-9])",
    re.IGNORECASE,
)

# A "." after the address continues it only with a letter or digit on its far
# side, so a fifth octet or a version such as "10.1.2.3.4" or "10.1.2.3.dev0"
# is skipped, while an address that ends a sentence is still caught.
PRIVATE_IP_RE = re.compile(
    r"(?<![\d.])"
    r"(?P<address>"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")"
    r"(?P<port>:\d{1,5})?"
    r"(?!\d)(?!\.[A-Za-z0-9])"
)


def scan_hostname(text: str, path: str) -> Iterator[tuple[int, str, str]]:
    for match in INTERNAL_HOST_RE.finditer(text):
        host = match.group(0)
        yield (
            match.start(),
            "hostname",
            f"internal hostname {host} — name the system by role, not by address",
        )

    for match in PRIVATE_IP_RE.finditer(text):
        address = match.group("address")
        if not octets_valid(address):
            continue
        yield (
            match.start(),
            "hostname",
            f"private network address {address}{match.group('port') or ''} — "
            f"name the system by role, not by address",
        )


# ---------------------------------------------------------------------------
# Rule 4 — account and personal identifiers
# ---------------------------------------------------------------------------

IBAN_RE = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?(?![A-Z0-9])")

# Areas 000, 666 and 900-999 are never issued; group 00 and serial 0000 never
# are either. Excluding them keeps the rule off dates and reference codes.
SSN_RE = re.compile(
    r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])"
)

CARD_RE = re.compile(r"(?<![\d-])(?:\d{4}[ -]){3}\d{4}(?![\d-])")

# Nine unbroken digits. Every legitimate figure in this repository is either
# grouped ("$1,187,000", "25,000") or shorter than this. A run inside a decimal
# or a digit group ("3.141592653589", "1,234567890") is skipped, so a "." or ","
# exempts it only with a digit on its far side; a run that ends a sentence
# ("is 123456789012.") or precedes a comma ("123456789012, which") still fires.
DIGIT_RUN_RE = re.compile(r"(?<!\d)(?<!\d[.,])\d{9,}(?![.,]?\d)")

HTML_TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
# A complete SHA-256 content digest, as an image reference pins it ("sha256:" and exactly 64 lower-case hex digits,
# bounded on both sides). Its hex can hold a run of nine or more decimal digits, which is never an account number.
# Only a whole, well-formed digest qualifies: a shorter or longer hex string, or bare digits, still reach the rule.
SHA256_DIGEST_RE = re.compile(r"(?<![0-9A-Za-z])sha256:[0-9a-f]{64}(?![0-9A-Za-z])")

TERRAFORM_CHECKSUM_RE = re.compile(
    r'(?m)^[ \t]*"(?:h1:[A-Za-z0-9+/]{43}=|zh:[0-9a-f]{64})",?[ \t]*$'
)
TERRAFORM_ZERO_ORG_RE = re.compile(
    r'(?m)^[ \t]*organization_id[ \t]*=[ \t]*"0{12}"[ \t]*$'
)


def html_tag_spans(text: str, path: str) -> list[tuple[int, int]]:
    """Character ranges inside HTML tags.

    The cost model states slider bounds as ``max="100000000"``. Those are
    attribute values, not account numbers, and the digit-run rule skips them.
    """
    if not path.lower().endswith((".html", ".htm")):
        return []
    return [(m.start(), m.end()) for m in HTML_TAG_RE.finditer(text)]


def scan_identifier(text: str, path: str) -> Iterator[tuple[int, str, str]]:
    for match in IBAN_RE.finditer(text):
        candidate = match.group(0)
        if not iban_valid(candidate):
            continue
        yield (
            match.start(),
            "identifier",
            f"IBAN {redact(candidate, keep=6)} — counterparty account data",
        )

    for match in SSN_RE.finditer(text):
        yield (
            match.start(),
            "identifier",
            f"US social security number {redact(match.group(0), keep=3)} — "
            f"personally identifying",
        )

    for match in CARD_RE.finditer(text):
        if not luhn_valid(match.group(0)):
            continue
        yield (
            match.start(),
            "identifier",
            f"payment card number {redact(match.group(0), keep=4)} — "
            f"counterparty account data",
        )

    tag_spans = html_tag_spans(text, path)
    # Terraform lock files are generated inventories of public provider
    # checksums. Limit the exemption to digit runs inside their h1:/zh: tokens:
    # skipping the whole file would also hide an identifier added in a comment.
    checksum_spans = (
        [(m.start(), m.end()) for m in TERRAFORM_CHECKSUM_RE.finditer(text)]
        if path.endswith("/.terraform.lock.hcl")
        else []
    )
    # The Terraform mock uses the syntactically valid all-zero organisation;
    # exempt that assignment, not all-zero identifiers in arbitrary content.
    zero_org_spans = (
        [(m.start(), m.end()) for m in TERRAFORM_ZERO_ORG_RE.finditer(text)]
        if path.endswith(".tftest.hcl")
        else []
    )
    digest_spans = [(m.start(), m.end()) for m in SHA256_DIGEST_RE.finditer(text)]
    for match in DIGIT_RUN_RE.finditer(text):
        if any(start <= match.start() < end for start, end in tag_spans):
            continue
        if any(start <= match.start() and match.end() <= end for start, end in digest_spans):
            continue
        if any(start <= match.start() < end for start, end in checksum_spans):
            continue
        if any(start <= match.start() < end for start, end in zero_org_spans):
            continue
        run = match.group(0)
        yield (
            match.start(),
            "identifier",
            f"unbroken run of {len(run)} digits {redact(run, keep=4)} — an "
            f"account number reads like this; a figure is grouped",
        )


# ---------------------------------------------------------------------------
# Rule 5 — real financial institutions
# ---------------------------------------------------------------------------

# Names distinctive enough that an occurrence is the institution. Technology
# vendors are deliberately absent: Collibra, Snowflake, Actimize, Verafin,
# Oracle FCCM, NICE, SAS, IVANS, Braintrust, Langfuse and Databricks appear in
# these documents as tooling, and tooling is named plainly.
INSTITUTIONS_UNAMBIGUOUS = (
    # North America
    "JPMorgan", "JP Morgan", "Citibank", "Citigroup", "Wells Fargo",
    "Bank of America", "Goldman Sachs", "Morgan Stanley", "Capital One",
    "Scotiabank", "Royal Bank of Canada", "Truist", "Fifth Third", "KeyBank",
    "PNC Bank", "Charles Schwab", "American Express", "Wachovia",
    "Silicon Valley Bank", "First Republic", "Navy Federal", "USAA",
    # Europe
    "HSBC", "Barclays", "NatWest", "Lloyds Bank", "Lloyds Banking",
    "Santander", "Deutsche Bank", "Commerzbank", "Credit Suisse",
    "BNP Paribas", "Societe Generale", "Société Générale", "Rabobank",
    "Standard Chartered", "Danske Bank", "Nordea", "UniCredit",
    "Intesa Sanpaolo", "Banco Bilbao", "ING Bank", "ING Group", "Monzo",
    "Starling Bank", "Revolut", "Klarna", "N26",
    # Asia-Pacific
    "Mizuho", "Sumitomo Mitsui", "Mitsubishi UFJ", "Nomura",
    "Bank of China", "Westpac", "Commonwealth Bank", "DBS Bank",
    "Standard Bank", "Itau", "Itaú",
    # Insurance and reinsurance
    "Allianz", "Munich Re", "Swiss Re", "Berkshire Hathaway", "MetLife",
    "Aviva", "Zurich Insurance", "Legal & General", "State Farm",
    "Liberty Mutual", "Geico", "Aflac", "Cigna", "Humana", "Anthem Inc",
    "Lloyd's of London", "Lloyds of London", "Hiscox", "Beazley",
    "Markel", "Arch Capital", "Everest Re", "QBE Insurance", "RSA Insurance",
    # Cards and payments
    "Mastercard", "Western Union", "MoneyGram",
)

# Acronyms carry no ordinary-English reading, but must match case-sensitively:
# "UBS" is a bank, "ubs" in a word is not.
INSTITUTIONS_ACRONYM = ("UBS", "AIG", "BBVA", "ICBC", "ANZ", "NAB", "AXA", "BNY Mellon")

# Names that are also ordinary English. These only count when a financial
# qualifier sits beside them — which is why "Week 1 — Discover: baseline" and
# "Discover the as-performed workflow" stay quiet.
INSTITUTIONS_QUALIFIED = (
    "Chase", "Discover", "Ally", "Visa", "Progressive", "Travelers",
    "Nationwide", "Regions", "Citizens", "Prudential", "Principal",
    "Synchrony", "Chubb", "Hartford", "Sentry", "Amica", "Erie", "Root",
)
QUALIFIER = (
    r"(?:Bank|Banking|Bancorp|Financial|Finance|Insurance|Insurers?|Assurance|"
    r"Casualty|Mutual|Securities|Card|Cards|Group|Holdings|Corp\.?|"
    r"Corporation|Inc\.?|plc|N\.A\.|Life)"
)


def _alternation(names: Iterable[str]) -> str:
    """A grouped alternation, longest first.

    The group matters: an ungrouped alternation binds a surrounding lookbehind
    to the first branch only, which lets "UBS" match inside "SUBSTRATE".
    """
    return "(?:" + "|".join(
        re.escape(n) for n in sorted(names, key=len, reverse=True)) + ")"


INSTITUTION_UNAMBIGUOUS_RE = re.compile(
    rf"(?<![A-Za-z]){_alternation(INSTITUTIONS_UNAMBIGUOUS)}(?![A-Za-z])",
    re.IGNORECASE,
)
INSTITUTION_ACRONYM_RE = re.compile(
    rf"(?<![A-Za-z]){_alternation(INSTITUTIONS_ACRONYM)}(?![A-Za-z])"
)
INSTITUTION_QUALIFIED_RE = re.compile(
    rf"(?<![A-Za-z])"
    rf"(?:(?:{_alternation(INSTITUTIONS_QUALIFIED)})\s+{QUALIFIER}"
    rf"|{QUALIFIER}\s+of\s+(?:{_alternation(INSTITUTIONS_QUALIFIED)}))"
    rf"(?![A-Za-z])"
)

GENERALISED = (
    "the fictional reference institutions (Meridian Union Bank, Thornbury "
    "Mutual Casualty) or a generalised description (\"mid-sized bank\") "
    "until the document is scoped to a signed engagement"
)


def scan_institution(text: str, path: str) -> Iterator[tuple[int, str, str]]:
    for pattern in (
        INSTITUTION_UNAMBIGUOUS_RE,
        INSTITUTION_ACRONYM_RE,
        INSTITUTION_QUALIFIED_RE,
    ):
        for match in pattern.finditer(text):
            yield (
                match.start(),
                "institution",
                f"real financial institution \"{match.group(0)}\" — use "
                f"{GENERALISED}",
            )


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

Scanner = Callable[[str, str], Iterator[tuple[int, str, str]]]

RULES: tuple[tuple[str, str, Scanner], ...] = (
    ("email", "contact addresses outside the example domains", scan_email),
    ("credential", "key, token, private key and password shapes", scan_credential),
    ("hostname", "*.internal/.corp/.local/.intranet and RFC 1918 addresses", scan_hostname),
    ("identifier", "IBAN, SSN, card number, account-length digit runs", scan_identifier),
    ("institution", "real banks and insurers named as a client", scan_institution),
)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Exemption:
    path: str
    line: int
    rule: Optional[str]     # None exempts every rule on that line
    reason: str


def load_allowlist(allow_path: str) -> tuple[list[Exemption], list[Finding]]:
    """Parse tools/disclosure-allow.txt.

    One exemption per line, three pipe-separated fields and an optional rule:

        docs/reference/example.md | 42 | email | why this address is fine
        docs/reference/example.md | 42 | | why every rule is fine on this line

    A reason is mandatory. An entry without one is itself a finding — an
    allowlist whose entries are unexplained is a list of unexamined leaks.
    """
    exemptions: list[Exemption] = []
    problems: list[Finding] = []
    rel = os.path.relpath(allow_path, REPO_ROOT)
    if not os.path.exists(allow_path):
        return exemptions, problems

    with open(allow_path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, start=1):
            entry = raw.split("#", 1)[0].strip()
            if not entry:
                continue
            fields = [field.strip() for field in entry.split("|")]
            if len(fields) != 4:
                problems.append(Finding(
                    rel, number, "allowlist",
                    "expected 'path | line | rule | reason' — four fields "
                    f"separated by '|', found {len(fields)}"))
                continue
            path_field, line_field, rule_field, reason = fields
            if not line_field.isdigit():
                problems.append(Finding(
                    rel, number, "allowlist",
                    f"line number expected, found {line_field!r}"))
                continue
            if rule_field and rule_field not in {name for name, _, _ in RULES}:
                problems.append(Finding(
                    rel, number, "allowlist",
                    f"unknown rule {rule_field!r}"))
                continue
            if len(reason) < 10:
                problems.append(Finding(
                    rel, number, "allowlist",
                    "a reason is mandatory — say why this line is not a disclosure"))
                continue
            exemptions.append(Exemption(
                path_field.replace("\\", "/"), int(line_field),
                rule_field or None, reason))
    return exemptions, problems


def exempted(finding: Finding, exemptions: list[Exemption]) -> bool:
    for entry in exemptions:
        if entry.path != finding.path or entry.line != finding.line:
            continue
        if entry.rule is None or entry.rule == finding.rule:
            return True
    return False


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def tracked_files() -> list[str]:
    """Every file git knows about. Falls back to a walk outside a checkout."""
    try:
        output = subprocess.run(
            ["git", "-C", REPO_ROOT, "ls-files", "-z"],
            capture_output=True, text=True, check=True).stdout
        names = [name for name in output.split("\0") if name]
        if names:
            return sorted(names)
    except (OSError, subprocess.CalledProcessError):
        pass

    names = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            full = os.path.join(root, name)
            names.append(os.path.relpath(full, REPO_ROOT).replace(os.sep, "/"))
    return sorted(names)


def display_path(given: str) -> str:
    """Repository-relative where possible — that is what GitHub annotates.

    A path outside the checkout is printed as given rather than as a stack of
    "../", which no annotation can resolve anyway.
    """
    relative = os.path.relpath(os.path.abspath(given), REPO_ROOT).replace(os.sep, "/")
    if relative.startswith("../"):
        return given
    return relative


def readable(rel_path: str) -> bool:
    if rel_path in UNSCANNED:
        return False
    parts = rel_path.split("/")
    if any(part in SKIP_DIRS for part in parts):
        return False
    suffix = os.path.splitext(rel_path)[1].lower()
    if not suffix:
        # Extensionless files (LICENSE, Makefile) are still text worth reading.
        return True
    return suffix in TEXT_SUFFIXES


def read_text(full_path: str) -> Optional[str]:
    try:
        with open(full_path, "rb") as handle:
            blob = handle.read()
    except OSError:
        return None
    if b"\0" in blob:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def line_of(offset: int, starts: list[int]) -> int:
    """1-based line number for a character offset, by binary search."""
    low, high = 0, len(starts) - 1
    while low < high:
        mid = (low + high + 1) // 2
        if starts[mid] <= offset:
            low = mid
        else:
            high = mid - 1
    return low + 1


def scan_file(rel_path: str, text: str) -> list[Finding]:
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)

    findings: list[Finding] = []
    for _, _, scanner in RULES:
        for offset, rule, message in scanner(text, rel_path):
            findings.append(Finding(rel_path, line_of(offset, starts), rule, message))
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def describe_rules() -> str:
    lines = ["check_disclosure: rules"]
    for name, summary, _ in RULES:
        lines.append(f"  {name:<12} {summary}")
    lines.append("")
    lines.append("check_disclosure: scope")
    lines.append("  every git-tracked text file, except the guard's own source")
    lines.append(f"  and its allowlist ({ALLOWLIST})")
    lines.append("")
    lines.append("check_disclosure: allowlist entry")
    lines.append("  path | line | rule (optional) | reason (mandatory)")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on client PII, credentials, internal hostnames and "
                    "real institution names in committed files.")
    parser.add_argument("paths", nargs="*",
                        help="files to check (default: every tracked text file)")
    parser.add_argument("--list", action="store_true",
                        help="print the rules, the scope and the allowlist "
                             "format, then exit 0")
    args = parser.parse_args(argv)

    if args.list:
        print(describe_rules())
        return 0

    explicit = bool(args.paths)
    if explicit:
        candidates = [display_path(given) for given in args.paths]
    else:
        candidates = tracked_files()

    exemptions, findings = load_allowlist(os.path.join(REPO_ROOT, ALLOWLIST))

    scanned = 0
    missing: list[str] = []
    for rel_path in candidates:
        full = os.path.join(REPO_ROOT, rel_path)
        if explicit and not os.path.isfile(full):
            # A named path that is not there is an error; a tracked path that
            # has just been deleted in the working tree is not.
            missing.append(rel_path)
            continue
        if not readable(rel_path):
            continue
        text = read_text(full)
        if text is None:
            continue
        scanned += 1
        findings.extend(scan_file(rel_path, text))

    if missing:
        for name in missing:
            print(f"check_disclosure: no such file: {name}", file=sys.stderr)
        return 1

    kept = [f for f in findings if not exempted(f, exemptions)]
    suppressed = len(findings) - len(kept)

    for finding in sorted(kept, key=lambda f: (f.path, f.line, f.rule)):
        print(finding.render())

    if kept:
        print(f"\ncheck_disclosure: {len(kept)} finding(s) across {scanned} file(s)",
              file=sys.stderr)
        return 1

    note = f", {suppressed} allowlisted" if suppressed else ""
    print(f"check_disclosure: {scanned} file(s) clean{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
