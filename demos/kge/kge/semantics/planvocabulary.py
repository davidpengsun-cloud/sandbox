"""Read-only scoped vocabulary assembled from a release's existing snapshots."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property, lru_cache
from types import MappingProxyType, SimpleNamespace
from typing import Mapping

from kge.semantics.plan import Binding
from kge.semantics.coverage import SCAFFOLD, OPERATORS
from kge.semantics.query import Context


@lru_cache(maxsize=32)
def denial_pattern(phrases):
    return re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in phrases) + r')(?!\w)', re.I) if phrases else None


def frozen(value):
    if isinstance(value, dict):
        return MappingProxyType({k: frozen(v) for k, v in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(frozen(v) for v in value)
    if isinstance(value, set):
        return frozenset(value)
    return value


@dataclass(frozen=True)
class Entry:
    id: str
    kind: str
    label: str
    unit: str | None
    context: str | None = None
    column: str | None = None
    path: tuple[str, ...] = ()
    value: object = None


@dataclass(frozen=True)
class MeasureSpec:
    id: str
    currency: str | None
    population: str
    additive: bool
    certified: bool = True


@dataclass(frozen=True)
class Vocabulary:
    entries: Mapping[str, Entry]
    phrases: Mapping[str, tuple[str, ...]]
    contexts: Mapping[str, Context]
    measures: Mapping[str, MeasureSpec]
    tables: Mapping
    entities: Mapping
    calendars: Mapping
    periods: Mapping
    scd2: Mapping
    releases: Mapping
    denied: tuple[str, ...]
    no_join: frozenset[frozenset[str]]
    binding: Binding
    semantics: object
    default_units: tuple[str, ...] = ()
    history_vocabulary: object = None

    @cached_property
    def lexical_ledger(self):
        """Bounded lexical reads owned by this immutable release and principal's vocabulary.

        Replacing a vocabulary starts a new cache, including after clarification narrows its names.
        """
        from kge.semantics.coverage import _ledger

        @lru_cache(maxsize=16)
        def read(text, turn, binding):
            return _ledger(text, turn, self)

        return read

    def candidates(self, phrase):
        return tuple(self.entries[i] for i in self.phrases.get(phrase.casefold(), ()))

    def denial(self, text):
        pattern = denial_pattern(self.denied)
        return bool(pattern and pattern.search(text))

    def public(self):
        return tuple({'id': e.id, 'kind': e.kind, 'label': e.label, 'context': e.context,
                      'path': e.path, 'value': e.value} for e in self.entries.values())


def build_vocabulary(sem, binding, readable_units, scoped_units, measure_specs, aliases=(), values=(),
                     candidates=(), denied_phrases=(), no_join=(), scoped_tables=None, qualified_policy_names=False):
    """Inputs are cached declarations, not warehouse reads. Product scope is intersected with access."""
    readable = set(readable_units)
    units = readable & set(scoped_units)
    if binding.products and scoped_tables is None:
        raise ValueError('product scope requires its resolved table set')
    allowed_tables = set(scoped_tables) if scoped_tables is not None else set(sem.tables)
    snapshot = type(sem).__new__(type(sem))
    snapshot.graph_vocabulary = getattr(sem, 'graph_vocabulary', None)
    snapshot.graph_rule_ids = getattr(sem, "graph_rule_ids", frozenset((kind, r.id) for c in sem.contexts.values()
        for kind, rules in (("Filter", c.filters), ("RowSelector", c.selectors)) for r in rules))
    snapshot.context_ids = getattr(sem, 'context_ids', frozenset(sem.contexts))
    snapshot.measure_ids = getattr(sem, 'measure_ids', frozenset(c.measure for c in sem.contexts.values()))
    snapshot.d = SimpleNamespace(**{k: frozen(v) for k, v in vars(sem.d).items() if k.isupper()})
    for key in ('contexts', 'tables', 'scd2', 'releases', 'entities', 'cites', 'declared', 'types', 'certified'):
        setattr(snapshot, key, frozen(getattr(sem, key, ())))
    setattr(snapshot, 'partitioned', frozen(getattr(sem, 'partitioned', None) or {}))
    sem = snapshot
    entries, phrases, denied = {}, {}, list(denied_phrases)
    forced, hidden_ids, policy_ids = set(denied_phrases), set(), set()
    restricted = {'.'.join(c) for c in sem.d.RESTRICTED_COLUMNS}
    def add(entry, names=()):
        if entry.unit is not None and entry.unit not in units:
            if entry.unit not in readable:
                hidden_ids.add(entry.id)
                denied.extend((entry.id, entry.label, *names))
                if entry.kind == 'unit':
                    forced.update((entry.id, entry.label, *names))
            return
        entries.setdefault(entry.id, entry)
        for phrase in (entry.id, entry.label, *names):
            if phrase:
                phrases.setdefault(phrase.casefold(), set()).add(entry.id)
    specs = {m.id: m for m in measure_specs}
    contexts = {}
    for unit in sorted({t.split('.')[0] for t in sem.tables}):
        add(Entry(unit, 'unit', unit, unit))
    for ctx in sem.contexts.values():
        if not specs.get(ctx.measure) or not specs[ctx.measure].certified:
            continue
        if ctx.fact not in allowed_tables:
            if ctx.unit not in readable:
                hidden_ids.update((ctx.id, ctx.measure))
                denied.extend((ctx.id, ctx.label, ctx.term, ctx.measure))
            continue
        add(Entry(ctx.id, 'context', ctx.label, ctx.unit))
        add(Entry(ctx.measure, 'measure', ctx.term, ctx.unit))
        if ctx.unit not in units:
            continue
        contexts[ctx.id] = ctx
        for join in ctx.joins:
            if (join.target.split('.')[0] in units and join.target in allowed_tables
                    and (not join.through or join.through in allowed_tables)):
                add(Entry(join.id, 'path', join.id, ctx.unit))
    for table, columns in sem.tables.items():
        unit = table.split('.')[0]
        if table not in allowed_tables:
            continue
        for column in columns:
            identifier = table + '.' + column
            if identifier in restricted:
                hidden_ids.add(identifier)
                policy_ids.add(identifier)
                forced.update((identifier, table.split('.', 1)[1] + '.' + column) if qualified_policy_names else (identifier, column))
            else:
                label = getattr(sem.d, 'BUSINESS', {}).get(table.split('.', 1)[1] + '.' + column, column)
                add(Entry(identifier, 'column', label, unit, column=identifier))
    for identifier, entity in sem.entities.items():
        if entity['table'] not in allowed_tables:
            continue
        if any(entity['table'] + '.' + key in restricted for key in entity['key']):
            hidden_ids.add(identifier)
            denied.extend((identifier, entity['label']))
        else:
            add(Entry(identifier, 'entity', entity['label'], entity['table'].split('.')[0]))
    for identifier in sem.d.CALENDARS:
        add(Entry(identifier, 'calendar', identifier, None))
    for entry in (*values, *candidates):
        table, _, column = (entry.column or '').rpartition('.')
        if (entry.context is not None and entry.context not in contexts) or (entry.column is not None and (
                table not in allowed_tables or table.split('.')[0] not in units or column not in sem.tables.get(table, {}) or entry.column in restricted)):
            if entry.column in restricted:
                hidden_ids.add(entry.id)
                denied.extend((entry.label, entry.id))
            elif table.split('.')[0] not in readable:
                hidden_ids.add(entry.id)
                denied.extend((entry.label, entry.id))
        else:
            add(entry)
    for phrase, identifier in aliases:
        if isinstance(identifier, (tuple, list)):
            members = tuple(sorted(set(identifier)))
            if not members or any(member not in entries or entries[member].kind != 'unit' for member in members):
                if set(members) - readable:
                    forced.add(phrase)
                continue
            identifier = 'sources:' + ','.join(members)
            add(Entry(identifier, 'source_set', phrase, None, value=members))
        if identifier in entries:
            phrases.setdefault(phrase.casefold(), set()).add(identifier)
            source, separator, _ = phrase.casefold().partition(' ')
            if separator and source in entries and entries[source].kind == 'unit' and entries[identifier].unit not in (None, source):
                phrases[phrase.casefold()].add(source)
        elif identifier in hidden_ids:
            if identifier in policy_ids and qualified_policy_names and '.' not in phrase:
                continue
            denied.append(phrase)
            if identifier in policy_ids or identifier in {t.split('.')[0] for t in sem.tables} - readable:
                forced.add(phrase)
    readable_words = set()
    for phrase in phrases:
        for word in re.findall(r'[^\W_]+', phrase):
            readable_words.update((word, word + 's', word[:-1] + 'ies' if word.endswith('y') else word))
    for row in getattr(sem.d, 'VOCABULARY', ()):
        params = row['params']
        if params.get('column') in entries:
            for phrase in (*params.get('after', ()), *params.get('before', ()), *params.get('phrases', ())):
                readable_words.update(re.findall(r'[^\W_]+', phrase.casefold()))
    def readable_phrase(phrase):
        words = set(re.findall(r'[^\W_]+', phrase.casefold()))
        return bool(words & readable_words) and words <= readable_words | SCAFFOLD | set(OPERATORS)
    denied = tuple(sorted(forced | {p for p in denied if p.casefold() not in phrases and not readable_phrase(p)},
                          key=lambda p: (-len(p), p.casefold())))
    sem.d.VOCABULARY = tuple(row for row in getattr(sem.d, 'VOCABULARY', ())
        if row['kind'] != 'code_name_pair' or (row['params']['name'] in entries
            and not any(denied and denial_pattern(denied).search(member['name']) for member in row['params'].get('members', ()))))
    through = {j.through for ctx in contexts.values() for j in ctx.joins if j.through in allowed_tables
               and j.target.split('.')[0] in units and j.target in allowed_tables
               and any(set(c['between']) == {ctx.unit, j.target.split('.')[0]} and c['declared_join'] == j.id for c in sem.certified)}
    visible_tables = {t for t in sem.tables if (t.split('.')[0] in units or t in through) and t in allowed_tables}
    return Vocabulary(frozen(entries), frozen({k: tuple(sorted(v)) for k, v in phrases.items()}), frozen(contexts),
                      frozen({k: v for k, v in specs.items() if k in entries}),
                      frozen({t: cs for t, cs in sem.tables.items() if t in visible_tables}),
                      frozen({k: v for k, v in sem.entities.items() if k in entries}), frozen(sem.d.CALENDARS),
                      frozen({t: p for t, p in sem.d.PERIODS.items() if t in visible_tables}),
                      frozen({t: p for t, p in sem.scd2.items() if t in visible_tables}),
                      frozen({t: p for t, p in sem.releases.items() if t in visible_tables}), denied,
                      frozenset(frozenset(pair) for pair in no_join), binding, sem)
