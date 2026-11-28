"""The owner's 20 golden NNA questions as a hard gate (issue #474): each of sage v4's items S06 to S25
(kge/estate/golden_sage.py), with every one of its phrasings, asked through the Ask path on Meridian's sample, the rules
path always and the replayed-model path where a recording is held for the phrasing (with none, that path is skipped for
it, not failed). Figures first: an answer passes when its figures match its item's truth by the set's own scorer,
whatever SQL it ran. The truth is the item's approved reference SQL run now on the runtime the answer ran on, through
the runtime layer and the SQL gate, as every eval judges (kge/eval/run_golden.py, judge; issue #565). An item whose
truth cannot be computed there (unavailable) or whose reference SQL is not approved (draft) fails this gate with its
reason: on the committed pack's reference runtime every truth must compute. Only for a mismatch, a decline or a missing
truth does the report show the answer's SQL beside the reference SQL, with the first difference between them (filter,
join, period, grouping or measure) and the layer that refused or got it wrong.

The committed golden-20 baseline (clients/meridian/baselines/golden20.json, kge/estate/golden20.py) is a snapshot of
each reference SQL's rows on the sample, checked here for staleness; it is not the gate's truth.

An item passes when every phrasing passes on every path asked. An item that cannot answer yet is a strict expected
failure, its reason in EXPECTED: one that starts answering fails the gate until it is promoted (taken out of EXPECTED),
and a passing item that regresses fails it.

GOLDEN20_HELDOUT names a JSON file of held-out phrasings, [{"id": "S06", "phrasings": ["..."]}, ...]; with it, each item
is asked again with its question, its other phrasings and its held-out ones, and passes only if all of them do. Without
it that part is skipped. make golden20 prints the score, "N of 20 answer correctly", and each failure.

The gate is also read as a BigQuery install reads it: each phrasing's statements, rebuilt from the plan its rules answer
checked, are renamed for a named BigQuery runtime (an IMPLEMENTS mapping that keeps the sample's names, and one that
renames every table and column), written in BigQuery's SQL and checked under BigQuery's rules, so a dialect that reads a
statement otherwise than the checked plan fails here, not on the pilot (#562).
"""
import contextlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from kge.adapters.warehouse.runtimes import RuntimeManager
from kge.estate import golden20
from kge.estate.golden import warehouses
from kge.estate.golden_sage import GOLDEN_20
from kge.eval import sets
from kge.pipeline.sqlguard import CheckedAllowlist, check
from kge.ports import AUTHORED
from kge.semantics.compile import compile_plan
from kge.semantics.plan import QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.eval.run_golden import asking_runtimes, judge
from kge.services.ask.engine import runtrace
from kge.services.ask.engine.agent import Agent
from tests.agent_recordings import for_question
from tests.integration.test_context_scope import vocabulary
from tests.scratch import BUILD

WHO = 'analyst_enterprise'
MAPPINGS = {'same names': (lambda table: table, lambda column: column),
            'renamed': (lambda table: 'runtime.APP_' + table.split('.')[-1], lambda column: 'P_' + column)}
HELDOUT = 'GOLDEN20_HELDOUT'
RESULTS: dict[str, dict[str, list]] = {'asked': {}, 'heldout': {}}
RULES: dict[str, tuple] = {}
TRUTH: dict[str, dict] = {}
UNIT = 'wealth'

EXPECTED = {
}

COVERAGE = re.compile(r"unrequested |could not be matched to a certified measure|^No answer:|^(?!Please )[^.;]{1,30}(; [^.;]{1,40})*\. No figure is given")
VOCABULARY = re.compile(r'No glossary term recognised|^Not certified in |not a glossary term|routed to the steward|not yet a defined term')
COMPILER = re.compile(r'not (yet )?released|no unique declared calendar|no declared calendar binding')


def layer(ans: dict) -> str:
    """Which layer refused an answer, from what the answer says: the SQL gate where its guard did not pass; the pack
    vocabulary where a word is not a declared term; the compiler where a checked plan did not compile (a month not
    released, a period with no calendar binding); the word check (kge/semantics/coverage.py) where words were left
    uncovered or the plan holds a node the question did not ask for; else the rules planner, which found no shape for
    the question (a period it cannot place, a shape it does not build, or a choice it asks for)."""
    if isinstance(ans.get('guard'), dict) and not ans['guard'].get('passed', True):
        return 'SQL gate'
    reason = ans.get('reason') or ''
    if ans.get('behaviour') == 'abstain_route_steward' or VOCABULARY.search(reason):
        return 'pack vocabulary'
    if COMPILER.search(reason):
        return 'compiler'
    if COVERAGE.search(reason):
        return 'word check'
    if ans.get('behaviour') in ('figure', 'combined'):
        return 'answered, figures differ'
    return 'rules planner'


def came_back(ans: dict) -> str:
    if ans.get('behaviour') in ('figure', 'combined'):
        return f"{ans['behaviour']}: {json.dumps(ans.get('rows') or ans.get('figure') or ans.get('answer_text'), default=str)[:160]}"
    return f"{ans.get('behaviour')}: {(ans.get('reason') or '')[:160]}"


def golden_items() -> dict[str, dict]:
    """The 20 items by id, each with every phrasing the set holds for it (its question, then its other phrasings). Their
    figures are not read here: judge computes each from the item's reference SQL on the runtime its answer used."""
    held = sets.items(BUILD / 'sage_v1.json')
    out = {i['id']: {**i, 'phrasings': [i['question']]}
           for i in held if i.get('category') == GOLDEN_20 and not i.get('variant_of')}
    for i in held:
        if i.get('variant_of') in out:
            out[i['variant_of']]['phrasings'].append(i['question'])
    return out


def heldout_phrasings() -> dict[str, list[str]]:
    """The held-out phrasings GOLDEN20_HELDOUT names, by item id; none without it."""
    path = os.environ.get(HELDOUT)
    if not path:
        return {}
    out: dict[str, list[str]] = {}
    for row in json.loads(Path(path).read_text()):
        phrasings = row['phrasings'] if isinstance(row['phrasings'], list) else [row['phrasings']]
        out.setdefault(row['id'], []).extend(phrasings)
    return out


ITEMS = golden_items()


def rules_answer(eng, question: str) -> dict:
    """The rules path's answer to question on this engine, asked once: the gate and the BigQuery sweep read the same."""
    held = RULES.get(question)
    if held is None or held[0] is not eng:
        held = RULES[question] = (eng, eng.answer(question, WHO))
    return held[1]


def verdict(eng, item: dict, ans: dict, runtimes: list[str] | None) -> dict:
    """judge's verdict on one answer: the item's approved reference SQL run on the runtime the answer used, through the
    runtime layer and the SQL gate, as WHO reads it; each truth computed once per runtime for the module (TRUTH)."""
    return judge(item, ans, w=eng.w, runtimes=runtimes, readable_units=eng.g.readable_units(WHO), cache=TRUTH,
                 default_unit=UNIT)


def ask(eng, item: dict, question: str) -> list[dict]:
    """The question on the rules path, then on the replayed-model path where a recording is held for it: each with
    whether it passed judge, what came back, and, where it did not, the layer (or why its truth is missing) and its
    SQL's first difference from the reference SQL."""
    runtimes = asking_runtimes(item)

    def run(path, ans):
        got = verdict(eng, item, ans, runtimes)
        ok = got['status'] == 'passed'
        missing = got['status'] in ('draft', 'unavailable')
        return {'path': path, 'question': question, 'ok': ok, 'status': got['status'], 'runtime': got['runtime'],
                'what': came_back(ans),
                'layer': '' if ok else f"no truth ({got['status']}): {got['why']}" if missing else layer(ans),
                'mode': (ans.get('answer_mode'), ans.get('answered_by')),
                'sql': None if ok else ans.get('sql'), 'difference': '' if ok else golden20.difference(ans.get('sql'), item['reference_sql'])}
    out = [run('rules', rules_answer(eng, question) if runtimes is None else eng.answer(question, WHO, runtimes=runtimes))]
    model = for_question(eng, question, WHO, 'treatment')
    if model.chat.recordings:
        out.append(run('replayed model', runtrace.run('agent', lambda: Agent(eng, llm=model).ask(question, WHO, arm='treatment',
                                                                                               runtimes=runtimes),
                                                      graph=eng.g)))
    return out


def marked(ids):
    return [pytest.param(i, marks=pytest.mark.xfail(strict=True, raises=AssertionError, reason=EXPECTED[i])) if i in EXPECTED else i for i in ids]


@pytest.fixture(scope='module', autouse=True)
def report(request):
    """The gate's score and its failures, printed when the module ends."""
    yield
    terminal = request.config.pluginmanager.get_plugin('terminalreporter')
    capture = request.config.pluginmanager.get_plugin('capturemanager')
    with capture.global_and_fixture_disabled() if capture else contextlib.nullcontext():
        summary(terminal.write_line if terminal else print)


def summary(write) -> None:
    for part, title in (('asked', 'answer correctly'), ('heldout', 'on held-out phrasings')):
        runs = RESULTS[part]
        if not runs:
            continue
        passed = sorted(i for i, r in runs.items() if all(x['ok'] for x in r))
        asked = [x for r in runs.values() for x in r]
        write('')
        write(f"golden20: {len(passed)} of {len(ITEMS)} {title} ({sum(x['ok'] for x in asked)} of {len(asked)} phrasings and paths)")
        for item_id in sorted(runs):
            failed = [x for x in runs[item_id] if not x['ok']]
            if not failed:
                continue
            write(f"  {item_id}: {len(failed)} of {len(runs[item_id])} phrasings and paths fail")
            for x in failed:
                write(f"    [{x['path']} on {x['runtime']}] {x['question']!r}\n      came back {x['what']}\n      layer {x['layer']}; "
                      f"SQL {x['difference']}")
                if x['sql']:
                    write(f"      Ask's SQL: {x['sql']}")
            write(f"    reference SQL: {ITEMS[item_id]['reference_sql']}")


def asked(eng, item_id: str, questions: list[str]) -> list[dict]:
    item = ITEMS[item_id]
    return [r for q in questions for r in ask(eng, item, q)]


def failures(runs: list[dict]) -> str:
    return '\n'.join(f"{x['question']!r} on {x['path']} ({x['runtime']}, {x['status']}): {x['what']} ({x['layer']}; {x['difference']})"
                     for x in runs if not x['ok'])


@pytest.mark.parametrize('item_id', marked(sorted(ITEMS)))
def test_each_golden_item_answers_as_its_reference_sql_on_every_phrasing(eng, item_id):
    runs = asked(eng, item_id, ITEMS[item_id]['phrasings'])
    RESULTS['asked'][item_id] = runs
    assert all(x['ok'] for x in runs), failures(runs)
    assert all(x['mode'] == ('certified', 'rules') for x in runs), [(x['question'], x['path'], x['mode']) for x in runs]


@pytest.mark.parametrize('item_id', marked(sorted(ITEMS)))
def test_each_golden_item_answers_its_heldout_phrasings_too(eng, item_id):
    heldout = heldout_phrasings()
    if not heldout:
        pytest.skip(f'no held-out phrasings: {HELDOUT} names none')
    assert heldout.get(item_id), f'{item_id} has no held-out phrasing in {os.environ[HELDOUT]}'
    runs = asked(eng, item_id, [*ITEMS[item_id]['phrasings'], *heldout[item_id]])
    RESULTS['heldout'][item_id] = runs
    assert all(x['ok'] for x in runs), failures(runs)


def statements(eng, question: str) -> list:
    """The statements the rules answer to question runs, rebuilt from the plan it checked: the release lookup, where
    the plan needs one, and the answer's own, which is the SQL the answer carries where it carries one, so the sweep
    checks the statement Ask runs."""
    answer = rules_answer(eng, question)
    assert answer.get('behaviour') in ('figure', 'combined'), (question, answer.get('reason'))
    turn = answer['history'][-1]
    checked = check_plan(QueryPlan.model_validate(turn['plan']), vocabulary(eng, question), question, turn['turn_id'])
    assert isinstance(checked, CheckedPlan), (question, checked)
    pending, out = compile_plan(checked), []
    if pending.release is not None:
        out.append(pending.release)
        pending = compile_plan(checked, eng.w.rows(pending.release.units[0], pending.release.sql, AUTHORED))
    if isinstance(answer.get('sql'), str):
        assert pending.answer.sql == answer['sql'], (question, pending.answer.sql, answer['sql'])
    return [*out, pending.answer]


def bigquery_runtimes(statement) -> RuntimeManager:
    """A named BigQuery runtime for each IMPLEMENTS mapping in MAPPINGS, over every table the statement may read."""
    schema, unit = statement.allow.schema, statement.units[0]
    manager = RuntimeManager(SimpleNamespace(units=list(statement.units), curated=[]))
    manager.ref_table_units = {table.lower(): unit for table in schema}
    manager.ref_columns = {(table.lower(), column.lower()): 'VARCHAR' for table, columns in schema.items() for column in columns}
    manager.rt_tables = {(name, table.lower()): {'table': tables(table)} for name, (tables, _) in MAPPINGS.items() for table in schema}
    manager.rt_columns = {(name, table.lower()): {column.lower(): {'column': columns(column)} for column in schema[table]}
                          for name, (_, columns) in MAPPINGS.items() for table in schema}
    manager.runtimes = {name: {'dialect': 'bigquery'} for name in MAPPINGS}
    return manager


def bigquery_problems(statement) -> list[str]:
    """What the gate finds in the statement as each BigQuery runtime writes it, and any checked scope whose output
    shape the runtime did not carry."""
    manager, problems = bigquery_runtimes(statement), []
    for name in MAPPINGS:
        sql, allow, _ = manager._rename_sql_for_runtime(statement.sql, statement.units[0], name, statement.allow)
        problems += [f'{name}: {problem}' for problem in check(manager._render_for_dialect(sql, 'bigquery'), allow, dialect='bigquery')]
        if isinstance(statement.allow, CheckedAllowlist) and set(allow.output_shapes) != set(statement.allow.output_shapes):
            problems.append(f'{name}: output shapes {sorted(allow.output_shapes)} for {sorted(statement.allow.output_shapes)}')
    return problems


@pytest.mark.parametrize('item_id', sorted(ITEMS))
def test_each_golden_items_answer_sql_passes_the_gate_as_a_bigquery_runtime_reads_it_on_every_phrasing(eng, item_id):
    if item_id in EXPECTED:
        pytest.skip(f'{item_id} does not answer yet: {EXPECTED[item_id]}')
    refused = {question: problems for question in ITEMS[item_id]['phrasings']
               for problems in [[p for statement in statements(eng, question) for p in bigquery_problems(statement)]] if problems}
    assert not refused, refused


def test_the_baseline_is_what_the_reference_sql_gives_on_the_sample_now(eng):
    """The stored snapshot's consistency check: make golden20-baseline wrote it; a changed reference SQL or sample
    without a new baseline fails here. The gate's truth is judge's, not this file."""
    assert golden20.text(eng, warehouses(BUILD, 'meridian')) == golden20.BASELINE.read_text(), \
        'clients/meridian/baselines/golden20.json is stale: run make golden20-baseline'


def test_the_set_holds_the_twenty_items_each_with_three_phrasings_and_an_approved_reference():
    raw = {i['id']: i for i in json.loads((BUILD / 'sage_v1.json').read_text())['items']}
    baseline = json.loads(golden20.BASELINE.read_text())['items']
    assert sorted(ITEMS) == [f'S{n:02d}' for n in range(6, 26)] == sorted(baseline)
    for item_id in ITEMS:
        assert len(raw[item_id]['also_asked_as']) + len(raw[item_id].get('added_phrasings', [])) >= 3, item_id
        assert raw[item_id]['reference_sql_approved_by'] == 'owner' and raw[item_id]['reference_sql_approved_on'] == '2026-11-14'
        assert raw[item_id]['expect']['table']['columns'] == baseline[item_id]['columns'], item_id
        assert len(raw[item_id]['expect']['table']['rows']) == baseline[item_id]['row_count'] > 0, item_id
    assert set(EXPECTED) <= set(ITEMS)
