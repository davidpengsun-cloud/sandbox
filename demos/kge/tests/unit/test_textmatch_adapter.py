"""Authored literal LIKE escapes retain their meaning in the warehouse adapter."""
import pytest
import sqlglot
from sqlglot import exp

from kge.adapters.warehouse.bigquery import rewrite_sql
from kge.pipeline.textmatch import text_match
from kge.pipeline.sqlguard import check


@pytest.mark.parametrize('value', ["O'Brien", r'a\b', 'A!B', '!*?[a]'])
def test_literal_contains_translates_to_native_pattern_escaping(value):
    authored = exp.select(text_match(exp.column('label', table='events'), 'contains', value)).from_('events').sql('duckdb')
    rendered = rewrite_sql(authored, lambda _: '', 'u')
    tree = sqlglot.parse_one(rendered, read='bigquery')
    assert not list(tree.find_all(exp.Escape))
    pattern = tree.find(exp.Like).expression
    assert pattern.is_string
    assert pattern.this == '%' + value.replace('\\', '\\\\') + '%'


def test_starts_with_remains_a_literal_case_sensitive_native_operator():
    value = "O'Brien\\path!"
    authored = exp.select(text_match(exp.column('label', table='events'), 'starts_with', value)).from_('events').sql('duckdb')
    tree = sqlglot.parse_one(rewrite_sql(authored, lambda _: '', 'u'), read='bigquery')
    assert tree.find(exp.StartsWith).expression.this == value


def test_generic_like_escape_translates_literal_wildcards_without_turning_them_into_patterns():
    authored = "SELECT label LIKE '%a!_b!%!\\c%' ESCAPE '!' FROM events"
    tree = sqlglot.parse_one(rewrite_sql(authored, lambda _: '', 'u'), read='bigquery')
    assert tree.find(exp.Like).expression.this == '%a\\_b\\%\\\\c%'


@pytest.mark.parametrize('value', ["O'Brien", r'a\b', 'A!B'])
def test_native_literal_match_preserves_checked_pack_authority(value):
    from tests.unit.test_textband_figures import band_dimension, compiled, declarations, model, text_filter
    predicate = dict(kind='predicate', params=dict(column='u.events.category', phrases=['selected labels'], op='contains', value=value))
    _, vocab = declarations(predicates=[predicate])
    result = compiled(model(filters=[text_filter(value=value)], dimensions=[band_dimension()]),
                      'Metric by size band for selected labels', vocab)
    native = rewrite_sql(result.answer.sql, lambda _: '')
    assert check(native, result.answer.allow, dialect='bigquery') == []


@pytest.mark.parametrize('predicate', ["REGEXP_FULL_MATCH(events.category, '.*A.*')", "STRPOS(events.category, 'A') > 0",
                                      "events.category LIKE '%A%'", "STARTS_WITH(events.category, 'A')"])
def test_gate_refuses_undeclared_text_matching_in_every_function_spelling(predicate):
    from tests.unit.test_textband_figures import compiled, declarations, model
    _, vocab = declarations(bands=False, predicates=[])
    original = compiled(model(), 'Metric', vocab)
    tree = sqlglot.parse_one(original.answer.sql, read='duckdb')
    base = next(s for s in tree.find_all(exp.Select) if any(t.name == 'events' for t in s.find_all(exp.Table)))
    base.where(sqlglot.parse_one(predicate, read='duckdb'), append=True, copy=False)
    assert check(tree.sql('duckdb'), original.answer.allow)


@pytest.mark.parametrize('identifier', ['u.events.value', 'u.metric', 'ctx', 'cal', 'entity', 'u'])
def test_band_id_cannot_hide_an_existing_vocabulary_entry(identifier):
    from tests.unit.test_textband_figures import declaration_pack
    from kge.compiler.checks import banded_dimensions
    pack = declaration_pack()
    pack.data.update(measures=[{'id': 'u.metric'}], contexts=[{'id': 'ctx'}], calendars=[{'id': 'cal'}],
                     entities=[{'id': 'entity'}], sources=[{'id': 'u'}])
    pack.data['banded_dimensions'][0]['id'] = identifier
    assert any(f.path.endswith('.id') for f in banded_dimensions(pack))


def test_removing_like_escape_is_refused_when_it_changes_literal_backslashes():
    from tests.unit.test_textband_figures import compiled, declarations, model, text_filter
    from kge.pipeline.textmatch import backslash_like
    value = r'a\b'
    predicate = dict(kind='predicate', params=dict(column='u.events.category', phrases=['selected labels'], op='contains', value=value))
    _, vocab = declarations(bands=False, predicates=[predicate])
    result = compiled(model(filters=[text_filter(value=value)]), 'Metric for selected labels', vocab)
    tree = sqlglot.parse_one(result.answer.sql, read='duckdb')
    for escaped in list(tree.find_all(exp.Escape)):
        escaped.replace(backslash_like(escaped))
    assert check(tree.sql('duckdb'), result.answer.allow)


def test_band_ordering_does_not_decode_a_numeric_code_name_pair():
    from tests.unit.test_textband_figures import BAND, ROWS, band_dimension, compiled, declarations, model
    from tests.plan_fixtures import vocabulary
    from tests.unit.test_plan_compiler import connection
    engine, _ = declarations()
    sem = engine.semantics
    sem.d.VOCABULARY.append(dict(kind='code_name_pair', params=dict(code=BAND['column'], name='u.events.category', members=[])))
    result = compiled(model(dimensions=[band_dimension()]), 'Metric by size band', vocabulary(sem, aliases=[('Metric', 'ctx')]))
    with connection(ROWS) as con:
        rows = con.execute(result.answer.sql).fetchall()
    assert {r[0]: r[1] for r in rows if r[0] is not None} == {'Small': 9, 'Medium': 29, 'Large': 50}


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
@pytest.mark.parametrize('value', ["O'Brien", r'a\b', 'A!B'])
def test_named_runtime_keeps_text_escape_policy_and_band_shapes(dialect, value):
    from tests.unit.test_filter_runtime_gate import renamed
    from tests.unit.test_textband_figures import band_dimension, compiled, declarations, model, text_filter
    predicate = dict(kind='predicate', params=dict(column='u.events.category', phrases=['selected labels'], op='contains', value=value))
    _, vocab = declarations(predicates=[predicate])
    built = compiled(model(filters=[text_filter(value=value)], dimensions=[band_dimension()]),
                     'Metric by size band for selected labels', vocab).answer
    _, manager, _, _, _ = renamed(built)
    manager.runtimes['live'] = {'dialect': dialect}
    sql, physical, _ = manager._rename_sql_for_runtime(built.sql, 'u', 'live', built.allow)
    written = manager._render_for_dialect(sql, dialect)
    assert physical._checked_sql
    assert check(written, physical, dialect=dialect) == []


@pytest.mark.parametrize('dialect', ['duckdb', 'bigquery'])
def test_named_runtime_cannot_acquire_an_undeclared_match_after_mapping(dialect):
    from tests.unit.test_filter_runtime_gate import renamed
    from tests.unit.test_textband_figures import compiled, declarations, model
    _, vocab = declarations(bands=False, predicates=[])
    built = compiled(model(), 'Metric', vocab).answer
    _, manager, _, _, _ = renamed(built)
    manager.runtimes['live'] = {'dialect': dialect}
    sql, physical, _ = manager._rename_sql_for_runtime(built.sql, 'u', 'live', built.allow)
    written = manager._render_for_dialect(sql, dialect)
    tree = sqlglot.parse_one(written, read=dialect)
    base = next(s for s in tree.find_all(exp.Select) if any(t.name == 'LIVE_EVENTS' for t in s.find_all(exp.Table)))
    base.where(sqlglot.parse_one('events.P_VALUE > 10', read=dialect), append=True, copy=False)
    assert check(tree.sql(dialect), physical, dialect=dialect)
