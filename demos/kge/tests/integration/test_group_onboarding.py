"""New named region groups travel through onboarding as pack data alone."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from decimal import Decimal

import yaml

from kge.graph import release
from kge.pipeline.profile import resolve
from kge.pipeline.settings import ROOT
from kge.ports import AUTHORED
from kge.semantics.query import Asked
from kge.services.ask import Engine


REGIONS = "'MIDWEST', 'NER'"
GROUPS = [
    {'phrases': ['Prairie region'], 'column': 'wealth.BRCH_DIM_1.RGN_CD',
     'op': 'in', 'values': ['MIDWEST', 'NER']},
    {'phrases': ['outside Prairie region'], 'column': 'wealth.BRCH_DIM_1.RGN_CD',
     'op': 'not_in', 'values': ['MIDWEST', 'NER']},
]
POPULATION = """FROM TRAN_FACT_1 f JOIN MONTH_DIM_1 m ON f.MNTH_END_DT=m.CLDR_EOM_DT
WHERE m.DW_BRKRG_MNTH_FRM_TODAY_CT<>0 AND f.DW_TRAN_DT>=DATE '2025-01-01' AND f.DW_TRAN_DT<DATE '2025-02-01'
AND f.NNA_IN='Y' AND f.RCV_IN=1 AND f.CURR_IN=1"""


def reference(engine, condition=None):
    """Read a gated included population; total minus included retains every missing region."""
    population = POPULATION
    if condition:
        population = population.replace('WHERE m.', """JOIN ACCT_DIM2_1 a ON f.DW_ACCT_ID=a.DW_ACCT_ID
AND f.ACCT_SRCE_SYS_ID=a.SRCE_SYS_ID AND a.CURR_IN<>0
JOIN BRCH_DIM_1 b ON a.ACCT_DOMCL_BRCH_CD=b.BRCH_CD AND b.CURR_IN<>0
WHERE m.""")
    statement = """SELECT COALESCE(SUM(f.TRAN_AM),0)
""" + population + (' AND (' + condition + ')' if condition else '')
    context = engine.semantics.contexts['nna']
    allow = engine.semantics.allowlist(engine.semantics.plan(context, Asked()))
    guard = engine.w.gate('wealth', statement, allow, dialect=AUTHORED)
    assert guard['passed'], guard
    return Decimal(str(engine.w.rows('wealth', statement, AUTHORED)[0][0])).quantize(Decimal('0.01'))


def figure(answer):
    assert answer['behaviour'] == 'figure', answer.get('reason')
    assert answer['guard']['passed'], answer['guard']
    cell, = answer['result']['rows'][0]['cells']
    return Decimal(cell['value']['value'])


def test_new_in_and_not_in_groups_onboard_and_answer_without_a_code_change(tmp_path, monkeypatch):
    """The pilot's new group requires a pack edit and the ordinary candidate loop."""
    for key in ('STORE_URL', 'LANDING_URL', 'TMP_URL', 'HASH', 'PREVIOUS', 'BASELINE', 'FROM', 'CATALOGUE_TARGET',
                'MODEL_HIGH_PROVIDER', 'MODEL_MEDIUM_PROVIDER', 'MODEL_LOW_PROVIDER', 'MODEL_DEFAULT_PROVIDER'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('MODEL_PROVIDER', 'none')
    monkeypatch.setenv('TAGGER_PROVIDER', 'rules')
    monkeypatch.setenv('PROFILE', '')
    clients = tmp_path / 'clients'
    folder = clients / 'meridian'
    shutil.copytree(ROOT / 'clients/meridian', folder)
    pack_path = folder / 'pack.yaml'
    pack = yaml.safe_load(pack_path.read_text())
    existing = {phrase for predicate in pack['terminology']['predicates'] for phrase in predicate['phrases']}
    assert not existing.intersection(phrase for group in GROUPS for phrase in group['phrases'])
    pack['terminology']['predicates'].extend(GROUPS)
    pack_path.write_text(yaml.safe_dump(pack, sort_keys=False))
    registry = tmp_path / 'profiles.yaml'
    registry.write_text(yaml.safe_dump({'default': 'meridian', 'profiles': {'meridian': {
        'client': 'meridian', 'clients': str(clients), 'artifacts': str(tmp_path / 'artifacts'),
        'store': str(tmp_path / 'stores'), 'landing': str(tmp_path / 'landing'),
        'warehouses': str(tmp_path / 'estate/warehouses'),
        'identities': str(ROOT / 'deploy/meridian/identities.yaml'),
        'baseline': str(folder / 'baselines/issue-474.json'), 'asker': 'analyst_enterprise',
    }}}))
    monkeypatch.setenv('PROFILES', str(registry))
    profile = resolve()
    done = subprocess.run([sys.executable, '-m', 'kge.cli', 'onboard', 'REPEATS=1'], cwd=ROOT,
                          env=dict(os.environ), capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    candidate = next(line.removeprefix('onboard: candidate ') for line in done.stdout.splitlines()
                     if line.startswith('onboard: candidate '))
    assert profile.current() is None, 'onboarding prepares a candidate for the steward'
    assert release.gate(profile.at(candidate).graph_path())['passed']
    engine = Engine.candidate(profile.at(candidate))
    try:
        predicates = [row['params'] for row in engine.VOCABULARY if row['kind'] == 'predicate']
        assert all(group in predicates for group in GROUPS), 'the candidate must carry both new pack declarations'
        included = reference(engine, f'b.RGN_CD IN ({REGIONS})')
        total = reference(engine)
        excluded = total - included
        assert included and excluded
        for text in ('NNA for Jan 2025 for Prairie region', 'NNA for Jan 2025 for Midwest and Pacific regions',
                     'NNA for Jan 2025 for Midwest or Pacific region'):
            answer = engine.answer(text, 'analyst_enterprise')
            assert figure(answer) == included, text
            assert ' IN (' in answer['sql'], answer['sql']
        for text in ('NNA for Jan 2025 excluding Prairie region', 'NNA for Jan 2025 for outside Prairie region',
                     'NNA for Jan 2025 excluding Midwest and Pacific regions',
                     'NNA for Jan 2025 excluding Midwest or Pacific region'):
            answer = engine.answer(text, 'analyst_enterprise')
            assert figure(answer) == excluded, text
            assert 'IS NULL' in answer['sql'], answer['sql']
        assert included + excluded == total
    finally:
        engine.close()
