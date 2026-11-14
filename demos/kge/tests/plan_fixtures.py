"""Isolated declarations and independent numerical input for the generic planner."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from kge.semantics.plan import Binding, QueryPlan
from kge.semantics.planvocabulary import MeasureSpec, build_vocabulary
from kge.semantics.query import Context, Join, Rule, Semantics


def name(identifier, evidence=()):
    return {'id': identifier, 'evidence': list(evidence)}


def model(**updates):
    data = {'schema': 'query-plan/v1', 'layout': 'aligned',
            'measures': [{'kind': 'measure', 'id': 'm', 'measure': name('u.metric'), 'context': name('ctx'), 'mode': 'current', 'filters': []}],
            'dimensions': [], 'filters': [], 'time': {'kind': 'all_available', 'calendar': name('cal')},
            'comparisons': [], 'derived': [], 'order': [], 'limit': None, 'obligations': []}
    data.update(updates)
    return QueryPlan.model_validate(data)


def semantics(release=False, joined=False):
    sem = Semantics.__new__(Semantics)
    sem.d = SimpleNamespace(RESTRICTED_COLUMNS=[('u', 'events', 'private')], PERIODS={'u.events': {'column': 'occurred', 'kind': 'day', 'calendar': 'cal'}},
                            GROUP_BY={}, ACRONYMS={}, CALENDARS={'cal': {'year_start_month': 1, 'quarter_label': '{yyyy}-Q{n}'}}, BUSINESS={})
    sem.tables = {'u.events': {'value': 'DECIMAL', 'occurred': 'DATE', 'category': 'VARCHAR', 'entity_id': 'INTEGER', 'source': 'VARCHAR', 'private': 'VARCHAR'}}
    joins = []
    if release:
        sem.tables['u.calendar_rows'] = dict.fromkeys(('period', 'freeze', 'release_date'), 'DATE') | {'released': 'BOOLEAN'}
        joins.append(Join('release_join', 'key', 'last_day(events.occurred) = calendar_rows.period', None, None, 'u.events', 'u.calendar_rows', True))
    if joined:
        sem.tables['u.members'] = {'entity_id': 'INTEGER', 'source': 'VARCHAR', 'grouping': 'VARCHAR'}
        joins.append(Join('member_join', 'key', 'events.entity_id = members.entity_id AND events.source = members.source', None, None, 'u.events', 'u.members', False))
    sem.contexts = {'ctx': Context('ctx', 'Context one', '', 'Metric', 'u.metric', 'sum(events.value)', 'sum', 'day', 'u.events',
                                   ('u.events.value',), (Rule('positive', '', 'events.value >= 0', 'u.events'),), (), tuple(joins))}
    sem.scd2 = {}
    sem.releases = {'u.calendar_rows': {'table': 'u.calendar_rows', 'period': 'period', 'freeze_date': 'freeze', 'release_date': 'release_date', 'released': 'released'}} if release else {}
    sem.entities = {'entity': {'label': 'Entities', 'table': 'u.events', 'key': ['entity_id', 'source']}}
    sem.cites, sem.declared, sem.types = {}, {j.id: j for j in joins}, {t + '.' + c: dtype for t, cs in sem.tables.items() for c, dtype in cs.items()}
    return sem


def vocabulary(sem=None, **options):
    return build_vocabulary(sem or semantics(), Binding(release_identity='r', access_fingerprint='a', products=(), purpose='analysis'),
                            ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True)], **options)


def grounded(plan, text, vocab, assignments=None):
    from kge.semantics.coverage import ledger
    data = plan.model_dump(mode='json', by_alias=True)
    assignments = assignments or {}
    obligations = []
    for i, piece in enumerate(ledger(text, 'turn', vocab)):
        target = assignments.get(piece.text, 'm')
        targets = target if isinstance(target, list) else [target]
        obligations.append({'id': 'o' + str(i), 'spans': [piece.span.model_dump()], 'kind': 'name', 'targets': targets, 'state': 'applied'})
    data['obligations'] = obligations
    return QueryPlan.model_validate(data)


def monthly_time(first='2025-04', last='2025-06'):
    return {'kind': 'range', 'first': {'kind': 'month', 'value': first, 'calendar': name('cal')},
            'last': {'kind': 'month', 'value': last, 'calendar': name('cal')}}


def time_dimension():
    return {'kind': 'time', 'id': 't', 'calendar': name('cal'), 'grain': 'month'}


ROWS = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 6, 10), 5, 'A', 1, 'x')]
