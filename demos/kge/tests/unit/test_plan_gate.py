from dataclasses import replace

import pytest

from kge.pipeline.sqlguard import temporal_rule, windowed
from kge.semantics.query import Asked, Plan
from tests.plan_fixtures import semantics


POLICY = {'is_current': 'cur', 'valid_from': 'vf', 'valid_to': 'vt', 'valid_to_inclusive': False}


@pytest.mark.parametrize('date', ["last_day(f.occurred)", "CAST('2025-06-30' AS DATE)", 'r.freeze'])
def test_complete_declared_validity_is_its_own_window(date):
    condition = f'm.entity_id = f.entity_id AND m.vf <= {date} AND (m.vt IS NULL OR m.vt > {date})'
    assert temporal_rule(condition, 'm', POLICY)
    assert not temporal_rule(condition, 'other', POLICY)


@pytest.mark.parametrize('condition', ['m.vf <= r.freeze', 'm.vt > r.freeze',
                                      'm.vf <= r.freeze AND (m.vt IS NULL OR m.vt >= r.freeze)',
                                      'm.vf <= r.freeze AND (m.vt IS NULL OR m.vt > r.freeze OR TRUE)',
                                      'm.cur AND m.vf <= r.freeze AND (m.vt IS NULL OR m.vt > r.freeze)'])
def test_partial_conflicting_and_wrong_endpoint_dating_refuses(condition):
    with pytest.raises(ValueError):
        temporal_rule(condition, 'm', POLICY)


def test_per_join_date_suppresses_only_that_roles_fallback():
    sem = semantics(release=True, joined=True)
    sem.scd2['u.members'] = POLICY
    sem.tables['u.members'].update({'cur': 'BOOLEAN', 'vf': 'DATE', 'vt': 'DATE'})
    p = Plan(sem, sem.contexts['ctx'], Asked(time_mode='as_reported'))
    p.fact = p.name(p.context.fact)
    p.release = {'alias': 'r', 'freeze_date': 'freeze'}
    join = replace(p.context.joins[-1], condition=p.context.joins[-1].condition + ' AND members.vf <= last_day(events.occurred) AND (members.vt IS NULL OR members.vt > last_day(events.occurred))')
    p.add_join(join)
    assert 'r.freeze' not in p.joins[-1]
    assert p.version('other', 'u.members') == 'other.vf <= r.freeze AND (other.vt IS NULL OR other.vt > r.freeze)'


def test_numeric_current_flag_is_a_complete_window():
    assert windowed({'m.cur <> 0'}, 'm', POLICY)
