"""Read-only scoped vocabulary assembled from a release's existing snapshots."""
from __future__ import annotations

import re
import sqlglot
from sqlglot import exp
from dataclasses import dataclass, replace
from functools import cached_property, lru_cache
from types import MappingProxyType, SimpleNamespace
from typing import Mapping

from kge.semantics.plan import Binding
from kge.semantics.coverage import BASELINE_PHRASES, phrase_table, typed_scalar
from kge.semantics.query import Context


def moving_average_lengths(vocabulary, measure):
    """Only the measure's explicitly onboarded moving-average window lengths."""
    return next((tuple(row['params']['lengths']) for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                 if row['kind'] == 'moving_average' and row['params']['measure'] == measure), ())


def aggregation_of(measure, context):
    return getattr(measure, 'aggregation', None) or context.aggregation


def output_spec(measure, context, vocabulary):
    spec = vocabulary.measures[context.measure]
    if getattr(measure, 'aggregation', None) is None and not spec.aggregations and context.aggregation not in ('count', 'min', 'max', 'median'):
        return spec
    aggregation = aggregation_of(measure, context)
    return replace(spec, currency=None if aggregation == 'count' else spec.currency,
                   additive=spec.additive and aggregation in ('sum', 'count'))


def aggregate_expression(context, aggregation):
    """Replace only a single declared aggregate, retaining its row expression and FILTER."""
    if aggregation == context.aggregation and aggregation != 'median':
        return context.expression
    tree = sqlglot.parse_one(context.expression, read='duckdb')
    root = tree.this if isinstance(tree, exp.Filter) else tree
    aggregates = list(tree.find_all(exp.AggFunc))
    if len(aggregates) != 1 or root is not aggregates[0] or isinstance(root.this, (exp.Distinct, exp.Star)):
        raise ValueError('This measure expression does not declare interchangeable aggregations.')
    classes = {'sum': exp.Sum, 'count': exp.Count, 'avg': exp.Avg, 'min': exp.Min, 'max': exp.Max, 'median': exp.Median}
    operand = exp.cast(root.this.copy(), 'DOUBLE') if aggregation == 'median' else root.this.copy()
    changed = classes[aggregation](this=operand)
    if root is tree:
        tree = changed
    else:
        root.replace(changed)
    return tree.sql('duckdb')


def count_metrics(vocabulary, context, entity):
    """Declared distinct entity-key counts over exactly the requested population."""
    if entity not in vocabulary.entities:
        return ()
    def population(candidate):
        return (candidate.fact, tuple((r.table, r.predicate) for r in candidate.filters),
                tuple((r.table, r.predicate, r.mode) for r in candidate.selectors if not r.id.startswith('cohort:')),
                tuple((j.source, j.target, j.condition, j.alias, j.through) for j in candidate.joins if j.required),
                candidate.bindings or {}, candidate.version_dates or {})
    default = vocabulary.contexts.get('context:count:' + entity)
    result = [dict(row) for row in vocabulary.count_metrics.values() if row['entity'] == entity and (
        row.get('context') == context.id.removeprefix('cohort:') if row.get('context') else
        context.id.removeprefix('cohort:') == 'context:count:' + entity or default is not None and population(default) == population(context))]
    contextual = [row for row in result if row.get('context')]
    return tuple(contextual or result)

def count_filter_path(context, column):
    """One shortest declared role reaches a count's local filter; aliases are separate roles."""
    table = column.rsplit('.', 1)[0]
    pending = [(context.fact, (), frozenset((context.fact,)))]
    found = []
    for current, path, visited in pending:
        if found and len(path) > len(found[0]):
            break
        if current == table:
            found.append(path)
            continue
        for join in context.joins:
            if join.source == current and not join.through and not join.alias and join.target not in visited:
                pending.append((join.target, (*path, join.id), visited | {join.target}))
    shortest = min((len(path) for path in found), default=None)
    choices = {path for path in found if len(path) == shortest}
    return next(iter(choices)) if len(choices) == 1 else None


def predicate_contexts(vocabulary, pieces):
    """Readable declared metric alternatives grounded by every unbound predicate's unique role."""
    if any(piece.kind == 'unknown' or piece.kind == 'name' and any(
            vocabulary.entries[identifier].kind in ('context', 'measure', 'count_metric', 'derived_measure', 'candidate')
            for identifier in piece.candidates) for piece in pieces):
        return ()
    predicates = [piece for piece in pieces if piece.kind == 'predicate']
    if not predicates:
        return ()
    units = {identifier for piece in pieces if piece.kind == 'name' for identifier in piece.candidates
             if vocabulary.entries[identifier].kind == 'unit'}
    units.update(unit for piece in pieces if piece.kind == 'name' for identifier in piece.candidates
                 if vocabulary.entries[identifier].kind == 'source_set' for unit in vocabulary.entries[identifier].value)
    restricted = {'.'.join(column) for column in vocabulary.semantics.d.RESTRICTED_COLUMNS}
    choices = []
    for context in vocabulary.contexts.values():
        if context.id not in vocabulary.semantics.context_ids or units and context.unit not in units or set(context.values) & restricted:
            continue
        required = ({context.fact} | {table for join in context.joins if join.required for table in (join.source, join.target)}
                    | {rule.table for rule in context.filters}
                    | {rule.table for rule in context.selectors if rule.table.split('.')[0] == context.unit})
        if not required <= set(vocabulary.tables):
            continue
        if all(sum(identifier in vocabulary.entries and vocabulary.entries[identifier].kind == 'column'
                   and count_filter_path(context, identifier) is not None for identifier in predicate.candidates) == 1
               for predicate in predicates):
            choices.append(context)
    return tuple(choices)


def count_filter_matches(predicate, declaration, vocabulary, context):
    if (predicate.kind not in ('compare', 'null') or predicate.left.kind != 'column'
            or predicate.left.name.id != declaration['column']):
        return False
    path = count_filter_path(context, declaration['column'])
    if path is None or tuple(role.id for role in predicate.left.path) != path:
        return False
    operation = declaration['op']
    if predicate.kind == 'null':
        return operation in ('is_null', 'is_not_null') and predicate.is_null == (operation == 'is_null')
    return predicate.op == operation and predicate.value.model_dump() == typed_scalar(
        declaration.get('value'), declaration['column'], vocabulary)


def selected_count_metrics(vocabulary, context, entity, declaration=None, filters=None):
    """A named count is exact; otherwise the declared unfiltered count is the default."""
    choices = count_metrics(vocabulary, context, entity)
    if declaration is not None:
        return tuple(row for row in choices if row['id'] == declaration)
    if filters is None:
        return tuple(row for row in choices if not row.get('filters')) or choices
    matching = [row for row in choices if all(any(count_filter_matches(predicate, expected, vocabulary, context) for predicate in filters)
                                            for expected in row.get('filters', ()))]
    if not matching:
        return choices if len(choices) == 1 else ()
    specificity = max(len(row.get('filters', ())) for row in matching)
    return tuple(row for row in matching if len(row.get('filters', ())) == specificity)


def metric_identifier(output, vocabulary):
    if output.kind == 'measure':
        return output.measure.id
    if output.declaration is not None:
        return output.declaration.id
    declarations = selected_count_metrics(vocabulary, vocabulary.contexts[output.context.id], output.entity.id, filters=output.filters)
    return declarations[0]['id'] if len(declarations) == 1 else None


def formula_metrics(vocabulary, kind, numerator, denominator, contexts=None):
    """Structural calculations select the declared formula and its input populations."""
    actual = contexts or {}
    choices = [(identifier, row) for identifier, row in vocabulary.derived_measures.items()
               if row['formula'] == {'kind': kind, 'numerator': numerator, 'denominator': denominator}
               and all(actual.get(role) == context for role, context in row.get('contexts', {}).items())]
    specificity = max((len(row.get('contexts', {})) for _, row in choices), default=0)
    return tuple(identifier for identifier, row in choices if len(row.get('contexts', {})) == specificity)


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


def thawed(value):
    """Convert immutable declaration metadata to model-facing JSON values."""
    if isinstance(value, Mapping):
        return {k: thawed(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(thawed(v) for v in value)
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


def value_id(column, value):
    """A declared value's name, from what declares it alone: the qualified column and the value.

    Never a row's position, so adding a predicate or a value row to a pack moves no other entry's name.
    """
    return f'value:{column}:{value}'


def value_entries(rows):
    """One entry per declared column and value across a pack's value rows, in declaration order.

    Two rows declaring the same column and value give one entry, not two names for one meaning.
    """
    entries = {}
    for row in rows:
        if row['kind'] != 'value':
            continue
        column = row['params']['column']
        for value in row['params'].get('values', ()):
            identifier = value_id(column, value)
            entries.setdefault(identifier, Entry(identifier, 'value', str(value), column.split('.')[0], column=column, value=value))
    return tuple(entries.values())


def member_names(rows):
    """Each member a code/name pair declares, by its code column and code, to its declared name."""
    return {(row['params']['code'], str(member['code'])): member['name'] for row in rows
            if row['kind'] == 'code_name_pair' for member in row['params'].get('members', ())}


def named_members(entries, rows, phrases):
    """Each declared value that is a pair's code, labelled with the member's declared name: the code is added, as
    "name (code)", where the name is already a phrase or another such value has the same name, so a label never reads
    as another entry. Each new label is added to phrases for its entry, so a question a picked choice wrote reads again
    on a later turn. A member whose name is its code keeps its label."""
    names = member_names(rows)
    named = {i: names.get((e.column, str(e.value))) for i, e in entries.items() if e.kind == 'value'}
    named = {i: name for i, name in named.items() if name is not None and name != entries[i].label}
    counts = {}
    for name in named.values():
        counts[name.casefold()] = counts.get(name.casefold(), 0) + 1
    out = dict(entries)
    for identifier, name in named.items():
        entry = entries[identifier]
        label = name if counts[name.casefold()] == 1 and name.casefold() not in phrases else f'{name} ({entry.value})'
        out[identifier] = replace(entry, label=label)
        phrases.setdefault(label.casefold(), set()).add(identifier)
    return out


@dataclass(frozen=True)
class MeasureSpec:
    id: str
    currency: str | None
    population: str
    additive: bool
    certified: bool = True
    aggregations: tuple[str, ...] = ()
    empty: str | None = None


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
    def phrase_table(self):
        """The question lexer's phrase table for this release: the graph's, or the constant baseline where the graph
        holds none (coverage.phrase_table)."""
        return phrase_table(self.semantics)

    @cached_property
    def lexical_ledger(self):
        """Bounded lexical reads owned by this immutable release and principal's vocabulary, with the full graph phrase
        table (phrase_table, which _ledger reads from the vocabulary).

        Replacing a vocabulary starts a new cache, including after clarification narrows its names.
        """
        from kge.semantics.coverage import _ledger

        @lru_cache(maxsize=16)
        def read(text, turn, binding):
            return _ledger(text, turn, self)

        return read

    @cached_property
    def baseline_ledger(self):
        """The coded rules' reading until B7 (#455): the constant baseline (coverage.BASELINE_PHRASES). While the graph's
        table equals the baseline it is lexical_ledger itself, so a request lexes no question twice."""
        if self.phrase_table == BASELINE_PHRASES:
            return self.lexical_ledger
        from kge.semantics.coverage import _ledger

        @lru_cache(maxsize=16)
        def read(text, turn, binding):
            return _ledger(text, turn, self, BASELINE_PHRASES)

        return read

    def candidates(self, phrase):
        return tuple(self.entries[i] for i in self.phrases.get(phrase.casefold(), ()))

    @cached_property
    def readable_phrases(self):
        """Every declared phrase this vocabulary may read, the longest first."""
        phrases = set(self.phrases)
        for row in getattr(self.semantics.d, 'VOCABULARY', ()):
            params = row['params']
            if params.get('column') in self.entries:
                phrases.update(p.casefold() for key in ('phrases', 'after', 'before') for p in params.get(key, ()))
        return tuple(sorted(phrases, key=lambda p: (-len(p), p)))

    def denials(self, text):
        """The denied phrases text says, where no longer readable declared phrase holds them: the longest declared
        phrase wins, so a word denied alone gives way inside a longer phrase the person may read that holds it."""
        pattern = denial_pattern(self.denied)
        if not pattern:
            return []
        readable = denial_pattern(self.readable_phrases)
        held = [m.span() for m in readable.finditer(text)] if readable else []
        return [m for m in pattern.finditer(text)
                if not any(start <= m.start() and m.end() <= end and end - start > m.end() - m.start() for start, end in held)]

    def denial(self, text):
        return bool(self.denials(text))

    def public(self):
        return tuple({'id': e.id, 'kind': e.kind, 'label': e.label, 'context': e.context,
                      'path': e.path, 'value': {**dict(e.value), 'formula': dict(e.value['formula']),
                       **({'contexts': dict(e.value['contexts'])} if e.value.get('contexts') else {})}
                      if e.kind == 'derived_measure' else {**dict(e.value), 'filters': [dict(f) for f in e.value.get('filters', ())]}
                      if e.kind == 'count_metric' else thawed(e.value) if e.kind == 'band' else e.value} for e in self.entries.values())

    @cached_property
    def count_metrics(self):
        return frozen({e.id: e.value for e in self.entries.values() if e.kind == 'count_metric'})

    @cached_property
    def derived_measures(self):
        return frozen({e.id: e.value for e in self.entries.values() if e.kind == 'derived_measure'})


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
    for row in getattr(sem.d, 'VOCABULARY', ()):
        if row['kind'] == 'measure_options' and row['params']['measure'] in specs:
            params = row['params']
            specs[params['measure']] = replace(specs[params['measure']], aggregations=tuple(params.get('aggregations', ())),
                                                empty=params.get('empty'))
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
    grouping = dict(getattr(sem.d, 'GROUP_BY', {}))
    for row in getattr(sem.d, 'VOCABULARY', ()):
        if row['kind'] != 'band_options':
            continue
        params = row['params']
        column = params['column']
        if column not in entries:
            if column in hidden_ids:
                denied.extend((params['id'], params['label'], 'by ' + params['label']))
            continue
        add(Entry(params['id'], 'band', params['label'], column.split('.')[0], column=column, value=params))
        for prefix in ('by ', 'each '):
            phrase = prefix + params['label']
            grouping[phrase.casefold()] = params['id']
            phrases.setdefault(phrase.casefold(), set()).add(params['id'])
    sem.d.GROUP_BY = frozen(grouping)
    for row in getattr(sem.d, 'VOCABULARY', ()):
        params = row['params']
        if row['kind'] == 'count_metric':
            context = params.get('context') or 'context:count:' + params['entity']
            context = context if context in contexts else 'cohort:' + context
            if context in contexts and params['entity'] in entries:
                add(Entry(params['id'], 'count_metric', params['label'], contexts[context].unit, context=context, value=params))
    for row in getattr(sem.d, 'VOCABULARY', ()):
        params = row['params']
        if row['kind'] == 'derived_measure':
            formula = params['formula']
            inputs = (formula['numerator'], formula['denominator'])
            if (all(any(c.measure == i for c in contexts.values()) or (i in entries and entries[i].kind == 'count_metric') for i in inputs)
                    and all(context in contexts for context in params.get('contexts', {}).values())):
                add(Entry(params['id'], 'derived_measure', params['label'], None, value=params), (params['label'],))
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
    table = phrase_table(sem)
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
        return bool(words & readable_words) and words <= readable_words | table.filler | table.operator_phrases
    denied = tuple(sorted(forced | {p for p in denied if p.casefold() not in phrases and not readable_phrase(p)},
                          key=lambda p: (-len(p), p.casefold())))
    sem.d.VOCABULARY = tuple(row for row in getattr(sem.d, 'VOCABULARY', ())
        if row['kind'] != 'code_name_pair' or (row['params']['name'] in entries
            and not any(denied and denial_pattern(denied).search(member['name']) for member in row['params'].get('members', ()))))
    entries = named_members(entries, getattr(sem.d, 'VOCABULARY', ()), phrases)
    through = {j.through for ctx in contexts.values() for j in ctx.joins if j.through in allowed_tables
               and j.target.split('.')[0] in units and j.target in allowed_tables
               and any(set(c['between']) == {ctx.unit, j.target.split('.')[0]} and c['declared_join'] == j.id for c in sem.certified)}
    visible_tables = {t for t in sem.tables if (t.split('.')[0] in units or t in through) and t in allowed_tables}
    calendars = {k: dict(v) for k, v in sem.d.CALENDARS.items()}
    for row in getattr(sem.d, 'VOCABULARY', ()):
        if row['kind'] == 'calendar_options' and row['params']['calendar'] in calendars:
            params = dict(row['params'])
            identifier = params.pop('calendar')
            calendars[identifier].update(params)
    return Vocabulary(frozen(entries), frozen({k: tuple(sorted(v)) for k, v in phrases.items()}), frozen(contexts),
                      frozen({k: v for k, v in specs.items() if k in entries}),
                      frozen({t: cs for t, cs in sem.tables.items() if t in visible_tables}),
                      frozen({k: v for k, v in sem.entities.items() if k in entries}), frozen(calendars),
                      frozen({t: p for t, p in sem.d.PERIODS.items() if t in visible_tables}),
                      frozen({t: p for t, p in sem.scd2.items() if t in visible_tables}),
                      frozen({t: p for t, p in sem.releases.items() if t in visible_tables}), denied,
                      frozenset(frozenset(pair) for pair in no_join), binding, sem)
