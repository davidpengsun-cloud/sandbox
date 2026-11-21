import copy
import json
from pathlib import Path


DEMO = Path(__file__).resolve().parents[2]
FIXTURES = DEMO / 'tests/fixtures/grounding_eval'
NNA_IDS = ('W01', 'W02', 'W03', 'W04', 'W05', 'W06', 'W07', 'W09', 'W10', 'W12', 'W14', 'W15', 'W18')
REFUSALS = ('W13', 'W19', 'W20')
VARIANTS = ('split', 'partial', 'missing', 'unrelated')


def read(path):
    return json.loads(path.read_text())


def without_spans(reply):
    reply = copy.deepcopy(reply)
    for obligation in reply['plan']['obligations']:
        obligation['spans'] = []
    return reply


def test_citations_cover_golden_questions_with_only_evidence_changes():
    golden = {item['id']: item for item in read(DEMO / 'clients/meridian/fixture/golden_wealth_v1.json')['items']}
    cases = read(FIXTURES / 'citations.json')
    assert len(cases) == 52
    assert {case['id'] for case in cases} == {f'{item}-{variant}' for item in NNA_IDS for variant in VARIANTS}
    for item_id in NNA_IDS:
        group = {case['id'].split('-')[-1]: case for case in cases if case['id'].startswith(item_id + '-')}
        source = golden[item_id]
        assert all(case['question'] == source['question'] and case['expect'] == source['expect'] and
                   case['reference_sql'] == source['reference_sql'] and case['principal'] == 'analyst_enterprise' and
                   case['expected_outcome'] == ['correct'] for case in group.values())
        assert len({json.dumps(without_spans(case['reply']), sort_keys=True) for case in group.values()}) == 1
        obligations = {variant: group[variant]['reply']['plan']['obligations'] for variant in VARIANTS}
        changed = [i for i in range(len(obligations['split'])) if len({
            json.dumps(obligations[variant][i]['spans'], sort_keys=True) for variant in VARIANTS}) > 1]
        assert len(changed) == 1
        spans = {variant: obligations[variant][changed[0]]['spans'] for variant in VARIANTS}
        assert len(spans['split']) == 2 and spans['split'][0]['end'] <= spans['split'][1]['start']
        assert len(spans['partial']) == 1 and spans['partial'][0]['end'] - spans['partial'][0]['start'] == 1
        assert spans['missing'] == []
        assert spans['unrelated'] in [o['spans'] for i, o in enumerate(obligations['split']) if i != changed[0]]
        assert all(0 <= span['start'] < span['end'] <= len(source['question']) and span['turn'] == 'turn'
                   for variant in VARIANTS for span in spans[variant])


def test_reference_items_preserve_recordings_and_golden_expectations():
    golden = {item['id']: item for item in read(DEMO / 'clients/meridian/fixture/golden_wealth_v1.json')['items']}
    recordings = {row['case'].split(':')[1]: row for row in read(DEMO / 'tests/fixtures/agent-query-plan-v1-recordings.json')
                  if row['arm'] == 'treatment' and row['case'].startswith('golden_wealth_v1:')}
    cases = read(FIXTURES / 'reference.json')
    assert len(cases) == 16
    assert {case['id'] for case in cases} == set(NNA_IDS + REFUSALS)
    for case in cases:
        item_id = case['id']
        assert all(case[key] == value for key, value in golden[item_id].items())
        assert case['principal'] == 'analyst_enterprise'
        assert case['question'] == recordings[item_id]['question']
        assert case['reply'] == recordings[item_id]['reply']
        assert case['expected_outcome'] == (['declined'] if item_id in REFUSALS else ['correct'])
