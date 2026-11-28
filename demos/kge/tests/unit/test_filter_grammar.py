"""Declared predicates share one grammar on rules and supplied model plans."""
import copy
import datetime as dt
from types import SimpleNamespace

import pytest

from kge.pipeline.sqlguard import check
from kge.semantics.compile import compile_plan
from kge.semantics.coverage import ground_plan, ledger
from kge.semantics.filtergrammar import preserved_paths
from kge.semantics.plan import Clarify, Propose, QueryPlan
from kge.semantics.plancheck import CheckedPlan, check_plan
from kge.semantics.vocabulary import compiled
from kge.services.ask.engine import planrules
from kge.services.ask.engine.question import parse
from tests.plan_fixtures import semantics, metric_vocabulary as vocabulary
from tests.unit.test_in_filters import PREDICATES
from tests.unit.test_plan_compiler import connection

ROWS = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 4, 11), 7, 'B', 2, 'x'),
        (dt.date(2025, 4, 12), 5, 'C', 3, 'y'), (dt.date(2025, 4, 13), 3, None, 4, 'y')]


def setup(monkeypatch, extra=()):
    sem = semantics()
    sem.measure_declarations = {}
    sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}
    sem.d.VOCABULARY = [*PREDICATES, dict(kind='predicate', params=dict(phrases=['second entity'], column='u.events.entity_id', op='eq', value=2)), *extra]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem, aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    return engine, v


def proposed(monkeypatch, text, extra=()):
    engine, v = setup(monkeypatch, extra)
    reply = planrules.read(engine, parse(text, engine.term_names, engine), v, SimpleNamespace(graph=True, glossary=True))
    return reply, v


def execute(monkeypatch, text, expected, extra=()):
    reply, v = proposed(monkeypatch, text, extra)
    assert isinstance(reply, Propose), reply
    for plan in (reply.plan, ground_plan(reply.plan.model_copy(update={'obligations': ()}), text, 'turn', v)):
        checked = check_plan(plan, v, text)
        assert isinstance(checked, CheckedPlan), (checked, ledger(text, 'turn', v))
        compiled = compile_plan(checked)
        assert check(compiled.answer.sql, compiled.answer.allow) == []
        with connection(ROWS) as con:
            row = con.execute(compiled.answer.sql).fetchone()
            assert (row[0] if row else None) == expected
    return reply.plan, v


@pytest.mark.parametrize('word', ['excluding', 'except', 'other than', 'not in'])
@pytest.mark.parametrize('values,expected', [('B category', 18), ('A and B categories', 8), ('A category or C category', 10)])
def test_excluding_declared_values_keeps_null_rows(monkeypatch, word, values, expected):
    plan, _ = execute(monkeypatch, f'Metric {word} {values}', expected)
    pred, = plan.filters
    assert pred.kind == 'any'
    assert {p.kind for p in pred.children} == {'not', 'null'}


def test_named_not_in_group_has_the_same_null_policy(monkeypatch):
    extra = [dict(kind='predicate', params=dict(phrases=['outside early categories'], column='u.events.category', op='not_in', values=['A', 'B']))]
    execute(monkeypatch, 'Metric for outside early categories', 8, extra)


@pytest.mark.parametrize('question,expected', [
    ('Metric for A category or x source', 17),
    ('Metric for A category and x source', 10),
    ('Metric for A category x source', 10),
    ('Metric for (A category or x source) and second entity', 7),
    ('Metric for A category or (x source and second entity)', 17),
])
def test_boolean_filters_union_rows_once_and_respect_parentheses(monkeypatch, question, expected):
    execute(monkeypatch, question, expected)


@pytest.mark.parametrize('question', ['Metric for A category or x source and second entity', 'Metric for A category and x source or second entity'])
def test_mixed_joining_words_need_grouping(monkeypatch, question):
    reply, _ = proposed(monkeypatch, question)
    assert isinstance(reply, Clarify), reply


@pytest.mark.parametrize('op,value,expected', [('eq', 7, 7), ('ne', 7, 18), ('lt', 7, 8), ('le', 7, 15), ('gt', 7, 10), ('ge', 7, 17),
                                             ('is_null', None, 3), ('is_not_null', None, 22)])
def test_every_scalar_pack_operator_is_reachable(monkeypatch, op, value, expected):
    params = dict(phrases=['chosen rows'], column='u.events.category' if op.startswith('is_') else 'u.events.value', op=op)
    if value is not None:
        params['value'] = value
    execute(monkeypatch, 'Metric for chosen rows', expected, [dict(kind='predicate', params=params)])


def test_a_declared_name_owns_its_exclusion_word(monkeypatch):
    engine, v = setup(monkeypatch)
    text = 'Metric excluding B category'
    engine.term_names = [text]
    object.__setattr__(v.semantics.contexts['ctx'], 'term', text)
    v = vocabulary(v.semantics, aliases=[(text, 'ctx')])
    assert parse(text, engine.term_names, engine).filters == []
    assert not [p for p in ledger(text, 'turn', v) if p.kind == 'predicate']


@pytest.mark.parametrize('mutation', ['drop_null', 'wrong_null_column', 'positive', 'wrong_value'])
def test_model_cannot_change_exclusion_semantics(monkeypatch, mutation):
    text = 'Metric excluding B category'
    plan, v = execute(monkeypatch, text, 18)
    data = copy.deepcopy(plan.model_dump(mode='json', by_alias=True))
    root = data['filters'][0]
    if mutation == 'drop_null':
        data['filters'] = [root['children'][0]]
    elif mutation == 'positive':
        data['filters'] = [root['children'][0]['child']]
    elif mutation == 'wrong_null_column':
        root['children'][1]['left']['name']['id'] = 'u.events.source'
    else:
        root['children'][0]['child']['values'][0]['value'] = 'A'
    data['obligations'] = []
    wrong = ground_plan(QueryPlan.model_validate(data), text, 'turn', v)
    assert not isinstance(check_plan(wrong, v, text), CheckedPlan)


@pytest.mark.parametrize('question', ['Metric for A category or x source', 'Metric for A category and x source'])
def test_model_cannot_swap_boolean_connectors(monkeypatch, question):
    plan, v = execute(monkeypatch, question, 17 if ' or ' in question else 10)
    data = plan.model_dump(mode='json', by_alias=True)
    data['filters'][0]['kind'] = 'all' if ' or ' in question else 'any'
    wrong = ground_plan(QueryPlan.model_validate(data), question, 'turn', v)
    assert not isinstance(check_plan(wrong, v, question), CheckedPlan)


@pytest.mark.parametrize('word,expected', [('equal to', None), ('not equal to', 25), ('above', 25), ('below', None), ('at most', None), ('at least', 25)])
def test_numeric_output_comparisons_share_one_rule(monkeypatch, word, expected):
    execute(monkeypatch, f'Metric {word} $20', expected)


def test_boolean_pack_flag_runs_on_rules_and_model_plans(monkeypatch):
    engine, _ = setup(monkeypatch)
    sem = engine.semantics
    sem.tables['u.events']['source'] = sem.types['u.events.source'] = 'BOOLEAN'
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(phrases=['selected flags'], column='u.events.source', op='is_true'))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    engine.FILTERS, engine.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem, aliases=[('Metric', 'ctx')])
    text = 'Metric for selected flags'
    reply = planrules.read(engine, parse(text, ['Metric'], engine), v, SimpleNamespace(graph=True, glossary=True))
    assert reply.kind == 'propose', reply
    for plan in (reply.plan, ground_plan(reply.plan.model_copy(update={'obligations': ()}), text, 'turn', v)):
        checked = check_plan(plan, v, text)
        assert isinstance(checked, CheckedPlan), checked
        output = compile_plan(checked)
        assert check(output.answer.sql, output.answer.allow) == []
        rows = [(when, value, category, entity, entity <= 2) for when, value, category, entity, _ in ROWS]
        with connection(rows) as con:
            con.execute('ALTER TABLE events ALTER source TYPE BOOLEAN')
            assert con.execute(output.answer.sql).fetchone()[0] == 17


def test_model_cannot_claim_a_second_unrequested_value(monkeypatch):
    from tests.plan_fixtures import grounded, model, name
    _, v = setup(monkeypatch)
    def pred(identifier, value):
        return dict(kind='compare', id=identifier, op='eq', left=dict(kind='column', name=name('u.events.category'), path=[]),
                    value=dict(kind='text', value=value), evidence=[])
    text = 'Metric for A category'
    plan = grounded(model(filters=[pred('a', 'A'), pred('b', 'B')]), text, v, {'A category': ['a', 'b']})
    assert not isinstance(check_plan(plan, v, text), CheckedPlan)


@pytest.mark.parametrize('joiner', ['with', 'and for'])
def test_model_cannot_or_filters_with_an_implicit_conjunction(monkeypatch, joiner):
    from tests.plan_fixtures import model, name
    _, v = setup(monkeypatch)
    def pred(identifier, column, value):
        return dict(kind='compare', id=identifier, op='eq', left=dict(kind='column', name=name('u.events.' + column), path=[]),
                    value=dict(kind='text', value=value), evidence=[])
    text = f'Metric for A category {joiner} x source'
    plan = model(filters=[dict(kind='any', id='j', children=[pred('a', 'category', 'A'), pred('x', 'source', 'x')], evidence=[])])
    assert not isinstance(check_plan(ground_plan(plan, text, 'turn', v), v, text), CheckedPlan)


def test_exclusion_preserves_rows_missing_an_optional_role(monkeypatch):
    sem = semantics(joined=True)
    sem.measure_declarations, sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}, {}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(phrases=[f'{value} group'], column='u.members.grouping', op='eq', value=value))
                        for value in ('G', 'H')]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    v = vocabulary(sem, aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    for text, expected in [('Metric excluding G group', 8), ('Metric excluding G and H groups', 3)]:
        reply = planrules.read(engine, parse(text, ['Metric'], engine), v, SimpleNamespace(graph=True, glossary=True))
        for plan in (reply.plan, ground_plan(reply.plan.model_copy(update={'obligations': ()}), text, 'turn', v)):
            checked = check_plan(plan, v, text)
            assert isinstance(checked, CheckedPlan), checked
            output = compile_plan(checked)
            assert check(output.answer.sql, output.answer.allow) == []
            assert 'LEFT JOIN' in output.answer.sql
            with connection(ROWS) as con:
                con.execute('CREATE TABLE members(entity_id INTEGER, source VARCHAR, grouping VARCHAR)')
                con.execute("INSERT INTO members VALUES (1, 'x', 'G'), (2, 'x', 'G'), (3, 'y', 'H')")
                assert con.execute(output.answer.sql).fetchone()[0] == expected


def test_positive_null_filter_keeps_the_existing_inner_join_population(monkeypatch):
    sem = semantics(joined=True)
    sem.measure_declarations, sem.d.ALIASES, sem.d.UNIT_WORDS = {}, {}, {}
    sem.d.VOCABULARY = [dict(kind='predicate', params=dict(phrases=['missing group'], column='u.members.grouping', op='is_null'))]
    words = compiled(sem.d.VOCABULARY)
    sem.d.FILTERS, sem.d.FILTER_COLUMNS = words.filters, words.columns
    vocab = vocabulary(sem, aliases=[('Metric', 'ctx')])
    monkeypatch.setattr(planrules, 'snapshot', lambda *a, **k: sem)
    engine = SimpleNamespace(**vars(sem.d), semantics=sem, term_names=['Metric'])
    text = 'Metric for missing group'
    reply = planrules.read(engine, parse(text, ['Metric'], engine), vocab, SimpleNamespace(graph=True, glossary=True))
    assert isinstance(reply, Propose), reply
    rows = [(dt.date(2025, 4, 10), 10, 'A', 1, 'x'), (dt.date(2025, 4, 10), 100, 'A', 2, 'x')]
    for plan in (reply.plan, ground_plan(reply.plan.model_copy(update={'obligations': ()}), text, 'turn', vocab)):
        checked = check_plan(plan, vocab, text)
        assert isinstance(checked, CheckedPlan), checked
        output = compile_plan(checked).answer
        assert check(output.sql, output.allow) == []
        with connection(rows) as con:
            con.execute('CREATE TABLE members(entity_id INTEGER, source VARCHAR, grouping VARCHAR)')
            con.execute("INSERT INTO members VALUES (1, 'x', NULL)")
            assert con.execute(output.sql).fetchone()[0] == 10


@pytest.mark.parametrize('shape,expected', [('null', set()), ('any', set()), ('not_in', set()),
                                          ('exclusion', {'member_join'}), ('nested_exclusion', {'member_join'})])
def test_only_the_exact_null_keeping_exclusion_preserves_optional_paths(shape, expected):
    from tests.plan_fixtures import model, name
    operand = dict(kind='column', name=name('u.members.grouping'), path=[name('member_join')])
    member = dict(kind='in', id='in', left=operand, values=[dict(kind='text', value='G')], evidence=[])
    empty = dict(kind='null', id='null', left=operand, is_null=True, evidence=[])
    negated = dict(kind='not', id='not', child=member, evidence=[])
    exclusion = dict(kind='any', id='excluded', children=[negated, empty], evidence=[])
    predicate = {'null': empty, 'any': dict(kind='any', id='any', children=[member, empty], evidence=[]),
                 'not_in': negated, 'exclusion': exclusion,
                 'nested_exclusion': dict(kind='all', id='all', children=[exclusion], evidence=[])}[shape]
    assert preserved_paths(model(filters=[predicate]).filters) == expected


@pytest.mark.parametrize('op,expected', [('eq', 7), ('ne', 15), ('lt', 10), ('le', 17), ('gt', 5), ('ge', 12)])
def test_declared_text_comparisons_share_the_scalar_path(monkeypatch, op, expected):
    params = dict(phrases=['chosen category bound'], column='u.events.category', op=op, value='B')
    execute(monkeypatch, 'Metric for chosen category bound', expected, [dict(kind='predicate', params=params)])


@pytest.mark.parametrize('word', ['Excluding', 'EXCEPT', 'Other Than', 'NOT IN'])
def test_exclusion_cues_are_case_insensitive(monkeypatch, word):
    execute(monkeypatch, f'Metric {word} B category', 18)


@pytest.mark.parametrize('filters,expected', [
    ('( ( A category or x source ) and second entity )', 7),
    ('(A category with x source) or second entity', 17),
    ('(A category and for x source) or second entity', 17),
])
def test_scaffold_connectors_and_spaced_parentheses_share_the_boolean_grammar(monkeypatch, filters, expected):
    execute(monkeypatch, 'Metric for ' + filters, expected)


@pytest.mark.parametrize('text', ['(Metric for A category)', 'Metric (for A category)', 'Metric (for A category for April 2025)',
                                'Metric for A category (for April 2025)'])
def test_parentheses_around_a_whole_clause_do_not_change_an_answer(monkeypatch, text):
    execute(monkeypatch, text, 10)


def test_a_value_and_its_declared_phrase_do_not_add_an_extra_and_outside_or(monkeypatch):
    values = dict(kind='value', params=dict(column='u.events.category', values=['A', 'B', 'C']))
    execute(monkeypatch, 'Metric for A category or x source', 17, [values])


@pytest.mark.parametrize('text', ['Metric not excluding B category', 'Metric excluding B category or', 'Metric excluding B category is null'])
def test_implicit_null_policy_does_not_license_extra_structural_words(monkeypatch, text):
    reply, v = proposed(monkeypatch, text)
    assert reply.kind != 'propose' or not isinstance(check_plan(reply.plan, v, text), CheckedPlan)


@pytest.mark.parametrize('words,op,expected', [('is not equal to', 'ne', 15), ('is below', 'lt', 10), ('is at most', 'le', 17),
                                               ('is above', 'gt', 5), ('is at least', 'ge', 12), ('is equal to', 'eq', 7)])
def test_named_column_comparison_binds_each_operator(monkeypatch, words, op, expected):
    """A column the question names, a comparison word and a declared value read as one filter on that column, on the
    rules plan and on a grounded model plan, with the figure the same pack phrase gives; text orders as text."""
    plan, _ = execute(monkeypatch, f'Metric where category {words} B', expected)
    node, = plan.filters
    assert (node.kind, node.op, node.left.name.id, node.value.value) == ('compare', op, 'u.events.category', 'B')


def test_column_is_not_null_binds_not_null(monkeypatch):
    plan, _ = execute(monkeypatch, 'Metric where category is not null', 22)
    node, = plan.filters
    assert (node.kind, node.is_null, node.left.name.id) == ('null', False, 'u.events.category')


def test_undeclared_value_after_comparison_refuses(monkeypatch):
    """The words are owned, so the refusal names the value the pack does not declare, not the words."""
    text = 'Metric where category is not equal to G'
    reply, v = proposed(monkeypatch, text)
    checked = check_plan(reply.plan, v, text) if reply.kind == 'propose' else reply
    assert not isinstance(checked, CheckedPlan)
    assert checked.missing == ('undeclared member value',)


def test_unnamed_column_comparison_stays_word_check(monkeypatch):
    text = 'Metric where is not equal to B'
    reply, v = proposed(monkeypatch, text)
    checked = check_plan(reply.plan, v, text) if reply.kind == 'propose' else reply
    assert not isinstance(checked, CheckedPlan)
    assert 'not equal to' in checked.missing


@pytest.mark.parametrize('words,op,expected', [('is not equal to', 'ne', 15), ('is below', 'lt', 10), ('is at least', 'ge', 12)])
def test_comparison_takes_a_member_the_pack_reads_alone_as_its_equality(monkeypatch, words, op, expected):
    """A value the pack also reads on its own as the named column's equality ('B' alone means category = B) is the
    member the comparison compares with, not a second filter: one compare node, the comparison's operator."""
    bare = dict(kind='predicate', params=dict(phrases=['B'], column='u.events.category', op='eq', value='B'))
    plan, _ = execute(monkeypatch, f'Metric where category {words} B', expected, extra=(bare,))
    node, = plan.filters
    assert (node.kind, node.op, node.left.name.id, node.value.value) == ('compare', op, 'u.events.category', 'B')
