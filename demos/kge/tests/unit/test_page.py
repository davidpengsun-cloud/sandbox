"""The page: where each answer, error and steward outcome lands, read from ui/index.html.

    .venv/bin/python -m pytest tests/unit/test_page.py -q

Each test pins one finding from the black-box run of the page (UAT on PR #41),
so the page's markup and script cannot drift back: every select has a label;
the steward's outcome shows in the queue panel, never in the question thread;
a question and its answer are one exchange, the question first and the newest
exchange on top; a failed question clears the panel beside the thread, and an
earlier question that answers late never writes over it; signing in as another
principal clears it too. The rest pin the review's findings on
the page: a cited answer never shown as resting on no documents, a citation
marker showing its own answer on every tab, the panel naming the answer it
describes and claiming no graph for it, the golden set panel naming whom it
scored as, the reference agent's arms sent to /agent, each
part of an agent's answer across units linked to the source it is bound to, the
card naming each document its figures are bound to and how it is cited, an
agent's arm named in words with its run config's label kept for the tooltip, the
grain and calendar an agent's parts carry, the map of an answer from a
context, and of one that did not run, the link to the latest A/B report, a
sample question's exchange brought into view when it is picked, the whole list
folded back, and the sample questions the baseline records missed marked so.

Standard library only: no browser and no node. The markup is parsed with
html.parser. The script is read function by function: brackets are matched
with strings, template literals, regular expressions and comments skipped, and
comments are dropped first, so a commented-out line never passes a check.
The stylesheet is not read, nor the page as a browser draws it: a style that
redrew the thread in another order than the script writes it would take a
browser to see. Needs no build and no graph.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[2] / "ui" / "index.html"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Markup(HTMLParser):
    """Every element in document order as (tag, attributes, ids of the open ancestors); the labels, each label's for=
    to its text; the script."""

    def __init__(self):
        super().__init__()
        self.elements: list[tuple[str, dict, list[str]]] = []
        self.labels: dict[str, str] = {}
        self.script = ""
        self._open: list[tuple[str, str | None]] = []
        self._label: list | None = None
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        self.elements.append((tag, a, [i for _, i in self._open if i]))
        if tag == "label":
            self._label = [a.get("for", ""), ""]
        self._in_script = tag == "script"
        if tag not in VOID:
            self._open.append((tag, a.get("id")))

    def handle_endtag(self, tag):
        for k in range(len(self._open) - 1, -1, -1):
            if self._open[k][0] == tag:
                del self._open[k:]
                break
        if tag == "label" and self._label:
            self.labels[self._label[0]] = self._label[1].strip()
            self._label = None
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._label is not None:
            self._label[1] += data
        if self._in_script:
            self.script += data

    def by_id(self, id_: str):
        found = [(n, e) for n, e in enumerate(self.elements) if e[1].get("id") == id_]
        assert len(found) == 1, f"#{id_} appears {len(found)} times on the page"
        return found[0]


def _regex_may_start(src: str, i: int) -> bool:
    k = i - 1
    while k >= 0 and src[k].isspace():
        k -= 1
    return k < 0 or src[k] in "(,=:[!&|?{};+-*%<>~^" or re.search(r"\b(return|typeof)$", src[:k + 1]) is not None


def _end_of_literal(src: str, i: int) -> int | None:
    """If a comment, string, template literal or regular expression starts at i, the index just after it."""
    c = src[i]
    if src.startswith("//", i):
        n = src.find("\n", i)
        return len(src) if n < 0 else n
    if src.startswith("/*", i):
        return src.index("*/", i) + 2
    if c in "'\"":
        j = i + 1
        while src[j] != c:
            j += 2 if src[j] == "\\" else 1
        return j + 1
    if c == "`":
        j = i + 1
        while src[j] != "`":
            if src[j] == "\\":
                j += 2
            elif src.startswith("${", j):
                j = _close(src, j + 1)
            else:
                j += 1
        return j + 1
    if c == "/" and _regex_may_start(src, i):
        j, in_class = i + 1, False
        while in_class or src[j] != "/":
            if src[j] == "\\":
                j += 1
            elif src[j] == "[":
                in_class = True
            elif src[j] == "]":
                in_class = False
            j += 1
        j += 1
        while j < len(src) and src[j].isalpha():
            j += 1
        return j
    return None


def _close(src: str, i: int) -> int:
    """src[i] opens a bracket: the index just after the bracket that closes it."""
    depth, j = 0, i
    while True:
        end = _end_of_literal(src, j)
        if end is not None:
            j = end
            continue
        if src[j] in "({[":
            depth += 1
        elif src[j] in ")}]":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1


def _uncommented(src: str) -> str:
    out, j = [], 0
    while j < len(src):
        end = _end_of_literal(src, j)
        if end is None:
            out.append(src[j])
            j += 1
        else:
            if not src.startswith(("//", "/*"), j):
                out.append(src[j:end])
            j = end
    return "".join(out)


def _args(src: str, i: int) -> list[str]:
    """src[i] opens a call's parentheses: its arguments, split at the top level."""
    end, out, depth, j, start = _close(src, i), [], 0, i + 1, i + 1
    while j < end - 1:
        lit = _end_of_literal(src, j)
        if lit is not None:
            j = lit
            continue
        if src[j] in "({[":
            depth += 1
        elif src[j] in ")}]":
            depth -= 1
        elif src[j] == "," and depth == 0:
            out.append(src[start:j].strip())
            start = j + 1
        j += 1
    return out + [src[start:end - 1].strip()]


def _statement(src: str, i: int) -> str:
    """From i to the end of the statement: the first ';' outside brackets, or the end of an enclosing block."""
    depth, j = 0, i
    while j < len(src):
        end = _end_of_literal(src, j)
        if end is not None:
            j = end
            continue
        if src[j] in "({[":
            depth += 1
        elif src[j] in ")}]":
            if depth == 0:
                return src[i:j]
            depth -= 1
        elif src[j] == ";" and depth == 0:
            return src[i:j + 1]
        j += 1
    return src[i:]


def _branch(src: str, cond: str) -> tuple[str, int]:
    """What runs when the condition (a regex over the inside of the if's parentheses) holds, and where it ends."""
    m = re.search(r"\bif\s*\(\s*" + cond + r"\s*\)\s*", src)
    assert m, f"no branch on {cond}"
    then = src[m.end():_close(src, m.end())] if src[m.end()] == "{" else _statement(src, m.end())
    return then, m.end() + len(then)


def _statements(src: str, start: str) -> list[tuple[int, str]]:
    """Each statement that begins with the regex start, and where it begins."""
    return [(m.start(), _statement(src, m.start())) for m in re.finditer(start, src)]


def _function(code: str, name: str) -> tuple[list[str], str]:
    """A named function's parameters and body."""
    m = re.search(r"\bfunction\s+" + name + r"\s*\(", code)
    assert m, f"the page has no function {name}"
    params = [p for p in _args(code, m.end() - 1) if p]
    brace = code.index("{", _close(code, m.end() - 1))
    return params, code[brace:_close(code, brace)]


def _listener(code: str, marker: str) -> str:
    """The addEventListener call whose handler mentions the marker."""
    calls = [code[m.end() - 1:_close(code, m.end() - 1)] for m in re.finditer(r"\.addEventListener\(", code)]
    found = [c for c in calls if marker in c]
    assert len(found) == 1, f"{len(found)} listeners mention {marker}"
    return found[0]


EL = r"""\$\(\s*["'`]#{}["'`]\s*\)"""


def writes(src: str, id_: str) -> bool:
    """Whether src sets the innerHTML or textContent of the element $("#id") finds."""
    return re.search(EL.format(id_) + r"\s*\.\s*(innerHTML|textContent)\s*=", src) is not None


@pytest.fixture(scope="module")
def page() -> Markup:
    p = Markup()
    p.feed(PAGE.read_text())
    return p


@pytest.fixture(scope="module")
def code(page) -> str:
    return _uncommented(page.script)


def test_the_reader_skips_what_is_not_code():
    assert _uncommented("a // b }\nc /* { */ e '// f' `/* g */`") == "a \nc  e '// f' `/* g */`"
    assert _args("f(a, `x${g(1, 2)}`, /[,)]/g, '}', b / 2)", 1) == ["a", "`x${g(1, 2)}`", "/[,)]/g", "'}'", "b / 2"]
    assert _function("function f(x, y) { if (x) { return `}${y}`; } }", "f") == (["x", "y"], "{ if (x) { return `}${y}`; } }")
    assert _branch("if (r.error) { s(\"}\"); return; } t();", r"r\.error") == ('{ s("}"); return; }', 32)


def test_every_select_has_a_label(page):
    selects = [a.get("id", "") for tag, a, _ in page.elements if tag == "select"]
    assert {"principal", "purpose", "arm"} <= set(selects)
    for id_ in selects:
        assert id_ and page.labels.get(id_), f"<select id={id_!r}> has no <label for> with text"


def test_every_label_names_an_element_that_exists(page):
    ids = {a.get("id") for _, a, _ in page.elements}
    assert page.labels and set(page.labels) <= ids


def test_the_token_box_has_an_accessible_name(page):
    _, (tag, a, _) = page.by_id("token")
    assert tag == "input" and a.get("aria-label", "").strip()


def test_the_password_box_shows_only_for_pilot_sign_in(page, code):
    """With SIGNIN=passwords (GET /mode says passwords) the username is typed, signed in with its
    password, and the token box is hidden as demo mode hides it; otherwise the password box stays hidden, as the markup
    has it."""
    _, (tag, a, _) = page.by_id("password")
    assert tag == "input" and a.get("type") == "password" and a.get("aria-label", "").strip() and "hidden" in a
    _, mode = _function(code, "loadMode")
    assert re.search(r"\bPASSWORDS\s*=\s*!\s*m\.error\s*&&\s*!\s*DEMO\s*&&\s*!!\s*m\.passwords\b", mode), "not from /mode, or beside demo mode"
    assert re.search(EL.format("password") + r"\.hidden\s*=\s*!\s*PASSWORDS\b", mode), "the password box shows without SIGNIN=passwords"
    assert re.search(EL.format("token") + r"\.hidden\s*=\s*DEMO\s*\|\|\s*PASSWORDS\b", mode)
    _, sign = _function(code, "signIn")
    assert re.search(r"PASSWORDS\s*\?\s*\{\s*username:\s*chosen,\s*password:\s*" + EL.format("password") + r"\.value\s*\}", sign), \
        "a password sign-in does not send the username and its password"
    assert re.search(EL.format("password") + r"\.value\s*=\s*\"\"", sign), "the password stays in the box after signing in"


def test_password_signin_uses_a_username_without_requesting_a_roster(page, code):
    _, (tag, attrs, _) = page.by_id("username")
    assert tag == "input" and attrs["type"] == "text" and attrs["autocomplete"] == "username"
    assert attrs["aria-label"] == "Username" and "hidden" in attrs
    _, mode = _function(code, "loadMode")
    assert re.search(EL.format("username") + r"\.hidden\s*=\s*!PASSWORDS", mode)
    for element in ("principal", "principal-label"):
        assert "hidden" in page.by_id(element)[1][1]
        assert re.search(EL.format(element) + r"\.hidden\s*=\s*!DEMO", mode)
    _, sign = _function(code, "signIn")
    assert re.search(r"const chosen\s*=\s*PASSWORDS\s*\?\s*" + EL.format("username") + r"\.value\.trim\(\)", sign)
    _, load = _function(code, "loadPrincipals")
    guard = re.search(r"if\s*\(!DEMO\)\s*return;", load)
    assert guard and guard.end() < load.index('api("/principals")')
    assert code.count('api("/principals")') == 1


def test_the_queue_message_sits_in_the_steward_panel_above_the_queue(page):
    n_msg, (_, a, around_msg) = page.by_id("queuemsg")
    n_queue, (_, _, around_queue) = page.by_id("queue")
    assert "steward" in around_msg and "steward" in around_queue
    assert n_msg < n_queue
    assert a.get("aria-live") or a.get("role") == "status"


def test_the_steward_outcome_is_written_to_the_queue_message_never_to_the_thread(code):
    handler = _listener(code, '"/steward/decide"')
    assert not re.search(EL.format("thread"), handler)
    r = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*await\s+api\(\s*\"/steward/decide\"", handler).group(1)
    error, _ = _branch(handler, rf"{r}\.error")
    second, end = _branch(handler, rf"""{r}\.status\s*===?\s*["']pending_second_check["']""")
    assert writes(error, "queuemsg"), "a failed decision is not shown in the queue panel"
    assert writes(second, "queuemsg"), "a second-check notice is not shown in the queue panel"
    assert writes(handler[end:], "queuemsg"), "a recorded decision is not shown in the queue panel"


def _exchange(code: str) -> tuple[str, str]:
    """ask()'s body and the name of the exchange element it builds."""
    _, body = _function(code, "ask")
    m = re.search(r"""\b(\w+)\s*\.\s*(?:className\s*=|classList\.add\()\s*["'`]exchange["'`]""", body)
    assert m, "ask() builds no .exchange"
    ex = m.group(1)
    assert re.search(rf"\b{ex}\s*=\s*document\.createElement\(", body)
    return body, ex


def test_ask_puts_the_question_in_a_new_exchange_at_the_top_of_the_thread(code):
    body, ex = _exchange(code)
    sent = body.index("await api(")
    q = [(n, s) for n, s in _statements(body, rf"\b{ex}\s*\.\s*(innerHTML\s*=|insertAdjacentHTML\()") if 'class="msg q"' in s]
    assert len(q) == 1 and "esc(question)" in q[0][1], "the question is not written into its exchange"
    assert q[0][0] < sent, "the question is written into its exchange after it is sent"
    threads = list(re.finditer(EL.format("thread"), body))
    assert len(threads) == 1, "ask() writes to the thread other than to add the exchange"
    assert re.match(rf"""\s*\.\s*(prepend\(\s*{ex}\s*\)|insertAdjacentElement\(\s*["']afterbegin["']\s*,\s*{ex}\s*\))""",
                    body[threads[0].end():]), "the exchange is not put at the top of the thread"


def test_ask_adds_the_answer_or_the_error_below_its_question(code):
    body, ex = _exchange(code)
    a = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*await\s+api\(", body).group(1)
    error, end = _branch(body, rf"{a}\.error")
    below = rf"""\b{ex}\s*\.\s*insertAdjacentHTML\(\s*["']beforeend["']"""
    assert any(f"{a}.error" in s for _, s in _statements(error, below)), "an error is not added to its own exchange"
    answers = [s for _, s in _statements(body[end:], below)]
    assert any("renderCombined(" in s and "renderAnswer(" in s for s in answers), "an answer is not added to its own exchange"


def test_clear_detail_overwrites_every_tab(page, code):
    tabs = re.findall(r"""["'](\w+)["']""", re.search(r"\bconst\s+TABS\s*=\s*(\[[^\]]*\])", code).group(1))
    buttons = [a["data-tab"] for tag, a, _ in page.elements if tag == "button" and "data-tab" in a]
    assert tabs and sorted(tabs) == sorted(buttons), "TABS and the tab buttons differ"
    for t in tabs:
        assert "detail" in page.by_id("tab-" + t)[1][2], f"#tab-{t} is not in the detail panel"
    (why,), body = _function(code, "clearDetail")
    each = re.search(r"\bTABS\s*\.\s*forEach\(", body)
    assert each, "clearDetail does not go through TABS"
    loop = body[each.end() - 1:_close(body, each.end() - 1)]
    assert re.search(r"""\$\(\s*["']#tab-["']\s*\+\s*\w+\s*\)\s*\.\s*innerHTML\s*=""", loop) and why in loop


def test_the_error_branch_of_ask_clears_the_panel_when_it_is_the_latest_question(code):
    _, body = _function(code, "ask")
    seq, counter = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*\+\+\s*(\w+)", body).groups()
    assert body.index("++") < body.index("await api("), "the question is numbered after it is sent"
    a = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*await\s+api\(", body).group(1)
    error, _ = _branch(body, rf"{a}\.error")
    assert re.search(rf"\bif\s*\(\s*{seq}\s*===?\s*{counter}\s*\)\s*\{{?\s*clearDetail\(", error), \
        "a failed question does not clear the panel, or a late one clears a newer answer's"
    assert re.search(r"\breturn\b", error), "the error branch goes on to render an answer"


def test_the_api_helper_turns_a_failure_into_an_error_and_a_status(code):
    start = re.search(r"\bconst\s+api\s*=", code).start()
    helper = _statement(code, start)
    then = re.search(r"\bfetch\([^)]*\)\s*\.\s*then\(", helper)
    assert then, "api() does not go through fetch(...).then"
    handlers, end = _args(helper, then.end() - 1), _close(helper, then.end() - 1)
    caught = re.match(r"\s*\.\s*catch\(", helper[end:])
    rejected = handlers[1] if len(handlers) == 2 else _args(helper, end + caught.end() - 1)[0] if caught else ""
    shape = re.compile(r"\berror\s*:.*\bstatus\s*:", re.S)
    assert shape.search(rejected), "a fetch that never reaches the server is not turned into {error, status}"
    assert re.search(r"\.ok\b", handlers[0]) and shape.search(handlers[0]), "a refused call is not turned into {error, status}"


def test_an_answer_renders_the_panel_only_while_it_is_the_latest_question(code):
    _, body = _function(code, "ask")
    seq, counter = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*\+\+\s*(\w+)", body).groups()
    call = re.search(rf"\bif\s*\(\s*{seq}\s*===?\s*{counter}\s*\)\s*\{{?\s*renderDetail\(", body)
    assert call, "renderDetail runs for an answer that is no longer the latest"
    assert seq in _args(body, call.end() - 1), "renderDetail is not told which question it renders"
    params, detail = _function(code, "renderDetail")
    assert len(params) >= 2, "renderDetail takes no question number"
    mine = params[1]
    for w in re.finditer(r"\bawait\b", detail):
        rest = detail[w.start() + len(_statement(detail, w.start())):]
        assert re.match(rf"\s*if\s*\(\s*{mine}\s*!==?\s*{counter}\s*(\|\|[^)]*)?\)\s*return\b", rest), \
            "after an await renderDetail goes on writing although a newer question was asked"


def _shown(code: str) -> str:
    """The variable that holds the answer the panel describes: renderDetail sets it to its answer first."""
    (a, *_), detail = _function(code, "renderDetail")
    m = re.match(rf"\{{\s*(\w+)\s*=\s*{a}\s*;", detail)
    assert m, "renderDetail does not first record which answer the panel now describes"
    return m.group(1)


def test_a_citation_marker_shows_its_answer_in_every_tab(code):
    handler = _listener(code, "a.cite")
    call = re.search(r"\bselectExchange\(", handler)
    assert call, "a citation marker does not select its exchange"
    ex, n = _args(handler, call.end() - 1)[:2]
    assert ex and ".dataset.n" in n, "the clicked marker is not passed with its exchange"
    assert "++" not in handler, "a click numbers a question: a newer question in flight could no longer take the panel"
    _, select = _function(code, "selectExchange")
    assert "EXCHANGES.get(" in select and "renderDetail(" in select
    params, detail = _function(code, "renderDetail")
    assert len(params) == 3 and re.search(rf"\bshowSources\(\s*{params[0]}\s*,\s*{params[2]}\s*\)", detail), \
        "the clicked marker's source is not the one highlighted"


def test_once_the_panel_shows_another_answer_an_earlier_render_writes_nothing(code):
    """A click renders an answer while a newer question is in flight; when that question answers and takes the panel,
    the click's /map read writes nothing over it when it returns, and an error clears what the panel describes."""
    shown = _shown(code)
    params, detail = _function(code, "renderDetail")
    for w in re.finditer(r"\bawait\b", detail):
        rest = detail[w.start() + len(_statement(detail, w.start())):]
        guard = re.match(r"\s*if\s*\(([^)]*)\)\s*return\b", rest)
        assert guard and re.search(rf"\b{shown}\s*!==?\s*{params[0]}\b", guard.group(1)), \
            "after an await renderDetail writes although the panel now describes another answer"
    _, clear = _function(code, "clearDetail")
    assert re.search(rf"\b{shown}\s*=\s*null\b", clear), "after an error the panel still claims to describe an answer"


def test_the_panel_names_the_answer_it_describes(page, code):
    """The question alone does not say which answer, since the control and the treatment are asked the same one."""
    n_about, (_, _, around) = page.by_id("about")
    assert "detail" in around and n_about < page.by_id("tab-sources")[0], "the panel has no line naming its answer above the tabs"
    (a, *_), detail = _function(code, "renderDetail")
    said = [s for _, s in _statements(detail, EL.format("about") + r"\s*\.\s*(innerHTML|textContent)\s*=")]
    assert len(said) == 1, "renderDetail does not name the answer the panel describes"
    for part in (f"{a}.question", f"armOf({a})", f"{a}.principal"):
        assert part in said[0], f"the panel's line does not give {part}"
    _, clear = _function(code, "clearDetail")
    assert writes(clear, "about"), "after an error the panel still names the last answer"


def test_an_agents_arm_is_named_by_its_side_of_the_ab(code):
    m = re.search(r"\bconst\s+armOf\s*=\s*\(\s*(\w+)\s*\)\s*=>", code)
    assert m, "the page has no armOf"
    body = _statement(code, m.start())
    a = m.group(1)
    assert re.search(rf"\b{a}\.agent\b", body) and "AGENTS[" in body and ".side" in body, "an agent's arm is not named graph off or graph on"


def test_the_panel_heading_claims_no_graph():
    """A control or schema answer's sources come from plain search, not from the graph."""
    heading = re.search(r"""<section[^>]*\bid=["']detail["'][^>]*>\s*<h2>([^<]*)</h2>""", PAGE.read_text())
    assert heading and "graph" not in heading.group(1).lower(), heading and heading.group(1)


def test_the_answer_the_panel_describes_is_marked_in_the_thread(page, code):
    css = PAGE.read_text().split("<style>", 1)[1].split("</style>", 1)[0]
    assert re.search(r"\.msg\.shown\s*\{[^}]*border-color", css), "the thread does not mark the answer the panel describes"
    (a, *_), detail = _function(code, "renderDetail")
    assert re.search(rf"""classList\.toggle\(\s*["']shown["']\s*,\s*ANSWERS\[[^\]]+\]\s*===?\s*{a}\s*\)""", detail)
    _, render = _function(code, "renderAnswer")
    assert re.search(r"\bconst\s+aid\s*=\s*`[^`]*data-aid=\"\$\{\s*ANSWERS\.push\(\s*a\s*\)", render), \
        "an answer with no sources has no index, so it cannot be marked"


def test_each_exchange_keeps_its_full_response_in_memory(code):
    store = re.search(r"\bconst\s+(\w+)\s*=\s*new\s+Map\(\)", code)
    assert store, "the page has no per-exchange response store"
    body, ex = _exchange(code)
    seq = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*\+\+\s*ASKED", body).group(1)
    answer = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*await\s+api\(", body).group(1)
    assert re.search(rf"\b{ex}\.dataset\.exchangeId\s*=\s*String\(\s*{seq}\s*\)", body)
    assert re.search(rf"\b{store.group(1)}\.set\(\s*String\(\s*{seq}\s*\)\s*,\s*{answer}\s*\)", body)


def test_selecting_an_exchange_reuses_its_map_read(code):
    assert re.search(r"\bconst\s+MAPS\s*=\s*new\s+WeakMap\(\)", code)
    _, detail = _function(code, "renderDetail")
    assert re.search(r"if\s*\(\s*!MAPS\.has\(\s*a\s*\)\s*\)\s*MAPS\.set\(\s*a\s*,\s*api\(", detail)
    assert re.search(r"await\s+MAPS\.get\(\s*a\s*\)", detail)


def test_an_exchange_selects_its_response_by_click_or_enter(code):
    _, select = _function(code, "selectExchange")
    assert "EXCHANGES.get(" in select and re.search(r"\brenderDetail\(", select)
    click = _listener(code, "marker?.dataset.n")
    assert re.search(r"\bselectExchange\(\s*ex\b", click)
    key = _listener(code, 'e.key !== "Enter"')
    assert re.search(r"\bselectExchange\(\s*e\.target\s*\)", key)


def test_the_selected_exchange_is_current_and_the_heading_names_its_question(page, code):
    (a, *_), detail = _function(code, "renderDetail")
    assert "aria-current" in detail and "EXCHANGES.get(" in detail
    heading = [s for _, s in _statements(detail, r"\$\(\s*[\"']#detail h2[\"']\s*\)\s*\.\s*(innerHTML|textContent)\s*=")]
    assert len(heading) == 1 and f"esc({a}.question)" in heading[0]


def test_a_cite_answer_has_sources_and_never_says_it_rests_on_none(code):
    has = re.search(r"\bconst\s+HAS_SOURCES\s*=\s*new\s+Set\(\[([^\]]*)\]\)", code)
    assert has and {"combined", "cite_certified", "cite"} <= set(re.findall(r"""["'](\w+)["']""", has.group(1)))
    _, render = _function(code, "renderAnswer")
    shown, _ = _branch(render, r"""a\.behaviour\s*===?\s*["']cite["']""")
    assert "a.cite" in shown and "shelf" in shown, "a cite answer's card does not list what it cites, by shelf"
    _, sources = _function(code, "renderSources")
    listed, _ = _branch(sources, r"""a\.behaviour\s*===?\s*["']cite["']""")
    assert "shelf" in listed and "uncertified" in listed, "the Sources tab does not mark each cited document certified or not"
    _, detail = _function(code, "renderDetail")
    none = detail.index("rests on no documents")
    cond = re.search(r"\bif\s*\(\s*hasSources\(\s*a\s*\)\s*\)", detail)
    assert cond and cond.start() < none, "renderDetail says 'rests on no documents' without asking hasSources"
    (param,), helper = _function(code, "hasSources")
    assert "HAS_SOURCES" in helper and f"{param}.citations" in helper, "an answer with citations has sources too"


def test_an_answer_says_who_asserted_a_fact_only_when_someone_did(code):
    """Since pack 1.1.0 a refusal across Retail and Wealth rests on no no-join fact, so its card names no one as
    asserting one."""
    _, render = _function(code, "renderAnswer")
    assert render.count("Asserted by") == 1
    assert re.search(r"a\.fact\?\.asserted_by\s*\?\s*`[^`]*Asserted by", render), "the card asserts a fact that names no one"


def test_the_tier_rule_lists_a_document_the_principal_may_not_read_as_held_back(code):
    _, sources = _function(code, "renderSources")
    assert re.search(r"\bheld\s*=\s*\(a\.not_ai_ready[^;]*\ba\.not_readable\b", sources), "a document of an unreadable unit is not shown as held back"


def test_the_golden_set_panel_names_the_principal_it_scored_as(code):
    handler = _listener(code, '"/eval"')
    assert re.search(r"Scored as \$\{\s*esc\(\s*ME\.id\s*\)", handler), "the panel does not name whom it scored as"
    caveat = re.search(r"ME\.reads\.length\s*<\s*UNITS\.length\s*\?", handler)
    assert caveat and "reads every unit" in handler[caveat.end():], "a principal who cannot read every unit is not told why it misses"
    assert writes(handler, "evalout")


def test_the_title_is_context_layer_and_the_profile_names_the_client(page, code):
    text = PAGE.read_text()
    assert re.search(r"<title>([^<]*)</title>", text).group(1) == "Context Layer"
    _, h1 = page.by_id("title")
    assert h1[0] == "h1" and re.search(r'<h1 id="title">Context Layer</h1>', text)
    assert re.search(r'<a id="product-discover" hidden>Data Discovery Portal</a>', text)
    _, load = _function(code, "loadProfile")
    assert '"/profile"' in load and "document.title" not in load and not writes(load, "title") and writes(load, "profile")
    assert "showBrand(p.title" in load, "the brand tile keeps the client's initial"
    assert re.search(r"\bp\.release\b", load), "the header names no landed release (#39 review round: the landing, shown)"
    assert re.search(r"UNITS\s*=\s*\w+\.units", load), "the profile's units, which the page counts by"


def test_the_sample_questions_are_the_profile_s_golden_questions(page, code):
    assert "CHIPS = [" not in code, "no question is written into the page"
    _, load = _function(code, "loadSamples")
    assert '"/golden"' in load and writes(load, "chips"), "the chips are the profile's golden questions, from GET /golden"
    assert writes(load, "evalnote"), "the golden set panel counts the profile's questions"
    assert page.by_id("q")[1][1].get("value") is None, "the question box holds no question of the client's"
    init = code[code.rindex("loadMode()"):]
    assert init.index("loadProfile") < init.index("loadPrincipals") and "loadSamples" in init, "the units are known before they are counted"


def test_the_default_chips_are_spread_over_the_questions_the_default_arm_answers(code):
    """The few chips shown first are spread over a set's samples, the questions the page's default arm answered as expected
    in the profile's A/B baseline (kge/eval/sets.py golden); every question stays a click away."""
    _, show = _function(code, "showSamples")
    assert re.search(r"spread\(\s*s\.samples", show), "the default chips are not spread over the set's samples"
    assert re.search(r"all\s*\?\s*s\.questions", show), "all the questions, a click away"


def test_a_chip_the_baseline_records_the_default_arm_missing_is_marked_a_known_miss(code):
    """UAT round 2: XB1 and XB2's chips gave figures that answer another question, and W11, W16 and W17's a reason that
    reads as a bug. Each chip a set's misses name (kge/eval/sets.py golden: what the profile's A/B baseline records the default
    arm failing) is marked a known miss, with a note pointing to the README; no question or id is written into the page."""
    (q, missed), body = _function(code, "chip")
    assert re.search(rf"\b{missed}\s*\?", body) and "known miss" in body, "a missed chip is not marked"
    _, show = _function(code, "showSamples")
    assert re.search(r"\bs\.misses\b", show) and "chip(" in show, "the chips are not marked from the set's misses"
    assert "README" in show and "known miss" in show.lower(), "no note says what a known miss is, or where the README says why"
    assert not re.search(r"\b(?:W\d\d|XB\d)\b", code), "an item's id written into the page"


def _set(tmp, name: str, items: list[dict], version: str | None = None) -> Path:
    import json
    path = tmp / f"{name}.json"
    path.write_text(json.dumps({"version": version, "items": items} if version else items))
    return path


def test_a_set_s_samples_are_the_questions_its_baseline_run_answered_on_the_arm(tmp_path):
    """kge/eval/sets.py golden: a question is a sample if every item that asks it as the run's principal passed on the arm in
    the baseline, which ran that very file; an item asked as another principal is not the page's to ask. No baseline, one
    of another file or another profile, or one in which nothing passed: every question. The known misses are the
    questions the baseline records an item of failing on the arm, asked as the run's principal."""
    import hashlib
    from kge.eval import sets
    one = [{"id": "A1", "kind": "k", "question": "first", "expect": {}}, {"id": "A2", "kind": "k", "question": "second", "expect": {}},
           {"id": "A3", "kind": "k", "question": "third", "expect": {}},
           {"id": "A3-R", "kind": "k", "question": "third", "expect": {}, "principal": "someone_else"},
           {"id": "A4", "kind": "k", "question": "fourth", "expect": {}}, {"id": "A4b", "kind": "k", "question": "fourth", "expect": {}}]
    path = _set(tmp_path, "extra_v1", one)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    base = {"profile": "p", "sets": {"extra_v1": {"file": path.name, "sha256": sha}},
            "items": {"extra_v1": {"A1": {"graph": 1.0}, "A2": {"graph": 0.0}, "A3": {"graph": 1.0}, "A3-R": {"graph": 0.0},
                                   "A4": {"graph": 1.0}, "A4b": {"graph": 0.0}}}}
    (got,) = sets.golden(tmp_path, base, "graph", "p")
    assert got["questions"] == ["first", "second", "third", "fourth"] and got["samples"] == ["first", "third"]
    assert got["misses"] == ["second", "fourth"], "a question another principal's item failed is not the page's miss"
    for other in ({**base, "sets": {"extra_v1": {"file": path.name, "sha256": "0" * 64}}}, {**base, "profile": "q"}, None,
                  {**base, "items": {"extra_v1": {k: {"graph": 0.0} for k in base["items"]["extra_v1"]}}}):
        assert sets.golden(tmp_path, other, "graph", "p")[0]["samples"] == got["questions"], other
    for other in ({**base, "sets": {"extra_v1": {"file": path.name, "sha256": "0" * 64}}}, {**base, "profile": "q"}, None,
                  {**base, "items": {}}, {**base, "items": {"extra_v1": {"A2": {"control": 0.0}}}}):
        assert sets.golden(tmp_path, other, "graph", "p")[0]["misses"] == [], f"no record of a miss on the arm: none ({other})"


def test_the_page_counts_the_profile_s_units_and_names_none_of_its_principals(code):
    assert not re.search(r"reads\.length\s*[=<>!]=*\s*\d", code), "a unit count written into the page"
    assert "analyst_enterprise" not in code and "four" not in code.lower()
    reads = _statement(code, re.search(r"\bconst\s+reads\s*=", code).start())
    assert "UNITS.length" in reads and "reads every unit" in reads
    _, load = _function(code, "loadPrincipals")
    assert "reads(p)" in load, "the sign-in list does not say what each principal reads"


def test_the_arm_list_holds_the_agents_arms_and_ask_sends_them_to_the_agent(page, code):
    _, load = _function(code, "loadPresets")
    assert "agent_arms" in load and "<optgroup" in load, "the arm list does not offer the reference agent's arms"
    _, body = _function(code, "ask")
    sent = re.search(r"\b(?:const|let)\s+path\s*=\s*([^;]*);", body)
    assert sent and re.search(r"AGENTS\[\s*body\.arm\s*\]", sent.group(1)) and '"/agent"' in sent.group(1), "an agent arm is not sent to /agent"
    _, render = _function(code, "renderAnswer")
    assert re.search(r"a\.agent\??\.mode", render), "an agent's answer does not say what ran it: rules, mock or model"


def test_each_part_of_an_agents_answer_links_the_source_it_is_bound_to(code):
    (param,), body = _function(code, "partsTable")
    head = re.search(r"<th[^>]*>Source</th>", body)
    assert head and re.search(rf"\b{param}\.agent\b", body), "the parts table has no source column for an agent's answer"
    assert re.search(rf"\b{param}\.figures\b", body) and re.search(r"\.cite\b", body) and re.search(r"\bciteLinks?\(", body), \
        "the source column does not link each figure's bound citation"


def test_the_control_panel_links_the_latest_ab_report(page, code):
    assert "control" in page.by_id("ab")[1][2], "the A/B report link is not in the control panel"
    _, body = _function(code, "loadAB")
    assert re.search(r"""api\(\s*["']/ab["']\s*\)""", body) and ".report" in body and writes(body, "ab")
    assert "make ab" in body, "with no run yet, the panel does not say how to make one"
    start = re.search(r"\bloadMode\(\)\s*\.then\(", code)
    assert start and "loadAB" in _statement(code, start.start()), "the page does not load the A/B runs when it opens"


def test_signing_in_as_another_principal_clears_the_panel(code):
    """The panel's map says what the principal who asked may read, and its plan ran as them: after a switch to
    analyst_retail it would show read access and SME SQL that analyst_retail does not have."""
    _, who = _function(code, "refreshWho")
    m = re.search(r"\b(?:const|let)\s+(\w+)\s*=\s*ME\s*\?\.\s*id\b", who)
    assert m and m.start() < who.index('await api("/whoami")'), "refreshWho does not note who was signed in before it asks"
    was = m.group(1)
    switched, _ = _branch(who, rf"{was}\s*&&\s*{was}\s*!==?\s*ME\s*\?\.\s*id")
    assert re.search(r"\+\+\s*ASKED\b", switched), "an answer asked as the last principal can still fill the panel"
    cleared = re.search(r"\bclearDetail\(", switched)
    assert cleared and "ME.id" in _args(switched, cleared.end() - 1)[0], "the panel is not cleared, or does not say who is signed in now"


def test_person_change_or_signout_clears_thread_and_all_cached_answers(code):
    _, who = _function(code, "refreshWho")
    switched, _ = _branch(who, r"was\s*&&\s*was\s*!==?\s*ME\s*\?\.\s*id")
    assert re.search(EL.format("thread") + r"\.replaceChildren\(\)", switched)
    assert re.search(r"ANSWERS\.length\s*=\s*0", switched)
    assert '$("#sql-query").value = ""' in switched
    assert '$("#sql-result").replaceChildren()' in switched
    assert 'delete $("#sql-result").dataset.query' in switched
    assert "EXCHANGES.clear()" in switched
    assert "MAPS.delete(a)" in switched
    assert re.search(EL.format("sql-query") + r"\.placeholder\s*=\s*\"SELECT count\(\*\) FROM a_table_you_may_read\"", switched)
    assert switched.index("clearDetail(") < switched.index("replaceChildren()"), "the mobile detail panel is removed with the exchange"
    _, ask = _function(code, "ask")
    guard = re.search(r"if\s*\(!ex\.isConnected\)\s*return;", ask)
    assert guard and ask.index("await api(") < guard.start() < ask.index("EXCHANGES.set(")
    assert guard.end() < ask.index("renderAnswer(")


def test_an_answer_asked_by_another_principal_is_not_mapped_as_the_one_signed_in(code):
    (a, *_), detail = _function(code, "renderDetail")
    other = re.search(rf"\bif\s*\(\s*{a}\.principal\s*!==?\s*ME\s*\?\.\s*id\s*\)", detail)
    assert other and other.start() < detail.index("/map/"), "an earlier principal's answer is mapped as the one signed in"


def test_how_a_citation_is_cited_is_said_one_way_and_never_as_a_tier_the_arm_did_not_read(code):
    """Gold is the authority and silver is cited with attribution; with the graph off a citation was found by search,
    under no tier rule, and is never called bronze."""
    m = re.search(r"\bconst\s+citedAs\s*=\s*\(\s*(\w+)\s*\)\s*=>", code)
    assert m, "the page has no citedAs"
    body = _statement(code, m.start())
    for said in ("the authority", "cited with attribution", "found by search, with no tier rule"):
        assert said in body, f"citedAs does not say {said!r}"
    assert "bronze" not in body
    _, sources = _function(code, "renderSources")
    tiers, _ = _branch(sources, r"a\.citations")
    assert "citedAs(" in tiers, "the Sources tab says how a citation is cited its own way"


def test_an_agents_card_names_the_document_each_figure_is_bound_to(code):
    """On rules the two arms usually give the same figure, so what each rests on is the difference the A/B shows."""
    (a,), bound = _function(code, "boundSources")
    for part in (f"{a}.figures", ".cite", f"{a}.citations", ".doc", ".version", "citedAs(", "citeLink("):
        assert part in bound, f"boundSources does not use {part}"
    _, render = _function(code, "renderAnswer")
    used = re.search(r"\bbody\s*\+=\s*boundSources\(\s*a\s*\)", render)
    assert used and used.start() > render.rindex("else body ="), "an answer's card does not name the documents its figures are bound to"


def test_an_agents_card_names_its_arm_and_keeps_the_run_config_label_for_a_tooltip(code):
    """On the mock the run config's label ran past the card's edge and put "model" beside a mock run, so the card names
    the arm and keeps the label for a tooltip."""
    _, render = _function(code, "renderAnswer")
    _, arm = _function(code, "armBadge")
    assert "armBadge(a)" in render and "armOf(a)" in arm, "the card does not name the arm in words"
    for m in re.finditer(r"\$\{\s*esc\(\s*a\.arm\s*\)\s*\}", arm):
        assert re.search(r'title="[^"<>]*$', arm[:m.start()]), "the run config's label is printed on the card, not kept for its tooltip"
    css = PAGE.read_text().split("<style>", 1)[1].split("</style>", 1)[0]
    badge = re.search(r"\.badge\s*\{([^}]*)\}", css).group(1)
    assert "nowrap" not in badge and "overflow-wrap:anywhere" in badge, "a long badge runs past the card's edge"


def test_the_parts_table_shows_the_grain_and_calendar_an_agents_figures_carry(code):
    (a,), body = _function(code, "partsTable")
    mapped = re.search(rf"{a}\.figures\s*\|\|\s*\[\]\s*\)\s*\.map\(\s*(\w+)\s*=>\s*\(\{{([^}}]*)\}}\)", body)
    assert mapped, "partsTable does not map an agent's figures"
    f, fields = mapped.groups()
    for k in ("grain", "calendar", "conversion"):
        assert re.search(rf"\b{k}\s*:\s*{f}\.{k}\b", fields), f"an agent's figure's {k} is dropped"


def test_the_plan_tab_lists_each_query_an_agents_answer_ran_and_says_none_ran_only_when_none_did(code):
    """The owner's acceptance run of PR #50 found an agent's answer across units citing two figures while the Plan tab
    said no warehouse query was run: such an answer has no plan and no one sql, but parts, each figure with its unit,
    column and query, and attempts with the gate's verdict on each query, which the tab now lists."""
    _, detail = _function(code, "renderDetail")
    rows = re.search(r"\bconst\s+planRows\s*=\s*Array\.isArray\(\s*plan\s*\)\s*\?\s*plan\s*:\s*([^;]+);", detail)
    assert rows, "the planner's plan, a list, is no longer what the Plan tab lists"
    ran = rows.group(1).strip()
    if re.fullmatch(r"\w+", ran):
        named = re.search(rf"\bconst\s+{ran}\s*=", detail)
        assert named, f"{ran} is not defined"
        ran = _statement(detail, named.start())
    assert re.search(r"\(\s*a\.figures\s*\|\|\s*\[\]\s*\)", ran) and ".unit" in ran and ".sql" in ran, \
        "with no plan, the Plan tab does not list each figure's unit and query"
    assert re.search(r"\ba\.attempts\b", ran) and ".passed" in ran, "the gate's verdict on each query is not shown"
    (_, written), = _statements(detail, EL.format("tab-plan") + r"\s*\.\s*innerHTML\s*=")
    order = [re.search(r, written) for r in (r"\ba\.sql\s*\?", r"\bplanRows\.length\s*\?", r"No warehouse query was run\.")]
    assert all(order) and order[0].start() < order[1].start() < order[2].start(), \
        "one sql, then the rows, and only then no query run: an abstention still says none ran"


def test_the_trace_tab_is_a_timeline_with_raw_json(code):
    _, detail = _function(code, "renderDetail")
    assert "trace.stages" in detail and all(name in detail for name in ("Step", "What ran", "Input", "Output", "Time"))
    assert "Raw JSON" in detail and re.search(r"JSON\.stringify\(\s*\{\s*run_trace\s*:", detail)


def test_sql_check_and_mcp_config_are_visible(page, code):
    for id_ in ("sql-check", "sql-query", "check-sql", "sql-result", "mcp-config", "copy-mcp", "mcp-result"):
        page.by_id(id_)
    assert 'api("/sql/check"' in code and 'new URL("/mcp", location.origin)' in code
    assert "navigator.clipboard.writeText" in code


def test_the_map_of_a_context_answer_shows_the_context_and_draws_no_empty_table(code):
    """A term a context answers, such as net new assets, maps to no column, so the Map tab shows the context the answer
    was built from, and what the answer used of it, with a table of implementations only when there are some."""
    (c, used), body = _function(code, "contextMap")
    for said in ("Measure", "Filters", "Row selectors", "Joins", "Time mode", "Across"):
        assert said in body, f"the context's map does not say {said}"
    for part in (f"{c}.measure", f"{c}.fact", f"{c}.filters", f"{c}.row_selectors", f"{c}.joins",
                 f"{used}.joins", f"{used}.time_mode", f"{used}.across"):
        assert part in body, f"the context's map does not show {part}"
    _, detail = _function(code, "renderDetail")
    assert re.search(r"\bm\.contexts\b", detail) and re.search(r"\ba\.context\b", detail), "the Map tab does not read the context"
    assert "contextMap(" in detail, "the Map tab does not show the context"
    impl = detail.index("<th>Unit</th><th>Column</th>")
    assert re.search(r"\(\s*m\.implementations\s*\|\|\s*\[\]\s*\)\s*\.length\s*\?", detail[:impl]), \
        "the implementations table is drawn with no rows"
    said = detail.index("no certified definition")
    assert "ctx" in detail[detail.rindex("defined by", 0, said):said] or "contexts" in detail[detail.rindex("defined by", 0, said):said], \
        "a term certified through its context is said to have no certified definition"


def test_the_map_of_an_answer_that_did_not_run_marks_no_join_made_and_crosses_to_no_unit(code):
    """UAT round 2: a question across Retail and Wealth blocked for analyst_retail queried no warehouse, yet the Map tab
    marked the context's joins joined; only an answer that ran shows what it used, and any other says why it did not."""
    _, detail = _function(code, "renderDetail")
    assert re.search(r"\bconst\s+ran\s*=\s*a\.behaviour\s*===\s*\"figure\"", detail), "what ran is not told apart"
    assert re.search(r"contextMap\(\s*ctx\s*,\s*ran\s*\?\s*a\.context\s*\|\|\s*\{\}\s*:\s*\{\s*\.\.\.a\.context\s*,\s*joins\s*:\s*\[\]\s*,"
                     r"\s*across\s*:\s*\[\]\s*\}\s*\)", detail), "an answer that did not run is mapped with the joins it would have made"
    said = detail[detail.index("contextMap("):detail.index("m.implementations")]
    assert re.search(r"\bran\s*\?\s*\"\"\s*:", said) and "did not run" in said and "a.denied_warehouses" in said, \
        "the map does not say the answer did not run, and why"


def test_with_no_ab_run_the_panel_says_how_to_make_one_for_this_profile(code):
    """The panel once said make ab runs every arm on the golden and held-out sets, on a profile with neither a held-out
    set nor a baseline."""
    (_, body), loaded = _function(code, "loadAB"), _function(code, "loadProfile")[1]
    assert "golden and held-out sets" not in body and "PROFILE=${esc(PROFILE_ID)}" in body and "PRINCIPAL" in body
    assert re.search(r"\br\.baseline\b", body) and "no A/B baseline" in body, "the panel does not say whether a baseline is there"
    assert re.search(r"\bPROFILE_ID\s*=\s*p\.profile\b", loaded), "the page does not keep the profile it is served for"


def test_a_new_exchange_and_then_its_answer_are_brought_into_view_below_the_header(code):
    """The thread sits below the fold under the sample questions, so a click showed nothing new on screen; ask() brings
    the question into view, then the latest question's answer, clear of the sticky header."""
    body, ex = _exchange(code)
    sent = body.index("await api(")
    shown = [m.start() for m in re.finditer(rf"\breveal\(\s*{ex}\s*\)", body)]
    assert shown and shown[0] < sent, "the question is not brought into view as it is asked"
    after = body[sent:]
    error, end = _branch(after, r"a\.error")
    assert re.search(rf"\breveal\(\s*{ex}\s*\)", error), "an error is not brought into view"
    latest, _ = _branch(after[end:], r"seq\s*===?\s*ASKED")
    assert re.search(rf"\breveal\(\s*{ex}\s*\)", latest), "the answer to the latest question is not brought into view"
    params, reveal = _function(code, "reveal")
    assert len(params) == 1 and re.search(rf"\b{params[0]}\s*\.\s*scrollIntoView\(", reveal)
    assert re.search(rf"\b{params[0]}\s*\.\s*style\s*\.\s*scrollMarginTop\s*=", reveal) and '$("header")' in reveal, \
        "the sticky header covers what is brought into view"
    assert "prefers-reduced-motion" in reveal, "motion is not kept for those who ask for less"


def test_a_sample_question_picked_from_the_whole_list_folds_the_list_back(code):
    """Picked from the whole list, the question went to a thread far down the page with the list still open above it."""
    handler = _listener(code, 'contains("more")')
    folded, end = _branch(handler, r"ALL")
    assert re.search(r"\bshowSamples\(\s*false\s*\)", folded), "the whole list stays open after a pick"
    assert end < handler.index("ask()"), "the list folds after the question is asked"
    _, show = _function(code, "showSamples")
    assert re.search(r"\bALL\s*=\s*all\b", show), "the page does not keep whether the whole list is shown"


def test_the_queue_says_of_a_cross_reference_match_whether_upstream_proposed_or_rejected_it(code):
    _, queue = _function(code, "loadQueue")
    assert re.search(r"\bt\.upstream\b", queue) and "upstream" in queue[queue.index("t.upstream"):][:200], \
        "a match's row does not say what upstream did with it"


def test_a_definition_from_the_glossary_says_so_and_cites_nothing_it_retrieved(code):
    """UAT round 2: "What is net new assets?" showed a certified card citing documents that do not define it. A term no
    document defines is answered from the pack's glossary, and what was retrieved is listed as not used, never as
    cited."""
    _, render = _function(code, "renderAnswer")
    shown, _ = _branch(render, r"""a\.behaviour\s*===?\s*["']glossary_definition["']""")
    assert "a.definition" in shown and "pack's glossary" in shown and "a.cite" not in shown, "the card cites, or does not say whose"
    _, badge = _function(code, "badge")
    assert re.search(r"""\bglossary_definition\s*:\s*["']ok["']""", badge), "the answer has no badge of its own"
    has = re.search(r"\bconst\s+HAS_SOURCES\s*=\s*new\s+Set\(\[([^\]]*)\]\)", code)
    assert has and "glossary_definition" in re.findall(r"""["'](\w+)["']""", has.group(1)), "its Sources tab says it rests on none"
    _, sources = _function(code, "renderSources")
    listed, _ = _branch(sources, r"""a\.behaviour\s*===?\s*["']glossary_definition["']""")
    assert re.search(r"\bcited\s*=\s*\[\s*\]", listed), "the Sources tab cites what the question retrieved"
    assert "a.references" in listed and "a.definition" in listed and "pack's glossary" in listed
    assert sources.index("glossary_definition") < sources.index("a.citations)"), "the tier rule's branch takes it first"
    _, detail = _function(code, "renderDetail")
    assert re.search(r"""a\.behaviour\s*===?\s*["']glossary_definition["']\s*\?\s*["']no document; the pack's glossary defines it""", detail), \
        "the Map tab says no certified definition"


def test_onboarding_formats_a_drafted_question_figure_like_other_figures(code):
    _, items = _function(code, "renderObItems")
    assert re.search(r"i\.kind\s*===\s*[\"']question[\"'].*money\(i\.detail\.figure\)", items, re.S)


def _style() -> str:
    return PAGE.read_text().split("<style>", 1)[1].split("</style>", 1)[0]


DARK_MEDIA = re.compile(r'@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{\s*:root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}\s*\}')
DARK_CHOSEN = re.compile(r':root\[data-theme="dark"\]\s*\{([^}]*)\}')


def _token_blocks(css: str) -> tuple[str, str, str, str]:
    """A stylesheet's token blocks, comments dropped: its first :root block (the light tokens), the dark block the system
    setting applies, the dark block a viewer's choice applies, and the rest of the stylesheet."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    root = re.search(r":root\s*\{([^}]*)\}", css)
    media, chosen = DARK_MEDIA.search(css), DARK_CHOSEN.search(css)
    assert root and media and chosen, "the token copy has a light block and both dark blocks"
    rest = css
    for found in (chosen, media, root):
        rest = rest[:found.start()] + rest[found.end():]
    return root.group(1), media.group(1), chosen.group(1), rest


def test_the_rich_answers_switch_is_off_on_every_page_load(page, code):
    _, (tag, a, around) = page.by_id("rich")
    assert tag == "input" and a.get("type") == "checkbox" and "checked" not in a, "the switch is on when the page loads"
    assert a.get("autocomplete") == "off", "a browser may restore the switch on reload"
    assert page.labels.get("rich") == "Rich answers" and "assistant" in around
    assert not re.search(EL.format("rich") + r"\s*\.\s*checked\s*=", code), "the page sets the switch itself"
    assert not re.search(r"(?:localStorage|sessionStorage)[^;\n]*rich", code, re.IGNORECASE), "the switch is remembered"


def test_ask_asks_for_a_display_only_while_the_switch_is_on(code):
    body, _ = _exchange(code)
    sent = re.search(r"\bawait\s+api\(\s*([^,]+),", body)
    assert sent and re.fullmatch(r"path\s*\+\s*(\w+)", sent.group(1).strip()), "the display is not asked for on the path"
    flag = re.fullmatch(r"path\s*\+\s*(\w+)", sent.group(1).strip()).group(1)
    made = re.search(rf"\bconst\s+{flag}\s*=\s*([^;]+);", body)
    assert made and re.search(EL.format("rich") + r"\s*\.\s*checked\s*\?\s*[\"']\?display=1[\"']\s*:\s*[\"'][\"']", made.group(1)), \
        "?display=1 is not sent only while the switch is on"
    assert "!AGENTS[body.arm]" in made.group(1), "an agent answer asks for the rich display instead of keeping its card"
    assert made.start() < body.index("await api("), "the switch is read after the question is sent"


def test_a_rich_card_is_drawn_only_from_a_display_with_a_table(code):
    body, ex = _exchange(code)
    below = rf"""\b{ex}\s*\.\s*insertAdjacentHTML\(\s*["']beforeend["']"""
    answers = [s for _, s in _statements(body, below) if "renderAnswer(" in s]
    assert len(answers) == 1 and "renderCombined(" in answers[0] and "renderRich(" in answers[0], "the card choice is not one statement"
    assert re.search(r"\ba\.display\?\.table\s*&&\s*!\s*a\.display\.error\b", answers[0]), "a rich card without a table, or with an error"
    assert answers[0].index("renderCombined(") < answers[0].index("renderRich("), "a combined answer keeps its own card"


def test_the_rich_card_reads_the_display_and_keeps_what_the_card_keeps(code):
    (a,), rich = _function(code, "renderRich")
    used = set(re.findall(rf"\b{a}\.(\w+)", rich))
    assert used <= {"display", "behaviour", "reason"}, f"renderRich reads the answer beyond its display and existing card data: {used}"
    assert re.search(rf"\bboundSources\(\s*{a}\s*\)", rich), "the card no longer names the documents its figures are bound to"
    assert "data-aid" in rich and "ANSWERS.push(" in rich, "the card cannot be pointed at from the panel"
    assert re.search(r"""<p class="ans">\$\{\s*citeLinks\(\s*esc\(\s*\w+\.headline\.text\s*\)\s*\)\s*\}</p>""", rich), \
        "the sentence is not a paragraph of its own, with its [n] markers linked"
    assert re.search(r"\bdrawChart\(", rich) and re.search(r"\.chart_note\b", rich), "no chart, and no line saying why"
    checked = _statement(rich, re.search(r"\bconst\s+checked\s*=", rich).start())
    assert re.search(r"\.checked\.line\b", checked) and "<details" not in checked, "the checked queries are not one line"
    assert not re.search(r"\.fig\b|class=\"fig\"", rich), "a rich card shows a big figure the display does not hold"
    assert re.search(r"\besc\(\s*\w+\.notes\.join\(", rich), "the rich card drops the answer's notes, or does not escape them"


def test_the_rich_card_keeps_the_arm_grouped_source_and_refused_joins(code):
    _, ordinary = _function(code, "renderAnswer")
    _, rich = _function(code, "renderRich")
    for helper in ("armBadge(a)", "refusedJoins(a)"):
        assert helper in ordinary and helper in rich, f"the ordinary and rich cards do not share {helper}"
    assert "groupedSource(a)" in rich, "the rich grouped card drops its unit and column"
    _, arm = _function(code, "armBadge")
    assert "armOf(a)" in arm and re.search(r"esc\(a\.arm\)", arm), "the shared arm badge is not escaped"
    _, source = _function(code, "groupedSource")
    _, provenance = _function(code, "provenance")
    assert "provenance(a)" in source and "figureSource(a)" in provenance, "the ordinary and rich figure cards do not share their provenance rendering"
    _, figure = _function(code, "figureSource")
    assert re.search(r"esc\(a\.unit\)", figure) and re.search(r"esc\(a\.column\)", figure), "the grouped source is not escaped"
    assert "contextOf(a)" in figure and re.search(r"esc\(a\.join_path\.join\(", figure), "the figure context or join path is not shared or escaped"
    _, joins = _function(code, "refusedJoins")
    assert re.search(r"esc\(a\.joins_refused\.join\(", joins), "the refused joins are not escaped"


def test_the_rich_sum_keeps_the_graph_verdict_and_escapes_its_reason(code):
    _, rich = _function(code, "renderRich")
    assert "commensurable" in rich and re.search(r"esc\(a\.reason\s*\|\|", rich), "the rich sum drops or does not escape the graph verdict"


def test_the_chart_is_drawn_by_code_from_the_table_alone(code):
    (d,), draw = _function(code, "drawChart")
    used = set(re.findall(rf"\b{d}\.(\w+)", draw))
    assert used <= {"chart", "table"}, f"the chart reads more than the display's chart and table: {used}"
    assert not re.search(r"\ba\.(rows|plan|figures|parts)\b", draw), "the chart reads the answer, not the table"
    assert "<svg" in draw and "<rect" in draw and 'class="bar"' in draw, "no inline SVG bars"
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|\brgb\(|fill=\"(?!none)", draw), "a colour written into the chart"
    assert re.search(r"\besc\(r\.label\)", draw) and not re.search(r"\$\{r\.label\}", draw), "a label not escaped"
    assert re.search(r"<line\b", draw) and re.search(r"<\s*0", draw), "no zero line for a negative value"
    assert re.search(rf"\b{d}\.chart\.max_bars\b", draw) and " shown" in draw, "the cap is not said"
    css = _style()
    assert re.search(r"\.chart\s+rect\.bar\s*\{[^}]*fill:\s*var\(--accent\)", css), "the bars are not on the page's accent token"


def test_every_cell_of_the_rich_table_is_escaped(code):
    (t,), table = _function(code, "richTable")
    assert re.search(r"citeLinks\(\s*esc\(", table) and not re.search(r"citeLinks\(\s*`", table), "a Source cell is not escaped"
    assert re.search(r"esc\(\s*v\s*\?\?", table) and re.search(r"esc\(\s*c\.label\s*\)", table), "a cell or a heading is not escaped"


def test_onboarding_renders_the_improvement_proposal_notes_and_version_history(page, code):
    for id_ in ("ob-proposal-card", "ob-proposal", "ob-notes", "ob-versions"):
        assert "onboard" in page.by_id(id_)[1][2], f"#{id_} is not in the onboard panel"
    _, prop = _function(code, "renderObProposal")
    assert writes(prop, "ob-proposal") and "p.version" in prop and "p.items" in prop
    assert "!OB?.units?.length" in prop
    assert "No proposal yet. Improve: Run proposes changes from the questions the live units miss." in prop
    assert 'obButton("improve", {}, (OB.units || []).every(u => u.live) || "plain")' in prop, "Improve is primary only once every unit is live"
    assert 'primary === "plain" ? ""' in _function(code, "obButton")[1]
    assert '<div class="items">' in prop, "proposal aliases stay visible to the engineer after Improve"
    _, notes = _function(code, "renderObNotes")
    assert writes(notes, "ob-notes")
    _, versions = _function(code, "renderObVersions")
    assert writes(versions, "ob-versions") and "Release again" in versions and "data-release-hash" in versions
    _, load = _function(code, "drawOnboard")
    assert "drawOnboard()" in _function(code, "loadOnboard")[1] and "OB_TURN" in _function(code, "loadOnboard")[1], "reads of /onboard can draw out of order"
    assert "renderObProposal(" in load and "renderObNotes(" in load and "renderObVersions(" in load
    dispatcher = _listener(code, '"/onboard/accept"')
    assert '"/onboard/certify"' in dispatcher and 'button.dataset.proposal' in dispatcher
    assert 'items:"all"' in dispatcher and "obPropActive" not in code
    assert "data-release-hash" in _listener(code, "b.dataset.releaseHash")

def test_onboarding_status_and_requests_are_escaped(code):
    _, loaded = _function(code, "drawOnboard")
    assert "#ob-status" in loaded and "renderObUnits(r.units)" in loaded


def test_engineer_workspace_and_business_reading_have_separate_upload_controls(page, code):
    _, can = _function(code, "canSee")
    for process in ("drafting", "building", "improving"):
        assert f'"{process}"' in can
    _, drawn = _function(code, "drawOnboard")
    assert 'canSee("onboard")' in drawn
    assert '#ob-add").hidden = !r.can.create' in drawn
    for control, parent in (("ob-file", "ob-upload"), ("ob-csv", "ob-upload"),
                            ("ob-rows", "ob-row-source"), ("ob-label", "ob-source-label")):
        assert parent in page.by_id(control)[1][2]
    assert "OB = r" in drawn


def test_hidden_controls_override_flex_layout_for_every_role():
    rules = re.findall(r"(?:^|})\s*\[hidden\]\s*\{([^}]+)\}", _style())
    assert any(re.search(r"display\s*:\s*none\s*!important", rule) for rule in rules), \
        "hidden landing controls must override .row and .field display:flex"


def test_onboarding_has_the_guide_and_a_unit_card_for_every_part(page, code):
    guide = next((a for tag, a, _ in page.elements if tag == "a" and a.get("id") == "ob-guide"), None)
    assert guide and guide["href"] == "/onboarding-guide" and guide.get("rel") == "noopener"
    _, cards = _function(code, "renderObUnits")
    for part in ("stagebar", "u.status", "Open requests", "Items to review", "History", "Documents"):
        assert part in cards, part
    assert cards.count("<details>") >= 2 and "esc(u.label || u.unit)" in cards


def test_onboarding_unit_card_escapes_labels_requests_items_history_and_documents(code):
    for function, fields in {
        "renderObUnits": ("u.label", "q.ask", "q.source"),
        "renderObItems": ("i.title",),
        "obHistory": ("h.step", "h.by", "h.at", "JSON.stringify"),
        "obDocuments": ("d.title", "path", "d.tier"),
    }.items():
        _, body = _function(code, function)
        for field in fields:
            assert re.search(rf"esc\([^)]*{re.escape(field)}", body), f"{function} does not escape {field}"


def test_onboarding_unit_card_splits_client_and_drafted_golden_questions(code):
    _, cards = _function(code, "renderObUnits")
    assert "u.golden?.client" in cards and "u.golden?.drafted" in cards
    assert "from client" in cards and "drafted" in cards


def test_onboarding_stage_bar_uses_manifest_items_and_candidate_markers(code):
    _, stages = _function(code, "obDone")
    assert "obDone(unit)" in _function(code, "obStages")[1], "the stage bar is not the one stepper"
    assert "unit.items" in stages and "unit.history" in stages and "unit.release?.model_hash" in stages
    assert 'h.step === "onboard"' in stages and "h.detail?.passed === true" in stages
    for name in ("Uploaded", "Drafted", "Accepted", "Certified", "Checks", "Live"):
        assert f'"{name}"' in code


def test_a_pending_update_shows_what_changed_on_its_unit_card_escaped(code):
    _, cards = _function(code, "renderObUnits")
    assert "obChanges(u.update)" in cards and "renderObItems(u.items)" in cards
    assert "obChange(i)" in _function(code, "renderObItems")[1]
    _, changes = _function(code, "obChanges")
    assert "What changed in v" in changes and all(kind in changes for kind in ('"changed"', '"added"', '"removed"'))
    for field in ("kind", "id", "changes.details", "update.version"):
        assert re.search(rf"esc\([^)]*{re.escape(field)}", changes), f"obChanges does not escape {field}"
    _, badge = _function(code, "obChange")
    assert "esc(item.change.kind)" in badge
    _, stages = _function(code, "obDone")
    assert "unit.update" in stages and "h.version" in stages, "an update's stages start again from its upload"


def test_the_map_lists_a_term_s_industry_standards_with_the_iri_as_plain_text(code):
    """Issue #57: the Map tab shows a term's mappings to the preloaded standards, each concept, its relation, its standard
    and version, and its IRI escaped as text in a code element, never a link the page follows."""
    _, body = _function(code, "standardsMap")
    for field in ("s.label", "s.note", "s.relation", "s.standard_name", "s.version", "s.iri"):
        assert re.search(rf"esc\([^)]*{re.escape(field)}", body), field
    assert "<code>${esc(s.iri)}</code>" in body and "href" not in body and "Industry standards" in body
    assert "standardsMap(m.standards)" in code


def test_onboarding_unit_card_shows_each_sub_graph_s_progress(code):
    _, cards = _function(code, "renderObUnits")
    assert "obProgress(u.progress)" in cards
    _, progress = _function(code, "obProgress")
    for field in ("name", "p.items", "p.accepted", "p.certified"):
        assert re.search(rf"esc\([^)]*{re.escape(field)}", progress), field
    for part in ("physical", "glossary", "metrics", "ontology", "documents", "governance"):
        assert f'"{part}"' in code, part


def test_chat_column_has_an_accessible_separator(page):
    _, (_, splitter, _) = page.by_id("chat-splitter")
    assert splitter["role"] == "separator"
    assert splitter["aria-orientation"] == "vertical"
    assert splitter["tabindex"] == "0"
    assert all(splitter.get(name) for name in ("aria-valuenow", "aria-valuemin", "aria-valuemax"))


def test_chat_width_storage_reads_and_writes_are_guarded(code):
    assert re.search(r"try\s*{[^}]*localStorage\.getItem\(CHAT_STORAGE\)", code, re.DOTALL)
    assert re.search(r"try\s*{[^}]*localStorage\.setItem\(CHAT_STORAGE", code, re.DOTALL)


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        c = [int(h.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_field_edges_and_the_focus_ring_meet_wcag_aa():
    """In light mode and in dark, whichever way dark is chosen: the system's setting or the viewer's."""
    css = _style()
    root, media, chosen, rest = _token_blocks(css)

    def declared(block):
        return dict(re.findall(r"--([\w-]+):\s*([^;{}]+);", block))

    light = {**declared(root), **declared(rest)}
    assert declared(media) == declared(chosen)
    for mode, values in (("light", light), ("dark", {**light, **declared(chosen)})):

        def resolve(name):
            value = values[name].strip()
            return resolve(value[6:-1]) if value.startswith("var(--") else value

        t = {name: resolve(name) for name in values}
        assert _contrast(t["faint"], t["panel"]) >= 4.5 and _contrast(t["muted"], t["sunk"]) >= 4.5, f"{mode}: small text under 4.5:1"
        assert _contrast(t["field-line"], t["panel"]) >= 3 and _contrast(t["field-line"], t["sunk"]) >= 3, f"{mode}: a field's edge under 3:1"
        assert _contrast(t["accent"], t["panel"]) >= 3 and _contrast(t["accent"], t["bg"]) >= 3, f"{mode}: the focus ring under 3:1"
        assert _contrast(t["v3-focus"], t["panel"]) >= 3 and _contrast(t["v3-focus"], t["bg"]) >= 3, f"{mode}: the focus ring under 3:1"
        for state in ("ok", "warn", "bad", "info"):
            assert _contrast(t[state], t[f"{state}-soft"]) >= 4.5, f"{mode}: {state}"
        assert _contrast(t["accent"], t["accent-ink"]) >= 4.5, mode
        assert _contrast(t["v3-action"], t["v3-action-ink"]) >= 4.5 and _contrast(t["v3-action-hover"], t["v3-action-ink"]) >= 4.5, mode
        assert _contrast(t["v3-link"], t["panel"]) >= 4.5 and _contrast(t["v3-link"], t["bg"]) >= 4.5, f"{mode}: links"
        assert _contrast(t["v3-structure"], t["v3-structure-soft"]) >= 4.5, f"{mode}: table headers"
        assert _contrast(t["v3-bar-ink-2"], t["v3-bar-2"]) >= 4.5, f"{mode}: top bar control text"
        assert _contrast(t["v3-bar-focus"], t["v3-bar"]) >= 3, f"{mode}: top bar focus ring"
        assert _contrast(t["v3-bar"], t["v3-bar-accent"]) >= 3, f"{mode}: the brand tile's initial"
    assert re.search(r":focus-visible\s*\{\s*outline:\s*2px solid var\(--accent\);\s*outline-offset:\s*2px", css), "no visible focus ring"
    assert "outline:none" not in css.replace(" ", ""), "a focus ring is taken away"
    assert re.search(r"input\[type=text\][^{]*\{[^}]*border:1px solid var\(--field-line\)", css), "fields keep a faint edge"


def test_each_role_sees_the_workspaces_it_can_use_and_lands_on_its_task(page, code):
    nav = [(a.get("data-view"), a) for tag, a, _ in page.elements if tag == "button" and a.get("role") == "tab" and "data-view" in a]
    assert [v for v, _ in nav] == ["ask", "review", "onboard", "quality", "tools"]
    for view, a in nav:
        _, (_, panel, _) = page.by_id(a["aria-controls"])
        assert panel.get("role") == "tabpanel" and panel.get("aria-labelledby") == a["id"], view
        assert ("hidden" in a) == (view != "ask"), "signed out, only Ask shows"
    for id_, view in (("steward", "review"), ("onboard", "onboard"), ("control", "quality"), ("sql-check", "tools"), ("mcp-config", "tools"), ("detail", "ask")):
        assert f"view-{view}" in page.by_id(id_)[1][2], f"#{id_} is not in the {view} workspace"
    _, can = _function(code, "canSee")
    assert '"certification"' in can and "ME.writes_terms" in can and '"querying"' in can, "the workspaces are not read from /whoami's processes"
    assert all(f'"{process}"' in can for process in ("drafting", "building", "improving", "accepting"))
    _, home = _function(code, "homeView")
    assert re.search(r'"certification"\)\s*\?\s*"review"\s*:\s*canSee\("onboard"\)\s*\?\s*"onboard"\s*:\s*"ask"', home), "a role does not land on its task"
    _, who = _function(code, "refreshWho")
    assert "showView(homeView())" in who, "signing in does not land the principal on its task"
    _, show = _function(code, "showView")
    assert "canSee(" in show and ".hidden" in show


def test_tabs_are_aria_tabs_with_arrow_keys_and_one_tab_stop(page, code):
    tabs = [a for tag, a, _ in page.elements if tag == "button" and "data-tab" in a]
    assert all(a.get("role") == "tab" and a.get("aria-controls") == "tab-" + a["data-tab"] for a in tabs)
    assert [a.get("aria-selected") for a in tabs].count("true") == 1 and sum("tabindex" not in a for a in tabs) == 1
    for a in tabs:
        _, (_, panel, _) = page.by_id(a["aria-controls"])
        assert panel.get("role") == "tabpanel" and panel.get("aria-labelledby") == a["id"]
    _, keys = _function(code, "tablist")
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert f'"{key}"' in keys, key
    assert "focus()" in keys and "preventDefault()" in keys
    _, mark = _function(code, "markTabs")
    assert "aria-selected" in mark and "tabIndex" in mark, "the selected tab is not the one tab stop"
    assert 'tablist($("#views")' in code and 'tablist($("#detail .tabs")' in code
    _, select = _function(code, "selectTab")
    assert "markTabs(" in select


def test_the_thread_is_read_out_and_the_chart_is_no_stray_tab_stop(page, code):
    _, (_, thread, _) = page.by_id("thread")
    assert thread.get("aria-live") == "polite"
    _, draw = _function(code, "drawChart")
    assert "viewBox" not in draw and "tabindex" not in draw, "the chart scales its text down, or is a tab stop"
    assert "overflow-x:auto" not in re.search(r"\.chart\s*\{([^}]*)\}", _style()).group(1), "a scrolling chart is a tab stop"
    assert re.search(r'class="bv">\$\{money\(r\.value\)\}', draw), "a bar is not labelled with its value"
    body, ex = _exchange(code)
    assert ".focus(" in body, "focus does not move to the new answer"


def test_asking_and_every_long_action_shows_it_is_busy(code):
    body, ex = _exchange(code)
    sent = body.index("await api(")
    assert re.search(r'busy\(\s*\$\("#ask"\)\s*,\s*"Asking…"\s*\)', body[:sent]), "Ask is not busy while it asks"
    assert "SKELETON" in body[:sent] and 'aria-busy="true"' in re.search(r"const SKELETON = `[^`]*`", code).group(0)
    assert re.search(r"\bASKING\s*\|\|", body[:body.index("const body")]), "a second question can be sent while one is asked"
    assert ".skeleton" in body[sent:], "the placeholder stays after the answer"
    (button, doing), b = _function(code, "busy")
    assert f"{button}.disabled = true" in b and "aria-busy" in b and f"{button}.textContent = {doing}" in b
    for marker, doing in (('"/eval"', '"Running…"'), ('"/sql/check"', '"Checking…"'), ('"/steward/decide"', "Accepting…")):
        assert "busy(" in _listener(code, marker) and doing in _listener(code, marker), marker
    _, post = _function(code, "obPost")
    assert "busy(button" in post, "an onboarding button is not busy while it runs"


def test_codes_and_build_detail_are_said_in_plain_words(page, code):
    _, b = _function(code, "badge")
    assert re.search(r"\$\{esc\(plain\(b\)\)\}", b) and 'title="${esc(b)}"' in b, "a badge prints the code, or drops it"
    said = re.search(r"const SAID = \{([^}]*)\}", code).group(1)
    codes = set(re.findall(r"(\w+)\s*:", re.search(r"const cls = \{([^}]*)\}", b).group(1)))
    assert codes <= set(re.findall(r"(\w+)\s*:", said)), "a behaviour has no plain words"
    for render in ("renderAnswer", "renderRich", "renderCombined"):
        assert 'data-behaviour="${esc(a.behaviour)}"' in _function(code, render)[1], f"{render}'s card drops its code"
    _, show = _function(code, "showSamples")
    assert "setName(s.name)" in show and "data-set=" in show, "a set is shown by its id"
    assert "README" not in show.split('title="', 1)[0] or "title=\"The README" in show, "the README pointer is printed, not a tooltip"
    _, ab = _function(code, "loadAB")
    assert "make ab" in ab.split("#about-ab", 1)[1] and "make ab" not in ab.split("#about-ab", 1)[0], "the CLI hint is not in About this build"
    assert "about-build" in page.by_id("profile")[1][2] and "about-build" in page.by_id("version")[1][2], "the build's hashes sit in the header"
    _, (_, token, _) = page.by_id("token")
    assert "python3" not in token.get("placeholder", "") and "make" not in token.get("placeholder", "")
    _, prob = _function(code, "problem")
    assert "401" in prob and "403" in prob, "a refusal is a raw status"
    assert "obPlain(" in _function(code, "obMessage")[1] and "ROWS=" in code, "the CLI's ROWS= is shown on the page"


def test_signed_out_ask_says_sign_in_and_demo_mode_hides_the_sign_in_button(code):
    _, ready = _function(code, "askReady")
    assert re.search(r"\.disabled\s*=\s*!ME", ready) and '"Sign in to ask"' in ready
    _, mode = _function(code, "loadMode")
    assert re.search(EL.format("signin") + r"\.hidden\s*=\s*DEMO\b", mode)
    _, who = _function(code, "refreshWho")
    assert "ME.role" in who and "account-detail" in who, "the signed-in pill names no role"


def test_a_figure_is_labelled_with_its_metric_period_basis_and_currency(code):
    _, label = _function(code, "figLabel")
    assert "a.context?.term" in label and "parsed.term" in label and "periodWords(" in label and "time_mode" in label
    assert "esc" in label
    _, big = _function(code, "bigFigure")
    assert "figLabel(a)" in big and "data-currency" in big and "copyButton(" in big and '<div class="fig">${money(a.figure)}</div>' in big
    _, render = _function(code, "renderAnswer")
    assert "bigFigure(a)" in render and "provenance(a" in render, "the answer and its source still run together"
    _, detail = _function(code, "renderDetail")
    assert re.search(r"ctx\?\.currency", detail) and "[data-currency]" in detail, "the currency is not read from the map"


def test_the_queue_is_grouped_and_takes_a_definition_inline(code):
    assert "prompt(" not in code, "a blocking prompt asks for the definition"
    _, draw = _function(code, "drawQueue")
    assert "FAMILIES" in draw and "data-family" in draw and "settled" in draw and 'class="quiet"' in draw and "input" in draw
    assert 'querySelector("input.def")' in _listener(code, '"/steward/decide"')
    _, render = _function(code, "renderAnswer")
    assert "queueLink" in render, "a routed document does not link its queue row"


def test_the_sql_check_shows_a_table_not_available_alike_whether_or_not_it_exists(page, code):
    handler = _listener(code, '"/sql/check"')
    assert "not available" in handler and "Not available" in handler and "unknown table" not in handler and "not permitted" not in handler
    assert "--mono" in re.search(r"#sql-query\s*\{([^}]*)\}", _style()).group(1)
    _, detail = _function(code, "renderDetail")
    assert re.search(EL.format("sql-query") + r"\.placeholder\s*=\s*a\.sql", detail), "no known-good query offered"


def test_the_plan_copies_a_formatted_query_and_the_trace_hides_an_empty_model_column(code):
    _, pretty = _function(code, "sqlPretty")
    assert "FROM" in pretty and "WHERE" in pretty and "\\n" in pretty
    assert "codeBlock(a.sql)" in _function(code, "renderDetail")[1] and "button.copy" in _listener(code, "nextElementSibling")
    _, detail = _function(code, "renderDetail")
    assert re.search(r"const modelled = steps\.some\(", detail) and 'modelled ? "<th>Output</th>"' in detail


def test_answer_settings_and_examples_fold_away(page):
    for id_ in ("purpose", "arm", "rich"):
        assert "settings" in page.by_id(id_)[1][2], f"#{id_} is not in Answer settings"
    assert "examples" in page.by_id("chips")[1][2]
    css = _style()
    assert "-webkit-line-clamp:2" in re.search(r"\.chips button\s*\{([^}]*)\}", css).group(1), "a sample question is cut to one line"


def test_a_principal_that_may_not_query_sees_no_ask_and_a_phone_keeps_about_this_build(page, code):
    """pipeline_tagger may not POST /ask, so Ask is not its workspace: none is, and the page says so. At phone width About
    this build stays reachable, its label shortened; in demo mode the MCP config says where a token comes from."""
    _, can = _function(code, "canSee")
    assert re.search(r'ask:\s*does\("querying"\)', can), "Ask shows to a principal that may not query"
    _, show = _function(code, "showView")
    assert '$("#no-view").hidden' in show and "VIEWS.find(canSee)" in show
    assert page.by_id("no-view")
    css = _style()
    phone = css.split("@media (max-width:600px)", 1)[1]
    assert "#about-build { display:none }" not in phone and "#about-build .long { display:none }" in phone
    _, mode = _function(code, "loadMode")
    assert '$("#mcp-token-how").hidden = !DEMO' in mode
    assert "make tokens" in PAGE.read_text().split('id="mcp-token-how"', 1)[1].split("</span>", 1)[0]


def test_next_step_uses_permissions_and_named_people(page, code):
    assert page.by_id("next-step") and page.by_id("onboard-count")
    _, draw = _function(code, "drawNextStep")
    assert "OB.can" in draw and "next-step-text" in draw and "mine.length" in draw
    assert "PRINCIPALS.filter" in _function(code, "actionPeople")[1]
    for name in ("loadQueue", "drawOnboard"):
        assert "drawNextStep()" in _function(code, name)[1]


def test_unit_controls_are_scoped_and_schema_preview_is_automatic(code):
    _, form = _function(code, "rowsForm")
    assert 'id="rows-csv-${esc(unit)}"' in form and 'for="rows-csv-${esc(unit)}"' in form
    assert 'id="rows-mode-${esc(unit)}"' in form and 'for="rows-mode-${esc(unit)}"' in form
    assert '"/onboard/tables"' in _function(code, "previewObTables")[1]
    assert '"ob-file", "ob-csv", "ob-rows"' in code and '"input", previewObTables' in code
    assert "#ob-items" not in code and "#ob-read" not in code


def test_refusals_offer_a_way_forward_without_reading_a_map(code):
    _, detail = _function(code, "renderDetail")
    assert detail.index("REFUSED.has(a.behaviour)") < detail.index("MAPS.has(a)")
    assert "No map for a refused answer" in detail and "wayForward(a)" in detail
    _, forward = _function(code, "wayForward")
    for value in ("denied_warehouses", "ME?.reads", "a.reason", "joins_refused", "restricted personal data", "will not sum", "esc(text)"):
        assert value in forward
    assert "PRINCIPALS" not in forward and "the business team that owns that data" in forward, "whom to ask is a role, never a person"


def test_phone_detail_moves_with_selection_and_does_not_intercept_its_tabs(code, page):
    assert "signin-box" in page.by_id("principal")[1][2]
    assert "account-menu" in page.by_id("signin-box")[1][2]
    _, place = _function(code, "placeDetail")
    assert "NARROW.matches && SHOWN" in place and "aria-current" in place and "aside" in place
    for name in ("clearDetail", "renderDetail"):
        assert "placeDetail()" in _function(code, name)[1]
    assert 'if (e.target.closest("#detail")) return;' in _listener(code, "marker?.dataset.n")


def test_jobs_show_elapsed_time_and_completed_steps_without_claiming_live_steps(code):
    _, strip = _function(code, "jobStrip")
    assert "started_at" in strip and "finished_at" in strip and "elapsed" in strip
    assert "last.n" in strip and "last.of" in strip and "Signed off" in strip
    assert '<progress aria-label="${esc(name)} running"></progress>' in strip
    assert 'id="ob-job"' in strip and 'id="ob-gate"' in strip
    assert "jobHome(job) !== where" in strip
    assert "seconds >= 1" in strip and " · took ${took}" in strip
    assert "job.gate?.lines?.length" in strip and "<br>candidate" not in strip
    assert "`unit:${home.unit}`" in _function(code, "jobHome")[1], "unit ids must not collide with proposal/bar job homes"


def test_finished_job_clears_only_its_unchanged_start_message(code):
    """Issue #142: the job strip owns completion; later action messages survive the poll."""
    _, post = _function(code, "obPost")
    assert 'path === "/onboard/run" && r.job?.id' in post
    assert 'OB_STARTED = {id: r.job.id, html: $("#ob-msg").innerHTML}' in post
    assert post.index("obMessage(said,") < post.index("OB_STARTED =")
    _, clear = _function(code, "clearObStarted")
    assert '!OB_STARTED || job?.id !== OB_STARTED.id || !["done", "failed"].includes(job.state)' in clear
    assert 'if ($("#ob-msg").innerHTML === OB_STARTED.html) $("#ob-msg").textContent = "";' in clear
    assert "OB_STARTED = null" in clear
    assert "OB_STARTED = null" in _function(code, "obMessage")[1]
    _, draw = _function(code, "drawOnboard")
    assert "clearObStarted(r.job)" in draw
    assert draw.index("if (who !== ME?.id)") < draw.index("clearObStarted(r.job)")
    assert draw.index("if (r.error)") < draw.index("clearObStarted(r.job)")


def test_queue_counts_only_open_tags_and_uses_plain_descriptions(code):
    _, load = _function(code, "loadQueue")
    assert 'Every kind (${open})' in load and 't.upstream !== "rejected"' in load
    _, draw = _function(code, "drawQueue")
    assert 'ts.filter(t => t.upstream !== "rejected").length' in draw
    assert "What was found" in draw and "Math.round(Number(t.confidence) * 100)" in draw
    _, words = _function(code, "said")
    assert 'Possible new term' in words and 'Possible personal data' in words and 'Possible duplicate client match' in words
    assert 'replace(/PROBABILISTIC/g, "probabilistic")' in words


def test_next_box_styling_does_not_style_the_next_stage_marker():
    css = _style()
    assert re.search(r"\.unit-card div\.next\s*\{", css)
    assert not re.search(r"(?:^|})\s*\.next\s*\{", css)


def test_sql_reply_from_a_previous_person_cannot_restore_the_cleared_panel(code):
    start = code.index('$("#check-sql").addEventListener')
    end = code.index('$("#copy-mcp").addEventListener', start)
    handler = code[start:end]
    assert 'person = ME?.id, epoch = ASKED' in handler
    guard = handler.index('if (person !== ME?.id || epoch !== ASKED) return;')
    assert handler.index('await api(') < guard < handler.index('$("#sql-result").dataset.query')
