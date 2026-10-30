"""Tag proposals for a chunk, in the repository's tag families.

Business meaning, classification and privacy are *proposed* here. AI readiness
is *derived* from them and never authored, and it fails closed: a chunk with no
classification is not ready.

The rule-based tagger is the default and is enough for the walkthrough. A model
tagger can be switched on with MODEL_PROVIDER; it proposes the same families
through the same interface, so everything downstream is indifferent to which
one produced a tag. Either way every tag records its generator, confidence,
model and prompt version, because a tag without provenance cannot be superseded
honestly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from kge.adapters.model import LLM, no_fallback, transient
from kge.pipeline.privacy import CONFIDENCE, DETECTORS, FAMILY, redact

PROMPT_VERSION = "tagger-v1"
RULE_MODEL = "rules-v1"
TAGGER_RETRIES = 2
TRAILING_COMMA = re.compile(r",\s*([}\]])")
DEPENDENT_EVIDENCE = "personal data present"
UNLANDED_EVIDENCE = "landed with no findings: certification did not say what it found"
CLASSIFICATION_ORDER = ["Public", "Internal", "Confidential", "Restricted"]

CANDIDATE_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+){1,3}):\s")


@dataclass
class Proposal:
    """One proposed tag: its family is business_meaning, classification, privacy or candidate_term."""
    family: str
    value: str
    confidence: float
    evidence: str
    generator: str = "rule"
    model: str = RULE_MODEL
    prompt_version: str = PROMPT_VERSION


@dataclass
class ChunkTags:
    proposals: list[Proposal] = field(default_factory=list)
    readiness: str = "embed-prohibited"
    purposes: list[str] = field(default_factory=list)


class RuleTagger:
    """Deterministic, explainable, and wrong in the ways rules are wrong."""

    def __init__(self, glossary_terms: list[str], role_mailboxes=()):
        """role_mailboxes: the client's functional mailboxes, its graph's: an address the detectors find here at one
        is kept in clear in the tag's evidence. None by default: every address is a person's, and redacted."""
        self.role_mailboxes = frozenset(role_mailboxes)
        self.terms = sorted(glossary_terms, key=len, reverse=True)
        self.term_res = [(t, re.compile(r"\b" + re.escape(t) + r"s?\b", re.I)) for t in self.terms]
        self.term_lower = {t.lower() for t in glossary_terms}

    def propose(self, text: str, shelf: str, kind: str, findings: list[dict] | None = None) -> list[Proposal]:
        """findings: what certification found on the chunk's raw text and landed with it (kge/pipeline/privacy.py), taken
        as privacy proposals beside the detectors' own on this text. A glossary term matched exactly is a confident
        proposal, still not a mapping. A capitalised phrase and a colon, not in the glossary, is a candidate term: how
        the recovery scenario finds definitions living only on the uncertified shelf. Personal data in a certified
        procedure is still personal data, since certification does not launder it, and privacy evidence is kept
        redacted with the client's mailboxes, as the queue shows it. The Restricted proposed for personal data rests
        on the privacy tags (DEPENDENT_EVIDENCE), so retiring every privacy tag retires it too."""
        out: list[Proposal] = []
        for term, rx in self.term_res:
            m = rx.search(text)
            if m:
                out.append(Proposal("business_meaning", term, 0.85, m.group(0)))
        body_after_title = text.split("\n", 1)[1] if "\n" in text else ""
        for m in CANDIDATE_RE.finditer(body_after_title if shelf == "uncertified" else ""):
            phrase = m.group(1)
            if phrase.lower() not in self.term_lower and phrase.split()[0] not in {"From", "To", "Note", "Editor", "Reviewer", "Attendees"}:
                out.append(Proposal("candidate_term", phrase, 0.55, m.group(0).strip()))
        privacy: dict[str, Proposal] = {}
        for f in findings or []:
            if f.get("family") == FAMILY and f.get("value"):
                privacy.setdefault(f["value"], Proposal(FAMILY, f["value"], float(f.get("confidence", CONFIDENCE)), f.get("evidence") or ""))
        for rx, value in DETECTORS:
            m = rx.search(text)
            if m:
                privacy.setdefault(value, Proposal(FAMILY, value, CONFIDENCE, redact(value, m.group(0), self.role_mailboxes)))
        order = [value for _, value in DETECTORS]
        out += sorted(privacy.values(), key=lambda p: order.index(p.value) if p.value in order else len(order))
        if shelf == "certified":
            out.append(Proposal("classification", "Internal", 0.7, "published document"))
        else:
            out.append(Proposal("classification", "Internal", 0.5, f"uncertified {kind}"))
        if any(p.family == "privacy" for p in out):
            out.append(Proposal("classification", "Restricted", 0.8, DEPENDENT_EVIDENCE))
        return out


def reply_json(raw: str) -> dict:
    """The JSON object in a model's reply, read strictly; only a reply that does not parse so has its trailing commas
    (",]", ",}") taken out and is read again, so text inside a valid reply's strings is never touched. ValueError if
    neither reads."""
    snippet = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        return json.loads(snippet)
    except ValueError:
        return json.loads(TRAILING_COMMA.sub(r"\1", snippet))


class ModelTagger(RuleTagger):
    """Optional. Same families, same provenance fields, a model's judgement on
    business meaning and candidate terms; rules still decide privacy, because a
    regex that says 'card number' is evidence and a model that says so is a claim."""

    def __init__(self, glossary_terms: list[str], provider: str, model: str, api_key: str, base_url: str | None = None,
                 role_mailboxes=()):
        super().__init__(glossary_terms, role_mailboxes)
        self.model = model
        self.llm = LLM(provider, model, api_key, base_url)
        self.withheld = 0

    @classmethod
    def configured(cls, glossary_terms: list[str], llm: LLM, role_mailboxes=()):
        """A tagger around the centrally selected client, without rebuilding it from partial settings."""
        self = cls.__new__(cls)
        RuleTagger.__init__(self, glossary_terms, role_mailboxes)
        self.model, self.llm, self.withheld = llm.model, llm, 0
        return self

    def _complete(self, prompt: str) -> str:
        return self.llm.complete(prompt, max_tokens=1600)

    def propose(self, text: str, shelf: str, kind: str, findings: list[dict] | None = None) -> list[Proposal]:
        """The rules' proposals, and the model's on top. The privacy gate runs before anything leaves the process: a
        chunk the gate keeps from the encoder is tagged by the rules alone and never sent. A failed call is named by its
        type, since a transport's text can quote the key, and the rules' proposals stand, privacy included; on the
        pilot's cloud path (gcp_native) a failed call or a reply that is not JSON stops the run instead (NoFallback). A
        call that failed for a while (transient: no answer, a timeout, 429, a 5xx) or a reply that is not JSON is asked
        again, TAGGER_RETRIES times at most; any other failure (a refusal, a setting) ends the chunk's turn at once."""
        rules = super().propose(text, shelf, kind, findings)
        if derive_readiness(rules, shelf)[0] == "embed-prohibited":
            self.withheld += 1
            return rules
        out = [p for p in rules if p.family in ("privacy", "classification")]
        prompt = (
            "You tag a text chunk for a bank's governance graph. Glossary terms: "
            + ", ".join(self.terms)
            + ".\nReturn JSON only: {\"mentions\": [{\"term\": str, \"confidence\": 0-1, \"evidence\": str}], "
              "\"candidates\": [{\"phrase\": str, \"definition\": str, \"confidence\": 0-1}]}. "
              "A mention is a glossary term the chunk is about. A candidate is a defined metric not in the glossary.\n\nChunk:\n"
            + text
        )
        data = {}
        for attempt in range(TAGGER_RETRIES + 1):
            last = attempt == TAGGER_RETRIES
            asked = prompt if attempt == 0 else (
                f"{prompt}\n\n(Attempt {attempt + 1}: return strict RFC 8259 JSON only, with double-quoted keys and strings and no trailing commas.)"
            )
            try:
                raw = self._complete(asked)
            except Exception as exc:  # noqa: BLE001
                if transient(exc) and not last:
                    continue
                no_fallback("the tagger", exc)
                out.append(Proposal("business_meaning", "__model_error__", 0.0, f"model call failed ({type(exc).__name__})", "auto", self.model))
                return out
            try:
                data = reply_json(raw)
                break
            except ValueError as exc:
                if not last:
                    continue
                no_fallback("the tagger", exc)
                out.append(Proposal("business_meaning", "__model_error__", 0.0, f"model reply was not JSON: {str(exc)[:100]}", "auto", self.model))
                return out
        for m in data.get("mentions", []):
            if m.get("term") in self.terms:
                out.append(Proposal("business_meaning", m["term"], float(m.get("confidence", 0.5)), m.get("evidence", "")[:120], "auto", self.model))
        for c in data.get("candidates", []):
            out.append(Proposal("candidate_term", c.get("phrase", "")[:60], float(c.get("confidence", 0.5)), c.get("definition", "")[:160], "auto", self.model))
        return out


def unlanded(proposals: list[Proposal]) -> list[Proposal]:
    """A chunk that landed without its findings: nothing says what certification found on its raw text, so it fails
    closed, Restricted on a ground no privacy decision retires."""
    kept = [p for p in proposals if not (p.family == "classification" and p.value == "Restricted")]
    return kept + [Proposal("classification", "Restricted", 1.0, UNLANDED_EVIDENCE)]


EMBEDDABLE = ("citable", "retrieval-internal")
"""The readiness values that let a chunk's text go to an embedder: an allow-list, so a chunk with no readiness yet (one a
pipeline stopped before deriving it) or any other value is never sent."""


def derive_readiness(proposals: list[Proposal], shelf: str) -> tuple[str, list[str]]:
    """AI readiness is a function of classification and privacy. Never authored.

    embed-prohibited  personal data, payment card or special category present, or no classification at all
    citable           certified shelf, Internal or Public, no privacy tag
    retrieval-internal  everything else that may be embedded

    The most restrictive live classification governs, and a machine Restricted that rests only on personal data falls
    with the last privacy tag. An embed-prohibited chunk may still reach supervision by a path that does not embed it;
    the assistant never reaches it: that is the purpose-bound part of the gate.
    """
    privacy = [p for p in proposals if p.family == "privacy"]
    classes = [p for p in proposals if p.family == "classification"]
    if not privacy:
        classes = [p for p in classes if not (p.value == "Restricted" and p.evidence == DEPENDENT_EVIDENCE and p.generator != "human")]
    ranked = [p.value for p in classes if p.value in CLASSIFICATION_ORDER]
    classification = max(ranked, key=CLASSIFICATION_ORDER.index) if ranked else (classes[0].value if classes else None)
    if privacy or classification in (None, "Restricted"):
        return "embed-prohibited", ["supervision"]
    if shelf == "certified" and classification in ("Internal", "Public"):
        return "citable", ["analysis", "assistant", "supervision"]
    return "retrieval-internal", ["analysis", "assistant", "supervision"]


def live_proposals(tags: list[dict]) -> list[Proposal]:
    """The tags that still count on a chunk, as proposals readiness can be derived from.

    A tag superseded by another is retired. A human tag whose value starts with
    'rejected:' is a retirement, not a proposal. A human acceptance counts as
    the value it accepts. Each dict needs family, value, generator, evidence
    and superseded (bool).
    """
    out = []
    for t in tags:
        if t.get("superseded") or t["family"] == "ai_readiness":
            continue
        if t["generator"] == "human" and str(t["value"]).startswith("rejected:"):
            continue
        out.append(Proposal(t["family"], t["value"], float(t.get("confidence") or 0), t.get("evidence") or "", t["generator"]))
    return out


def make_tagger(glossary_terms: list[str], role_mailboxes=()) -> RuleTagger:
    """The tagger the settings name: the model the model adapter gives the tagger (tagger_model), which decides which
    one and refuses where the settings name a model it has not; the rule tagger where it gives none."""
    from kge.adapters.model import tagger_model
    llm = tagger_model()
    if llm is not None:
        return ModelTagger.configured(glossary_terms, llm, role_mailboxes)
    return RuleTagger(glossary_terms, role_mailboxes)
