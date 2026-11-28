"""A CPU-only view of the engine's already-read inputs for shadow planning.

The caller captures the serving arm's planner snapshot, the release's templates and any needed columns outside shadow work.
It must keep capture and use synchronized with graph writes. This facade refuses stale inputs and backend calls; it
never refreshes them. It is not the serving engine and cannot select a different answer.
"""
from __future__ import annotations

import copy
import re
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

from kge.semantics.vocabulary import Listed, SEPARATOR, compiled, coordinated
from kge.services.ask.engine.graphview import ordered_rows
from kge.services.ask.engine.question import normalise_question, read_filters
from kge.services.ask.engine.schema_guess import guess_columns


class ShadowInputsStale(RuntimeError):
    """The caller's cached release inputs are missing or no longer current."""


class ShadowIOForbidden(RuntimeError):
    """An adapter attempted I/O through the CPU-only shadow view."""


def member_rows(columns, names, selected, vocabulary):
    """Overlay the chosen graph phrases while keeping unrelated vocabulary rows."""
    members = []
    for term, bindings in columns.items():
        phrases = sorted(phrase for phrase in selected if names[phrase] == term)
        if not phrases:
            continue
        for column, codes in bindings.items():
            condition = {'op': 'eq', 'value': codes[0]} if len(codes) == 1 else {'op': 'in', 'values': tuple(codes)}
            members.append({'kind': 'predicate', 'params': {'column': column, 'phrases': phrases, **condition}})
    ordinary = []
    for row in vocabulary:
        if row['kind'] == 'predicate':
            params = row['params']
            phrases = [phrase for phrase in params['phrases'] if phrase.casefold() not in selected]
            if not phrases:
                continue
            row = {**row, 'params': {**params, 'phrases': phrases}}
        ordinary.append(row)
    return members, [*members, *ordinary]


def member_lists(text, filters, columns, visible, names):
    """Read the graph and legacy lists with the same coordination grammar the planner uses."""
    lists = {}
    for index, start, end in coordinated(text, filters, columns, visible.__contains__):
        phrases = filters[index][4].named_phrases(text[start:end])
        lists.setdefault((start, end), set()).update(names & phrases)
    return lists


class _Semantics:
    def __init__(self, original, graph_mode, runtime):
        self._original = original
        self._graph_mode, self._runtime = graph_mode, runtime

    @property
    def plan_snapshot(self):
        if not self._graph_mode:
            raise ShadowIOForbidden('the graph snapshot is outside this shadow arm')
        return self._runtime

    @property
    def baseline_snapshot(self):
        if self._graph_mode:
            raise ShadowIOForbidden('the baseline snapshot is outside this shadow arm')
        return self._runtime

    def __getattr__(self, name):
        value = getattr(self._original, name)
        if callable(value):
            raise ShadowIOForbidden(f'shadow semantics cannot call {name}')
        return value


class _Graph:
    def __init__(self, owner, identity, writes, templates, mappings):
        self._owner, self.identity, self.writes = owner, identity, writes
        self._templates, self._mappings = templates, mappings

    def templates(self):
        self._owner.validate()
        return copy.deepcopy(self._templates)

    def implementations(self, term):
        self._owner.validate()
        rows = [{key: value for key, value in row.items() if key not in ('term', 'qualified')}
                for row in self._mappings if row['term'] == term]
        return ordered_rows(rows, 'unit', 'tbl', 'col')

    def __getattr__(self, name):
        raise ShadowIOForbidden(f'shadow graph cannot access {name}')


class _Warehouses:
    def __init__(self, owner, columns):
        self._owner, self._columns = owner, columns

    def columns(self, unit):
        self._owner.validate()
        if unit not in self._columns:
            raise ShadowInputsStale('shadow columns are missing for a unit')
        return list(self._columns[unit])

    def __getattr__(self, name):
        raise ShadowIOForbidden(f'shadow warehouse cannot access {name}')


class ShadowInputs:
    """A read-only engine-shaped view over one fresh release's cached planner inputs."""

    def __init__(self, engine, *, templates, graph_mode, runtime, columns, term_names=None, shadow_baseline=False):
        if templates is None or runtime is None:
            raise ShadowInputsStale('shadow planner inputs are missing')
        if type(graph_mode) is not bool:
            raise ValueError('graph_mode must be a boolean')
        self._engine = engine
        self._templates = templates
        self._graph_mode = graph_mode
        self._runtime = runtime
        self._shadow_baseline = shadow_baseline
        self._clarification_texts = getattr(engine, '_shadow_clarification_texts', None)
        self.member_plan_inputs = None
        self._writes = engine.g.writes
        self._identity = copy.deepcopy(engine.g.identity)
        self._engine_identity = copy.deepcopy(engine.identity)
        self.term_names = tuple(engine.term_names if term_names is None else term_names)
        self._partitioned = tuple(sorted((getattr(engine.semantics, 'partitioned', None) or {}).keys()))
        self._columns = MappingProxyType({unit: tuple(tuple(row) for row in rows) for unit, rows in columns.items()})
        self.semantics = _Semantics(engine.semantics, graph_mode, runtime)
        self.g = _Graph(self, copy.deepcopy(self._identity), self._writes, copy.deepcopy(templates),
                        tuple(copy.deepcopy(row) for row in runtime.mapping_declarations))
        self.w = _Warehouses(self, self._columns)
        self.validate()

    @property
    def identity(self):
        self.validate()
        return copy.deepcopy(self._engine_identity)

    def validate(self):
        """Refuse stale inputs; callers synchronize this check and comparison with graph mutation."""
        graph = self._engine.g
        partitions = tuple(sorted((getattr(self._engine.semantics, 'partitioned', None) or {}).keys()))
        stamp = (self._writes, self._partitioned)
        owner = (getattr(self._engine, '_shadow_baseline', None) if self._shadow_baseline
                 else getattr(self._engine.semantics, 'plan_snapshot' if self._graph_mode else 'baseline_snapshot', None))
        if (graph.writes != self._writes or getattr(graph, '_shadow_writes_inflight', 0)
                or graph.identity != self._identity or partitions != self._partitioned
                or getattr(graph, '_shadow_templates', getattr(graph, '_templates', None)) is not self._templates
                or self._templates.release != self._identity
                or getattr(self._runtime, 'plan_writes', None) != stamp
                or owner is not self._runtime
                or tuple(self._engine.term_names) != self.term_names):
            raise ShadowInputsStale('shadow planner inputs are not current')

    def shadow_clarification_texts(self):
        """The platform wording pinned outside the request, where a comparison needs a clarification."""
        self.validate()
        if self._clarification_texts is None:
            raise ShadowInputsStale('shadow clarification texts are not preloaded')
        return self._clarification_texts

    def prepare_members(self, request, vocabulary):
        """Give the graph arm the visible term predicates from the prepared release, keeping the coded arm intact.

        Where a coordinated list has no one replacement column, keep its original alternatives together.
        """
        held = getattr(self._engine.g, '_member_terms', None)
        if held is None or held.release != self._identity or not held.codes:
            return
        self.validate()
        columns = {}
        for term, column, code in held.codes:
            if column in vocabulary.entries:
                columns.setdefault(term, {}).setdefault(column, []).append(code)
        names = {}
        for phrase, term in (*held.aliases, *((name, name) for name in columns)):
            if term in columns:
                names.setdefault(phrase.casefold(), set()).add(term)
        names = {phrase: next(iter(terms)) for phrase, terms in names.items() if len(terms) == 1}
        if not names:
            return
        text = normalise_question(request.question)
        occurrences = {phrase: tuple(match.span() for match in
                       re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)) for phrase in names}
        selected = {phrase for phrase, spans in occurrences.items() if spans}
        domain = vocabulary.semantics.d
        if re.search(SEPARATOR, text, re.I):
            _, possible = member_rows(columns, names, names.keys(), domain.VOCABULARY)
            projected = compiled(possible)
            promoted = member_lists(text, projected.filters, projected.columns, vocabulary.entries, names.keys())
            legacy = member_lists(text, domain.FILTERS, domain.FILTER_COLUMNS, vocabulary.entries, names.keys())
            selected.update(phrase for phrases in promoted.values() for phrase in phrases)
            for (start, end), phrases in legacy.items():
                if not promoted.get((start, end)):
                    selected.difference_update(phrases)
                    selected.difference_update(phrase for phrase, spans in occurrences.items()
                                               if any(start <= a and b <= end for a, b in spans))
        if not selected:
            return
        members, rows = member_rows(columns, names, selected, domain.VOCABULARY)
        patterns, preferred = compiled(rows), compiled(members)
        preferred_filters = [(pattern, column) for pattern, column in zip(preferred.filters, preferred.columns)
                             if not isinstance(pattern[4], Listed)]
        pairs = preferred_filters + [pair for pair in zip(patterns.filters, patterns.columns) if pair not in preferred_filters]
        semantics = copy.copy(vocabulary.semantics)
        semantics.d = copy.copy(semantics.d)
        semantics.d.VOCABULARY = tuple(rows)
        semantics.d.FILTERS = tuple(pattern for pattern, _ in pairs)
        semantics.d.FILTER_COLUMNS = tuple(column for _, column in pairs)
        semantics.d.MEMBER_FILTERS = frozenset(preferred_filters)
        semantics.d.MEMBER_PHRASES = frozenset((row['params']['column'], phrase) for row in members
                                              for phrase in row['params']['phrases'])
        phrases = {phrase: tuple(i for i in ids if phrase not in selected or vocabulary.entries[i].kind != 'value')
                   for phrase, ids in vocabulary.phrases.items()}
        graph_vocabulary = replace(vocabulary, semantics=semantics,
                                   phrases=MappingProxyType({phrase: ids for phrase, ids in phrases.items() if ids}))
        baseline = read_filters(text, self, self.term_names)
        revised = read_filters(text, SimpleNamespace(ALIASES=self.ALIASES, FILTERS=semantics.d.FILTERS,
                               FILTER_COLUMNS=semantics.d.FILTER_COLUMNS), self.term_names)
        retained = [(item, column) for item, column in zip(request.filters, request.filtered)
                    if (item, column) not in baseline or (item, column) in revised]
        self.member_plan_inputs = (replace(request, filters=[item for item, _ in retained],
                                           filtered=[column for _, column in retained]), graph_vocabulary)

    def guess(self, words):
        """The serving engine's name-based schema guess, over captured columns only."""
        self.validate()
        if any(unit not in self._columns for unit in self.UNIT_ORDER):
            raise ShadowInputsStale('shadow columns are missing for a unit')
        return guess_columns(self.UNIT_ORDER, words, self._columns)

    def resolve(self, term, cfg):
        self.validate()
        if cfg.glossary:
            return self.g.implementations(term)
        if cfg.resolution in ('none', 'words'):
            return []
        return self.guess([word for word in re.split(r'\W+', term.lower()) if word])

    def __getattr__(self, name):
        value = getattr(self._engine, name)
        if callable(value):
            raise ShadowIOForbidden(f'shadow engine cannot call {name}')
        return value


class _PreparationGraph:
    """Graph reads for shadow preparation must not populate the serving graph's term cache."""

    def __init__(self, graph):
        self._graph = graph

    def q(self, cypher, params=None):
        return self._graph.read_uncached(cypher, params)

    def __getattr__(self, name):
        return getattr(self._graph, name)


def prepare_baseline(engine):
    """Read a baseline snapshot outside Ask without filling the serving engine's own planner cache."""
    from kge.services.ask.engine.planrules import snapshot

    isolated = copy.copy(engine)
    isolated._semantics = copy.copy(engine.semantics)
    isolated._semantics.baseline_snapshot = None
    isolated.g = _PreparationGraph(engine.g)
    return snapshot(isolated, graph=False)
