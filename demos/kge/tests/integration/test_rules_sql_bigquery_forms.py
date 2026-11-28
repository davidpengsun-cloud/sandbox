"""Every golden-20 item's question and every rules shape (tests/fixtures/rules_shapes.json), asked on the rules path,
compiles to SQL whose BigQuery rewrite (to_bigquery's) holds none of the forms BigQuery refuses (tests/bigquery_forms.py):
the local gate runs on DuckDB, which accepts them. No BigQuery call is made."""
import json

import pytest

from kge.pipeline.settings import ROOT
from tests.bigquery_forms import for_bigquery, refusals
from tests.integration.test_golden20 import ITEMS, WHO

SHAPES = json.loads((ROOT / 'tests/fixtures/rules_shapes.json').read_text())
QUESTIONS = [pytest.param(item['question'], id=item_id) for item_id, item in sorted(ITEMS.items())]
QUESTIONS += [pytest.param(case['question'], id=case['id']) for case in SHAPES]


@pytest.mark.parametrize('question', QUESTIONS)
def test_the_rules_sql_holds_no_form_bigquery_refuses(eng, question):
    answer = eng.answer(question, WHO)
    if answer.get('behaviour') not in ('figure', 'combined'):
        pytest.skip(f"no SQL: {answer.get('behaviour')}")
    assert answer.get('sql')
    assert refusals(for_bigquery(answer['sql'])) == []
