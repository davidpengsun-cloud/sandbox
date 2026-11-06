// Existing Context task flows, mounted inside the portal rather than a second document.
// Scope selectors and tear down listeners, polling and requests when leaving the app.
export function mountContext(root, bootstrap) {
const cleanups = [];
const abort = new AbortController();
const timers = new Set();
const timeouts = new Set();
const fetch = (url, init = {}) => globalThis.fetch(url, { ...init, signal: abort.signal });
const setInterval = (fn, ms) => { if (abort.signal.aborted) return null; const id = globalThis.setInterval(fn, ms); timers.add(id); return id; };
const clearInterval = (id) => { globalThis.clearInterval(id); timers.delete(id); };
const setTimeout = (fn, ms) => { if (abort.signal.aborted) return null; const id = globalThis.setTimeout(fn, ms); timeouts.add(id); return id; };
const listen = (target, type, handler) => { target.addEventListener(type, handler); cleanups.push(() => target.removeEventListener(type, handler)); };
const document = {
  querySelector: (selector) => root.querySelector(selector),
  querySelectorAll: (selector) => root.querySelectorAll(selector),
  createElement: (tag) => globalThis.document.createElement(tag),
  get activeElement() { return globalThis.document.activeElement; },
  contains: (node) => root.contains(node),
  addEventListener: (type, handler) => listen(globalThis.document, type, handler),
};
const window = {
  addEventListener: (type, handler) => listen(globalThis.window, type, handler),
  top: globalThis.window.top,
};

const $ = (s) => root.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const money = (v) => v == null ? "none" : Number(v).toLocaleString(undefined, {maximumFractionDigits: 2});
const CHAT_MIN = 320;
const CHAT_DEFAULT = 480;
const CHAT_STORAGE = "kge-chat-width";
const chatSplitter = $("#chat-splitter");
const askView = $("#view-ask");
function chatMax() { return Math.max(CHAT_MIN, askView.clientWidth - 12 - 320 - 28); }
let chatWant = CHAT_DEFAULT;
function setChatWidth(width, remember = true) {
  if (askView.hidden) return chatWant;
  const next = Math.min(chatMax(), Math.max(CHAT_MIN, Math.round(width)));
  askView.style.setProperty("--chat-width", `${next}px`);
  chatSplitter.setAttribute("aria-valuenow", next);
  chatSplitter.setAttribute("aria-valuemax", chatMax());
  if (remember) { chatWant = next; try { localStorage.setItem(CHAT_STORAGE, String(next)); } catch (e) { /* storage may be unavailable */ } }
  return next;
}
try {
  const stored = Number(localStorage.getItem(CHAT_STORAGE));
  if (stored && Number.isFinite(stored)) chatWant = stored;
} catch (e) { /* storage may be unavailable */ }
setChatWidth(chatWant, false);
let chatDrag = null;
listen(chatSplitter, "pointerdown", e => {
  if (e.button !== 0) return;
  chatDrag = {x: e.clientX, width: Number(chatSplitter.getAttribute("aria-valuenow"))};
  chatSplitter.setPointerCapture(e.pointerId);
  e.preventDefault();
});
listen(chatSplitter, "pointermove", e => {
  if (chatDrag && chatSplitter.hasPointerCapture(e.pointerId)) setChatWidth(chatDrag.width + e.clientX - chatDrag.x);
});
listen(chatSplitter, "pointerup", e => {
  if (!chatSplitter.hasPointerCapture(e.pointerId)) return;
  chatSplitter.releasePointerCapture(e.pointerId);
  chatDrag = null;
});
listen(chatSplitter, "pointercancel", () => { chatDrag = null; });
listen(chatSplitter, "dblclick", () => setChatWidth(CHAT_DEFAULT));
listen(chatSplitter, "keydown", e => {
  const current = Number(chatSplitter.getAttribute("aria-valuenow"));
  const next = e.key === "ArrowLeft" ? current - 24 : e.key === "ArrowRight" ? current + 24
    : e.key === "Home" ? CHAT_MIN : e.key === "End" ? chatMax() : e.key === "Enter" ? CHAT_DEFAULT : null;
  if (next === null) return;
  e.preventDefault();
  setChatWidth(next);
});
window.addEventListener("resize", () => setChatWidth(chatWant, false));

// A tab list (the workspaces, and the tabs behind the answer): click or arrow keys select, one tab in the tab order.
function tablist(list, select) {
  const tabs = () => [...list.querySelectorAll('[role="tab"]')].filter(t => !t.hidden);
  listen(list, "click", e => { const t = e.target.closest('[role="tab"]'); if (t) select(t); });
  listen(list, "keydown", e => {
    const all = tabs(), i = all.indexOf(document.activeElement);
    if (i < 0) return;
    const next = e.key === "ArrowRight" || e.key === "ArrowDown" ? all[(i + 1) % all.length] : e.key === "ArrowLeft" || e.key === "ArrowUp" ? all[(i - 1 + all.length) % all.length]
      : e.key === "Home" ? all[0] : e.key === "End" ? all[all.length - 1] : null;
    if (!next) return;
    e.preventDefault();
    select(next);
    next.focus();
  });
}
function markTabs(list, chosen) {
  list.querySelectorAll('[role="tab"]').forEach(t => { const on = t === chosen; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1; t.classList.toggle("on", on); });
}
// The workspaces: each role sees the ones it can use, and lands on its own task when it signs in.
const VIEWS = ["docs", "ask", "review", "onboard", "quality"];
let VIEW = "ask";
function canEvaluate() {
  return !!ME && ((ME.processes || []).includes("building") || !!ME.admin || (ME.roles || []).includes("admin"));
}
function canSee(view) {
  if (view === "docs") return true;
  if (!ME) return view === "ask";
  const does = (p) => (ME.processes || []).includes(p);
  return {ask: does("querying"), review: does("certification"), onboard: !!ME.writes_terms || ["certification", "drafting", "building", "improving", "accepting"].some(does), quality: does("querying") || canEvaluate()}[view];
}
function homeView() {
  if (!ME) return "ask";
  return (ME.processes || []).includes("certification") ? "review" : canSee("onboard") ? "onboard" : "ask";
}

const nav = $("#views");
const cue = () => nav.classList.toggle("more", nav.scrollWidth - nav.clientWidth - nav.scrollLeft > 4);
listen(nav, "scroll", cue);
window.addEventListener("resize", cue);
cue();
function showView(name) {
  $("#runeval").hidden = !canEvaluate();
  VIEW = canSee(name) ? name : VIEWS.find(canSee) || null;
  if (VIEW) markTabs($("#views"), $("#nav-" + VIEW));
  VIEWS.forEach(v => { $("#nav-" + v).hidden = !canSee(v); $("#view-" + v).hidden = v !== VIEW; });
  $("#no-view").hidden = !!VIEW;   // one that may not query, such as the tagger: no workspace is its
  if (!VIEW) { $("#no-view p").textContent = `${ME.id} may not query, certify or onboard (its processes: ${(ME.processes || []).join(", ") || "none"}), so no workspace on this page is its.`; cue(); return; }
  if (VIEW === "ask") setChatWidth(chatWant, false);
  cue();
}
tablist($("#views"), t => showView(t.dataset.view));
// Popovers: one open at a time; a click elsewhere or Escape closes it.
document.addEventListener("click", e => document.querySelectorAll("details.popover[open]").forEach(d => { if (!d.contains(e.target)) d.open = false; }));
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  document.querySelectorAll("details.popover[open]").forEach(d => { d.open = false; d.querySelector("summary").focus(); });
});
// A button at work: disabled, saying what it is doing, until done() puts it back.
function busy(button, doing) {
  const was = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = doing;
  return () => { button.disabled = false; button.removeAttribute("aria-busy"); button.textContent = was; };
}
async function copyText(button, text) {
  try { await navigator.clipboard.writeText(text); button.textContent = "Copied"; }
  catch { button.textContent = "Select and copy"; }
  setTimeout(() => { button.textContent = "Copy"; }, 1600);
}

// Every call goes to this server; paths are API routes, not files.
// A failed call, or one that never reached the server, resolves to {error, status} so every caller can show why.
const contextApiBase = "/api/context";
const sessionPath = "/session";
let redirecting = false;
const initial = new Map(Object.entries({ "/whoami": bootstrap.person, "/profile": bootstrap.profile, "/mode": bootstrap.mode, "/presets": bootstrap.presets, "/healthz": bootstrap.build }));
const api = (path, init) => {
  if (!init && initial.has(path)) { const value = initial.get(path); initial.delete(path); return Promise.resolve(value); }
  return fetch(contextApiBase + path, init).then(async r => {
  if (r.status === 401 && !redirecting) {
    redirecting = true;
    await fetch(sessionPath, {method: "DELETE"});
    window.top.location = "/";
  }
  const body = await r.json().catch(() => ({}));
  return r.ok ? body : {error: body.detail || body.error || r.statusText, status: r.status};
}, e => ({error: "the server did not answer: " + e.message, status: "no reply"}));
};
// A failure in plain words: who may do what, not a status code.
function problem(error, status) {
  const text = typeof error === "string" ? error : JSON.stringify(error);
  if (status === 401) return "You are not signed in, or your session has ended. Sign in, then try again.";
  if (status === 403) return `Not permitted: ${text}`;
  if (status === "no reply") return "The server did not answer. Is it still running?";
  return text;
}
async function loadVersion() {
  const health = await api("/healthz");
  if (!health.error) $("#version").textContent = `Version ${health.version}`;
}
let ME = null;
// The shared password sign-in exposes no roster; action hints name the role.
const PRINCIPALS = [];

let PROFILE_ID = "";   // the profile this process serves (GET /profile)
let UNITS = [];   // the client's units, from GET /profile: the page names no client, and counts the profile's
// Whom this process serves: the profile, the pack's title and the artifact whose graph it serves (GET /profile).
async function loadProfile() {
  const p = await api("/profile");
  if (p.error) return;
  UNITS = p.units || [];
  PROFILE_ID = p.profile;

  $("#preview").textContent = p.preview ? p.preview.banner : "";
  $("#preview").dataset.level = p.preview && p.preview.released ? "info" : "warn";

  $("#profile").textContent = `Profile ${p.profile}, client ${p.client}, release ${p.release ? `${p.release.id} as of ${p.release.as_of}` : "none"}, ` +
    `artifact ${String(p.artifact || "none").slice(0, 12)}`;
}
async function refreshWho() {
  const was = ME?.id;   // who was signed in: the panel's map and plan are theirs
  const w = await api("/whoami");
  ME = w.error ? null : w;
  $("#queuemsg").innerHTML = "";
  askReady();
  // Move the detail panel out of a narrow-screen exchange before removing the old thread.
  if (was && was !== ME?.id) {
    ++ASKED;
    clearDetail(ME ? `Signed in as ${ME.id}. Ask a question and what lies behind its answer shows here.` : "Not signed in.");
    $("#thread").replaceChildren();
    EXCHANGES.forEach(a => MAPS.delete(a));
    ANSWERS.length = 0;
    EXCHANGES.clear();
  }
  if (was !== ME?.id) { OB = null; ++OB_PREVIEW; $("#ob-msg").textContent = ""; $("#onboard").removeAttribute("data-drawn"); drawNextStep(); showView(homeView()); } else showView(VIEW);
  $("#role-docs").src = "/api/context/onboarding-guide";
  root.dataset.person = ME?.id || "";
  loadQueue();
  loadOnboard();
  loadReleaseEvaluation();
}
let PRESETS = {};   // the arms, from GET /presets: each with the endpoints it runs on as it is
let AGENTS = {};    // the reference agent's arms, graph off and graph on, which run on /agent
let DEFAULT_ARM = "";   // the arm the page asks on first, which the sample questions' known misses are the baseline's misses on
async function loadPresets() {
  const p = await api("/presets");
  if (p.error) return;
  PRESETS = p.presets;
  AGENTS = p.agent_arms || {};
  const stages = (c) => esc(["resolution", "context", "sql_source", "citation_rule", "renderer"].map(k => k + "=" + c[k]).join(", "));
  $("#arm").innerHTML = `<optgroup label="The control: presets">` +
    Object.values(PRESETS).map(c => `<option value="${esc(c.arm)}" title="${stages(c)}">${esc(c.arm)}</option>`).join("") + `</optgroup>` +
    `<optgroup label="The reference agent, the A/B">` +
    Object.values(AGENTS).map(c => `<option value="${esc(c.arm)}" title="${stages(c)}; runs on ${esc(c.mode)}">agent ${esc(c.arm)}, ${esc(c.side)} (${esc(c.mode)})</option>`).join("") + `</optgroup>`;
  $("#arm").value = DEFAULT_ARM = p.default;
  settingsNow();
}
// The closed settings say what they are set to.
function settingsNow() {
  $("#settings-now").textContent = `· ${$("#arm").value || "default"} arm, ${$("#purpose").value}${$("#rich").checked ? ", rich answers" : ""}`;
}
for (const id of ["arm", "purpose", "rich"]) listen($("#" + id), "change", settingsNow);
async function loadMode() {
  const m = await api("/mode");
  $("#release-banner").hidden = m.released !== false;
  $("#release-banner").innerHTML = m.released === false ? esc(m.message || "") : "";
}
async function loadReleaseEvaluation() {
  const element = $("#release-eval-banner");
  if (!ME) { element.hidden = true; return; }
  const status = await api("/versions");
  const post = status.eval_post;
  element.hidden = !post;
  element.className = post?.passed === false ? "notice badge bad" : "notice muted small";
  element.innerHTML = post ? esc(post.message) + `<details><summary>Automatic release evaluation</summary><pre>${esc(JSON.stringify(post, null, 2))}</pre></details>` : "";
}
// Codes and ids, in plain words: what an answer did, a queue's kind of tag, a question set's name.
const SAID = {figure: "Answer", combined: "Answer with sources", sum_with_verdict: "Total", sum_without_verdict: "Total, not checked",
  parts_with_reason: "Parts, no total", abstain: "Cannot answer", abstain_with_reason: "Cannot answer", abstain_route_steward: "Sent to a steward",
  refuse: "Refused", blocked_access: "No access", cite_certified: "Definition", glossary_definition: "Glossary definition", cite: "Search results"};
const FAMILIES = {suggested_mention: "Suggested passage-to-term links", candidate_term: "Candidate terms", privacy: "Personal data in documents", xref_match: "Client cross-reference matches"};
const plain = (code, words = SAID) => words[code] || String(code ?? "").replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
const setName = (name) => name === "golden" ? "Sample questions" : plain(String(name).replace(/^golden_/, ""), {}) + " questions";
// The sample questions are the profile's golden questions (GET /golden: the question text alone): a few from each set,
// spread across its samples, the questions the default arm answered as expected in the profile's A/B baseline, and every
// one of them a click away; a set's misses, those the baseline records the default arm failing, are marked known misses.
let SAMPLES = [];
const SAMPLED = 4;   // from each set until all are shown
let ALL = false;     // whether every sample question is shown
function spread(qs, n) { return qs.length <= n ? qs : Array.from({length: n}, (_, i) => qs[Math.floor((i + 0.5) * qs.length / n)]); }
const MISS = () => `the ${DEFAULT_ARM || "default"} arm does not answer it as the set expects, as this profile's A/B baseline records`;
function chip(q, missed) {
  return `<button data-q="${esc(q)}" title="${esc(q)}${missed ? " · known miss: " + esc(MISS()) : ""}"${missed ? ' class="miss"' : ""}${ME ? "" : " disabled"}>${esc(q)}` +
    `${missed ? '<span class="tag">known miss</span>' : ""}</button>`;
}
function showSamples(all) {
  ALL = all;
  const total = SAMPLES.reduce((n, s) => n + s.questions.length, 0);
  const first = SAMPLES.map(s => spread(s.samples || s.questions, SAMPLED));
  const shown = SAMPLES.map((s, k) => all ? s.questions : first[k]);
  const missed = shown.some((qs, k) => qs.some(q => (SAMPLES[k].misses || []).includes(q)));
  $("#chips").innerHTML = SAMPLES.map((s, k) => `<span class="set" data-set="${esc(s.name)} ${esc(s.version)}" title="${esc(s.name)} ${esc(s.version)}">${esc(setName(s.name))}</span>` + shown[k].map(q => chip(q, (s.misses || []).includes(q))).join("")).join("") +
    (missed ? `<span class="note" title="The README's Demo script, step 2, says what each gives instead.">Known miss: ${esc(MISS())}.</span>` : "") +
    (total > first.reduce((n, f) => n + f.length, 0) ? `<button class="more" data-more="${all ? "" : "1"}">${all ? "Fewer sample questions" : `All ${total} sample questions`}</button>` : "");
}
async function loadSamples() {
  const g = await api("/golden");
  if (g.error) { $("#chips").innerHTML = `<span class="small muted">${esc(problem(g.error, g.status))}</span>`; return; }
  SAMPLES = g.sets || [];
  showSamples(false);
  const golden = SAMPLES.find(s => s.name === "golden");
  $("#evalnote").textContent = golden ? `${golden.questions.length} questions of the golden set on the three presets, scored apart by kind.` : "This profile holds no golden set.";
}
listen($("#chips"), "click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.classList.contains("more")) { showSamples(Boolean(b.dataset.more)); return; }
  if (ALL) showSamples(false);   // picked from the whole list: fold it back, so the thread is just below
  $("#q").value = b.dataset.q; ask();
});
// Signed out, asking does nothing: the button says why. While a question is on its way, it says so.
let ASKING = null;
function askReady() {
  const b = $("#ask");
  if (ASKING) return;
  b.disabled = !ME;
  b.textContent = ME ? "Ask" : "Sign in to ask";
  $("#q").placeholder = ME ? "Ask for a figure or a definition, or pick an example below" : "Sign in (top right) to ask a question";
  if (SAMPLES.length) showSamples(ALL);
}
const TABS = ["sources","map","plan","trace"];
function selectTab(name) {
  markTabs($("#detail .tabs"), $(`#detail .tabs [data-tab="${name}"]`));
  TABS.forEach(t => $("#tab-" + t).hidden = (t !== name));
}
tablist($("#detail .tabs"), b => selectTab(b.dataset.tab));
listen($("#ask"), "click", ask);
listen($("#q"), "keydown", e => { if (e.key === "Enter") ask(); });

function badge(b) {
  const cls = {combined:"ok", figure:"ok", sum_with_verdict:"ok", cite_certified:"ok", glossary_definition:"ok", parts_with_reason:"warn", abstain:"warn", abstain_with_reason:"warn", abstain_route_steward:"warn", refuse:"bad", blocked_access:"bad", sum_without_verdict:"bad", cite:"warn"}[b] || "";
  return `<span class="badge ${cls}" title="${esc(b)}">${esc(plain(b))}</span>`;
}
function armBadge(a) {
  return `<span class="badge" title="run config ${esc(a.arm)}${a.agent?.mode === "mock" ? ": each model stage ran on the mock" : ""}">${esc(armOf(a))}</span>`;
}
function figureSource(a) {
  return `${esc(a.unit)} · ${esc(a.column)}${contextOf(a)}${a.join_path?.length ? " · via " + esc(a.join_path.join(", ")) : ""}`;
}
// Where a figure came from, behind a toggle: the unit on the row, the column, context and joins inside.
function provenance(a, more = "") {
  return `<details class="prov"><summary>Source: ${esc(a.unit)}</summary><div>${figureSource(a)}${more}</div></details>`;
}
function groupedSource(a) {
  return a.behaviour === "figure" ? provenance(a) : "";
}
function refusedJoins(a) {
  return a.behaviour === "parts_with_reason" && a.joins_refused?.length ? `<div class="small muted">Joins refused: ${esc(a.joins_refused.join(", "))}</div>` : "";
}
// A figure's label: the metric, the period and the basis, read from the answer's own parse and context.
const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
function periodWords(period, months) {
  const month = (iso) => { const m = /^(\d{4})-(\d{2})/.exec(iso || ""); return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : ""; };
  if ((months || []).length === 1 && month(months[0])) return month(months[0]);
  const m = /^month (\d{4}-\d{2})/.exec(period || "");
  return m ? month(m[1]) : String(period || "").replace(/^(fiscal_)?(month|quarter|year|day) /, "");
}
function figLabel(a) {
  const parsed = (a.trace || [])[0] || {};
  const basis = {as_reported: "as reported", current: "current"}[a.context?.time_mode] || "";
  const said = [a.context?.term || parsed.term, periodWords(parsed.period, a.months), basis].filter(Boolean);
  return said.length ? `<div class="figlabel">${said.map(esc).join(" · ")}</div>` : "";
}
const copyButton = (value) => `<button class="copy" data-copy="${esc(value)}" aria-label="Copy the figure">Copy</button>`;
function bigFigure(a) {
  return figLabel(a) + `<div class="figrow"><span class="cur" data-currency></span><div class="fig">${money(a.figure)}</div>${a.figure == null ? "" : copyButton(a.figure)}</div>`;
}
// The steward's queue row a routed answer names, when the one signed in may review it.
const queueLink = (doc) => canSee("review") ? `<a class="queue-link" href="#view-review" data-doc="${esc(doc)}">${esc(doc)}</a>` : esc(doc);
const REFUSED = new Set(["refuse", "blocked_access"]);
function wayForward(a) {
  const denied = a.denied_warehouses || [];
  let text = "";
  if (denied.length) {
    // Name only the units the server's reason names: one it withholds stays withheld, and whom to ask is a role, never a person.
    const said = new Set((a.reason || "").toLowerCase().match(/[\w-]+/g) || []);
    const named = denied.filter(u => said.has(u.toLowerCase()));
    const other = (ME?.reads || []).filter(u => !denied.includes(u));
    text = `${named.length ? `You may not read ${named.join(", ")}` : "This question needs a unit you may not read"}. Ask about ${other.join(", ") || "the units you read"} instead, or ask the business team that owns that data if you need access.`;
  } else if (a.joins_refused?.length || /no certified join path/i.test(a.reason || "")) text = "Ask for each unit's figure on its own; a join between them is a steward's to certify.";
  else if (/restricted personal data/i.test(a.reason || "")) text = "Ask for the figure without the restricted columns.";
  else if (/will not sum/i.test(a.reason || "")) text = "Ask for each measure on its own.";
  return text ? `<div class="way"><b>A way forward.</b> ${esc(text)}</div>` : "";
}
function renderAnswer(a) {
  let body = "";
  const source = (f) => f?.cite ? " · source " + citeLink(f.cite, `[${f.cite}]`) : "";   // the citation a figure is bound to
  const notes = (a.notes||[]).length ? `<div class="small muted">${esc(a.notes.join("; "))}</div>` : "";
  if (a.behaviour === "figure" && a.rows) body = figLabel(a) + rowsTable(a) + provenance(a) + notes;
  else if (a.behaviour === "figure") body = bigFigure(a) + notes + provenance(a, source((a.figures || [])[0]));
  else if (a.behaviour === "sum_with_verdict") body = `<div class="fig">${money(a.figure)}</div><div class="small"><span class="badge ok">commensurable</span>${esc(a.reason)}</div>` + partsTable(a);
  else if (a.behaviour === "sum_without_verdict") body = `<div class="fig">${money(a.figure)}</div><div class="small"><span class="badge bad">no verdict</span>${esc(a.reason)}</div>` + partsTable(a);
  else if (a.behaviour === "parts_with_reason") body = `<div class="small"><span class="badge warn">${a.missing_parts?.length ? "incomplete: no total" : "not commensurable"}</span>${esc(a.reason)}</div>` + partsTable(a) + refusedJoins(a);
  else if (a.behaviour === "cite_certified") {
    const unused = (a.superseded_not_cited || []).length + (a.uncertified_definition_not_cited || []).length + (a.uncertified_context_not_cited || []).length + (a.references || []).length;
    const tier = Object.fromEntries((a.citations || []).map(c => [c.doc, c.tier]));   // the tier rule says each citation's tier
    body = `${a.answer ? `<div class="fig" style="font-size:18px">${esc(a.answer)}</div>` : ""}<div class="small">Cites: ${(a.cite||[]).map(c => `<code>${esc(c)}</code>${tier[c] ? ` <span class="muted">${esc(tier[c])}</span>` : ""}`).join(" ")}</div>` +
      (unused ? `<div class="small muted" style="margin-top:6px">${citeLink("", `${unused} source${unused > 1 ? "s" : ""} shown, not used`)}: ${a.references ? "superseded or reference only" : "superseded or uncertified"}</div>` : "") +
      (a.reason ? `<div class="small muted">${esc(a.reason)}</div>` : "");
  }
  else if (a.behaviour === "cite") {   // no shelf distinction in this arm: what search returned is cited, each shown by its shelf
    const shelf = Object.fromEntries((a.cited_docs || []).map(d => [d.doc, d.shelf]));
    body = `<div class="small">Cites: ${(a.cite||[]).map(c => `<code>${esc(c)}</code>${shelf[c] ? ` <span class="badge ${shelf[c] === "certified" ? "" : "warn"}">${esc(shelf[c])}</span>` : ""}`).join(" ")}</div>` +
      `<div class="small muted">${esc(a.reason || "")} · ${citeLink("", "show the sources")}</div>`;
  }
  else if (a.behaviour === "glossary_definition") {   // no document defines the term: the pack's glossary does, and nothing retrieved is cited
    const refs = (a.references || []).length;
    body = `<p class="ans">${esc(a.definition)}</p><div class="small muted">${esc(a.term)}, as the pack's glossary defines it` +
      `${a.steward ? " (steward: " + esc(a.steward) + ")" : ""}${a.contexts?.length ? "; certified through its context " + esc(a.contexts.join(", ")) : ""}. ` +
      `No document defines it, so none is cited${refs ? " · " + citeLink("", `${refs} retrieved, listed as reference${refs > 1 ? "s" : ""}`) : ""}.</div>`;
  }
  else if (REFUSED.has(a.behaviour)) body = `<div class="small">${esc(a.reason || "")}</div>` + wayForward(a);
  else body = `<div class="small">${esc(a.reason || "")}</div>` + (a.candidate_sources?.length ? `<div class="small muted">Routed to the steward: ${a.candidate_sources.map(queueLink).join(", ")}</div>` : "");
  body += (a.fact?.asserted_by ? `<div class="small muted">Asserted by ${esc(a.fact.asserted_by)} on ${esc(a.fact.asserted_on)}.</div>` : "");
  body += boundSources(a);   // what each figure rests on: where the two arms of the A/B differ when their figures agree
  const aid = ` data-aid="${ANSWERS.push(a) - 1}"`;   // every answer, so the thread can mark the one the panel describes
  // The reference agent's answers say what ran them: rules, the mock (which proves the seam, not a model) or a model.
  const ran = a.agent ? `<span class="badge ${a.agent.mode === "rules" ? "" : "warn"}" title="${a.agent.mode === "mock" ? "the offline mock model: proves the seam, not a model's quality" : "what ran the reference agent"}">agent · ${esc(a.agent.mode)}</span>` : "";
  const said = a.agent && a.answer_text ? `<p class="ans">${citeLinks(esc(a.answer_text))}</p>` : "";
  // The arm in words; its run config's label, which on the mock names the model stages the mock stands in for, is the tooltip.
  const arm = armBadge(a);
  return `<div class="msg" tabindex="-1" data-behaviour="${esc(a.behaviour)}"${aid}>${badge(a.behaviour)}${arm}${ran}${said}${body}</div>`;
}
function richTable(t) {
  const cell = (v, c) => c.type === "number" ? money(v) : c.type === "cite" && v ? citeLinks(esc(`[${v}]`)) : esc(v ?? "");
  const rows = t.rows.map(r => `<tr>${t.columns.map((c, i) => `<td class="${c.type === "number" ? "num" : ""}">${cell(r[i], c)}</td>`).join("")}</tr>`).join("");
  const total = t.total ? `<tr><th>${esc(t.total.label)}</th><td class="num">${money(t.total.value)}</td>${t.columns.slice(2).map(() => "<td></td>").join("")}</tr>` : "";
  return `<div class="scroll"><table class="rich-table"><caption>${esc(t.caption || "Figures")}</caption><tr>${t.columns.map(c => `<th class="${c.type === "number" ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr>${rows}${total}</table></div>`;
}
// A bar per row, each labelled with its value: the label and value are page text, the bar an SVG drawn in percentages.
function drawChart(d) {
  if (!d.chart || !d.table) return "";
  const keys = d.table.columns.map(c => c.key), xi = keys.indexOf(d.chart.x), yi = keys.indexOf(d.chart.y);
  const all = d.table.rows.map(r => ({label:String(r[xi] ?? ""), value:r[yi]})).filter(r => typeof r.value === "number" && Number.isFinite(r.value));
  const rows = all.slice(0, d.chart.max_bars);
  const lo = Math.min(0, ...rows.map(r => r.value)), hi = Math.max(0, ...rows.map(r => r.value));
  const span = hi - lo || 1, scale = v => (v - lo) / span * 100, zero = scale(0);
  const bars = rows.map(r => { const x = scale(r.value < 0 ? r.value : 0), w = Math.max(0.5, Math.abs(scale(r.value) - zero));
    return `<div class="bar-row"><span class="bl">${esc(r.label)}</span><svg aria-hidden="true"><line class="zero" x1="${zero}%" x2="${zero}%" y1="0" y2="18" />` +
      `<rect class="bar" x="${x}%" y="2" width="${w}%" height="14"><title>${esc(r.label)}: ${money(r.value)}</title></rect></svg><span class="bv">${money(r.value)}</span></div>`; }).join("");
  const note = all.length > rows.length ? `<div class="small muted">${rows.length} of ${all.length} bars shown; the table keeps every row.</div>` : "";
  return `<div class="chart" role="figure" aria-label="Bar chart drawn from the figures table">` + bars + `</div>` + note;
}
function renderRich(a) {
  const d = a.display, aid = ` data-aid="${ANSWERS.push(a) - 1}"`;
  const chart = d.chart ? drawChart(d) : d.chart_note ? `<div class="small muted">No chart: ${esc(d.chart_note)}</div>` : "";
  const table = d.chart ? `<details class="figures"><summary>The figures as a table</summary>${richTable(d.table)}</details>` : richTable(d.table);   // with a chart, the table is a click away
  const checked = d.checked ? `<div class="small muted" title="${esc(d.checked.about)}">${esc(d.checked.line)}</div>` : "";
  const notes = (d.notes || []).length ? `<div class="small muted">${esc(d.notes.join("; "))}</div>` : "";   // as the card without the switch shows them
  const verdict = a.behaviour === "sum_with_verdict" ? `<div class="small"><span class="badge ok">commensurable</span>${esc(a.reason || "")}</div>` : "";
  return `<div class="msg" tabindex="-1" data-behaviour="${esc(a.behaviour)}"${aid}>${badge(a.behaviour)}${armBadge(a)}<p class="ans">${citeLinks(esc(d.headline.text))}</p>${verdict}${chart}${table}${groupedSource(a)}${notes}${refusedJoins(a)}${checked}${boundSources(a)}</div>`;
}
// A figure a context of the graph answers names the context; one grouped by a dimension is its rows.
const contextOf = (a) => a.context ? ` · context ${esc(a.context.context)}${a.context.time_mode === "as_reported" ? ", as reported" : ""}` : "";
function rowsTable(a) {
  return `<div class="scroll"><table class="parts"><tr><th>${esc(a.group_by || "group")}</th><th class="num">Figure</th></tr>` +
    Object.entries(a.rows).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="num">${money(v)}</td></tr>`).join("") + `</table></div>`;
}
// A term certified through a context, mapped: what the graph declares of the context (its measure over its fact, its
// filters, row selectors and joins, by label), and what the answer used of it: the joins it made, its time mode, what it
// grouped by, the question's conditions, and the certified join it crossed to another unit. The joins it did not make
// fold under one line.
function contextMap(c, used) {
  const row = (name, html) => html ? `<tr><th>${name}</th><td>${html}</td></tr>` : "";
  const named = (xs) => (xs || []).map(x => `${esc(x.label || x.id)} <code>${esc(x.table)}</code>${x.mode === "include" ? " <span class='muted'>(kept only with one)</span>" : ""}`).join("<br>");
  const made = (j) => (used.joins || []).includes(j.id);
  const join = (j) => `<code>${esc(j.id)}</code> ${esc(j.from)} to ${esc(j.to)}` + (made(j) ? " <span class='badge ok'>joined</span>" : "");
  const taken = (c.joins || []).filter(j => made(j) || j.required), rest = (c.joins || []).filter(j => !made(j) && !j.required);
  const joins = taken.map(join).join("<br>") + (rest.length ? `<details class="more-joins"><summary>${rest.length} other certified join${rest.length > 1 ? "s" : ""}, made when a question reaches through them</summary>${rest.map(join).join("<br>")}</details>` : "");
  const across = (used.across || []).map(x => `${esc(x.unit)}, through the certified join <code>${esc(x.join)}</code>`).join("<br>");
  const mode = used.time_mode ? (used.time_mode === "as_reported" ? "as reported: each row's version valid on its month's freeze date" : "current: each row's version that holds now") : "";
  return `<div class="small" style="margin-top:6px">Resolves to the context <code>${esc(c.id)}</code>, ${esc(c.label)}: ${esc(c.description)}</div><table class="kv">` +
    row("Measure", `<code>${esc(c.measure)}</code> over <code>${esc(c.fact)}</code>${c.currency ? ", in " + esc(c.currency) : ""}`) + row("Filters", named(c.filters)) +
    row("Row selectors", named(c.row_selectors)) + row("Joins", joins) + row("Time mode", esc(mode)) +
    row("Grouped by", used.group_by ? `<code>${esc(used.group_by)}</code>` : "") + row("Conditions", esc((used.conditions || []).join("; "))) +
    row("Across", across) + `</table>`;
}
// A combined answer is split: the answer and its figures in the thread, the documents behind them in the Sources tab.
const ANSWERS = [];   // every answer in the thread, by its card's data-aid
const EXCHANGES = new Map();   // each full response, in memory, by the exchange that received it
const MAPS = new WeakMap();   // the map read on first display, reused when an earlier exchange is selected
// An answer's arm in words: a preset by its name; the reference agent's by its side of the A/B, graph off or graph on.
const armOf = (a) => a.agent ? `${a.agent.arm}, ${AGENTS[a.agent.arm]?.side || "the reference agent"}` : a.arm;
const citeLink = (n, label) => `<a class="cite" href="#tab-sources" data-n="${n}" title="${n ? "Show source " + n : "Show the sources"}">${label}</a>`;
const citeLinks = (html) => html.replace(/\[(\d+)\]/g, (_, n) => citeLink(n, `[${n}]`));
// How an answer cites a document: by the tier rule, gold as the authority and silver with attribution; graph off, as
// plain search found it. A citation with no tier is never called bronze: the control does not read the landing.
const citedAs = (c) => c.cited_as === "attributed" ? `silver, cited with attribution: ${c.attribution}` : c.cited_as === "authority" ? `${c.tier}, the authority`
  : `found by search, with no tier rule${c.tier ? `; landed ${c.tier}` : ""}`;
// Each document an answer's figures are bound to, once, with how it is cited.
function boundSources(a) {
  const by = Object.fromEntries((a.citations || []).map(c => [c.n, c]));
  const ns = [...new Set((a.figures || []).map(f => f.cite))].filter(n => by[n]);
  return ns.length ? `<div class="small bound">` + ns.map(n => `<div>${citeLink(n, `[${n}]`)} <code>${esc(by[n].doc)}${by[n].version ? " v" + esc(by[n].version) : ""}</code> ` +
    `${esc(by[n].title || "")} <span class="muted">· ${esc(citedAs(by[n]))}</span></div>`).join("") + `</div>` : "";
}
function renderCombined(a) {
  const aid = ANSWERS.push(a) - 1;
  const val = (f) => f.kind === "count" ? money(f.value) : `${f.currency ? esc(f.currency) + " " : ""}${money(f.value)}`;
  const figs = `<div class="scroll"><table class="figs"><tr><th>Figure · certified column</th><th class="num">Value</th><th class="num">Source</th></tr>` +
    a.figures.map(f => `<tr><td>${esc(f.label)} <span class="muted">(${esc(f.kind)})</span><code>${esc(f.column)}</code></td><td class="num">${val(f)}</td><td class="num">${f.cite ? citeLinks("[" + f.cite + "]") : ""}</td></tr>`).join("") + `</table></div>`;
  const docs = `${a.citations.length} cited · ${(a.not_cited || []).length} not used · ${(a.withheld || []).length} held back`;
  const gate = `<div class="small muted" style="margin-top:10px"><span class="badge ${a.guard?.passed ? "ok" : "bad"}">gate ${a.guard?.passed ? "passed" : "failed"}</span>SQL by ${esc(a.sql_source)} · text by ${esc(a.phrased_by)} · ${citeLink("", esc(docs))}</div>`;
  return `<div class="msg" tabindex="-1" data-behaviour="${esc(a.behaviour)}" data-aid="${aid}">${badge(a.behaviour)}<span class="badge">${esc(a.unit_label)} · ${esc(a.period)}</span>` +
    `<p class="ans">${citeLinks(esc(a.answer_text))}</p>${figs}${gate}</div>`;
}
function renderSources(a) {
  const li = (doc, title, why, excerpt, n) => `<li${n ? ` data-n="${n}"` : ""}>${n ? `<span class="n">[${n}]</span>` : ""}<code>${esc(doc)}</code> ${esc(title || "")}${why ? ` <span class="muted">· ${esc(why)}</span>` : ""}${excerpt ? `<span class="ex">${esc(excerpt)}</span>` : ""}</li>`;
  const sect = (name, items, first) => items.length ? `<div class="sect"${first ? ' style="margin-top:0"' : ""}>${name}</div><ul class="cites">${items.join("")}</ul>` : "";
  let cited, unused, held = [], defined = [];
  if (a.behaviour === "combined") {
    cited = a.citations.map(c => li(c.doc + (c.version ? " v" + c.version : ""), "· " + c.title, c.why, c.excerpt, c.n));
    unused = (a.not_cited || []).map(n => li(n.doc, n.title, n.why, n.excerpt));
    held = (a.withheld || []).map(w => li(w.doc, w.title, w.why));
  } else if (a.behaviour === "cite") {   // no shelf distinction in this arm: cited as search returned it, certified or not
    cited = (a.cited_docs || (a.cite || []).map(doc => ({doc}))).map(d => li(d.doc, d.title,
      d.shelf === "certified" ? "certified shelf" : d.shelf ? `${d.shelf} shelf: cited all the same, as this arm cannot tell` : "uncertified or not, this arm cannot tell"));
    unused = [];
  } else if (a.behaviour === "glossary_definition") {   // no document defines the term: the pack's glossary does; what was retrieved is listed, never cited
    defined = [`<li><code>${esc(a.term)}</code> the pack's glossary${a.steward ? ` <span class="muted">· steward ${esc(a.steward)}</span>` : ""}<span class="ex">${esc(a.definition)}</span></li>`];
    cited = [];
    unused = (a.references || []).map(r => li(r.doc, r.title, r.why, r.excerpt));
  } else if (a.citations) {   // the tier rule: gold the authority, silver attributed, bronze a reference, never cited
    cited = a.citations.map(c => li(c.doc + (c.version ? " v" + c.version : ""), "· " + c.title,
      citedAs(c) + (c.cited_as && c.why ? ` · ${c.why}` : ""), "", c.n));   // and why: the term's definition, or retrieved by the question
    unused = (a.superseded_not_cited || []).map(id => li(id, "", "not in force for the period asked"))
      .concat((a.references || []).map(r => li(r.doc, r.title, r.why, r.excerpt)));
    held = (a.not_ai_ready || []).concat(a.not_readable || []).map(x => li(x.doc, x.title, x.why));   // not AI-ready, or a unit's document this principal may not read
  } else {   // a definitional answer: certified sources cited, the rest shown and not used
    cited = (a.cite || []).map(id => li(id, "", "certified"));
    unused = (a.superseded_not_cited || []).map(id => li(id, "", "superseded"))
      .concat((a.uncertified_definition_not_cited || []).map(u => li(u.id, u.title, "uncertified definition")))
      .concat((a.uncertified_context_not_cited || []).map(u => li(u.id, u.title, "uncertified", u.excerpt)));
  }
  return sect("Defined by", defined, true) + sect("Cited", cited, !defined.length) + sect("Shown, not used", unused) + sect("Held back", held);
}
function showSources(a, n) {
  $("#tab-sources").innerHTML = renderSources(a);
  selectTab("sources");
  const li = n && $(`#tab-sources li[data-n="${n}"]`);
  if (li) { li.classList.add("hl"); li.scrollIntoView({block: "nearest", behavior: "smooth"}); }
}
function selectExchange(ex, n) {
  const a = EXCHANGES.get(ex.dataset.exchangeId); if (!a) return;
  renderDetail(a, ASKED, n);
}
listen($("#thread"), "click", e => {
  if (e.target.closest("#detail")) return;
  const copy = e.target.closest("button.copy");
  if (copy) { copyText(copy, copy.dataset.copy); return; }
  const routed = e.target.closest("a.queue-link");
  if (routed) { e.preventDefault(); showView("review"); highlightQueue(routed.dataset.doc); return; }
  const ex = e.target.closest(".exchange"); if (!ex) return;
  const marker = e.target.closest("a.cite");
  if (marker) e.preventDefault();
  selectExchange(ex, marker?.dataset.n);
});
listen($("#thread"), "keydown", e => {
  if (e.key !== "Enter" || e.target !== e.target.closest(".exchange")) return;
  e.preventDefault();
  selectExchange(e.target);
});
const HAS_SOURCES = new Set(["combined", "cite_certified", "cite", "glossary_definition"]);
// An answer rests on documents if it is one of these, or cites any: the reference agent binds each figure to one.
function hasSources(a) { return HAS_SOURCES.has(a.behaviour) || (a.citations || []).length > 0; }
function partsTable(a) {   // the planner's parts, or the reference agent's figures; an agent's, each with the source it is bound to
  const plan = a.plan || (a.figures || []).map(f => ({unit: f.unit, status: f.value == null ? "error" : "ok", value: f.value, reason: f.error || "no value",
    grain: f.grain, calendar: f.calendar, conversion: f.conversion}));   // what the agent's context said of the column: graph off, nothing
  const bound = a.agent ? Object.fromEntries((a.figures || []).map(f => [f.unit, f.cite])) : null;
  const source = (p) => bound ? `<td class="num">${bound[p.unit] ? citeLinks("[" + bound[p.unit] + "]") : ""}</td>` : "";
  return `<div class="scroll"><table class="parts"><tr><th>Unit</th><th class="num">Figure</th><th>Grain</th><th>Calendar</th>${bound ? `<th class="num">Source</th>` : ""}</tr>` +
    plan.map(p => `<tr><td>${esc(p.unit)}</td><td class="num">${p.status === "ok" ? money(p.value) : "<span class='muted'>" + esc(p.reason) + "</span>"}</td><td>${esc(p.grain||"")}</td><td>${esc(p.calendar||"")}${p.conversion && p.conversion !== "none" ? "<br><span class='muted'>" + esc(p.conversion) + "</span>" : ""}</td>${source(p)}</tr>`).join("") + `</table></div>`;
}
let ASKED = 0;   // the latest question: an earlier one that answers late never writes over the panel
// The thread sits under the examples: a new exchange, and then its answer, is scrolled into view, clear of the header
// where it stays on screen (a laptop's; a phone's scrolls away).
function reveal(el) {
  const bar = globalThis.document.querySelector("header.gb-bar");
  el.style.scrollMarginTop = (bar && globalThis.getComputedStyle(bar).position === "sticky" ? bar.offsetHeight + 12 : 12) + "px";
  el.scrollIntoView({block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth"});
}
const SKELETON = `<div class="msg skeleton" aria-busy="true"><span>Working out the answer…</span><div class="bone big"></div><div class="bone"></div><div class="bone w"></div></div>`;
async function ask() {
  const question = $("#q").value.trim(); if (!question || ASKING || !ME) return;
  const body = {question, purpose: $("#purpose").value, arm: $("#arm").value};
  // The reference agent's arms run on /agent; an arm /ask takes answers whole, figures and the documents behind them; the rest /answer.
  const path = AGENTS[body.arm] ? "/agent" : (PRESETS[body.arm]?.paths || []).includes("ask") ? "/ask" : "/answer";
  const display = !AGENTS[body.arm] && $("#rich").checked ? "?display=1" : "";
  const seq = ++ASKED;
  ASKING = busy($("#ask"), "Asking…");
  $("#examples").open = false;
  const ex = document.createElement("div");   // one exchange: the question, then its answer; the newest exchange first
  ex.className = "exchange";
  ex.dataset.exchangeId = String(seq);
  ex.tabIndex = 0;
  ex.setAttribute("aria-busy", "true");
  ex.innerHTML = `<div class="msg q"><span class="muted small">${esc(ME ? ME.id : "not signed in")} · ${esc(body.purpose)}</span><br>${esc(question)}</div>` + SKELETON;
  $("#thread").prepend(ex);
  reveal(ex);
  const a = await api(path + display, {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(body)});
  ASKING(); ASKING = null; askReady();
  if (!ex.isConnected) return;
  ex.querySelector(".skeleton")?.remove();
  ex.removeAttribute("aria-busy");
  if (a.error) {
    ex.insertAdjacentHTML("beforeend", `<div class="msg" tabindex="-1"><span class="badge bad">Not answered</span><span class="small">${esc(problem(a.error, a.status))}</span></div>`);
    if (seq === ASKED) { clearDetail(`No answer to the last question: ${problem(a.error, a.status)}`); reveal(ex); }
    return;
  }
  EXCHANGES.set(String(seq), a);
  ex.insertAdjacentHTML("beforeend", a.behaviour === "combined" ? renderCombined(a) : a.display?.table && !a.display.error ? renderRich(a) : renderAnswer(a));
  if (seq === ASKED) { renderDetail(a, seq); reveal(ex); if (!ex.contains(document.activeElement)) ex.querySelector(".msg:not(.q)")?.focus({preventScroll: true}); }
}
// After an error nothing from an earlier answer stays beside the thread.
const NARROW = matchMedia("(max-width:999px)");
function placeDetail() {
  const parent = NARROW.matches && SHOWN ? $('#thread .exchange[aria-current="true"]') : null;
  (parent || $("#view-ask aside")).append($("#detail"));
}
listen(NARROW, "change", placeDetail);
cleanups.push(() => NARROW.removeEventListener("change", placeDetail));
function clearDetail(why) {
  SHOWN = null;
  $("#detail h2").textContent = "Behind the answer";
  $("#about").textContent = "";
  document.querySelectorAll("#thread .exchange[aria-current]").forEach(ex => ex.removeAttribute("aria-current"));
  document.querySelectorAll("#thread .msg.shown").forEach(m => m.classList.remove("shown"));
  TABS.forEach(t => $("#tab-" + t).innerHTML = `<p class="muted small">${esc(why)}</p>`);
  placeDetail();
}
// A query, one clause a line, with a button that copies it.
function sqlPretty(sql) {
  return String(sql || "").replace(/\s+(FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|UNION ALL|(?:LEFT |RIGHT |INNER |FULL |CROSS )?JOIN)\s/g, "\n$1 ").replace(/\s+(AND|OR)\s(?![^(]*\))/g, "\n  $1 ");
}
const codeBlock = (sql) => `<div class="codeblock"><button class="copy" aria-label="Copy the query">Copy</button><pre>${esc(sqlPretty(sql))}</pre></div>`;
listen($("#tab-plan"), "click", e => { const b = e.target.closest("button.copy"); if (b) copyText(b, b.nextElementSibling.textContent); });
let SHOWN = null;   // the answer the panel describes, on every tab
// The panel for one answer: the latest, or the one whose citation marker was clicked (n, the marker, is highlighted).
async function renderDetail(a, seq, n) {
  SHOWN = a;
  $("#detail h2").innerHTML = `Behind “${esc(a.question)}”`;
  $("#about").innerHTML = `${esc(a.question)}<br>${a.agent ? "agent " : ""}${esc(armOf(a))}${a.agent ? " (" + esc(a.agent.mode) + ")" : ""} · asked as ${esc(a.principal)}`;
  document.querySelectorAll("#thread .exchange").forEach(ex => ex.setAttribute("aria-current", String(EXCHANGES.get(ex.dataset.exchangeId) === a)));
  document.querySelectorAll("#thread .msg[data-aid]").forEach(m => m.classList.toggle("shown", ANSWERS[m.dataset.aid] === a));
  if (hasSources(a)) showSources(a, n);
  else {
    $("#tab-sources").innerHTML = `<p class="muted small">This answer rests on no documents. A combined or definitional answer lists what it cites, what it did not use, and what the privacy gate held back.</p>`;
    if (!$("#tab-sources").hidden) selectTab("map");
  }
  const term = (a.trace?.[0]?.term);
  // Only an answer that ran, a figure, made the joins the context's map marks and crossed to another unit: a blocked,
  // refused or abstaining one made none, and the map says it did not run, and why, rather than show what it would have made.
  const ran = a.behaviour === "figure";
  let map = "";
  if (a.principal !== ME?.id) {   // /map answers for whoever is signed in, not for who asked
    map = `<p class="muted small">Asked as ${esc(a.principal)}; ${ME ? "signed in now as " + esc(ME.id) : "no one is signed in now"}. The map says what the principal signed in may read: ask again to see it.</p>`;
  } else if (REFUSED.has(a.behaviour)) {
    map = `<p class="muted small">No map for a refused answer: ${esc(a.reason)}</p>` + wayForward(a);
  } else if (term && term !== "__population__") {
    if (!MAPS.has(a)) MAPS.set(a, api(`/map/${encodeURIComponent(term)}`));
    const m = await MAPS.get(a);
    if (seq !== ASKED || SHOWN !== a) return;   // a newer question is on its way, or another answer has the panel
    // A term certified through its contexts maps to no column: the context the answer was built from is its map.
    const ctx = m.error ? null : (m.contexts || []).find(c => c.id === a.context?.context) || (m.contexts || [])[0];
    map = m.error ? `<p class="muted small">The map for ${esc(term)} could not be read: ${esc(problem(m.error, m.status))}</p>` :
      `<div class="small"><b>${esc(term)}</b> · defined by ${(m.defined_by||[]).map(d => `<code>${esc(d.id)}</code>${d.superseded_by ? " <span class='muted'>(superseded)</span>" : ""}`).join(", ") || `<span class='muted'>${ctx ? "no document; certified through its context" : a.behaviour === "glossary_definition" ? "no document; the pack's glossary defines it" : "no certified definition"}</span>`}</div>` +
      (ctx ? contextMap(ctx, ran ? a.context || {} : {...a.context, joins: [], across: []}) + (ran ? "" : `<div class="small muted" style="margin-top:6px">This answer did not run ` +
        `(${esc(plain(a.behaviour))}): it made no join and reached no other unit${a.denied_warehouses?.length ? `, since ${esc(a.principal)} may not read ${esc(a.denied_warehouses.join(", "))}` : ""}.</div>`) : "") +
      ((m.implementations || []).length ? `<div class="scroll"><table><tr><th>Unit</th><th>Column</th><th>Grain</th><th>Currency</th><th>Calendar</th><th>Conversion</th><th>Read</th></tr>` +
      m.implementations.map(i => `<tr><td>${esc(i.unit)}</td><td><code>${esc(i.tbl)}.${esc(i.col)}</code></td><td>${esc(i.grain)}</td><td>${esc(i.currency)}</td><td>${esc(i.calendar)}</td><td>${esc(i.conversion)}</td><td>${i.readable ? "yes" : "<span class='bad'>no</span>"}</td></tr>`).join("") + `</table></div>` : "") +
      ((m.no_join||[]).length ? `<div class="small muted" style="margin-top:8px">No join exists between: ${m.no_join.map(n => esc(n.pair)).join(", ")} <span class="muted">(asserted ${esc(m.no_join[0].asserted_by||"")}, ${esc(m.no_join[0].asserted_on||"")})</span></div>` : "") +
      (m.certified_joins||[]).map(c => `<div class="small muted">Joined only through a certified join: ${esc(c.pair)}, by ${esc(c.declared_join)} (${esc(c.note)}; asserted ${esc(c.asserted_by)}, ${esc(c.asserted_on)})</div>`).join("") +
      standardsMap(m.standards);
    const currency = ctx?.currency || (m.implementations || []).find(i => i.unit === a.unit && `${i.tbl}.${i.col}` === a.column)?.currency;
    if (currency) document.querySelectorAll("#thread .msg[data-aid]").forEach(card => { if (ANSWERS[card.dataset.aid] === a) card.querySelectorAll("[data-currency]").forEach(c => { c.textContent = currency; }); });
  } else {
    map = `<p class="muted small">${esc(a.reason || "No term resolved.")}</p>`;
  }
  $("#tab-map").innerHTML = map;
  const plan = (a.plan || a.parts || []);
  // On a model, or the mock, the reference agent's parts are {unit: value}, not the planner's list: each figure says what
  // ran, its unit, column and query, with the gate's verdict on that query's last try.
  const queried = (a.figures || []).filter(f => f.sql).map(f => { const t = (a.attempts || []).filter(x => x.unit === f.unit).pop();
    return {unit: f.unit, status: t ? `gate ${t.passed ? "passed" : "failed"}` : f.value == null ? "error" : "ok", notes: [f.column, f.error].filter(Boolean), sql: f.sql}; });
  const planRows = Array.isArray(plan) ? plan : queried;
  const gate = a.guard ? `<div class="small"><span class="badge ${a.guard.passed ? "ok" : "bad"}">gate ${a.guard.passed ? "passed" : "failed"}</span>${esc(a.guard.passed ? (a.guard.checks || []).join(" · ") : (a.guard.problems || []).join("; "))}</div>` : "";
  $("#tab-plan").innerHTML = a.sql ? `<div class="small muted">${esc(a.unit)}${a.sql_source ? " · SQL by " + esc(a.sql_source) : ""}</div>${gate}${codeBlock(a.sql)}` :
    planRows.length ? planRows.map(p => `<div class="small muted">${esc(p.unit)} · ${esc(p.status)}${p.notes?.length ? " · " + esc(p.notes.join("; ")) : ""}</div>` + (p.sql ? codeBlock(p.sql) : `<pre>${esc(p.reason || "")}</pre>`)).join("") :
    `<p class="muted small">No warehouse query was run.</p>`;
  const trace = a.run_trace || {};
  const steps = trace.stages || [];
  const modelled = steps.some(s => s.llm_calls || s.tokens_in || s.tokens_out);   // the model column only where a model ran
  const timeline = steps.length ? `<table><tr><th class="num">Step</th><th>What ran</th><th>Input</th>${modelled ? "<th>Output</th>" : ""}<th class="num">Time</th></tr>` + steps.map((s, i) =>
    `<tr><td class="num">${esc(i + 1)}</td><td>${esc(s.stage)}</td><td>${esc(i ? `the ${steps[i - 1].stage} stage's result` : `the question: ${a.question || ""}`)}</td>` +
    (modelled ? `<td>${esc(`${s.llm_calls ?? 0} model call${s.llm_calls === 1 ? "" : "s"}; ${s.tokens_in ?? 0} tokens in, ${s.tokens_out ?? 0} out`)}</td>` : "") +
    `<td class="num">${esc(s.ms == null ? "" : `${s.ms} ms`)}</td></tr>`).join("") + `</table>` + (modelled ? "" : `<p class="small muted">No model was called for this answer.</p>`) :
    `<p class="muted small">No timed stages were recorded for this answer.</p>`;
  const raw = JSON.stringify({run_trace: a.run_trace, trace: a.trace, versions: a.versions, retrieval: a.retrieval}, null, 2);
  $("#tab-trace").innerHTML = `${timeline}<details><summary>Raw JSON</summary><pre>${esc(raw)}</pre></details>`;
  placeDetail();
}
// The steward's queue: grouped by kind with counts, filterable; a row needing no action is greyed; Accept beside a quieter
// Reject; a candidate term's definition typed in its own row.
let QUEUE = [];
async function loadQueue() {
  if (!ME || !ME.processes.includes("certification")) { QUEUE = []; $("#queue-count").textContent = ""; $("#queue").innerHTML = "<span class='muted'>Sign in as a certification identity, a steward, to see the queue.</span>"; drawNextStep(); return; }
  const who = ME.id;
  const q = await api("/steward/queue");
  if (who !== ME?.id) return;
  if (q.error) { $("#queue").innerHTML = `<span class='muted'>${esc(problem(q.error, q.status))}</span>`; return; }
  QUEUE = q;
  const open = q.filter(t => t.upstream !== "rejected").length;
  $("#queue-count").textContent = open || "";
  const kinds = [...new Set(q.map(t => t.family))];
  const keep = $("#queue-filter").value;
  $("#queue-filter").innerHTML = `<option value="">Every kind (${open})</option>` + kinds.map(k => `<option value="${esc(k)}">${esc(plain(k, FAMILIES))} (${q.filter(t => t.family === k && t.upstream !== "rejected").length})</option>`).join("");
  $("#queue-filter").value = kinds.includes(keep) ? keep : "";
  drawQueue();
  drawNextStep();
}
function said(t) {
  const evidence = `<span class="muted">${esc(String(t.evidence || "").replace(/PROBABILISTIC/g, "probabilistic"))}</span>`;
  if (t.family === "suggested_mention") return `Possible mention of <b>${esc(t.value)}</b><br><span class="muted">${esc(t.definition || "")}</span><br>${esc(t.passage || "")}<br>${evidence}`;
  if (t.family === "candidate_term") return `Possible new term: <b>${esc(t.value)}</b>${String(t.evidence || "").replace(/:\s*$/, "") === t.value ? "" : ` · ${evidence}`}`;
  if (t.family === "privacy") return `Possible personal data: ${esc(({email:"an email address", date_of_birth:"a date of birth", phone:"a phone number"})[t.value.split(":")[1]] || t.value)} · ${evidence}`;
  if (t.family === "xref_match") return `Possible duplicate client match ${esc(t.value.split(":").pop())} between ${esc((t.units || []).join(" and "))} · ${evidence}`;
  return `${esc(t.value)} · ${evidence}`;
}
function drawQueue() {
  const q = QUEUE;
  if (!q.length) { $("#queue").innerHTML = "<span class='muted'>Nothing waiting.</span>"; return; }
  const only = $("#queue-filter").value;
  const kinds = [...new Set(q.map(t => t.family))].filter(k => !only || k === only);
  const row = (t) => {
    const settled = t.upstream === "rejected";
    return `<tr data-family="${esc(t.family)}" data-doc="${esc(t.doc)}"${settled ? ' class="settled"' : ""}><td>${said(t)}` +
      `${t.assessment ? `<br><span class="badge ${t.assessment === "likely_personal" ? "bad" : "warn"}">${esc(t.assessment.replace("_", " "))}</span>` : ""}` +
      `${t.upstream ? `<br><span class="badge ${settled ? "" : "warn"}">${esc(t.upstream)} upstream</span>${settled ? `<span class="small muted"> no action needed</span>` : ""}` : ""}` +
      `</td>` +
      `<td class="num">${Math.round(Number(t.confidence) * 100)}%</td><td><code>${esc(t.doc)}</code><br><span class="muted">${esc(t.shelf)}</span></td>` +
      `<td class="actions">${t.family === "candidate_term" ? `<input type="text" class="def" aria-label="Definition to record with ${esc(t.value)}" placeholder="Definition to record with the term">` : ""}` +
      `<div class="acts"><button class="${settled ? "" : "primary"}" data-tag="${esc(t.tag)}" data-d="accept" data-f="${esc(t.family)}">Accept</button><button class="quiet" data-tag="${esc(t.tag)}" data-d="reject" data-f="${esc(t.family)}">Reject</button></div>${t.reject_requested_by || t.second_check_required_to_reject ? `<span class="note">Reject needs a second steward${t.reject_requested_by ? "; requested by " + esc(t.reject_requested_by) : ""}</span>` : ""}</td></tr>`;
  };
  $("#queue").innerHTML = kinds.map(k => { const ts = q.filter(t => t.family === k);
    return `<h3>${esc(plain(k, FAMILIES))}<span class="group-count">${ts.filter(t => t.upstream !== "rejected").length}</span></h3><div class="scroll"><table class="stack"><tr><th>What was found</th><th class="num">${k === "suggested_mention" ? "Similarity" : "Confidence"}</th><th>Where</th><th><span class="vh">Decision</span></th></tr>` +
      ts.sort((x, y) => (x.upstream === "rejected") - (y.upstream === "rejected")).map(row).join("") + `</table></div>`; }).join("");
}
listen($("#queue-filter"), "change", drawQueue);
function highlightQueue(doc) {
  const r = [...document.querySelectorAll("#queue tr[data-doc]")].find(x => x.dataset.doc === doc);
  if (!r) return;
  document.querySelectorAll("#queue tr.hl").forEach(x => x.classList.remove("hl"));
  r.classList.add("hl");
  r.scrollIntoView({block: "center"});
  (r.querySelector("input.def") || r.querySelector("button"))?.focus({preventScroll: true});
}

let OB_DDL = "";
let OB_CSV = null;
let OB_QUESTIONS = undefined;
let OB_HELDOUT = undefined;
let OB_POLL = null;
let OB_STARTED = null;
let OB_TURN = Promise.resolve();   // reads of /onboard draw one after another, so an earlier one never draws over a later
let OB = null;
let OB_PREVIEW = 0;
let OB_UPDATE = false;
// An onboarding outcome, in its tone: done; a step that waits on something (rows, a check) to be put right; a missing
// choice on this page; or refused.
const TONES = {ok: ["ok", "done"], warn: ["warn", "needs attention"], info: ["info", "check"], bad: ["bad", "refused"]};
const obPlain = (text) => String(text ?? "").replace(/\s*\(ROWS=\w+\)/g, "");
function obMessage(text, tone = "ok") { OB_STARTED = null; const [cls, word] = TONES[tone] || TONES.ok; $("#ob-msg").innerHTML = `<span class="badge ${cls}">${word}</span>${esc(obPlain(text))}`; }
function clearObStarted(job) {
  if (!OB_STARTED || job?.id !== OB_STARTED.id || !["done", "failed"].includes(job.state)) return;
  if ($("#ob-msg").innerHTML === OB_STARTED.html) $("#ob-msg").textContent = "";
  OB_STARTED = null;
}
function obSource() { return {unit: $("#ob-unit").value.trim(), label: $("#ob-label").value.trim(), ddl: OB_DDL, rows: $("#ob-rows").value, csv: OB_CSV, ...(OB_QUESTIONS !== undefined ? {questions: OB_QUESTIONS} : {}), ...(OB_HELDOUT !== undefined ? {heldout: OB_HELDOUT} : {})}; }
async function readObFiles(input, output) {
  const files = [...input.files], csvs = files.filter(f => f.name.toLowerCase().endsWith(".csv"));
  const csv = csvs.length ? Object.fromEntries(await Promise.all(csvs.map(async f => [f.name, await f.text()]))) : null;
  const result = {csv}, labels = csvs.length ? [`${csvs.length} CSV files: ${csvs.map(f => f.name).join(", ")}`] : [];
  for (const [name, key, label] of [["questions.json", "questions", "team questions"], ["heldout.json", "heldout", "held-out questions"]]) {
    const file = files.find(f => f.name === name);
    if (!file) continue;
    result[key] = await file.text();
    try { const data = JSON.parse(result[key]); const items = Array.isArray(data) ? data : data.items; labels.push(`${name}: ${Array.isArray(items) ? items.length : "no"} ${label}`); }
    catch { labels.push(`${name}: invalid JSON`); }
  }
  output.textContent = labels.join(" · ");
  return result;
}
async function obCsv() {
  const data = await readObFiles($("#ob-csv"), $("#ob-files"));
  OB_CSV = data.csv; OB_QUESTIONS = data.questions; OB_HELDOUT = data.heldout;
}
async function previewObTables() {
  const turn = ++OB_PREVIEW;
  const file = $("#ob-file").files[0];
  OB_DDL = file ? await file.text() : "";
  await obCsv();
  if (turn !== OB_PREVIEW) return;
  if (!$("#ob-unit").value.trim()) { $("#ob-tables").textContent = "Name the unit (a short id) and its tables preview here."; return; }
  if (!OB_DDL) { $("#ob-tables").textContent = ""; return; }
  const body = obSource();
  const r = await api("/onboard/tables", {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body)});
  if (turn !== OB_PREVIEW) return;
  if (r.error) { $("#ob-tables").textContent = ""; obMessage(problem(r.error, r.status), "info"); return; }
  $("#ob-msg").textContent = "";
  renderObTables(r, body.rows);
}
for (const id of ["ob-file", "ob-csv", "ob-rows"]) listen($("#" + id), "change", previewObTables);
listen($("#ob-unit"), "input", previewObTables);
// Read DDL answers with its row source ({origin, count, label}); Create source answers with per-table counts only, so the
// row mode the request named labels it. Rows are called invented only when they were.
// A term's mappings to the preloaded industry standards: the concept, the SKOS relation (broadMatch: the standard's concept
// is the broader), the standard and its version, and the IRI as plain text, never a link the page follows; shortened on
// screen, whole in its tooltip. Folded under one line: the definition comes first.
function standardsMap(standards) {
  if (!(standards || []).length) return "";
  return `<details class="standards"><summary>Industry standards (${standards.length})</summary><table class="stack"><tr><th>Concept</th><th>Relation</th><th>Standard</th><th class="iri">IRI</th></tr>` +
    standards.map(s => `<tr><td>${esc(s.label)}<br><span class="muted">${esc(s.note || "")}</span></td><td>${esc(s.relation)}</td>` +
      `<td>${esc(s.standard_name)}<br><span class="muted">${esc(s.version)}</span></td><td class="iri" title="${esc(s.iri)}"><code>${esc(s.iri)}</code></td></tr>`).join("") + `</table></details>`;
}
// Each of the six sub-graphs' progress on a unit card: drafted items, accepted by the business team, certified by the steward
const SUB_GRAPHS = [["physical", "Physical"], ["glossary", "Glossary"], ["metrics", "Metrics"], ["ontology", "Industry ontology"],
  ["documents", "Documents"], ["governance", "Governance"]];
function obProgress(progress) {
  if (!progress) return "";
  return `<table class="progress" aria-label="Progress by sub-graph"><tr><th>Sub-graph</th><th class="num">Items</th><th class="num">Accepted</th><th class="num">Certified</th></tr>` +
    SUB_GRAPHS.map(([key, name]) => { const p = progress[key] || {items: 0, accepted: 0, certified: 0};
      return `<tr><td>${esc(name)}</td><td class="num">${esc(p.items)}</td><td class="num">${esc(p.accepted)}</td><td class="num">${esc(p.certified)}</td></tr>`; }).join("") + `</table>`;
}
function renderObTables(r, mode) {
  const origin = r.rows?.origin || mode;
  const count = r.rows?.count ?? (r.tables || []).reduce((n, t) => n + Number(t.rows || 0), 0);
  const label = r.rows?.label || {csv: "from CSV files", invented: "invented from the DDL (seed 7)", none: "none: drafting only"}[origin] || "";
  const kind = {csv: "rows from CSV", invented: "invented rows", none: "rows: none loaded"}[origin] || "rows";
  $("#ob-tables").innerHTML = `<div class="sect">Tables · ${esc(count)} rows, ${esc(label)}</div>` + (r.tables || []).map(t =>
    `<div><b>${esc(t.name)}</b> · ${esc(t.comment || "no table comment")} · ${esc(t.rows)} ${kind}</div><div class="scroll"><table><tr><th>Column</th><th>Type</th><th>Key</th><th>Comment</th></tr>` +
    (t.columns || []).map(c => `<tr><td><code>${esc(c.name)}</code></td><td>${esc(c.type)}</td><td>${esc(c.key || "")}</td><td>${esc(c.comment || "")}</td></tr>`).join("") + `</table></div>`).join("");
}
function renderObItems(items) {
  return items?.length ? items.map(i => `<div class="item"><b>${esc(i.id)}</b> · ${esc(i.title || i.detail?.question || i.kind)} ${obChange(i)}<span class="badge">${esc(i.state)}</span><br><span class="muted">${esc(i.from || "")}${i.made_by ? ` · drafted by ${esc(i.made_by === "model" ? `the model ${i.model || ""}`.trim() : "rules")}` : ""}</span>` +
    (i.accepted_by ? `<br>accepted by ${esc(i.accepted_name || i.accepted_by)}` : "") + (i.certified_by ? ` · certified by ${esc(i.certified_name || i.certified_by)}` : "") +
    (i.kind === "question" ? `<br><b>${money(i.detail.figure)}</b> · <code>${esc(i.detail.sql)}</code>` : "") +
    (i.kind === "mapping" ? `<br>${esc(i.detail.relation)} <code>${esc(i.detail.iri)}</code> · ${esc(i.detail.standard)} ${esc(i.detail.standard_version)}` : "") +
    (i.kind === "policy" ? `<br>restrict <code>${esc(i.detail.column)}</code> · ${esc(i.detail.family)} · ${esc(i.detail.relation)} <code>${esc(i.detail.iri)}</code>` : "") + `</div>`).join("") : "";
}
function renderObProposal(p) {
  $("#ob-proposal-card").hidden = !p && (!OB?.can?.improve || !OB?.units?.length);
  if (!p) { $("#ob-proposal").innerHTML = OB?.can?.improve ? `<p>No proposal yet. Improve: Run proposes changes from the questions the live units miss.</p>${obButton("improve", {}, (OB.units || []).every(u => u.live) || "plain")}${jobStrip(OB.job, "proposal")}` : ""; return; }
  const vFrom = p.version?.from || p.base_version, vTo = p.version?.to || p.next_version;
  const ver = (vFrom && vTo) ? ` · pack ${esc(vFrom)} → ${esc(vTo)}` : "";
  const items = (p.items || []).map(i => `<div><b>${esc(i.id)}</b> · add the alias <code>${esc(i.detail?.phrase || i.phrase || "")}</code> for ${esc(i.detail?.term_name || "")}${i.detail?.term_name && (i.detail?.term || i.term) ? " " : ""}<code>${esc(i.detail?.term || i.term || "")}</code> in <code>${esc(i.detail?.section || "terminology.aliases")}</code> <span class="badge">${esc(i.state)}</span><br><span class="muted">from ${esc(i.from || p.from || "")} · why: ${esc(i.detail?.why || "missing alias")} · evidence: ${esc(i.detail?.evidence_column || "")} (${esc(i.detail?.evidence || "")})${i.detail?.also_answers?.length ? ` · would also answer: ${esc(i.detail.also_answers.join("; "))}` : ""}</span>` +
    (i.accepted_by ? `<br>accepted by ${esc(i.accepted_name || i.accepted_by)}` : "") + (i.certified_by ? ` · certified by ${esc(i.certified_name || i.certified_by)}` : "") + `</div>`).join("");
  const lrows = p.learnable_table?.length ? p.learnable_table.map(r => [r.set, {before: r.before_passed, after: r.after_passed, of: r.of}]) : Object.entries(p.ab?.learnable?.sets || {});
  const hrows = p.heldout_table?.length ? p.heldout_table.map(r => [r.set, {before: r.before_passed, after: r.after_passed, of: r.of, fell: r.fell}]) : Object.entries(p.ab?.heldout?.sets || {});
  const lsets = lrows.map(([s, v]) => `<tr><td><code>${esc(s)}</code></td><td>learnable</td><td class="num">${v.before}/${v.of}</td><td class="num">${v.after}/${v.of}</td></tr>`).join("");
  const hsets = hrows.map(([s, v]) => `<tr><td><code>${esc(s)}</code>${(v.fell || v.after < v.before) ? ' <span class="badge warn">held-out fell</span>' : ""}</td><td>held-out (${esc(p.ab?.heldout?.note || "judge only, never read by the job")})</td><td class="num">${v.before}/${v.of}</td><td class="num">${v.after}/${v.of}</td></tr>`).join("");
  const fellWarn = (p.heldout_fell || hrows.some(([, v]) => v.fell || v.after < v.before)) ? ' <span class="badge warn">held-out fell</span>' : "";
  const ab = (lsets || hsets) ? `<div class="scroll"><table><tr><th>Set</th><th>Kind</th><th class="num">Before</th><th class="num">After</th></tr>${lsets}${hsets}</table></div>` : "";
  const rep = p.report ? `<div class="muted"><a href="${esc("/api/context" + p.report)}" target="_blank" rel="noopener">candidate A/B report</a></div>` : "";
  const next = proposalPlan(p), done = proposalDone(p);
  $("#ob-proposal").innerHTML = `<div class="unit-head"><h3>Improvement proposal${ver}</h3>${fellWarn}</div>` +
    `<ol class="stagebar five">${["Proposed", "Accepted", "Certified", "Checks", "Live"].map((name, i) => `<li class="${done[i] ? "done" : i === done.indexOf(false) ? "next" : ""}">${name}</li>`).join("")}</ol>` +
    nextBox(next, "proposal") + `<div class="items"><div class="sect">Items to review (${(p.items || []).length})</div>${items}</div>${ab}${rep}`;

}
function renderObNotes(notes, requests) {
  const rs = (requests || []).map(x => `<div><span class="badge warn">request to the platform team</span> <b>${esc(x.question_id || x.id || "")}</b> · ${esc(x.category || x.diagnosis || "")}: ${esc(x.summary || x.why || x.question || "")} <span class="muted">(${esc(x.proposer || "rules")})</span></div>`);
  const ns = (notes || []).map(x => `<div><span class="badge">note to the steward</span> <b>${esc(x.question_id || x.id || "")}</b> · ${esc(x.category || x.diagnosis || "")}: ${esc(x.summary || x.why || x.question || "")} <span class="muted">(${esc(x.proposer || "rules")})</span></div>`);
  $("#ob-notes").innerHTML = (rs.length || ns.length) ? `<div class="sect">Notes &amp; platform requests</div>` + rs.concat(ns).join("") : "";
}
function renderObChanges(record, descriptions = null) {
  if (!record) { $("#ob-changes").innerHTML = ""; return; }
  const value = v => v == null ? "none" : String(v);
  const columns = names => names?.length ? `(${names.join(", ")})` : "none";
  const foreign = key => `${columns(key.columns)} referencing ${key.ref_table} ${columns(key.ref_columns)}`;
  const lines = descriptions ? [...descriptions] : (record.lock?.changes || []).flatMap(c => {
    const table = `${c.source}.${c.table}`, column = `${table}.${c.column}`;
    if (c.change === "key-changed") {
      const before = c.was || {}, after = c.now || {}, result = [];
      if (columns(before.primary_key) !== columns(after.primary_key)) result.push(`Primary key of ${table} changed from ${columns(before.primary_key)} to ${columns(after.primary_key)}`);
      const old = (before.foreign_keys || []).map(foreign), next = (after.foreign_keys || []).map(foreign);
      old.filter(key => !next.includes(key)).forEach(key => result.push(`Foreign key of ${table} ${key} removed`));
      next.filter(key => !old.includes(key)).forEach(key => result.push(`Foreign key of ${table} ${key} added`));
      if (old.join("; ") !== next.join("; ") && old.every(key => next.includes(key)) && next.every(key => old.includes(key))) result.push(`Foreign keys of ${table} reordered from ${old.join("; ")} to ${next.join("; ")}`);
      return result;
    }
    const type = v => value(v && typeof v === "object" ? v.type : v);
    return ({
      "table-added": `Table ${table} added`, "table-removed": `Table ${table} removed`,
      "column-added": `Column ${column} added (${type(c.now)})`,
      "column-removed": `Column ${column} removed (was ${type(c.was)})`,
      "column-retyped": `Column ${column} retyped from ${value(c.was)} to ${value(c.now)}`,
      "column-described": `Description of column ${column} changed from ${value(c.was)} to ${value(c.now)}`,
      "column-moved": `Column ${column} moved from position ${value(c.was)} to ${value(c.now)}`
    })[c.change] || `Change to ${table}: ${c.change}`;
  });
  if (!lines.length) lines.push("No schema changes");
  if (descriptions === null) {
    for (const doc of record.estate?.unread_documents || []) lines.push(`${doc.path}: ${doc.evidence}`);
    for (const path of record.inputs?.ignored || []) lines.push(`Ignored: ${path}`);
    if (record.inputs?.suggestion) lines.push(`Declarations to add\n${record.inputs.suggestion}`);
    if (record.inputs?.upload) lines.push(`Upload ${record.inputs.upload.sha256.slice(0, 12)}`);
    if (record.rechecked) lines.push("Checked again: the last check did not pass");
  }
  const estate = record.estate;
  if (estate) {
    const why = {"reader-changed":"the document reader changed", "no-estate":"no estate had been built", "fixture-changed":"the fixture files changed", "unchanged":"the fixture files are unchanged", "no-fixture":"this pack has no fixture"}[estate.why] || estate.why;
    lines.push(`${estate.rebuilt ? "Estate rebuilt" : "Estate not rebuilt"}: ${why}`);
  }
  if (record.reloaded) lines.push("Reloaded the same model");
  $("#ob-changes").innerHTML = '<div class="sect">Candidate changes — review before sign-off</div><ul>' + lines.map(line => `<li>${line.startsWith("Declarations to add\n") ? `<pre>${esc(line)}</pre>` : esc(line)}</li>`).join("") + '</ul>';
}
function renderObVersions(versions, canRelease, canRollback) {
  const rollback = canRollback ? `<button id="ob-rollback"${OB?.job?.state === "running" ? " disabled" : ""}>Roll back to the previous release</button>` : "";
  if (!versions?.length) { $("#ob-versions").innerHTML = rollback; return; }
  const curHash = versions.find(x => x.current)?.model_hash;
  const sc = (s) => s && (s.total ?? s.of) != null ? `${s.passed}/${s.total ?? s.of}` : "not scored";
  $("#ob-versions").innerHTML = `<div class="sect">Versions</div>${rollback}<div class="scroll"><table><tr><th>Version</th><th>Released</th><th>Signed off</th><th class="num">Learnable</th><th class="num">Held-out</th><th>Note</th><th></th></tr>` +
    versions.map(v => `<tr><td><code>${esc(v.version || "")}</code> <span class="muted" title="${esc(v.model_hash || "")}">${esc(String(v.model_hash || "").slice(0, 12))}</span>${v.current ? ' <span class="badge ok">current</span>' : ""}${v.rollback_of ? ' <span class="badge warn">rollback</span>' : ""}</td><td>${esc((v.released_at || "").slice(0, 10))}</td><td>${esc(v.signed_off_by || "none")}</td><td class="num">${esc(sc(v.scores?.learnable))}</td><td class="num">${esc(sc(v.scores?.heldout))}</td><td>${esc(v.note || "")}</td><td>${!v.current && v.model_hash !== curHash && canRelease ? (v.kept_pack !== false ? `<button data-release-hash="${esc(v.model_hash)}">Release again</button>` : '<span class="muted">no kept pack</span>') : ""}</td></tr>`).join("") + `</table></div>`;
}
// The one onboarding stepper, read from the unit as the server holds it: the same on its card and for the next step.
const STAGES = ["Uploaded","Drafted","Accepted","Certified","Checks","Live"];
function obDone(unit) {
  const items = unit.items || [];
  const history = unit.history || [];
  // Each version is checked and released on its own: an update's stages start again from its upload
  const version = unit.update?.version || unit.version || 1;
  const passed = !unit.update && history.some(h => h.step === "onboard" && (h.version || 1) === version && h.detail?.passed === true);
  return [true, items.length > 0, items.length > 0 && items.every(i => i.accepted_by),
    items.length > 0 && items.every(i => i.certified_by), passed,
    !unit.update && !!unit.release?.model_hash && (unit.release.version || 1) === version && unit.live === true];
}
function obStages(unit) {
  const done = obDone(unit), next = done.indexOf(false);
  return STAGES.map((name, i) =>
    `<li class="${done[i] ? "done" : i === next ? "next" : ""}" aria-label="${esc(name)}: ${done[i] ? "complete" : i === next ? "next" : "not complete"}">${esc(name)}</li>`).join("");
}
const OB_LABELS = {create:"Create source", draft:"Draft", rows:"Add rows", accept:"Accept all", certify:"Certify all", run:"Run checks", sign_off:"Sign off", release:"Release", improve:"Improve: Run", update:"Update this unit"};
const OB_ROLES = {draft:"a knowledge engineer", create:"a knowledge engineer", rows:"a knowledge engineer", run:"a knowledge engineer", improve:"a knowledge engineer", accept:"the business team", certify:"a steward", sign_off:"a steward", release:"a steward"};
function actionPeople(action) {
  return PRINCIPALS.filter(p => {
    const does = name => (p.processes || []).includes(name);
    if (action === "accept") return does("accepting") || (p.writes_terms && !does("certification"));
    if (["certify", "sign_off", "release"].includes(action)) return does("certification");
    if (action === "improve") return does("improving");
    return does("drafting") || does("building");
  }).map(p => p.name || p.id);
}
function rowsMissing(u) { return !!u.needs || (u.requests || []).some(q => /\brows\b/i.test(q.ask || "")); }
function actionPlan(action, u = {}, at = -1, proposal = null) {
  const label = u.label || u.unit || "the proposal", count = (u.items || proposal?.items || []).length;
  const mine = !!OB?.can?.[action === "rows" ? "create" : action];
  const people = actionPeople(action), role = `${OB_ROLES[action] || "a knowledge engineer"}${people.length ? " (" + people.join(", ") + ")" : ""}`;
  const phrase = {draft:`draft ${label}'s items`, rows:`add ${label}'s rows, as CSV files or invented from the DDL`,
    accept:`accept ${count} drafted items`, certify:`certify ${count} accepted items`, run:"run the checks on the candidate",
    sign_off:"sign off the checked candidate", release:`release the signed-off candidate: ${label} goes live`, improve:"draft an improvement proposal"}[action];
  return {action, unit:u.unit, label, count, at, proposal:proposal?.id, mine, role, phrase};
}
function plan(u) {
  const at = obDone(u).indexOf(false);
  if (at < 0) return {at, unit:u.unit, label:u.label || u.unit, mine:false};
  let action = ["create", "draft", "accept", "certify", "run", "sign_off"][at];
  if (at > 1 && rowsMissing(u) && (OB?.can?.create || at === 4)) action = "rows";
  if (at === 5 && OB?.candidate?.signed_off) action = "release";
  return actionPlan(action, u, at);
}
function proposalDone(p) {
  const items = p.items || [];
  return [true, items.length > 0 && items.every(i => i.accepted_by), items.length > 0 && items.every(i => i.certified_by), !!OB?.candidate?.gate?.passed, false];
}
function proposalPlan(p) {
  const at = proposalDone(p).indexOf(false);
  const action = at === 1 ? "accept" : at === 2 ? "certify" : at === 3 ? "run" : OB?.candidate?.signed_off ? "release" : "sign_off";
  return actionPlan(action, {}, at, p);
}
function drawNextStep() {
  const strip = $("#next-step"), button = strip.querySelector("button");
  const steps = ME && OB ? [...(OB.units || []).map(plan), ...(OB.proposal ? [proposalPlan(OB.proposal)] : [])].filter(p => p.action) : [];
  const mine = steps.filter(p => !!OB.can[p.action === "rows" ? "create" : p.action]);
  $("#onboard-count").textContent = mine.length || "";
  const tags = ME?.processes?.includes("certification") ? QUEUE.filter(t => t.upstream !== "rejected").length : 0;
  strip.hidden = !ME || (!steps.length && !tags);
  if (strip.hidden) return;
  const chosen = mine.length ? mine : steps;
  strip.firstElementChild.classList.toggle("wait", !mine.length && !!steps.length);
  strip.querySelector(".lead").textContent = !mine.length && steps.length ? "Waiting on" : "Your next step ·";
  const label = p => {
    if (!p.mine) return `${p.role} to ${p.action === "accept" ? `accept ${p.label}'s ${p.count} drafted items` : p.phrase}.`;
    const phrase = p.action === "draft" ? `Draft ${p.label}` : p.action === "rows" ? `Add ${p.label}'s rows` : ["accept", "certify"].includes(p.action) ? `${p.phrase} for ${p.label}` : p.phrase;
    return phrase[0].toUpperCase() + phrase.slice(1) + ".";
  };
  $("#next-step-text").textContent = chosen.length ? chosen.map(label).join(" · ") : `${tags} tags wait for a decision in the Review queue.`;
  button.dataset.go = chosen.length ? "onboard" : "review";
  button.dataset.unit = chosen[0]?.unit || "";
  button.dataset.proposal = chosen[0]?.proposal || "";
}
listen($("#next-step"), "click", e => {
  const b = e.target.closest("button[data-go]"); if (!b) return;
  showView(b.dataset.go);
  const card = b.dataset.proposal ? $("#ob-proposal-card") : [...document.querySelectorAll("#ob-status .unit-card")].find(c => c.dataset.unit === b.dataset.unit);
  const target = card || $("#view-" + b.dataset.go);
  reveal(target);
  (target.querySelector("button.primary") || target.querySelector("summary") || $("#nav-" + b.dataset.go))?.focus({preventScroll:true});
});
function obButton(action, p = {}, primary = true) {
  return `<button class="${primary === "plain" ? "" : primary ? "primary" : "quiet"}" data-act="${action}"${p.unit ? ` data-unit="${esc(p.unit)}"` : ""}${p.proposal ? ` data-proposal="${esc(p.proposal)}"` : ""}${OB?.job?.state === "running" ? " disabled" : ""}>${OB_LABELS[action]}</button>`;
}
function rowsForm(unit) {
  return `<div class="rows-form"><span class="drop"><label class="lbl" for="rows-csv-${esc(unit)}">CSV files, one per table (questions.json and heldout.json too)</label>` +
    `<input id="rows-csv-${esc(unit)}" class="rows-csv" type="file" multiple accept=".csv,.json"><span class="field"><label for="rows-mode-${esc(unit)}">Rows</label>` +
    `<select id="rows-mode-${esc(unit)}" class="rows-mode">${$("#ob-rows").innerHTML}</select></span><div class="files"></div></span></div>`;
}
function jobHome(job) {
  if (!job) return "bar";
  if (job.step === "improve") return OB.proposal || OB.can.improve ? "proposal" : "bar";
  const units = OB.units || [];
  if (["release", "rollback"].includes(job.step) && job.state === "done") {
    const released = units.find(u => u.release?.model_hash === job.hash);
    return released ? `unit:${released.unit}` : "bar";
  }
  const home = units.find(u => [4, 5].includes(plan(u).at));
  return home ? `unit:${home.unit}` : OB.proposal ? "proposal" : "bar";
}
function jobStrip(job, where) {
  if (!job || jobHome(job) !== where) return "";
  const ms = Math.max(0, (Date.parse(job.finished_at) || Date.now()) - (Date.parse(job.started_at) || Date.now()));
  const seconds = Math.floor(ms / 1000), took = seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  const name = {checks:"Checks", "sign-off":"Sign-off", release:"Release", rollback:"Rollback", improve:"Improvement"}[job.step] || job.step;
  const last = job.progress?.at(-1), steps = last ? ` · ${last.n} of ${last.of} steps` : "";
  const live = (OB.units || []).filter(u => u.release?.model_hash === job.hash).map(u => u.label || u.unit).join(", ") || "the candidate";
  let text = `${name} running · ${took} elapsed · started by ${job.by}`;
  if (job.state === "done") text = {checks:`Checks passed${steps}`, "sign-off":`Signed off by ${job.by}`, release:`Released: ${live} is live`, improve:`Proposal drafted${steps}`}[job.step] || `${name} done`;
  else if (job.state !== "running") text = `${name} failed: ${job.message || job.state}`;
  if (job.state !== "running" && seconds >= 1) text += ` · took ${took}`;
  return `<div class="job" id="ob-job" data-job-id="${esc(job.id)}" data-state="${esc(job.state)}" role="status" aria-live="polite">${job.state === "running" ? `<progress aria-label="${esc(name)} running"></progress>` : `<span class="badge ${job.state === "done" ? "ok" : "bad"}">${job.state === "done" ? "done" : "failed"}</span>`}${esc(text)}` +
    (job.gate?.lines?.length ? `<div id="ob-gate" class="lines">${job.gate.lines.map(esc).join("<br>")}</div>` : "") +
    (job.hash || job.report ? `<div>${job.hash ? `candidate <code title="${esc(job.hash)}">${esc(job.hash.slice(0, 12))}</code>` : ""}${job.report ? `${job.hash ? " · " : ""}<a href="${esc("/api/context" + job.report)}" target="_blank" rel="noopener">report</a>` : ""}</div>` : "") + `</div>`;
}
function nextBox(p, where) {
  const text = p.at < 0 && !p.action ? `${p.label} is live.${OB.can.improve ? " Improve: Run, below, proposes changes from the questions it misses." : ""}` : p.mine ? `Next: ${p.phrase}.` : `Waiting on ${p.role} to ${p.phrase}.`;
  return `<div class="next${p.action && !p.mine ? " wait" : ""}"><div class="cta">${esc(text)}${p.mine && p.action !== "rows" ? obButton(p.action, p) : ""}</div>` +
    (p.action === "rows" && p.mine ? rowsForm(p.unit) + `<div class="cta">${obButton("rows", p)}</div>` : "") + jobStrip(OB.job, where) + `</div>`;
}
// A pending update's what-changed list: each item it adds, changes or proposes to remove, with what changed in it
function obChanges(update) {
  const changes = update?.changes;
  if (!changes) return "";
  const rows = ["changed", "added", "removed"].flatMap(kind => (changes[kind] || []).map(id =>
    `<li><span class="badge">${esc(kind)}</span> <b>${esc(id)}</b>: ${esc((changes.details?.[id] || []).join("; "))}</li>`));
  return `<div class="what-changed"><b>What changed in v${esc(update.version)}</b>${rows.length ? `<ul class="requests">${rows.join("")}</ul>` : `<p class="muted">Nothing to review: only rows changed.</p>`}</div>`;
}
function obChange(item) {
  return item.change ? `<span class="badge">${esc(item.change.kind)}</span> ` : "";
}
function obHistory(history) {
  return (history || []).map(h => `<li><b>${esc(h.step)}</b> · ${esc(h.by)} · ${esc(h.at)} · v${esc(h.version)}<br><span class="muted">${esc(JSON.stringify(h.detail ?? {}))}</span></li>`).join("") || "<li>No history yet.</li>";
}
function obDocuments(documents) {
  return (documents || []).map(d => {
    const path = d.path === "m365" ? "certified by M365" : d.path === "graded" ? "graded here" : "not used";
    return `<li><b>${esc(d.title)}</b> · ${esc(path)}${d.tier ? ` · ${esc(d.tier)} tier` : ""}</li>`;
  }).join("") || "<li>No reference documents supplied.</li>";
}
function renderObUnits(units) {
  return (units || []).map(u => {
    const next = plan(u), more = [];
    if (OB.can.run && next.action !== "run" && u.items?.length) more.push(obButton("run", next, false));
    if (OB.can.create && u.live) more.push(obButton("update", next, false));
    if (OB.can.create && rowsMissing(u) && next.action !== "rows") more.push(obButton("rows", next, false));
    return `<article class="unit-card" data-unit="${esc(u.unit)}"><div class="unit-head"><h3>${esc(u.label || u.unit)}</h3><span class="badge">${esc(next.at < 0 ? "Live" : STAGES[Math.max(0, next.at - 1)])}</span></div>` +
      `<ol class="stagebar" aria-label="Onboarding stages for ${esc(u.label || u.unit)}">${obStages(u)}</ol><p class="status">${esc(obPlain(u.status || ""))}</p>` + nextBox(next, `unit:${u.unit}`) +
      `<p class="muted small">Golden questions · ${esc(u.golden?.client || 0)} from client · ${esc(u.golden?.drafted || 0)} drafted</p>` +
      `<details><summary>Progress by sub-graph</summary><div class="scroll">${obProgress(u.progress)}</div></details>` + obChanges(u.update) +
      `<div><b>Open requests</b>${u.requests?.length ? `<ul class="requests">${u.requests.map(q => `<li>${esc(obPlain(q.ask))} <span class="muted">${esc(q.source)}</span></li>`).join("")}</ul>` : `<p class="muted">None.</p>`}</div>` +
      `<details class="items"${next.mine && ["accept", "certify"].includes(next.action) ? " open" : ""}><summary>Items to review (${(u.items || []).length})</summary>${renderObItems(u.items)}</details>` +
      (more.length ? `<details class="more"><summary>More actions</summary><div class="row">${more.join("")}</div></details>` : "") +
      `<details><summary aria-label="Show history for ${esc(u.label || u.unit)}">History</summary><ol>${obHistory(u.history)}</ol></details>` +
      `<details><summary aria-label="Show documents for ${esc(u.label || u.unit)}">Documents</summary><ul>${obDocuments(u.documents)}</ul></details></article>`;
  }).join("");
}
// One onboarding call: its button busy meanwhile; a refusal in its tone (a step waiting on something is not refused).
async function obPost(path, body, button, done = "Done.") {
  const who = ME?.id;
  const ready = button ? busy(button, "Working…") : () => {};
  const r = await api(path, {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(body)});
  ready();
  if (who !== ME?.id) return null;
  if (r.error) { obMessage(problem(r.error, r.status), r.status === 409 ? "warn" : "bad"); return null; }
  const said = path === "/onboard/run" ? ({checks:"Checks started.", "sign-off":"Sign-off started.", release:"Release started.", rollback:"Rollback started.", improve:"Improvement job started."}[body.step] || done) : r.said || done;
  obMessage(said, /has no rows|needs? (its )?rows/i.test(said) ? "warn" : "ok");
  if ((path === "/onboard/run" && r.job?.id) || (path === "/onboard/rollback" && r.job?.id)) OB_STARTED = {id: r.job.id, html: $("#ob-msg").innerHTML};
  return r;
}
async function loadOnboard() {
  const before = OB_TURN; let finished;
  const turn = new Promise(r => { finished = r; });
  OB_TURN = turn;
  $("#onboard").setAttribute("aria-busy", "true");
  await before;
  try { await drawOnboard(); } finally {
    finished();
    if (OB_TURN === turn) $("#onboard").removeAttribute("aria-busy");
  }
}
async function drawOnboard() {
  const allowed = canSee("onboard");
  $("#onboard").hidden = !allowed;
  if (!allowed) { OB = null; if (OB_POLL) { clearInterval(OB_POLL); OB_POLL = null; } drawNextStep(); return; }
  const who = ME?.id;
  const r = await api("/onboard");
  if (who !== ME?.id) return;
  if (r.error) { obMessage(problem(r.error, r.status), "bad"); return; }
  OB = r;
  clearObStarted(r.job);
  const oldCards = [...document.querySelectorAll("#ob-status .unit-card")];
  const forms = new Map(oldCards.map(c => [c.dataset.unit, c.querySelector(".rows-form")]));
  const opened = new Map(oldCards.map(c => [c.dataset.unit, [...c.querySelectorAll("details[open]")].map(d => d.querySelector("summary").textContent)]));
  const focus = document.activeElement, focused = focus?.closest("#ob-status .unit-card")?.dataset.unit;
  $("#ob-status").innerHTML = renderObUnits(r.units);
  document.querySelectorAll("#ob-status .unit-card").forEach(c => {
    const form = forms.get(c.dataset.unit), next = c.querySelector(".rows-form");
    if (form && next) next.replaceWith(form);
    c.querySelectorAll("details").forEach(d => { if (opened.get(c.dataset.unit)?.includes(d.querySelector("summary").textContent)) d.open = true; });
    if (focused === c.dataset.unit && !document.contains(focus)) [...c.querySelectorAll("button")].find(b => b.dataset.act === focus.dataset.act)?.focus({preventScroll:true});
  });
  $("#ob-work").hidden = !r.writable;
  if (!r.writable) $("#ob-msg").textContent = r.why;
  $("#ob-add").hidden = !r.can.create;
  if (!(r.units || []).length) $("#ob-add").open = true;
  renderObChanges(r.changes, r.change_lines);
  renderObProposal(r.proposal);
  renderObNotes(r.notes || [], r.requests || []);
  renderObVersions(r.versions || [], !!r.can?.release, !!r.can?.rollback);
  const post = r.eval_post;
  $("#ob-eval-post").hidden = !post;
  $("#ob-eval-post").className = post?.passed === false ? "badge bad" : "muted small";
  $("#ob-eval-post").innerHTML = post ? esc(post.message) + `<details><summary>Automatic release evaluation</summary><pre>${esc(JSON.stringify(post, null, 2))}</pre></details>` : "";
  $("#ob-eval-pre").innerHTML = r.eval_pre ? `<details><summary>Pre-release margin report · ${r.eval_pre.passed ? "passed" : "failed"}</summary><pre>${esc(r.eval_pre.report)}</pre></details>` : "";
  $("#ob-jobbar").innerHTML = jobStrip(r.job, "bar");
  $("#ob-create").disabled = r.job?.state === "running";
  if (r.job?.state === "running" && !OB_POLL) OB_POLL = setInterval(loadOnboard, 2000);
  if (r.job?.state !== "running" && OB_POLL) { clearInterval(OB_POLL); OB_POLL = null; }
  if (["release", "rollback"].includes(r.job?.step) && r.job.state === "done") { await loadProfile(); await loadSamples(); }
  $("#onboard").dataset.drawn = who || "";
  drawNextStep();
}
function updateUnit(unit) {
  OB_UPDATE = !!unit;
  $("#ob-add-title").textContent = unit ? `Update ${unit.label || unit.unit}` : "Add a unit";
  $("#ob-unit").readOnly = !!unit;
  $("#ob-unit").value = unit?.unit || "";
  $("#ob-label").value = unit?.label || "";
  $("#ob-cancel").hidden = !unit;
  $("#ob-file").value = ""; $("#ob-csv").value = "";
  OB_DDL = ""; OB_CSV = null; OB_QUESTIONS = undefined; OB_HELDOUT = undefined;
  ++OB_PREVIEW;
  $("#ob-tables").textContent = ""; $("#ob-files").textContent = "";
  $("#ob-add").open = !!unit;
  if (unit) { reveal($("#ob-add")); $("#ob-file").focus({preventScroll:true}); }
}
listen($("#ob-cancel"), "click", () => updateUnit(null));
listen($("#ob-create"), "click", async e => {
  const button = e.currentTarget;
  ++OB_PREVIEW;
  const file = $("#ob-file").files[0]; OB_DDL = file ? await file.text() : "";
  await obCsv(); const body = obSource();
  const r = await obPost("/onboard/source", body, button, "Source created.");
  if (r) { renderObTables(r, body.rows); $("#ob-add").open = false; if (OB_UPDATE) updateUnit(null); await loadOnboard(); }
});
for (const id of ["ob-status", "ob-proposal-card"]) listen($("#" + id), "click", async e => {
  const button = e.target.closest("button[data-act]"); if (!button) return;
  const action = button.dataset.act, unit = button.dataset.unit, proposal = button.dataset.proposal;
  if (action === "update") { updateUnit(OB.units.find(u => u.unit === unit)); return; }
  let path, body;
  if (action === "rows") {
    const card = button.closest(".unit-card");
    if (!card.querySelector(".rows-form")) { button.before(Object.assign(document.createElement("div"), {innerHTML:rowsForm(unit)})); return; }
    const files = await readObFiles(card.querySelector(".rows-csv"), card.querySelector(".rows-form .files"));
    path = "/onboard/rows"; body = {unit, rows:card.querySelector(".rows-mode").value, ...files};
  } else if (action === "draft") { path = "/onboard/draft"; body = {unit}; }
  else if (action === "accept" || action === "certify") { path = action === "accept" ? "/onboard/accept" : "/onboard/certify"; body = {...(proposal ? {proposal} : {unit}), items:"all"}; }
  else { path = "/onboard/run"; body = {step:({run:"checks", sign_off:"sign-off", release:"release", improve:"improve"})[action]}; }
  await obPost(path, body, button);
  await loadOnboard();
});
listen($("#ob-status"), "change", async e => {
  if (e.target.matches(".rows-csv")) await readObFiles(e.target, e.target.closest(".rows-form").querySelector(".files"));
});
listen($("#ob-versions"), "click", async e => {
  const rollback = e.target.closest("#ob-rollback");
  if (rollback) { const r = await obPost("/onboard/rollback", {}, rollback, "Rollback started."); if (r) await loadOnboard(); return; }
  const b = e.target.closest("button[data-release-hash]"); if (!b) return;
  const r = await obPost("/onboard/run", {step:"release", hash:b.dataset.releaseHash}, b, "Started.");
  if (r) loadOnboard();
});
listen($("#queue"), "click", async e => {
  const b = e.target.closest("button"); if (!b) return;
  const body = {tag: b.dataset.tag, decision: b.dataset.d};
  if (b.dataset.f === "candidate_term" && b.dataset.d === "accept") body.definition = b.closest("tr").querySelector("input.def")?.value.trim() || "";
  const ready = busy(b, b.dataset.d === "accept" ? "Accepting…" : "Rejecting…");
  const r = await api("/steward/decide", {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(body)});
  ready();
  // The outcome shows here, above the queue it changed.
  if (r.error) { $("#queuemsg").innerHTML = `<span class="badge bad">refused</span>${esc(problem(r.error, r.status))}`; return; }
  if (r.status === "pending_second_check") { $("#queuemsg").innerHTML = `<span class="badge warn">second check</span>${esc(r.reason)}`; loadQueue(); return; }
  $("#queuemsg").innerHTML = `<span class="badge ok">recorded</span>${esc(r.decision)} · human tag at 1.0 supersedes <code>${esc(r.superseded)}</code>${r.term_created ? " · term created: <b>" + esc(r.term_created) + "</b>" : ""}${r.embedded_now ? " · chunk now " + esc(r.readiness) + " and embedded for the first time" : ""}${r.note ? " · " + esc(r.note) : ""}`;
  loadQueue();
});
const KINDS = {within: "Within a unit", cross: "Across units", shelf: "Shelf", abstain: "Abstain"};
listen($("#runeval"), "click", async () => {
  if (!canEvaluate()) return;
  const ready = busy($("#runeval"), "Running…");
  $("#evalprogress").hidden = false;
  $("#evalnote").textContent = "Running the golden set on the three presets: this takes a minute.";
  const r = await api("/eval", {method:"POST"});
  ready();
  $("#evalprogress").hidden = true;
  if (r.error) { $("#evalnote").textContent = problem(r.error, r.status); return; }
  const kinds = ["within","cross","shelf","abstain"];
  // /eval scores as whoever is signed in: say who, and why a principal who cannot read every unit scores lower.
  const who = ME ? `Scored as ${esc(ME.id)} (${esc(ME.reads.length && ME.reads.length === UNITS.length ? "reads every unit" : "reads " + (ME.reads.join(", ") || "no unit"))}).` +
    (ME.reads.length < UNITS.length ? ` The golden answers assume a principal who reads every unit: a question about a unit ${esc(ME.id)} cannot read is refused, and the refusal counts as a miss.` : "") : "";
  $("#evalout").innerHTML = `<p class="small">${who}</p><div class="scroll"><table><tr><th>Arm</th>${kinds.map(k => `<th class="num">${KINDS[k]}</th>`).join("")}</tr>` +
    Object.entries(r).map(([arm, v]) => `<tr><td>${esc(arm)}</td>${kinds.map(k => `<td class="num">${v.by_kind[k].passed}/${v.by_kind[k].of}</td>`).join("")}</tr>`).join("") + `</table></div>` +
    `<p class="small muted">Schema: names, types and declared keys only. Catalog: plus glossary, descriptions and certified mappings. Graph: plus join paths, measure metadata, the no-join facts and the shelves. Within a unit the catalog arm is close; across units only the graph arm refuses the joins that do not exist.</p>`;
  $("#evalnote").textContent = "Done.";
});
// The A/B harness runs from the command line (make ab, with the API stopped); its latest report opens from here. How to
// run it is build detail, in About this build.
async function loadAB() {
  const r = await api("/ab");
  const last = (r.runs || [])[0];
  $("#ab").innerHTML = `<div class="sect">The A/B harness</div>` + (r.error ? `<span class="muted">${esc(problem(r.error, r.status))}</span>` : last
    ? `Latest run <code>${esc(last.run_id)}</code>, asked as ${esc(last.principal)}, ${esc(last.repeats)} repeat${last.repeats === 1 ? "" : "s"}: ` +
      last.arms.map(x => `${esc(x.arm)} (${esc(x.mode)})`).join(", ") + `. <a href="${esc("/api/context" + last.report)}" target="_blank" rel="noopener">Open its report</a>` +
      (last.arms.some(x => x.mode === "mock") ? ` <span class="muted">Mock arms prove the seam, not a model's quality.</span>` : "")
    : `<span class="muted">No A/B run yet. It runs from the command line; About this build, at the top, says how.</span>`);
  $("#about-ab").innerHTML = `<b>The A/B harness:</b> <code>make ab PROFILE=${esc(PROFILE_ID)} PRINCIPAL=&lt;one of its querying identities&gt;</code>, ` +
      `with the API stopped, runs every arm on the question sets this profile's pack names; its report opens in Quality` +
      (r.baseline ? `, beside its A/B baseline, <code>${esc(r.baseline)}</code>.` : `. This profile has no A/B baseline: a run is reported alone, and make margin has nothing to check it against.`);
}
showView("ask");
askReady();
loadVersion();
loadMode().then(loadProfile).then(loadPresets).then(refreshWho).then(loadSamples).then(loadAB);
setInterval(loadReleaseEvaluation, 30000);

return () => {
  abort.abort();
  timers.forEach(globalThis.clearInterval);
  timeouts.forEach(globalThis.clearTimeout);
  cleanups.forEach((cleanup) => cleanup());
};
}
