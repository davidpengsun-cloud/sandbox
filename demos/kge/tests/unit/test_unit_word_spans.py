"""A unit word yields to a longer declared filter phrase the question says: the longest declared phrase wins, and the
bare unit word still names its unit (#474)."""
import re
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.question import declared_spans, unit_words_named

DOMAIN = SimpleNamespace(UNIT_WORDS={'retail': 'retail', 'wealth': 'wealth'},
                         FILTERS=((re.compile(r'(?<!\w)(?:Segment Six|Retail Investor)(?!\w)', re.I),),))


@pytest.mark.parametrize('question,units', [
    ('retail investor certified nna for september 2024', []),
    ('retail nna for september 2024', ['retail']),
    ('retail investor and retail nna', ['retail']),
    ('wealth retail investor nna', ['wealth']),
])
def test_the_longest_declared_phrase_wins_over_a_unit_word(question, units):
    named = unit_words_named(question, DOMAIN, declared_spans(question, DOMAIN))
    assert [u for _, u, _ in named] == units


def test_a_phrase_no_longer_than_the_unit_word_does_not_hide_it():
    domain = SimpleNamespace(UNIT_WORDS={'retail': 'retail'}, FILTERS=((re.compile(r'(?<!\w)retail(?!\w)', re.I),),))
    assert [u for _, u, _ in unit_words_named('retail nna', domain, declared_spans('retail nna', domain))] == ['retail']


@pytest.mark.parametrize('question,units', [
    ('number of open retail complaints as at 30 june 2025', ['retail']),
    ('retail complaints still open', ['retail']),
    ('open complaints', []),
])
def test_a_unit_word_between_the_parts_of_a_declared_phrase_still_names_its_unit(question, units):
    """A predicate said with a noun (open ... complaints) is the two parts, not every word between them (G006)."""
    domain = SimpleNamespace(UNIT_WORDS={'retail': 'retail'}, FILTERS=((re.compile(
        r'(?:(?<!\w)(?:unresolved|open)(?!\w).*(?<!\w)(?:complaints|complaint)(?!\w)'
        r'|(?<!\w)(?:complaints|complaint)(?!\w).*(?<!\w)(?:unresolved|open)(?!\w))', re.I),),))
    assert [u for _, u, _ in unit_words_named(question, domain, declared_spans(question, domain))] == units


def test_a_unit_word_that_is_a_part_of_a_declared_phrase_stays_inside_it():
    """A predicate whose own part is a unit word (retail ... complaints) keeps its whole span, so the word names the
    phrase, not the unit, whatever is said between the parts."""
    domain = SimpleNamespace(UNIT_WORDS={'retail': 'retail'}, FILTERS=((re.compile(
        r'(?<!\w)retail(?!\w).*(?<!\w)complaints(?!\w)', re.I),),))
    for question in ('retail priority complaints', 'retail complaints'):
        assert unit_words_named(question, domain, declared_spans(question, domain)) == []
