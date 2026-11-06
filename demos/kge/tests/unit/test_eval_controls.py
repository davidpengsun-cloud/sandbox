"""Both evaluation controls follow the same admission rule as the API."""
import shutil
import subprocess

import pytest

from kge.pipeline.settings import ROOT
from tests.unit.test_page import Markup, _function, _uncommented


@pytest.mark.parametrize("source", ["ui/index.html", "portal/ui/src/pages/Context/controller.js"])
def test_evaluation_control_follows_current_identity(source):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise both evaluation controls")
    raw = (ROOT / source).read_text()
    if source.endswith(".html"):
        page = Markup()
        page.feed(raw)
        raw = page.script
    code = _uncommented(raw)
    functions = []
    for name in ("canEvaluate", "canSee", "showView"):
        args, body = _function(code, name)
        functions.append(f"function {name}({', '.join(args)}) {body}")
    script = """
const assert = require('node:assert/strict');
let ME, VIEW;
const VIEWS = ['docs', 'ask', 'review', 'onboard', 'quality', 'tools'];
const elements = new Map();
const $ = selector => {
  if (!elements.has(selector)) elements.set(selector, {});
  return elements.get(selector);
};
const cue = () => {}, markTabs = () => {}, setChatWidth = () => {};
const chatWant = 480;
""" + "\n".join(functions) + """
const reader = {processes: ['querying'], roles: ['analyst_retail']};
const checker = {processes: ['building'], roles: ['custom_checker']};
const cases = [
  [null, false, false],
  [reader, false, true],
  [checker, true, true],
  [{processes: [], roles: ['admin']}, true, true],
  [{processes: [], roles: ['access_admin'], admin: true}, true, true],
  [{processes: ['certification', 'drafting', 'improving', 'accepting']}, false, false],
  [{id: 'admin@example.com', role: 'Admin', processes: ['querying']}, false, true],
  [checker, true, true],
  [reader, false, true],
  [null, false, false],
];
for (const [who, allowed, quality] of cases) {
  ME = who;
  assert.equal(canEvaluate(), allowed);
  assert.equal(canSee('quality'), quality);
  showView('quality');
  assert.equal($('#runeval').hidden, !allowed);
  assert.equal($('#nav-quality').hidden, !quality);
}
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    handler = code[code.index('$("#runeval")', code.index("const KINDS")):]
    assert handler.index("if (!canEvaluate()) return;") < handler.index('await api("/eval"')


def test_evaluation_buttons_are_hidden_before_identity_loads():
    assert '<button id="runeval" hidden>' in (ROOT / "ui/index.html").read_text()
    assert '<Button id={"runeval"} hidden>' in (ROOT / "portal/ui/src/pages/Context/Workspace.js").read_text()
