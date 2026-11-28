"""A question's bindings and open items, as ask's rules read it (#707 decisions 12, 13 and 16): facts only.

The reading is the one `ask` makes on its default arm: the same parse, scope, vocabulary, turn and rules reply
(rulesport), checked by the same planner, with no model call and no warehouse query. The bindings say what each span
of the question was bound to, in business names; the open items list each thing `ask` would settle by a default,
clarify, or cannot answer, with its options. Nothing here is agent-specific: no card, no suggested default and no
"ask the person" flag.

The generic rules it applies are kge/semantics/readingrules.py's: pure functions over the pack's own declarations.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import replace

from kge.semantics.readingrules import column_label, grouping_options, meanings

TIME_MODE_OPERATIONS = ("current", "as_reported")
"""The rules' operations that set a time mode rather than name a meaning."""


def month_label(day: datetime.date) -> dict:
    """A covered month as an option: YYYY-MM and its words."""
    return {"value": f"{day.year:04d}-{day.month:02d}", "label": f"{day:%B %Y}"}


class Reading:
    """One question as ask's rules read it, for one person: the request, the vocabulary, the rules reply and what the
    planner made of it, read as the ask service reads it before it runs anything."""

    def __init__(self, engine, question, principal, purpose="analysis", history=(), products=None, runtimes=None):
        from kge.pipeline.trace import graph_recording
        from kge.services.ask.engine import planrules, rulesport
        from kge.services.ask.engine.config import PRESETS
        from kge.services.ask.engine.planpublic import scope_requests
        from kge.services.ask.engine.planner import Planner
        from kge.services.ask.engine.question import definition_request, parse
        from kge.services.ask.engine.scope import ProductScope
        from kge.services.ask.engine.wording import Hidden
        from kge.services.ask.engine import difference
        from kge.semantics.coverage import ledger

        self.engine, self.question, self.principal, self.purpose = engine, question, principal, purpose
        self.history, self.runtimes = tuple(history or ()), runtimes
        cfg = self.cfg = engine.stamped(PRESETS["graph"])
        req = parse(question, engine.term_names if cfg.glossary else [], engine, aliases=cfg.resolution not in ("none", "words"))
        self.req = difference.request(definition_request(req), engine)
        self.scope = ProductScope(engine, products)
        self.selected = scope_requests(engine, self.req, principal, cfg, self.scope)[0]
        vocab = planrules.vocabulary(engine, principal, purpose, self.scope, cfg, self.selected, self.history)
        self.vocab = replace(vocab, default_units=tuple(self.selected.units if self.selected is not self.req else ()))
        self.turn = hashlib.sha256(json.dumps([self.selected.question, cfg.label, self.vocab.binding.model_dump(mode="json"),
                                               [entry.model_dump(mode="json", by_alias=True) for entry in self.history]],
                                              sort_keys=True).encode()).hexdigest()[:32]
        cohort = cfg.graph and bool(planrules.cohort_terms(engine, self.selected))
        replies = []

        def rules(text, vocabulary):
            replies.append(rulesport.planned(rulesport.rules_planner(), rulesport.RulesQuestion(
                engine, self.selected, text, vocabulary, cfg, self.turn, self.history, cohort)))
            return replies[-1]
        planner = Planner(self.vocab, engine.w, Hidden.of(engine, principal),
                          {c.id: ("pack:" + c.term,) for c in self.vocab.contexts.values()})
        with graph_recording(None):
            try:
                self.checked = planner.prepare(self.selected.question, rules, self.turn, self.history)
            except ValueError:
                self.checked = None
        self.reply = replies[0] if replies else None
        self.pieces = sorted((p for p in ledger(self.selected.question, self.turn, self.vocab) if p.span.turn == self.turn),
                             key=lambda p: (p.span.start, p.span.end))

    @property
    def plan(self):
        from kge.semantics.plancheck import CheckedPlan
        return self.checked.plan if isinstance(self.checked, CheckedPlan) else None


def readable_names(engine, readable: set[str]) -> dict[str, list[str]]:
    """Each term the person may read, by name, with the pack's aliases for it: readable where one of its contexts or
    mapped columns is in a unit the person may read. Read from the per-release snapshot (planrules.snapshot) and the
    domain, as scope_requests reads a term's units: no graph read a term."""
    from kge.services.ask.engine.planrules import snapshot
    runtime = snapshot(engine)
    units: dict[str, set[str]] = {}
    for context in runtime.contexts.values():
        units.setdefault(context.term, set()).add(context.unit)
    for mapping in runtime.mapping_declarations:
        units.setdefault(mapping["term"], set()).add(mapping["unit"])
    aliases: dict[str, list[str]] = {}
    for phrase, target in (engine.g.domain().get("aliases") or {}).items():
        if isinstance(target, str):
            aliases.setdefault(target, []).append(phrase)
    return {name: sorted(aliases.get(name, [])) for name in engine.term_names if units.get(name, set()) & readable}


def term_definitions(engine, names: list[str]) -> dict[str, dict]:
    """Each named term's id and glossary definition."""
    if not names:
        return {}
    rows = engine.g.q("MATCH (t:Term) WHERE t.name IN $names RETURN t.name AS name, t.id AS id, t.definition AS definition",
                      {"names": list(names)})
    return {r["name"]: {"id": r["id"] or r["name"], "definition": r["definition"] or ""} for r in rows}


def resolved(engine, phrase: str, principal: str) -> list[dict]:
    """The readable terms a name that is not a term's own resolves to (map_term's resolves_to): each with its id, label
    and definition, and whether the pack's alias binds it by default."""
    g = engine.g
    readable = set(g.readable_units(principal))
    names = readable_names(engine, readable)
    if hasattr(g, "member_terms"):
        members = g.member_terms()
        restricted = {".".join(row) for row in g.domain()["restricted"]} if members.names else set()
        names.update({term: [alias for alias, name in members.aliases if name == term]
                      for term in members.names if any(column.split(".", 1)[0] in readable and column not in restricted
                                                      for column in members.columns(term))})
    found = meanings(phrase, names)
    about = term_definitions(engine, [m["term"] for m in found])
    return [{"id": about.get(m["term"], {}).get("id", m["term"]), "label": m["term"],
             "definition": about.get(m["term"], {}).get("definition", ""), "default_alias": m["default_alias"]} for m in found]


def column_labels(engine, identifiers) -> dict[str, str]:
    """Each column's business label by readingrules.column_label, the one rule search uses too: from the domain's
    business names and the per-release catalogue's descriptions, never a graph read per request."""
    business = engine.g.domain().get("business") or {}
    descriptions = engine.g.column_descriptions()
    return {i: column_label(i, business, descriptions) for i in dict.fromkeys(identifiers) if i}


def plan_terms(reading) -> list[dict]:
    """The plan's measures, each with its context's term, label and time mode."""
    plan = reading.plan
    if plan is None:
        return []
    out = []
    for measure in plan.measures:
        context = reading.vocab.contexts.get(measure.context.id)
        if context is not None:
            out.append({"context": context.id, "term": context.term, "label": context.label, "time_mode": measure.mode})
    return out


DECLINES = {"refuse": "access", "blocked_access": "access", "abstain": "question", "abstain_route_steward": "question",
            "abstain_with_reason": "period", "clarify": "meaning"}
"""ask's declining behaviours, each the open item it is reported as. Any other behaviour answers."""


def report(engine, question, principal, purpose="analysis", history=(), products=None, runtimes=None, combined=None) -> dict:
    """{bindings, open_items}: the question as ask's rules read it for principal, with the same history, products,
    purpose and runtimes, as facts. No model call and no warehouse query; graph-store and retrieval reads as any context
    call makes. A decline (an access refusal, a period ask has not released, a question ask cannot answer, a
    clarification) is ask's own: ask run dry (dryrun.py) on combined, the deployment's own Combined (a Combined with no
    model when None), in its words and with its withholding; a question ask answers, or hands to its model, has none.
    The items the rules' reading finds (a term's meanings, the period, a grouping) are reported whether ask declines,
    answers or hands the question to its model; only an access refusal stands alone."""
    reading = Reading(engine, question, principal, purpose, history, products, runtimes)
    decided = asks_decision(engine, question, principal, purpose, history, products, runtimes, combined)
    bindings, term_items = bind(engine, reading, principal)
    declined = decline_item(engine, reading, decided)
    if declined is not None and declined["item"] == "access":
        return {"bindings": bindings, "open_items": [declined]}
    items = [*term_items, *([declined] if declined is not None else []), *period_items(engine, reading, decided),
             *grouping_items(engine, reading)]
    return {"bindings": bindings, "open_items": items}


def asks_decision(engine, question, principal, purpose, history, products, runtimes, combined=None) -> dict | None:
    """What ask decides on its default arm before its first warehouse query or model request: its whole reply where it
    declines or answers without either (a definition, a refusal), a decline worded as ask words it for this person
    (wording.worded: its reason withheld as ask withholds it); None where it reaches a query; {handed_to_model: True}
    where it reaches its model. A question ask fails on is {failed: True}: ask itself returns a 500 for it."""
    from kge.pipeline.trace import graph_recording
    from kge.services.ask.engine.combined import Combined
    from kge.services.ask.engine.dryrun import WouldAskModel, WouldRun, dry_run
    from kge.services.ask.engine.wording import worded
    combined = combined if combined is not None else Combined(engine, llm=None)
    with dry_run(lambda runtime: engine.g.release_calendar_current(runtime, engine.w)), graph_recording(None):
        try:
            decided = combined.governed(question, principal, purpose, None, products, history, runtimes=runtimes)
        except WouldAskModel:
            return {"handed_to_model": True}
        except WouldRun:
            return None
        except ValueError:
            return {"failed": True}
        return worded(decided, engine) if decided.get("behaviour") in DECLINES else decided


def decline_item(engine, reading, decided) -> dict | None:
    """ask's decline as an open item, with ask's reason as ask gives it; None where ask answers or reaches a query."""
    if decided is None:
        return None
    if decided.get("failed"):
        return {"item": "question", "resolution": "unanswerable", "reason": "The question cannot be read.", "options": []}
    item = DECLINES.get(decided.get("behaviour"))
    if item is None:
        return None
    if item == "meaning":
        return {"item": "meaning", "resolution": "clarify", "options": clarify_options(engine, reading, decided)}
    out = {"item": item, "resolution": "unanswerable", "reason": decided.get("reason") or "", "options": []}
    if item == "period":
        covered = covered_months(engine, reading, plan_context(reading))
        if covered:
            out.update(grain="month", latest=month_label(covered[0]), options=[month_label(m) for m in covered])
    return out


def clarify_options(engine, reading, decided) -> list[dict]:
    """The declared alternatives ask's clarification offers: the rules' own where they clarify, else ask's choices."""
    decision = reading.checked if isinstance(reading.checked, dict) else {}
    choices = [c.get("id") if isinstance(c, dict) else c for c in decision.get("choices") or decided.get("choices") or ()]
    entries = [reading.vocab.entries[c] for c in choices if c in reading.vocab.entries]
    labels = column_labels(engine, [e.column for e in entries if e.column])
    return [{"label": e.label, "kind": e.kind, **({"of": labels[e.column]} if e.column in labels and e.column != e.id else {})}
            for e in entries]


def plan_context(reading):
    """The context of the plan's first measure, else of the first term the question binds, or None."""
    plan = reading.plan
    if plan is not None and plan.measures:
        return reading.vocab.contexts.get(plan.measures[0].context.id)
    return next((reading.vocab.contexts[c] for p in reading.pieces for c in p.candidates if c in reading.vocab.contexts), None)


def bind(engine, reading, principal) -> tuple[list[dict], list[dict]]:
    """Each piece of this turn's question and what the rules bound it to, and the term items: a term bound by an alias
    that resolves to more than one readable term, with no other word of the question qualifying it."""
    from kge.services.ask.engine.planledger import unread
    from kge.services.ask.engine.question import period_words

    vocab, question = reading.vocab, reading.selected.question
    unread_spans = {tuple(s) for s in unread(reading.reply, reading.turn)}
    measures = plan_terms(reading)
    planned = {m["context"]: m for m in measures}
    names = readable_names(engine, set(engine.g.readable_units(principal)))
    columns = [c for p in reading.pieces if p.kind in ("name", "predicate") for c in p.candidates
               if c in vocab.entries and vocab.entries[c].kind == "column"]
    labels = column_labels(engine, columns)
    bindings, term_items, bound_contexts = [], [], set()
    for piece in reading.pieces:
        span = (piece.span.start, piece.span.end)
        out = {"text": question[span[0]:span[1]], "start": span[0], "end": span[1]}
        contexts = [c for c in piece.candidates if c in vocab.contexts]
        if piece.kind == "unknown" or (span in unread_spans and not contexts):
            bindings.append({**out, "kind": "unread"})
            continue
        if piece.kind == "name" and piece.operation == "dimension":
            bindings.append({**out, "kind": "grouping", "label": ", ".join(labels.get(c, vocab.entries[c].label)
                                                                             for c in piece.candidates if c in vocab.entries)})
            continue
        if piece.kind == "name" and contexts:
            chosen = [c for c in contexts if c in planned] or contexts
            terms = list(dict.fromkeys(vocab.contexts[c].term for c in chosen))
            if len(terms) != 1:
                bindings.append({**out, "kind": "term", "term": None, "by": "unsettled",
                                 "options": [{"label": t} for t in terms]})
                continue
            context = vocab.contexts[chosen[0]]
            bound_contexts.add(context.id)
            qualified = any(other is not piece and context.id in other.candidates for other in reading.pieces)
            found = meanings(out["text"], names)
            by = "default_alias" if len(found) > 1 and not qualified and any(
                m["default_alias"] and m["term"] == context.term for m in found) and not any(
                m["named"] and not m["default_alias"] and m["term"] == context.term for m in found) else "explicit"
            bindings.append({**out, "kind": "term", "term": context.term, "context": context.label, "by": by,
                             **({"time_mode": planned[context.id]["time_mode"]} if context.id in planned else {})})
            if by == "default_alias":
                about = term_definitions(engine, [m["term"] for m in found])
                term_items.append({"item": "term", "resolution": "default", "text": out["text"], "start": span[0], "end": span[1],
                                   "options": [{"label": m["term"], "id": about.get(m["term"], {}).get("id", m["term"]),
                                                "definition": about.get(m["term"], {}).get("definition", ""),
                                                "default_alias": m["default_alias"]} for m in found]})
            continue
        if piece.kind == "name":
            entries = [vocab.entries[c] for c in piece.candidates if c in vocab.entries]
            bindings.append({**out, "kind": "name", "label": ", ".join(labels.get(e.id, e.label) for e in entries)})
            continue
        if piece.kind == "predicate":
            column = next((c for c in piece.candidates if c in vocab.entries), None)
            bindings.append({**out, "kind": "filter", "label": labels.get(column, column or ""),
                             **({"value": str(piece.value)} if piece.value is not None else {}),
                             **({"operation": piece.operation} if piece.operation else {})})
            continue
        if piece.kind == "time":
            value = piece.value
            label = period_words(value.kind, value.value) if value is not None and hasattr(value, "kind") else out["text"]
            bindings.append({**out, "kind": "period", "label": label})
            continue
        if piece.kind == "operator" and piece.operation == "comparison":
            bindings.append({**out, "kind": "comparison"})
            continue
        if piece.kind == "operator" and piece.operation in TIME_MODE_OPERATIONS:
            bindings.append({**out, "kind": "time_mode", "value": piece.operation})
            continue
        if piece.kind == "literal":
            bindings.append({**out, "kind": "number", **({"value": str(piece.value)} if piece.value is not None else {})})
            continue
        bindings.append({**out, "kind": "operator", **({"operation": piece.operation} if piece.operation else {})})
    unbound = [m for m in measures if m["context"] not in bound_contexts]
    for measure in unbound:
        carrier = next((b for b in bindings if b["kind"] == "time_mode" and b["value"] == measure["time_mode"]
                        and "term" not in b), None) or next((b for b in bindings if b["kind"] == "comparison"
                                                             and "term" not in b), None)
        if carrier is not None:
            carrier.update(term=measure["term"], context=measure["label"], by="explicit")
        else:
            bindings.append({"text": "", "start": None, "end": None, "kind": "term", "term": measure["term"],
                             "context": measure["label"], "by": "implied", "time_mode": measure["time_mode"]})
    return bindings, term_items


def covered_months(engine, reading, context) -> list[datetime.date]:
    """The months the context's release calendar has released, newest first, from the calendar the engine pinned as it
    opened: no warehouse query. None where the runtime's calendar was not pinned or is not current."""
    from kge.semantics.compile import ReleaseNeed, policy_for
    if context is None:
        return []
    policy = policy_for(reading.vocab, context)
    if policy is None:
        return []
    runtime = (reading.runtimes or engine.w.default_runtime_list() or [None])[0]
    if runtime is None or not engine.g.release_calendar_current(runtime, engine.w):
        return []
    need = ReleaseNeed("0", policy, context.unit, None, True)
    rows = engine.g.release_periods((need,), runtime)
    return sorted({period for _, period, ready, _ in rows if ready and period is not None}, reverse=True)


def period_items(engine, reading, decided) -> list[dict]:
    """The period item the rules' reading finds, with the covered months newest first: settled by a default where the
    question names no period and ask reads every released month; cannot be answered, with the latest covered month,
    where a period it names runs past the latest covered month and ask does not reach its own release check (it
    declines on another ground, fails, or hands the question to its model). None where ask answers without a query
    (a definition), or declines on the period itself (decline_item reports that). Every time piece names a period, one
    with no value read from its words; one that cannot be placed gives no item, never a default."""
    reached = decided is None
    if not reached and not decided.get("failed") and not decided.get("handed_to_model"):
        if DECLINES.get(decided.get("behaviour")) in (None, "period"):
            return []
    named = [p for p in reading.pieces if p.kind == "time"]
    if reached and (named or reading.plan is None):
        return []
    context = plan_context(reading)
    if named:
        windows = [window for window in (period_window(engine, placed(p)) for p in named) if window]
        covered = covered_months(engine, reading, context) if windows else []
        if not covered or max(end for _, end in windows) <= next_month(covered[0]):
            return []
        return [{"item": "period", "resolution": "unanswerable", "grain": "month", "latest": month_label(covered[0]),
                 "options": [month_label(m) for m in covered]}]
    if any(p.operation in PERIOD_OPERATIONS for p in reading.pieces) or not reads_every_month(engine, reading, context):
        return []
    covered = covered_months(engine, reading, context)
    return [{"item": "period", "resolution": "default", "grain": "month", "options": [month_label(m) for m in covered]}] if covered else []


PERIOD_OPERATIONS = ("latest_released", "ytd", "mtd", "qtd", "previous_period")
"""The rules' operations that say a period without a period word."""


def reads_every_month(engine, reading, context) -> bool:
    """Whether ask reads a question that names no period over every released month: as its plan says where the rules
    plan it, else as the rules default one (planrules.time_of), unless the pack names a default period for a
    partitioned fact."""
    plan = reading.plan
    if plan is not None:
        return bool(plan.measures) and getattr(plan.time, "kind", None) == "all_available"
    if context is None:
        return False
    partitioned = getattr(reading.vocab.semantics, "partitioned", None) or {}
    return not (context.fact in partitioned and (getattr(engine, "NAMED_PERIODS", None) or {}).get("default"))


def next_month(day: datetime.date) -> datetime.date:
    """The first day of the month after day's."""
    return datetime.date(day.year + day.month // 12, day.month % 12 + 1, 1)


def placed(piece):
    """A time piece's period: its own value, else the period its words read as (coverage.to_date_reason's reading of
    a time piece the ledger made with no value), or None."""
    from kge.services.ask.engine.question import read_period
    if getattr(piece.value, "plan_kind", None):
        return piece.value
    return read_period(piece.text)[0]


def period_window(engine, value) -> tuple[datetime.date, datetime.date] | None:
    """A named period's first day and the day after its last, as the context path reads it (agent.context_window: a
    year label from the month the pack's labelled year starts in), or None where it cannot be placed."""
    from types import SimpleNamespace

    from kge.services.ask.engine.agent import context_window
    if getattr(value, "plan_kind", None) is None:
        return None
    try:
        window = context_window(SimpleNamespace(period=value, question=""), engine)
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if window is None:
        return None
    return datetime.date.fromisoformat(window[0]), datetime.date.fromisoformat(window[1]) + datetime.timedelta(days=1)


def grouping_items(engine, reading) -> list[dict]:
    """The grouping item: unread words after a grouping operator ("by zone") that name declared grouping dimensions
    (decision 20): the one dimension a declared phrase they spell with other separators names ("by sub zone" for a
    declared "by sub-zone"), else every dimension they name, even one; each by its label and phrases. Its resolution
    is clarify: the person picks one. ask's own reading is unchanged: the item only states what the words could name."""
    from kge.services.ask.engine.planledger import unread
    vocab, question = reading.vocab, reading.selected.question
    unread_spans = {tuple(s) for s in unread(reading.reply, reading.turn)}
    bindings = {}
    for piece in reading.pieces:
        for c in piece.candidates:
            if c in vocab.contexts:
                bindings.update(vocab.contexts[c].bindings or {})
    phrases = {phrase: bindings.get(column, column)
               for phrase, column in (getattr(vocab.semantics.d, "GROUP_BY", None) or {}).items()}
    phrases = {phrase: column for phrase, column in phrases.items() if column in vocab.entries}
    items = []
    pieces = reading.pieces
    for index, piece in enumerate(pieces):
        if not (piece.kind == "operator" and piece.operation == "dimension"):
            continue
        words = []
        for following in pieces[index + 1:]:
            if following.kind == "operator" or (following.kind != "unknown"
                                                and (following.span.start, following.span.end) not in unread_spans):
                break
            words.append(following)
        if not words:
            continue
        start, end = words[0].span.start, words[-1].span.end
        said = question[start:end]
        labels = column_labels(engine, set(phrases.values()))
        business = engine.g.domain().get("business") or {}
        names = {dimension: business[dimension] for dimension in set(phrases.values()) if business.get(dimension)}
        operator = question[piece.span.start:piece.span.end]
        options = grouping_options(said, phrases, labels, operator, names)
        if options:
            items.append({"item": "grouping", "resolution": "clarify", "text": said, "start": start, "end": end,
                          "options": [{"label": o["label"], "phrases": o["phrases"]} for o in options]})
    return items
