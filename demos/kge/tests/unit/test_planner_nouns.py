"""No pack-specific names enter the new plan runtime, including assembled literals."""
import ast
import calendar
import json
import re
from functools import lru_cache

import pytest
import yaml

from kge.pipeline.settings import ROOT

NEW = tuple(str(path.relative_to(ROOT / 'kge')) for folder in ('semantics', 'services/ask/engine')
            for path in sorted((ROOT / 'kge' / folder).rglob('*.py')))


GENERIC = {
    'as_of': 'The shared Period tagged variant and Release property name an instant.',
    'quarter_end': 'The meta-model Measure grain enum names a snapshot boundary.',
    'fiscal_quarter': 'The shared calendar grain and period kind are meta-model vocabulary.',
    'Fiscal Year': 'A calendar unit, independent of any pack calendar or column.',
    'month_start': 'The baseline calendar grammar recognizes a month boundary; table and rollup names still come only from declarations.',
}
PARAMETER_KEYS = {
    'valid_to_inclusive': 'The SCD2 interval-boundary parameter, only in a parameter key position.',
    'valid_from': 'The SCD2 policy parameter names its lower validity bound.',
    'valid_to': 'The SCD2 policy parameter names its upper validity bound.',
    'is_current': 'The SCD2 policy parameter names its current-version flag.',
}
PLATFORM_IDENTIFIERS = {
    'month_name': 'The calendar formatting helper, only as a code identifier.',
    'branch': 'The LangGraph control-flow selector, only as a code identifier.',
}
PLATFORM_SENTINELS = {'auto': 'The configured-model sentinel, only as a default or comparison of llm.'}
PLATFORM_ATTRIBUTES = {
    'status_code': 'The HTTP response status field, only as an object attribute.',
    'month_name': 'The standard calendar module’s month labels, only as an object attribute.',
    'current_branch': 'The compiler’s aggregate scope, only as an object attribute.',
}
PLATFORM_LITERALS = {
    'xref_match': 'The platform steward Tag family for a proposed cross-reference match.',
    '.certified_by': 'MAPS_TO edge provenance property, only when accessed as a graph property.',
    '.certified_on': 'MAPS_TO edge provenance property, only when accessed as a graph property.',
    ':CERTIFIED_BY': 'The meta-model Context-to-Golden edge, only in a graph type position.',
}


@lru_cache(maxsize=1)
def pack_names():
    names = set()
    for folder in ('clients', 'examples'):
        for path in (ROOT / folder).glob('*/pack.yaml'):
            pack = yaml.safe_load(path.read_text())
            names.add(pack['manifest']['client'])
            names.update(s['id'] for s in [*pack.get('sources', ()), *pack.get('curated_sources', ())])
            names.update(t['id'] for t in pack.get('glossary', ()))
            names.update(t['name'] for t in pack.get('glossary', ()) if 'name' in t)
            names.update(m['id'] for m in pack.get('measures', ()))
            names.update(a['phrase'] for a in pack.get('aliases', ()) if len(a['phrase'].split()) > 1)
            for value in pack.get('terminology', {}).get('value_vocabularies', ()):
                names.update(value.get('values', ()))
            lock = path.with_name('pack.lock')
            if lock.exists():
                for source in json.loads(lock.read_text())['sources'].values():
                    for table in source['tables']:
                        if '_' in table['name']:
                            names.add(table['name'])
                        names.update(table['name'] + '.' + c['name'] for c in table['columns'])
                        names.update(c['name'] for c in table['columns'] if '_' in c['name'])
    return {str(n) for n in names if len(str(n)) > 1}


def literals(tree, excluded=()):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in excluded:
            yield node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            try:
                yield assembled(node)
            except ValueError:
                pass


def assembled(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return assembled(node.left) + assembled(node.right)
    raise ValueError('not a literal')


def language_literals(tree):
    """Recognise standard language only in its existing grammar and ISO currency data positions."""
    replacements = {}
    currencies = {
        'USD': 'US dollar|United States dollar|American dollar',
        'XOF': 'CFA franc BCEAO|West African CFA franc',
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        target, value = node.targets[0].id, node.value
        if target == 'NAMES' and isinstance(value, ast.Dict):
            for key, label in zip(value.keys, value.values):
                if (isinstance(key, ast.Constant) and key.value in currencies
                        and isinstance(label, ast.Constant) and label.value == currencies[key.value]):
                    replacements[id(label)] = ''
        if target == 'ABBREVIATED' and isinstance(value, ast.Dict):
            for key, month in zip(value.keys, value.values):
                if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                        and isinstance(month, ast.Constant) and isinstance(month.value, int)
                        and 1 <= month.value <= 12 and key.value == calendar.month_abbr[month.value].lower()):
                    replacements[id(key)] = ''
        if (target == 'GROUNDING_STOP' and isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr)
                and isinstance(value.left, ast.Call) and isinstance(value.left.func, ast.Name)
                and value.left.func.id == 'set' and len(value.left.args) == 1):
            split = value.left.args[0]
            if (isinstance(split, ast.Call) and isinstance(split.func, ast.Attribute) and split.func.attr == 'split'
                    and not split.args and not split.keywords and isinstance(split.func.value, ast.Constant)
                    and isinstance(split.func.value.value, str)):
                text = split.func.value
                grammatical = {'us', *(day.lower() for day in calendar.day_name)}
                replacements[id(text)] = ' '.join(w for w in text.value.split() if w not in grammatical)
    return replacements


def violations(source, names):
    tree = ast.parse(source)
    from typing import get_args
    from kge.semantics.plan import Grain
    protocol_tokens = set(get_args(Grain))
    from kge.graph.schema import meta_model
    protocol_keys = {p for kind in ('node_types', 'edge_types') for schema in meta_model()[kind].values()
                     for p in schema.get('properties') or {}} | PARAMETER_KEYS.keys()
    keys = {id(k) for n in ast.walk(tree) if isinstance(n, ast.Dict) for k in n.keys
            if isinstance(k, ast.Constant) and k.value in protocol_keys}
    keys.update(id(n.slice) for n in ast.walk(tree) if isinstance(n, ast.Subscript)
                and isinstance(n.slice, ast.Constant) and n.slice.value in protocol_keys)
    for node in ast.walk(tree):
        iterator = node.iter if isinstance(node, (ast.For, ast.comprehension)) else None
        if isinstance(iterator, ast.Tuple) and all(isinstance(v, ast.Constant) and v.value in PARAMETER_KEYS for v in iterator.elts):
            keys.update(id(v) for v in iterator.elts)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'get' and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and argument.value in PARAMETER_KEYS:
                keys.add(id(argument))
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in node.targets):
            keys.update(id(v) for v in ast.walk(node.value) if isinstance(v, ast.Constant) and v.value in PLATFORM_IDENTIFIERS)
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == 'llm':
            keys.update(id(v) for v in node.comparators if isinstance(v, ast.Constant) and v.value in PLATFORM_SENTINELS)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = node.args.posonlyargs + node.args.args
            for argument, default in zip(arguments[-len(node.args.defaults):], node.args.defaults):
                if argument.arg == 'llm' and isinstance(default, ast.Constant) and default.value in PLATFORM_SENTINELS:
                    keys.add(id(default))
    texts = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id not in PLATFORM_IDENTIFIERS]
    texts += [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr not in protocol_keys and n.attr not in PLATFORM_ATTRIBUTES]
    language = language_literals(tree)
    texts += [s for s in literals(tree, keys | language.keys()) if s not in protocol_tokens and s not in PLATFORM_LITERALS]
    texts += language.values()
    names = {n for n in names if n.casefold() not in {g.casefold() for g in GENERIC}}
    texts = [re.sub(r':CERTIFIED_BY\b', '', re.sub(r'\bxref_match\b', '', t)) for t in texts]
    texts = [re.sub(r'\.(?:certified_by|certified_on)\b(?:\s+AS\s+(?:certified_by|certified_on)\b)?', '', t, flags=re.I) for t in texts]
    for key in protocol_keys & names:
        texts = [re.sub(r'\b\w+\.' + re.escape(key) + r'\s+AS\s+' + re.escape(key) + r'\b', '', t) for t in texts]
    pattern = re.compile(r'(?<![a-zA-Z])(' + '|'.join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n))) + r')(?![a-zA-Z])', re.I)
    found = {m.group().casefold() for text in texts for m in pattern.finditer(text)}
    return {n for n in names if n.casefold() in found}


@pytest.mark.parametrize('path', NEW)
def test_new_plan_modules_name_no_pack(path):
    assert violations((ROOT / 'kge' / path).read_text(), pack_names()) == set()


@pytest.mark.parametrize('shape', ['x = "my_pack"', 'my_pack = 1', 'obj.my_pack = 1', 'prompt = "Use my_pack"', '"""my_pack"""', 'x = "my_" + "pack"'])
def test_scan_detects_code_identifiers_prompts_docstrings_and_assembled_literals(shape):
    assert violations(shape, {'my_pack'}) == {'my_pack'}


def test_scan_follows_transformed_pack_names():
    for value in ('invented_unit', 'a_different_table', 'another.metric', 'unseen member'):
        assert violations('prompt = ' + repr(value), {value}) == {value}


@pytest.mark.parametrize('key', ['certified_by', 'valid_from', 'valid_to', 'is_current'])
def test_protocol_keys_do_not_exempt_domain_values(key):
    assert violations('x = {' + repr(key) + ': ' + repr(key) + '}', {key}) == {key}
    assert violations('x = {' + repr(key) + ': \'someone\'}', {key}) == set()


@pytest.mark.parametrize('name', ['AUTO', 'Branch', 'MONTH_NAME', 'status_code'])
def test_generic_identifiers_do_not_exempt_domain_literal_values(name):
    assert violations('value = ' + repr(name), {name}) == {name}


@pytest.mark.parametrize('word', ['DT', 'CORE', 'US', 'WEST', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'SEP'])
def test_standard_language_positions_never_exempt_a_domain_literal(word):
    assert violations('value = ' + repr(word), {word}) == {word}
    assert violations('prompt = ' + repr('Use ' + word), {word}) == {word}
    assert violations(word + ' = 1', {word}) == {word}
    assert violations('obj.' + word + ' = 1', {word}) == {word}
    assert violations('GROUNDING_STOP = {' + repr(word) + '}', {word}) == {word}


@pytest.mark.parametrize('source,names', [
    ('NAMES = {"USD": "US dollar|United States dollar|American dollar"}', {'US'}),
    ('NAMES = {"XOF": "CFA franc BCEAO|West African CFA franc"}', {'WEST'}),
    ('ABBREVIATED = {"sep": 9}', {'SEP'}),
    ('GROUNDING_STOP = set("we us Monday monday".split()) | set(ABBREVIATED)', {'US', 'Monday'}),
])
def test_only_standard_language_in_the_existing_data_position_is_recognised(source, names):
    expected = {'Monday'} if 'Monday monday' in source else set()
    assert violations(source, names) == expected


def test_language_data_still_rejects_pack_names_and_incorrect_standard_values():
    assert violations('NAMES = {"USD": "US"}', {'US'}) == {'US'}
    assert violations('ABBREVIATED = {"sep": 8}', {'SEP'}) == {'SEP'}
    assert violations('GROUNDING_STOP = set("us my_pack".split()) | set(ABBREVIATED)', {'US', 'my_pack'}) == {'my_pack'}
