"""The page no-term log (kge/app.py -> unmatched.jsonl).

    .venv/bin/python -m pytest tests/integration/test_unmatched.py -q
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from kge import app as app_mod
from kge.pipeline.trace import read_jsonl
from kge.eval import harness


def _token_for(principal: str) -> str:
    tokens = app_mod.ids().by_token
    return next(tok for tok, who in tokens.items() if who == principal)


def test_only_graph_on_no_term_abstentions_are_logged():
    """Graph-on no-term abstentions on /answer, /ask and the /agent treatment are logged; graph-off arms, answered
    questions, refusals for another reason, the harness's direct Arms.ask calls (kge/eval/harness.py, kge/eval/margin.py),
    questions holding personal data and held-out questions asked on the page never are."""
    log_file = app_mod.PROFILE.run_dir() / "unmatched.jsonl"
    log_file.unlink(missing_ok=True)
    try:
        with TestClient(app_mod.app) as client:
            headers = {"Authorization": f"Bearer {_token_for('analyst_enterprise')}"}

            r1 = client.post("/answer", json={"question": "Lease income for June 2025", "arm": "graph"}, headers=headers)
            assert r1.status_code == 200 and r1.json()["behaviour"] == "abstain"

            r2 = client.post("/ask", json={"question": "Lease income for June 2025", "arm": "graph"}, headers=headers)
            assert r2.status_code == 200 and r2.json()["behaviour"] == "abstain"

            r3 = client.post("/agent", json={"question": "Lease income for June 2025", "arm": "treatment"}, headers=headers)
            assert r3.status_code == 200 and r3.json()["behaviour"] == "abstain"

            client.post("/answer", json={"question": "Lease income for June 2025", "arm": "schema"}, headers=headers)
            client.post("/answer", json={"question": "Lease income for June 2025", "arm": "catalog"}, headers=headers)
            client.post("/agent", json={"question": "Lease income for June 2025", "arm": "control"}, headers=headers)

            ok_resp = client.post("/answer", json={"question": "What was retail revenue in June 2025?", "arm": "graph"}, headers=headers)
            assert ok_resp.status_code == 200 and ok_resp.json()["behaviour"] == "figure"

            ref_resp = client.post(
                "/answer",
                json={"question": "Total revenue across retail and corporate in June 2025", "arm": "graph"},
                headers=headers,
            )
            assert ref_resp.status_code == 200 and ref_resp.json()["behaviour"] != "figure"

            arms = harness.Arms(app_mod.engine())
            arms.ask("treatment", "Lease income for June 2025", "analyst_enterprise")
            arms.ask("graph", "Lease income for June 2025", "analyst_enterprise")

            pii_resp = client.post(
                "/answer",
                json={"question": "Lease income for alice.smith@example.com in June 2025", "arm": "graph"},
                headers=headers,
            )
            assert pii_resp.status_code == 200 and pii_resp.json()["behaviour"] == "abstain"

            ho_file = app_mod.PROFILE.estate() / "heldout_leasing_v1.json"
            ho_file.write_text('{"name": "heldout_leasing_v1", "items": [{"id": "LEASING-H02", "question": "Total lease income in June 2025"}]}\n')
            try:
                for q in ("Total lease income in June 2025", "What was the total lease income in June 2025?"):
                    for endpoint, arm_name in (("/answer", "graph"), ("/ask", "graph"), ("/agent", "treatment")):
                        ho_resp = client.post(
                            endpoint,
                            json={"question": q, "arm": arm_name},
                            headers=headers,
                        )
                        assert ho_resp.status_code == 200 and ho_resp.json()["behaviour"] == "abstain"
            finally:
                ho_file.unlink(missing_ok=True)

        rows = read_jsonl(log_file)
        assert len(rows) == 3
        assert [r["path"] for r in rows] == ["/answer", "/ask", "/agent"]
        assert [r["arm"] for r in rows] == ["graph", "graph", "treatment"]
        assert all(r["question"] == "Lease income for June 2025" for r in rows)
        assert all(r["principal"] == "analyst_enterprise" for r in rows)
    finally:
        log_file.unlink(missing_ok=True)
