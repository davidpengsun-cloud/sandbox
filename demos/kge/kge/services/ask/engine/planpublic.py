"""Compatibility presentation of checked numeric results for Ask and its baseline."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from copy import copy
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import asdict, replace

from kge.adapters.deadline import RequestDeadlineExceeded
from kge.pipeline.settings import ask_ai_fallback, ask_ai_fallback_setting, ask_order
from kge.pipeline.trace import QueryCancelled, graph_read, graph_recording, stream_event, streaming
from kge.pipeline.questionrun import active, memo, memo_with_sidecar
from kge.semantics.plan import reply_adapter
from kge.semantics.filtergrammar import exclusion
from kge.semantics.plan import Clarification, Choice, HistoryEntry, Span, QueryPlan
from kge.semantics.planvocabulary import member_names
from kge.semantics.plancheck import CheckedPlan
from kge.semantics.query import Asked
from kge.services.ask.engine import planledger, planmodel, planrules, rulesport, runtrace
from kge.services.ask.engine.reading import dimension_value
from kge.services.ask.engine.planner import Planner, count_label, counted, entity_only, display_outputs, display_value, measure_label, fixed_period_words
from kge.services.ask.engine.question import not_joinable_named, pair
from kge.services.ask.engine.errors import WAREHOUSE_PROBLEM
from kge.services.ask.engine.wording import Hidden, exact

_shadow_capture = ContextVar('shadow_capture', default=None)


@contextmanager
def capture_shadow():
    """Collect only redacted observations while a service request runs; evaluation never writes a log."""
    captured = []
    token = _shadow_capture.set(captured)
    try:
        yield captured
    finally:
        _shadow_capture.reset(token)


def _shadow_term_stamp(engine):
    """Detect local planner input changes before crediting a comparison."""
    terms = tuple(getattr(engine, 'term_names', ()))
    graph = getattr(engine, 'g', None)
    semantics = getattr(engine, 'semantics', None)
    partitions = tuple(sorted((getattr(semantics, 'partitioned', None) or {}).keys()))
    templates = getattr(graph, '_shadow_templates', getattr(graph, '_templates', None))
    generation = getattr(graph, '_shadow_template_generation', id(templates))
    inputs = (terms, getattr(graph, 'writes', None), getattr(graph, 'identity', None), partitions, generation)
    return hashlib.sha256(json.dumps(inputs, ensure_ascii=True, sort_keys=True, default=str).encode()).hexdigest()


def _shadow_candidate(engine, req, cfg, turn, history, cohort, planner, vocabulary, rules_reply, served, shadow_columns=None,
                      shadow_runtime=None):
    """Compare without changing the served reply; return redacted evidence only for a service capture."""
    captured = _shadow_capture.get()
    from kge.semantics.coverage import new_phrase
    from kge.semantics.plan import Abstain, Clarify, Name
    from kge.services.ask.engine.shadowinputs import ShadowInputs, ShadowInputsStale
    from kge.services.ask.engine.shadowing import Shadowing

    try:
        guard = getattr(engine, 'shadow_read_guard', engine.shadow_write_guard)
        with guard(), graph_recording(None):
            templates = getattr(engine.g, '_shadow_templates', getattr(engine.g, '_templates', None))
            if templates is None:
                raise ShadowInputsStale('shadow templates are not preloaded')
            if served == 'model' and not any(s['stage'] != 'retired' for s in templates.shapes.values()):
                return
            shadow_baseline = (not cfg.graph and shadow_runtime is not
                               getattr(engine.semantics, 'baseline_snapshot', None))
            runtime = engine._shadow_baseline if shadow_baseline else shadow_runtime
            inputs = ShadowInputs(engine, templates=templates, graph_mode=cfg.graph, runtime=runtime,
                                  columns=shadow_columns or {}, shadow_baseline=shadow_baseline)
            inputs.prepare_members(req, vocabulary)
            question = rulesport.RulesQuestion(inputs, req, req.question, vocabulary, cfg, turn, tuple(history), cohort)

            def checked(reply, check_planner=planner, check_vocabulary=vocabulary):
                result = check_planner.prepare(req.question, reply, turn, history)
                if isinstance(result, CheckedPlan):
                    return reply.model_copy(update={'plan': result.plan})
                if result.get('kind') == 'clarify':
                    choices = tuple(c['id'] for c in result.get('choices', []))
                    original = {n.id: n for n in reply.candidates} if reply.kind == 'clarify' else {}
                    if reply.kind == 'clarify' and set(choices) == set(original):
                        return reply
                    pieces = planrules.ledger(req.question, turn, check_vocabulary)
                    spans = (reply.spans if reply.kind == 'clarify' else
                             tuple(p.span for p in pieces if set(p.candidates) & set(choices)))
                    return Clarify(kind='clarify', spans=spans, candidates=tuple(
                        original[i] if i in original else Name(id=i, evidence=tuple(p.span for p in pieces if i in p.candidates))
                        for i in choices))
                if result.get('kind') == reply.kind == 'abstain':
                    return reply
                return Abstain(kind='abstain', spans=(), missing=())

            graph_vocabulary = vocabulary
            if inputs.member_plan_inputs is None:
                compared = Shadowing().read(question, coded=rules_reply, check=checked)
            else:
                _, graph_vocabulary = inputs.member_plan_inputs
                graph_checker = copy(planner)
                graph_checker.vocabulary = graph_vocabulary
                compared = Shadowing().read(question, coded=rules_reply, check=lambda reply: reply)
                compared = replace(compared, checked_coded=checked(compared.coded),
                                   checked_graph=checked(compared.graph, graph_checker, graph_vocabulary))
            if captured is None:
                compared.outcome
                inputs.validate()
                return
            pieces = planrules.ledger(req.question, turn, graph_vocabulary)
            markers = frozenset(m for piece in pieces for m in piece.markers)
            mirrors = {s['replaces']: s['id'] for s in templates.shapes.values() if s.get('replaces')}
            identity = engine.identity
            release = identity.get('release') or identity['hash']
            observation = compared.observe(release=release, served=served, pieces=pieces, vocab=graph_vocabulary,
                                           new_phrase=new_phrase(req.question, turn, vocabulary), markers=markers,
                                           mirrors=mirrors)
            inputs.validate()
            via_terms = inputs.member_plan_inputs is not None and _used_member_predicate(compared.checked_graph, pieces, graph_vocabulary)
            return replace(observation, via_terms=True) if via_terms else observation
    except (QueryCancelled, RequestDeadlineExceeded):
        raise
    except Exception:
        logging.getLogger(__name__).warning('shadow comparison could not be recorded')
        return


def _used_member_predicate(reply, pieces, vocabulary):
    """Whether a checked graph plan applies a predicate backed by a governed member."""
    from kge.semantics.coverage import predicate_supported

    if reply.kind != 'propose':
        return False
    plan = reply.plan
    nodes = {node.id: node for node in plan.nodes()}
    for piece in pieces:
        if not piece.member_bindings:
            continue
        for obligation in plan.obligations:
            if obligation.state == 'applied' and piece.span in obligation.spans and any(
                    name in nodes and predicate_supported(piece, nodes[name], vocabulary, plan) for name in obligation.targets):
                return True
    return False


def _shadow_compare(*args, **kwargs):
    """Retain one candidate for the service to append after the answer completes."""
    observation = _shadow_candidate(*args, **kwargs)
    if observation is not None and (captured := _shadow_capture.get()) is not None:
        captured.append(observation)


def _saved_shadow(value, served):
    """Read a redacted durable candidate, rejecting a malformed sidecar rather than crediting evidence."""
    if value is None:
        return None
    from kge.adapters.shadowlog.record import line_of
    from kge.ports import ShadowObservation

    try:
        data = dict(value)
        for name in ('graph_shapes', 'coded_shapes', 'shapes'):
            data[name] = tuple(data[name])
        data['served'] = served
        observation = ShadowObservation(**data)
        line_of(observation)
        return observation
    except (KeyError, TypeError, ValueError):
        logging.getLogger(__name__).warning('shadow checkpoint could not be read')
        return None


VERDICTS = {None: {"behaviour": "sum_without_verdict"}, True: {"behaviour": "sum_with_verdict"},
            False: {"behaviour": "parts_with_reason"}}
DECISIONS = {'clarify': {"behaviour": "clarify"}, 'abstain': {"behaviour": "abstain"}, 'refuse': {"behaviour": "refuse"}}
STATED_PHASES = frozenset(('release', 'observations', 'readings'))
RECEIPT_PLAN = '_receipt_plan'
"""Planner phases that refuse with their own stated reason: an unreleased period (release), a current to-date window
holding no observation (observations), or an AI-Generated reading left ambiguous (readings)."""


def measure_question(question):
    return bool(re.search(r'\b(?:plan|budget|target|total|how much)\b', question, re.I))


def declared_measure_labels(req, vocab, default_units=()):
    pieces = planrules.ledger(req.question, 'turn', vocab)
    nearby = {vocab.entries[i].unit for p in pieces for i in p.candidates
              if vocab.entries[i].kind in ('context', 'measure', 'column', 'value', 'entity') and vocab.entries[i].unit}
    units = set(req.units or default_units) or nearby
    units = units or {c.unit for c in vocab.contexts.values() if c.term in req.terms_found}
    measures = {c.measure for c in vocab.contexts.values()
                if (not units or c.unit in units) and not c.id.startswith('context:count:')}
    return sorted({vocab.entries[m].label for m in measures if m in vocab.entries})


UNREADABLE_DATA = 'It needs data outside what you may read or outside the products in scope, so no warehouse was queried.'
NOT_DECLARED_LINK = 'The pack declares no link that connects these figures as asked.'
NO_JOIN = 'The no-join policy keeps the data these figures come from apart, so they cannot be combined.'
PERIOD_ORDER = 'Name the two periods in the order you want them compared.'
OPERAND_ORDER = 'Name the two figures in the order you want them compared, the first one first.'
WRONG_VALUE = 'A value in the question does not fit the kind of data it is compared with.'
SHAPE = 'The question asks for a combination of figures that cannot be computed as asked.'
OUTSIDE_CALENDAR = 'Some observations are dated outside the declared calendar, so they are not in this answer.'
FOLLOW_UP = 'This follow-up does not match the earlier answer it builds on. Ask the whole question again.'
PLAIN = {
    'missing compatible period binding': 'This figure is not recorded by period, so it cannot be given for one.',
    'requested time grain is finer than the declared observations':
        'This figure is recorded only by a longer period than the breakdown asks for.',
    'requested period is finer than the declared quarter observations':
        'This figure is recorded by quarter, so it cannot be given for a period shorter than a quarter.',
    'incompatible dimension calendar': 'This figure is not recorded on that calendar, so it cannot be broken down by it.',
    'missing declared calendar': 'No calendar is declared for the period asked.',
    'The requested period has no declared calendar binding.': 'No calendar is declared for the period asked.',
    'The requested period has no unique declared calendar binding.':
        'The period asked can be read on more than one calendar. Say which calendar, for example calendar or fiscal.',
    'YTD calendar mismatch': 'The year to date asked for is on a different calendar from the figure.',
    'snapshot rollup requires a period binding': 'This balance is recorded at points in time, so it needs a period to read it at.',
    'missing release/freeze policy':
        'This figure has no declared release or freeze date, so it cannot be given as reported or for the latest released period.',
    'no common released period': 'The figures asked for have no released period in common.',
    "The previous period of a multi-period range isn't declared.":
        'The period before a range of several periods is not declared. Name the period to compare with.',
    'time comparison needs bounded periods': 'A comparison over time needs two periods with a start and an end.',
    'explicit comparison needs bounded windows on one calendar': 'The two periods compared must each have a start and an end, on one calendar.',
    'explicit comparison needs a one-to-one period mapping': 'The two periods compared must have the same number of months, quarters or years.',
    'comparison needs a one-to-one period mapping': 'The two periods compared must have the same number of months, quarters or years.',
    'comparison requires grounded periods in question order': PERIOD_ORDER,
    'comparison base and other must follow the question period order': PERIOD_ORDER,
    'comparison base and other must follow the question context or measure order':
        'Name the two figures in the order you want them compared.',
    'range endpoints must be ordered at one grain and calendar': 'A range must run from an earlier period to a later one, both months, quarters or years.',
    'a current-anchored range needs its to-date grain, and only it has one':
        'A to-date period must say whether it runs to date in the current month or the current quarter.',
    'a size threshold needs a declared amount column of its context':
        'A size threshold can be set only on an amount column declared for this data.',
    'a size threshold needs a numeric comparison with a number of zero or more':
        'A size threshold must compare an amount with a number of zero or more.',
    'incompatible observation grains': 'The figures asked for are recorded by different periods, so they cannot be put side by side.',
    'incompatible currencies or populations':
        'The figures asked for are in different currencies or cover different populations, so they cannot be combined.',
    'entity has no declared path': 'What is counted has no declared link to the figure it would be counted by.',
    'missing complete entity key': 'What is counted has no declared key, so each one cannot be counted once.',
    'unreachable entity key': 'What is counted has no declared link to the figure it would be counted by.',
    'missing readable context': 'This figure is not one you may read. ' + UNREADABLE_DATA,
    'context requires data outside access or product scope': UNREADABLE_DATA,
    'membership requires data outside access or product scope': UNREADABLE_DATA,
    'product scope requires its resolved table set': UNREADABLE_DATA,
    'no-join policy blocks membership': NO_JOIN,
    'no-join policy blocks alignment or arithmetic': NO_JOIN,
    'no-join policy blocks the requested cross-source addition.': NO_JOIN,
    'no-join policy blocks the requested relationship.': NO_JOIN,
    'cross-unit role requires certified membership':
        'Linking these business units needs a certified match between them, and none is declared.',
    'membership requires a certified relationship': 'Linking these records needs a certified match, and none is declared.',
    'membership entity does not match the far context': NOT_DECLARED_LINK,
    'membership source has no declared path': NOT_DECLARED_LINK,
    'missing declared membership binding': NOT_DECLARED_LINK,
    'missing required membership binding': 'This figure is defined only for members of a certified match, and the question names none.',
    'missing declared role path': NOT_DECLARED_LINK,
    'role has no declared key alignment': NOT_DECLARED_LINK,
    'no declared dimension role alignment': NOT_DECLARED_LINK,
    'role does not reach the requested dimension level': 'The breakdown asked for is not declared at that level for this figure.',
    'missing dimension binding': 'The breakdown asked for is not declared for this figure.',
    'dimension must bind every context': 'The breakdown asked for is not declared for every figure asked.',
    'unselected dimension context': 'The breakdown asked for is not declared for every figure asked.',
    'dimension column required': 'The breakdown asked for is not declared for this figure.',
    'duplicate dimension binding': 'The question asks for the same breakdown twice.',
    'hierarchies require a level; columns require null level': 'Say which level of the breakdown you want.',
    'measure does not belong to context': 'The measure and the figure it was matched to do not belong together.',
    'undeclared member value': 'A value in the question is not one the pack declares for that field.',
    'local predicates must narrow rows': 'A condition in the question does not narrow the figure it is applied to.',
    'membership predicates must narrow rows': 'A condition in the question does not narrow the figure it is applied to.',
    'text literal on nontext column': WRONG_VALUE,
    'numeric literal on nonnumeric column': WRONG_VALUE,
    'date literal on nondate column': WRONG_VALUE,
    'boolean literal on nonboolean column': WRONG_VALUE,
    'numeric output requires a numeric literal': WRONG_VALUE,
    'a finite canonical decimal string is required': WRONG_VALUE,
    'ISO date required': 'A date in the question could not be read. Write it as a day, month and year.',
    'year required': 'A year in the question could not be read.',
    'year-month required': 'A month in the question could not be read. Write it as a month and year.',
    'year-quarter required': 'A quarter in the question could not be read. Write it as, for example, Q1 2025.',
    'ratio operands do not identify a unique order': OPERAND_ORDER,
    'ratio requires grounded operands in question order': OPERAND_ORDER,
    'ratio requires the first named operand as numerator and the second as denominator': OPERAND_ORDER,
    'share requires a base aggregate': 'A share needs the figure it is a share of.',
    'share denominator must retain every named filter unless a whole-population share is requested':
        'A share keeps the conditions you named unless you ask for a share of the whole.',
    'share denominator must remove the requested breakdown dimensions': 'A share is taken of the total across the breakdown asked for.',
    'denominator scope can remove only row filters': 'A share can leave out only conditions on the rows.',
    'missing denominator scope': 'A share needs the figure it is a share of.',
    'running total requires additive observations and time order': 'A running total needs a figure that adds up over time and a breakdown by period.',
    'both ends require one output ordering, a limit per end and a breakdown':
        'A top and bottom list needs one figure to rank, how many at each end and what to rank.',
    'comparison requires measure outputs': SHAPE,
    'missing derived input': SHAPE,
    'missing order output': SHAPE,
    'missing predicate output': SHAPE,
    'missing obligation target': SHAPE,
    'unresolved derived order': SHAPE,
    'derived cycle': SHAPE,
    'column operand required': SHAPE,
    'boolean predicate mixes row and output operands': SHAPE,
    'invalid window dimensions': SHAPE,
    'node ids must be unique and unreserved': SHAPE,
    'empty evidence span': SHAPE,
    'reply_bounds': SHAPE,
    'A name has unrelated evidence.': 'A name in the question was matched to the wrong figure.',
    'A requested name is absent from the readable vocabulary.': 'A name in the question is not one you may read or one the pack declares.',
    'A proposed alternative is unavailable.': 'The meanings offered for the question are not ones you may use.',
    'The proposed clarification has no grounded alternatives.': 'The meanings offered for the question do not match its words.',
    'A changed or removed node needs explicit follow-up evidence.': FOLLOW_UP,
    'A clarification follow-up must select a named alternative.': 'Choose one of the meanings offered.',
    'A follow-up requires its newest checked conversation base.': FOLLOW_UP,
    'Conversation alternatives are unavailable.': FOLLOW_UP,
    'Conversation alternatives could not be validated.': FOLLOW_UP,
    'Conversation alternatives have unrelated evidence.': FOLLOW_UP,
    'Conversation history could not be validated.': FOLLOW_UP,
    'Conversation history has no checked interpretation.': FOLLOW_UP,
    'Conversation history is no longer applicable.': FOLLOW_UP,
    'Conversation history exceeds the interpretation bounds.': 'The conversation is too long to follow. Ask the whole question again.',
    'Conversation turn ids must be unique.': FOLLOW_UP,
    'Deletion evidence does not identify the removed nodes.': FOLLOW_UP,
    'Deletion evidence overlaps another obligation.': FOLLOW_UP,
    'Deletion must name a removed node.': FOLLOW_UP,
    'Deletion requires current follow-up evidence.': FOLLOW_UP,
    'Deletion requires nodes from the checked conversation base.': FOLLOW_UP,
    'No permitted runtime can answer the rules plan.': 'No warehouse you may use holds this figure.',
    'Addition of different measures has no supported typed operation.':
        'Addition of different measures is not supported: they are not the same kind of figure.',
    'text matching requires a declared text column and literal value':
        'Text can be matched only on a column declared for it, against a value written in the question.',
    'undeclared text matching operator, column or value':
        'That kind of text match is not declared for this data.',
    'count must use the requested entity and its complete key': SHAPE,
    'requested calendar attribute is finer than the declared observations':
        'This figure is recorded only by a longer period than the breakdown asks for.',
    'missing declared dimension calendar': 'No calendar is declared for the breakdown asked.',
    'band dimension requires its exact declared column': 'Those bands are not declared for this data.',
    'band bindings require null level': SHAPE,
    'ranking must partition by every requested partition dimension': SHAPE,
    'ranking partitions must be dimensions of the plan': SHAPE,
    'partitioned ranking requires one ranked breakdown and one ordering': SHAPE,
    'invalid ranking partitions': SHAPE,
    'partitioned ranking requires an output, a limit and a ranked dimension': SHAPE,
    'comparison grain disagrees with the question; give a matching time breakdown':
        'The comparison asked does not match the breakdown asked. Ask for a breakdown by the same period.',
    'moving average length is not declared for this measure':
        'A moving average of that length is not declared for this figure.',
    'moving average must use the requested length and time grain': SHAPE,
    'derived input must use its named measure': SHAPE,
    'each requested measure needs exactly one ': SHAPE,
    'time mode comparison must include every requested measure': SHAPE,
    'each compared measure needs its requested ': SHAPE,
    'ISO week-year and week required': 'Name the week as an ISO week of a year, for example week 12 of 2025.',
    'ISO week periods require the default week start; use date bounds for another start':
        'Name the week as an ISO week. For a week that starts on another day, give the dates.',
    'comparison base and other must follow the question time mode order':
        'Name the two bases in the order you want them compared, for example current versus as reported.',
}
PATTERNS = (
    (re.compile(r'table \S+ requires a period filter on partition column (\S+)'),
     'This figure is read only for a stated period: its data is kept by {0}. Name the period you want.'),
    (re.compile(r'SQL gate: .*'), 'The query this question needs did not pass the SQL gate, so it did not run.'),
    (re.compile(r'join \S+: .*'), NOT_DECLARED_LINK),
    (re.compile(r'each (?:requested|compared) measure needs (?:exactly one|its requested) [a-z ]+'), SHAPE),
)
INTERNAL = re.compile(r"[a-z'][^.?!]*")


def plain_reason(reason, question):
    """One plan-check reason in words: the table's sentence, the words of the question no certified definition
    covers, or for any other check of the planner's own a plain sentence of what kind of question it cannot answer."""
    if reason in PLAIN:
        return PLAIN[reason]
    for pattern, sentence in PATTERNS:
        if found := pattern.fullmatch(reason):
            return sentence.format(*found.groups())
    if reason.strip() and reason.casefold() in question.casefold():
        return f'“{reason}” in the question matches no certified definition, so it cannot be applied.'
    return SHAPE if INTERNAL.fullmatch(reason) else reason


UNREAD_PHASES = ('truncated', 'contract')
"""A model refusal with no reply read: every attempt was cut off at its budget, or failed its contract."""


def model_abstained(reply, result):
    """What the model said when it abstained and the rules found no answer either, as a sentence for the reason: the
    words it could not read, which the rules' own reason does not name. '' otherwise."""
    refusal = result.get('model_refusal') or {}
    if reply is None or reply.kind != 'abstain' or result['kind'] != 'abstain' or refusal.get('kind') != 'abstain':
        return ''
    said = '; '.join(m for m in refusal.get('missing') or () if m)
    return '' if not said else said.rstrip('. ') + '.'


def gate_decline(problems):
    """A query the SQL gate stopped, in words: restricted personal data and a warehouse that could not be read are said
    as the gate says them; any other gate finding as the query not passing the gate."""
    kept = [p.removeprefix('dry run failed: ') for p in problems
            if 'restricted personal data' in p or p.endswith(WAREHOUSE_PROBLEM)]
    plain = [] if len(kept) == len(problems) else ['The query this question needs did not pass the SQL gate, so it did not run.']
    return ' '.join(dict.fromkeys([*kept, *plain]))


def plain_decline(why, plan, vocab, question, turn):
    """A plan check's decline as a business user reads it: a count of what has no period of its own asks which figure
    to count it by; every other reason is said in words, an access or policy refusal as such."""
    reasons = why.split('; ')
    if plan is not None and 'missing compatible period binding' in reasons:
        counts = [m for m in plan.measures if m.kind == 'distinct_count' and m.entity.id in vocab.entities
                  and m.context.id in vocab.contexts and entity_only(vocab.contexts[m.context.id], vocab)]
        if counts:
            spans = [s for o in plan.obligations if counts[0].id in o.targets for s in o.spans if s.turn == turn]
            return (counted(counts[0], vocab, question, spans) + ' have no period of their own; '
                    'which figure should they be counted by?')
    words = [r for r in reasons if r not in PLAIN and r.strip() and r.casefold() in question.casefold()]
    rest = [plain_reason(r, question) for r in reasons if r not in words]
    said = ([('“' + '”, “'.join(words) + '” in the question match' if len(words) > 1 else f'“{words[0]}” in the question matches')
             + ' no certified definition, so ' + ('they cannot' if len(words) > 1 else 'it cannot') + ' be applied.']
            if words else [])
    return ' '.join(dict.fromkeys([*said, *rest]))


def release_decline(reason, tables=()):
    reason = re.sub(r'([^;]+?) (?:is|are) not booked in the release calendar', r'\1 is not released yet.', reason)
    reason = re.sub(r'([^;]+?) is not yet released(?::[^;]+)?', r'\1 is not released yet.', reason)
    reason = reason.replace('; The latest released month', ' The latest released month')
    return reason


def value(cell):
    scalar = cell['value']
    v = scalar.get('value')
    return round(float(v), 2) if scalar['kind'] == 'decimal' else v


def scope_requests(engine, req, principal, cfg, scope):
    """Default page units first, then other readable declarations; explicit sources stay fixed."""
    if scope.products is None or req.units or req.all_units or not req.terms_found:
        return [req]
    runtime = planrules.snapshot(engine, cfg.graph)
    readable = engine.g.readable_units(principal)
    sources = [{c.unit for c in runtime.contexts.values() if c.term == term}
               | {m['unit'] for m in runtime.mapping_declarations if m['term'] == term}
               for term in req.terms_found]
    sources = [units for units in sources if units]
    if not sources:
        return [req]
    cohort = cfg.graph and bool(planrules.cohort_terms(engine, req))
    candidates = set.intersection(*sources) if cohort else set.union(*sources)
    units = [u for u in engine.UNIT_ORDER if u in candidates & readable]
    preferred = [u for u in units if u in scope.units]
    if cohort:
        if not preferred and len(units) != 1:
            return [req]
        return [replace(req, units=[u], counted=planrules.population(engine, runtime, u) if req.counted else None)
                for u in [*preferred, *(u for u in units if u not in preferred)]]
    if not preferred:
        return [replace(req, units=units)] if units else [req]
    remaining = [u for u in units if u not in preferred]
    return [replace(req, units=preferred), *([replace(req, units=remaining)] if remaining else [])]


def answer(engine, req, principal, purpose, cfg, scope, out, history=(), combined=None, runtimes=None, shadow_columns=None):
    requests = scope_requests(engine, req, principal, cfg, scope)
    captured = _shadow_capture.get()
    for index, selected in enumerate(requests):
        if index or (scope.products and not req.units and not req.all_units and selected.units
                     and not set(selected.units) & set(scope.units)):
            out['trace'].append({'step': 'scope_fallback', 'reason': 'The preferred page unit cannot answer.',
                                 'units': selected.units})
        start = len(captured) if captured is not None else 0
        result = selected_answer(engine, selected, principal, purpose, cfg, scope, dict(out), history, combined,
                                 default_units=selected.units if selected is not req else (), runtimes=runtimes,
                                 shadow_columns=shadow_columns)
        readings = result.pop('_readings', ())
        if readings:
            result['notes'] = list(dict.fromkeys([*(result.get('notes') or []), *readings]))
        if not engine.needs_scope_fallback(result) or index == len(requests) - 1:
            return result
        if captured is not None:
            del captured[start:]
    return result


OPERATORS = {'eq': '=', 'ne': '<>', 'lt': '<', 'le': '<=', 'gt': '>', 'ge': '>=', 'contains': 'contains',
             'starts_with': 'starts with'}


def question_filter_notes(plan, question, turn, names=None):
    """Each filter the plan takes from the question's own words, as a note: the words, then the condition they set. An
    exclusion (not in, or empty) and a negation are one condition each, never their parts. A code with a declared name
    (names: member_names) reads as the name."""
    names = names or {}
    def said(node):
        spans = [*node.evidence, *(s for o in plan.obligations if o.state == 'applied' and node.id in o.targets for s in o.spans)]
        return sorted({(s.start, s.end) for s in spans if s.turn == turn and s.end <= len(question)})

    def scalar(value, column=None):
        text = json.dumps(value.value) if isinstance(value.value, bool) else str(value.value)
        return names.get((column, text), text)

    def condition(node):
        left = getattr(node, 'left', None)
        if getattr(left, 'kind', None) != 'column':
            return None
        column = left.name.id.split('.', 1)[-1]
        if node.kind == 'compare':
            return f"{column} {OPERATORS[node.op]} {scalar(node.value, left.name.id)}" + (' (absolute amount)' if node.magnitude else '')
        if node.kind == 'in':
            return f"{column} in ({', '.join(scalar(v, left.name.id) for v in node.values)})"
        return f"{column} is {'' if node.is_null else 'not '}empty"

    def walk(node, spans=()):
        spans = sorted({*spans, *said(node)})
        excluded = exclusion(node)
        if excluded is not None:
            text = condition(excluded)
            return [(spans, text.replace(' in (', ' not in (', 1) + ' or empty')] if text else []
        if node.kind == 'not':
            text = condition(node.child)
            return [(sorted({*spans, *said(node.child)}), f'not ({text})')] if text else []
        if node.kind in ('all', 'any', 'membership'):
            return [note for child in (node.children if node.kind != 'membership' else node.filters) for note in walk(child)]
        text = condition(node)
        return [(spans, text)] if text else []

    notes = []
    for root in (*plan.filters, *(f for m in plan.measures for f in m.filters)):
        for spans, text in walk(root):
            if spans:
                words = ' ... '.join(dict.fromkeys(question[start:end] for start, end in spans))
                notes.append(f'filter read from "{words}": {text}')
    return list(dict.fromkeys(notes))


def asked_label(measure, plan, vocab, question, turn):
    """The one name a single figure is given, in its column, its context and its answer text."""
    if measure.kind == 'distinct_count':
        return count_label(measure, vocab, question, turn, [s for o in plan.obligations if measure.id in o.targets
                                                           for s in o.spans if s.turn == turn])
    return measure_label(measure, plan, vocab, question, turn)


def public_context(engine, req, cfg, history, plan, vocab, describe, turn):
    """The existing answer context, resolved before execution and reused after it."""
    if len(plan.measures) != 1 or plan.comparisons or plan.derived:
        return None
    measure = plan.measures[0]
    ctx = vocab.contexts[measure.context.id]
    authored = engine.semantics.contexts if cfg.graph else {}
    context = None
    if history and history[-1].clarification is not None and ctx.id in authored:
        context = {'context': ctx.id, 'id': ctx.id, 'label': ctx.label, 'term': ctx.term,
                   'fact': ctx.fact, 'measure': ctx.measure, 'time_mode': measure.mode}
    if ctx.id in authored and any(j.required for j in ctx.joins):
        context = {**describe(ctx, req).about(), 'id': ctx.id, 'label': ctx.label,
                   'count': measure.entity.id if measure.kind == 'distinct_count' else None, 'time_mode': measure.mode}
    label = asked_label(measure, plan, vocab, req.question, turn)
    if label != ctx.label:
        context = {**(context or {}), 'term': label, 'label': label}
    if measure.kind == 'distinct_count':
        context = {**(context or {}), 'count': measure.entity.id}
    return context


def reading_notes(readings, vocab, planner):
    """An AI-Generated answer's readings as notes, each phrase the model read as a declared meaning other than its own
    words: read 'we book' as the declared measure's label. A period's calendar is the period line's to state, not a reading."""
    notes = []
    for reading in readings:
        entry = vocab.entries.get(reading['id'])
        if (entry is not None and entry.kind != 'calendar'
                and (entry.label or '').casefold() != reading['phrase'].casefold()):
            notes.append(f"read '{reading['phrase']}' as {entry.label}")
    return planner.safe(list(dict.fromkeys(notes)))


def full_match(planner, question, turn):
    """Whether the question has no piece the release's vocabulary leaves unknown."""
    return not any(p.kind == 'unknown' for p in planrules.ledger(question, turn, planner.vocabulary))


def rules_floor_mode(full, model_asked, follow_up):
    """The answer mode of a figure the rules plan gave outside rules first: certified where every word is certified and
    no model may have planned an earlier turn; AI-Generated where the model path served it as its floor; else, with no
    model asked and a word the release does not know, not sure."""
    if full and not (model_asked and follow_up):
        return 'certified'
    return 'ai_generated' if model_asked else 'not_sure'


def rules_first(planner, question, rules, turn, history=(), release_periods=None, follow_ups=True):
    """Rules first: (the planner's result, the rules reply) where the rules fully match, that is they propose a plan,
    the question has no unknown piece and check_plan passes; the result is then certified, an answer or the compiler's or
    the SQL gate's decline. (None, the rules reply) otherwise, with nothing recorded on the graph and no model asked.
    Without follow_ups, a rules plan built on an earlier turn is not a full match: where a model may have planned that
    turn, the history cannot show its words were certified."""
    replies = []
    def proposal(text, vocabulary):
        replies.append(rules(text, vocabulary))
        return replies[-1]
    with graph_recording(None):
        checked = planner.prepare(question, proposal, turn, history)
    reply = replies[0] if replies else None
    if (not isinstance(checked, CheckedPlan) or not full_match(planner, question, turn)
            or (not follow_ups and reply.kind == 'propose' and reply.base_turn is not None)):
        return None, reply
    return planner.answer(question, reply, turn, history, release_periods=release_periods), reply


def model_fallback(planner, question, turn, history, interpret, floor, grounding, release_periods):
    """Where the rules do not fully match: the planner's result from today's path, the model's plan checked first and
    the rules plan its floor where a model is configured, else the rules plan's own decline or answer."""
    return planner.answer(question, interpret, turn, history, grounding=grounding,
                          fallback=floor if grounding else None, release_periods=release_periods)


def model_plan_is_rules_plan(planner, question, rules, turn, history, result):
    """Under model_first: whether the served model plan is the rules plan and the question fully matches."""
    if history or not full_match(planner, question, turn):
        return False
    with graph_recording(None):
        checked = planner.prepare(question, rules, turn, history)
    return isinstance(checked, CheckedPlan) and checked.plan.model_dump(mode='json', by_alias=True) == result.get('plan')


def selected_answer(engine, req, principal, purpose, cfg, scope, out, history=(), combined=None, default_units=(), runtimes=None,
                    shadow_columns=None):
    descriptions = {}
    def describe(context, request):
        asked = Asked.of(request)
        key = (context.id, repr(asked))
        if key not in descriptions:
            descriptions[key] = engine.semantics.plan(context, asked)
        return descriptions[key]
    contract = getattr(combined, 'plan_vocabulary', None)
    if contract is not None and not cfg.graph:
        vocab, runtime = planrules.control_vocabulary(engine, principal, purpose, scope, req)
    else:
        vocab = planrules.vocabulary(engine, principal, purpose, scope, cfg, req, history)
        runtime = planrules.snapshot(engine, cfg.graph)
    if contract is not None:
        allowed = {entry['id'] for entry in contract['names']}
        vocab = replace(vocab,
            entries={k: v for k, v in vocab.entries.items() if k in allowed},
            phrases={p: tuple(i for i in ids if i in allowed) for p, ids in vocab.phrases.items() if any(i in allowed for i in ids)},
            contexts={k: v for k, v in vocab.contexts.items() if k in allowed},
            measures={k: v for k, v in vocab.measures.items() if k in allowed},
            entities={k: v for k, v in vocab.entities.items() if k in allowed},
            calendars={k: v for k, v in vocab.calendars.items() if k in allowed})
    vocab = replace(vocab, default_units=tuple(default_units))
    cohort = cfg.graph and bool(planrules.cohort_terms(engine, req))
    hidden = Hidden.of(engine, principal)
    turn = hashlib.sha256(json.dumps([req.question, cfg.label, vocab.binding.model_dump(mode='json'),
        [entry.model_dump(mode='json', by_alias=True) for entry in history]], sort_keys=True).encode()).hexdigest()[:32]
    configured = combined is not None and any(cfg.uses_model(s) for s in ('resolution', 'sql_source'))
    model = combined.llm if configured else None
    fallback_mode = ask_ai_fallback(model is not None)
    if fallback_mode == 'off':
        model = None
    source = 'model:' + model.name if model is not None else 'rules'
    runtrace.use(cfg, model)
    out.update(combined=False, versions={**out['versions'], 'planner': 'query-plan-v1', 'interpretation': source})
    if vocab.denial(req.question):
        return {**out, 'behaviour': 'refuse', 'reason': 'The requested read is not permitted. Contact the access administrator.'}
    interpreted = []
    failed = False
    grounding = model is not None and getattr(combined, 'plan_grounding', True)
    def failure():
        nonlocal failed
        failed = True
    read_rules = {}
    rule_shadows = {}
    rules_planner = rulesport.rules_planner()
    def rules(question, vocabulary):
        """The rules reply, read once per question and vocabulary from the rules-planner port (rulesport), whichever
        adapter it gives, and held for a background run to resume on. The held replies are matched by the order they
        were asked in, so no other held step asks for one: interpret reads the rules here, not inside its own held model
        reply."""
        guard = getattr(engine, 'shadow_read_guard', getattr(engine, 'shadow_write_guard', nullcontext))
        computed = False
        def compare_reply(reply):
            with guard():
                semantics = getattr(engine, 'semantics', None)
                selected_runtime = getattr(semantics, 'plan_snapshot' if cfg.graph else 'baseline_snapshot', None)
                sidecar = None
                try:
                    stamp = _shadow_term_stamp(engine)
                    candidate = (_shadow_candidate(engine, req, cfg, turn, history, cohort, planner, vocabulary, reply,
                                                   'coded', shadow_columns, selected_runtime or runtime)
                                 if question == req.question else None)
                    templates = getattr(engine.g, '_shadow_templates', getattr(engine.g, '_templates', None))
                    model_eligible = bool(templates and any(s['stage'] != 'retired' for s in templates.shapes.values()))
                    if candidate is not None and stamp != _shadow_term_stamp(engine):
                        logging.getLogger(__name__).warning('shadow comparison inputs changed during comparison')
                    elif candidate is not None:
                        sidecar = {'observation': asdict(candidate), 'model_eligible': model_eligible,
                                   'term_stamp': stamp}
                except (QueryCancelled, RequestDeadlineExceeded):
                    raise
                except Exception:
                    logging.getLogger(__name__).warning('shadow comparison could not be recorded')
            return sidecar
        def compute():
            nonlocal computed
            computed = True
            with guard():
                reply = rulesport.planned(rules_planner, rulesport.RulesQuestion(
                    engine, req, question, vocabulary, cfg, turn, tuple(history), cohort))
                sidecar = compare_reply(reply)
            return (reply.model_dump(mode='json', by_alias=True, exclude_unset=True) if active() else reply), sidecar
        key = (question, id(vocabulary))
        if key not in read_rules:
            held, sidecar = memo_with_sidecar('rules:' + turn, compute) if active() else compute()
            reply = reply_adapter.validate_python(held) if active() else held
            if active() and not computed and question == req.question:
                sidecar = compare_reply(reply)
            read_rules[key] = (vocabulary, reply)
            rule_shadows[key] = sidecar
            if active() and sidecar is None and _shadow_capture.get() is not None and question == req.question:
                logging.getLogger(__name__).warning('shadow checkpoint has no comparison evidence')
        return read_rules[key][1]
    def interpret(question, vocabulary):
        def generate():
            return planmodel.read(model, question, vocabulary, history, turn, grounding=grounding, on_failure=failure)
        def compute():
            reply = generate()
            return {"reply": reply.model_dump(mode="json", by_alias=True, exclude_unset=True), "failed": failed}
        if model is None:
            reply = rules(question, vocabulary)
        elif active():
            held = memo("interpret:" + turn, compute)
            if held["failed"]:
                failure()
            reply = reply_adapter.validate_python(held["reply"])
        else:
            reply = generate()
        interpreted.append(reply)
        return reply
    def floor(question, vocabulary):
        return interpreted[0] if failed else rules(question, vocabulary)
    citations = {c.id: ('pack:' + c.term,) for c in vocab.contexts.values()}
    readable_units = engine.g.readable_units(principal) if principal else set(engine.UNIT_ORDER)
    def chosen(plan, sql, guard):
        public = {'sql': sql, 'sql_source': 'query-plan-v1', 'guard': guard}
        if len(plan.measures) == 1 and not plan.comparisons and not plan.derived:
            public['unit'] = vocab.contexts[plan.measures[0].context.id].unit
        stream_event({'event': 'plan', 'plan': planner.safe(public)})
        context = public_context(engine, req, cfg, history, plan, vocab, describe, turn)
        if context is None and req.term:
            context = {'term': req.term}
        stream_event({'event': 'context', 'context': planner.safe(context)})

    planner = Planner(vocab, engine.w, hidden, citations, runtimes=runtimes, readable_units=readable_units,
                      on_plan=chosen if streaming() else None,
                      answer_release_periods=getattr(engine.g, 'answer_release_periods', None))
    runtrace.stage('plan')
    def release_periods(needs, runtime):
        return engine.g.release_periods(needs, runtime)
    order = ask_order() if model is not None else 'rules_first'
    certified, rules_reply = (rules_first(planner, req.question, rules, turn, history, release_periods, model is None)
                              if order == 'rules_first' else (None, None))
    readings = []
    if certified is not None:
        interpreted.append(rules_reply)
        result = certified
        out['versions'] = {**out['versions'], 'interpretation': 'rules'}
    elif model is not None and order == 'rules_first':
        examples = planmodel.verified_examples(engine)
        def read(corrections):
            def compute():
                return planmodel.read_on(model, req.question, vocab, history, turn, examples=examples,
                                         corrections=corrections).dump()
            reading = planmodel.Reading.load(memo('interpret-on:' + turn, compute) if active() else compute())
            if reading.reply is not None and not interpreted:
                interpreted.append(reading.reply)
            return reading
        result = planner.answer_model(req.question, read, rules, turn, history, release_periods)
        if result['kind'] == 'result' and 'model_refusal' not in result:
            readings = reading_notes(result.get('readings', ()), vocab, planner)
            out['versions'] = {**out['versions'], 'interpretation':
                               f"{source} prompt:{planmodel.on_prompt_version()} schema:{result.get('schema') or 'none'}"
                               f" examples:{len(examples)}"}
    else:
        result = model_fallback(planner, req.question, turn, history, interpret, floor, grounding, release_periods)
    reply = interpreted[0] if interpreted else None
    if (refusal := result.get('model_refusal')) and (reply is not None or refusal.get('phase') in UNREAD_PHASES):
        out['trace'].append({'step': 'model_plan_refused', 'outcome': refusal['kind'],
                             'reason': '; '.join(refusal.get('missing') or []) or 'Choose the intended declared meaning.',
                             'choices': refusal.get('choices', []), 'fallback': refusal.get('fallback')})
    executed_source = 'rules' if certified is not None or result.get('model_refusal', {}).get('fallback') == 'rules' else source
    if certified is not None:
        answer_mode = 'certified'
    elif result['kind'] != 'result':
        answer_mode = 'not_sure'
    elif executed_source == 'rules':
        answer_mode = rules_floor_mode(full_match(planner, req.question, turn), model is not None, bool(history))
    else:
        answer_mode = 'certified' if model_plan_is_rules_plan(planner, req.question, rules, turn, history, result) else 'ai_generated'
    answered_by = 'rules' if executed_source == 'rules' else 'model'
    graph_read('served_planner', 'coded' if answered_by == 'rules' else 'model')
    if answered_by == 'rules':
        out['versions'] = {**out['versions'], 'interpretation': 'rules'}
    out.update(answer_mode=answer_mode, answered_by=answered_by, answer_id=turn, ai_fallback=fallback_mode,
               model=model.name if model is not None and executed_source != 'rules' else 'none')
    held_keys = [key for key in read_rules if key[0] == req.question]
    held_rules = [read_rules[key] for key in held_keys]
    read_vocabulary, served_reply = (held_rules[0] if answered_by == 'rules' and held_rules
                                     else (held_rules[0][0] if held_rules else vocab, reply))
    runtrace.plan_ledger(planledger.block(req.question, turn, read_vocabulary, served_reply, result, bool(history)))
    served = 'coded' if answered_by == 'rules' else 'model'
    sidecar = rule_shadows.get(held_keys[0]) if held_keys else None
    if isinstance(sidecar, dict):
        if 'term_stamp' not in sidecar:
            logging.getLogger(__name__).warning('shadow checkpoint could not be read')
        elif sidecar['term_stamp'] != _shadow_term_stamp(engine):
            logging.getLogger(__name__).warning('shadow checkpoint inputs changed before resume')
        elif served == 'coded' or sidecar.get('model_eligible') is True:
            if (observation := _saved_shadow(sidecar.get('observation'), served)) is not None:
                if (captured := _shadow_capture.get()) is not None:
                    captured.append(observation)
    elif sidecar is not None:
        logging.getLogger(__name__).warning('shadow checkpoint could not be read')
    elif not held_rules:
        _shadow_compare(engine, req, cfg, turn, history, cohort, planner, read_vocabulary, None,
                        served, shadow_columns, runtime)
    if answer_mode == 'ai_generated' and readings:
        out['_readings'] = readings
    out['trace'].append({'step': 'plan', 'planner': 'query-plan-v1', 'interpretation': executed_source, 'outcome': result['kind'],
                         'order': order, 'answer_mode': answer_mode, 'answered_by': answered_by, 'answer_id': turn,
                         'ai_fallback': fallback_mode, 'model': out['model']})
    if result['kind'] != 'result':
        why = '; '.join(result.get('missing') or [])
        if result['kind'] == 'refuse' and cfg.graph:
            requested = set(req.units)
            if not requested and reply is not None and reply.kind == 'propose':
                requested = {vocab.contexts[m.context.id].unit for m in reply.plan.measures if m.context.id in vocab.contexts}
            pairs = [(a, b) for n, a in enumerate(engine.UNIT_ORDER) for b in engine.UNIT_ORDER[n + 1:]
                     if {a, b} <= requested and frozenset((a, b)) in vocab.no_join]
            if pairs and (req.special == 'key_name_join' or req.terms_found or req.counted
                          or not_joinable_named(req.question.casefold(), engine)):
                out['joins_refused'] = [a + '-' + b for a, b in pairs]
                certifying = [{c.unit for c in vocab.contexts.values() if c.term == term} for term in req.terms_found]
                sole = {next(iter(units)) for units in certifying if len(units) == 1}
                named = [refused for refused in pairs if len(sole) == 1 and sole <= set(refused)]
                a, b = (named or pairs)[0]
                out['fact'] = next({k: v for k, v in f.items() if k not in ('a', 'b')}
                                   for f in runtime.no_join_declarations if {f['a'], f['b']} == {a, b})
                context_units = sole if named else {c.unit for c in vocab.contexts.values()
                                                    if c.term in req.terms_found and any(j.required for j in c.joins)}
                if len(context_units) == 1 and b in context_units:
                    a, b = b, a
                why = f"No join path between {a} and {b}. {out['fact']['note']}"
        if len(req.units) > 1 and cohort:
            from kge.services.ask.engine.combined import across
            held = across(engine, req.units)
            out.update(joins_refused=[f['pair'] for f in held['facts']], certified_joins=held['certified'])
            why = held['reason']
        if result.get('guard'):
            out['guard'] = result['guard']
        if result.get('skipped_runtimes'):
            out['_skipped_runtimes'] = result['skipped_runtimes']
        if model is None and why and re.search(r'\bunrequested node\b', why):
            why = 'The request could not be matched to a certified measure.'
        if why and result['kind'] in ('abstain', 'refuse') and result.get('phase') not in STATED_PHASES and not result.get('skipped_runtimes'):
            why = (gate_decline(result['guard']['problems']) if result.get('guard') and not result['guard'].get('passed')
                   else plain_decline(why, reply.plan if reply is not None and reply.kind == 'propose' else None,
                                      vocab, req.question, turn))
        out.update(DECISIONS[result['kind']], reason=why or 'Choose the intended declared meaning before a figure can be computed.')
        if model is None and cohort and result['kind'] == 'abstain' and not req.problem and len(req.units) == 1 and scope.products is None:
            unit = req.units[0]
            available = {c.term for c in runtime.contexts.values() if c.unit == unit}
            available.update(m['term'] for m in runtime.mapping_declarations if m['unit'] == unit)
            missing = sorted(set(req.terms_found) - available)
            if missing:
                out['reason'] = f"Not certified in {engine.UNIT_LABEL[unit]}: {', '.join(missing)}. The answer would rest on a guess."
        declared_formula = bool(vocab.derived_measures) and any(i in vocab.derived_measures
            for p in planrules.ledger(req.question, turn, vocab) for i in p.candidates)
        if (cfg.graph and result['kind'] == 'abstain' and req.term and not req.problem and not declared_formula
                and not any(c.term in req.terms_found for c in vocab.contexts.values())
                and not any(m['term'] in req.terms_found for m in runtime.mapping_declarations)):
            out['reason'] = f"'{req.term}' has no certified implementation in any warehouse."
        planner_reason = '; '.join(result.get('missing') or [])
        rules_abstained = req.problem and result['kind'] == 'abstain' and model is None and not (reply is not None and reply.kind == 'propose')
        planner_owns_reason = bool(rules_abstained and planner_reason not in ('', req.problem))
        if rules_abstained and not planner_owns_reason:
            out.update(engine.request_abstention(req, req.problem))
        if (result['kind'] == 'abstain' and result.get('phase') not in STATED_PHASES and cfg.graph
                and result.get('missing') and all(vocab.candidates(m) or engine.ALIASES.get(m.casefold()) in req.terms_found
                    for m in result['missing'])):
            contexts = list({c.id: c for term in req.terms_found for c in engine.semantics.contexts_of(term)
                             if c.id in vocab.contexts and any(j.required for j in c.joins)}.values())
            if len(contexts) == 1:
                diagnostic = describe(contexts[0], req).decision
                if diagnostic and diagnostic['behaviour'] in ('abstain', 'abstain_with_reason'):
                    out['reason'] = planner.safe(diagnostic['reason'])
        if result.get('phase') == 'release':
            out['behaviour'] = 'abstain_with_reason'
            out['sql_run'] = result['sql_run']
            if reply is not None and reply.kind == 'propose' and len(reply.plan.measures) == 1:
                ctx = vocab.contexts[reply.plan.measures[0].context.id]
                tables = [j.target for j in ctx.joins if j.required and j.target in vocab.releases]
                if len(tables) == 1:
                    out['reason'] = release_decline(out['reason'], tables)
            if 'is not released yet.' not in out['reason']:
                out['reason'] = release_decline(out['reason'])
        elif result.get('phase') == 'observations':
            out['behaviour'] = 'abstain_with_reason'
            out['sql_run'] = result['sql_run']
        elif (result['kind'] == 'abstain' and (not req.problem or planner_owns_reason) and 'certified_joins' not in out
              and out['reason'] != 'No glossary term recognised in the question.' and not out['reason'].startswith('Not certified in ')
              and 'no figure' not in out['reason'].casefold() and 'none is given' not in out['reason'].casefold()):
            out['reason'] = out['reason'].rstrip('. ') + ('' if out['reason'].endswith('?') else '.') + ' No figure is given.'
        if result['kind'] == 'clarify':
            replied = (reply is not None and reply.kind == 'clarify' and bool(reply.spans)
                       and ('model_refusal' not in result or result['model_refusal'].get('kind') == 'clarify'
                            and bool(result['model_refusal'].get('choices'))))
            if len(result['choices']) > 1:
                out['reason'] = 'Which meaning do you intend?'
            entities = [vocab.entities[c['id']] for c in result['choices'] if c['id'] in vocab.entities]
            if len(entities) == len(result['choices']) == 1 and req.special is None and replied:
                unit = entities[0]['table'].split('.')[0]
                written_units = {i: p.text for p in planrules.ledger(req.question, turn, vocab)
                                 for i in p.candidates if i in req.units}
                unresolved = [written_units.get(u, engine.UNIT_LABEL[u]) for u in req.units if u != unit]
                if unresolved:
                    label = written_units.get(unit, engine.UNIT_LABEL[unit])
                    noun = entities[0]['label'].removeprefix(label + ' ')
                    out['reason'] = (f"{' and '.join(unresolved)} {planrules.plural_forms(noun)[1]} is unresolved: "
                                     f"{noun} names only the declared {label} entity.")
            spans = tuple(reply.spans) if replied else tuple(
                p.span for p in planrules.ledger(req.question, turn, vocab) if p.text in result.get('missing', ()) or set(p.candidates) & {c['id'] for c in result.get('choices', ())})
            clarification = Clarification(spans=spans or (Span(turn=turn, start=0, end=len(req.question)),),
                slot='meaning', choices=tuple(Choice.model_validate(c) for c in result['choices']))
            out['clarification'] = planner.safe(clarification.model_dump(mode='json'))
            out['history'] = [h.model_dump(mode='json', by_alias=True) for h in history] + [HistoryEntry(turn_id=turn, question=req.question, plan=None,
                clarification=clarification, binding=vocab.binding).model_dump(mode='json', by_alias=True)]
        if (cfg.graph and result['kind'] == 'abstain' and not req.problem and result.get('phase') not in STATED_PHASES and not declared_formula
                and measure_question(req.question)
                and (not any(c.term in req.terms_found for c in vocab.contexts.values())
                     or any(p.kind == 'unknown' for p in planrules.ledger(req.question, turn, vocab)))):
            labels = declared_measure_labels(req, vocab, default_units)
            if 'has no certified implementation in any warehouse' in out['reason'] and any(
                    p.kind == 'unknown' for p in planrules.ledger(req.question, turn, vocab)):
                out['reason'] = 'The requested measure is not declared. No figure is given.'
            if labels:
                out['reason'] += ' Declared measures here are ' + planner.safe('; '.join(labels)) + '. Which would you like?'
        if (reply is not None and reply.kind == 'abstain' and result['kind'] == 'abstain'
                and req.period is not None and not req.problem
                and re.search(r'not (?:yet )?released|unreleased|not started|future', out['reason'], re.I)):
            release_plan = rulesport.planned(rules_planner, rulesport.RulesQuestion(
                engine, req, req.question, vocab, cfg, turn, (), cohort))
            if release_plan.kind == 'propose' and (offer := planner.release_offer(
                    req.question, release_plan.plan, turn, engine.g.release_periods)):
                out['reason'] = out['reason'].rstrip('. ') + '. ' + offer
        if (said := model_abstained(reply, result)) and said.rstrip('.') not in out['reason']:
            out['reason'] = out['reason'].rstrip() + ('' if out['reason'].rstrip().endswith(('.', '?')) else '.') + ' ' + said
        if 'history' in out:
            out['history'] = planner.safe(out['history'])
        return out
    plan = QueryPlan.model_validate(result['plan'])
    out.update(result=result['result'], sql=result['sql'], guard=result['guard'], sql_source='query-plan-v1')
    if engine.w.runtimes_active(runtimes):
        for k in ('reference_sql', 'runtime', 'physical_tables', 'runtime_certified', 'certified_in', 'live_data', 'queried_at'):
            if k in result:
                out[k] = result[k]
    if result.get('skipped_runtimes'):
        out['_skipped_runtimes'] = result['skipped_runtimes']
    answered_time = QueryPlan.model_validate({**result['plan'], 'time': result['answered_time']}).time if 'answered_time' in result else plan.time
    from kge.semantics.planperiods import time_bounds, time_calendar, to_date_operation
    current = to_date_operation(answered_time)
    if answered_time.kind == 'ytd' or current:
        from datetime import timedelta
        first, end = time_bounds(answered_time, vocab.calendars)
        last = end - timedelta(days=1)
        fiscal = vocab.calendars[time_calendar(answered_time)]['year_start_month'] != 1
        grain = {'ytd': 'year', 'mtd': 'month', 'qtd': 'quarter'}[current or 'ytd']
        prefix = ('fiscal ' if fiscal and grain != 'month' else '') + grain + ' to date'
        start = first.strftime('%b %Y' if first.year != last.year else '%b')
        one_month = current and (first.year, first.month) == (last.year, last.month)
        out['period_label'] = f"{prefix}, {last:%b %Y}" if one_month else f"{prefix}, {start}–{last:%b %Y}"
    if result['months']:
        out['months'] = result['months']
    authored = engine.semantics.contexts if cfg.graph else {}
    if any(m.context.id in authored for m in plan.measures):
        out['sql_run'] = result['sql_run']
    out['history'] = [h.model_dump(mode='json', by_alias=True) for h in history] + [HistoryEntry(turn_id=turn, question=req.question, plan=plan,
        clarification=None, binding=vocab.binding).model_dump(mode='json', by_alias=True)]
    out['history'] = planner.safe(out['history'])
    out[RECEIPT_PLAN] = receipt_plan(plan.model_dump(mode='json', by_alias=True), vocab, [n.id for n in plan.nodes()])
    rows = [r for r in result['result']['rows'] if not r.get('total')]
    contexts = [vocab.contexts[m.context.id] for m in plan.measures]
    said = planner.safe(question_filter_notes(plan, req.question, turn, member_names(getattr(vocab.semantics.d, 'VOCABULARY', ()))))
    outputs = [column for column in result['result']['columns'] if column['kind'] != 'dimension']
    observed = {source for row in rows for column, cell in zip(outputs, row['cells'])
                if cell['value']['kind'] != 'null' for source in column['lineage']}
    out['trace'].append({'step': 'sources', 'tables': sorted({c.fact for c in contexts if c.id in observed})})
    if contexts and all(c.id.startswith('cohort:') for c in contexts):
        worded = cfg.with_stages(renderer='template') if ask_ai_fallback_setting() == 'off' else cfg
        return cohort_answer(engine, combined, req, principal, purpose, worded, scope, out, plan, contexts, rows)
    if len(plan.measures) == 1 and not plan.comparisons and not plan.derived:
        ctx = contexts[0]
        count = plan.measures[0].kind == 'distinct_count'
        context = public_context(engine, req, cfg, history, plan, vocab, describe, turn)
        if context is not None:
            out['context'] = context
        out.update({"behaviour": "figure"}, unit=ctx.unit, column=ctx.values[0].split('.', 1)[1] if ctx.values else '',
                   join_path=[j.condition for j in ctx.joins if j.required], notes=[] if plan.time.kind != 'all_available' else ['no period asked: every month the filters keep'])
        if result['result'].get('outside_calendar'):
            out['notes'].append(OUTSIDE_CALENDAR)
        if ctx.id in authored and any(j.required for j in ctx.joins):
            described = describe(ctx, req)
            about = described.about()
            if count:
                about['count'] = plan.measures[0].entity.id
            out['join_path'], out['notes'] = about['joins'], list(dict.fromkeys([*out['notes'], *described.notes]))
            if plan.time.kind != 'all_available':
                out['notes'] = [n for n in out['notes'] if n != 'no period asked: every month the filters keep']
            if plan.measures[0].mode == 'as_reported':
                out['notes'].append('as reported: each version is read at the declared release freeze date')
            out['trace'].insert(1, {'step': 'context', **about})
            if req.latest and result['months']:
                from datetime import date
                out['notes'].append(date.fromisoformat(result['months'][-1]).strftime('%B %Y, the latest released month'))
        out['notes'] = list(dict.fromkeys([*out['notes'], *said]))
        out['column'] = figure_column(ctx, runtime.output_metadata.get(ctx.id, {}))
        if not any(j.required for j in ctx.joins):
            used = {n.id for d in plan.dimensions if d.kind in ('column', 'hierarchy') for b in d.bindings for n in b.path}
            used.update(n.id for p in plan.filters if getattr(getattr(p, 'left', None), 'kind', None) == 'column' for n in p.left.path)
            joins = [j.condition for j in ctx.joins if j.id in used]
            out['join_path'] = [re.sub(r'\s*=\s*', ' -> ', j) for j in joins]
            if req.period and req.period.kind in ('calendar_quarter', 'fiscal_quarter', 'year'):
                _, rollups, note = engine.period_clause(ctx.unit, ctx.fact.split('.', 1)[1], req.period, cfg, req.agg)
                out['join_path'][:0] = [m.group(1) + ' -> ' + m.group(2) for j in rollups
                    if (m := re.fullmatch(r'JOIN \w+ ON (\w+\.\w+) = (\w+\.\w+)', j))]
                if note and note not in out['notes']:
                    out['notes'].append(note)
        if count:
            entity = vocab.entities[plan.measures[0].entity.id]
            metadata = runtime.output_metadata.get(ctx.id, {})
            out['column'] = metadata['tbl'] + '.' + metadata['col'] if metadata else ', '.join(entity['table'].split('.', 1)[1] + '.' + key for key in entity['key'])
        if not plan.dimensions:
            out['figure'] = value(rows[0]['cells'][0]) if rows else None
        elif len(plan.dimensions) == 1:
            out.update(figure=None, rows={(f"{r['end']} {r['rank']}: " if r.get('end') else '') + str(r['dimensions'][0].get('value')): value(r['cells'][0]) for r in rows},
                       group_by=plan.dimensions[0].bindings[0].column.id.split('.', 1)[1] if plan.dimensions[0].kind != 'time' else plan.dimensions[0].grain)
        if (len(plan.dimensions) == 1 and len(rows) == 1 and plan.dimensions[0].kind in ('column', 'hierarchy')
                and any(p.kind == 'compare' and p.op == 'eq' and p.left.kind == 'column'
                        and p.left.name.id == plan.dimensions[0].bindings[0].column.id for p in plan.filters)):
            out['figure'] = value(rows[0]['cells'][0])
            out.pop('rows', None)
            out.pop('group_by', None)
        memberships = [p for p in plan.filters if p.kind == 'membership']
        if len(memberships) == 1:
            predicate = memberships[0]
            far = vocab.contexts[predicate.context.id]
            fact = next(c for c in engine.semantics.certified if c['declared_join'] == predicate.relation.id)
            mine = vocab.entities[plan.measures[0].entity.id]['label'] if count else ctx.label
            theirs = vocab.entities[predicate.entity.id]['label']
            explanation = f"counts each {mine} a certified match links to a {theirs}, once" if count else f"aggregates {mine} only for members a certified match links to a {theirs}"
            out.update(certified_join=fact, warehouses_involved=sorted({ctx.unit, far.unit}),
                       join_path=list(dict.fromkeys([*out['join_path'], predicate.relation.id])), reason=f"{engine.UNIT_LABEL[ctx.unit]} and {engine.UNIT_LABEL[far.unit]} join through the certified join {fact['declared_join']} ({fact['note']}): the figure {explanation}.")
        elif ctx.id not in authored and not plan.dimensions:
            metadata = runtime.output_metadata.get(ctx.id, {})
            out['parts'] = [{k: metadata.get(k, '') if cfg.graph else '' for k in ('grain', 'currency', 'calendar', 'conversion')} | {
                'unit': ctx.unit, 'status': 'ok' if out['figure'] is not None else 'abstain',
                'reason': '; '.join(rows[0]['cells'][0]['null_reasons']) if rows else 'No observations matched the request.',
                'column': out['column'], 'value': out['figure'],
                'sql': out['sql'], 'guard': out['guard'], 'join_path': out['join_path'], 'notes': out['notes']}]
        label = asked_label(plan.measures[0], plan, vocab, req.question, turn)
        if label != ctx.label:
            out['context'] = {**out.get('context', {}), 'term': label, 'label': label}
        if count:
            out['context'] = {**out.get('context', {}), 'count': plan.measures[0].entity.id}
        return out
    same_term = len({c.term for c in contexts}) == 1
    if same_term and len({c.id for c in contexts}) > 1 and not plan.dimensions and not plan.comparisons and not plan.derived:
        parts = []
        metadata = runtime.output_metadata
        for n, ctx in enumerate(contexts):
            item = metadata.get(ctx.id, {}) if cfg.graph else {}
            parts.append({**item, 'unit': ctx.unit, 'status': 'ok' if rows and value(rows[0]['cells'][n]) is not None else 'abstain',
                          'reason': '; '.join(rows[0]['cells'][n]['null_reasons']) if rows else 'No observations matched the request.',
                          'value': value(rows[0]['cells'][n]) if rows else None,
                          'column': figure_column(ctx, metadata.get(ctx.id, {})),
                          'sql': result['sql'], 'guard': result['guard'],
                          'join_path': [j.condition for j in ctx.joins if j.required], 'notes': []})
        verdict, why = engine.commensurable(parts, req, cfg)
        missing = [p['unit'] for p in parts if p['value'] is None]
        if missing:
            verdict, why = False, 'incomplete: no observation for ' + ', '.join(missing) + '. A total without every part is not the total asked for'
        out.update(VERDICTS[verdict],
                   parts={p['unit']: p['value'] for p in parts}, plan=parts, commensurable=verdict, reason=why, missing_parts=missing,
                   joins_refused=[pair(a['unit'], b['unit'], engine) for n, a in enumerate(parts) for b in parts[n + 1:]
                                  if cfg.graph and frozenset((a['unit'], b['unit'])) in vocab.no_join])
        if verdict is not False:
            out['figure'] = round(sum(p['value'] for p in parts if p['value'] is not None), 2)
        return out
    period = out.get('period_label') or fixed_period_words(plan, vocab.calendars)
    out.update({"behaviour": "combined"}, combined=True, figures=[], citations=[], not_cited=[], withheld=[],
               answer_text=(period + ': ' if period else '') + render(result['result']), phrased_by='template')
    if said:
        out['notes'] = [*out.get('notes', []), *said]
    if result['result'].get('outside_calendar'):
        out['answer_text'] += '\n\n' + OUTSIDE_CALENDAR
    return out


def figure_column(ctx, metadata):
    """The column a figure is reported on, without its unit: for a count, the column its certified mapping names (the
    active flag, not the key a declared count counts), so the figure binds the citation that defines its term; else
    the context's first value."""
    if ctx.aggregation == 'count' and metadata:
        return metadata['tbl'] + '.' + metadata['col']
    return ctx.values[0].split('.', 1)[1] if ctx.values else ''


def render(result, labelled_breakdown=False, named=True):
    """A checked result as text: a table where several labelled figures share a row, else a line a row; named=False
    leaves out the figure's name where the line beside it already gives it (a breakdown's total)."""
    if not result['rows']:
        return 'No observations matched the checked request.'
    outputs = display_outputs(result)
    def shown_value(column, cell):
        if labelled_breakdown and cell['value']['kind'] in ('decimal', 'integer') and not column.get('format'):
            return (column.get('currency') + ' ' if column.get('currency') else '') + exact(value(cell))
        return display_value(column, cell)
    dimensions = [c for c in result['columns'] if c['kind'] == 'dimension']
    if len(outputs) > 1 and any(c.get('label') for c, _ in outputs):
        headers = [c.get('label') or c['id'] for c in dimensions] + [c.get('label') or c['id'] for c, _ in outputs]
        ranked = any(r.get('end') for r in result['rows'])
        if ranked:
            headers = ['End', 'Rank', *headers]
        def escaped(value):
            return str(value).replace('|', '\\|').replace('\n', ' ')
        lines = [' | '.join(escaped(v) for v in headers), ' | '.join('---' for _ in headers)]
        for row in result['rows']:
            dims = [dimension_value(row, i, result['rows']) for i in range(len(row['dimensions']))]
            if row.get('total') and any(d['kind'] == 'null' for d in row['dimensions']):
                dims[next(i for i, d in enumerate(row['dimensions']) if d['kind'] == 'null')] = 'Total'
            values = []
            for column, index in outputs:
                cell = row['cells'][index]
                shown = '' if row.get('total') and cell['value']['kind'] == 'null' and not cell['null_reasons'] else shown_value(column, cell)
                if cell['null_reasons']:
                    shown += ' (' + '; '.join(cell['null_reasons']) + ')'
                values.append(shown)
            lines.append(' | '.join(escaped(v) for v in ([row.get('end', ''), row.get('rank', '')] if ranked else []) + dims + values))
        return '\n'.join(lines)
    lines = []
    for row in result['rows']:
        dimensions = ', '.join(str(dimension_value(row, i, result['rows'])) for i, d in enumerate(row['dimensions']) if d['kind'] != 'null') + ' Total' if row.get('total') else ', '.join(str(dimension_value(row, i, result['rows'])) for i in range(len(row['dimensions'])))
        dimensions = dimensions.strip()
        if row.get('end'):
            dimensions = f"{row['end']} {row['rank']}: " + dimensions
        cells = []
        for column, index in outputs:
            cell = row['cells'][index]
            if row.get('total') and cell['value']['kind'] == 'null' and not cell['null_reasons']:
                continue
            shown = shown_value(column, cell)
            if cell['null_reasons']:
                shown += ' (' + '; '.join(cell['null_reasons']) + ')'
            cells.append((column.get('label') or column['id']) + ': ' + shown if named or len(outputs) > 1 else shown)
        lines.append((dimensions + ': ' if dimensions else '') + '; '.join(cells))
    return '\n'.join(lines) or 'No observations matched the checked request.'


def cohort_answer(engine, combined, req, principal, purpose, cfg, scope, out, plan, contexts, rows):
    from kge.services.ask.engine.combined import Combined, disclosed, number_citations, period_words
    combined = combined or Combined(engine, llm=None)
    metadata = planrules.snapshot(engine).output_metadata
    figures = []
    for n, (measure, ctx) in enumerate(zip(plan.measures, contexts)):
        item = metadata[ctx.id.removeprefix('cohort:')]
        count = measure.kind == 'distinct_count'
        figures.append({'id': measure.id, 'label': ctx.term, 'term': ctx.term,
            'kind': 'count' if count else ctx.aggregation, 'role': planrules.mapping_role(item),
            'column': item['tbl'] + '.' + item['col'], 'currency': '' if count else item['currency'] or '',
            'certified_by': item['certified_by'], 'certified_on': item['certified_on'],
            'value': value(rows[0]['cells'][n]) if rows and not plan.dimensions else None, 'cite': None})
    out['trace'].append({'step': 'map', 'columns': [{'term': f['term'], 'column': f['column'],
        'certified_by': f['certified_by'], 'certified_on': f['certified_on']} for f in figures]})
    unit = contexts[0].unit
    runtrace.stage('retrieve')
    docs = scope.documents({'question': req.question, **combined.sources(req.question, [c.term for c in contexts], principal, purpose, unit)})
    docs.pop('question')
    hits = docs.pop('vector_hits')
    out['trace'].append({'step': 'retrieve', 'vector_hits': hits, 'cited': [c['doc'] for c in docs['citations']]})
    runtrace.stage('bind')
    for figure in figures:
        figure['cite'] = next((c['doc'] for c in docs['citations'] if figure['term'] in c['terms']), None)
    bound = [tuple([f['cite']]) if f['cite'] else () for f in figures]
    for row in out['result']['rows']:
        for cell, refs in zip(row['cells'], bound):
            cell['citations'] = list(refs)
    number_citations(docs['citations'], figures)
    out.update({"behaviour": "combined"}, combined=True, figures=figures, unit=unit, unit_label=engine.UNIT_LABEL[unit],
               period=period_words(req.period), **docs)
    runtrace.stage('render')
    if plan.dimensions:
        text = f"{out['unit_label']}, {out['period']}: " + render(out['result']) + ('\n' + disclosed(out).strip() if disclosed(out) else '')
        return {**out, 'answer_text': text, 'phrased_by': 'template'}
    text, phrased_by = combined.phrase(out, cfg)
    return {**out, 'answer_text': text, 'phrased_by': phrased_by}


NODE_REFERENCES = ('inputs', 'left', 'right', 'numerator', 'denominator', 'input', 'remove_dimensions', 'remove_filters',
                   'partition_by', 'order_by', 'output')


def receipt_plan(plan, vocab, nodes=()):
    """The plan an answer's receipt keeps, normalised: declared names, operations and typed scalars, with no evidence
    and no obligation, since those point into the question. A column's value stays only where it is one of that
    column's declared members; any other, an entity key or a word from the question, is replaced by its type. The
    plan's node ids (nodes, in plan order), which the model may make from the question's words, are renamed n1, n2 and
    so on, and every reference to them with them, an output's base or other side included."""
    names = {node: f'n{n}' for n, node in enumerate(nodes, 1)}

    def renamed(ref):
        parts = ref.split('/')
        return '/'.join(names.get(part, part) if n < max(len(parts) - 1, 1) else part for n, part in enumerate(parts))

    def members(column):
        return {e.value for e in vocab.entries.values() if e.kind == 'value' and e.column == column}

    def typed(value, declared):
        if isinstance(value, dict) and 'value' in value and value['value'] not in declared:
            return {k: v for k, v in value.items() if k != 'value'}
        return value

    def walk(node):
        if isinstance(node, list):
            return [walk(child) for child in node]
        if not isinstance(node, dict):
            return node
        node = {key: walk(child) for key, child in node.items() if key not in ('evidence', 'obligations')}
        left = node.get('left')
        if node.get('kind') in ('compare', 'in') and isinstance(left, dict) and left.get('kind') == 'column':
            entry = vocab.entries.get((left.get('name') or {}).get('id'))
            declared = members(entry.column) if entry is not None and entry.kind == 'column' else set()
            if 'value' in node:
                node['value'] = typed(node['value'], declared)
            if isinstance(node.get('values'), list):
                node['values'] = [typed(v, declared) for v in node['values']]
        if 'kind' in node and isinstance(node.get('id'), str):
            node['id'] = renamed(node['id'])
        for key in NODE_REFERENCES:
            if isinstance(node.get(key), str):
                node[key] = renamed(node[key])
            elif isinstance(node.get(key), list):
                node[key] = [renamed(ref) if isinstance(ref, str) else ref for ref in node[key]]
        return node
    return walk(plan)


def collapsed_values(plan):
    """A receipt's normalised plan (receipt_plan) with each IN filter's values given as how many of each kind it held,
    in the order the kinds first appear ({"kind": "integer", "count": 256}), its declared members dropped with the rest:
    what a receipt keeps where the whole list would make it too long to post back (service.sealed_within). Everything
    else in the plan is kept as it was."""
    def walk(node):
        if isinstance(node, list):
            return [walk(child) for child in node]
        if not isinstance(node, dict):
            return node
        node = {key: walk(child) for key, child in node.items()}
        if node.get('kind') == 'in' and isinstance(node.get('values'), list):
            counts = {}
            for value in node['values']:
                kind = value.get('kind') if isinstance(value, dict) else type(value).__name__
                counts[kind] = counts.get(kind, 0) + 1
            node['values'] = [{'kind': kind, 'count': count} for kind, count in counts.items()]
        return node
    return walk(plan)


def client_result(out: dict) -> dict:
    """The answer as a client reads it: each compared figure once. A result column that repeats another, and its cells,
    are left out, and no column carries the repeats flag; the engine's own result keeps both, for scoring. The receipt's
    plan is the service's to seal, never shown."""
    if RECEIPT_PLAN in out:
        out = {k: v for k, v in out.items() if k != RECEIPT_PLAN}
    result = out.get("result")
    if not isinstance(result, dict) or not any(c.get("repeats") for c in result.get("columns", [])):
        return out
    outputs = [c for c in result["columns"] if c.get("kind") != "dimension"]
    kept = [n for n, c in enumerate(outputs) if not c.get("repeats")]
    columns = [{k: v for k, v in c.items() if k != "repeats"} for c in result["columns"] if not c.get("repeats")]
    rows = [{**row, "cells": [row["cells"][n] for n in kept]} for row in result.get("rows", [])]
    return {**out, "result": {**result, "columns": columns, "rows": rows}}
