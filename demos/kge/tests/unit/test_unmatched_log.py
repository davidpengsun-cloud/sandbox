"""The page's no-term log (kge/services/ask/service.py, log_unmatched, to the run state's unmatched.jsonl): a graph-on
no-term abstention on /answer, /ask or the /agent treatment is logged; graph-off arms, answered questions, refusals for
another reason, the harness's direct Arms.ask calls (kge/eval/harness.py, kge/eval/margin.py's EvidenceArms), questions
holding personal data and held-out questions asked on the page never are. The routes run on the real presets and agent
arms, with stub engines, a stub run state and a stub estate: no graph, no disk, no HTTP."""
from __future__ import annotations

from fnmatch import fnmatch
from functools import partial
from pathlib import PurePosixPath
from types import SimpleNamespace

import anyio
import pytest

from kge.eval import harness
from kge.eval.stages import EvidenceArms
from kge.services.ask import routes, service
from kge.services.ask.engine import core
from kge.services.ask.engine.config import PRESETS
from kge.services.ask.models import AgentAsk, Ask
from kge.services.ask.receipt import Receipts

QUESTION = "Lease income for June 2025"
WHO = "analyst_enterprise.meridian@example.com"
NO_TERM = {"behaviour": "abstain", "reason": "No glossary term recognised in the question."}
RUN_DIR = PurePosixPath("/run-state")
HELDOUT = '{"name": "heldout_leasing_v1", "items": [{"id": "LEASING-H02", "question": "Total lease income in June 2025"}]}\n'
GRAPH_ON = (("answer", "graph", "/answer"), ("ask", "graph", "/ask"), ("agent", "treatment", "/agent"))


class Missing:
    """A folder or file that is not there."""

    def __truediv__(self, name):
        return self

    def is_dir(self):
        return False

    def is_file(self):
        return False


class File:
    def __init__(self, text):
        self.text = text

    def read_text(self):
        return self.text


class Estate(Missing):
    """The estate folder, holding files by name."""

    def __init__(self, files):
        self.files = files
        self.parent = Missing()

    def is_dir(self):
        return True

    def glob(self, pattern):
        return [File(text) for name, text in sorted(self.files.items()) if fnmatch(name, pattern)]


@pytest.fixture
def page(monkeypatch):
    """The three routes served on stubs. page.out is the answer every stub engine gives; page.estate the estate's files;
    page.rows the (path, row) pairs appended to the run state."""
    state = SimpleNamespace(out=dict(NO_TERM), estate={}, rows=[], parsed=SimpleNamespace(term=None, definitional=False, special=None))

    def answered(*args, **kwargs):
        return dict(state.out)

    eng = SimpleNamespace(answer=answered, g=None, term_names=(), identity={"release": "r1", "hash": "f" * 64})
    comb = SimpleNamespace(ask=answered, g=None, e=eng, llm=None, default_config=lambda arm: PRESETS[arm])
    ag = SimpleNamespace(ask=answered, g=None, e=eng)
    profile = SimpleNamespace(run_dir=lambda: RUN_DIR, estate=lambda: Estate(state.estate), clients=Missing(), client="meridian")
    monkeypatch.setattr(routes, "served", SimpleNamespace(engine=lambda: eng, combined=lambda: comb, agent=lambda: ag, PROFILE=profile))
    monkeypatch.setattr(routes, "engineer_view", lambda who: True)
    monkeypatch.setattr(routes, "person_tools", lambda request, loop: None)
    monkeypatch.setattr(service.runtrace, "run", lambda kind, fn, graph=None, on_stage=None: fn())
    monkeypatch.setattr(service.runtrace, "append_jsonl", lambda path, row: state.rows.append((path, row)))
    monkeypatch.setattr(service, "ROOT", Missing())
    monkeypatch.setattr(service, "receipts", lambda engine, profile: Receipts(b"k" * 32, engine.identity["release"]))
    monkeypatch.setattr(service, "worded", lambda out, e: out)
    monkeypatch.setattr(service, "client_result", lambda out: out)
    monkeypatch.setattr(service, "build_path", lambda e, out: None)
    monkeypatch.setattr(service, "build_graph_path", lambda e, out: None)
    monkeypatch.setattr(core, "parse", lambda question, names, e: state.parsed)
    return state


def post(route: str, question: str, arm: str) -> dict:
    """One page request, as the route function serves it, signed in as WHO."""
    request = SimpleNamespace(headers={})
    if route == "answer":
        out = routes.answer(Ask(question=question, arm=arm), request, who=WHO, display=False)
    elif route == "ask":
        out = routes.ask(Ask(question=question, arm=arm), request, who=WHO, display=False, background=False)
    else:
        out = anyio.run(partial(routes.ask_agent, AgentAsk(question=question, arm=arm), request, who=WHO))
    assert isinstance(out, dict), out
    return out


def test_only_the_graph_on_arms_log_a_no_term_abstention(page):
    for route, arm in (("answer", "graph"), ("ask", "graph"), ("agent", "treatment"),
                       ("answer", "schema"), ("answer", "catalog"), ("agent", "control")):
        assert post(route, QUESTION, arm)["behaviour"] == "abstain"
    assert [path for path, _ in page.rows] == [RUN_DIR / "unmatched.jsonl"] * 3
    rows = [row for _, row in page.rows]
    assert [r["path"] for r in rows] == ["/answer", "/ask", "/agent"]
    assert [r["arm"] for r in rows] == ["graph", "graph", "treatment"]
    assert all(r["question"] == QUESTION for r in rows)
    assert all(r["principal"] == WHO for r in rows)


def test_the_presets_turn_the_graph_on_for_the_graph_arm_alone():
    assert {arm: cfg.graph for arm, cfg in PRESETS.items()} == {"schema": False, "catalog": False, "graph": True}


def test_an_answered_question_is_never_logged(page):
    page.out = {"behaviour": "figure", "figure": 1}
    for route, arm, _ in GRAPH_ON:
        assert post(route, "What was retail revenue in June 2025?", arm)["behaviour"] == "figure"
    assert page.rows == []


@pytest.mark.parametrize("out, term", [
    ({"behaviour": "refuse", "reason": "Totals across units need a certified join."}, None),
    ({"behaviour": "abstain", "reason": "Totals across units need a certified join."}, None),
    ({"behaviour": "abstain", "reason": "The context names no column for this question."}, "Revenue"),
], ids=["refused", "other-reason", "unresolved-with-a-term"])
def test_a_refusal_for_another_reason_is_never_logged(page, out, term):
    page.out = out
    page.parsed = SimpleNamespace(term=term, definitional=False, special=None)
    for route, arm, _ in GRAPH_ON:
        assert post(route, "Total revenue across retail and corporate in June 2025", arm)["behaviour"] != "figure"
    assert page.rows == []


def test_an_unresolved_abstention_with_no_term_is_logged(page):
    page.out = {"behaviour": "abstain", "reason": "The context names no column for this question."}
    post("answer", QUESTION, "graph")
    assert [row["path"] for _, row in page.rows] == ["/answer"]


def test_a_question_naming_personal_data_is_never_logged(page):
    for route, arm, _ in GRAPH_ON:
        assert post(route, "Lease income for alice.smith@example.com in June 2025", arm)["behaviour"] == "abstain"
    assert page.rows == []


def test_a_held_out_question_is_never_logged_in_either_wording(page):
    page.estate = {"heldout_leasing_v1.json": HELDOUT}
    for question in ("Total lease income in June 2025", "What was the total lease income in June 2025?"):
        for route, arm, _ in GRAPH_ON:
            assert post(route, question, arm)["behaviour"] == "abstain"
    assert page.rows == []
    post("answer", QUESTION, "graph")
    assert [row["question"] for _, row in page.rows] == [QUESTION]


@pytest.mark.parametrize("kind", [harness.Arms, EvidenceArms])
def test_the_harness_asks_the_engine_directly_and_logs_nothing(page, monkeypatch, kind):
    monkeypatch.setattr(service, "log_unmatched", lambda *args: pytest.fail("the harness reached the page's log"))
    arms = object.__new__(kind)
    arms.e = SimpleNamespace(answer=lambda *args, **kwargs: dict(NO_TERM), g=None)
    arms.agent = SimpleNamespace(ask=lambda *args, **kwargs: dict(NO_TERM))
    for arm in ("treatment", "graph"):
        assert arms.ask(arm, QUESTION, "analyst_enterprise")["behaviour"] == "abstain"
    assert page.rows == []
