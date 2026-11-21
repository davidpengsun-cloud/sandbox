"""Concept wordings share one declared target and the existing Alias semantics."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import duckdb
import pytest

from kge.compiler import checks, emit
from kge.compiler.pack import Pack
from kge.compiler.vocabulary import alias_declarations, alias_entries
from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ledger, ground_plan
from kge.semantics.plan import Propose
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.planvocabulary import build_vocabulary, MeasureSpec
from kge.semantics.query import Rule
from kge.services.ask.engine import planmodel, planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary as vocabulary
from tests.unit.test_measure_capability_figures import ROWS
from tests.unit.test_plan_compiler import connection


def pack(tmp_path, concepts=(), aliases=()):
    return Pack("example", tmp_path, data={
        "glossary": [{"id": "metric", "name": "Metric"}], "sources": [{"id": "u"}],
        "entities": [{"id": "entity"}], "manifest": {"meta_model_version": "1.5.0"},
        "terminology": {"aliases": list(aliases), "concepts": deepcopy(list(concepts))}},
        lock={"sources": {"u": {"tables": [{"name": "events", "columns": [{"name": "category"}]}]}}})


def test_one_concept_compiles_every_wording_to_the_same_alias_target(tmp_path):
    p = pack(tmp_path, [dict(id="past_basis", time_mode="as_reported", wordings=["recorded basis", "saved basis"])])
    assert checks.aliases(p) == []
    assert alias_entries(p.data["terminology"]) == [
        {"phrase": "recorded basis", "time_mode": "as_reported"},
        {"phrase": "saved basis", "time_mode": "as_reported"}]
    rows = emit.NODES["Alias"](p.data, p.lock)
    assert [(r["phrase"], r["kind"], r["target"], r["ordinal"]) for r in rows] == [
        ("recorded basis", "time_mode", "as_reported", 1), ("saved basis", "time_mode", "as_reported", 2)]


def test_qualified_terms_expand_every_wording_without_changing_the_concept(tmp_path):
    p = pack(tmp_path, [dict(id='basis', time_mode='as_reported', wordings=['stored', 'published'],
                            qualified_terms=[dict(noun='Metric', term='metric')])])
    before = deepcopy(p.data)
    assert checks.aliases(p) == []
    assert not [finding for finding in checks.sections(p) if 'terminology.concepts' in finding.path]
    expanded = alias_entries(p.data['terminology'])
    assert expanded == [{'phrase': 'stored', 'time_mode': 'as_reported'}, {'phrase': 'published', 'time_mode': 'as_reported'},
                        {'phrase': 'stored Metric', 'term': 'metric'}, {'phrase': 'published Metric', 'term': 'metric'}]
    assert emit.NODES['Alias'](p.data, p.lock) == emit.NODES['Alias']({**p.data, 'terminology': {'aliases': expanded}}, p.lock)
    assert emit.alias_edges(p.data, p.lock) == emit.alias_edges({**p.data, 'terminology': {'aliases': expanded}}, p.lock)
    assert p.data == before


@pytest.mark.parametrize('qualified,field,message', [
    ({'noun': '', 'term': 'metric'}, 'noun', 'non-empty noun'),
    ({'noun': '  ', 'term': 'metric'}, 'noun', 'non-empty noun'),
    ({'noun': 'Metric', 'term': 'missing'}, 'term', 'glossary declares'),
])
def test_qualified_terms_require_a_noun_and_a_declared_term(tmp_path, qualified, field, message):
    p = pack(tmp_path, [dict(id='basis', time_mode='as_reported', wordings=['stored', 'published'], qualified_terms=[qualified])])
    finding, = checks.aliases(p)
    assert finding.path.endswith('qualified_terms[0].' + field) and message in finding.message


@pytest.mark.parametrize('target', [{'term': 'metric'}, {'time_mode': 'restated'}])
def test_generated_qualified_terms_cannot_duplicate_a_direct_alias(tmp_path, target):
    p = pack(tmp_path, [dict(id='basis', time_mode='as_reported', wordings=['stored'],
                            qualified_terms=[dict(noun='Metric', term='metric')])],
             [dict(phrase='Stored Metric', **target)])
    finding, = checks.aliases(p)
    assert finding.path.endswith('qualified_terms[0].noun')
    assert ('is listed already' if 'term' in target else 'names term metric here') in finding.message


@pytest.mark.parametrize('other', ['metric', 'other'])
def test_generated_qualified_terms_cannot_duplicate_another_qualified_row(tmp_path, other):
    p = pack(tmp_path, [dict(id='basis', time_mode='as_reported', wordings=['stored'], qualified_terms=[
        dict(noun='Metric', term='metric'), dict(noun='metric', term=other)])])
    p.data['glossary'].append(dict(id='other', name='Other metric'))
    finding, = checks.aliases(p)
    assert finding.path.endswith('qualified_terms[1].noun')
    assert ('is listed already' if other == 'metric' else 'names term other here') in finding.message


@pytest.mark.parametrize('target', [{'term': 'metric'}, {'time_mode': 'restated'}])
def test_generated_qualified_terms_cannot_duplicate_another_concepts_wording(tmp_path, target):
    p = pack(tmp_path, [dict(id='basis', time_mode='as_reported', wordings=['stored'],
                            qualified_terms=[dict(noun='Metric', term='metric')]),
                        dict(id='other', wordings=['STORED Metric'], **target)])
    finding, = checks.aliases(p)
    assert finding.path.endswith('terminology.concepts[1].wordings[0]')
    assert ('is listed already' if 'term' in target else 'names time_mode restated here') in finding.message


@pytest.mark.parametrize("target", [dict(term="metric"), dict(source="u"), dict(candidate="Candidate metric"),
                                   dict(group_by="u.events.category"), dict(column="u.events.category"),
                                   dict(entity="entity"), dict(time_mode="as_reported"), dict(period="latest"),
                                   dict(sources=["u"]), dict(column_wording="u.events.category")])
def test_the_concept_construct_reuses_every_alias_target_kind(tmp_path, target):
    p = pack(tmp_path, [dict(id="choice", wordings=["first wording", "second wording"], **target)])
    assert checks.aliases(p) == []
    expanded = alias_entries(p.data["terminology"])
    assert expanded == [dict(phrase=w, **target) for w in p.data["terminology"]["concepts"][0]["wordings"]]
    assert emit.NODES["Alias"](p.data, p.lock) == emit.NODES["Alias"]({
        **p.data, "terminology": {"aliases": expanded}}, p.lock)
    assert emit.alias_edges(p.data, p.lock) == emit.alias_edges({**p.data, "terminology": {"aliases": expanded}}, p.lock)


@pytest.mark.parametrize("wordings", [["recorded", "recorded"], ["recorded", " RECORDED "]])
def test_check_pack_refuses_a_wording_declared_twice_in_one_concept(tmp_path, wordings):
    p = pack(tmp_path, [dict(id="basis", time_mode="as_reported", wordings=wordings)])
    finding, = checks.aliases(p)
    assert finding.rule == "S11" and finding.path.endswith("terminology.concepts[0].wordings[1]")
    assert "is listed already" in finding.message


@pytest.mark.parametrize("other", ["as_reported", "restated"])
def test_check_pack_refuses_a_wording_in_two_concepts_even_when_targets_agree(tmp_path, other):
    p = pack(tmp_path, [dict(id="first", time_mode="as_reported", wordings=["recorded"]),
                        dict(id="second", time_mode=other, wordings=["Recorded"])])
    finding, = checks.aliases(p)
    assert finding.rule == "S11" and finding.path.endswith("terminology.concepts[1].wordings[0]")
    assert ("is listed already" if other == "as_reported" else "names time_mode restated here") in finding.message


def test_check_pack_refuses_a_concept_wording_also_declared_as_an_alias(tmp_path):
    p = pack(tmp_path, [dict(id="basis", time_mode="as_reported", wordings=["recorded"])],
             [dict(phrase="recorded", time_mode="as_reported")])
    finding, = checks.aliases(p)
    assert "terminology.aliases[0]" in finding.message


@pytest.mark.parametrize("wordings", [[], ["  "]])
def test_check_pack_requires_nonempty_concept_wordings(tmp_path, wordings):
    p = pack(tmp_path, [dict(id="basis", time_mode="as_reported", wordings=wordings)])
    assert checks.aliases(p)


@pytest.mark.parametrize("target", [{}, dict(term="metric", time_mode="as_reported")])
def test_the_concept_shape_requires_exactly_one_target(tmp_path, target):
    p = pack(tmp_path, [dict(id="basis", wordings=["wording"], **target)])
    findings = [f for f in checks.sections(p) if f.path.endswith("terminology.concepts[0]")]
    assert len(findings) == 1 and findings[0].rule == "S01"


def test_concept_ids_are_unique_and_the_declaration_is_never_mutated(tmp_path):
    p = pack(tmp_path, [dict(id="basis", wordings=["first"], time_mode="as_reported"),
                        dict(id="basis", wordings=["second"], time_mode="restated")])
    before = deepcopy(p.data)
    finding, = checks.ids(p, held=("terminology",))
    assert finding.path.endswith("terminology.concepts[1].id")
    assert list(alias_declarations(p.data["terminology"]))
    assert p.data == before


def without_evidence(value):
    if isinstance(value, dict):
        return {k: without_evidence(v) for k, v in value.items() if k not in ("evidence", "obligations")}
    if isinstance(value, list):
        return [without_evidence(v) for v in value]
    return value


class SuppliedPlan:
    def __init__(self, plan):
        self.plan = plan

    def json(self, prompt):
        return Propose(kind="propose", base_turn=None, plan=self.plan).model_dump(mode="json", by_alias=True)


def test_arbitrary_pack_wordings_give_identical_rules_and_model_plans_and_hand_computed_figures(monkeypatch):
    concepts = [dict(id="past_basis", time_mode="as_reported", wordings=["saved basis", "recorded basis", "preserved basis"]),
                dict(id="present_basis", time_mode="restated", wordings=["live basis", "revised basis"])]
    rows = alias_entries({"aliases": [], "concepts": concepts})
    sem = semantics(release=True)
    sem.tables["u.events"].update(since="DATE", until="DATE", active="BOOLEAN")
    sem.types.update({"u.events." + c: kind for c, kind in sem.tables["u.events"].items()})
    sem.scd2["u.events"] = dict(valid_from="since", valid_to="until", is_current="active", valid_to_inclusive=False)
    sem.d.ALIASES, sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}, {}
    sem.d.TIME_MODES = {a["phrase"]: a["time_mode"] for a in rows}
    sem.measure_declarations = {}
    vocab = vocabulary(sem, aliases=[("Metric", "ctx")])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=["Metric"])
    monkeypatch.setattr(planrules, "snapshot", lambda *args, **kwargs: sem)
    with duckdb.connect(":memory:") as con:
        con.execute('CREATE TABLE events(occurred DATE, value DECIMAL(18,2), category VARCHAR, entity_id INT, '
                    'source VARCHAR, private VARCHAR, since DATE, until DATE, active BOOLEAN)')
        con.execute("INSERT INTO events VALUES ('2025-06-10', 10, 'A', 1, 'x', NULL, '2025-01-01', '2025-06-20', FALSE), "
                    "('2025-06-10', 20, 'A', 1, 'x', NULL, '2025-06-20', NULL, TRUE), "
                    "('2025-06-10', 5, 'B', 2, 'x', NULL, '2025-01-01', NULL, TRUE)")
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-06-30', '2025-06-15', '2025-06-15', TRUE)")
        for concept in concepts:
            answers = []
            for wording in concept["wordings"]:
                text = "Metric for June 2025 " + wording
                assert not [p.text for p in ledger(text, "turn", vocab) if p.kind == "unknown"]
                proposal = planrules.read(engine, parse(text, engine.term_names, sem.d), vocab,
                                          SimpleNamespace(graph=True, glossary=True))
                assert proposal.kind == "propose", proposal
                replay = planmodel.read(SuppliedPlan(proposal.plan), text, vocab)
                assert replay.kind == "propose", replay
                checked = check_plan(replay.plan, vocab, text)
                assert isinstance(checked, CheckedPlan), checked
                compiled = compile_plan(checked)
                if compiled.release is not None:
                    assert check(compiled.release.sql, compiled.release.allow) == []
                    compiled = compile_plan(checked, con.execute(compiled.release.sql).fetchall())
                assert compiled.answer is not None, compiled
                assert check(compiled.answer.sql, compiled.answer.allow) == []
                figure = con.execute(compiled.answer.sql).fetchone()[0]
                assert figure == (15 if concept["time_mode"] == "as_reported" else 25)
                answers.append((without_evidence(proposal.plan.model_dump(mode="json")), compiled.answer.sql, figure))
            assert all(answer == answers[0] for answer in answers)


def test_qualified_wordings_share_one_plan_wherever_the_wording_sits(monkeypatch):
    concept = dict(id='basis', time_mode='as_reported', wordings=['saved basis', 'recorded basis', 'preserved basis'],
                   qualified_terms=[dict(noun='Metric', term='qualified')])
    rows = alias_entries({'aliases': [], 'concepts': [concept]})
    terms = {'metric': 'Metric', 'qualified': 'Qualified metric'}
    sem = semantics(release=True)
    sem.contexts['qualified'] = replace(sem.contexts['ctx'], grain='day', id='qualified', label='Qualified metric',
        term='Qualified metric', measure='u.qualified_metric',
        filters=(*sem.contexts['ctx'].filters, Rule('category', '', "events.category='A'", 'u.events')))
    sem.d.ALIASES = {row['phrase'].casefold(): terms[row['term']] for row in rows if 'term' in row}
    sem.d.TIME_MODES = {row['phrase'].casefold(): row['time_mode'] for row in rows if 'time_mode' in row}
    sem.d.UNIT_WORDS, sem.d.GROUP_BY = {}, {}
    sem.measure_declarations = {}
    base = vocabulary(sem)
    vocab = build_vocabulary(sem, base.binding, ['u'], ['u'], [MeasureSpec('u.metric', 'USD', 'entities', True),
        MeasureSpec('u.qualified_metric', 'USD', 'entities', True)],
        aliases=[('Metric', 'ctx'), *((row['phrase'], 'qualified') for row in rows if 'term' in row)])
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=list(terms.values()))
    monkeypatch.setattr(planrules, 'snapshot', lambda *args, **kwargs: sem)
    with connection(ROWS) as con:
        con.execute('CREATE TABLE calendar_rows(period DATE, "freeze" DATE, release_date DATE, released BOOLEAN)')
        con.execute("INSERT INTO calendar_rows VALUES ('2025-04-30', '2025-04-15', '2025-04-15', TRUE)")
        def ask(text):
            reply = planrules.read(engine, parse(text, engine.term_names, engine), vocab,
                                   SimpleNamespace(graph=True, glossary=True))
            assert reply.kind == 'propose', reply
            measure, = reply.plan.measures
            checked = check_plan(reply.plan, vocab, text)
            assert isinstance(checked, CheckedPlan), checked
            other = 'ctx' if measure.context.id == 'qualified' else 'qualified'
            swapped = measure.model_copy(update={
                'context': measure.context.model_copy(update={'id': other, 'evidence': ()}),
                'measure': measure.measure.model_copy(update={'id': vocab.contexts[other].measure, 'evidence': ()})})
            changed = reply.plan.model_copy(update={'measures': (swapped,), 'obligations': ()})
            assert not isinstance(check_plan(ground_plan(changed, text, 'turn', vocab), vocab, text), CheckedPlan)
            compiled = compile_plan(checked)
            if compiled.release is not None:
                assert check(compiled.release.sql, compiled.release.allow) == []
                compiled = compile_plan(checked, con.execute(compiled.release.sql).fetchall())
            assert compiled.answer is not None and check(compiled.answer.sql, compiled.answer.allow) == []
            return (measure.context.id, measure.mode, without_evidence(reply.plan.model_dump(mode='json', by_alias=True)),
                    compiled.answer.sql, con.execute(compiled.answer.sql).fetchone()[0])
        canonical = ask('Qualified metric for April 2025')
        assert canonical[:2] == ('qualified', 'current') and canonical[-1] == 6
        for wording in concept['wordings']:
            for text in (wording + ' Metric for April 2025', wording + ' for April 2025 Metric',
                         'Metric for April 2025 ' + wording, 'Metric, ' + wording + ', for April 2025'):
                assert ask(text) == canonical, text
