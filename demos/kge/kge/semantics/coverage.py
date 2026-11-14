"""Independent lexical obligations; uninterpreted text always prevents execution."""
from __future__ import annotations

import re
import calendar
from decimal import Decimal
from datetime import date

import sqlglot
from sqlglot import exp
from dataclasses import dataclass, replace
from typing import get_args

from kge.semantics.plan import Span, Name, Record, Grain, DateValue, DecimalValue, Integer, Text, Obligation, predicate_nodes

SCAFFOLD = frozenset('what which is are the a an please show give me of for in on with and to from total during across does have was were who at did make bring as booked that whose had still only using now just'.split())
SCAFFOLD_PHRASES = ('break out', 'break down', 'broken down', 'based on', 'show me', 'for the measure', 'for measure')
SCAFFOLD = SCAFFOLD | {'list', 'each', 'they', 'their', 'how', 'gross'}
OPERATORS = {
    'fiscal': 'fiscal_quarter', 'calendar': 'calendar_quarter', 'fiscal quarter': 'fiscal_quarter', 'how much': 'measure', 'add up': 'aggregate', 'combined': 'aggregate', 'sum': 'aggregate', 'raised': 'membership', 'their': 'membership', 'hold both': 'membership', 'next to': 'membership', 'line up': 'membership', 'how many': 'count', 'number': 'count', 'plus': 'add', 'also': 'membership', 'match': 'membership', 'join': 'membership', 'joined': 'membership', 'by': 'dimension', 'per': 'dimension', 'count': 'count', 'counts': 'count', 'number of': 'count',
    'distinct': 'count', 'versus': 'comparison', 'vs': 'comparison', 'compare': 'comparison',
    'previous period': 'previous_period', 'previous year': 'previous_year', 'year over year': 'previous_year',
    'ratio': 'ratio', 'share': 'share', 'percent change': 'percent_change', 'change': 'difference', 'difference': 'difference', '$ change': 'difference', 'running total': 'running_total',
    'all': 'all', 'any': 'any', 'membership': 'membership', 'in': 'in', 'is null': 'null', 'is not null': 'not_null', 'not': 'not', 'excluding': 'not', 'or': 'any', 'above': 'gt', 'below': 'lt', 'at least': 'ge',
    'at most': 'le', 'equal to': 'eq', 'top': 'desc', 'bottom': 'asc', 'ascending': 'asc', 'descending': 'desc', 'most': 'desc', 'least': 'asc',
    'current': 'current', 'current versions': 'current', 'current version': 'current', 'as reported': 'as_reported', 'year to date': 'ytd', 'through': 'range',
    'all available': 'all_available', 'latest released': 'latest_released', 'separate': 'separate',
    'day': 'day', 'month': 'month', 'year': 'year', 'monthly': 'month', 'quarter': 'calendar_quarter',
    '>=': 'ge', '<=': 'le', '>': 'gt', '<': 'lt', '=': 'eq', '!=': 'ne',
}

OPERATORS.update({grain.replace('_', ' '): grain for grain in get_args(Grain)})
OPERATORS.update({'bring in': 'measure', 'in total': 'aggregate', 'booked by': 'measure'})
OPERATORS['which'] = 'count'
OPERATORS.update({p: 'ytd' for p in ('ytd', 'year-to-date', 'fiscal year to date', 'fiscal year-to-date', 'fiscal ytd', 'calendar year to date', 'calendar year-to-date')})
OPERATORS.update({'highest': 'desc', 'lowest': 'asc', 'cumulative': 'running_total',
                  'contribution': 'share', 'contribute': 'share', 'dollar': 'difference',
                  'percent difference': 'percent_change', '%': 'percent_change',
                  'month-over-month': 'previous_period', 'month over month': 'previous_period', 'or more': 'ge'})
OPERATORS.update({phrase: 'comparison' for phrase in ('compared with', 'compared to', 'against')})
OPERATORS.update({'quarterly': 'calendar_quarter', 'yearly': 'year'})


@dataclass(frozen=True)
class Piece:
    span: Span
    text: str
    kind: str
    candidates: tuple[str, ...] = ()
    operation: str | None = None
    value: object = None
    window: tuple[Span, ...] = ()
    cutoff: bool = False


def modified_measures(text, names, vocabulary):
    """An adjacent modifier qualifies the whole declared measure phrase it precedes."""
    contexts = {phrase: tuple(i for i in ids if i in vocabulary.contexts) for phrase, ids in names.items()}
    contexts = {phrase: ids for phrase, ids in contexts.items() if ids}
    nouns = {phrase for phrase, ids in names.items() if any(vocabulary.entries[i].kind in ('context', 'measure') for i in ids)}
    modifiers = {}
    for phrase, identifiers in contexts.items():
        for split in re.finditer(r'\s+', phrase):
            prefix, noun = phrase[:split.end()], phrase[split.end():]
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


def ledger(text, turn, vocabulary):
    """Reuse a scoped vocabulary's evidence, copying the mutable period readings for each caller."""
    from kge.services.ask.engine.question import Period
    pieces = vocabulary.lexical_ledger(text, turn, vocabulary.binding)
    return tuple(replace(p, value=replace(p.value)) if isinstance(p.value, Period) else p for p in pieces)


def _ledger(text, turn, vocabulary):
    declared = {phrase: 'as_reported' if mode == 'as_reported' else 'current'
                for phrase, mode in getattr(vocabulary.semantics.d, 'TIME_MODES', {}).items()}
    declared.update({p: 'latest_released' for p, v in getattr(vocabulary.semantics.d, 'NAMED_PERIODS', {}).items() if v == 'latest'})
    grouping = getattr(vocabulary.semantics.d, 'GROUP_BY', {})
    syntax = {**OPERATORS, **declared}
    names = dict(vocabulary.phrases)
    grouping = dict(grouping)
    ranking_groups = {}
    list_groups = {}
    for phrase, column in tuple(grouping.items()):
        if phrase.startswith('by ') and column in vocabulary.entries:
            each = 'each ' + phrase[3:]
            grouping[each] = column
            names[each] = (column,)
            list_groups[phrase[3:]] = column
    group_list = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in sorted({*list_groups, 'month', 'quarter', 'year'}, key=len, reverse=True)) + r')(?!\w)', re.I)
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
    if re.search(r'\b(?:top|bottom|most|least|highest|lowest)\b', text, re.I):
        from kge.services.ask.engine.question import plural_forms
        syntax.pop('which', None)
        for phrase, column in tuple(grouping.items()):
            if phrase.startswith('by ') and column in vocabulary.entries:
                noun = phrase[3:]
                for form in (*plural_forms(noun), noun + 'es' if noun.endswith(('ch', 'sh', 's', 'x')) else noun + 's'):
                    ranking_groups[form] = column
                    names.setdefault(form, (column,))
    if re.search(r'\b(?:inflows?|outflows?)\b', text, re.I):
        syntax['net'] = 'aggregate'
        gross = re.search(r'\bgross\s+(inflows?|outflows?)\b', text, re.I)
        if gross and not any(any(i in vocabulary.contexts or vocabulary.entries[i].kind == 'measure' for i in ids)
                             and re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)
                             for phrase, ids in names.items()):
            columns = {column for (pattern, *_), column in zip(getattr(vocabulary.semantics.d, 'FILTERS', ()),
                       getattr(vocabulary.semantics.d, 'FILTER_COLUMNS', ()))
                       if pattern.fullmatch(gross[1]) and column in vocabulary.entries}
            candidates = tuple(c.id for c in vocabulary.contexts.values()
                               if c.id in vocabulary.semantics.context_ids
                               and any(column.rsplit('.', 1)[0] == c.fact for column in columns))
            if candidates:
                names['gross'] = candidates
    literal = re.compile(r'FY\d{2,4}(?:-Q[1-4])?|\d{4}-\d{2}(?:-\d{2})?|\d{4}-Q[1-4]|-?\d+(?:\.\d+)?|[\w]+|>=|<=|!=|[><=]', re.I)
    amount = re.compile(r'\$\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)([KMB])?(?![\w,])', re.I)
    scaffold = re.compile(r'(?<!\w)(?:' + '|'.join(re.escape(p) for p in SCAFFOLD_PHRASES) + r')(?!\w)', re.I)
    title = (getattr(vocabulary.semantics.d, 'TITLE', None) or '').split()
    owner = re.compile(r'(?<!\w)' + re.escape(title[0]) + r"['’]s(?!\w)", re.I) if title else None
    months = '|'.join((*calendar.month_name[1:], *calendar.month_abbr[1:]))
    temporal = re.compile(r'(?:calendar\s+)?Q[1-4]\s+\d{4}|(?:\d{1,2}\s+)?(?:' + months + r')\s+\d{4}', re.I)
    from kge.services.ask.engine.question import period_evidence, qualified_phrases, read_period
    points = {start: (end, period) for start, end, period in period_evidence(text)}
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
    for start, end, phrase in qualified_phrases(text, names):
        if not text[start:].casefold().startswith(phrase.casefold()):
            qualifiers[start] = (end, names[phrase])
            noun = re.search(r'(?<!\w)' + re.escape(phrase[len('certified '):]) + r'(?!\w)', text[end:], re.I)
            if noun and not re.search(r'\b(?:vs|versus|against|compared|current)\b', text[end:end + noun.start()], re.I):
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
    currencies = tuple(match.span() for match in rx.finditer(text) if any(
        vocabulary.entries[identifier].kind == 'currency' for identifier in names.get(match.group().casefold(), ())))
    out, i = [], 0
    while i < len(text):
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
        match = re.match(r'(each\b|and\b|,)(?=\s+(?:day|month|quarter|year)\b)', text[i:], re.I)
        if match and (match[1].casefold() == 'each' or any(p.kind == 'name' and p.operation == 'dimension' for p in out)):
            end = i + match.end()
            out.append(Piece(Span(turn=turn, start=i, end=end), text[i:end], 'operator', operation='dimension'))
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
        match = temporal.match(text, i)
        if match:
            out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'time'))
            i = match.end()
            continue
        filtered = None
        for (pattern, _, op, group, value), column in filters:
            matched = pattern.match(text, i)
            if matched is None:
                later = pattern.search(text, i)
                prefix = text[i:later.start()].strip().casefold() if later else ''
                if prefix and vocabulary.candidates(prefix) and all(e.kind == 'unit' and e.id == column.split('.')[0] for e in vocabulary.candidates(prefix)):
                    matched = later
            if matched and column in vocabulary.entries:
                if any(i <= start < matched.end() < end for start, end in currencies):
                    continue
                scalar = matched.group(group) if group else value
                bare_value = group and matched.span() == matched.span(group)
                member = member_value(tuple(e.id for e in vocabulary.candidates(str(scalar))), vocabulary) if bare_value else None
                if member is not None:
                    column, scalar = member.column, member.value
                if filtered is None or matched.end() > filtered[0].end():
                    filtered = (matched, [column], op, scalar)
                elif matched.span() == filtered[0].span() and op == filtered[2] and scalar == filtered[3]:
                    filtered[1].append(column)
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
                             {'=': 'eq', '= TRUE': 'eq', 'IS NULL': 'null', '>': 'gt', '<': 'lt', '>=': 'ge', '<=': 'le'}[op], True if op == '= TRUE' else value))
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
            ranked = phrase in ranking_groups and re.search(r'\b(?:which|top|bottom)\s+(?:\d+\s+)?$', text[:i], re.I)
            ids = (ranking_groups[phrase],) if ranked else vocabulary.phrases.get(phrase, ()) if phrase in ranking_groups else names.get(phrase, ())
            if member := member_value(ids, vocabulary):
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'predicate',
                                 (member.column,), 'eq', member.value))
                i = match.end()
                continue
            if out and out[-1].operation == 'running_total':
                prior = {c for p in out[:-1] if p.kind == 'name'
                         and (p.text.casefold() == phrase or p.text.casefold().endswith(' ' + phrase))
                         for c in p.candidates if c in vocabulary.contexts}
                if len(prior) == 1:
                    out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'operator', tuple(prior), 'running_total'))
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
            predicate_following = re.sub(r'^(?:the|a|an)\s+', '', following, flags=re.I)
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
            adverb = {'monthly': 'month', 'quarterly': 'calendar_quarter', 'yearly': 'year'}.get(phrase)
            operation = 'dimension' if ranked or phrase in grouping or adverb else syntax.get(phrase)
            if phrase == 'share' and re.match(r'\s+of\s+', text[match.end():], re.I):
                tail = re.sub(r'^\s+of\s+', '', text[match.end():], flags=re.I)
                if any(noun not in ('total', 'firm', 'all') and any(vocabulary.entries[i].kind in ('context', 'measure') for i in ids)
                       and re.match(re.escape(noun) + r'(?!\w)', tail, re.I) for noun, ids in names.items()):
                    operation = 'ratio'
            whole = re.match(r'\s+of\s+(?:(?:the\s+)?(?:firm|total)|all)\b', text[match.end():], re.I) if operation == 'share' else None
            out.append(Piece(Span(turn=turn, start=i, end=match.end()), match.group(), 'name' if ids else 'operator', ids,
                             operation, 'whole_population' if whole else adverb))
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
            elif word.casefold() not in SCAFFOLD:
                out.append(Piece(Span(turn=turn, start=i, end=match.end()), word, 'literal' if re.fullmatch(r'FY\d{2,4}(?:-Q[1-4])?|-?\d+(?:\.\d+)?|\d{4}-\d{2}(?:-\d{2})?|\d{4}-Q[1-4]', word) else 'unknown'))
            i = match.end()
        else:
            if text[i] == '-' and (text[i + 1:i + 4].casefold() == 'to-' or text[max(0, i - 3):i].casefold() == '-to'):
                i += 1
                continue
            if not text[i].isspace() and text[i] not in ',.?!:;()\'"':
                out.append(Piece(Span(turn=turn, start=i, end=i + 1), text[i], 'unknown'))
            i += 1
    times = [p for p in out if p.kind == 'time']
    heads = [p for p in out if p.kind == 'name' and times and p.span.end <= times[0].span.start
             and any(i in vocabulary.contexts for i in p.candidates)]
    bare_modes = [p for p in out if times and p.span.end <= times[0].span.start
                  and p.operation in ('current', 'as_reported') and not p.candidates
                  and not any(h.span.start >= p.span.end and text[p.span.end:h.span.start].isspace() for h in heads)]
    changing = len(times) == 2 and bool(re.search(r'\bfrom\s*$', text[:times[0].span.start], re.I)) and (
        any(p.operation in ('difference', 'percent_change') and p.span.end <= times[0].span.start for p in out)
        or any(p.text.casefold() == 'compare' and p.span.end <= times[0].span.start for p in out)
        and len(heads) + len(bare_modes) <= 1
        and any(p.operation in ('difference', 'percent_change') for p in out))
    if changing:
        out = [replace(p, operation='comparison', value=('difference', 'percent_change'))
               if p.operation in ('difference', 'percent_change') else p for p in out]
    if len(times) == 2 and re.search(r'\bwith\b', text[times[0].span.end:times[1].span.start], re.I):
        out = [replace(p, value=('difference', 'percent_change')) if p.text.casefold() == 'compare' else p for p in out]
    comparison_to = (len(times) == 2 and re.fullmatch(r'\s*to\s*', text[times[0].span.end:times[1].span.start], re.I)
                     and re.search(r'\bcompare\b', text[:times[0].span.start], re.I)
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
    return tuple(replace(p, window=windows.get(p.span, ()), cutoff=bool(
        p.kind == 'time' and re.search(r'\b(?:(?:as\s+)?at|as\s+of)\s*$', text[:p.span.start], re.I))) for p in out)


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
    """Only a declared code/name pair and one declared member may coalesce."""
    entries = [vocabulary.entries[i] for i in identifiers]
    if len(entries) != 2 or any(e.kind != 'value' or not isinstance(e.value, str) or not e.column for e in entries):
        return None
    for row in getattr(vocabulary.semantics.d, 'VOCABULARY', ()):
        if row['kind'] != 'code_name_pair':
            continue
        pair = row['params']
        code = next((e for e in entries if e.column == pair['code']), None)
        name = next((e for e in entries if e.column == pair['name']), None)
        if code is None or name is None:
            continue
        members = [m for m in pair['members'] if m['name'].casefold() == name.value.casefold()]
        if len(members) == 1 and members[0]['code'] == code.value:
            return code
    return None


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


def context_names(ctx, vocabulary):
    names = set(ctx.values)
    names.update(i for i, e in vocabulary.entities.items() if e['table'] == ctx.fact)
    for rule in ctx.filters:
        names.update(ctx.unit + '.' + c.table + '.' + c.name for c in sqlglot.parse_one(rule.predicate, read='duckdb').find_all(exp.Column))
    return names


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
            referenced.update((context.unit, context.measure))
            referenced.update(context_names(context, vocabulary))
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
    if getattr(node, 'kind', '') == 'null' and not node.is_null:
        result.discard('null')
        result.add('not_null')
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
                    if entity is not None and entity['table'] == ctx.fact:
                        result.add('membership')
    return result


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
                dimensions = {i for i, t in (available or removed).items() if getattr(t, 'kind', '') in ('column', 'time')}
                valid = all(getattr(t, 'kind', '') in ('column', 'time') for t in targets) and dimensions == {t.id for t in targets}
            else:
                match = re.fullmatch(r'drop (?:the )?(.+?)(?: breakdown)?', phrase)
                named = {e.id for e in vocabulary.candidates(match[1])} if match else set()
                if match and all(getattr(target, 'kind', '') == 'column' for target in targets):
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
    ytd = [p for p in pieces if p.operation == 'ytd']
    fiscal_grouping = any(p.kind == 'operator' and p.operation in ('fiscal_quarter', 'fiscal_year') for p in pieces)
    if kind.startswith('fiscal_') or fiscal_grouping or any(p.text.casefold().startswith('fiscal') for p in ytd):
        calendars = {identifier for identifier, calendar in vocabulary.calendars.items()
                     if calendar['quarter_label'].startswith('FY')}
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
    expected = bounds(expected_period, vocabulary.calendars)
    actual = time_bounds(selection, vocabulary.calendars)
    if selection.kind == 'ytd':
        return ((expected_period.kind == 'month' and expected[1] == actual[1] or expected == actual)
                and selection.through.calendar.id == expected_period.calendar.id
                and any(p.operation == 'ytd' for p in pieces))
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


def ytd_problem(plan, text, vocabulary):
    if plan.time.kind != 'ytd':
        return None
    fiscal = bool(re.search(r'\bfiscal[ -]+(?:year[ -]to[ -]date|ytd)\b', text, re.I))
    cal = plan.time.calendar.id
    if fiscal:
        if vocabulary.calendars.get(cal, {}).get('year_start_month') == 1:
            return 'Fiscal year to date requires the declared fiscal calendar.'
    elif any(c.ytd_calendar and c.ytd_calendar != cal for m in plan.measures
             if (c := vocabulary.contexts.get(m.context.id)) is not None):
        return 'Year to date requires the context default calendar.'
    return None


def grouping_grains(pieces, text, turn=None):
    grains = {p.value for p in pieces if p.operation == 'dimension' and p.value in get_args(Grain)}
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
                calendar = (period_binding(times[0], pieces, vocabulary).calendar.id if times
                            else calendar_binding(dimension.grain, pieces, vocabulary))
                if dimension.calendar.id != calendar:
                    problems.append('Time grouping calendar disagrees with the question.')
            except ValueError as exc:
                problems.append(str(exc))
    for o in plan.obligations:
        if o.state == 'removed':
            continue
        if o.state != 'applied' or not o.targets:
            problems.extend(text[s.start:s.end] for s in o.spans if s.turn == turn)
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
                    contexts = set(max(heads, key=lambda head: head.span.end).candidates) & vocabulary.contexts.keys()
                    outputs = [m for m in plan.measures if m in targets or any(
                        node.id in o.targets for root in m.filters for node in predicate_nodes(root))]
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
                matching = [t for t in targets if p.operation in operations(t, vocabulary, plan) and set(p.candidates) & references(t, vocabulary)]
                if not matching or not all(p.operation == 'null' or predicate_value_supported(t, p.value, vocabulary) for t in matching):
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
                if p.operation == 'comparison' and p.value == ('difference', 'percent_change'):
                    comparisons = [target for target in targets if target in plan.comparisons]
                    pairs = [pair for comparison in comparisons for pair in (
                        [(comparison.id + '/base', comparison.id + '/other')] if comparison.kind == 'contexts' else
                        [(comparison.id + '/' + identifier + '/base', comparison.id + '/' + identifier + '/other') for identifier in comparison.inputs])]
                    if not pairs or any(not any(derived.kind == kind and (derived.numerator, derived.denominator) == pair
                                                for derived in plan.derived if derived.kind in ('difference', 'percent_change'))
                                        for pair in pairs for kind in p.value):
                        problems.append('comparison requires dollar difference and percent change')
                if p.operation == 'running_total' and p.candidates and not any(
                        getattr(target, 'kind', None) == 'running_total'
                        and getattr(getattr(nodes.get(target.input), 'context', None), 'id', None) in p.candidates for target in targets):
                    legal.discard('running_total')
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
        if not owns(node.id, allowed):
            problems.append('unrequested operation ' + node.id)
    for dimension in plan.dimensions:
        if not owns(dimension.id, {'dimension'}):
            problems.append('unrequested dimension ' + dimension.id)
    for order in plan.order:
        directions = ('asc', 'desc') if order.direction == 'both' else (order.direction,)
        if not all(owns(order.output, {direction}) for direction in directions):
            problems.append('unrequested ordering ' + order.output)
    for measure in plan.measures:
        if not problems and named_contexts and measure.kind == 'measure' and measure.id not in implicit_measures and not any(p.kind == 'name' and (
                measure.context.id in p.candidates or (measure.measure.id in p.candidates
                    and not set(p.candidates) & vocabulary.contexts.keys())) for p in pieces):
            problems.append('every measure part must use its named context')
        if measure.kind == 'distinct_count' and not owns(measure.id, {'count'}):
            problems.append('unrequested count ' + measure.id)
        if measure.mode != 'current' and not (owns(measure.id, {measure.mode}) or owns(measure.id + '/mode', {measure.mode})):
            problems.append('unrequested mode')
    for pred in (*plan.filters, *(p for m in plan.measures for p in m.filters)):
        for node in predicate_nodes(pred):
            owned = [p for p in pieces for o in plan.obligations if node.id in o.targets and p.span in o.spans]
            required = 'not_null' if node.kind == 'null' and not node.is_null else getattr(node, 'op', node.kind)
            if not any(p.operation == required for p in owned):
                problems.append('unrequested predicate ' + node.id)
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
    if re.fullmatch(r'(?:inflows?|outflows?)', piece.text, re.I):
        if following and re.fullmatch(r'\s+(?:of\s+)?', text[piece.span.end:following.span.start], re.I):
            return binding(following)
        preceding = [p for p in names if p.span.end <= piece.span.start]
        if preceding:
            return binding(preceding[-1])
    modifier = any(r['kind'] == 'predicate' and r['params'].get('modifier')
                   and r['params']['column'] in piece.candidates
                   and piece.text.casefold() in {p.casefold() for p in r['params']['phrases']}
                   for r in getattr(vocabulary.semantics.d, 'VOCABULARY', ()))
    if following and (re.fullmatch(r"(?:['’]s\s*|\s+for\s+(?:the\s+)?measure\s+)", text[piece.span.end:following.span.start], re.I)
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
    named_contexts = {c for p in pieces if p.kind == 'name' for c in p.candidates if c in vocabulary.contexts}
    if named_contexts and not plan.comparisons and any(m.kind == 'measure' and not any(
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
        period_change = (piece.operation == 'comparison' and any(c.kind == 'explicit_period' for c in plan.comparisons)
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
                        and node.context.id not in contexts):
                    continue
                eligible.append(identifier)
            elif (piece.kind == 'predicate' and set(piece.candidates) & references(node, vocabulary)
                  and piece.operation in operations(node, vocabulary, plan)
                  and (piece.operation == 'null' or predicate_value_supported(node, piece.value, vocabulary))):
                eligible.append(identifier)
            elif piece.kind == 'time' and ((identifier == 'time' and grounded_time(piece, node)) or (
                    getattr(node, 'kind', None) == 'explicit_period' and grounded_time(piece, node.other))):
                eligible.append(identifier)
            elif piece.kind == 'operator' and (piece.operation in operations(node, vocabulary, plan)
                    or piece.operation == 'dimension' and identifier in {d.id for d in plan.dimensions}
                    or piece.operation == 'comparison' and piece.value == ('difference', 'percent_change')
                    and getattr(node, 'kind', None) in piece.value):
                eligible.append(identifier)
            elif period_change and getattr(node, 'kind', None) in ('difference', 'percent_change'):
                eligible.append(identifier)
            elif piece.kind == 'literal' and literal_supported(piece, [node]):
                eligible.append(identifier)
        if piece.operation in ('asc', 'desc'):
            eligible.extend(o.output for o in plan.order if o.direction in (piece.operation, 'both'))
        hints = {target for o in plan.obligations if o.state == 'applied' and any(overlaps(s, piece) for s in o.spans)
                 for target in o.targets}
        targets = tuple(dict.fromkeys(i for i in eligible if not hints or i in hints))
        if not targets:
            targets = tuple(dict.fromkeys(eligible))
        if piece.operation in ('asc', 'desc') and plan.limit == 1 and re.search(r'\bwhich\b', text, re.I):
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
        if re.fullmatch(r'(?:\s*(?:,|and|to)\s*|-to-)', text[left.span.end:right.span.start], re.I):
            return True
    return False
