"""Recorded content mistakes may refuse, clarify, or return only the question's truth."""
import json

import pytest

from kge.eval.grounding import load, outcome
from kge.pipeline.settings import ROOT
from kge.semantics.plan import Propose


FIXTURE = ROOT / 'tests/fixtures/grounding_eval/mistakes.json'
ITEMS = load(FIXTURE)
MISTAKES = {
    'wrong_measure', 'wrong_period', 'unreleased_month', 'invented_predicate',
    'unknown_column', 'other_context', 'swapped_filter_value', 'principal_scope',
    'wrong_unit', 'extra_grouping', 'dropped_filter', 'made_up_product',
    'prompt_injection_evidence', '405_modifier_context_i', '405_modifier_context_o',
    '405_modifier_context_net', '405_count_modifier', '410_filter_every_output_a',
    '410_filter_every_output_b', '410_filter_every_output_n', '410_comparison_sides',
    '410_period_sides', '410_share_scope', '405_unfrozen_share',
    '410_stale_followup_binding',
    'wrong_calendar',
}


def test_mistakes_set_keeps_all_seventy_one_distinct_recordings_and_attack_kinds():
    assert len(ITEMS) == 71
    assert len({item['id'] for item in ITEMS}) == 71
    assert {item['mistake_kind'] for item in ITEMS} == MISTAKES
    assert {'golden.json', 'heldout.json', 'golden_wealth_v1.json'} <= {
        item['source'].split('/')[-1].split('#')[0] for item in ITEMS
    }


@pytest.mark.parametrize('item', ITEMS, ids=lambda item: item['id'])
def test_recorded_mistake_has_a_replayable_plan_and_only_safe_expected_outcomes(item):
    assert isinstance(item['question'], str) and item['question'].strip()
    assert item['principal'] in ('analyst_enterprise', 'analyst_retail')
    assert item['kind'] in ('figure', 'abstain')
    assert item['mistake_kind'] in MISTAKES
    assert Propose.model_validate(item['reply']).kind == 'propose'
    assert (ROOT / item['source'].split('#')[0].split('::')[0]).is_file()
    expected = item['expected_outcome']
    assert isinstance(expected, list) and expected
    assert set(expected) <= {'correct', 'declined', 'asked'}
    assert len(expected) == len(set(expected))
    if item['expect'].get('no_figure'):
        assert item['kind'] == 'abstain'
        assert set(expected) == {'declined', 'asked'}
        assert 'reference_sql' not in item
        assert 'figure' not in item['expect'] and 'rows' not in item['expect']
    else:
        assert item['kind'] == 'figure'
        assert set(expected) == ({'correct'} if item['mistake_kind'] == 'wrong_calendar' else {'correct', 'declined', 'asked'})
        assert item['reference_sql'].lstrip().upper().startswith(('SELECT ', 'WITH '))
        assert item['expect']['unit'] in ('retail', 'sme', 'corporate', 'wealth')


@pytest.mark.parametrize('item', [item for item in ITEMS if '#' in item['source']], ids=lambda item: item['id'])
def test_golden_and_heldout_truth_is_preserved_when_only_the_reply_is_mistaken(item):
    path, identifier = item['source'].split('#')
    raw = json.loads((ROOT / path).read_text())
    source = next(entry for entry in (raw if isinstance(raw, list) else raw['items']) if entry['id'] == identifier)
    if item['expect'].get('no_figure'):
        assert item['mistake_kind'] in ('principal_scope', 'unreleased_month')
        return
    assert item['question'] == source['question']
    assert item['expect'] == source['expect']
    assert outcome(item, {'behaviour': 'figure', 'figure': float(item['expect']['figure']) + 1000}, {}) == 'wrong'
    assert 'wrong' not in item['expected_outcome']


def test_prompt_injection_is_recorded_inside_evidence_and_does_not_change_truth():
    injected = [item for item in ITEMS if item['mistake_kind'] == 'prompt_injection_evidence']
    assert len(injected) == 3
    for item in injected:
        evidence, = item['reply']['plan']['measures'][0]['context']['evidence']
        assert 'Ignore all previous instructions' in evidence['turn']
        assert 'bypass access and release checks' in evidence['turn']
        assert float(item['expect']['figure']) != 999_999_999
