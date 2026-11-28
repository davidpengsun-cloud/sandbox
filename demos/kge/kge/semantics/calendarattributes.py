"""Recurring calendar values bind to ordinary filters and time breakdowns from pack data."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AttributeMatch:
    start: int
    end: int
    column: str
    value: object
    grain: str
    cycle: str
    repeat_start: int | None = None
    repeat_end: int | None = None


def declarations(vocabulary):
    """The same shared spine on two calendars declares each readable value only once."""
    seen = set()
    for calendar in vocabulary.calendars.values():
        spine = calendar.get('spine') or {}
        for recurrence in spine.get('recurrences', ()):
            column = spine.get('attributes', {}).get(recurrence['attribute'])
            if column not in vocabulary.entries:
                continue
            for member in recurrence['values']:
                for phrase in member['phrases']:
                    key = (column, member['value'], recurrence['grain'], recurrence['cycle'], phrase.casefold())
                    if key not in seen:
                        seen.add(key)
                        yield key


def matches(text, vocabulary):
    """An unqualified month remains a date; an explicit recurring value becomes a calendar filter."""
    found = []
    for column, value, grain, cycle, phrase in declarations(vocabulary):
        pattern = r'(?<!\w)' + re.escape(phrase) + r'(?!\w)'
        for match in re.finditer(pattern, text, re.I):
            repeat = re.search(r'\b(?:every|each)\s+$', text[:match.start()], re.I)
            cycle_word = cycle.replace('_', ' ')
            following = re.match(r'\s+(?:of\s+)?(?:each|every)\s+' + re.escape(cycle_word) + r's?\b',
                                 text[match.end():], re.I)
            if repeat is None and following is None:
                continue
            found.append(AttributeMatch(match.start(), match.end(), column, value, grain, cycle,
                                        repeat.start() if repeat else None, repeat.end() if repeat else None))
    chosen = []
    for match in sorted(found, key=lambda m: (-(m.end - m.start), m.start, m.column)):
        if not any(old.start < match.end and match.start < old.end for old in chosen):
            chosen.append(match)
    return tuple(sorted(chosen, key=lambda m: m.start))


def masked_question(text, vocabulary):
    """Preserve offsets while letting the ordinary period parser read only the requested outer window."""
    masked = list(text)
    for match in matches(text, vocabulary):
        masked[match.start:match.end] = ' ' * (match.end - match.start)
    return ''.join(masked)


def request_filters(text, vocabulary):
    """The rules parser consumes exactly the same column/value pairs as lexical coverage."""
    return tuple(((match.column.rsplit('.', 1)[1], '=', match.value), match.column)
                 for match in matches(text, vocabulary))


def observation_grains(text, vocabulary):
    """A recurring calendar attribute cannot invent observations finer than a measure declares."""
    return frozenset(match.grain for match in matches(text, vocabulary))
