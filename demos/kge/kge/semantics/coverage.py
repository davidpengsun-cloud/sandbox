"""Independent lexical obligations; uninterpreted text always prevents execution.

OPERATORS, SCAFFOLD and SCAFFOLD_PHRASES are baseline v1 of the question lexer's phrase table (BASELINE_PHRASES), frozen
in the platform preload and loaded into every graph; they stay here until B7 (#455), when the coded rules stop reading
them. The lexer reads the table it is given (kge/semantics/phrasetable.py): the graph's for coverage and the graph
rules, a table equal to this constant for the coded rules.
"""
from __future__ import annotations

import re
import calendar
import threading
from contextlib import contextmanager
from decimal import Decimal
from datetime import date

import sqlglot
from sqlglot import exp
from dataclasses import dataclass, replace
from typing import get_args

from kge.semantics.plan import Span, Name, Record, Grain, DateValue, DecimalValue, Integer, Text, Obligation, predicate_nodes
from kge.semantics.vocabulary import Listed, coordinated as coordinated_lists, filter_value
from kge.semantics.filtergrammar import FILTER_CONNECTOR, boolean_groups, exclusion, leaves, list_quantifier
from kge.semantics.phrasetable import PhraseTable

SCAFFOLD = frozenset('what which is are the a an please show give me of for in on with and to from total during across does have was were who at did make bring as booked that whose had still only using now just'.split())
SCAFFOLD_PHRASES = ('break out', 'break down', 'broken down', 'based on', 'show me', 'for the measure', 'for measure')
SCAFFOLD = SCAFFOLD | {'list', 'each', 'they', 'their', 'how', 'gross', 'having'}
OPERATORS = {
    'fiscal': 'fiscal_quarter', 'calendar': 'calendar_quarter', 'fiscal quarter': 'fiscal_quarter', 'how much': 'measure', 'add up': 'aggregate', 'combined': 'aggregate', 'sum': 'aggregate', 'raised': 'membership', 'their': 'membership', 'hold both': 'membership', 'next to': 'membership', 'line up': 'membership', 'how many': 'count', 'number': 'count', 'plus': 'add', 'also': 'membership', 'match': 'membership', 'join': 'membership', 'joined': 'membership', 'by': 'dimension', 'per': 'dimension', 'count': 'count', 'counts': 'count', 'number of': 'count',
    'distinct': 'count', 'versus': 'comparison', 'vs': 'comparison', 'compare': 'comparison',
    'previous period': 'previous_period', 'previous year': 'previous_year', 'year over year': 'previous_year',
    'ratio': 'ratio', 'share': 'share', 'percent change': 'percent_change', 'change': 'difference', 'difference': 'difference', '$ change': 'difference', 'running total': 'running_total',
    'all': 'all', 'any': 'any', 'membership': 'membership', 'in': 'in', 'is null': 'null', 'is not null': 'not_null', 'not': 'not', 'excluding': 'not', 'or': 'any', 'above': 'gt', 'below': 'lt', 'at least': 'ge',
    'at most': 'le', 'equal to': 'eq', 'top': 'desc', 'bottom': 'asc', 'ascending': 'asc', 'descending': 'desc', 'most': 'desc', 'least': 'asc',
    'year to date': 'ytd', 'through': 'range',
    'all available': 'all_available', 'latest released': 'latest_released', 'separate': 'separate',
    'day': 'day', 'month': 'month', 'year': 'year', 'monthly': 'month', 'quarter': 'calendar_quarter',
    '>=': 'ge', '<=': 'le', '>': 'gt', '<': 'lt', '=': 'eq', '!=': 'ne',
}

OPERATORS.update({grain.replace('_', ' '): grain for grain in get_args(Grain)})
OPERATORS.update({'in total': 'aggregate'})
OPERATORS['which'] = 'count'
OPERATORS.update({p: 'ytd' for p in ('ytd', 'year-to-date', 'fiscal year to date', 'fiscal year-to-date', 'fiscal ytd', 'calendar year to date', 'calendar year-to-date')})
OPERATORS.update({'highest': 'desc', 'lowest': 'asc', 'cumulative': 'running_total',
                  'contribution': 'share', 'contribute': 'share', 'dollar': 'difference',
                  'percent difference': 'percent_change', '%': 'percent_change',
                  'month-over-month': 'previous_period', 'month over month': 'previous_period', 'or more': 'ge'})
OPERATORS.update({phrase: 'comparison' for phrase in ('compared with', 'compared to', 'against')})
OPERATORS.update({'quarterly': 'calendar_quarter', 'yearly': 'year'})
OPERATORS.update({'% of total': 'share', 'percent of total': 'share'})
OPERATORS['not equal to'] = 'ne'
OPERATORS.update({'average': 'avg', 'mean': 'avg', 'minimum': 'min', 'smallest': 'min',
                  'maximum': 'max', 'largest': 'max', 'median': 'median'})

OPERATOR_FAMILIES = {
    'measure': ('how much', 'bring in', 'booked by'),
    'comparison': ('compared to', 'compared with', 'compare to', 'compare with', 'against'),
    'previous_period': ('prior period', 'last period', 'previous month', 'prior month', 'last month',
                        'previous quarter', 'prior quarter', 'last quarter', 'mom', 'qoq',
                        'quarter over quarter', 'quarter-over-quarter', 'previous week', 'prior week', 'last week',
                        'week over week', 'week-over-week', 'wow', 'previous day', 'prior day', 'last day',
                        'day over day', 'day-over-day', 'dod'),
    'previous_year': ('prior year', 'last year', 'same period last year', 'same month last year',
                      'same quarter last year', 'a year earlier', 'one year earlier', 'yoy', 'year-over-year',
                      'same period a year earlier', 'same month a year earlier', 'same quarter a year earlier',
                      'same day last year', 'same week last year', 'same day a year earlier', 'same week a year earlier'),
    'difference': ('dollar change', 'dollars', 'in dollars', 'in dollar terms', 'dollar difference', '$ difference', 'gap', 'delta',
                   'increase', 'decrease', 'increased', 'decreased'),
    'percent_change': ('% change', 'percentage change', 'percentage difference', 'percent', 'percentage',
                       'in percent', 'in percentage terms'),
    'desc': ('best', 'largest', 'rank', 'ranked', 'ranking'),
    'asc': ('worst', 'smallest'),
    'share': ('% of', 'percent of', 'percentage of', 'portion', 'proportion', 'rest', 'remainder'),
    'running_total': ('running totals', 'cumulative total', 'cumulative sum'),
    'ytd': ('to date', 'year to date', 'year-to-date', 'ytd', 'so far this year',
            'calendar year to date', 'calendar year-to-date', 'calendar ytd',
            'fiscal year to date', 'fiscal year-to-date', 'fiscal ytd'),
    'mtd': ('month to date', 'month-to-date', 'mtd', 'so far this month',
            'calendar month to date', 'calendar month-to-date', 'calendar mtd',
            'fiscal month to date', 'fiscal month-to-date', 'fiscal mtd'),
    'qtd': ('quarter to date', 'quarter-to-date', 'qtd', 'so far this quarter',
            'calendar quarter to date', 'calendar quarter-to-date', 'calendar qtd',
            'fiscal quarter to date', 'fiscal quarter-to-date', 'fiscal qtd'),
    'count': ('count of',),
    'not_in': ('exclude', 'excluded', 'except', 'other than', 'not in', 'outside', 'apart from', 'without'),
    'in': ('include', 'includes', 'included', 'any of'),
    'all': ('all of',),
    'null': ('null', 'missing', 'is missing'),
    'not_null': ('not null', 'is present'),
    'gt': ('greater than', 'more than', 'over'),
    'ge': ('greater than or equal to', 'no less than', 'or above', 'and above', 'and over'),
    'lt': ('less than', 'fewer than', 'under'),
    'le': ('less than or equal to', 'no more than', 'or below', 'or less', 'and below', 'and under'),
    'eq': ('equals', 'equal', 'exactly'),
    'ne': ('different from', 'not equal', '<>'),
    'avg': ('average', 'mean'),
    'min': ('minimum', 'min'),
    'max': ('maximum', 'max'),
    'median': ('median',),
    'contains': ('contains', 'containing', 'including', 'in the name'),
    'starts_with': ('starts with', 'starting with', 'begins with', 'beginning with'),
    'band': ('band', 'bands', 'bucket', 'buckets', 'range', 'ranges'),
    'moving_average': ('moving average', 'rolling average'),
    'dimension': ('within each', 'in each'),
    'range': ('between', 'until'),
    'latest_released': ('latest', 'most recent', 'newest', 'last'),
    'all_available': ('all time', 'all dates'),
}
OPERATORS.update({phrase: operation for operation, phrases in OPERATOR_FAMILIES.items() for phrase in phrases})
GRAIN_WORDS = {form: grain for grain in get_args(Grain)
               for word in (grain.replace('_', ' '),) for form in (word, word + 's')} | {'quarter': 'calendar_quarter', 'quarters': 'calendar_quarter'}
GRAIN_WORDS.update({'calendar ' + word: grain for word, grain in tuple(GRAIN_WORDS.items()) if grain in ('day', 'week', 'month', 'year')})
GRAIN_ADVERBS = dict(daily='day', weekly='week', monthly='month', quarterly='calendar_quarter', yearly='year', annually='year')
TIME_GROUPING = re.compile(r'\b(?:(?:by|per|each)\s+(?:' + '|'.join(re.escape(word) for word in GRAIN_WORDS)
                           + r')|' + '|'.join(GRAIN_ADVERBS) + r')\b', re.I)
OPERATORS.update(GRAIN_WORDS)
OPERATORS.update(GRAIN_ADVERBS)


def comparison_grains(pieces):
    """A named comparison interval cannot silently use another period's offset."""
    abbreviations = {'dod': 'day', 'wow': 'week', 'mom': 'month', 'qoq': 'calendar_quarter'}
    return {grain for piece in pieces if piece.operation == 'previous_period'
            for grain in ({abbreviations[piece.text.casefold()]} if piece.text.casefold() in abbreviations else
                          {GRAIN_WORDS[word] for word in re.findall(r'[a-z]+', piece.text.casefold()) if word in GRAIN_WORDS})}
RANK_WORDS = tuple(phrase for operation in ('asc', 'desc') for phrase, op in OPERATORS.items() if op == operation)
RANK_PATTERN = r'(?:' + '|'.join(re.escape(word) for word in sorted(RANK_WORDS, key=len, reverse=True)) + r')'
RANK_VERBS = frozenset(('rank', 'ranked', 'ranking'))
SINGULAR_RANK_WORDS = frozenset(('most', 'least', 'highest', 'lowest', 'best', 'worst', 'largest', 'smallest'))
SHARE_COMPLEMENTS = frozenset(('rest', 'remainder'))
SCAFFOLD = SCAFFOLD | {'where', 'be', 'can', 'may', 'you', 'tell', 'it', 'there', 'put', 'within', 'over'}
BASELINE_PHRASES = PhraseTable.constant(OPERATORS, SCAFFOLD, SCAFFOLD_PHRASES)
_WATCHERS = ()
_WATCHING = threading.Lock()


@dataclass(frozen=True)
class Piece:
    """A stated word or phrase and what it reads as. filter_part is read on a piece inherited from an earlier turn:
    whether its own turn named it as one of a measure's separate filter parts, which a follow-up's check cannot see.
    markers are the lexer markers of question-shapes.md §3.1 (MARKERS, set by lexer_markers): facts a template's
    `requires` reads in place of the raw text a coded builder reads. The coded planner never reads them."""
    span: Span
    text: str
    kind: str
    candidates: tuple[str, ...] = ()
    operation: str | None = None
    value: object = None
    window: tuple[Span, ...] = ()
    cutoff: bool = False
    filter_part: bool = False
    markers: tuple[str, ...] = ()


def _counted_entity_lists(pieces, text, vocabulary, coordinated=True):
    """An explicit count owns its nearest noun and that noun's structural list."""
    groups = []
    names = [piece for piece in pieces
             if piece.operation != 'dimension' and set(piece.candidates) & vocabulary.entities.keys()]
    for operator in pieces:
        if operator.operation != 'count' or operator.text.casefold() == 'which':
            continue
        adjacent = []
        for piece in names:
            if piece.span.turn != operator.span.turn:
                continue
            first, last = sorted((operator, piece), key=lambda p: p.span.start)
            gap = text[first.span.end:last.span.start]
            if re.fullmatch(r'(?:\s|\b(?:of|the|all|distinct)\b)*', gap, re.I):
                adjacent.append((len(gap), piece))
        if adjacent:
            nearest = min(distance for distance, _ in adjacent)
            owned = [piece for distance, piece in adjacent if distance == nearest]
            pending = list(owned)
            while pending and coordinated:
                anchor = pending.pop()
                for piece in names:
                    if piece.span.turn != operator.span.turn or any(piece is previous for previous in owned):
                        continue
                    first, last = sorted((anchor, piece), key=lambda p: p.span.start)
                    if _count_list_gap(text[first.span.end:last.span.start]):
                        owned.append(piece)
                        pending.append(piece)
            groups.append(tuple(owned))
    return tuple(groups)


def _count_list_gap(text):
    return bool(re.fullmatch(r'\s*(?:,|&|\b(?:and|or)\b)(?:\s|,|&|\b(?:and|or|the|all|distinct)\b)*', text, re.I))


def counted_names(pieces, text, vocabulary, *, coordinated=True):
    """Each independently counted entity retains its own complete declared key."""
    return {entity for group in _counted_entity_lists(pieces, text, vocabulary, coordinated)
            for piece in group for entity in set(piece.candidates) & vocabulary.entities.keys()}


def _count_list_connectors(pieces, text, vocabulary):
    """Only proven count-list connectors are scaffolding; other disjunctions remain obligations."""
    spans = set()
    for group in _counted_entity_lists(pieces, text, vocabulary):
        ordered = sorted(group, key=lambda piece: piece.span.start)
        for left, right in zip(ordered, ordered[1:]):
            if _count_list_gap(text[left.span.end:right.span.start]):
                spans.update(piece.span for piece in pieces if piece.span.turn == left.span.turn
                    and left.span.end <= piece.span.start < piece.span.end <= right.span.start
                    and ((piece.kind == 'operator' and piece.operation == 'any' and piece.text.casefold() == 'or')
                         or (piece.kind == 'unknown' and piece.text == '&')))
    return spans


def output_aggregation_positions(pieces, text, vocabulary):
    """Aggregation ownership follows each occurrence of an output name."""
    heads = [piece for piece in pieces if piece.kind == 'name' and piece.operation != 'dimension']
    def contexts(piece):
        return (set(piece.candidates) & vocabulary.contexts.keys()
                or {c.id for c in vocabulary.contexts.values() if c.measure in piece.candidates})
    requested, bound = {}, {}
    for operator in pieces:
        operation = 'sum' if operator.operation == 'aggregate' else operator.operation
        if operation not in ('sum', 'count', 'avg', 'min', 'max', 'median') or operator.text.casefold() == 'which':
            continue
        adjacent = []
        for piece in heads:
            if piece.span.turn != operator.span.turn:
                continue
            first, last = sorted((operator, piece), key=lambda p: p.span.start)
            gap = text[first.span.end:last.span.start]
            if not re.fullmatch(r'(?:\s|\b(?:of|the|all|distinct)\b)*', gap, re.I):
                continue
            if identifiers := contexts(piece):
                adjacent.append((len(gap), piece, identifiers))
        if adjacent:
            nearest = min(distance for distance, _, _ in adjacent)
            for distance, piece, identifiers in adjacent:
                if distance == nearest:
                    for context in identifiers:
                        positions = requested.setdefault(context, {})
                        positions[operation] = min(positions.get(operation, len(text)), operator.span.start)
                        bound.setdefault(context, set()).add(piece.span)
    for context, positions in requested.items():
        occurrences = {piece.span for piece in heads if context in contexts(piece)}
        unqualified = occurrences - bound.get(context, set())
        if len(occurrences) > 1 and unqualified:
            operation = vocabulary.contexts[context].aggregation
            positions[operation] = min(positions.get(operation, len(text)), min(span.start for span in unqualified))
    return requested


def output_aggregations(pieces, text, vocabulary):
    """Requested aggregations, retaining a default for an unqualified occurrence."""
    return {context: set(positions) for context, positions in output_aggregation_positions(pieces, text, vocabulary).items()}


def count_population_contexts(pieces, text, vocabulary):
    """A leading entity count scopes later context names unless a list introduces another output."""
    entities = counted_names(pieces, text, vocabulary)
    contexts = [p for p in pieces if p.kind == 'name' and set(p.candidates) & vocabulary.contexts.keys()]
    if not entities or not contexts:
        return set()
    first = min(p.span.start for p in contexts)
    heads = [p for p in pieces if p.span.end <= first and p.operation != 'dimension' and set(p.candidates) & entities]
    operators = [p for p in pieces if p.operation == 'count' and p.span.end <= first and p.text.casefold() != 'which']
    if not heads or not operators:
        return set()
    end = max(p.span.end for p in (*heads, *operators))
    if re.search(r'\b(?:and|or|plus)\b|,', text[end:first], re.I):
        return set()
    return {c for p in contexts for c in p.candidates if c in vocabulary.contexts}


def aggregation_owner(piece, node, requested, vocabulary=None):
    """Aggregation evidence targets only the output names that own that operation."""
    operation = 'sum' if piece.operation == 'aggregate' else piece.operation
    owners = {c for c, owned in requested.items() if operation in owned}
    context = getattr(getattr(node, 'context', None), 'id', None)
    if owners and context not in owners:
        return False
    if operation in ('sum', 'count', 'avg', 'min', 'max', 'median') and getattr(node, 'kind', None) in ('measure', 'distinct_count'):
        aggregation = ('count' if node.kind == 'distinct_count' else node.aggregation or
                       (vocabulary.contexts[context].aggregation if vocabulary is not None else None))
        return aggregation is None or aggregation == operation
    return True


def ranking_scope(pieces):
    """Declared columns and time grains certified as partition dimensions."""
    columns = {c for p in pieces if p.operation == 'dimension' and p.value == 'rank_partition' for c in p.candidates}
    grains = {p.value[1] for p in pieces if p.operation == 'dimension' and isinstance(p.value, tuple)
              and len(p.value) == 2 and p.value[0] == 'rank_partition'}
    return columns, grains


def modified_measures(text, names, vocabulary):
    """An adjacent modifier qualifies the whole declared measure phrase it precedes."""
    modes = {phrase.casefold() for phrase in getattr(vocabulary.semantics.d, 'TIME_MODES', ())}
    contexts = {phrase: tuple(i for i in ids if i in vocabulary.contexts) for phrase, ids in names.items()}
    contexts = {phrase: ids for phrase, ids in contexts.items() if ids}
    nouns = {phrase for phrase, ids in names.items() if any(vocabulary.entries[i].kind in ('context', 'measure') for i in ids)}
    modifiers = {}
    for phrase, identifiers in contexts.items():
        for split in re.finditer(r'\s+', phrase):
            prefix, noun = phrase[:split.end()], phrase[split.end():]
            if prefix.strip().casefold() in modes:
                continue
            if noun not in nouns or not re.search(r'(?<!\w)' + re.escape(prefix), text, re.I):
                continue
            for target, candidates in contexts.items():
                if target != noun and target.endswith(' ' + noun) and prefix + target not in names:
                    for match in re.finditer(r'(?<!\w)' + re.escape(prefix + target) + r'(?!\w)', text, re.I):
                        supported = tuple(i for i in candidates if any(vocabulary.contexts[i].measure == vocabulary.contexts[j].measure
                                                                      for j in identifiers))
                        if match.end() >= modifiers.get(match.start(), (0, ()))[0]:
                            modifiers[match.start()] = (match.end(), supported)
    return modifiers


MAGNITUDE_OPERATIONS = ('eq', 'gt', 'ge', 'lt', 'le')


def amount_columns(vocabulary, tables):
    """The amount columns declared on these fact tables: the values a summed context of each reads."""
    return {v for ctx in vocabulary.contexts.values() if ctx.fact in tables and ctx.aggregation != 'count'
            for v in ctx.values if v.rsplit('.', 1)[0] == ctx.fact}


def magnitude_filters(pieces, text, vocabulary):
    """A size threshold on rows: an entity's name, then only "of" or "worth", then a comparison with a number ("transactions
    of $250K or more", "transactions of at least $250,000"). It sizes each row by its declared amount, whatever its sign,
    so it becomes one predicate piece, operation magnitude_<op>, whose candidates are the amount columns the contexts on
    that entity's table declare; it consumes the comparison, which therefore no longer reads as a threshold on an output."""
    def number(value):
        try:
            return Decimal(str(value)) if value is not None and not isinstance(value, bool) else None
        except ArithmeticError:
            return None
    out = list(pieces)
    index = 0
    while index + 2 < len(out):
        named, first, second = out[index:index + 3]
        entities = {i for i in named.candidates if i in vocabulary.entities} if named.kind == 'name' else set()
        operator, amount = (first, second) if first.kind == 'operator' else (second, first)
        size = number(amount.value) if amount.kind == 'literal' else None
        if (not entities or not re.fullmatch(r'\s+(?:of|worth)\s+', text[named.span.end:first.span.start], re.I)
                or text[first.span.end:second.span.start].strip() or operator.kind != 'operator'
                or operator.operation not in MAGNITUDE_OPERATIONS or size is None or not size.is_finite() or size < 0):
            index += 1
            continue
        tables = {vocabulary.entities[e]['table'] for e in entities}
        columns = amount_columns(vocabulary, tables)
        candidates = tuple(i for i, e in vocabulary.entries.items() if e.kind == 'column' and e.column in columns)
        if not candidates:
            index += 1
            continue
        span = Span(turn=first.span.turn, start=first.span.start, end=second.span.end)
        out[index + 1:index + 3] = [Piece(span, text[span.start:span.end], 'predicate', candidates,
                                          'magnitude_' + operator.operation, amount.value)]
        index += 2
    return out


COLUMN_COMPARISONS = ('eq', 'ne', 'lt', 'le', 'gt', 'ge')
COLUMN_TESTS = ('null', 'not_null')


def column_comparisons(pieces, text, vocabulary):
    """A comparison a question states on a named column: the column's name (every candidate a column), then only "is" or
    nothing, then a comparison word and the value it compares with ("stage code is not equal to OPEN", "stage code is
    below OPEN"), or a null test ("settlement reference is not null"). The three become one predicate
    piece, candidates the column, operation the comparison, value the word as written or a number's literal value, so
    the words are owned by the filter they state. A value already read as a name or a pack phrase is left as it was,
    but for a declared member of the named column read alone as its equality, which the comparison takes.
    Whether the value is one the pack declares for that column is the plan check's, which refuses an undeclared one by
    name; text orders as text. A bare name, operator and number with nothing between them is left to the planner's own
    typed column comparison."""
    def column(piece):
        entries = [vocabulary.entries.get(i) for i in piece.candidates] if piece.kind == 'name' else []
        return bool(entries) and all(e is not None and e.kind == 'column' for e in entries)

    def joined(left, right, pattern):
        return re.fullmatch(pattern, text[left.span.end:right.span.start], re.I) is not None

    def declared_member(named, compared):
        """The value is one the pack declares for the named column, read alone as that column's equality (the bare
        member "X" after "table.column is below"): the comparison takes the member, on that column."""
        return (compared is not None and compared.kind == 'predicate' and compared.operation == 'eq'
                and compared.value is not None and str(compared.value) == compared.text
                and any(i in compared.candidates for i in named.candidates))

    out = list(pieces)
    index = 0
    while index + 1 < len(out):
        named, operator = out[index:index + 2]
        if not column(named) or operator.kind != 'operator' or not joined(named, operator, r'\s+(?:is\s+)?'):
            index += 1
            continue
        if operator.operation in COLUMN_TESTS:
            span = Span(turn=named.span.turn, start=named.span.start, end=operator.span.end)
            out[index:index + 2] = [Piece(span, text[span.start:span.end], 'predicate', named.candidates, operator.operation, None)]
            index += 1
            continue
        compared = out[index + 2] if index + 2 < len(out) else None
        member = declared_member(named, compared)
        if (operator.operation not in COLUMN_COMPARISONS or compared is None or not joined(operator, compared, r'\s+')
                or (compared.kind not in ('unknown', 'literal') and not member)
                or (compared.kind == 'literal' and not text[named.span.end:operator.span.start].strip())):
            index += 1
            continue
        value = compared.value if member or (compared.kind == 'literal' and compared.value is not None) else compared.text
        span = Span(turn=named.span.turn, start=named.span.start, end=compared.span.end)
        candidates = tuple(i for i in named.candidates if i in compared.candidates) if member else named.candidates
        out[index:index + 3] = [Piece(span, text[span.start:span.end], 'predicate', candidates, operator.operation, value)]
        index += 1
    return out


def phrase_table(semantics):
    """The phrase table the lexer reads for a release: the graph's (Graph.vocabulary().phrases, read once per release).
    A graph loaded before meta-model 1.6.0 holds no Phrase rows and reads the constant baseline, with source
    "constant", which an evaluation counts as a read that fell back."""
    return getattr(getattr(semantics, 'graph_vocabulary', None), 'phrases', None) or BASELINE_PHRASES


@contextmanager
def watching(watcher):
    """An evaluation instrument: while active, watcher(text, turn, vocabulary) is called on every ledger() read, from
    any thread. Production never registers one, and ledger() reads the empty tuple once per call."""
    global _WATCHERS
    with _WATCHING:
        _WATCHERS = (*_WATCHERS, watcher)
    try:
        yield watcher
    finally:
        with _WATCHING:
            held = list(_WATCHERS)
            if watcher in held:
                held.remove(watcher)
            _WATCHERS = tuple(held)


def ledger(text, turn, vocabulary, *, baseline=False):
    """Reuse a scoped vocabulary's evidence, copying the mutable period readings for each caller. The reading is the
    graph's phrase table (lexical_ledger); only the coded rules' reading (planrules.read) passes baseline=True, which
    reads a table equal to the constant baseline (baseline_ledger) until B7 (#455). Every other rules-path reader,
    planpublic's floor and routing, planner, combined and plancontract among them, reads the graph's table, so on a
    graph a pack extends their full_match and refusal reasons follow the graph's reading while planrules.read follows
    the constant's. Watchers see each read before it is lexed, so a read that
    raises is recorded too."""
    from kge.services.ask.engine.question import Period
    for watcher in _WATCHERS:
        watcher(text, turn, vocabulary)
    read = (getattr(vocabulary, 'baseline_ledger', None) or vocabulary.lexical_ledger) if baseline else vocabulary.lexical_ledger
    pieces = read(text, turn, vocabulary.binding)
    return tuple(replace(p, value=replace(p.value)) if isinstance(p.value, Period) else p for p in pieces)


def new_phrase(text, turn, vocabulary):
    """The observation's new_phrase mark: the graph-side reading used a phrase beyond baseline v1, so the graph's ledger
    differs from the constant's. Such an observation is counted apart and left out of mirrored M1, M3 and M6 (B3)."""
    if getattr(vocabulary, 'baseline_ledger', None) is getattr(vocabulary, 'lexical_ledger', None):
        return False
    return ledger(text, turn, vocabulary) != ledger(text, turn, vocabulary, baseline=True)


def mirror_ledgers(text, turn, vocabulary):
    """For evaluation only: the text lexed afresh with the vocabulary's phrase table and with the constant baseline, with
    no cache and no short-circuit, so the two can be compared piece for piece."""
    table = getattr(vocabulary, 'phrase_table', None) or BASELINE_PHRASES
    return tuple(_ledger(text, turn, vocabulary, table)), tuple(_ledger(text, turn, vocabulary, BASELINE_PHRASES))


def _ledger(text, turn, vocabulary, table=None):
    from kge.services.ask.engine.question import NUMBER_PATTERN, number_value, numbered_period_evidence, period_evidence, qualified_phrases, read_period, window_points
    from kge.semantics.calendarattributes import matches as attribute_matches, masked_question
    table = table or getattr(vocabulary, 'phrase_table', None) or BASELINE_PHRASES
    attributes = attribute_matches(text, vocabulary)
    recurring = {match.start: match for match in attributes}
    repeated = {match.repeat_start: match for match in attributes if match.repeat_start is not None}
    declared = {phrase: 'as_reported' if mode == 'as_reported' else 'current'
                for phrase, mode in getattr(vocabulary.semantics.d, 'TIME_MODES', {}).items()}
    declared.update({p: 'latest_released' for p, v in getattr(vocabulary.semantics.d, 'NAMED_PERIODS', {}).items() if v == 'latest'})
    grouping = getattr(vocabulary.semantics.d, 'GROUP_BY', {})
    syntax = {**table.operators, **declared}
    declared_aggregations = bool(getattr(vocabulary.semantics, 'measure_ids', ())) or any(
        spec.aggregations for spec in vocabulary.measures.values())
    if not declared_aggregations:
        syntax = {phrase: operation for phrase, operation in syntax.items() if operation not in ('avg', 'min', 'max', 'median')}
    ranked_breakdown = any(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I) for phrase in grouping)
    if declared_aggregations and not ranked_breakdown and not re.search(
            r'\b(?:which|top|bottom)\b|\b' + table.rank_pattern + r'\s+' + NUMBER_PATTERN + r'\b', text, re.I):
        for word, operation in (('lowest', 'min'), ('smallest', 'min'), ('highest', 'max'), ('largest', 'max')):
            if any(operation in (spec.aggregations or tuple(c.aggregation for c in vocabulary.contexts.values() if c.measure == spec.id))
                   for spec in vocabulary.measures.values()):
                syntax[word] = operation
    names = dict(vocabulary.phrases)
    grouping = dict(grouping)
    ranking_groups = {}
    ranking_partitions = set()
    ranking_time_partitions = {}
    list_groups = {}
    for phrase, column in tuple(grouping.items()):
        if phrase.startswith('by ') and column in vocabulary.entries:
            each = 'each ' + phrase[3:]
            grouping[each] = column
            names[each] = (column,)
            list_groups[phrase[3:]] = column
    group_list = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in sorted({*list_groups, *GRAIN_WORDS}, key=len, reverse=True)) + r')(?!\w)', re.I)
    coordinated = {}
    if group_list:
        for match in group_list.finditer(text):
            prefix = text[:match.start()]
            if re.search(r'\b(?:by|per|each)\s*$', prefix, re.I) or re.search(r'(?:,|\band)\s*$', prefix, re.I) and any(
                    re.fullmatch(r'\s*(?:,|and)\s*', text[end:match.start()], re.I) for end in coordinated):
                coordinated[match.end()] = match.start()
                noun = match.group().casefold()
                if noun in list_groups and not re.search(r'\b(?:by|per|each)\s*$', prefix, re.I):
                    if any(len(phrase) > len(noun) and re.match(re.escape(phrase) + r'(?!\w)', text[match.start():], re.I)
                           for phrase, ids in names.items() if any(i in vocabulary.contexts for i in ids)):
                        continue
                    names[noun] = (list_groups[noun],)
                    grouping[noun] = list_groups[noun]
    rank_words = tuple(re.finditer(r'\b' + table.rank_pattern + r'\b', text, re.I))
    named_ranks = [match.span() for phrase in names if re.search(r'\b' + table.rank_pattern + r'\b', phrase, re.I)
                   for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)] if rank_words else ()
    if any(not any(start <= word.start() < word.end() <= end for start, end in named_ranks) for word in rank_words):
        from kge.services.ask.engine.question import plural_forms
        syntax.pop('which', None)
        ranking_partitions.update(p for p in grouping if p.startswith(('in each ', 'within each ', 'per ')))
        for grain in get_args(Grain):
            nouns = (grain.replace('_', ' '), 'quarter') if grain == 'calendar_quarter' else (grain.replace('_', ' '),)
            for noun in nouns:
                for prefix in ('in each ', 'within each ', 'per '):
                    phrase = prefix + noun
                    if phrase in declared:
                        continue
                    syntax[phrase] = 'dimension'
                    ranking_time_partitions[phrase] = grain
        for phrase, column in tuple(grouping.items()):
            if phrase.startswith('by ') and column in vocabulary.entries:
                noun = phrase[3:]
                for prefix in ('in each ', 'within each ', 'per '):
                    partition = prefix + noun
                    if partition in names and (partition not in grouping or grouping[partition] != column
                                               or set(names[partition]) != {column}):
                        continue
                    grouping[partition] = column
                    names[partition] = (column,)
                    ranking_partitions.add(partition)
                for form in (*plural_forms(noun), noun + 'es' if noun.endswith(('ch', 'sh', 's', 'x')) else noun + 's'):
                    ranking_groups[form] = column
                    names.setdefault(form, (column,))
    declared_matches = [(column, match, filter_value(match, group, value))
                        for (pattern, _, _, group, value), column in zip(getattr(vocabulary.semantics.d, 'FILTERS', ()),
                            getattr(vocabulary.semantics.d, 'FILTER_COLUMNS', ()))
                        if column in vocabulary.entries for match in pattern.finditer(text)]
    coordinated = any(column == other_column and value != other_value and left.end() <= right.start()
                      and re.fullmatch(r'(?:\s|,|\band\b|\bto\b|\bgross\b)+|-to-', text[left.end():right.start()], re.I)
                      for column, left, value in declared_matches for other_column, right, other_value in declared_matches)
    if coordinated:
        syntax['net'] = 'aggregate'
    gross_columns = {column for column, match, _ in declared_matches if re.search(r'\bgross\s*$', text[:match.start()], re.I)}
    if gross_columns and not any(any(i in vocabulary.contexts or vocabulary.entries[i].kind == 'measure' for i in ids)
                                and re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)
                                for phrase, ids in names.items()):
        candidates = tuple(c.id for c in vocabulary.contexts.values()
                           if c.id in vocabulary.semantics.context_ids
                           and any(column.rsplit('.', 1)[0] == c.fact for column in gross_columns))
        if len(candidates) == 1:
            names['gross'] = candidates
    literal = re.compile(r'FY\d{2,4}(?:-Q[1-4])?|\d{4}-\d{2}(?:-\d{2})?|\d{4}-Q[1-4]|-?\d+(?:\.\d+)?|[\w]+|>=|<=|!=|[><=]', re.I)
    amount = re.compile(r'\$\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)([KMB])?(?![\w,])', re.I)
    scaffold = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in table.filler_phrases) + r')(?!\w)', re.I) if table.filler_phrases else re.compile(r'(?!)')
    title = (getattr(vocabulary.semantics.d, 'TITLE', None) or '').split()
    owner = re.compile(r'(?<!\w)' + re.escape(title[0]) + r"['’]s(?!\w)", re.I) if title else None
    months = '|'.join((*calendar.month_name[1:], *calendar.month_abbr[1:]))
    temporal = re.compile(r'(?:calendar\s+)?Q[1-4]\s+\d{4}|(?:\d{1,2}\s+)?(?:' + months + r')\s+\d{4}', re.I)
    points = {start: (end, period) for start, end, period in period_evidence(masked_question(text, vocabulary))}
    period_names = {phrase.casefold(): ids for phrase, ids in names.items()}
    named_period_sources = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in sorted(period_names, key=lambda p: (-len(p), p))) + r')(?!\w)', re.I) if period_names else re.compile(r'(?!)')
    sources = [Piece(Span(turn=turn, start=m.start(), end=m.end()), m[0], 'name', period_names[m[0].casefold()])
               for m in named_period_sources.finditer(text)]
    try:
        selected_calendar = calendar_binding('day', sources, vocabulary)
    except ValueError:
        selected_calendar = None
    numbered_windows = numbered_period_evidence(text, vocabulary.calendars, selected_calendar)
    for window in numbered_windows:
        for start, end, period in window_points(text, window):
            points[start] = (end, period)
    for pattern in (r'(?<!\w)(Q[1-4])\s+(?:through|to|vs\.?|versus|against)\s+Q[1-4]\s+(\d{4})(?!\w)',
                    r'(?<!\w)(' + months + r')\s+(?:through|to|vs\.?|versus|against)\s+(?:' + months + r')\s+(\d{4})(?!\w)',
                    r'\bbetween\s+(' + months + r')\s+and\s+(?:' + months + r')\s+(\d{4})(?!\w)'):
        for match in re.finditer(pattern, text, re.I):
            period, problem = read_period(match[1] + ' ' + match[2])
            if period is not None and problem is None:
                points[match.start(1)] = (match.end(1), period)
    if any(re.search(r'\bbetween\s*$', text[:start], re.I)
           for start in {*points, *(match.start() for match in temporal.finditer(text))}):
        syntax['between'] = 'range'
    phrases = sorted(set(names) | set(syntax), key=lambda s: (-len(s), s))
    rx = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in phrases) + r')(?!\w)', re.I)
    qualifiers = {}
    measure_names = {phrase: ids for phrase, ids in names.items() if any(
        identifier in vocabulary.contexts or vocabulary.entries[identifier].kind == 'measure' for identifier in ids)}
    qualifier_stops = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(phrase) for phrase, operation in syntax.items()
        if operation in ('comparison', 'current')) + r')(?!\w)', re.I)
    for start, end, phrase, noun_start, noun_end in qualified_phrases(
            text, measure_names, getattr(vocabulary.semantics.d, 'TIME_MODES', ()), compounds=True,
            members=tuple(match.span() for _, match, _ in declared_matches), nouns=True):
        if not text[start:].casefold().startswith(phrase.casefold()):
            qualifiers[start] = (end, names[phrase])
            if noun_end <= start:
                qualifiers[noun_start] = (noun_end, names[phrase])
                continue
            noun = re.search(r'(?<!\w)' + re.escape(phrase[end - start:].lstrip()) + r'(?!\w)', text[end:], re.I)
            if noun and not qualifier_stops.search(text[end:end + noun.start()]):
                qualifiers[end + noun.start()] = (end + noun.end(), names[phrase])
    qualifiers.update(modified_measures(text, names, vocabulary))
    modifier_predicates = {}
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        if row['kind'] != 'predicate' or not row['params'].get('modifier'):
            continue
        rule = row['params']
        for phrase in rule['phrases']:
            for noun in rule.get('with', ()):
                candidates = tuple(c for c in names.get(noun.casefold(), ()) if c in vocabulary.contexts
                                   and vocabulary.contexts[c].fact == rule['column'].rsplit('.', 1)[0])
                if candidates:
                    for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I):
                        for head in re.finditer(r'(?<!\w)' + re.escape(noun) + r'(?!\w)', text[match.end():], re.I):
                            start, end = match.end() + head.start(), match.end() + head.end()
                            gap = list(text[match.end():start])
                            for first, last, period in period_evidence(''.join(gap)):
                                points[match.end() + first] = (match.end() + last, period)
                            for first, (last, _) in points.items():
                                if match.end() <= first < last <= start:
                                    gap[first - match.end():last - match.end()] = ' ' * (last - first)
                            if gap and not ''.join(gap).strip():
                                modifier_predicates[match.start()] = (match.end(), rule)
                                qualifiers[start] = (end, candidates)
                                break
    filters = tuple(zip(getattr(vocabulary.semantics.d, 'FILTERS', ()), getattr(vocabulary.semantics.d, 'FILTER_COLUMNS', ())))
    listed = coordinated_lists(text, getattr(vocabulary.semantics.d, 'FILTERS', ()), getattr(vocabulary.semantics.d, 'FILTER_COLUMNS', ()),
                               lambda column: column in vocabulary.entries)
    currencies = tuple(match.span() for match in rx.finditer(text) if any(
        vocabulary.entries[identifier].kind == 'currency' for identifier in names.get(match.group().casefold(), ())))
    out, i = [], 0
    window_grains = '|'.join(re.escape(g.replace('_', ' ')) for g in sorted((*get_args(Grain), 'quarter'), key=len, reverse=True))
    moving_window = re.compile(r'(\d+)\s*[- ]\s*(' + window_grains + r')s?\s+moving average\b', re.I)
    while i < len(text):
        if i in repeated:
            attribute = repeated[i]
            out.append(Piece(Span(turn=turn, start=i, end=attribute.repeat_end), text[i:attribute.repeat_end],
                             'operator', operation='dimension', value=attribute.cycle))
            i = attribute.repeat_end
            continue
        if i in recurring:
            attribute = recurring[i]
            out.append(Piece(Span(turn=turn, start=i, end=attribute.end), text[i:attribute.end], 'predicate',
                             (attribute.column,), 'eq', attribute.value))
            i = attribute.end
            continue
        moving = moving_window.match(text, i)
        declared_name = rx.match(text, i) if moving else None
        if moving and not (declared_name and declared_name.group().casefold() in names and declared_name.end() >= moving.end()):
            grain = {'quarter': 'calendar_quarter'}.get(moving[2].casefold(), moving[2].casefold().replace(' ', '_'))
            end = moving.end()
            out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'operator',
                             operation='moving_average', value=(int(moving[1]), grain)))
            i = end
            continue
        owned = owner.match(text, i) if owner else None
        if owned:
            i = owned.end()
            continue
        possessive = re.match(r"['’]s\b", text[i:], re.I)
        if possessive and out and out[-1].span.end == i and out[-1].kind in ('name', 'predicate'):
            i += possessive.end()
            continue
        match = scaffold.match(text, i)
        if match:
            i = match.end()
            continue
        match = re.match(r'(each\b|and\b|,)(?=\s+(?:' + '|'.join(re.escape(word) for word in GRAIN_WORDS) + r')\b)', text[i:], re.I)
        if match and (match[1].casefold() == 'each' or any(p.kind == 'name' and p.operation == 'dimension' for p in out)):
            end = i + match.end()
            partition = 'rank_partition' if out and any(ranking_scope(out[-1:])) else None
            out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'operator', operation='dimension', value=partition))
            i = end
            continue
        if i in modifier_predicates:
            end, rule = modifier_predicates[i]
            if rule['op'] == 'eq' and rule['column'] in vocabulary.entries:
                out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'predicate',
                                 (rule['column'],), 'eq', rule['value']))
                i = end
                continue
        if i in qualifiers:
            end, candidates = qualifiers[i]
            for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
                if row['kind'] != 'predicate' or not row['params'].get('modifier'):
                    continue
                rule = row['params']
                for phrase in rule['phrases']:
                    prefix = re.match(re.escape(phrase) + r'\s+', text[i:end], re.I)
                    if (prefix and rule['op'] == 'eq' and rule['column'] in vocabulary.entries
                            and all(vocabulary.contexts[c].fact == rule['column'].rsplit('.', 1)[0] for c in candidates)):
                        stop = i + len(phrase)
                        out.append(Piece(Span(turn=turn, start=i, end=stop), text[i:stop], 'predicate',
                                         (rule['column'],), 'eq', rule['value']))
                        i += prefix.end()
                        break
            out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'name' if candidates else 'unknown', candidates))
            i = end
            continue
        match = amount.match(text, i)
        if match:
            value = Decimal(match[1].replace(',', '')) * {'': 1, 'K': 10 ** 3, 'M': 10 ** 6, 'B': 10 ** 9}[(match[2] or '').upper()]
            out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'literal', value=format(value, 'f')))
            i = match.end()
            continue
        if i in points:
            end, period = points[i]
            out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'time', value=period))
            i = end
            continue
        if text[i] in '-–—' and out and out[-1].kind == 'time' and any(
                i < start and not text[i + 1:start].strip() for start in points):
            out.append(Piece(Span(turn=turn, start=i, end=i + 1), text[i], 'operator', operation='range'))
            i += 1
            continue
        match = temporal.match(text, i)
        if match:
            out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'time'))
            i = match.end()
            continue
        filtered = None
        for index, ((pattern, _, op, group, value), column) in enumerate(filters):
            matched = pattern.match(text, i)
            if matched is None:
                later = pattern.search(text, i)
                prefix = text[i:later.start()].strip().casefold() if later else ''
                if prefix and vocabulary.candidates(prefix) and all(e.kind == 'unit' and e.id == column.split('.')[0] for e in vocabulary.candidates(prefix)):
                    matched = later
            if matched and isinstance(value, Listed) and (index, *matched.span()) not in listed:
                continue
            if matched and column in vocabulary.entries:
                if any(i <= start < matched.end() < end for start, end in currencies):
                    continue
                scalar = filter_value(matched, group, value)
                bare_value = group and matched.span() == matched.span(group)
                member = member_value(tuple(e.id for e in vocabulary.candidates(str(scalar))), vocabulary) if bare_value else None
                if member is not None:
                    column, scalar = member.column, member.value
                if filtered is None or matched.end() > filtered[0].end():
                    filtered = (matched, [column], op, scalar)
                elif matched.span() == filtered[0].span() and op == filtered[2] and scalar == filtered[3]:
                    filtered[1].append(column)
        if filtered:
            declared_name = rx.match(text, i)
            if (declared_name and declared_name.group().casefold() in names and declared_name.end() > filtered[0].end()
                    and any(vocabulary.entries[identifier].kind != 'value' for identifier in names[declared_name.group().casefold()])):
                filtered = None
        if filtered:
            matched, columns, op, value = filtered
            values = tuple(e.id for e in vocabulary.candidates(text[i:matched.end()]) if e.kind == 'value')
            if len(values) > 1 and member_value(values, vocabulary) is None:
                out.append(Piece(Span(turn=turn, start=i, end=matched.end()), text[i:matched.end()], 'name', values))
                i = matched.end()
                continue
            if re.match(r'\s+contribut(?:ion|e)\b', text[matched.end():], re.I):
                out.append(Piece(Span(turn=turn, start=i, end=matched.end()), text[i:matched.end()], 'name', tuple(dict.fromkeys(columns)), 'dimension'))
                i = matched.end()
                continue
            out.append(Piece(Span(turn=turn, start=i, end=matched.end()), text[i:matched.end()], 'predicate', tuple(dict.fromkeys(columns)),
                             {'=': 'eq', '<>': 'ne', '!=': 'ne', '= TRUE': 'eq', 'IS NULL': 'null', 'IS NOT NULL': 'not_null',
                              '>': 'gt', '<': 'lt', '>=': 'ge', '<=': 'le', 'IN': 'in', 'NOT IN': 'not_in',
                              'CONTAINS': 'contains', 'STARTS WITH': 'starts_with'}[op], True if op == '= TRUE' else value))
            i = matched.end()
            continue
        match = rx.match(text, i)
        if match:
            phrase = match.group().casefold()
            if phrase.startswith('by ') and any(
                    re.match(re.escape(noun) + r'(?!\w)', text[i + 3:], re.I)
                    for noun, ids in names.items() if len(noun) > len(phrase[3:]) and any(c in vocabulary.contexts for c in ids)):
                out.append(Piece(Span(turn=turn, start=i, end=i + 2), text[i:i + 2], 'operator', operation='dimension'))
                i += 2
                continue
            ranked = phrase in ranking_groups and (re.search(r'\b(?:which|' + table.rank_pattern + r')\s+(?:' + NUMBER_PATTERN + r'\s+)?$', text[:i], re.I)
                     or re.search(r'\b(?:rank|ranked|ranking)\b', text, re.I))
            ids = ((ranking_groups[phrase],) if ranked else names.get(phrase, ())
                   if phrase in grouping or phrase not in ranking_groups else vocabulary.phrases.get(phrase, ()))
            if member := member_value(ids, vocabulary):
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'predicate',
                                 (member.column,), 'eq', member.value))
                i = match.end()
                continue
            if out and out[-1].operation in ('running_total', 'moving_average'):
                prior = {c for p in out[:-1] if p.kind == 'name'
                         and (p.text.casefold() == phrase or p.text.casefold().endswith(' ' + phrase))
                         for c in p.candidates if c in vocabulary.contexts}
                if len(prior) == 1:
                    out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'operator', tuple(prior), out[-1].operation))
                    i = match.end()
                    continue
            if len(ids) == 1 and ids[0] in vocabulary.entities:
                entity = vocabulary.entities[ids[0]]
                selector = re.match(r'\s+(-?\d+)(?![\w.])', text[match.end():])
                if selector and len(entity['key']) == 1:
                    column = entity['table'] + '.' + entity['key'][0]
                    if column in vocabulary.entries:
                        end = match.end() + selector.end()
                        out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'predicate', (column,), 'eq', int(selector.group(1))))
                        i = end
                        continue
            following = text[match.end():].lstrip()
            if (phrase in ('and above', 'or above', 'and below', 'or below', 'or less', 'and over', 'and under') and re.match(
                    r'(?:\$|-?\d|' + NUMBER_PATTERN + r'\b)', following, re.I)) or (
                    phrase in ('or less', 'or more') and re.match(r'than\b', following, re.I)):
                connector = phrase.split()[0]
                end = i + len(connector)
                if connector == 'or':
                    out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'operator', operation='any'))
                i = end
                continue
            predicate_following = re.sub(r'^(?:the|a|an)\s+', '', following, flags=re.I)
            if phrase == 'in' and any(re.fullmatch(r'\s*(?:the\s+)?', text[match.end():start], re.I)
                                      for start in points if start >= match.end()):
                i = match.end()
                continue
            if phrase == 'in' and any(pattern.match(predicate_following) for (pattern, *_), column in filters if column in vocabulary.entries):
                i = match.end()
                continue
            if (phrase == 'in' and out and out[-1].operation in ('difference', 'percent_change')
                    and any(any(i in vocabulary.contexts or vocabulary.entries[i].kind == 'measure' for i in ids)
                            and re.match(re.escape(noun) + r'(?!\w)', following, re.I) for noun, ids in names.items())):
                i = match.end()
                continue
            if phrase == 'in' and (temporal.match(following) or re.match(r'(?:FY|\d{4})', following) or any(e.kind in ('unit', 'currency') and re.match(re.escape(p) + r'\b', following, re.I) for p, ids in vocabulary.phrases.items() for e in [vocabulary.entries[ids[0]]] if len(ids) == 1)):
                i = match.end()
                continue
            if not ids and phrase == 'over' and any(re.fullmatch(r'\s*(?:the\s+)?(?:(?:calendar\s+)?year\s+)?', text[match.end():start], re.I) for start in points if start >= match.end()):
                i = match.end()
                continue
            adverb = GRAIN_ADVERBS.get(phrase) if not ids else None
            if phrase in ranking_partitions:
                adverb = 'rank_partition'
            elif phrase in ranking_time_partitions and not ids:
                adverb = ('rank_partition', ranking_time_partitions[phrase])
            elif phrase in grouping and out and any(ranking_scope(out[-1:])) and re.fullmatch(
                    r'\s*(?:,\s*(?:and\s*)?|and\s+)', text[out[-1].span.end:i], re.I):
                adverb = 'rank_partition'
            elif syntax.get(phrase) in get_args(Grain) and out and (
                    out[-1].operation == 'dimension' and out[-1].value == 'rank_partition'
                    or any(ranking_scope(out[-1:])) and re.fullmatch(r'\s*(?:,\s*(?:and\s*)?|and\s+)', text[out[-1].span.end:i], re.I)):
                adverb = ('rank_partition', syntax[phrase])
            operation = 'dimension' if ranked or phrase in grouping or adverb else syntax.get(phrase) if not ids else None
            if ids and phrase in ranking_time_partitions and phrase not in grouping:
                operation = None
            if ids and operation == 'moving_average':
                operation = None
            if phrase == 'share' and any(p.kind == 'name' and any(c in vocabulary.contexts for c in p.candidates) for p in out) and re.match(r'\s+of\s+', text[match.end():], re.I):
                tail = re.sub(r'^\s+of\s+', '', text[match.end():], flags=re.I)
                if any(noun not in ('total', 'firm', 'all') and any(vocabulary.entries[i].kind in ('context', 'measure') for i in ids)
                       and re.match(re.escape(noun) + r'(?!\w)', tail, re.I) for noun, ids in names.items()):
                    operation = 'ratio'
            whole = re.match(r'\s+of\s+(?:(?:the\s+)?(?:firm|total)|all)\b', text[match.end():], re.I) if operation == 'share' else None
            out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'name' if ids else 'operator', ids,
                             operation, 'whole_population' if whole and not whole.group().casefold().endswith('total') else adverb))
            i = match.end()
            if whole and not whole.group().casefold().endswith('total'):
                i += whole.end()
            continue
        match = literal.match(text, i)
        if match:
            word = match.group()
            if re.fullmatch(r'FY\d{2,4}', word, re.I):
                from kge.services.ask.engine.question import Period
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), word, 'time', value=Period('fiscal_year', word.upper())))
            elif (re.fullmatch(r'\d{4}', word) and out and out[-1].operation in ('year', 'fiscal_year')
                  and not text[out[-1].span.end:i].strip()):
                from kge.services.ask.engine.question import Period
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), word, 'time', value=Period(out[-1].operation, word)))
            elif word.casefold() not in table.filler:
                number = number_value(word)
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), word, 'literal' if number is not None or re.fullmatch(r'FY\d{2,4}(?:-Q[1-4])?|-?\d+(?:\.\d+)?|\d{4}-\d{2}(?:-\d{2})?|\d{4}-Q[1-4]', word) else 'unknown', value=number))
            i = match.end()
        else:
            if text[i] == '-' and (text[i + 1:i + 4].casefold() == 'to-' or text[max(0, i - 3):i].casefold() == '-to'):
                i += 1
                continue
            if not text[i].isspace() and text[i] not in ',.?!:;()\'"':
                if text[i] == '-' and out and out[-1].kind == 'literal' and any(
                        re.match(re.escape(word) + r'(?!\w)', text[i + 1:], re.I) for word in GRAIN_WORDS):
                    i += 1
                    continue
                out.append(Piece(Span(turn=turn, start=i, end=i + 1), text[i], 'unknown'))
            i += 1
    times = [p for p in out if p.kind == 'time']
    heads = [p for p in out if p.kind == 'name' and times and p.span.end <= times[0].span.start
             and any(i in vocabulary.contexts for i in p.candidates)]
    bare_modes = [p for p in out if times and p.span.end <= times[0].span.start
                  and p.operation in ('current', 'as_reported') and not p.candidates
                  and not any(h.span.start >= p.span.end and text[p.span.end:h.span.start].isspace() for h in heads)]
    changing = len(times) == 2 and not (grouping_grains(out, text) and any(
        p.operation in ('previous_period', 'previous_year') for p in out)) and bool(re.search(r'\bfrom\s*$', text[:times[0].span.start], re.I)) and (
        any(p.operation in ('difference', 'percent_change') and p.span.end <= times[0].span.start for p in out)
        or any(p.text.casefold() == 'compare' and p.span.end <= times[0].span.start for p in out)
        and len(heads) + len(bare_modes) <= 1
        and any(p.operation in ('difference', 'percent_change') for p in out))
    if changing and not any(p.operation == 'comparison' for p in out):
        requested = ('difference', 'percent_change') if any(p.operation == 'difference' for p in out) else ('percent_change',)
        out = [replace(p, operation='comparison', value=requested)
               if p.operation in ('difference', 'percent_change') else p for p in out]
    if len(times) == 2 and re.search(r'\bwith\b', text[times[0].span.end:times[1].span.start], re.I):
        out = [replace(p, value=('difference', 'percent_change')) if p.text.casefold() == 'compare' else p for p in out]
    comparison_to = (len(times) == 2 and re.fullmatch(r'\s*to\s*', text[times[0].span.end:times[1].span.start], re.I)
                     and re.search(r'\bcompare\b', text[:times[0].span.start], re.I)
                     and len(heads) + len(bare_modes) <= 1
                     and not re.search(r'\bfrom\s*$', text[:times[0].span.start], re.I))
    groups = []
    for piece in times:
        connector = text[groups[-1][-1].span.end:piece.span.start] if groups else ''
        between = groups and re.search(r'\bbetween\s*$', text[:groups[-1][0].span.start], re.I)
        if groups and not comparison_to and not changing and (
                re.fullmatch(r'\s*(?:through|to)\s*', connector, re.I)
                or between and re.fullmatch(r'\s*and\s*', connector, re.I)):
            groups[-1].append(piece)
        else:
            groups.append([piece])
    windows = {piece.span: tuple(p.span for p in group) for group in groups if len(group) > 1 for piece in group}
    for start, boundary, end, first, last in numbered_windows:
        point_spans = window_points(text, (start, boundary, end, first, last))
        if len(point_spans) == 2 and point_spans[0][1] < boundary and (changing or comparison_to):
            continue
        selected = [p.span for p in times if start <= p.span.start and p.span.end <= end]
        if len(selected) == 2:
            windows.update({span: tuple(selected) for span in selected})
    predicates = [p for p in out if p.kind == 'predicate']
    for left, right in zip(predicates, predicates[1:]):
        gap = text[left.span.end:right.span.start]
        if (re.fullmatch(FILTER_CONNECTOR, gap, re.I) and re.search(r'\band\b', gap, re.I)
                and not any(separate_filter_parts(p, out, text) for p in (left, right))):
            match = re.search(r'\band\b', gap, re.I)
            start, end = left.span.end + match.start(), left.span.end + match.end()
            out.append(Piece(Span(turn=turn, start=start, end=end), text[start:end], 'operator', operation='all'))
    out.sort(key=lambda p: p.span.start)
    count_connectors = _count_list_connectors(out, text, vocabulary)
    out = [piece for piece in out if piece.span not in count_connectors]
    if grouping_grains(out, text):
        out = [replace(p, operation='running_total') if p.kind == 'operator' and p.text.casefold() == 'to date' else p for p in out]
    directions = {p.operation for p in out if p.text.casefold() not in RANK_VERBS and p.operation in ('asc', 'desc')}
    out = [replace(p, operation='asc' if directions == {'asc'} else 'desc')
           if p.kind == 'operator' and p.text.casefold() in RANK_VERBS else p for p in out]
    quantified, _ = boolean_groups(out, text)
    predicates = [p for p in out if p.kind == 'predicate']
    grouped_filters = {predicates[index].span for group in quantified if len(leaves(group)) > 1 for index in leaves(group)}
    for group in quantified:
        members = leaves(group)
        if len(members) > 1 and list_quantifier(out, text, predicates[members[0]]):
            for a, b in zip(group[1], group[1][1:]):
                first, last = predicates[leaves(a)[-1]], predicates[leaves(b)[0]]
                out = [replace(p, operation=group[0]) if p.kind == 'operator' and p.operation in ('all', 'any')
                       and first.span.end <= p.span.start < last.span.start else p for p in out]
    for index, piece in enumerate(out[:-1]):
        following = out[index + 1]
        if (piece.kind == 'operator' and piece.text.casefold() in OPERATOR_FAMILIES['in'] and following.kind == 'predicate'
                and re.fullmatch(r'[\s(]*(?:(?:the|only)[\s(]+)*', text[piece.span.end:following.span.start], re.I)):
            out[index] = replace(piece, operation='any' if piece.text.casefold() == 'any of' and following.span in grouped_filters else following.operation)
    out = column_comparisons(magnitude_filters(share_split(out, text, vocabulary), text, vocabulary), text, vocabulary)
    from kge.services.ask.engine.question import requested_currencies
    currencies = set(requested_currencies(text))
    return lexer_markers(tuple(replace(p, candidates=currency_candidates(p.candidates, vocabulary, currencies), window=windows.get(p.span, ()), cutoff=bool(
        p.kind == 'time' and re.search(r'\b(?:(?:as\s+)?at|as\s+of)\s*$', text[:p.span.start], re.I))) for p in out), text, vocabulary)


MARKERS = ('from_to', 'asks_one', 'measure_part')
ADJACENT_DIRECTION = 'adjacent_direction'
PART_OPERATIONS = {'is_null': ('null', None), 'is_not_null': ('not_null', None), 'is_true': ('eq', True)}


def _flagged_part(piece, rows):
    """A predicate piece reads a terminology predicate the pack flags `part: true`: one of its phrases, on the same
    column, operator and declared value or values (is_null, is_not_null and is_true read as the lexer's null, not_null
    and eq TRUE). Another predicate that reads the same filter by a phrase of its own is not marked."""
    for rule in rows:
        if piece.text.casefold() not in {str(phrase).casefold() for phrase in rule.get('phrases') or ()}:
            continue
        operation, implied = PART_OPERATIONS.get(rule.get('op'), (rule.get('op'), None))
        if rule.get('column') not in piece.candidates or operation != piece.operation:
            continue
        if implied is not None and piece.value is not implied:
            continue
        if 'value' in rule and piece.value != rule['value']:
            continue
        if 'values' in rule and (not isinstance(piece.value, (list, tuple)) or set(piece.value) != set(rule['values'])):
            continue
        return True
    return False


def lexer_markers(pieces, text, vocabulary):
    """The lexer markers of question-shapes.md §3.1, added to the pieces they mark. Platform code both rules adapters
    share; the coded planner ignores them, so they move nothing it builds. MARKERS are the names a template's
    `requires` and `forbids` may use (kge/compiler/standards.py lists the same three); ADJACENT_DIRECTION marks the
    number that a template's `{adjacent: [direction, number]}` constraint reads.

    - from_to, on both periods: two readable periods the lexer kept apart (not one window), joined by "to" or
      "through", with "from" before the first and a comparison, difference or percent-change word in the question:
      the condition on which the coded period comparison (planshapes.compose) takes the "to" period as its base.
    - asks_one, on a singular superlative direction word (most, highest, ...): a `which` the lexer dropped for the
      ranking comes before it, wherever that `which` stands.
    - adjacent_direction, on a whole-number literal: its neighbouring piece is a direction word (asc or desc) with no
      text between them, as in "top 5" or "5 lowest".
    - measure_part, on a predicate: it reads a terminology predicate the pack flags `part: true`."""
    from kge.services.ask.engine.question import read_period
    marks = {}
    times = [p for p in pieces if p.kind == 'time']
    if (len(times) == 2 and all(p.value or read_period(p.text.casefold())[0] for p in times)
            and not (times[0].window and times[0].window == times[1].window)
            and text[times[0].span.end:times[1].span.start].strip().casefold().rstrip('.') in ('to', 'through')
            and re.search(r'\bfrom\s*$', text[:times[0].span.start], re.I)
            and any(p.operation in ('comparison', 'difference', 'percent_change') for p in pieces)):
        for piece in times:
            marks.setdefault(piece.span, []).append('from_to')
    dropped = [m for m in re.finditer(r'\bwhich\b', text, re.I)
               if not any(p.span.start <= m.start() < p.span.end for p in pieces)]
    for piece in pieces:
        if (dropped and piece.kind == 'operator' and piece.operation in ('asc', 'desc')
                and piece.text.casefold() in SINGULAR_RANK_WORDS and dropped[0].end() <= piece.span.start):
            marks.setdefault(piece.span, []).append('asks_one')
    ordered = sorted(pieces, key=lambda p: p.span.start)
    for i, piece in enumerate(ordered):
        if piece.kind == 'literal' and str(piece.value if piece.value is not None else piece.text).isdecimal() and any(
                neighbor.operation in ('asc', 'desc')
                and not text[min(piece.span.end, neighbor.span.end):max(piece.span.start, neighbor.span.start)].strip()
                for neighbor in ordered[max(0, i - 1):i] + ordered[i + 1:i + 2]):
            marks.setdefault(piece.span, []).append(ADJACENT_DIRECTION)
    parts = [row['params'] for row in getattr(getattr(getattr(vocabulary, 'semantics', None), 'd', None), 'VOCABULARY', ())
             if row.get('kind') == 'predicate' and row.get('params', {}).get('part') is True]
    for piece in pieces:
        if parts and piece.kind == 'predicate' and _flagged_part(piece, parts):
            marks.setdefault(piece.span, []).append('measure_part')
    if not marks:
        return pieces
    return tuple(replace(p, markers=tuple(dict.fromkeys((*p.markers, *marks[p.span])))) if p.span in marks else p for p in pieces)


def share_split(pieces, text, vocabulary):
    """A share with no breakdown asked for, split by the column of one value it names: an equality on a column the pack
    groups by, directly before a share word ('X share of ...'), or the nearest before a complement ('... from X and
    how much from the rest'), becomes that column's breakdown (every value, the named one beside the rest) in place of
    its filter, and the complement is marked 'split' as the share it then is. Anything else is left as it was: an
    unanchored complement, or one whose value is coordinated with another filter, is still refused."""
    shares = [i for i, p in enumerate(pieces) if p.kind == 'operator' and p.operation == 'share']
    if any(p.operation == 'dimension' for p in pieces) or len(shares) != 1:
        return pieces
    at = shares[0]
    complement = pieces[at].text.casefold() in SHARE_COMPLEMENTS
    before = [i for i, p in enumerate(pieces[:at]) if p.kind == 'predicate']
    if not before or not complement and text[pieces[before[-1]].span.end:pieces[at].span.start].strip():
        return pieces
    index = before[-1]
    named = pieces[index]
    groupable = set(getattr(vocabulary.semantics.d, 'GROUP_BY', {}).values())
    if named.operation != 'eq' or len(named.candidates) != 1 or named.candidates[0] not in groupable or any(
            p.kind == 'predicate' and p.candidates == named.candidates for i, p in enumerate(pieces) if i != index):
        return pieces
    linked = [p for p in (*reversed(pieces[:index]), None) if p is None or p.operation not in ('all', 'any')][:1] + [
        p for p in (*pieces[index + 1:at], None) if p is None or p.operation not in ('all', 'any')][:1]
    if complement and any(p is not None and p.kind == 'predicate' for p in linked):
        return pieces
    out = list(pieces)
    out[index] = Piece(named.span, named.text, 'name', named.candidates, 'dimension')
    if complement:
        out[at] = replace(pieces[at], value='split')
    return out


def currency_candidates(identifiers, vocabulary, currencies):
    """A requested output currency narrows measures, preserving foreign-currency row constraints."""
    if not currencies:
        return tuple(identifiers)
    from kge.compiler.checks import ISO_4217
    def units(identifier):
        entry = vocabulary.entries[identifier]
        contexts = ([vocabulary.contexts[identifier]] if identifier in vocabulary.contexts else
                    [context for context in vocabulary.contexts.values() if context.measure == identifier]
                    if entry.kind == 'measure' else [])
        declared = {spec.currency for context in contexts
                    if (spec := vocabulary.measures.get(context.measure)) is not None and spec.currency}
        if declared:
            return declared
        columns = [column for context in contexts if context.id.startswith('schema:') for column in context.values]
        return {token.upper() for column in columns if column for token in column.rsplit('.', 1)[-1].split('_')
                if token.upper() in ISO_4217}
    return tuple(identifier for identifier in identifiers if not (known := units(identifier)) or known & currencies)


def period_candidates(identifiers, vocabulary, required):
    """A clarification cannot offer a measure whose arm has no required period binding."""
    def compatible(identifier):
        entry = vocabulary.entries[identifier]
        if entry.kind not in ('context', 'measure'):
            return True
        contexts = ([vocabulary.contexts[identifier]] if identifier in vocabulary.contexts else
                    [context for context in vocabulary.contexts.values() if context.measure == identifier]
                    if entry.kind == 'measure' else [])
        return any(not required or context.fact in vocabulary.periods for context in contexts)
    return tuple(identifier for identifier in identifiers if compatible(identifier))


def names_in(value):
    if isinstance(value, Name):
        yield value
    elif isinstance(value, Record):
        for field in type(value).model_fields:
            yield from names_in(getattr(value, field))
    elif isinstance(value, tuple):
        for item in value:
            yield from names_in(item)


def one_meaning(identifiers, vocabulary):
    if len(identifiers) == 1:
        return True
    if member_value(identifiers, vocabulary) is not None:
        return True
    entries = [vocabulary.entries[i] for i in identifiers]
    if {e.kind for e in entries} <= {'context', 'measure'}:
        contexts = [vocabulary.contexts[e.id] for e in entries if e.kind == 'context']
        measures = {e.id for e in entries if e.kind == 'measure'}
        return (bool(contexts) and len({c.term for c in contexts}) == 1 and len({c.unit for c in contexts}) == len(contexts)
                and measures <= {c.measure for c in contexts})
    if len(entries) != 2 or {e.kind for e in entries} != {'context', 'measure'}:
        return False
    context = next(e.id for e in entries if e.kind == 'context')
    measure = next(e.id for e in entries if e.kind == 'measure')
    return vocabulary.contexts[context].measure == measure


def member_value(identifiers, vocabulary):
    """Resolve one name or a code/name pair through exactly one declared readable member."""
    entries = [vocabulary.entries[i] for i in identifiers]
    if len(entries) not in (1, 2) or any(e.kind != 'value' or not isinstance(e.value, str) or not e.column for e in entries):
        return None
    resolved = []
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        if row['kind'] != 'code_name_pair':
            continue
        pair = row['params']
        code = next((e for e in entries if e.column == pair['code']), None)
        name = next((e for e in entries if e.column == pair['name']), None)
        if name is None or len(entries) == 2 and code is None:
            continue
        members = [m for m in pair['members'] if m['name'].casefold() == name.value.casefold()]
        if len(members) != 1:
            if members:
                return None
            continue
        if code is not None:
            if members[0]['code'] != code.value:
                continue
        else:
            codes = [entry for entry in vocabulary.entries.values() if entry.kind == 'value'
                     and entry.column == pair['code'] and entry.value == members[0]['code']]
            if len(codes) != 1:
                return None
            code, = codes
        resolved.append(code)
    return resolved[0] if len(resolved) == 1 else None


def name_supported(identifier, candidates, vocabulary, context=None, named_contexts=()):
    contexts = set(candidates) & vocabulary.contexts.keys()
    if context is not None and contexts and context not in contexts | set(named_contexts):
        return False
    return identifier in candidates or any(
        vocabulary.contexts[c].measure == identifier for c in contexts)


def typed_scalar(value, column, vocabulary):
    table, field = column.rsplit('.', 1)
    dtype = vocabulary.tables.get(table, {}).get(field, '').upper()
    try:
        if dtype == 'BOOLEAN' and (isinstance(value, bool) or str(value).casefold() in ('true', 'false')):
            return {'kind': 'boolean', 'value': value if isinstance(value, bool) else value.casefold() == 'true'}
        if 'INT' in dtype and not isinstance(value, bool) and re.fullmatch(r'-?\d+', str(value)):
            return {'kind': 'integer', 'value': int(value)}
        if any(t in dtype for t in ('INT', 'DECIMAL', 'NUMERIC', 'FLOAT', 'DOUBLE', 'REAL')) and not isinstance(value, bool):
            return DecimalValue(kind='decimal', value=format(Decimal(str(value)), 'f')).model_dump()
        if any(t in dtype for t in ('CHAR', 'STRING', 'TEXT')) and isinstance(value, str):
            return {'kind': 'text', 'value': value}
        if ('DATE' in dtype or 'TIME' in dtype) and isinstance(value, str):
            return DateValue(kind='date', value=value).model_dump()
    except (ValueError, ArithmeticError):
        pass
    return None


def predicate_value_supported(node, value, vocabulary):
    if inner := exclusion(node):
        return listed_values_supported(inner, value, vocabulary)
    if getattr(node, 'kind', None) == 'in':
        return listed_values_supported(node, value, vocabulary)
    operand = getattr(node, 'left', None)
    scalar = getattr(node, 'value', None)
    if getattr(operand, 'kind', None) != 'column' or scalar is None or scalar.kind == 'null':
        return False
    entry = vocabulary.entries.get(operand.name.id)
    if entry is None or entry.kind != 'column':
        return False
    expected = typed_scalar(value, entry.column, vocabulary)
    actual = typed_scalar(scalar.value, entry.column, vocabulary)
    if expected is None or actual is None:
        return False
    if expected['kind'] in ('integer', 'decimal'):
        return scalar.kind in ('integer', 'decimal') and Decimal(str(actual['value'])) == Decimal(str(expected['value']))
    return actual == expected



def declared_whole(column, vocabulary):
    """The label the pack gives a measure's unsplit figure when the measure is split by this column's listed values (a
    value vocabulary's whole), else None."""
    return next((r['params']['whole'] for r in getattr(vocabulary.semantics.d, 'VOCABULARY', ())
                 if r['kind'] == 'value' and r['params'].get('column') == column and r['params'].get('whole')), None)


def declared_part(piece, node, vocabulary):
    """A declared metric may already implement the exact requested row qualifier."""
    if getattr(node, 'kind', None) != 'measure' or piece.kind != 'predicate':
        return False
    context = vocabulary.contexts.get(node.context.id)
    if context is None:
        return False
    tables = {context.fact.rsplit('.', 1)[-1].casefold(): context.fact}
    tables.update({(join.alias or join.target.rsplit('.', 1)[-1]).casefold(): join.target for join in context.joins})
    conditions = [sqlglot.parse_one(rule.predicate, read='duckdb') for rule in context.filters]
    aggregate = sqlglot.parse_one(context.expression, read='duckdb')
    value = aggregate.this if isinstance(aggregate, exp.AggFunc) else None
    if (isinstance(value, exp.Case) and isinstance(value.args.get('default'), exp.Literal)
            and value.args['default'].is_number and Decimal(value.args['default'].this) == 0
            and len(value.args.get('ifs', ())) == 1):
        conditions.append(value.args['ifs'][0].this)
    kinds = {exp.EQ: 'eq', exp.NEQ: 'ne', exp.GT: 'gt', exp.GTE: 'ge', exp.LT: 'lt', exp.LTE: 'le'}
    for condition in conditions:
        if type(condition) not in kinds or kinds[type(condition)] != piece.operation:
            continue
        column, literal = condition.this, condition.expression
        if not isinstance(column, exp.Column) or not isinstance(literal, exp.Literal):
            continue
        for identifier in piece.candidates:
            entry = vocabulary.entries.get(identifier)
            if entry is not None and entry.kind == 'column' and entry.column is not None:
                table, field = entry.column.rsplit('.', 1)
                if (field.casefold() == column.name.casefold() and (f'{column.db}.{column.table}' if column.db else tables.get(column.table.casefold(), '')).casefold() == table.casefold()
                        and typed_scalar(piece.value, entry.column, vocabulary) == typed_scalar(literal.this, entry.column, vocabulary)):
                    return True
    return False


def predicate_supported(piece, node, vocabulary, plan):
    """A declared exclusion owns its exact null-preserving subtree as one predicate."""
    if declared_part(piece, node, vocabulary):
        return True
    if getattr(node, 'kind', None) in ('all', 'any') and exclusion(node) is None:
        return any(predicate_supported(piece, child, vocabulary, plan) for child in node.children)
    if piece.operation == 'not_in':
        return any(exclusion(root) is not None and getattr(node, 'id', None) in {p.id for p in predicate_nodes(root)}
                   and set(piece.candidates) & references(root, vocabulary)
                   and predicate_value_supported(root, piece.value, vocabulary)
                   for root in plan.nodes() if getattr(root, 'kind', None) == 'any')
    if nested_member(piece, node, vocabulary, plan):
        return True
    return (piece.operation in operations(node, vocabulary, plan)
            and bool(set(piece.candidates) & references(node, vocabulary))
            and (piece.operation in ('null', 'not_null') or predicate_value_supported(node, piece.value, vocabulary)))


def nested_member(piece, node, vocabulary, plan):
    """A stated equality whose words hold a shorter declared phrase or value of another column (a child member's name
    that holds its parent's) owns that column's equality too: both hold where the named member sits under the other,
    and the warehouse reads them together. It owns it only beside the stated equality itself, never in its place."""
    operand = getattr(node, 'left', None)
    if (piece.kind != 'predicate' or piece.operation != 'eq' or getattr(node, 'kind', None) != 'compare' or node.op != 'eq'
            or getattr(operand, 'kind', None) != 'column' or operand.name.id in piece.candidates):
        return False
    if not any(getattr(n, 'kind', None) == 'compare' and n.op == 'eq' and getattr(n.left, 'kind', None) == 'column'
               and n.left.name.id in piece.candidates and predicate_value_supported(n, piece.value, vocabulary)
               for n in plan.nodes()):
        return False
    declared = getattr(getattr(vocabulary, 'semantics', None), 'd', None)
    text = piece.text
    return any(column == operand.name.id and operator == '='
               and match.span() != (0, len(text if group else text.casefold()))
               and predicate_value_supported(node, filter_value(match, group, literal), vocabulary)
               for (pattern, _, operator, group, literal), column in zip(getattr(declared, 'FILTERS', ()) or (),
                                                                         getattr(declared, 'FILTER_COLUMNS', ()) or ())
               for match in pattern.finditer(text if group else text.casefold()))


def filter_group_problems(plan, pieces, text, vocabulary):
    """Compare boolean structure independently of a model's claimed evidence spans."""
    groups, problem = boolean_groups(pieces, text)
    if problem:
        return [problem]
    predicates = [p for p in pieces if p.kind == 'predicate']

    def canonical(kind, children):
        flattened = []
        for child in children:
            if isinstance(child, tuple) and child[0] == kind:
                flattened.extend(child[1])
            else:
                flattened.append(child)
        children = tuple(sorted(set(flattened), key=repr))
        return children[0] if len(children) == 1 else (kind, children)

    def expected(tree):
        return tree if isinstance(tree, int) else canonical(tree[0], [expected(c) for c in tree[1]])

    for group in (*groups, ('all', groups)):
        indexes = leaves(group)
        if len(indexes) < 2:
            continue
        def actual(node):
            if node.kind not in ('all', 'any') or exclusion(node) is not None:
                for i in indexes:
                    if predicate_supported(predicates[i], node, vocabulary, plan):
                        return i
            if node.kind not in ('all', 'any'):
                return None
            children = [c for p in node.children if (c := actual(p)) is not None]
            return canonical(node.kind, children) if children else None
        for measure in plan.measures:
            children = [c for root in (*plan.filters, *measure.filters) if (c := actual(root)) is not None]
            if not children:
                continue
            observed = canonical('all', children)
            present = set(leaves(observed))
            if present == set(indexes) and observed != expected(group):
                return ['Filter grouping disagrees with the question.']
    return []


def listed_values_supported(node, values, vocabulary):
    """An In node holds exactly the values the question's list names, each typed as its column is, in any order."""
    operand = node.left
    entry = vocabulary.entries.get(operand.name.id) if operand.kind == 'column' else None
    if entry is None or entry.kind != 'column' or not isinstance(values, tuple):
        return False

    def typed(value):
        scalar = typed_scalar(value, entry.column, vocabulary)
        if scalar is None:
            return None
        if scalar['kind'] in ('integer', 'decimal'):
            return ('number', Decimal(str(scalar['value'])))
        return (scalar['kind'], scalar['value'])
    expected = [typed(v) for v in values]
    actual = [typed(v.value) if v.kind != 'null' else None for v in node.values]
    if None in expected or None in actual or len(set(actual)) != len(actual):
        return False
    return set(expected) == set(actual) and len(expected) == len(actual)


def context_names(ctx, vocabulary):
    names = set(ctx.values)
    names.update(i for i, e in vocabulary.entities.items() if e['table'] == ctx.fact)
    for rule in ctx.filters:
        names.update(ctx.unit + '.' + c.table + '.' + c.name for c in sqlglot.parse_one(rule.predicate, read='duckdb').find_all(exp.Column))
    return names


def cohort_selectors(target, vocabulary):
    """The cohort contexts a cohort output is restricted to: the words naming them are applied by this output."""
    context = vocabulary.contexts.get(target.context.id)
    if context is None or not context.id.startswith('cohort:'):
        return set()
    return {rule.id for rule in context.selectors if rule.id.startswith('cohort:')}


def references(target, vocabulary):
    referenced = {n.id for n in names_in(target)}
    tables = {vocabulary.entries[i].column.rsplit('.', 1)[0] for i in referenced
              if i in vocabulary.entries and vocabulary.entries[i].kind == 'column'}
    paths = [j for ctx in vocabulary.contexts.values() for j in ctx.joins if j.id in referenced]
    tables.update(t for j in paths for t in (j.source, j.target))
    if getattr(target, 'kind', None) == 'membership':
        referenced.update(t.split('.')[0] for j in paths for t in (j.source, j.target))
    referenced.update(i for i, entity in vocabulary.entities.items() if entity['table'] in tables)
    if getattr(target, 'kind', None) in ('measure', 'distinct_count'):
        context = vocabulary.contexts.get(target.context.id)
        if context is not None:
            if target.kind == 'measure':
                referenced.update(i for i, d in vocabulary.derived_measures.items()
                                  if context.measure in (d['formula']['numerator'], d['formula']['denominator']))
            referenced.update((context.unit, context.measure))
            referenced.update(context_names(context, vocabulary))
            referenced.update(cohort_selectors(target, vocabulary))
            spec = vocabulary.measures.get(context.measure)
            if spec is not None and spec.population in vocabulary.entities:
                referenced.add(spec.population)
            if spec is not None and spec.currency and target.kind == 'measure':
                referenced.add(spec.currency)
    operand = getattr(target, 'left', None)
    if operand is not None and getattr(operand, 'kind', None) == 'column':
        column = vocabulary.entries.get(operand.name.id)
        values = getattr(target, 'values', (getattr(target, 'value', None),))
        referenced.update(e.id for e in vocabulary.entries.values() if e.kind == 'value' and column is not None and e.column == column.column
                          and any(v is not None and getattr(v, 'value', None) == e.value for v in values))
    return referenced


def referenced_targets(targets, vocabulary):
    referenced = {identifier for target in targets for identifier in references(target, vocabulary)}
    units = {i for i in referenced if i in vocabulary.entries and vocabulary.entries[i].kind == 'unit'}
    referenced.update(e.id for e in vocabulary.entries.values() if e.kind == 'source_set' and set(e.value) == units)
    return referenced


def operations(node, vocabulary=None, plan=None):
    result = {getattr(getattr(node, 'period', None), 'kind', ''), getattr(node, 'kind', ''), getattr(node, 'op', ''), getattr(node, 'mode', ''), getattr(node, 'grain', '')}
    if getattr(node, 'magnitude', False):
        result.discard(node.op)
        result.add('magnitude_' + node.op)
    if getattr(node, 'kind', '') == 'null' and not node.is_null:
        result.discard('null')
        result.add('not_null')
    if exclusion(node):
        result.discard('any')
        result.add('not_in')
    if plan is not None and getattr(node, 'kind', None) in ('not', 'in', 'null') and any(
            exclusion(root) is not None and node.id in {p.id for p in predicate_nodes(root)} for root in plan.nodes()
            if getattr(root, 'kind', None) == 'any'):
        result.discard(node.kind)
    if getattr(node, 'kind', '') in ('range', 'ytd'):
        result.update(('mtd', 'qtd'))
    if getattr(node, 'kind', '') == 'ytd':
        result.add('range')
    if getattr(node, 'kind', '') == 'distinct_count':
        result.add('count')
    if getattr(node, 'kind', '') == 'time_mode':
        result.update((node.base, node.other, 'comparison'))
    if getattr(node, 'kind', '') in ('previous_period', 'previous_year', 'explicit_period', 'contexts'):
        result.add('comparison')
    if getattr(node, 'kind', '') in ('measure', 'distinct_count'):
        result.add('aggregate')
        if vocabulary is not None:
            ctx = vocabulary.contexts.get(node.context.id)
            if ctx is not None and node.kind == 'measure':
                result.add(node.aggregation or ctx.aggregation)
            if ctx is not None and ctx.aggregation == 'count':
                result.add('count')
            if ctx is not None and any(s.id.startswith('cohort:') for s in ctx.selectors):
                result.add('membership')
            if ctx is not None and plan is not None:
                for other in plan.measures:
                    if ({node.kind, other.kind} != {'measure', 'distinct_count'}
                            or node.context.id != other.context.id or node.mode != other.mode or node.filters != other.filters):
                        continue
                    counted = node if node.kind == 'distinct_count' else other
                    entity = vocabulary.entities.get(counted.entity.id)
                    joined = {ctx.fact} | {join.target for join in ctx.joins if not join.through and not join.alias}
                    if entity is not None and entity['table'] in joined:
                        result.add('membership')
    return result


def shape_contexts(kind, pieces, text, vocabulary):
    """Bind a unary shape to its adjacent measure list or its named suffix operand."""
    operators = [p for p in pieces if p.operation == kind]
    if not operators:
        return set()
    def contexts(piece):
        declared = set(piece.candidates) & vocabulary.contexts.keys()
        return declared or {c.id for c in vocabulary.contexts.values() if c.measure in piece.candidates}
    heads = [p for p in pieces if p.kind == 'name' and contexts(p)]
    selected = set()
    for operator in operators:
        if bound := contexts(operator):
            selected.update(bound)
            continue
        after = [p for p in heads if p.span.start >= operator.span.end]
        before = [p for p in heads if p.span.end <= operator.span.start]
        if after and re.fullmatch(r'\s*(?:of\s+)?(?:the\s+)?', text[operator.span.end:after[0].span.start], re.I):
            group = [after[0]]
            for left, right in zip(after, after[1:]):
                if not re.fullmatch(r'\s*(?:,\s*(?:and\s*)?|and\s+)', text[left.span.end:right.span.start], re.I):
                    break
                group.append(right)
        elif before and re.fullmatch(r'\s*(?:as\s+(?:a\s+)?)?', text[before[-1].span.end:operator.span.start], re.I):
            group = [before[-1]]
            for right, left in zip(reversed(before), reversed(before[:-1])):
                if not re.fullmatch(r'\s*(?:,\s*(?:and\s*)?|and\s+)', text[left.span.end:right.span.start], re.I):
                    break
                group.append(left)
        elif any(contexts(p) for p in operators):
            continue
        else:
            group = heads
        selected.update(c for p in group for c in contexts(p))
    return selected


def shape_inputs(kind, pieces, text, vocabulary, measures):
    """A named context selects its measure output, keeping counts on count-only contexts.

    Each candidate is an output id, context id and measure kind. An accompanying
    entity count shares the measure's context, not its value or unary operation.
    """
    requested = shape_contexts(kind, pieces, text, vocabulary)
    primary = {context for _, context, measure_kind in measures if measure_kind == 'measure'}
    return [identifier for identifier, context, measure_kind in measures
            if (not requested or context in requested) and (measure_kind == 'measure' or context not in primary)]


def ranked_outputs(directions, pieces, text, vocabulary, outputs):
    """The outputs a ranking may order by. An accompanying entity count rides along with the amount it shares a context
    with; a count the ranking names before any amount (top groups by number of entities, with the amount) is the ranked
    one. Word order is read from the ranking operator on, so a count asked before the ranking (how many entities ..., top
    5 by the amount) does not take the ranking from the amount; 'which' asks for the ranked rows and names no count. A
    count the ranking names is one it is by ('top 5 by number of entities'): a count that only follows the ranking (top 1,
    count of entities) rides along."""
    inputs = {i for direction in directions for i in shape_inputs(direction, pieces, text, vocabulary, outputs)}
    counts = {i for i, context, kind in outputs if kind == 'distinct_count'
              and any(k == 'measure' and c == context for _, c, k in outputs)}
    ranks = [p.span.start for p in pieces if p.operation in directions]
    if not counts or not ranks:
        return inputs
    anchor = min(ranks)
    heads = [p.span.start for p in pieces if p.kind == 'name' and p.span.start > anchor
             and (set(p.candidates) & vocabulary.contexts.keys()
                  or any(c.measure in p.candidates for c in vocabulary.contexts.values()))]
    named = [p.span.start for p in pieces if p.operation == 'count' and p.span.start > anchor and p.text.casefold() != 'which'
             and re.search(r'\bby\b', text[anchor:p.span.end], re.I)]
    if named and (not heads or min(named) < min(heads)):
        return counts
    return inputs


def unqualified_contexts(piece, vocabulary):
    """Resolve a compound name's noun through declared aliases, including a separated qualifier."""
    selected = set(piece.candidates) & vocabulary.contexts.keys()
    labels = [piece.text, *(phrase for phrase, ids in vocabulary.phrases.items() if selected.intersection(ids))]
    contexts = set()
    for label in labels:
        for split in re.finditer(r'\s+', label):
            candidates = {entry.id for entry in vocabulary.candidates(label[split.end():])}
            contexts.update(context.id for context in vocabulary.contexts.values()
                            if context.id not in selected and (context.id in candidates or context.measure in candidates)
                            and context.term.casefold() == vocabulary.entries[context.measure].label.casefold())
    return contexts


def removal_spans(plan, text, turn, vocabulary, removed, available=None):
    spans = []
    for obligation in plan.obligations:
        if obligation.state != 'removed':
            continue
        if not obligation.targets or not set(obligation.targets) <= set(removed):
            raise ValueError('Deletion requires nodes from the checked conversation base.')
        for span in obligation.spans:
            if span.turn != turn or span.end > len(text):
                raise ValueError('Deletion requires current follow-up evidence.')
            if any(s.start < span.end and span.start < s.end for s in spans):
                raise ValueError('Deletion evidence overlaps another obligation.')
            phrase = text[span.start:span.end].strip().casefold()
            targets = [removed[t] for t in obligation.targets if not t.endswith('/mode')]
            if not targets:
                raise ValueError('Deletion must name a removed node.')
            if re.fullmatch(r'without (?:the )?filter', phrase):
                predicates = [t for t in targets if getattr(t, 'kind', '') in ('compare', 'in', 'null', 'not', 'all', 'any', 'membership')]
                nested = {p.id for t in predicates for p in predicate_nodes(t) if p.id != t.id}
                roots = {t.id for t in predicates} - nested
                choices = [t for t in (available or removed).values() if getattr(t, 'kind', '') in ('compare', 'in', 'null', 'not', 'all', 'any', 'membership')]
                choice_nested = {p.id for t in choices for p in predicate_nodes(t) if p.id != t.id}
                valid = (len(predicates) == len(targets) and len(roots) == 1
                         and {t.id for t in choices} - choice_nested == roots)
            elif re.fullmatch(r'just (?:the )?total', phrase):
                dimensions = {i for i, t in (available or removed).items() if getattr(t, 'kind', '') in ('column', 'hierarchy', 'band', 'time')}
                valid = all(getattr(t, 'kind', '') in ('column', 'hierarchy', 'band', 'time') for t in targets) and dimensions == {t.id for t in targets}
            else:
                match = re.fullmatch(r'drop (?:the )?(.+?)(?: breakdown)?', phrase)
                named = {e.id for e in vocabulary.candidates(match[1])} if match else set()
                if match and all(getattr(target, 'kind', '') in ('column', 'hierarchy') for target in targets):
                    named.update(e.id for e in vocabulary.candidates('by ' + match[1]) if e.kind == 'column')
                valid = bool(named) and all(named & referenced_targets([t], vocabulary) for t in targets)
                kinds = {getattr(t, 'kind', '') for t in targets}
                matches = {i for i, t in (available or removed).items() if getattr(t, 'kind', '') in kinds
                           and named & referenced_targets([t], vocabulary)}
                valid = valid and matches == {t.id for t in targets}
            if not valid:
                raise ValueError('Deletion evidence does not identify the removed nodes.')
            spans.append(span)
    return tuple(spans)


def remaining_pieces(pieces, spans):
    return tuple(p for p in pieces if not any(s.turn == p.span.turn and s.start <= p.span.start and p.span.end <= s.end for s in spans))


def calendar_binding(kind, pieces, vocabulary):
    """Use lexical sources and declaration order, never a proposed calendar or context."""
    named = {identifier for p in pieces if p.kind == 'name' for identifier in p.candidates}
    named_contexts = named & vocabulary.contexts.keys()
    units = {identifier for identifier in named if vocabulary.entries[identifier].kind == 'unit'}
    units = units or {unit for identifier in named if vocabulary.entries[identifier].kind == 'source_set'
                      for unit in vocabulary.entries[identifier].value}
    units = units or set(vocabulary.default_units) or {product.split('.')[0] for product in vocabulary.binding.products}
    contexts = [context for identifier, context in vocabulary.contexts.items()
                if (identifier in named_contexts or not named_contexts and context.measure in named)
                and (not units or context.unit in units)]
    ytd = [p for p in pieces if p.operation in ('ytd', 'mtd', 'qtd')]
    if kind.startswith('fiscal_') or any(p.text.casefold().startswith('fiscal') for p in ytd):
        calendars = {identifier for identifier, calendar in vocabulary.calendars.items()
                     if calendar['quarter_label'].startswith('FY')}
    elif any(p.text.casefold().startswith('calendar') for p in ytd):
        calendars = [identifier for identifier, calendar in vocabulary.calendars.items() if calendar['year_start_month'] == 1]
    else:
        calendars = list(dict.fromkeys(context.ytd_calendar for context in contexts if ytd and context.ytd_calendar))
        calendars = calendars or list(dict.fromkeys(vocabulary.periods[context.fact]['calendar'] for context in contexts
                                                    if context.fact in vocabulary.periods))
        if calendars:
            calendars = calendars[:1]
        if not calendars:
            calendars = list(vocabulary.calendars)[:1]
    if len(calendars) != 1:
        raise ValueError('The requested period has no unique declared calendar binding.')
    return next(iter(calendars))


def period_binding(piece, pieces, vocabulary):
    """Bind a lexical period to declared calendars independently of any proposed selection."""
    from kge.semantics.plan import Period
    from kge.services.ask.engine.question import read_period
    period, problem = (piece.value, None) if piece.value is not None else read_period(piece.text.casefold())
    if problem or period is None or period.plan_kind is None:
        raise ValueError('The requested period has no declared calendar binding.')
    return Period(kind=period.plan_kind, value=period.value[:7] if period.plan_kind == 'month' else period.value,
                  calendar=Name(id=calendar_binding(period.plan_kind, pieces, vocabulary), evidence=(piece.span,)))


def grouping_calendar(grain, pieces, vocabulary):
    """Fiscal breakdowns name their own calendar; absolute breakdowns inherit the stated window's calendar."""
    times = [piece for piece in pieces if piece.kind == 'time']
    return (period_binding(times[0], pieces, vocabulary).calendar.id if times and not grain.startswith('fiscal_')
            else calendar_binding(grain, pieces, vocabulary))


def canonical_calendars(plan, pieces, vocabulary):
    """Ground equivalent absolute windows to the ledger without changing fiscal or YTD selections."""
    from kge.semantics.planperiods import bounds, time_calendar
    absolute = ('day', 'month', 'calendar_quarter', 'year')

    def period(value):
        if value.kind not in absolute or value.calendar.id not in vocabulary.calendars:
            return value
        try:
            identifier = calendar_binding(value.kind, pieces, vocabulary)
            grounded = value.model_copy(update={'calendar': value.calendar.model_copy(update={'id': identifier})})
            return grounded if bounds(value, vocabulary.calendars) == bounds(grounded, vocabulary.calendars) else value
        except (ValueError, KeyError):
            return value

    def timing(value):
        if value.kind == 'single':
            return value.model_copy(update={'period': period(value.period)})
        if value.kind == 'range':
            return value.model_copy(update={'first': period(value.first), 'last': period(value.last)})
        return value

    time = timing(plan.time)
    before, after = time_calendar(plan.time), time_calendar(time)
    return plan.model_copy(update={
        'time': time,
        'comparisons': tuple(c.model_copy(update={'other': timing(c.other)}) if c.kind == 'explicit_period' else c
                             for c in plan.comparisons),
        'dimensions': tuple(d.model_copy(update={'calendar': d.calendar.model_copy(update={'id': after})})
                            if d.kind == 'time' and d.grain in absolute and d.calendar.id == before and before != after
                            else d for d in plan.dimensions)})


def cutoff_count(piece, pieces, vocabulary):
    """An as-at count of dated events includes earlier events on the declared event-date binding."""
    if not piece.cutoff or piece.window:
        return False
    named = {i for p in pieces if p.kind in ('name', 'predicate') for i in p.candidates}
    contexts = [c for c in vocabulary.contexts.values() if c.id in named or c.measure in named]
    if not any(p.operation == 'count' for p in pieces) and not (contexts and all(c.aggregation == 'count' for c in contexts)):
        return False
    tables = {vocabulary.entries[i].column.rsplit('.', 1)[0] for i in named if vocabulary.entries[i].column}
    tables.update(c.fact for c in contexts)
    tables.update(vocabulary.entities[i]['table'] for i in named if i in vocabulary.entities)
    return bool(tables) and all(vocabulary.periods.get(table, {}).get('kind') == 'event_date' for table in tables)


def time_matches(piece, selection, pieces, vocabulary):
    from kge.semantics.planperiods import bounds, time_bounds, time_calendar
    expected_period = period_binding(piece, pieces, vocabulary)
    if expected_period.calendar.id != time_calendar(selection):
        return False
    actual = time_bounds(selection, vocabulary.calendars)
    if any(p.operation in ('ytd', 'mtd', 'qtd') for p in pieces):
        requested = to_date_selection(pieces, vocabulary)
        return actual == time_bounds(requested, vocabulary.calendars)
    spans = piece.window or (piece.span,)
    selected = sorted((p for p in pieces if p.span in spans), key=lambda p: p.span.start)
    if {p.span for p in selected} != set(spans):
        return False
    periods = [period_binding(p, pieces, vocabulary) for p in selected]
    if any(p.calendar.id != expected_period.calendar.id for p in periods):
        return False
    windows = [bounds(p, vocabulary.calendars) for p in periods]
    if expected_period.kind == 'day' and cutoff_count(piece, pieces, vocabulary):
        windows[0] = date.min, windows[0][1]
    return (windows[0][0], windows[-1][1]) == actual


YEAR_PERIOD_KINDS = ('year', 'fiscal_year')
INHERITED_UNAPPLIED = 'A word from an earlier question is no longer applied.'


def stated_to_date_ambiguity(pieces, text=None):
    """A bare "to date" straight after a stated period shorter than a year ("June 2025 to date", "Q2 2025 to date",
    "June 2025, to date", "June 2025 to date by month") reads as the year to date through that period or as that period
    until now, so it names both readings and gives no figure; "YTD", "year to date" and a stated year keep their one
    reading. Only a space or a comma may stand between the period and "to date"."""
    from kge.services.ask.engine.question import read_period

    def adjacent(point, operator):
        if 0 <= operator.span.start - point.span.end <= 1:
            return True
        return (text is not None and point.span.end <= operator.span.start
                and text[operator.span.start:operator.span.end].casefold() == 'to date'
                and re.fullmatch(r'\s*,\s*', text[point.span.end:operator.span.start]) is not None)
    for operator in pieces:
        if (operator.kind != 'operator' or operator.text.casefold() != 'to date'
                or operator.operation not in ('ytd', 'running_total')):
            continue
        points = [p for p in pieces if p.kind == 'time' and p.span.turn == operator.span.turn and adjacent(p, operator)]
        if len(points) != 1:
            continue
        anchor = points[0].value or read_period(points[0].text)[0]
        if anchor is not None and anchor.plan_kind not in YEAR_PERIOD_KINDS:
            stated = points[0].text
            return (f"'{stated} to date' can mean the year to date through {stated} or the period from {stated} until now. "
                    'Please say which one you mean. No figure is given.')
    return None


def to_date_selection(pieces, vocabulary, text=None):
    """Resolve an explicit cutoff or the calendar's declared current period, independently of a proposed plan."""
    from kge.semantics.plan import Period
    from kge.semantics.planperiods import to_date_window
    operators = [p for p in pieces if p.operation in ('ytd', 'mtd', 'qtd')]
    if len(operators) != 1:
        raise ValueError('Please identify one to-date window.')
    points = [p for p in pieces if p.kind == 'time']
    if len(points) > 1:
        raise ValueError('A to-date window needs one stated cutoff period.')
    def cutoff_literal(piece):
        if text is None or piece.kind != 'literal' or piece.span.start < operators[0].span.end:
            return False
        prefix = re.sub(r'\bthe\b|[^\w\s]', ' ', text[operators[0].span.end:piece.span.start], flags=re.I)
        return bool(re.fullmatch(r'\s*(?:(?:through|to|as\s+(?:at|of)|for|in|during)\s*)?', prefix, re.I))
    if not points and text is not None and (re.match(r'\s+(?:through|to|as\s+(?:at|of))\b',
            text[operators[0].span.end:], re.I) or any(cutoff_literal(p) for p in pieces)):
        raise ValueError('A to-date window needs a valid stated cutoff period.')
    from kge.services.ask.engine.question import read_period
    anchor = (points[0].value or read_period(points[0].text)[0]) if points else None
    if reason := stated_to_date_ambiguity(pieces, text):
        raise ValueError(reason)
    if anchor and anchor.plan_kind.startswith('fiscal_') and operators[0].text.casefold().startswith('calendar'):
        raise ValueError('A calendar to-date window needs a calendar cutoff period.')
    calendar = calendar_binding(anchor.plan_kind if anchor else 'day', pieces, vocabulary)
    cal = vocabulary.calendars[calendar]
    if points:
        through = period_binding(points[0], pieces, vocabulary).model_copy(update={'calendar': Name(id=calendar, evidence=())})
    else:
        current = cal.get('current_period')
        if not current:
            raise ValueError('A to-date window needs a stated period or a declared current period.')
        through = Period(kind='day', value=current, calendar=Name(id=calendar, evidence=()))
    return to_date_window(operators[0].operation, through, vocabulary.calendars, 'stated' if points else 'current')


def ytd_problem(plan, text, vocabulary):
    """A to-date selection must equal the independently grounded cutoff and calendar."""
    from kge.semantics.planperiods import time_bounds, time_calendar, to_date_operation
    pieces = ledger(text, 'period-check', vocabulary)
    requested = any(p.operation in ('ytd', 'mtd', 'qtd') for p in pieces)
    if not requested:
        if to_date_operation(plan.time):
            return 'unrequested current to-date window'
        return 'unrequested year to date' if plan.time.kind == 'ytd' else None
    try:
        expected = to_date_selection(pieces, vocabulary, text)
        if to_date_operation(plan.time) != to_date_operation(expected):
            return 'To-date window disagrees with its stated cutoff or the current period.'
        if time_calendar(plan.time) != time_calendar(expected):
            return 'To-date window disagrees with its declared calendar or cutoff.'
        if time_bounds(plan.time, vocabulary.calendars) != time_bounds(expected, vocabulary.calendars):
            points = [p for p in pieces if p.kind == 'time']
            return points[0].text if len(points) == 1 else 'To-date window disagrees with its declared calendar or cutoff.'
    except (ValueError, KeyError) as exc:
        return str(exc)
    return None


def grouping_grains(pieces, text, turn=None):
    grains = {p.value for p in pieces if p.operation == 'dimension' and p.value in get_args(Grain)}
    grains.update(p.value[1] for p in pieces if p.operation == 'dimension' and isinstance(p.value, tuple)
                  and len(p.value) == 2 and p.value[0] == 'rank_partition' and p.value[1] in get_args(Grain))
    ordered = sorted(pieces, key=lambda p: (p.span.turn, p.span.start))
    for previous, piece in zip(ordered, ordered[1:]):
        if previous.span.turn != piece.span.turn or piece.kind != 'operator' or piece.operation not in get_args(Grain):
            continue
        between = text[previous.span.end:piece.span.start] if turn is None or piece.span.turn == turn else ''
        if (previous.operation == 'dimension' and not between.strip()
                or previous.operation in grains and (turn is not None and piece.span.turn != turn
                    or re.fullmatch(r'\s*(?:and|,)\s*', between))):
            grains.add(piece.operation)
    return grains


def coverage(plan, text, turn, vocabulary, inherited=(), removed=None, implicit_measures=()):
    spans = removal_spans(plan, text, turn, vocabulary, removed or {})
    pieces = (*remaining_pieces(ledger(text, turn, vocabulary), spans), *inherited)
    named_contexts = {c for p in pieces if p.kind == 'name' for c in p.candidates if c in vocabulary.contexts}
    nodes = {n.id: n for n in plan.nodes()}
    nodes.update({'time': plan.time, 'limit': plan.limit})
    nodes.update({m.id + '/mode': m for m in plan.measures})
    filters = {node.id for root in (*plan.filters, *(f for m in plan.measures for f in m.filters)) for node in predicate_nodes(root)}
    problems, claimed = [], set()
    if any(p.kind == 'operator' and p.operation == 'share' and p.text.casefold() in SHARE_COMPLEMENTS and p.value != 'split' for p in pieces):
        problems.append('The remaining share needs an explicit complement numerator.')
    times = [piece for piece in pieces if piece.kind == 'time']
    grains = grouping_grains(pieces, text, turn)
    from kge.semantics.planperiods import time_calendar
    selected_calendars = {time_calendar(plan.time)} | {
        time_calendar(comparison.other) for comparison in plan.comparisons if comparison.kind == 'explicit_period'}
    for piece in times:
        try:
            if period_binding(piece, pieces, vocabulary).calendar.id not in selected_calendars:
                problems.append('Calendar binding disagrees with the question: ' + piece.text)
        except ValueError:
            pass
    for dimension in plan.dimensions:
        if dimension.kind == 'time':
            if dimension.grain not in grains:
                problems.append('Time grouping grain disagrees with the question.')
            try:
                calendar = grouping_calendar(dimension.grain, pieces, vocabulary)
                if dimension.calendar.id != calendar:
                    problems.append('Time grouping calendar disagrees with the question.')
            except ValueError as exc:
                problems.append(str(exc))
    for o in plan.obligations:
        if o.state == 'removed':
            continue
        if o.state != 'applied' or not o.targets:
            problems.extend(text[s.start:s.end] for s in o.spans if s.turn == turn)
            problems.extend(next((p.text for p in pieces if p.span == s), INHERITED_UNAPPLIED) for s in o.spans if s.turn != turn)
            continue
        for span in o.spans:
            matches = [p for p in pieces if p.span == span]
            if len(matches) != 1 or span in claimed:
                problems.append('invalid or overlapping evidence')
                continue
            p = matches[0]
            targets = [nodes[t] for t in o.targets if t in nodes]
            if p.span.turn == turn and ((p.kind == 'predicate' and separate_filter_parts(p, pieces, text)) or p.operation == 'aggregate'):
                heads = [head for head in pieces if head.span.turn == p.span.turn and head.span.end <= p.span.start
                         and head.kind == 'name' and set(head.candidates) & vocabulary.contexts.keys()]
                if heads:
                    contexts = ({c for c, owned in output_aggregations(pieces, text, vocabulary).items() if 'sum' in owned}
                                if p.operation == 'aggregate' else set())
                    contexts = contexts or set(max(heads, key=lambda head: head.span.end).candidates) & vocabulary.contexts.keys()
                    outputs = [m for m in plan.measures if m.id not in implicit_measures and (m in targets or any(
                        node.id in o.targets for root in m.filters for node in predicate_nodes(root)))]
                    if any(m.context.id not in contexts for m in outputs):
                        problems.append('every measure part must use its named context')
            if p.kind == 'unknown':
                problems.append(p.text)
            elif p.kind == 'name':
                referenced = referenced_targets(targets, vocabulary)
                measures = [t for t in targets if getattr(t, 'kind', None) in ('measure', 'distinct_count')]
                if (not referenced.intersection(p.candidates) or any(
                        set(p.candidates) & vocabulary.contexts.keys() and t.context.id not in named_contexts
                        for t in measures)):
                    problems.append(p.text)
                elif {'context', 'unit'} <= {vocabulary.entries[i].kind for i in p.candidates}:
                    problems.append(p.text)
                elif len(p.candidates) > 1 and not one_meaning(referenced.intersection(p.candidates), vocabulary):
                    problems.append(p.text)
            elif p.kind == 'predicate':
                part = p.filter_part if p.span.turn != turn else separate_filter_parts(p, pieces, text)
                matching = [t for t in targets if predicate_supported(p, t, vocabulary, plan)
                            and (part or getattr(t, 'kind', None) != 'measure')]
                if not matching:
                    problems.append(p.text)
            elif p.kind == 'time':
                selections = [t for t in targets if getattr(t, 'kind', None) in ('single', 'range', 'ytd')]
                selections += [t.other for t in targets if getattr(t, 'kind', None) == 'explicit_period']
                valid = False
                for selection in selections:
                    try:
                        matched = time_matches(p, selection, pieces, vocabulary)
                    except (ValueError, KeyError):
                        matched = False
                    if selection.kind == 'ytd':
                        requested_ytd = any(piece.operation == 'ytd' and any(
                            obligation.state == 'applied' and piece.span in obligation.spans
                            and any(nodes.get(target) == selection for target in obligation.targets)
                            for obligation in plan.obligations) for piece in pieces)
                        matched &= requested_ytd
                    valid |= matched
                if not valid:
                    problems.append(p.text)
            elif p.kind == 'operator':
                legal = {op for target in targets for op in operations(target, vocabulary, plan)}
                if p.operation == 'comparison' and isinstance(p.value, tuple) and set(p.value) <= {'difference', 'percent_change'}:
                    comparisons = [target for target in targets if target in plan.comparisons]
                    pairs = [pair for comparison in comparisons for pair in (
                        [(comparison.id + '/base', comparison.id + '/other')] if comparison.kind == 'contexts' else
                        [(comparison.id + '/' + identifier + '/base', comparison.id + '/' + identifier + '/other') for identifier in comparison.inputs])]
                    if not pairs or any(not any(derived.kind == kind and (derived.numerator, derived.denominator) == pair
                                                for derived in plan.derived if derived.kind in ('difference', 'percent_change'))
                                        for pair in pairs for kind in p.value):
                        problems.append('comparison requires ' + ' and '.join('dollar difference' if kind == 'difference' else 'percent change' for kind in p.value))
                if p.operation in ('running_total', 'moving_average') and p.candidates and not any(
                        getattr(target, 'kind', None) == p.operation
                        and getattr(getattr(nodes.get(target.input), 'context', None), 'id', None) in p.candidates for target in targets):
                    legal.discard(p.operation)
                if any(getattr(target, 'kind', None) == 'ytd' for target in targets):
                    legal.add('range')
                if p.operation == 'dimension':
                    legal |= {'dimension'} if any(t in {d.id for d in plan.dimensions} for t in o.targets) else set()
                if p.operation in ('asc', 'desc'):
                    legal |= {direction for order in plan.order if order.output in o.targets
                              for direction in (('asc', 'desc') if order.direction == 'both' else (order.direction,))}
                if p.operation == 'separate' and plan.layout == 'separate':
                    legal.add('separate')
                if p.operation not in legal:
                    problems.append(p.text)
            else:
                if not literal_supported(p, targets):
                    problems.append(p.text)
            if p.span.turn == turn and (matching := filters.intersection(o.targets)) and not separate_filter_parts(p, pieces, text):
                columns = {vocabulary.entries[c].column for identifier in matching for c in references(nodes[identifier], vocabulary)
                           if c in vocabulary.entries and vocabulary.entries[c].kind == 'column'}
                for measure in plan.measures:
                    ctx = vocabulary.contexts[measure.context.id]
                    reachable = {ctx.fact} | {join.target for join in ctx.joins if not join.through}
                    applicable = any(column.rsplit('.', 1)[0] in reachable for column in columns)
                    scoped = filter_scope(p, pieces, text, vocabulary)
                    if not applicable or (scoped and not scoped & references(measure, vocabulary)):
                        continue
                    predicates = {node.id for root in (*plan.filters, *measure.filters) for node in predicate_nodes(root)}
                    if not any(predicate_meaning(nodes[a].model_dump(mode='json')) == predicate_meaning(nodes[b].model_dump(mode='json'))
                               for a in predicates for b in matching):
                        problems.append('named filter must apply to every applicable retained measure and count')
            claimed.add(span)
    problems.extend(p.text for p in pieces if p.span not in claimed)
    covered = {t for o in plan.obligations if o.state == 'applied' for t in o.targets} | set(implicit_measures)
    def owns(identifier, operations):
        return any(p.operation in operations and any(identifier in o.targets and p.span in o.spans and o.state == 'applied' for o in plan.obligations) for p in pieces)
    for node in (*plan.comparisons, *plan.derived):
        allowed = {'comparison', node.kind}
        if node.kind in ('difference', 'percent_change'):
            allowed |= {'previous_period', 'previous_year'}
        declared = getattr(node, 'declaration', None)
        named_formula = declared is not None and any(declared.id in p.candidates and any(
            node.id in o.targets and p.span in o.spans and o.state == 'applied' for o in plan.obligations) for p in pieces)
        if not owns(node.id, allowed) and not named_formula:
            problems.append('unrequested operation ' + node.id)
    for dimension in plan.dimensions:
        if not owns(dimension.id, {'dimension'}):
            problems.append('unrequested dimension ' + dimension.id)
    for order in plan.order:
        directions = ('asc', 'desc') if order.direction == 'both' else (order.direction,)
        if not all(owns(order.output, {direction}) for direction in directions):
            problems.append('unrequested ordering ' + order.output)
        current = [p for p in pieces if p.span.turn == turn]
        if order.output in {m.id for m in plan.measures} and any(p.operation in directions for p in current):
            outputs = [(m.id, m.context.id, m.kind) for m in plan.measures]
            if order.output not in ranked_outputs(directions, current, text, vocabulary, outputs):
                problems.append('ordering must use the requested measure, not its accompanying count'
                                if next(kind for i, _, kind in outputs if i == order.output) == 'distinct_count'
                                else 'ordering must use the count the ranking names, not the amount')
    for measure in plan.measures:
        if not problems and named_contexts and measure.kind == 'measure' and measure.id not in implicit_measures and not any(p.kind == 'name' and (
                measure.context.id in p.candidates or (measure.measure.id in p.candidates
                    and not set(p.candidates) & vocabulary.contexts.keys())) for p in pieces):
            problems.append('every measure part must use its named context')
        named_count = (measure.kind == 'distinct_count' and getattr(measure, 'declaration', None) is not None
                       and any(measure.declaration.id in p.candidates for p in pieces))
        if measure.kind == 'distinct_count' and measure.id not in implicit_measures and not owns(measure.id, {'count'}) and not named_count:
            problems.append('unrequested count ' + measure.id)
        if measure.mode != 'current' and not (owns(measure.id, {measure.mode}) or owns(measure.id + '/mode', {measure.mode})):
            problems.append('unrequested mode')
    for pred in (*plan.filters, *(p for m in plan.measures for p in m.filters)):
        for node in predicate_nodes(pred):
            if node.id in implicit_measures:
                continue
            owned = [p for p in pieces for o in plan.obligations if node.id in o.targets and p.span in o.spans]
            required = 'not_null' if node.kind == 'null' and not node.is_null else getattr(node, 'op', node.kind)
            if not any((predicate_supported(p, node, vocabulary, plan)
                        and (node.kind != 'any' or exclusion(node) is not None)) if p.kind == 'predicate'
                       else p.operation == required for p in owned):
                problems.append('unrequested predicate ' + node.id)
    problems.extend(filter_group_problems(plan, pieces, text, vocabulary))
    for identifier in sorted({n.id for n in plan.nodes()} - covered):
        problems.append('unrequested node ' + identifier)
    if plan.limit is not None and 'limit' not in covered:
        problems.append('unrequested limit')
    has_partitioned = any(
        vocabulary.contexts.get(m.context.id) is not None
        and vocabulary.contexts[m.context.id].fact in (getattr(vocabulary.semantics, 'partitioned', None) or {})
        for m in plan.measures
    )
    if plan.time.kind != 'all_available' and 'time' not in covered and not has_partitioned:
        problems.append('unrequested time')
    if problem := ytd_problem(plan, text, vocabulary):
        problems.append(problem)
    if reason := stated_to_date_ambiguity(pieces, text):
        problems.append(reason)
    if plan.time.kind == 'ytd' and not owns('time', {'ytd'}):
        problems.append('unrequested year to date')
    for m in plan.measures:
        if m.mode != 'current' and m.id + '/mode' not in covered and m.id not in covered:
            problems.append('unrequested mode')
    if plan.layout == 'separate' and not any(p.operation == 'separate' for p in pieces):
        problems.append('unrequested separate figures')
    return tuple(dict.fromkeys(problems))


def filter_scope(piece, pieces, text, vocabulary):
    """Possessives, explicit bindings and coordinated measure clauses narrow filter scope."""
    def binding(head):
        return set(head.candidates) & vocabulary.contexts.keys() or set(head.candidates)
    names = sorted((p for p in pieces if p.span.turn == piece.span.turn and p.kind == 'name'
                    and any(vocabulary.entries[i].kind in ('context', 'measure') for i in p.candidates)),
                   key=lambda p: p.span.start)
    following = next((p for p in names if p.span.start >= piece.span.end), None)
    if separate_filter_parts(piece, pieces, text):
        if following and re.fullmatch(r'\s+(?:of\s+)?', text[piece.span.end:following.span.start], re.I):
            return binding(following)
        preceding = [p for p in names if p.span.end <= piece.span.start]
        if preceding:
            return binding(preceding[-1])
    modifier = any(r['kind'] == 'predicate' and r['params'].get('modifier')
                   and r['params']['column'] in piece.candidates
                   and piece.text.casefold() in {p.casefold() for p in r['params']['phrases']}
                   for r in getattr(vocabulary.semantics.d, 'VOCABULARY', ()))
    if following and re.fullmatch(r"['’]s\s*", text[piece.span.end:following.span.start], re.I):
        scope, left = binding(following), following
        for right in names[names.index(following) + 1:]:
            if not re.fullmatch(r'\s*(?:,\s*(?:and\s+)?|and\s+)', text[left.span.end:right.span.start], re.I):
                break
            scope, left = scope | binding(right), right
        return scope
    if following and (re.fullmatch(r"\s+for\s+(?:the\s+)?measure\s+", text[piece.span.end:following.span.start], re.I)
                      or (modifier and text[piece.span.end:following.span.start].isspace())):
        return binding(following)
    if (len(names) == 1 and names[0].span.end <= piece.span.start
            and re.search(r'\bjust\s*$', text[:names[0].span.start], re.I)):
        return binding(names[0])
    for left, right in zip(names, names[1:]):
        between = text[left.span.end:right.span.start]
        separator = re.search(r'\band\b|[,;]', between, re.I)
        if separator is None:
            continue
        boundary = left.span.end + separator.start()
        local = [p for p in pieces if p.span.turn == piece.span.turn and p.kind == 'predicate'
                 and left.span.end <= p.span.start and p.span.end <= boundary]
        if piece in local:
            return binding(left)
        if local and right.span.end <= piece.span.start and re.fullmatch(
                r'\s*(?:for\s+)?', text[right.span.end:piece.span.start], re.I):
            return binding(right)
    preceding = [p for p in names if p.span.end <= piece.span.start
                 and re.search(r'\bfor\s+(?:the\s+)?measure\s*$', text[:p.span.start], re.I)]
    return binding(preceding[-1]) if preceding else set()


def predicate_meaning(value):
    if isinstance(value, dict):
        return {k: predicate_meaning(v) for k, v in value.items() if k != 'evidence' and not (k == 'id' and 'kind' in value)}
    if isinstance(value, list):
        return [predicate_meaning(v) for v in value]
    return value


def literal_supported(piece, targets):
    def literals(value):
        if isinstance(value, Record):
            for field in type(value).model_fields:
                if field not in ('evidence', 'obligations', 'id'):
                    yield from literals(getattr(value, field))
        elif isinstance(value, tuple):
            for v in value:
                yield from literals(v)
        elif value is not None:
            yield str(value)
    def numbers(value):
        if isinstance(value, (DecimalValue, Integer)):
            yield Decimal(value.value)
        elif isinstance(value, Record) and not isinstance(value, Text):
            for field in type(value).model_fields:
                if field not in ('evidence', 'obligations', 'id'):
                    yield from numbers(getattr(value, field))
        elif isinstance(value, tuple):
            for child in value:
                yield from numbers(child)
        elif isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            yield Decimal(value)
    values = {value for target in targets for value in literals(target)}
    numeric = piece.value if piece.value is not None else piece.text if re.fullmatch(r'-?\d+(?:\.\d+)?', piece.text) else None
    return ((piece.value is None and piece.text in values) or (numeric is not None and any(
        value == Decimal(numeric) for target in targets for value in numbers(target))))


def ground_plan(plan, text, turn, vocabulary, *, rules_floor=False):
    """Selections remain untrusted; their certificates come from the rules planner's ledger."""
    pieces = remaining_pieces(ledger(text, turn, vocabulary),
        tuple(s for o in plan.obligations if o.state == 'removed' for s in o.spans))
    plan = canonical_calendars(plan, pieces, vocabulary)
    requested_aggregations = output_aggregations(pieces, text, vocabulary)
    named_contexts = {c for p in pieces if p.kind == 'name' for c in p.candidates if c in vocabulary.contexts}
    named_formulas = {i for p in pieces for i in p.candidates if i in vocabulary.derived_measures}
    formula_inputs = {i for d in plan.derived if getattr(d, 'declaration', None) is not None
                      and d.declaration.id in named_formulas for i in ((d.input,) if d.kind == 'share' else (d.numerator, d.denominator))}
    if named_contexts and not plan.comparisons and any(m.kind == 'measure' and m.id not in formula_inputs and not any(
            p.kind == 'name' and (m.context.id in p.candidates or (m.measure.id in p.candidates
                and not set(p.candidates) & vocabulary.contexts.keys())) for p in pieces) for m in plan.measures):
        return plan
    protected = {identifier for derived in plan.derived if derived.kind == 'share' for identifier in derived.remove_filters}

    def required(predicate, contexts):
        if (predicate.id in protected or predicate.kind != 'compare' or predicate.op != 'eq'
                or predicate.left.kind != 'column' or predicate.left.path or any(
                    predicate.id in o.targets and any(s.turn != turn for s in o.spans) for o in plan.obligations)):
            return False
        entry = vocabulary.entries.get(predicate.left.name.id)
        if entry is None or entry.kind != 'column' or any(
                piece.kind in ('name', 'predicate') and entry.id in piece.candidates for piece in pieces):
            return False
        table, column = entry.column.rsplit('.', 1)
        for context in contexts:
            if context is None or context.fact != table:
                return False
            found = False
            for rule in context.filters:
                expression = sqlglot.parse_one(rule.predicate, read='duckdb')
                if (rule.table != table or not isinstance(expression, exp.EQ)
                        or not isinstance(expression.this, exp.Column)
                        or expression.this.table != table.rsplit('.', 1)[-1] or expression.this.name != column
                        or not isinstance(expression.expression, exp.Literal)):
                    continue
                literal = expression.expression
                expected = typed_scalar(literal.this, entry.column, vocabulary)
                actual = typed_scalar(getattr(predicate.value, 'value', None), entry.column, vocabulary)
                found |= expected is not None and actual == expected and predicate_value_supported(predicate, literal.this, vocabulary)
            if not found:
                return False
        return bool(contexts)

    contexts = [vocabulary.contexts.get(m.context.id) for m in plan.measures]
    plan = plan.model_copy(update={
        'filters': tuple(p for p in plan.filters if not required(p, contexts)),
        'measures': tuple(m.model_copy(update={'filters': tuple(p for p in m.filters if not required(
            p, [vocabulary.contexts.get(m.context.id)]))}) for m in plan.measures)})
    nodes = {node.id: node for node in plan.nodes()} | {'time': plan.time, 'limit': plan.limit}
    nodes.update({m.id + '/mode': m for m in plan.measures})
    obligations = [o for o in plan.obligations if o.state == 'removed' or all(s.turn != turn for s in o.spans)]
    named = {i for p in pieces if p.kind in ('name', 'predicate') for i in p.candidates}
    columns = named | {vocabulary.entries[i].column for i in named
                       if i in vocabulary.entries and vocabulary.entries[i].kind == 'value'}
    columns.update(column for context in vocabulary.contexts.values()
                   if context.id in named_contexts or not named_contexts and context.measure in named
                   for column in context.values)

    def overlaps(span, piece):
        return span.turn == turn and span.start < piece.span.end and piece.span.start < span.end

    def grounded_time(piece, selection):
        """Ground every supported period against the complete requested window."""
        try:
            return time_matches(piece, selection, pieces, vocabulary)
        except (ValueError, KeyError):
            return False

    for piece in pieces:
        eligible = []
        period_change = (piece.operation == 'comparison' and any(c.kind in ('explicit_period', 'time_mode') for c in plan.comparisons)
                         or piece.operation in ('previous_period', 'previous_year'))
        for identifier, node in nodes.items():
            if identifier.endswith('/mode') and piece.operation not in ('current', 'as_reported'):
                continue
            operand = getattr(node, 'left', None)
            if getattr(operand, 'kind', None) == 'column' and operand.name.id not in columns:
                continue
            if piece.kind == 'name' and set(piece.candidates) & references(node, vocabulary):
                contexts = set(piece.candidates) & vocabulary.contexts.keys()
                if (contexts and getattr(node, 'kind', None) in ('measure', 'distinct_count')
                        and node.context.id not in contexts and not contexts & cohort_selectors(node, vocabulary)):
                    continue
                eligible.append(identifier)
            elif piece.kind == 'predicate' and predicate_supported(piece, node, vocabulary, plan):
                eligible.append(identifier)
            elif piece.kind == 'time' and ((identifier == 'time' and grounded_time(piece, node)) or (
                    getattr(node, 'kind', None) == 'explicit_period' and grounded_time(piece, node.other))):
                eligible.append(identifier)
            elif piece.kind == 'operator' and (piece.operation in operations(node, vocabulary, plan)
                    or piece.operation == 'dimension' and identifier in {d.id for d in plan.dimensions}
                    or piece.operation == 'comparison' and piece.value == ('difference', 'percent_change')
                    and getattr(node, 'kind', None) in piece.value):
                if aggregation_owner(piece, node, requested_aggregations, vocabulary):
                    eligible.append(identifier)
            elif period_change and getattr(node, 'kind', None) in ('difference', 'percent_change'):
                eligible.append(identifier)
            elif piece.kind == 'literal' and literal_supported(piece, [node]):
                eligible.append(identifier)
        if piece.operation in ('asc', 'desc'):
            eligible.extend(o.output for o in plan.order if o.direction in (piece.operation, 'both'))
        if piece.operation == 'separate' and plan.layout == 'separate':
            eligible.extend(m.id for m in plan.measures)
        hints = {target for o in plan.obligations if o.state == 'applied' and any(overlaps(s, piece) for s in o.spans)
                 for target in o.targets}
        targets = tuple(dict.fromkeys(i for i in eligible if not hints or i in hints))
        if not targets:
            targets = tuple(dict.fromkeys(eligible))
        if piece.operation in ('asc', 'desc') and plan.limit == 1 and any(
                p.kind == 'operator' and p.text.casefold() in SINGULAR_RANK_WORDS for p in pieces):
            targets = tuple(dict.fromkeys((*targets, 'limit')))
        if piece.operation == 'comparison' and piece.value == ('difference', 'percent_change'):
            targets = tuple(dict.fromkeys((*targets, *(d.id for d in plan.derived if d.kind in piece.value))))
        elif period_change:
            targets = tuple(dict.fromkeys((*targets, *(d.id for d in plan.derived if d.kind in ('difference', 'percent_change')))))
        obligations.append(Obligation(id='o' + str(len(obligations)), spans=(piece.span,), kind='name',
                                      targets=targets, state='applied' if targets else 'unresolved'))

    def grounded(value, owner=None):
        if isinstance(value, Name):
            spans = tuple(p.span for p in pieces if name_supported(value.id, p.candidates, vocabulary)
                          and any(p.span in o.spans and owner in o.targets for o in obligations))
            old = tuple(s for s in value.evidence if s.turn != turn)
            return value.model_copy(update={'evidence': (*old, *spans)})
        if isinstance(value, Record):
            identifier = getattr(value, 'id', getattr(value, 'output', owner))
            fields = {field: grounded(getattr(value, field), 'time' if field == 'time' else identifier)
                      for field in type(value).model_fields if field != 'obligations'}
            if 'evidence' in fields:
                fields['evidence'] = tuple(dict.fromkeys((*(s for s in value.evidence if s.turn != turn),
                    *(s for o in obligations if identifier in o.targets for s in o.spans))))
            return value.model_copy(update=fields)
        if isinstance(value, tuple):
            return tuple(grounded(child, owner) for child in value)
        return value

    plan = grounded(plan)
    return plan.model_copy(update={'obligations': tuple(o.model_copy(update={'id': 'o' + str(i)})
                                                       for i, o in enumerate(obligations))})


def separate_filter_parts(piece, pieces, text):
    """Coordinated contrasting qualifiers name separate outputs of a measure."""
    if piece.kind != 'predicate':
        ordered = sorted((p for p in pieces if p.span.turn == piece.span.turn), key=lambda p: p.span.start)
        qualifiers = [(operator, value) for operator, value in zip(ordered, ordered[1:])
                      if operator.operation in ('gt', 'ge', 'lt', 'le') and value.kind == 'literal'
                      and not text[operator.span.end:value.span.start].strip()]
        for (left, a), (right, b) in zip(qualifiers, qualifiers[1:]):
            if (piece in (left, a, right, b) and (left.operation in ('gt', 'ge')) != (right.operation in ('gt', 'ge'))
                    and re.fullmatch(r'\s*(?:,|and)\s*', text[a.span.end:right.span.start], re.I)):
                return True
        return False
    for other in pieces:
        if (other.span.turn != piece.span.turn or other.kind != 'predicate' or other.value == piece.value
                or not set(piece.candidates) & set(other.candidates)):
            continue
        left, right = sorted((piece, other), key=lambda p: p.span.start)
        gap = text[left.span.end:right.span.start]
        if re.fullmatch(r'(?:\s*(?:,|and|to)\s*|-to-)', gap, re.I):
            return True
        inside = [p for p in pieces if p.span.turn == piece.span.turn and left.span.end <= p.span.start and p.span.end <= right.span.start]
        earlier = text[:left.span.start].casefold()
        if (re.match(r'\s*(?:,|and\b)', gap, re.I) and len(re.findall(r'\w+', gap)) <= 3
                and all(p.kind == 'operator' and re.search(r'\b' + re.escape(p.text.casefold()) + r'\b', earlier) for p in inside)):
            return True
    return False
