"""Shared boolean grammar and the null-preserving meaning of declared exclusions."""
from __future__ import annotations

import re

FILTER_CONNECTOR = r'[\s()]*(?:(?:and|or)\b[\s()]*)?(?:(?:for|with|the)\b[\s()]*)*'


def preserved_paths(predicates):
    """Only a null-keeping exclusion retains unmatched optional roles."""
    def paths(predicate):
        member = exclusion(predicate)
        if member is not None:
            return {step.id for step in member.left.path} if member.left.kind == 'column' else set()
        if predicate.kind in ('all', 'any'):
            return {identifier for child in predicate.children for identifier in paths(child)}
        if predicate.kind == 'not':
            return paths(predicate.child)
        return set()
    return {identifier for predicate in predicates for identifier in paths(predicate)}


def exclusion(node):
    """Recognise exactly NOT(IN) OR IS NULL on the same operand, in either order."""
    if getattr(node, 'kind', None) != 'any' or len(node.children) != 2:
        return None
    negated = next((p for p in node.children if p.kind == 'not' and p.child.kind == 'in'), None)
    empty = next((p for p in node.children if p.kind == 'null' and p.is_null), None)
    if negated is None or empty is None:
        return None
    def operand(left):
        data = left.model_dump(mode='json')
        if left.kind == 'column':
            return (left.kind, left.name.id, tuple(p.id for p in left.path))
        return data
    return negated.child if operand(negated.child.left) == operand(empty.left) else None


def list_quantifier(pieces, text, first):
    """A lexical quantifier owns a contiguous list, including its joining words."""
    return next((p for p in pieces if p.kind == 'operator' and p.text.casefold() in ('any of', 'all of')
                 and p.span.end <= first.span.start
                 and re.fullmatch(r'[\s(]*', text[p.span.end:first.span.start])), None)


def boolean_groups(pieces, text):
    """Parse contiguous declared filters, requiring parentheses around mixed AND/OR.

    Leaves index the supplied predicate pieces. Other words end a filter group;
    lists of one column's values have already become one predicate piece.
    """
    predicates = [p for p in pieces if p.kind == 'predicate']
    if not predicates:
        return (), None
    characters, opened = list(text), []
    for position, char in enumerate(text):
        if char == '(':
            opened.append(position)
        elif char == ')' and opened:
            start = opened.pop()
            if any(start < p.span.start < p.span.end <= position and p.kind != 'predicate'
                   and not (p.kind == 'operator' and p.operation in ('all', 'any')) for p in pieces):
                characters[start] = characters[position] = ' '
    text = ''.join(characters)
    clusters, tokens = [], []
    first = predicates[0]
    prefix = re.search(r'(?:[\s(]|\b(?:for|with|the)\b)*$', text[:first.span.start], re.I)[0]
    tokens.extend(re.findall(r'[()]', prefix))
    tokens.append(0)
    for index, (left, right) in enumerate(zip(predicates, predicates[1:]), 1):
        gap = text[left.span.end:right.span.start]
        first_index = next(token for token in tokens if isinstance(token, int))
        listed = list_quantifier(pieces, text, predicates[first_index])
        inner = [p for p in pieces if p.kind == 'operator' and p.text.casefold() in ('any of', 'all of')
                 and left.span.end <= p.span.start < p.span.end <= right.span.start]
        if listed and inner:
            scoped = list(gap)
            for piece in inner:
                scoped[piece.span.start - left.span.end:piece.span.end - left.span.end] = ' ' * (piece.span.end - piece.span.start)
            if re.fullmatch(FILTER_CONNECTOR, ''.join(scoped).replace(',', ' '), re.I):
                return (), 'Please separate nested quantified filter lists into explicitly scoped conditions.'
        connector = gap.replace(',', ' ') if listed else gap
        if re.fullmatch(FILTER_CONNECTOR, connector, re.I):
            between = re.findall(r'\b(?:and|or)\b|[()]', connector, re.I)
            if not any(t.casefold() in ('and', 'or') for t in between):
                closed = [t for t in between if t == ')']
                opened = [t for t in between if t == '(']
                between = [*closed, 'and', *opened]
            tokens.extend(t.casefold() for t in between)
            tokens.append(index)
        else:
            tokens.extend(re.findall(r'[()]', re.match(r'[\s)]*', gap)[0]))
            clusters.append(tokens)
            tokens = [*re.findall(r'[()]', re.search(r'[(\s]*$', gap)[0]), index]
    tokens.extend(re.findall(r'[()]', re.match(r'[\s)]*', text[predicates[-1].span.end:])[0]))
    clusters.append(tokens)

    def parse(tokens):
        values, operators, index = [], [], 0
        while index < len(tokens):
            token = tokens[index]
            if token == '(':
                depth, end = 1, index + 1
                while end < len(tokens) and depth:
                    depth += (tokens[end] == '(') - (tokens[end] == ')')
                    end += 1
                if depth:
                    raise ValueError('Please group the filters with balanced parentheses.')
                values.append(parse(tokens[index + 1:end - 1]))
                index = end
                continue
            if token in ('and', 'or'):
                operators.append(token)
            elif isinstance(token, int):
                values.append(token)
            else:
                raise ValueError('Please group the filters with balanced parentheses.')
            index += 1
        if len(operators) != len(values) - 1 or not values:
            raise ValueError('Please identify how the filters are grouped.')
        if len(set(operators)) > 1:
            raise ValueError('Please group the filters: which conditions belong together with AND or OR?')
        return values[0] if not operators else ('any' if operators[0] == 'or' else 'all', tuple(values))
    try:
        groups = []
        for cluster in clusters:
            first = predicates[next(token for token in cluster if isinstance(token, int))]
            quantified = list_quantifier(pieces, text, first)
            if quantified:
                depth, outer = 0, 0
                if cluster[0] == '(':
                    for i, token in enumerate(cluster):
                        depth += (token == '(') - (token == ')')
                        if depth == 0:
                            outer = int(i == len(cluster) - 1)
                            break
                depth, normalized = 0, []
                for token in cluster:
                    depth += token == '('
                    normalized.append(('or' if quantified.text.casefold() == 'any of' else 'and')
                                      if token in ('and', 'or') and depth == outer else token)
                    depth -= token == ')'
                cluster = normalized
            group = parse(cluster)
            groups.append(group)
        return tuple(groups), None
    except ValueError as exc:
        return (), str(exc)


def leaves(tree):
    return (tree,) if isinstance(tree, int) else tuple(i for child in tree[1] for i in leaves(child))
