"""Scratch pack text filters and bands reconcile Ask with independently written SQL."""
from copy import copy, deepcopy
from types import SimpleNamespace

import pytest

from kge.compiler.checks import banded_dimensions, vocabulary as check_vocabulary
from kge.compiler.pack import read
from kge.compiler.vocabulary import band_entries
from kge.semantics.vocabulary import compiled
from tests.integration.test_measure_capabilities import CURRENT, FLOW, independent, rules_and_replay


BAND = {'id': 'sample_amount_bands', 'label': 'amount band', 'column': 'wealth.TRAN_FACT_1.TRAN_AM', 'ranges': [
    {'label': 'Small', 'min': None, 'max': 1000}, {'label': 'Medium', 'min': 1000, 'max': 10000},
    {'label': 'Large', 'min': 10000, 'max': None}]}
TEXT = [('contains', 'matching flow labels'), ('starts_with', 'initial flow labels')]


def declared_engine(eng):
    """New instances are metadata copies in this test, never sample-pack or fixture changes."""
    pack = deepcopy(read(eng.profile.clients, eng.profile.client))
    pack.data['banded_dimensions'] = [deepcopy(BAND)]
    predicates = [dict(column='wealth.TRAN_FACT_1.DW_TRAN_DIRCTN_CD', phrases=[phrase], op=operation, value='I')
                  for operation, phrase in TEXT]
    pack.data['terminology']['predicates'].extend(predicates)
    assert banded_dimensions(pack) == []
    assert check_vocabulary(pack) == []
    scratch = copy(eng)
    scratch.g = copy(eng.g)
    scratch.g._scoped_vocabularies = {}
    scratch.VOCABULARY = [*eng.VOCABULARY, *band_entries(pack.data),
                          *(dict(kind='predicate', params=p) for p in predicates)]
    scratch.GROUP_BY = {**eng.GROUP_BY, 'by amount band': BAND['id']}
    words = compiled(scratch.VOCABULARY)
    scratch.FILTERS, scratch.FILTER_COLUMNS = words.filters, words.columns
    scratch._semantics = copy(eng.semantics)
    scratch._semantics.d = SimpleNamespace(**{k: v for k, v in vars(scratch).items() if k.isupper()})
    scratch._semantics.plan_snapshot = scratch._semantics.baseline_snapshot = None
    return scratch


@pytest.mark.parametrize('operation,phrase', TEXT)
def test_text_matching_reaches_both_paths_and_matches_independent_sql(eng, operation, phrase):
    scratch = declared_engine(eng)
    plan, actual = rules_and_replay(scratch, f'NNA for June 2025 for {phrase}')
    predicate, = [n for n in plan.nodes() if n.kind == 'compare' and n.op == operation]
    assert predicate.left.name.id == 'wealth.TRAN_FACT_1.DW_TRAN_DIRCTN_CD'
    assert predicate.value.kind == 'text' and predicate.value.value == 'I'
    expected, = independent(scratch, f"SELECT sum({FLOW}) {CURRENT} AND f.DW_TRAN_DIRCTN_CD = 'I'")[0]
    assert actual[plan.measures[0].id] == expected


@pytest.mark.parametrize('operation,phrase', [(None, None), *TEXT])
def test_bands_and_text_matching_combine_and_match_independent_sql(eng, operation, phrase):
    scratch = declared_engine(eng)
    question = 'NNA by amount band for June 2025' + (' for ' + phrase if phrase else '')
    plan, actual = rules_and_replay(scratch, question, grouped=True)
    band, = plan.dimensions
    assert band.kind == 'band' and band.band.id == BAND['id']
    condition = " AND f.DW_TRAN_DIRCTN_CD = 'I'" if operation else ''
    statement = f"""SELECT CASE WHEN f.TRAN_AM < 1000 THEN 'Small'
WHEN f.TRAN_AM >= 1000 AND f.TRAN_AM < 10000 THEN 'Medium'
WHEN f.TRAN_AM >= 10000 THEN 'Large' END AS amount_group, sum({FLOW})
{CURRENT}{condition} GROUP BY 1"""
    expected = {label: value for label, value in independent(scratch, statement)}
    assert {key[0]: values[plan.measures[0].id] for key, values in actual.items()} == expected


def test_scratch_declarations_do_not_grant_names_to_the_served_pack(eng):
    scratch = declared_engine(eng)
    assert 'by amount band' not in eng.GROUP_BY
    assert all(row['params'].get('id') != BAND['id'] for row in eng.VOCABULARY)
    assert scratch.answer('NNA by amount band for June 2025', 'analyst_enterprise')['behaviour'] in ('figure', 'combined')
    assert eng.answer('NNA by amount band for June 2025', 'analyst_enterprise')['behaviour'] == 'abstain'
