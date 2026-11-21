"""Issue 474, golden S13: a size threshold is a plan comparison marked magnitude, read on the absolute value of its
column; a signed comparison keeps its exact representation."""
import pytest
from pydantic import ValidationError

from kge.semantics.plan import Compare

COLUMN = {'kind': 'column', 'name': {'id': 'wealth.T.AMOUNT', 'evidence': []}, 'path': []}


def compare(**changes):
    return Compare.model_validate({'kind': 'compare', 'id': 'f0', 'left': COLUMN, 'op': 'ge',
                                   'value': {'kind': 'integer', 'value': 250000}, 'evidence': [], **changes})


def test_a_signed_comparison_keeps_its_exact_representation():
    assert 'magnitude' not in compare().model_dump(mode='json')
    assert compare(magnitude=True).model_dump(mode='json')['magnitude'] is True


@pytest.mark.parametrize('changes', [{'value': {'kind': 'integer', 'value': -1}}, {'value': {'kind': 'text', 'value': 'x'}},
                                     {'op': 'contains', 'value': {'kind': 'text', 'value': 'x'}}])
def test_a_size_threshold_is_a_numeric_comparison_with_a_number_of_zero_or_more(changes):
    with pytest.raises(ValidationError):
        compare(magnitude=True, **changes)
    compare(magnitude=True, op='lt', value={'kind': 'decimal', 'value': '0.5'})
