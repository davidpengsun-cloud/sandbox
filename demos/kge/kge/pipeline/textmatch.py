"""Literal, case-sensitive text operators shared by compilation and gate policy."""
from sqlglot import exp

TEXT_MATCHES = ('contains', 'starts_with')


def text_value_problem(value):
    if not isinstance(value, str) or not value.strip():
        return 'requires a nonempty text value'
    if '%' in value or '_' in value:
        return 'takes literal text, without LIKE wildcards (% or _)'
    return None


def text_match(column, operator, value):
    """Quote SQL literals and escape pattern metacharacters independently of question wording."""
    if operator not in TEXT_MATCHES or text_value_problem(value):
        raise ValueError('a declared text operator and literal value are required')
    if operator == 'starts_with':
        return exp.StartsWith(this=column, expression=exp.Literal.string(value))
    pattern = '%' + value.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
    return exp.Escape(this=exp.Like(this=column, expression=exp.Literal.string(pattern)),
                      expression=exp.Literal.string('!'))


def backslash_like(escaped):
    """Normalize an explicit literal LIKE escape to the standard backslash spelling."""
    like, marker = escaped.this, escaped.expression
    if (not isinstance(like, exp.Like) or not isinstance(like.expression, exp.Literal)
            or not like.expression.is_string or not isinstance(marker, exp.Literal)
            or not marker.is_string or len(marker.this) > 1):
        return None
    pattern, translated, index = like.expression.this, [], 0
    while index < len(pattern):
        char = pattern[index]
        if char == marker.this:
            index += 1
            if index == len(pattern):
                raise ValueError('LIKE pattern ends in its escape character')
            char = pattern[index]
            translated.append(('\\' if char in ('\\', '%', '_') else '') + char)
        else:
            translated.append('\\\\' if char == '\\' else char)
        index += 1
    like = like.copy()
    like.set('expression', exp.Literal.string(''.join(translated)))
    return like
