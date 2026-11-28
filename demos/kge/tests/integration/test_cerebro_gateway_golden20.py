"""Combined offline Cerebro → model gateway → Meridian MCP acceptance.

This uses a scripted model that sends each primary golden question to Ask. It checks
routing and approved SQL answers, not live-model tool selection or alternate wording.
The usage values and prices are synthetic. Requires a built Meridian demo and the
portal server's Node dependencies; no cloud credentials are used.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.scratch import registry, scratch_build

DEMO = Path(__file__).resolve().parents[2]
FIXTURE = DEMO / "tests" / "fixtures" / "model_gateway" / "golden.py"


def test_cerebro_gateway_meridian_golden20(tmp_path):
    build = scratch_build(tmp_path / "build")
    env = os.environ.copy()
    for key in (
        "K_SERVICE",
        "STORE_URL",
        "MODEL_USAGE_KEY",
        "SESSION_SECRET",
        "TRUSTED_CALLER_AUDIENCE",
    ):
        env.pop(key, None)
    env.update(
        {
            "PYTHONPATH": str(DEMO),
            "PROFILE": "",
            "MODEL_PROVIDER": "none",
            "MODEL_HIGH_PROVIDER": "none",
            "MODEL_MEDIUM_PROVIDER": "none",
            "MODEL_LOW_PROVIDER": "none",
            "DEMO_MODE": "1",
            "PORTAL_SYNC_TOKEN": "s" * 32,
            "MODEL_RATE_LIMITS": "",
            "PROFILES": str(registry(build)),
            "GOLDEN_SET": str(build / "sage_v1.json"),
            "GOLDEN_QUESTIONS": str(tmp_path / "questions.json"),
            "GOLDEN_RESULTS": str(tmp_path / "results.json"),
            "GOLDEN_SCORES": str(tmp_path / "scores.json"),
        }
    )
    run = subprocess.run(
        [sys.executable, str(FIXTURE)],
        env=env,
        text=True,
        capture_output=True,
        timeout=1000,
    )
    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
    results = json.loads((tmp_path / "results.json").read_text())
    scores = json.loads((tmp_path / "scores.json").read_text())
    assert len(results) == len(scores) == 20
    assert len({item["id"] for item in scores}) == 20
    for result, score in zip(results, scores, strict=True):
        assert result["id"] == score["id"]
        assert not result.get("error") and not result["interrupt"], result
        assert score.get("baseline_status") == score.get("agent_status") == "passed", (
            score
        )
        assert any(
            reply.get("question") == result["question"] for reply in result["replies"]
        ), result
        assert score["same_rows"], score
        assert score["usage"] == {
            "calls": 1,
            "input_tokens": 10,
            "output_tokens": 4,
            "thinking_tokens": 1,
            "cost_usd": 0.000036,
            "complete": True,
        }, score
