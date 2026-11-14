"""The shipped sample agent keeps HTTP people credentials separate from gate roles."""
import json
from types import SimpleNamespace

import pytest

from kge.services.ask.engine.agent import Agent
from kge.services.identity import Identities

PERSON = "reader.sample@example.com"


def agent(tmp_path, remote):
    people = {PERSON}
    graph = SimpleNamespace(principal_ids=lambda: people | {"reader"}, principal_ids_people=lambda: people,
                            rights=lambda: SimpleNamespace(roles={"reader": "Reader"}))
    held = Agent.__new__(Agent)
    held.e = SimpleNamespace(profile=SimpleNamespace(run_dir=lambda: tmp_path))
    held.g, held._ids, held.context_url = graph, None, "http://context" if remote else None
    return held, people


def test_http_agent_never_mints_or_resolves_a_role_credential(tmp_path):
    held, people = agent(tmp_path, True)
    assert held.token("reader") is None
    token = held.token(PERSON)
    service = Identities(tmp_path, lambda: people)
    assert service.from_token(token) == PERSON
    assert set(json.loads((tmp_path / "session-state.json").read_text())["tokens"]) == {PERSON}
    people.clear()
    assert held.token(PERSON) is None and service.from_token(token) is None


def test_in_process_agent_prepares_people_tokens_for_a_shared_http_identity_store(tmp_path):
    held, people = agent(tmp_path, False)
    held.identities
    saved = json.loads((tmp_path / "session-state.json").read_text())["tokens"]
    assert PERSON in saved
    service = Identities(tmp_path, lambda: people)
    assert service.from_token(held.token(PERSON)) == PERSON
    assert held.token("reader") and service.from_token(held.token("reader")) is None


@pytest.mark.parametrize("roles", [{"reader"}, {"reader", "writer"}, set()])
def test_answer_scope_is_presentation_only_and_preserves_authored_content(tmp_path, roles):
    held, _ = agent(tmp_path, True)
    checked = []
    def acting(who):
        checked.append(who)
        return roles
    held.g.acting_roles = acting
    text = f"{PERSON} may not read restricted"
    answer = {"principal": PERSON, "question": text, "answer_text": text, "agent": {"sql_source": "planner"},
              "trace": [{"step": "access", "principal": PERSON}],
              "reason": text, "not_readable": [{"unit": "restricted", "why": f"a restricted document, and {text}"}],
              "sql": {"unit": text}, "evidence": {"principal": PERSON, "reason": text, "why": text}}
    result = held.answer_scope(answer, PERSON)
    scope = "reader" if roles == {"reader"} else PERSON
    assert result["principal"] == result["trace"][0]["principal"] == scope
    assert result["reason"] == f"{scope} may not read restricted"
    assert result["not_readable"][0]["why"] == f"a restricted document, and {scope} may not read restricted"
    assert result["evidence"] == answer["evidence"]
    assert result["question"] == result["answer_text"] == result["sql"]["unit"] == text
    assert answer["principal"] == PERSON
    assert checked == [PERSON]


def test_only_template_denial_wording_follows_the_answer_scope(tmp_path):
    held, _ = agent(tmp_path, True)
    held.g.acting_roles = lambda who: {"reader"}
    denial = f"Cross-warehouse query blocked: {PERSON} may not read restricted. No warehouse was queried."
    answer = {"principal": PERSON, "reason": denial, "answer_text": denial, "phrased_by": "template"}
    shown = held.answer_scope(answer, PERSON)
    assert shown["answer_text"] == shown["reason"] == denial.replace(PERSON, "reader")
    answer["phrased_by"] = "model"
    assert held.answer_scope(answer, PERSON)["answer_text"] == denial
    assert held.answer_scope(answer, PERSON)["reason"] == denial
    answer.update(phrased_by="template", trace=[{"step": "plan"}, {"step": "answer"}])
    assert held.answer_scope(answer, PERSON)["reason"] == denial


@pytest.mark.parametrize("who", ["warehouse", "query", "read", "a"])
def test_scope_rendering_changes_only_the_subject_not_words_containing_it(tmp_path, who):
    held, _ = agent(tmp_path, True)
    held.g.acting_roles = lambda person: {"reader"}
    reason = f"Cross-warehouse query blocked: {who} may not read restricted. No warehouse was queried."
    answer = {"principal": who, "reason": reason, "agent": {"sql_source": "planner"}}
    assert held.answer_scope(answer, who)["reason"] == (
        "Cross-warehouse query blocked: reader may not read restricted. No warehouse was queried.")
