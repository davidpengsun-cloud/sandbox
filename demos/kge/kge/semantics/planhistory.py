"""Untrusted conversation bases are rechecked before interpretation or execution."""
from __future__ import annotations

import json
from dataclasses import replace
from types import MappingProxyType

from kge.semantics.coverage import ledger, removal_spans, referenced_targets, canonical_calendars
from kge.semantics.plancheck import CheckedPlan, Decision, check_plan

MAX_HISTORY_TURNS = 8
MAX_HISTORY_CHARS = 12000


def bounds(question, history):
    encoded = json.dumps([h.model_dump(mode='json', by_alias=True) for h in history],
                         separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(history) > MAX_HISTORY_TURNS or len(encoded) + len(question) > MAX_HISTORY_CHARS:
        raise ValueError('Conversation history exceeds the interpretation bounds.')
    if len({h.turn_id for h in history}) != len(history):
        raise ValueError('Conversation turn ids must be unique.')


def semantic(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json', by_alias=True)
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if k not in ('evidence', 'obligations')}
    if isinstance(value, (list, tuple)):
        return [semantic(v) for v in value]
    return value


def nodes(plan):
    return {n.id: n for n in plan.nodes()} | {'time': plan.time, 'limit': plan.limit} | {m.id + '/mode': m.mode for m in plan.measures}


def retained(before, after):
    if semantic(before) == semantic(after):
        return True
    if (getattr(before, 'kind', None) not in ('measure', 'distinct_count')
            or getattr(after, 'kind', None) != before.kind):
        return False
    old, new = semantic(before), semantic(after)
    old_filters, new_filters = old.pop('filters'), new.pop('filters')
    return old == new and all(f in new_filters for f in old_filters)


def inherited(plan, history, vocabulary):
    current = nodes(plan)
    spans = {s for o in plan.obligations for s in o.spans}
    pieces = []
    for entry in history:
        if entry.plan is None:
            continue
        old = nodes(entry.plan)
        valid = {s for o in entry.plan.obligations if o.state == 'applied' and any(
            t in current and retained(old.get(t), current[t]) for t in o.targets) for s in o.spans}
        for piece in ledger(entry.question, entry.turn_id, vocabulary):
            if piece.span not in spans & valid:
                continue
            selected = [old[t] for o in entry.plan.obligations if o.state == 'applied' and piece.span in o.spans
                        for t in o.targets if t in old and hasattr(old[t], 'model_dump')]
            resolved = referenced_targets(selected, vocabulary).intersection(piece.candidates)
            pieces.append(replace(piece, candidates=tuple(i for i in piece.candidates if i in resolved))
                          if resolved else piece)
    return tuple(pieces)


def canonical_history(history, vocabulary):
    return tuple(entry.model_copy(update={'plan': canonical_calendars(
        entry.plan, ledger(entry.question, entry.turn_id, vocabulary), vocabulary)})
        if entry.plan is not None else entry for entry in history)


def validate(history, vocabulary, question, turn):
    bounds(question, history)
    if any(h.turn_id == turn for h in history):
        raise ValueError('Conversation turn ids must be unique.')
    vocabulary = vocabulary.history_vocabulary or vocabulary
    history = canonical_history(history, vocabulary)
    previous = []
    for entry in history:
        if entry.binding != vocabulary.binding or vocabulary.denial(entry.question):
            return Decision('abstain', ('Conversation history is no longer applicable.',))
        if entry.plan is None and entry.clarification is None:
            return Decision('abstain', ('Conversation history has no checked interpretation.',))
        if entry.plan is not None:
            base = next((h for h in reversed(previous) if h.plan is not None), None)
            removed = removed_nodes(entry.plan, base.plan) if base else {}
            try:
                removal_spans(entry.plan, entry.question, entry.turn_id, vocabulary, removed, nodes(base.plan) if base else {})
            except ValueError:
                return Decision('abstain', ('Conversation history could not be validated.',))
            checked = check_plan(entry.plan, vocabulary, entry.question, entry.turn_id,
                                 inherited(entry.plan, previous, vocabulary), removed)
            if not isinstance(checked, CheckedPlan):
                return Decision('abstain', ('Conversation history could not be validated.',))
        if entry.clarification is not None:
            clarification = entry.clarification
            if not clarification.spans or not clarification.choices:
                return Decision('abstain', ('Conversation alternatives could not be validated.',))
            for choice in clarification.choices:
                if choice.id not in vocabulary.entries or choice.label != vocabulary.entries[choice.id].label:
                    return Decision('abstain', ('Conversation alternatives are unavailable.',))
            pieces = ledger(entry.question, entry.turn_id, vocabulary)
            if any(p.kind == 'unknown' for p in pieces):
                return Decision('abstain', ('Conversation alternatives could not be validated.',))
            candidates = {i for p in pieces if any(s.turn == p.span.turn and s.start <= p.span.start and p.span.end <= s.end
                                                        for s in clarification.spans) for i in p.candidates}
            if (any(s.turn != entry.turn_id or s.end > len(entry.question) for s in clarification.spans)
                    or any(c.id not in candidates for c in clarification.choices)):
                return Decision('abstain', ('Conversation alternatives have unrelated evidence.',))
        previous.append(entry)
    return None


def replacement(reply, history, vocabulary, question, turn):
    history = canonical_history(history, vocabulary.history_vocabulary or vocabulary)
    reply = reply.model_copy(update={'plan': canonical_calendars(
        reply.plan, ledger(question, turn, vocabulary), vocabulary)})
    if history and history[-1].clarification is not None:
        if reply.base_turn != history[-1].turn_id or clarification_choice(history[-1], question) is None:
            return Decision('abstain', ('A clarification follow-up must select a named alternative.',)), ()
        return None, ()
    base = next((h for h in reversed(history) if h.plan is not None), None)
    if base is None or base.turn_id != reply.base_turn:
        return Decision('abstain', ('A follow-up requires its newest checked conversation base.',)), ()
    before, after = nodes(base.plan), nodes(reply.plan)
    changed = {i for i in before.keys() | after.keys() if not retained(before.get(i), after.get(i))}
    try:
        removal_spans(reply.plan, question, turn, vocabulary, removed_nodes(reply.plan, base.plan), before)
    except ValueError as exc:
        return Decision('abstain', (str(exc),)), ()
    evidenced = {t for o in reply.plan.obligations if o.state in ('applied', 'removed') for t in o.targets if any(s.turn == turn for s in o.spans)}
    if changed - evidenced:
        return Decision('abstain', ('A changed or removed node needs explicit follow-up evidence.',)), ()
    return None, inherited(reply.plan, history, vocabulary.history_vocabulary or vocabulary)


def clarification_choice(entry, question):
    choices = []
    for choice in entry.clarification.choices:
        updated = entry.question
        for span in sorted(entry.clarification.spans, key=lambda s: s.start, reverse=True):
            updated = updated[:span.start] + choice.label + updated[span.end:]
        if updated == question:
            choices.append(choice)
    return choices[0] if len(choices) == 1 else None


def clarified_vocabulary(history, vocabulary, question):
    if not history or history[-1].clarification is None:
        return vocabulary
    choice = clarification_choice(history[-1], question)
    if choice is None:
        return vocabulary
    return replace(vocabulary, phrases=MappingProxyType({**vocabulary.phrases, choice.label.casefold(): (choice.id,)}))


def removed_nodes(plan, base):
    before, after = nodes(base), nodes(plan)
    return {identifier: node for identifier, node in before.items() if identifier not in after}
