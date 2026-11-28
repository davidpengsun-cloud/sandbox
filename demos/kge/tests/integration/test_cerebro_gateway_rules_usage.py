"""A configured-model Cerebro turn with a real rules-first Meridian MCP answer and accounted usage."""
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.scratch import registry, scratch_build

DEMO = Path(__file__).resolve().parents[2]
FIXTURE = DEMO / 'tests' / 'fixtures' / 'model_gateway' / 'golden.py'


def test_configured_model_rules_answer_preserves_gateway_usage(tmp_path):
    build = scratch_build(tmp_path / 'build')
    env = os.environ.copy()
    for key in ('K_SERVICE', 'STORE_URL', 'MODEL_USAGE_KEY', 'SESSION_SECRET', 'TRUSTED_CALLER_AUDIENCE'):
        env.pop(key, None)
    env.update({
        'MODEL_PROVIDER': 'mock', 'MODEL_HIGH_PROVIDER': '', 'MODEL_MEDIUM_PROVIDER': '', 'MODEL_LOW_PROVIDER': '',
        'ASK_ORDER': 'rules_first', 'ASK_AI_FALLBACK': 'on', 'DEMO_MODE': '1', 'PORTAL_SYNC_TOKEN': 's' * 32,
        'MODEL_RATE_LIMITS': '', 'PROFILES': str(registry(build)), 'GOLDEN_SET': str(build / 'sage_v1.json'),
        'GOLDEN_QUESTIONS': str(tmp_path / 'questions.json'), 'GOLDEN_RESULTS': str(tmp_path / 'results.json'),
        'GOLDEN_SCORES': str(tmp_path / 'scores.json'), 'RULES_USAGE_QUESTION': 'NNA for June 2025',
    })
    completed = subprocess.run([sys.executable, str(FIXTURE)], env=env, text=True, capture_output=True, timeout=180)
    assert completed.returncode == 0, f'{completed.stdout}\n{completed.stderr}'
    results = json.loads((tmp_path / 'results.json').read_text())
    assert len(results) == 1 and not results[0].get('error') and not results[0]['interrupt']
    answer = next(reply for reply in results[0]['replies'] if reply.get('behaviour') == 'figure')
    assert round(float(answer['figure']), 2) == 9947562.78
    usage = results[0]['usage']
    assert usage['complete'] is True and usage['calls'] >= 1
    assert usage['input_tokens'] == 10 * usage['calls'] and usage['output_tokens'] == 4 * usage['calls']
    assert usage['thinking_tokens'] == usage['calls'] and usage['cost_usd'] is not None
