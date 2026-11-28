"""No client-specific identifier from a client pack appears in the generic code.

The generic code is the planner, the compiler, the SQL gate and the admission code it relies on, the semantics, the ask
service and the warehouse query translation (GENERIC_CODE). A client's identifiers are read from every
clients/*/pack.yaml and its pack.lock:

- codes and names: table and column names, codes and values (any upper-case identifier, any snake_case name, any code
  with a digit, and a two-character code declared as a value), looked for anywhere in the source text, docstrings
  included: upper-case identifiers and codes exactly, snake_case names in any case;
- business phrases: every wording of two or more words under a wording key (PHRASE_KEYS), looked for anywhere in the
  source text, in any case;
- single words: every one-word wording or value (under PHRASE_KEYS or VALUE_KEYS, such as a group or a place name),
  looked for in the code's string literals other than docstrings, in any case, since a single word in prose or a name
  is ordinary English.

What is not client-specific is said here and nowhere else, each entry by hand with its reason:

- STRUCTURAL: the words requirement 19 keeps in kge/semantics/coverage.py (SCAFFOLD, SCAFFOLD_PHRASES, OPERATORS,
  OPERATOR_FAMILIES, GRAIN_WORDS, GRAIN_ADVERBS, RANK_WORDS, RANK_VERBS, SINGULAR_RANK_WORDS, SHARE_COMPLEMENTS): a
  wording made only of these words is grammar, not a client's wording;
- SQL_WORDS: SQL keywords and functions, which a pack's predicates and formulas use in upper case;
- NOT_DATA: the parts of a pack that hold no data identifier: its manifest, its schema version and its test sets (whose
  item ids, such as S01, are question ids);
- GENERIC: each identifier a pack declares that is also the platform's own word, with why;
- PENDING: a client word the owner has ruled must leave the generic code, allowed only in the named file until the
  named step removes it.
"""
from __future__ import annotations

import ast
import json
import re
from functools import lru_cache

import yaml

from kge.pipeline.settings import ROOT
from kge.semantics import coverage

GENERIC_CODE = (
    'kge/services/ask',
    'kge/semantics',
    'kge/compiler',
    'kge/pipeline/sqlguard.py',
    'kge/pipeline/textmatch.py',
    'kge/adapters/warehouse',
)
PHRASE_KEYS = frozenset((
    'phrase', 'phrases', 'wording', 'wordings', 'column_wording', 'term', 'qualified_terms', 'name', 'label',
    'synonyms', 'also', 'definition_terms',
))
VALUE_KEYS = frozenset(('value', 'values', 'code', 'codes'))
NOT_DATA = frozenset(('manifest', 'schema', 'tests'))
SQL_WORDS = frozenset('''
    ABS ALL AND ANY ARRAY AS ASC AVG BETWEEN BIGINT BOOL BOOLEAN BY CASE CAST COALESCE COUNT CURRENT_DATE DATE DAY DECIMAL
    DESC DISTINCT DOUBLE ELSE END EXISTS EXTRACT FALSE FLOAT FLOAT64 FROM GROUP HAVING IF IFNULL IN INNER INT INT64
    INTEGER INTERVAL IS JOIN LEFT LIKE LIMIT LOWER MAX MEDIAN MIN MONTH NOT NULL NULLIF NUMERIC ON OR ORDER OVER
    PARTITION QUARTER ROUND SELECT SQL STRING SUM THEN TIMESTAMP TRIM TRUE UPPER VARCHAR WEEK WHEN WHERE WITH YEAR
'''.split())
CALENDAR_NAMES = 'The calendar module’s month and weekday names, which the period grammar reads for every pack.'
PLATFORM_WORD = 'A pack-contract value (an operator, a kind, a pattern or a policy parameter) every pack may use.'
GENERIC = {
    'status_code': 'The HTTP response status attribute.',
    'product_id': 'The catalogue’s own "<unit>.<table>" product id variable.',
    'event_date': 'The meta-model period-binding kind (CALENDAR_KINDS), declared by every client pack.',
    'quarter_end': 'A meta-model grain (GRAINS).',
    'month_end': 'The date helper that gives a month’s last day.',
    'calendar_year': 'A platform period name (PERIOD_COLUMNS).',
    'calendar_date': 'The generic calendar table kind and its date column, in a docstring.',
    'FISCAL': 'The standard id STD-FISCAL-CAL in a note.',
    'EUR': 'An ISO 4217 currency code in the platform’s currency tables.',
    'US': 'The words "US dollar" in the platform’s currency names.',
    'XXX': 'The ISO 4217 code for no currency.',
    'business team': 'The platform’s generic access-refusal message.',
    'USD': 'An ISO 4217 currency code in the platform’s currency tables.',
    'fiscal_quarter': PLATFORM_WORD,
    'calendar_quarter': PLATFORM_WORD,
    'fiscal_year': PLATFORM_WORD,
    'month_start': PLATFORM_WORD,
    'as_of': PLATFORM_WORD,
    'as_reported': PLATFORM_WORD,
    'percent_change': PLATFORM_WORD,
    'count_distinct': PLATFORM_WORD,
    'not_in': PLATFORM_WORD,
    'is_null': PLATFORM_WORD,
    'is_true': PLATFORM_WORD,
    'scd2': PLATFORM_WORD,
    'valid_from': PLATFORM_WORD,
    'valid_to': PLATFORM_WORD,
    'is_current': PLATFORM_WORD,
    'effective_from': PLATFORM_WORD,
    'release_date': PLATFORM_WORD,
    'certified_by': PLATFORM_WORD,
    'certified_on': PLATFORM_WORD,
    'cross_reference': PLATFORM_WORD,
    'calendar_rollup': PLATFORM_WORD,
    'upper_2': PLATFORM_WORD,
    'upper_2_3': PLATFORM_WORD,
    'upper_4_plus': PLATFORM_WORD,
    'upper_letter': PLATFORM_WORD,
    'v1': 'A version label.',
    'pipeline_tagger': 'The pipeline\'s own service role, which every client\'s pack and the kge client template '
                       '(kge/estate/onboard/clients.py) define, and the tagging run acts as (kge/pipeline/run.py).',
    'DPV': 'The W3C Data Privacy Vocabulary, a preloaded standard.',
    'january': CALENDAR_NAMES, 'february': CALENDAR_NAMES, 'march': CALENDAR_NAMES, 'april': CALENDAR_NAMES,
    'june': CALENDAR_NAMES, 'july': CALENDAR_NAMES, 'august': CALENDAR_NAMES, 'september': CALENDAR_NAMES,
    'october': CALENDAR_NAMES, 'november': CALENDAR_NAMES, 'december': CALENDAR_NAMES,
    'jan': CALENDAR_NAMES, 'feb': CALENDAR_NAMES, 'mar': CALENDAR_NAMES, 'apr': CALENDAR_NAMES, 'jun': CALENDAR_NAMES,
    'jul': CALENDAR_NAMES, 'aug': CALENDAR_NAMES, 'sep': CALENDAR_NAMES, 'oct': CALENDAR_NAMES, 'nov': CALENDAR_NAMES,
    'dec': CALENDAR_NAMES,
    'monday': CALENDAR_NAMES, 'tuesday': CALENDAR_NAMES, 'wednesday': CALENDAR_NAMES, 'thursday': CALENDAR_NAMES,
    'friday': CALENDAR_NAMES, 'saturday': CALENDAR_NAMES, 'sunday': CALENDAR_NAMES,
    'certified': 'The meta-model’s certification concept (a certified mapping, a certified runtime).',
    'steward': 'The platform’s steward role, declared by every client pack.',
    'current': 'The platform’s current time mode and current row version.',
    'group': 'Ordinary English and SQL GROUP BY.',
    'clients': 'The clients/ folder of the pack store.',
    'unresolved': 'Ordinary English in a plan-check message.',
    'holding': 'Ordinary English in a compiler finding.',
    'pipeline': 'The platform’s tagging pipeline.',
    'admin': 'The platform’s admin role.',
    'analyst': 'Ordinary English in a model prompt (a reader of the answer).',
    'published': 'The catalogue’s published state.',
    'define': 'Ordinary English in the definition grammar, declared by every client pack.',
    'exclusion': 'The platform’s NOT IN filter grammar.',
    'came': 'Ordinary English in a field description.',
    'transaction': 'A SQL transaction in the BigQuery estate adapter.',
    'open': 'Ordinary English: a file that is opened.',
    'converted': 'Ordinary English about currency conversion.',
    'office': 'Ordinary English in a compiler finding (a certifying office).',
    'restated': 'A meta-model time mode (TIME_MODES).',
    'as reported': ('The platform’s words for the as_reported time mode in its own notes, messages and docstrings; the '
                    'question wordings that choose a time mode are read from the pack’s concepts.'),
    'current version': 'The platform’s words for the restated time mode in its own notes and docstrings.',
    'current versions': 'The platform’s words for the restated time mode in its own notes and docstrings.',
    'customer': 'The platform’s customer-group concept (each unit’s customer key) in its own messages.',
    'birth': 'Ordinary English in the platform’s personal-data guard (date of birth).',
}
SCOPE_WORDS = ('Owner decision pending (#474 guard, A): the all-units scope words move to the pack and are read '
               'from there; removed by its own commit after the capability steps.')
PENDING = {
    ('kge/services/ask/engine/question.py', 'bank'): SCOPE_WORDS,
    ('kge/services/ask/engine/question.py', 'enterprise'): SCOPE_WORDS,
}
CODE = re.compile(r'\b(?:[A-Z][A-Z0-9_]{2,}|[A-Za-z]+[0-9][A-Za-z0-9_]*|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b')
SHORT_CODE = re.compile(r'^[A-Z0-9]{2}$')
PHRASE = re.compile(r"^[a-z][a-z'&-]*(?: [a-z][a-z'&-]*){1,5}$")
WORD = re.compile(r"^[A-Za-z][A-Za-z'&-]{2,}$")


def structural_words() -> frozenset[str]:
    words = set(coverage.SCAFFOLD)
    for group in (coverage.SCAFFOLD_PHRASES, coverage.OPERATORS, coverage.GRAIN_WORDS, coverage.GRAIN_ADVERBS,
                  coverage.RANK_WORDS, coverage.RANK_VERBS, coverage.SINGULAR_RANK_WORDS, coverage.SHARE_COMPLEMENTS,
                  *coverage.OPERATOR_FAMILIES.values()):
        for phrase in group:
            words.update(str(phrase).lower().split())
    return frozenset(words)


def leaves(node, key=None):
    if isinstance(node, dict):
        for name, value in node.items():
            yield from leaves(value, str(name))
    elif isinstance(node, list):
        for value in node:
            yield from leaves(value, key)
    elif isinstance(node, str):
        yield key, node


def pack_texts(path):
    data = yaml.safe_load(path.read_text())
    texts = [leaf for section, body in data.items() if section not in NOT_DATA for leaf in leaves(body, section)]
    lock = path.with_name('pack.lock')
    if lock.exists():
        for source in json.loads(lock.read_text()).get('sources', {}).values():
            for table in source.get('tables', ()):
                texts.append(('table', table['name']))
                texts.extend(('column', column['name']) for column in table.get('columns', ()))
    return texts


@lru_cache(maxsize=1)
def client_identifiers() -> tuple[dict[str, str], dict[str, str]]:
    """Each client-specific identifier with the packs that declare it: the codes, names and phrases, and the single
    words."""
    structural = structural_words()
    generic = {identifier.casefold() for identifier in GENERIC}
    found: dict[str, set[str]] = {}
    words: dict[str, set[str]] = {}
    for path in sorted((ROOT / 'clients').glob('*/pack.yaml')):
        pack = path.parent.name
        for key, text in pack_texts(path):
            stripped = text.strip()
            tokens = list(CODE.findall(text))
            if key in VALUE_KEYS and SHORT_CODE.match(stripped) and not stripped.isdigit():
                tokens.append(stripped)
            phrase = stripped.lower()
            if key in PHRASE_KEYS and PHRASE.match(phrase) and not set(phrase.split()) <= structural:
                tokens.append(phrase)
            for token in tokens:
                if token not in SQL_WORDS and token.casefold() not in generic:
                    found.setdefault(token, set()).add(pack)
            if ((key in PHRASE_KEYS or key in VALUE_KEYS) and WORD.match(stripped) and not stripped.isupper()
                    and phrase not in structural and phrase not in generic):
                words.setdefault(phrase, set()).add(pack)
    named = {identifier: ', '.join(sorted(packs)) for identifier, packs in found.items()}
    return named, {word: ', '.join(sorted(packs)) for word, packs in words.items() if word not in named}


def generic_sources():
    for entry in GENERIC_CODE:
        path = ROOT / entry
        yield from sorted(path.rglob('*.py')) if path.is_dir() else [path]


def pattern(identifier: str) -> re.Pattern:
    body = r'\b' + re.escape(identifier).replace(r'\ ', r'\s+') + r'\b'
    exact = identifier.isupper() or any(ch.isdigit() for ch in identifier) and identifier[0].isupper()
    return re.compile(body) if exact else re.compile(body, re.IGNORECASE)


def string_literals(tree):
    """Each string literal of a module but its docstrings, with its line."""
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def alternation(identifiers, flags=0) -> re.Pattern | None:
    """One pattern for many identifiers, longest first, each on word boundaries."""
    body = '|'.join(re.escape(i).replace(r'\ ', r'\s+') for i in sorted(identifiers, key=len, reverse=True))
    return re.compile(r'\b(?:' + body + r')\b', flags)


def hits() -> list[str]:
    named, words = client_identifiers()
    exact = [i for i in named if pattern(i).flags & re.IGNORECASE == 0]
    folded = {' '.join(i.split()).casefold(): i for i in named if i not in exact}
    exact_pattern, folded_pattern = alternation(exact), alternation(folded, re.IGNORECASE)
    word_pattern = alternation(words, re.IGNORECASE)
    out = set()
    for path in generic_sources():
        source = path.read_text()
        where = str(path.relative_to(ROOT))
        for number, line in enumerate(source.splitlines(), 1):
            for found in exact_pattern.finditer(line):
                out.add(f'{where}:{number}: {found.group()!r} ({named[found.group()]})')
            for found in folded_pattern.finditer(line):
                identifier = folded[' '.join(found.group().split()).casefold()]
                out.add(f'{where}:{number}: {identifier!r} ({named[identifier]})')
        for number, text in string_literals(ast.parse(source)):
            for found in word_pattern.finditer(text):
                word = found.group().casefold()
                if (where, word) not in PENDING:
                    out.add(f'{where}:{number}: {word!r} ({words[word]})')
    return sorted(out)


def test_the_packs_declare_identifiers_to_look_for():
    named, words = client_identifiers()
    assert any(identifier.isupper() for identifier in named)
    assert any(' ' in identifier for identifier in named)
    assert words


def test_every_generic_entry_says_why():
    assert all(reason.strip().endswith('.') for reason in [*GENERIC.values(), *PENDING.values()])


def test_every_pending_exception_is_still_needed():
    _, words = client_identifiers()
    stale = [key for key in PENDING if key[1] not in words or not any(
        re.search(r'\b' + re.escape(key[1]) + r'\b', text, re.IGNORECASE)
        for _, text in string_literals(ast.parse((ROOT / key[0]).read_text())))]
    assert not stale, stale


def test_no_client_identifier_appears_in_generic_code():
    found = hits()
    assert not found, 'client-specific identifiers in generic code:\n' + '\n'.join(found)
