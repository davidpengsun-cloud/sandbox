"""The sample refuses moving-average lengths absent from its pack declaration."""
import pytest


@pytest.mark.parametrize('length', [1, 4, 6, 12])
def test_unlisted_sample_window_lengths_are_refused_with_a_reason(eng, length):
    text = f'{length}-month moving average of NNA by month for Q1 2025'
    answer = eng.answer(text, 'analyst_enterprise')
    assert answer['behaviour'] not in ('figure', 'combined'), answer
    assert answer['reason'] == 'A moving average of that length is not declared for this figure. No figure is given.', answer
