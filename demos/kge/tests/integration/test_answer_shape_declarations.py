"""Released measure declarations, without extra warehouse reads."""
from kge.services.ask.engine import planrules


def test_current_and_frozen_measures_load_the_same_declared_population(eng):
    runtime = planrules.snapshot(eng)
    measures = ('wealth.net-new-assets', 'wealth.certified-nna')
    declared = [runtime.measure_declarations[m]['population'] for m in measures]
    assert declared[0] and declared[0] == declared[1]
    assert [runtime.measure_specs[m].population for m in measures] == declared
