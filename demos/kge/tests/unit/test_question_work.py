"""Repeated lexical work preserves evidence and stays within its release and access scope."""
from dataclasses import asdict, replace
import json
import re
from types import SimpleNamespace

import pytest

from kge.semantics import coverage
from kge.semantics.plan import Binding
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.services.ask.engine import question
from kge.services.ask.engine import path, wording
from tests.plan_fixtures import semantics, vocabulary


def encoded(pieces):
    return json.dumps([asdict(p) for p in pieces], default=str, sort_keys=True).encode()


@pytest.mark.parametrize('text', [
    'Metric in June 2025', 'Metric April through June 2025',
    'Metric in ２０２５-０６', 'top 3 Metric by category',
    'Metric above $20K', 'Metric unknown', 'Metric in FY26-Q1',
])
def test_repeated_ledger_is_byte_identical_to_uncached_evidence(text, monkeypatch):
    vocab = vocabulary()
    original = coverage._ledger
    built = []

    def read(text, turn, scoped):
        built.append((text, turn, scoped.binding))
        return original(text, turn, scoped)

    monkeypatch.setattr(coverage, '_ledger', read)
    for turn in ('first', 'second'):
        expected = encoded(original(text, turn, vocab))
        assert encoded(coverage.ledger(text, turn, vocab)) == expected
        assert encoded(coverage.ledger(text, turn, vocab)) == expected
    assert len(built) == 2


@pytest.mark.parametrize('release,principal', [('r', 'restricted'), ('new', 'full')])
def test_same_question_never_reuses_another_release_or_principals_names(release, principal):
    sem = semantics()
    full = vocabulary(sem)
    text = 'Metric value'
    first = coverage.ledger(text, 'turn', full)
    binding = Binding(release_identity=release, access_fingerprint=principal, products=(), purpose='analysis')
    scoped = build_vocabulary(sem, binding, [] if principal == 'restricted' else ['u'], ['u'],
                              [MeasureSpec('u.metric', 'USD', 'entities', True)],
                              aliases=[('new name', 'u.metric')])
    current = coverage.ledger(text, 'turn', scoped)
    assert current == coverage._ledger(text, 'turn', scoped)
    assert encoded(first) == encoded(coverage.ledger(text, 'turn', full))
    if principal == 'restricted':
        assert all(not p.candidates for p in current)
    else:
        assert coverage.ledger('new name', 'turn', scoped) != coverage.ledger('new name', 'turn', full)


def test_replaced_vocabulary_discards_its_lexical_cache():
    original = vocabulary()
    assert coverage.ledger('new name', 'turn', original)[0].kind == 'unknown'
    updated = replace(original, phrases={**original.phrases, 'new name': ('u.metric',)})
    piece, = coverage.ledger('new name', 'turn', updated)
    assert piece.candidates == ('u.metric',)


def test_cached_ledger_does_not_share_mutable_period_values():
    vocab = vocabulary()
    text = 'Metric June 2025'
    pieces = coverage.ledger(text, 'turn', vocab)
    period = next(p.value for p in pieces if p.kind == 'time')
    period.value = '1900-01-01'
    assert encoded(coverage.ledger(text, 'turn', vocab)) == encoded(coverage._ledger(text, 'turn', vocab))


@pytest.mark.parametrize('text', [
    'US dollars and dollars', 'Egyptian pounds, pounds, pound sterling',
    'Burundi francs, Swiss francs and francs', 'euro euroS EURO',
    'ſterling and Brİtİsh pounds', 'Swiss francs and xfrancsy',
])
def test_compiled_currency_names_keep_longest_first_spans(text):
    names = [(name, code) for code, variants in question.CURRENCY_NAMES.items() for name in variants.split('|')]
    names.extend([('pound', 'GBP'), ('franc', 'CHF')])
    expected, consumed = [], []
    for name, code in sorted(names, key=lambda item: -len(item[0])):
        for match in re.finditer(r'\b' + re.escape(name) + r's?\b', text, re.I):
            if not any(start < match.end() and match.start() < end for start, end in consumed):
                consumed.append(match.span())
                expected.append((match.span(), match.group(), code))
    actual = [(match.span(), match.group(), code) for match, code in question.currency_names(text)]
    assert json.dumps(actual).encode() == json.dumps(expected).encode()


@pytest.mark.parametrize('text,expected', [
    ('safe text', 'safe text'),
    ('secret_value SECRET_VALUE ſecret_value', 'withheld withheld withheld'),
    ('secret_value_suffix secret_value2', 'secret_value_suffix secret_value2'),
    ('private.rows.secret_value', 'withheld'),
])
def test_redaction_shortcut_preserves_boundaries_and_unicode(text, expected, monkeypatch):
    columns = [dict(unit='public', tbl='rows', name='id'), dict(unit='private', tbl='rows', name='secret_value')]
    monkeypatch.setattr(path, 'catalogue', lambda e: (columns, []))
    monkeypatch.setattr(wording, 'catalogue', lambda e: (columns, []))
    engine = SimpleNamespace(g=SimpleNamespace(readable_units=lambda p: {'public'}),
                             UNIT_LABEL={'public': 'Public', 'private': 'Private'},
                             BUSINESS={}, RESTRICTED_COLUMNS=[])
    redact, _ = path._redactor(engine, 'reader')
    assert redact(text) == expected


def test_document_redaction_keeps_visible_ids_and_protected_text():
    hidden = wording.Hidden(['private'], {'DOC-private'}, ['DOC-private-public'])
    assert hidden.withheld('DOC-private-public and private') == 'DOC-private-public and (withheld)'
    assert hidden.withheld('DOC-private and private') == '(withheld) and (withheld)'
    assert hidden.withheld('private', ['private']) == 'private'
