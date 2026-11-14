"""Independent obligations survive source qualifiers around declared filter phrases."""
import re
from dataclasses import replace

from kge.semantics.coverage import ledger
from kge.semantics.planvocabulary import Entry
from tests.plan_fixtures import semantics, vocabulary


def test_a_filter_phrase_cannot_consume_a_different_source_qualifier():
    sem = semantics()
    sem.tables['u.events']['resolved_on'] = 'DATE'
    sem.d.FILTERS = ((re.compile(r'complaints.*unresolved', re.I), 'resolved_on', 'IS NULL', None, None),)
    sem.d.FILTER_COLUMNS = ('u.events.resolved_on',)
    v = vocabulary(sem)
    v = replace(v, entries={**v.entries, 'other': Entry('other', 'unit', 'Other', 'other')},
                phrases={**v.phrases, 'other': ('other',)})
    pieces = ledger('Other complaints still unresolved', 'turn', v)
    assert [(p.text, p.candidates) for p in pieces] == [
        ('Other', ('other',)), ('complaints still unresolved', ('u.events.resolved_on',))]
