"""The business guide has one source, a deliberately restricted renderer and the page's vocabulary."""
from pathlib import Path
import re

from kge.services.tools.markdown import render_markdown

DEMO = Path(__file__).resolve().parents[2]
GUIDE = DEMO / "ui" / "onboarding.md"
PAGE = DEMO / "ui" / "index.html"


def test_the_guide_names_every_step_shown_on_the_onboard_panel():
    """The onboard panel's stepper shows a unit's stages (the page's STAGES): the guide gives each a section of its own, in
    the stepper's order. And it names every button the panel's actions offer, as the page labels it."""
    page = PAGE.read_text()
    stages = re.search(r"^const STAGES = \[([^\]]*)\];$", page, re.M)
    steps = re.findall(r'"([^"]+)"', stages.group(1)) if stages else []
    guide = GUIDE.read_text()
    sections = re.findall(r"^## \d+\. (\w[\w ]*?) (?:—|$)", guide, re.M)
    assert steps and sections[:len(steps)] == steps, (steps, sections)
    labels = re.search(r"^const OB_LABELS = \{([^}]+)\};$", page, re.M)
    buttons = re.findall(r':"([^"]+)"', labels.group(1)) if labels else []
    assert buttons and [label for label in buttons if f"**{label}**" not in guide] == [], buttons


def test_the_guide_is_business_facing_and_describes_industry_standards_and_data_policies():
    """Industry standards and data policies are drafted now (issue #57), and updating a live unit is built (step 7): the
    guide describes both and promises neither as coming."""
    guide = GUIDE.read_text()
    assert all(term in guide for term in ("industry standards", "data policies", "confirm that there is no industry equivalent",
                                          "proposed as restricted", "six parts of its graph"))
    assert "coming" not in guide
    keep = guide.split("## 7. Keep it current", 1)[1]
    assert "coming" not in keep and "(update):" in keep and "What changed" in keep and "proposed removal" in keep
    forbidden = re.compile(r"github\.com|demos/|docs/use-cases|\.\./\.\./|make |README\.md", re.I)
    assert not forbidden.search(guide)


def test_the_renderer_supports_its_whitelist_and_escapes_everything_else():
    source = "# <script>x</script>\n\nA **bold** and `code` <img src=x>.\n\n- one\n\n| A | B |\n|---|---|\n| <b>x</b> | y |\n\n> unsupported but visible"
    rendered = render_markdown(source)
    assert "<h1>&lt;script&gt;x&lt;/script&gt;</h1>" in rendered
    assert "<strong>bold</strong>" in rendered and "<code>code</code>" in rendered
    assert "<ul>\n<li>one</li>" in rendered and "<table>" in rendered
    assert "&lt;img src=x&gt;" in rendered and "&lt;b&gt;x&lt;/b&gt;" in rendered
    assert "&gt; unsupported but visible" in rendered
    assert "<script>" not in rendered and "<img" not in rendered and "<b>" not in rendered
    malformed = render_markdown("| A | B |\n|---|---|\n| every | cell | remains |")
    assert "every" in malformed and "remains" in malformed and "<table>" not in malformed


def test_the_guide_route_reads_the_one_source():
    app = (DEMO / "kge" / "services" / "tools" / "service.py").read_text()
    assert '"ui" / "onboarding.md"' in app and "render_markdown" in app
