#!/usr/bin/env node
// The README's Demo script, driven end to end in a browser: each step's commands run as the presenter runs them, each
// click made on the page, and what the step says the audience sees checked there. Playwright for Node, Chromium.
//
// Run it from the demo folder with the API stopped: make demo-script, Meridian on PORT (8000) and Thornbury beside it on
// PORT2 (8001); PORT2=PORT runs the two one after the other, and HOST2 serves Thornbury on another loopback address;
// Fellmoor, the client onboarded from scratch, is served on PORT3 (8002; a page already serving that port is stopped
// first); HEADED=1 shows the browser. It is not part of make test: it builds whatever is missing, serves, compiles, loads,
// releases, rebuilds and restarts the demo, which takes minutes. It starts from a fresh steward queue and the pack as
// committed, and leaves both as it found them: the pack put back, the old hash released again, the decision set aside.
// Each command's output is kept in build/demo-script/; on a failure it stops, names the step and what it saw, and saves
// a screenshot of each page there.
'use strict';

const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const DEMO = path.resolve(__dirname, '..', '..');
const HOST = process.env.HOST || '127.0.0.1';
const PORT = Number(process.env.PORT || 8000);
const PORT2 = Number(process.env.PORT2 || 8001);
const HOST2 = process.env.HOST2 || HOST;
const PORT3 = Number(process.env.PORT3 || 8002);
const PORT4 = Number(process.env.PORT4 || PORT + 9);   // the discovery portal, alone, after the steps
const PY = process.env.PY || 'python3';
const HEADED = process.env.HEADED === '1';
const SCREENSHOTS = process.env.SCREENSHOTS === '1';
const OUT = path.join(DEMO, 'build', 'demo-script');
const SHOTS = path.join(DEMO, 'docs', 'img');
const BUILD_MS = 60 * 60 * 1000;   // a first make demo builds the estate, the graph and the pipeline: minutes, not an hour
const ANSWER_MS = 120 * 1000;
const SIDE_BY_SIDE = PORT2 !== PORT || HOST2 !== HOST;

let playwright;
try {
  playwright = require('playwright');
} catch (e) {
  console.error('make demo-script needs Playwright for Node: make demo-script installs it into build/node');
  process.exit(2);
}

// -- what is run, and what is checked ---------------------------------------------------------------------------------

let checked = 0;
class Failure extends Error {}
function check(ok, what, saw) {
  if (!ok) throw new Failure(saw === undefined ? what : `${what}; saw: ${typeof saw === 'string' ? saw : JSON.stringify(saw)}`);
  checked += 1;
  console.log(`     ok  ${what}`);
}
const say = (s) => console.log(`   ${s}`);
const flat = (s) => String(s || '').replace(/\s+/g, ' ').trim();
// The README's Quick start views (make screenshots): a 1280 x 800 frame of the page as the run reaches each, with what it
// shows scrolled into view and the caret and animations off, so the same data gives the same picture. The run itself
// keeps its own viewport.
async function quickstartShot(page, name, selector) {
  if (!SCREENSHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const viewport = page.viewportSize();
  await page.setViewportSize({ width: 1280, height: 800 });
  if (selector) await page.locator(selector).first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(SHOTS, `quickstart-${name}.png`), animations: 'disabled', caret: 'hide' });
  await page.setViewportSize(viewport);
}

// The make steps run as a presenter runs them, in a clean environment: no make of ours above them, and Meridian's profile
// unless a step names Thornbury's on its command line, as the README does (whatever .env says). PY and the
// like pass through.
function env(extra) {
  const e = { ...process.env };
  for (const k of ['MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES']) delete e[k];
  return { ...e, PROFILE: 'meridian', ...extra };
}
let logs = 0;
function logFile(label) {
  fs.mkdirSync(OUT, { recursive: true });
  return path.join(OUT, `${String(++logs).padStart(2, '0')}-${label.replace(/[^a-z0-9]+/gi, '-')}.log`);
}

// A command the presenter types, run to its end: its output is kept in build/demo-script/, and returned.
function run(cmd, args, extra = {}) {
  say(`$ ${cmd === 'sh' ? args[1] : [cmd, ...args].join(' ')}`);
  const r = spawnSync(cmd, args, { cwd: DEMO, env: env(extra), encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 });
  const out = (r.stdout || '') + (r.stderr || '');
  const log = logFile(cmd === 'sh' ? args[1].split(' ')[0] : [cmd, ...args].join(' '));
  fs.writeFileSync(log, out);
  if (r.status !== 0) throw new Failure(`${cmd} ${args.join(' ')} exited ${r.status}: see ${path.relative(DEMO, log)}\n${out.slice(-2000)}`);
  return out;
}
const make = (...args) => run('make', args);
// A make the step expects refused: it must fail (make exits 2), and its output is returned for the step to check.
function refused(...args) {
  say(`$ make ${args.join(' ')}   (refused)`);
  const r = spawnSync('make', args, { cwd: DEMO, env: env({}), encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  const out = (r.stdout || '') + (r.stderr || '');
  fs.writeFileSync(logFile(`make ${args.join(' ')}`), out);
  if (r.status !== 2) throw new Failure(`make ${args.join(' ')} exited ${r.status}, where the step expects it refused\n${out.slice(-2000)}`);
  return out;
}

async function answers(host, port) {
  try { return (await fetch(`http://${host}:${port}/profile`)).ok; } catch (e) { return false; }
}

// A server the presenter starts (make demo, make serve): in a process group of its own, so stopping it (Ctrl-C) stops
// make, uvicorn and anything else it started. It is up when its page answers.
const SERVERS = new Set();
async function serve(args, port, host = HOST, extra = {}) {
  if (await answers(host, port)) throw new Failure(`something already answers on ${host}:${port}: stop it, or name another port (PORT, PORT2)`);
  say(`$ make ${args.join(' ')}`);
  const log = logFile(`make ${args.join(' ')}`);
  const fd = fs.openSync(log, 'w');
  const proc = spawn('make', args, { cwd: DEMO, env: env(extra), detached: true, stdio: ['ignore', fd, fd] });
  fs.closeSync(fd);
  const server = { proc, host, port, log, exited: false };
  proc.on('exit', () => { server.exited = true; });
  SERVERS.add(server);
  const start = Date.now();
  let told = start;
  while (!(await answers(host, port))) {
    if (server.exited) throw new Failure(`make ${args.join(' ')} stopped before it served: see ${path.relative(DEMO, log)}\n${tail(log)}`);
    if (Date.now() - start > BUILD_MS) throw new Failure(`make ${args.join(' ')} did not serve within ${BUILD_MS / 60000} minutes`);
    if (Date.now() - told > 60000) { told = Date.now(); say(`  ... still building (${Math.round((told - start) / 60000)} min; ${path.relative(DEMO, log)})`); }
    await sleep(1000);
  }
  return server;
}
async function stop(server) {
  if (!server || server.exited) { SERVERS.delete(server); return; }
  say(`(Ctrl-C: the API on ${server.host}:${server.port} stopped)`);
  for (const [sig, wait] of [['SIGINT', 30000], ['SIGTERM', 10000], ['SIGKILL', 5000]]) {
    try { process.kill(-server.proc.pid, sig); } catch (e) { /* gone already */ }
    const until = Date.now() + wait;
    while (!server.exited && Date.now() < until) await sleep(200);
    if (server.exited) break;
  }
  while (await answers(server.host, server.port)) await sleep(200);
  SERVERS.delete(server);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const tail = (file) => { try { return fs.readFileSync(file, 'utf8').slice(-2000); } catch (e) { return ''; } };

// -- the page ---------------------------------------------------------------------------------------------------------

async function open(context, port, host = HOST) {
  const page = await context.newPage();
  await page.goto(`http://${host}:${port}/`);
  await page.waitForFunction(() => document.querySelector('#profile').textContent.trim() !== '');
  await page.waitForFunction(() => document.querySelectorAll('#principal option').length > 1 && document.querySelectorAll('#arm option').length > 0);
  await page.waitForFunction(() => document.querySelectorAll('#chips button').length > 0);
  return page;
}

// The shared bar and the theme (issue #163): the page drawn in the mode asked for, the bar navy in both, the brand the
// client's name beside its initial with no logo set, and the switcher KGE alone with no PORTAL_URL.
const PAPER = { light: 'rgb(244, 247, 250)', dark: 'rgb(11, 22, 36)' };
const BAR = 'rgb(14, 42, 71)';
async function look(page, mode, theme) {
  const seen = await page.evaluate(() => ({
    paper: getComputedStyle(document.body).backgroundColor,
    bar: getComputedStyle(document.querySelector('header')).backgroundColor,
    scheme: getComputedStyle(document.documentElement).colorScheme,
    chosen: document.documentElement.dataset.theme || 'system',
    stored: (() => { try { return localStorage.getItem('v3-theme'); } catch (e) { return 'unreadable'; } })(),
    button: document.querySelector('#theme').getAttribute('aria-label'),
    tile: !document.querySelector('#brand-tile').hidden && document.querySelector('#brand-tile').textContent,
    logo: !document.querySelector('#brand-logo').hidden,
    products: [...document.querySelectorAll('#products a')].filter((a) => !a.hidden).map((a) => `${a.textContent}${a.getAttribute('aria-current') ? '*' : ''}`),
  }));
  check(seen.paper === PAPER[mode] && seen.bar === BAR && seen.scheme === mode, `the page in ${mode} mode, the bar navy`, seen);
  check(seen.chosen === theme && seen.stored === (theme === 'system' ? null : theme) && seen.button === `Theme: ${theme[0].toUpperCase()}${theme.slice(1)}`,
    `the theme: ${theme}, remembered on this device as ${theme === 'system' ? 'nothing' : theme}`, seen);
  const title = flat(await page.locator('#title').textContent());
  const client = await page.evaluate(async () => { try { const r = await fetch('/profile'); return r.ok ? (await r.json()).title || '' : ''; } catch (e) { return ''; } });
  check(title === 'Context Layer' && seen.tile === (client.trim().charAt(0) || 'G').toUpperCase() && !seen.logo && JSON.stringify(seen.products) === '["Ask*"]',
    'the brand: Context Layer, with the client\'s initial and no logo set; the switcher: KGE alone, current', { ...seen, title, client });
}

// Picking a principal signs in, in demo mode.
async function signIn(page, who) {
  await page.locator('#account-menu').evaluate(d => { d.open = true; });
  await page.selectOption('#principal', who);
  await page.waitForFunction((w) => {
    const t = document.querySelector('#who').textContent;
    return t.startsWith(`Signed in as ${w} `) && t.endsWith('demo mode');
  }, who);
}

// The page's workspaces (Ask, Review queue, Onboard, Quality, Tools): the one a step uses is selected first, as a person
// would pick its tab.
async function view(page, name) {
  const tab = page.locator(`#nav-${name}`);
  if ((await tab.getAttribute('aria-selected')) !== 'true') await tab.click();
  await page.locator(`#view-${name}`).waitFor({ state: 'visible' });
}
const views = (page) => page.locator('#views [role="tab"]').evaluateAll((ts) => ts.filter((t) => !t.hidden).map((t) => t.dataset.view));
const viewNow = (page) => page.locator('#views [aria-selected="true"]').getAttribute('data-view');
// The answer settings and the examples fold away; a step that uses them opens them.
async function unfold(page, id) {
  await page.locator(`#${id}`).evaluate((d) => { d.open = true; });
}

async function waitForOnboard(page, button, scope = '#onboard') {
  await view(page, 'onboard');
  await page.locator('#onboard').waitFor({ state: 'visible' });
  await page.waitForFunction(() => document.querySelector('#onboard').dataset.drawn === document.querySelector('#principal').value);
  if (button === 'Create source') await unfold(page, 'ob-add');
  await page.locator(scope).getByRole('button', { name: button, exact: true }).waitFor({ state: 'visible' });
}

// A button pressed and its POST awaited; returns the job a POST /onboard/run started (its id is what the wait follows),
// or null. Nothing polls GET /onboard while the POST is still in flight.
async function onboardPress(page, button, endpoint, expect, label) {
  const response = page.waitForResponse((r) => r.url().endsWith(endpoint) && r.request().method() === 'POST');
  await button.click();
  const got = await response;
  await got.finished();
  await page.waitForFunction(() => !document.querySelector('#onboard button[aria-busy="true"]'));
  await page.evaluate(() => OB_TURN);
  if (expect === null ? !got.ok() : got.status() !== expect)
    throw new Failure(`${label} returned ${got.status()}${expect === null ? '' : `, where the step expects ${expect}`}: ${await got.text()}`);
  if (expect !== null) return null;
  const body = await got.json().catch(() => null);
  return body?.job ?? null;
}

async function onboardClick(page, button, endpoint, expect = null, scope = '#onboard') {
  return onboardPress(page, page.locator(scope).getByRole('button', { name: button, exact: true }), endpoint, expect, button);
}

// The job a press started, followed by its id to its end within the build's deadline: an earlier job of the same step,
// still the last one on record, is never taken for it.
async function waitForOnboardJob(page, started, step, state) {
  if (!started?.id || started.step !== step)
    throw new Failure(`${step} started no job: ${JSON.stringify(started)}`);
  const deadline = Date.now() + BUILD_MS;
  let job;
  while (Date.now() < deadline) {
    job = await page.evaluate(async () => (await (await fetch('/onboard')).json()).job);
    if (job?.id === started.id && job.state !== 'running') break;
    await sleep(500);
  }
  if (job?.id !== started.id || job.state === 'running')
    throw new Failure(`timed out waiting for ${step} (${started.id}): ${JSON.stringify(job)}`);
  if (job.state !== state)
    throw new Failure(`${step} ended ${job.state}, wanted ${state}: ${job.message || job.log_tail || ''}`);
  await page.evaluate(() => loadOnboard());
}

// A question asked, by a click on its sample question (from the whole list if it is not among the first) or typed into
// the box; returns what its card shows, once the panel beside it describes it.
async function ask(page, question, { sample = false } = {}) {
  await view(page, 'ask');
  const before = await page.locator('#thread .exchange').count();
  const trace = await page.locator('#tab-trace').innerHTML();
  if (sample) {
    await unfold(page, 'examples');
    const chip = () => page.locator(`#chips button[data-q="${question.replace(/["\\]/g, '\\$&')}"]`);
    if (!(await chip().count())) await page.click('#chips button.more');
    if ((await chip().count()) !== 1) throw new Failure(`"${question}" is not a sample question on the page`);
    await chip().click();
  } else {
    await page.fill('#q', question);
    await page.click('#ask');
  }
  await page.waitForFunction((n) => document.querySelectorAll('#thread .exchange').length > n, before);
  const card = page.locator('#thread .exchange').first().locator('.msg:not(.q):not(.skeleton)');
  await card.waitFor({ timeout: ANSWER_MS });
  await page.waitForFunction((t) => document.querySelector('#tab-trace').innerHTML !== t, trace, { timeout: ANSWER_MS });
  // What the card shows, its source details opened; what it did, by its code, and the plain words its badge says.
  await card.evaluate((c) => c.querySelectorAll('details').forEach((d) => { d.open = true; }));
  const text = flat(await card.innerText());
  const behaviour = (await card.getAttribute('data-behaviour')) || '';
  const label = flat(await card.locator('.badge').first().textContent());
  const fig = (await card.locator('.fig').count()) ? flat(await card.locator('.fig').first().innerText()) : null;
  // The reason the card gives, its first line of small print, without the badge that labels it.
  const reason = flat(await card.evaluate((c) => {
    const small = c.querySelector('.small');
    if (!small) return '';
    const copy = small.cloneNode(true);
    copy.querySelectorAll('.badge').forEach((b) => b.remove());
    return copy.textContent;
  }));
  const parts = await card.locator('table.parts tr').evaluateAll((rows) => rows.slice(1).map((r) => [...r.cells].map((c) => c.textContent.trim())));
  return { text, behaviour, label, fig, parts, reason, question };
}

async function tab(page, name) {
  await view(page, 'ask');
  await page.click(`.tabs button[data-tab="${name}"]`);
  await page.locator(`#tab-${name}`).evaluate((t) => t.querySelectorAll('details').forEach((d) => { d.open = true; }));   // what folds away, opened
  return flat(await page.locator(`#tab-${name}`).innerText());
}
async function runTrace(page) {
  await view(page, 'ask');
  await page.click('.tabs button[data-tab="trace"]');
  return JSON.parse(await page.locator('#tab-trace details pre').textContent()).run_trace;   // the raw JSON, behind its toggle
}
const header = async (page) => flat(await page.locator('#profile').textContent());   // in About this build, folded

function figure(a, value, what) {
  check(a.fig === value, `${what || `"${a.question}"`}: ${value}`, a.fig === null ? `${a.behaviour}: ${a.text}` : a.fig);
}
async function noFigure(a, what) {
  check(!a.fig && !a.parts.some((p) => /\d/.test(p[1] || '')), `${what || `"${a.question}"`}: no figure (${a.behaviour})`, a.text);
}

// -- the profile's paths, as make reads them --------------------------------------------------------------------------

function profile(id) {
  const r = spawnSync(PY, ['-m', 'kge.pipeline.profile', '--make', '--profile', id], { cwd: DEMO, env: env({}), encoding: 'utf8' });
  if (r.status !== 0) throw new Failure(`${PY} -m kge.pipeline.profile --profile ${id}: ${r.stderr || r.stdout}`);
  return Object.fromEntries(r.stdout.trim().split(/\s+/).map((kv) => kv.split(/=(.*)/s).slice(0, 2)));
}

// -- the steps --------------------------------------------------------------------------------------------------------

const PACK = path.join(DEMO, 'clients', 'meridian', 'pack.yaml');
const ALIAS_AT = '    - {phrase: revenue, term: revenue}\n';
const ALIAS = '    - {phrase: takings, term: revenue}\n';
const TAKINGS = 'Total retail takings for June 2025';
const DES = 'What was the Digital Engagement Score for June 2025?';
const XB3 = 'NNA in FY26-Q1 from clients who also bank with Retail';
const MASKED = String.raw`grep -rhoE '[a-z.*]+@example\.com' build/landing/meridian/current/documents | sort -u`;
// The Wealth samples graph on does not answer as the set expects (the README names each, and what the page gives).
const NO_FIGURE = {
  W08: 'How many accounts were excluded from NNA at 31 Mar 2025?',
  W11: 'How many accounts did household 5001 hold at 31 Dec 2024?',
  XB4: "Of FY26-Q1 advisory inflows, how much came from the client's own retail accounts?",
  XB5: 'Retail customers with average balance above $8,000 in June 2025: how many are covered by a wealth advisor, by advisor office?',
  XB1: 'How many clients hold both retail deposits and advisory AUM at 31 Mar 2025?',
};
// XB2 asks for the dual clients' deposits beside their AUM, and its share: the page gives their AUM alone, one figure.
const ONE_FIGURE = {
  XB2: 'For dual clients, deposits vs AUM and AUM share of total, 31 Mar 2025',
};

const state = { browser: null, context: null, context2: null, context3: null, meridian: null, thornbury: null, fellmoor: null, page: null, pack: null,
  released: null, oldHash: null, decided: false, onboarding: null, newClient: false };

async function buildAndServe() {
  const before = profile('meridian');
  const journal = path.join(DEMO, before.store, 'overlay', 'decisions.jsonl');
  check(!fs.existsSync(journal) || fs.readFileSync(journal, 'utf8').trim() === '',
    'a fresh steward queue to start from (else: make reset-overlay graph pipeline, with the API stopped)', journal);
  check(!fs.readFileSync(PACK, 'utf8').includes(ALIAS), 'the pack as committed: no takings alias in it');
  state.meridian = await serve(['demo', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  const p = profile('meridian');
  const release = path.basename(fs.realpathSync(path.join(DEMO, p.landed)));
  const manifest = JSON.parse(fs.readFileSync(path.join(DEMO, p.landed, 'manifest.json'), 'utf8'));
  state.page = await open(state.context, PORT);
  await look(state.page, 'light', 'system');
  await quickstartShot(state.page, 'sign-in');
  const h = await header(state.page);
  check(h.startsWith('Profile meridian, client meridian,'), 'the header names the profile, meridian', h);
  check(h.includes(`release ${release} as of ${manifest.as_of}`), `the header names the landed release, ${release}, as of ${manifest.as_of}`, h);
  check(h.endsWith(`artifact ${p.current.slice(0, 12)}`), 'the header names the artifact current points at', h);
  state.oldHash = p.current;
  // What landed of the documents' e-mail addresses, beside what the raw documents hold (the landing zone's own step).
  const landed = run('sh', ['-c', MASKED]).trim().split('\n');
  const raw = run('sh', ['-c', String.raw`grep -rhoE '[a-z.]+@example\.com' build/docs | sort -u`]).trim().split('\n');
  check(landed.filter((a) => !a.includes('*')).join() === 'records@example.com', 'the role mailbox, and nothing else, landed in clear: records@example.com', landed);
  const personal = raw.filter((a) => a !== 'records@example.com');
  const redact = (a) => a[0] + '*'.repeat(a.indexOf('@') - 1) + a.slice(a.indexOf('@'));
  check(personal.length > 0 && personal.every((a) => landed.includes(redact(a))),
    `each customer's address in the raw documents (${personal.length}) landed masked, as m********@example.com and the like`, { raw, landed });
}

async function wealthQuestions() {
  const page = state.page;
  const splitter = page.locator('#chat-splitter');
  const assistant = page.locator('#assistant');
  const beforeResize = (await assistant.boundingBox()).width;
  const handle = await splitter.boundingBox();
  await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle.x + handle.width / 2 + 200, handle.y + handle.height / 2);
  await page.mouse.up();
  const afterResize = (await assistant.boundingBox()).width;
  check(afterResize > beforeResize, 'dragging the chat edge right makes the chat column wider', {beforeResize, afterResize});
  await splitter.focus();
  await page.keyboard.press('Home');
  check(await splitter.getAttribute('aria-valuenow') === await splitter.getAttribute('aria-valuemin'),
    'Home moves the chat column to its minimum', await splitter.getAttribute('aria-valuenow'));
  // Signed out: only the Ask workspace, and Ask says to sign in rather than run and fail.
  check(JSON.stringify(await views(page)) === '["ask"]', 'signed out, the page shows the Ask workspace alone', await views(page));
  const askButton = await page.locator('#ask').evaluate((b) => ({ disabled: b.disabled, text: b.textContent }));
  check(askButton.disabled && askButton.text === 'Sign in to ask', 'signed out, Ask is disabled and says: Sign in to ask', askButton);
  const sets = await page.locator('#chips .set').evaluateAll((ss) => ss.map((s) => [s.dataset.set, s.textContent]));
  check(sets.some(([s]) => /^golden v\d/.test(s)) && sets.some(([s]) => /^golden_wealth v1/.test(s)), 'the sample questions are the golden set\'s and golden_wealth_v1\'s', sets);
  check(sets.every(([, shown]) => !/_|\bv\d/.test(shown)) && sets.some(([, shown]) => shown === 'Wealth questions'),
    'each set is named in plain words (Wealth questions), its id and version kept for a tooltip', sets);
  const first = await page.locator('#chips button[data-q]').evaluateAll((bs) => bs.map((b) => b.dataset.q));
  const misses = Object.values(NO_FIGURE).concat(Object.values(ONE_FIGURE)).filter((q) => first.includes(q));
  check(misses.length === 0, 'none of the first few is one graph on does not answer as the set expects', misses);
  const more = page.locator('#chips button.more');
  check(flat(await more.innerText()) === 'All 78 sample questions', 'All 78 sample questions shows the rest', await more.innerText());
  await more.click();
  check(await page.locator('#chips button[data-q]').count() === 78, 'all 78 shown', await page.locator('#chips button[data-q]').count());
  // Those graph on does not answer as the set expects are marked known misses, from the profile's A/B baseline.
  const known = Object.values(NO_FIGURE).concat(Object.values(ONE_FIGURE));
  const marked = await page.locator('#chips button.miss').evaluateAll((bs) => bs.map((b) => [b.dataset.q, b.querySelector('.tag')?.textContent]));
  check(marked.length === known.length && known.every((q) => marked.some(([m, tag]) => m === q && tag === 'known miss')),
    `each of the ${known.length} graph on does not answer as the set expects, and no other, is marked known miss`, marked);
  const note = flat(await page.locator('#chips .note').innerText());
  const noteTip = await page.locator('#chips .note').getAttribute('title');
  check(/^Known miss: the graph arm does not answer it as the set expects/.test(note) && noteTip.includes("The README's Demo script, step 2"),
    'a note says what a known miss is, and its tooltip that the README says what each gives instead', { note, noteTip });
  await signIn(page, 'analyst_enterprise');
  check(await page.inputValue('#arm') === 'graph', 'on the graph arm', await page.inputValue('#arm'));
  // An analyst lands on Ask, and sees no steward queue and no onboarding: only what the role can use.
  check(JSON.stringify(await views(page)) === '["ask","quality","tools"]' && (await viewNow(page)) === 'ask',
    'an analyst sees Ask, Quality and Tools, and lands on Ask: no Review queue, no Onboard', await views(page));
  check(flat(await page.locator('#settings-now').textContent()) === '· graph arm, analysis' && !(await page.locator('#settings').getAttribute('open')),
    'the answer settings are folded away, saying they are on the graph arm, for analysis', await page.locator('#settings-now').textContent());
  // While a question is on its way, Ask is busy and a placeholder answer stands in, marked busy.
  await page.fill('#q', 'NNA for March 2025 as reported');
  const busy = await page.evaluate(() => {
    document.querySelector('#ask').click();
    const b = document.querySelector('#ask');
    return { disabled: b.disabled, text: b.textContent, skeleton: !!document.querySelector('#thread .exchange .skeleton[aria-busy="true"]') };
  });
  check(busy.disabled && busy.text === 'Asking…' && busy.skeleton, 'while it asks, Ask is disabled and says Asking…, and a busy placeholder stands in for the answer', busy);
  await page.locator('#thread .exchange').first().locator('.msg[data-behaviour]').waitFor({ timeout: ANSWER_MS });
  await page.waitForFunction(() => document.querySelector('#tab-trace').innerHTML.includes('What ran'), null, { timeout: ANSWER_MS });
  check(await page.locator('#ask').evaluate((b) => !b.disabled && b.textContent === 'Ask' && !document.querySelector('.skeleton')), 'and once answered, Ask is back');
  const reported = await ask(page, 'NNA for March 2025 as reported', { sample: true });
  const folded = flat(await page.locator('#chips button.more').textContent());   // the examples fold away once asked
  check(folded === 'All 78 sample questions' && !(await page.locator('#examples').getAttribute('open')), 'picking one folds the list back, and the examples away', folded);
  await page.waitForFunction(() => {
    const r = document.querySelector('#thread .exchange').getBoundingClientRect();
    return r.top >= 0 && r.top < window.innerHeight;
  });
  check(true, 'and the page scrolls to the question and its answer');
  figure(reported, '5,868,354.54');
  check(/as reported/.test(reported.text), 'answered as reported', reported.text);
  check(reported.label === 'Answer' && reported.behaviour === 'figure', 'its badge says Answer, in plain words, not figure', reported);
  const shown = page.locator('#thread .exchange').first();
  const labelled = { label: flat(await shown.locator('.figlabel').innerText()), currency: flat(await shown.locator('[data-currency]').textContent()) };
  check(labelled.label === 'Net New Assets · March 2025 · as reported' && labelled.currency === 'USD',
    'the figure is labelled with its metric, period and basis, Net New Assets · March 2025 · as reported, and its currency, USD', labelled);
  const badges = await page.locator('#thread .badge').allTextContents();
  check(badges.every((b) => !/_/.test(b)), 'no badge on the thread shows a code', badges);
  // The tabs behind the answer are ARIA tabs: arrow keys move between them, one tab stop.
  await page.focus('#detail .tabs [data-tab="map"]');
  await page.keyboard.press('ArrowRight');
  const moved = await page.evaluate(() => ({ focus: document.activeElement.dataset.tab, selected: document.querySelector('#detail [aria-selected="true"]').dataset.tab,
    stops: [...document.querySelectorAll('#detail [role="tab"]')].filter((t) => t.tabIndex === 0).length, shown: !document.querySelector('#tab-plan').hidden }));
  check(moved.focus === 'plan' && moved.selected === 'plan' && moved.stops === 1 && moved.shown, 'ArrowRight moves from Map to Plan and shows it; one tab stop', moved);
  await page.keyboard.press('End');
  check(await page.evaluate(() => document.activeElement.getAttribute('aria-selected') === 'true' && document.activeElement.dataset.tab === 'trace'), 'End moves to Trace');
  await page.keyboard.press('Home');
  check(await page.evaluate(() => document.activeElement.dataset.tab === 'sources' && !document.querySelector('#tab-sources').hidden), 'Home moves to Sources');
  await quickstartShot(page, 'ask', '#thread .exchange');
  const timeline = await page.locator('#tab-trace table tr').evaluateAll((rows) => rows.map((r) => r.cells[1]?.textContent.trim()));
  check(timeline[0] === 'What ran' && ['access', 'gate', 'execute'].every((s) => timeline.includes(s)),
    'the Trace tab is a timeline of the stages that ran, the raw JSON behind a toggle', timeline);
  const sqlCheck = async (query) => {
    await view(page, 'tools');
    await page.fill('#sql-query', query);
    await page.click('#check-sql');
    await page.waitForFunction((q) => document.querySelector('#sql-result').dataset.query === q, query, { timeout: ANSWER_MS });
    return flat(await page.locator('#sql-result').innerText());
  };
  const allowed = await sqlCheck('SELECT count(*) FROM monthly_revenue');
  check(allowed.startsWith('allowed'), 'the SQL check box allows a read of a table the analyst may read, without running it', allowed);
  const email = await sqlCheck('SELECT email FROM customer');
  check(email.startsWith('refused') && email.includes('restricted personal data'), 'and refuses a restricted column', email);
  const unknown = await sqlCheck('SELECT count(*) FROM no_such_table');
  check(unknown.startsWith('refused') && unknown.includes('Not available: no_such_table'), 'and says a table it does not know is not available, as one it may not read is', unknown);
  const restated = await ask(page, 'NNA for March 2025 restated', { sample: true });
  figure(restated, '5,920,237.28');
  const restatedSql = await tab(page, 'plan');
  const latest = await ask(page, 'NNA for the latest month', { sample: true });
  figure(latest, '6,069,905.64');
  check(/June 2025/.test(latest.text), 'the latest month released: June 2025', latest.text);
  const exchanges = page.locator('#thread .exchange');
  const latestSql = await tab(page, 'plan');
  const heading = () => page.locator('#detail h2').textContent();   // the heading as written: the page shows it in capitals
  const current = () => exchanges.evaluateAll((xs) => xs.map((x) => x.getAttribute('aria-current')));
  await exchanges.nth(1).click();
  const selected = await current();
  check(selected[1] === 'true' && selected.filter((m) => m === 'true').length === 1, 'the earlier exchange is the one marked current', selected);
  check((await heading()).includes('NNA for March 2025 restated') && (await tab(page, 'plan')) === restatedSql,
    'an earlier exchange restores its question and answer details', await page.locator('#detail').innerText());
  await exchanges.first().focus();
  await page.keyboard.press('Enter');
  check((await current())[0] === 'true', 'Enter on the latest exchange marks it current', await current());
  check((await heading()).includes('NNA for the latest month') && (await tab(page, 'plan')) === latestSql,
    'the latest exchange restores its question and answer details', await page.locator('#detail').innerText());
  const july = await ask(page, 'NNA for July 2025', { sample: true });
  await noFigure(july);
  check(/not yet released/.test(july.text) && july.text.includes('2025-08-08'), 'July 2025 is not yet released: its figures are released on 2025-08-08', july.text);
  figure(await ask(page, 'NNA from BROKERAGE in FY26-Q1', { sample: true }), '16,910,957.72');
  // No document defines net new assets: the pack's glossary does, and the answer says so and cites nothing it retrieved.
  const nna = await ask(page, 'What is NNA?');
  check(nna.behaviour === 'glossary_definition' && nna.text.includes("as the pack's glossary defines it") && nna.text.includes('Money clients bring to the firm'),
    '"What is NNA?": net new assets as the pack\'s glossary defines it, labelled so', nna.text);
  check(nna.text.includes('No document defines it, so none is cited') && !nna.text.includes('Cites:'), 'no document defines it: nothing is cited', nna.text);
  const listed = await tab(page, 'sources');
  const sects = await page.locator('#tab-sources .sect').allTextContents();   // the headings as written: the page shows them in capitals
  check(sects.join(' | ') === 'Defined by | Shown, not used' && listed.includes("the pack's glossary"),
    "the Sources tab: the pack's glossary defines it, and what the question retrieved is shown, not used", sects.join(' | ') + ': ' + listed);
  for (const [id, q] of Object.entries(NO_FIGURE)) {
    const a = await ask(page, q, { sample: true });
    await noFigure(a, `${id}, "${q}"`);
    check(a.reason.length > 20 && (id !== 'W08' || /no condition on/.test(a.reason)), `${id} says why: ${a.reason}`, a.text);
  }
  for (const [id, q] of Object.entries(ONE_FIGURE)) {
    const a = await ask(page, q, { sample: true });
    check(a.behaviour === 'figure' && /^\d[\d,]*\.\d\d$/.test(a.fig || '') && !a.parts.length,
      `${id}: one figure, the dual clients' assets under management, not their deposits beside it and its share`, a.text);
  }
}

async function optionB() {
  const page = state.page;
  const xb3 = await ask(page, XB3, { sample: true });
  figure(xb3, '4,985,720.27', `as analyst_enterprise, "${XB3}"`);
  check(xb3.text.includes('certified client cross-reference'), 'through the certified client cross-reference', xb3.text);
  check((await tab(page, 'map')).includes('through the certified join'), 'the Map tab names the certified join it crossed');
  await quickstartShot(page, 'map', '#tab-map');
  await signIn(page, 'analyst_retail');
  const blocked = await ask(page, XB3);
  check(blocked.behaviour === 'blocked_access', `as analyst_retail: blocked: ${blocked.reason}`, blocked);
  await noFigure(blocked, 'as analyst_retail');
  check(await tab(page, 'plan') === 'No warehouse query was run.', 'no warehouse queried', await tab(page, 'plan'));
  await tab(page, 'map');
  const map = await page.locator('#tab-map').evaluate((m) => ({ joined: [...m.querySelectorAll('.badge')].filter((b) => b.textContent.trim() === 'joined').length,
    tables: m.querySelectorAll('table').length, rows: [...m.querySelectorAll('th')].map((t) => t.textContent.trim()), text: m.textContent.replace(/\s+/g, ' ').trim() }));
  check(map.tables === 0 && map.joined === 0 && !map.rows.includes('Across') && map.text.includes('No map for a refused answer') && map.text.includes('needs a unit you may not read') && !/wealth/i.test(map.text),
    'the Map tab offers a way forward with no map for a refused answer, and names no unit the server withholds', map);
  await signIn(page, 'analyst_enterprise');
  const emails = await ask(page, 'Which retail customers are also wealth households, and what are their emails?');
  check(emails.behaviour === 'refuse' && /restricted personal data/i.test(emails.text), `their emails: refused: ${emails.reason}`, emails.text);
  await noFigure(emails);
  const contacts = await ask(page, 'Which corporate counterparty contacts are also wealth households?');
  check(contacts.behaviour === 'refuse' && /no join path/i.test(contacts.text), `corporate counterparty contacts: refused: ${contacts.reason}`, contacts.text);
  await noFigure(contacts);
  // Two answers worded from the graph's own facts (issue #40): a key-name join on the column the pack declares not joinable,
  // refused on the units' no-join fact, and a currency no certified measure is in, which abstains saying which are
  const g024 = await ask(page, 'Join retail customers to corporate contacts on customer_id and count the matches', { sample: true });
  check(g024.behaviour === 'refuse' && g024.text.includes('No certified join path between Retail Banking and Corporate & Institutional')
    && g024.text.includes('CRM contact number'), `G024: refused on the no-join fact: ${g024.reason}`, g024.text);
  await noFigure(g024);
  const g042 = await ask(page, 'Total bank revenue in GBP for Q2 2025', { sample: true });
  check(/^abstain/.test(g042.behaviour) && g042.text.includes('certified figures are in EUR and USD, and the only conversion is EUR to USD'),
    `G042: abstains, worded from the certified measures: ${g042.reason}`, g042.text);
  await noFigure(g042);
}

async function packEdit() {
  let page = state.page;
  const old = state.oldHash;
  state.pack = fs.readFileSync(PACK, 'utf8');
  check(state.pack.includes(ALIAS_AT), 'the pack holds the revenue alias the takings phrase goes under');
  say(`(clients/meridian/pack.yaml: ${ALIAS.trim()} added under ${ALIAS_AT.trim()})`);
  fs.writeFileSync(PACK, state.pack.replace(ALIAS_AT, ALIAS_AT + ALIAS));
  // Onboarding's step 4, the structural checks, beside the API: no findings, and the hash the pack now compiles to
  const checks = make('check');
  const next = (checks.match(/: no findings; its model hash is ([0-9a-f]{64}),/) || [])[1];
  check(next && next !== old, 'make check finds nothing and names the new hash', checks.slice(-600));
  const loaded = make('compile', 'load');
  const fresh = (loaded.match(/^release: ([0-9a-f]{64}) LOADED/m) || [])[1];
  check(fresh && fresh === next, 'make compile load marks the new hash LOADED: a candidate', loaded.slice(-600));
  check(loaded.includes(`make margin HASH=${fresh} checks it, make sign-off PRINCIPAL=<steward> HASH=${fresh} signs it off`),
    'and names the onboarding gate as its next step', loaded.slice(-600));
  const still = await ask(page, TAKINGS);
  check(/^abstain/.test(still.behaviour), `with the API serving, "${TAKINGS}" still abstains`, still);
  check((await runTrace(page)).graph.artifact === old, 'the Trace tab\'s graph.artifact is the old hash');
  // Onboarding's steps 7 and 8 (issue #40): not released until it is checked and signed off
  const early = refused('release');
  check(early.includes(`${fresh} is not through the onboarding gate: not checked since it was loaded, not signed off`) && /current is as it was/.test(early),
    'make release is refused: not checked, not signed off', early);
  // The candidate checked beside the API against the gates its own pack declares
  const gate = make('margin', `HASH=${fresh}`, 'REPEATS=1');
  for (const kind of ['Within a unit', 'Across units', 'Shelf', 'Abstain']) {
    check(new RegExp(`^  V01 golden v2, ${kind}: graph on passed (\\d+) of \\1 \\(100%\\), at least 100%: passed$`, 'm').test(gate),
      `V01, ${kind}: every golden question passes on graph on`, gate.slice(-3000));
  }
  for (const kind of ['Across units', 'Shelf']) {
    check(gate.includes(`  V02 golden v2, ${kind}: graph on less graph off, +100 pts, at least +50 pts: passed`),
      `V02, ${kind}: graph on is 100 points ahead of graph off, where the pack asks for 50`, gate.slice(-3000));
  }
  check(/^  V03 heldout v1, held-out: graph on passed .*: reported, never gated$/m.test(gate), 'V03: the held-out set is reported', gate.slice(-3000));
  check(gate.includes('  V04 every set, held-out included: no graph-on answer cites an uncertified or off-tier document: passed'),
    'V04: no graph-on answer cites an off-tier document', gate.slice(-3000));
  check(gate.includes('passed: no margin fell by more than its spread across repeats'), 'no margin fell beside the baseline', gate.slice(-3000));
  const marker = (gate.match(/^GATE: (\S+): passed, run /m) || [])[1];
  const verdict = marker ? Object.values(JSON.parse(fs.readFileSync(marker, 'utf8'))) : [];
  check(marker && marker.includes(fresh) && verdict.length === 1 && verdict[0].passed === true && verdict[0].model_hash === fresh,
    'passed, and the candidate\'s GATE says so', verdict);
  // The steward's sign-off: only a role that may perform certification
  const analyst = refused('sign-off', 'PRINCIPAL=analyst_retail');
  check(analyst.includes('analyst_retail may not perform certification') && /Nothing was signed off/.test(analyst),
    'make sign-off PRINCIPAL=analyst_retail is refused: an analyst\'s role may not perform certification', analyst);
  const signed = make('sign-off', 'PRINCIPAL=steward_glossary');
  const signoff = (signed.match(/^sign-off: .*?: (\S+SIGNOFF)\. /m) || [])[1];
  const entry = signoff ? Object.values(JSON.parse(fs.readFileSync(signoff, 'utf8'))) : [];
  check(signed.includes(`${fresh} signed off by steward_glossary`) && entry.length === 1 && entry[0].principal === 'steward_glossary'
    && entry[0].run_id === verdict[0].run_id, 'make sign-off PRINCIPAL=steward_glossary signs it off, on the check that passed: SIGNOFF beside GATE', entry);
  const swapped = make('release');
  check(swapped.includes(`current is now ${fresh}`) && swapped.includes('signed off by steward_glossary'), 'make release swaps current to it, signed off', swapped);
  state.released = true;
  await stop(state.meridian);
  state.meridian = await serve(['serve', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  await page.close();
  page = state.page = await open(state.context, PORT);
  await signIn(page, 'analyst_enterprise');
  const revenue = await ask(page, TAKINGS);
  figure(revenue, '13,287.67', `after make release and a restart, "${TAKINGS}" is answered as revenue`);
  const now = (await runTrace(page)).graph.artifact;
  check(now !== old && (await header(page)).endsWith(`artifact ${now.slice(0, 12)}`), 'the new hash is served', now);
  fs.writeFileSync(PACK, state.pack);
  say('(clients/meridian/pack.yaml put back as committed: git checkout clients/meridian/pack.yaml)');
  state.pack = null;
  const back = make('release', `HASH=${old}`);
  check(back.includes(`current is now ${old}`) && back.includes('released before, so no new sign-off'),
    'make release HASH=<the old hash>: released before, so no new sign-off', back);
  await stop(state.meridian);
  state.meridian = await serve(['serve', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  state.released = null;
  await page.close();
  page = state.page = await open(state.context, PORT);
  await signIn(page, 'analyst_enterprise');
  const again = await ask(page, TAKINGS);
  check(/^abstain/.test(again.behaviour) && (await runTrace(page)).graph.artifact === old, 'the old hash is served again: the takings question abstains', again);
}

// A one-line edit that loses golden questions: the post-load check fails, naming them, and the candidate stays one
const MAPPING = '  - {column: retail.monthly_revenue.total_revenue_usd, term: revenue, certified: true,';
const REVENUE = 'Total retail revenue for June 2025';
async function failedCheck() {
  const page = state.page;
  const old = state.oldHash;
  state.pack = fs.readFileSync(PACK, 'utf8');
  const first = (state.pack.split('\nmappings:\n')[1] || '').split('\n', 1)[0];
  check(first.startsWith(MAPPING), 'the first mapping is the certified one of retail.monthly_revenue.total_revenue_usd', first);
  say('(clients/meridian/pack.yaml: certified: false on the first mapping, retail.monthly_revenue.total_revenue_usd)');
  fs.writeFileSync(PACK, state.pack.replace(MAPPING, MAPPING.replace('certified: true,', 'certified: false,')));
  const loaded = make('compile', 'load');
  const fresh = (loaded.match(/^release: ([0-9a-f]{64}) LOADED/m) || [])[1];
  check(fresh && fresh !== old, 'make compile load marks the new hash LOADED: a candidate', loaded.slice(-600));
  const gate = refused('margin', `HASH=${fresh}`, 'REPEATS=1');
  const failed = (gate.split("\nFAILED: these post-load checks against the pack's gates:\n")[1] || '').split('\n\n', 1)[0];
  const ids = [...failed.matchAll(/^  V01 golden v2, .*: failed, graph on failed (.+)$/gm)].flatMap((m) => m[1].split(', '));
  const golden = JSON.parse(fs.readFileSync(path.join(DEMO, profile('meridian').estate, 'golden.json'), 'utf8')).items;
  const missed = ids.map((id) => (golden.find((i) => i.id === id) || {}).question);
  check(ids.length === 5 && missed.includes(REVENUE), `make margin fails: graph on now misses five golden questions, each named (${ids.join(', ')}), "${REVENUE}" among them`,
    gate.slice(-3000));
  const marker = (gate.match(/^GATE: (\S+): failed, run /m) || [])[1];
  const verdict = marker ? Object.values(JSON.parse(fs.readFileSync(marker, 'utf8'))) : [];
  check(marker && marker.includes(fresh) && verdict.length === 1 && verdict[0].passed === false, 'its GATE says failed', verdict);
  for (const args of [['sign-off', 'PRINCIPAL=steward_glossary'], ['release']]) {
    const no = refused(...args);
    check(no.includes(`${fresh} is not through the onboarding gate: its post-load check failed`) && no.includes('so it stays a candidate'),
      `make ${args.join(' ')} is refused: its check failed, so it stays a candidate`, no.slice(-800));
  }
  const still = await ask(page, REVENUE);
  figure(still, '13,287.67', `on the page, "${REVENUE}", one of the five, is still`);
  check((await runTrace(page)).graph.artifact === old, 'the old hash is still served', (await runTrace(page)).graph.artifact);
  fs.writeFileSync(PACK, state.pack);
  say('(clients/meridian/pack.yaml put back as committed)');
  state.pack = null;
}

// AUM, added to Meridian's pack later by a pack edit alone (pack 1.3.0), released through onboarding's steps 4 to 8: the
// plan's Phase 3 onboarding test. The page answers it; no platform file names it.
const W16 = 'AUM for household 5001 at 31 Mar 2025';
const W17 = 'AUM plus AUA for household 5001 at 31 Mar 2025';
const AUM_GREP = 'grep -rliw aum --exclude-dir=estate kge ui meta-model.yaml Makefile';
async function aum() {
  const page = state.page;
  await signIn(page, 'analyst_enterprise');
  check(await page.inputValue('#arm') === 'graph', 'as analyst_enterprise, on the graph arm', await page.inputValue('#arm'));
  await view(page, 'ask');
  await unfold(page, 'examples');
  const chip = (q) => page.locator(`#chips button[data-q="${q}"]`);
  if (!(await chip(W16).count()) || !(await chip(W17).count())) await page.click('#chips button.more');   // all the samples
  for (const [id, q] of [['W16', W16], ['W17', W17]]) {
    const cls = (await chip(q).count()) === 1 ? (await chip(q).getAttribute('class')) || '' : null;
    check(cls !== null && !cls.split(/\s+/).includes('miss'), `${id}'s sample question is not marked known miss`, cls);
  }
  const w16 = await ask(page, W16, { sample: true });
  figure(w16, '7,641,422.24', `W16, "${W16}"`);
  check(w16.text.includes('context aum'), 'answered from the context aum', w16.text);
  const map = await tab(page, 'map');
  check(map.includes('Resolves to the context aum') && map.includes('wealth.assets-under-management over wealth.AUM_MNTH_END_FACT_1'),
    'the Map tab names the AUM measure and context', map);
  const w17 = await ask(page, W17, { sample: true });
  check(w17.behaviour === 'refuse' && /will not sum them/.test(w17.text), `W17 is refused, two measures not added: ${w17.reason}`, w17.text);
  await noFigure(w17);
  const what = await ask(page, 'What is AUM?');
  check(what.behaviour === 'glossary_definition' && what.text.includes("as the pack's glossary defines it")
    && what.text.includes("The market value of a household's holdings"), '"What is AUM?": assets under management as the pack\'s glossary defines it, labelled so', what.text);
  check(what.text.includes('No document defines it, so none is cited') && !what.text.includes('Cites:'), 'no document defines it: nothing is cited', what.text);
  const listed = run('sh', ['-c', 'grep -niw aum clients/meridian/pack.yaml']);
  check(listed.includes('{phrase: aum, term: assets-under-management}') && listed.includes('- id: aum'),
    'grep -niw aum clients/meridian/pack.yaml lists the change: its phrase and its context', listed);
  const grep = spawnSync('sh', ['-c', AUM_GREP], { cwd: DEMO, encoding: 'utf8' });
  say(`$ ${AUM_GREP}`);
  check(grep.status === 1 && grep.stdout === '', `${AUM_GREP}: prints nothing`, grep.stdout);
}

async function overlay() {
  let page = state.page;
  const routed = await ask(page, DES);
  check(routed.behaviour === 'abstain_route_steward' && /steward/i.test(routed.text), 'not a glossary term: routed to the steward queue', routed.text);
  check(routed.label === 'Sent to a steward', 'its badge says, in plain words: Sent to a steward', routed.label);
  await signIn(page, 'steward_glossary');
  check(await viewNow(page) === 'review' && JSON.stringify(await views(page)) === '["ask","review","onboard","quality","tools"]',
    'a steward lands on the Review queue, and has Onboard beside it', { now: await viewNow(page), views: await views(page) });
  const row = page.locator('#queue tr[data-family="candidate_term"]', { hasText: 'Digital Engagement Score' });
  await row.first().waitFor();
  const groups = (await page.locator('#queue h3').allTextContents()).map(flat);
  check(groups.some((g) => /^Candidate terms\s*1$/.test(g)) && groups.every((g) => !/_/.test(g)), 'the queue is grouped by kind, in plain words, each with its count', groups);
  check(await page.locator('#queue tr.settled').count() > 0 && (await page.locator('#queue tr.settled').first().innerText()).includes('no action needed'),
    'a match rejected upstream is greyed: no action needed', await page.locator('#queue').innerText());
  // The steward, asking the routed question, can follow its routed document to its row in the queue.
  await ask(page, DES);
  await page.locator('#thread .exchange').first().locator('a.queue-link').click();
  check(await viewNow(page) === 'review' && (await row.first().getAttribute('class') || '').includes('hl'), 'the routed document EM-004 links to its row in the queue', await row.first().getAttribute('class'));
  await row.first().locator('input.def').fill('Engagement with the digital channels, scored monthly (any definition will do)');
  await row.first().locator('button[data-d="accept"]').click();
  await page.waitForFunction(() => document.querySelector('#queuemsg').textContent.includes('recorded'));
  check((await page.locator('#queuemsg').innerText()).includes('term created: Digital Engagement Score'), 'accepted: the term is created', await page.locator('#queuemsg').innerText());
  state.decided = true;
  const term = await ask(page, DES);
  check(/no certified implementation/i.test(term.text), 'a glossary term now, with no certified implementation', term.text);
  await stop(state.meridian);
  make('graph');
  state.meridian = await serve(['serve', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  await page.close();
  page = state.page = await open(state.context, PORT);
  await signIn(page, 'steward_glossary');
  const kept = await ask(page, DES);
  check(kept.behaviour === term.behaviour && kept.text === term.text, 'after make graph the term is still accepted: the same answer', kept.text);
  await view(page, 'review');
  await page.waitForFunction(() => !/Loading/.test(document.querySelector('#queue').textContent));
  check(!(await page.locator('#queue').innerText()).includes('Digital Engagement Score'), 'the queue does not list it');
  await stop(state.meridian);
  make('reset-overlay', 'graph');
  state.decided = false;
  state.meridian = await serve(['serve', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  await page.close();
  page = state.page = await open(state.context, PORT);
  await signIn(page, 'analyst_enterprise');
  const fresh = await ask(page, DES);
  check(fresh.behaviour === 'abstain_route_steward', 'after make reset-overlay graph: routed to the steward again', fresh.text);
}

async function twoProfiles() {
  let context = state.context;
  if (!SIDE_BY_SIDE) {
    // One port given (PORT2 = PORT): Thornbury is served there once Meridian stops, in a browser of its own.
    say(`(PORT2 is PORT, ${PORT}: Meridian stopped first, so the two run one after the other, not side by side)`);
    await stop(state.meridian);
    context = state.context2 = await state.browser.newContext({ locale: 'en-US', viewport: { width: 1400, height: 900 } });
  }
  state.thornbury = await serve(['demo', 'PROFILE=thornbury', `PORT=${PORT2}`, 'DEMO_MODE=1', ...(HOST2 !== HOST ? [`HOST=${HOST2}`] : [])], PORT2, HOST2);
  const page = await open(context, PORT2, HOST2);
  const title = flat(await page.locator('#title').innerText());
  const tile = flat(await page.locator('#brand-tile').innerText());
  check(title === 'Context Layer' && (await page.title()) === title && tile === 'T', `Thornbury Mutual Casualty's page: Context Layer, with its own initial: ${tile}`, { title, tile });
  check((await header(page)).startsWith('Profile thornbury, client thornbury,'), 'the header names the profile, thornbury', await header(page));
  const samples = await page.locator('#chips button[data-q]').evaluateAll((bs) => bs.map((b) => b.dataset.q));
  check(samples.includes('Gross written premium for June 2025') && !samples.some((q) => /NNA|retail|wealth|Meridian/i.test(q)), 'its own sample questions', samples);
  await signIn(page, 'actuary');
  figure(await ask(page, 'Gross written premium for June 2025'), '37,646.14');
  const meridians = await ask(page, 'Total retail revenue for June 2025');
  check(/^abstain/.test(meridians.behaviour), 'Meridian\'s question abstains on Thornbury', meridians);
  check(await tab(page, 'plan') === 'No warehouse query was run.', 'no warehouse queried');
  const eur = await ask(page, 'Gross written premium in EUR for June 2025');
  check(/^abstain/.test(eur.behaviour) && eur.text.includes('certified figures are in USD'), `in EUR on Thornbury: abstains, its figures in USD alone: ${eur.reason}`, eur.text);
  await noFigure(eur);
  if (SIDE_BY_SIDE) {
    const m = state.page;
    await m.bringToFront();
    figure(await ask(m, 'Total retail revenue for June 2025'), '13,287.67', 'Meridian, still serving beside it: Total retail revenue for June 2025');
    check((await runTrace(m)).profile === 'meridian' && (await runTrace(page)).profile === 'thornbury', 'each trace names its own profile');
  }
}

// The client onboarded from scratch (issue #40's exit gate): its folders hold data only, each onboarding step run for it as
// the README runs it, then its page. Its build is removed first, so each run starts as a clean checkout does.
const TOY = 'fellmoor';
const TOY_GREP = 'grep -rliw fellmoor --exclude-dir=estate kge ui meta-model.yaml Makefile';
async function newClient() {
  const listed = run('sh', ['-c', `ls clients/${TOY} deploy/${TOY}`]);
  check(['pack.yaml', 'pack.lock', 'fixture', 'identities.yaml'].every((f) => listed.split(/\s+/).includes(f)),
    `ls clients/${TOY} deploy/${TOY}: its pack, its lock, its fixture and its identities`, listed);
  const files = run('sh', ['-c', `find clients/${TOY} deploy/${TOY} -type f`]).trim().split('\n');
  check(files.length > 5 && files.every((f) => /\.(yaml|json|csv|md|lock)$/.test(f)), 'no Python: a schema, CSVs, Markdown and YAML', files);
  const p = profile(TOY);
  const roots = [p.artifacts, p.store, path.dirname(p.landed), p.estate];
  for (const r of roots) fs.rmSync(path.join(DEMO, r), { recursive: true, force: true });
  say(`(its build removed, as on a clean checkout: ${roots.join(' ')})`);
  const at = (...args) => [...args, `PROFILE=${TOY}`];
  const estate = make(...at('estate'));
  check(estate.includes('kge/estate/from_files.py') && /"ai_ready": 4/.test(estate) && /"heldout_questions": 3/.test(estate),
    'make estate builds its estate from the fixture, its three held-out paraphrases with it, and certifies it', estate.slice(-1500));
  const locked = make(...at('lock'));
  check(/"written": false/.test(locked) && run('git', ['status', '--porcelain', `clients/${TOY}`]).trim() === '',
    `make lock writes nothing: git status clients/${TOY} stays clean`, locked.slice(-800));
  const checked = make(...at('check'));
  check(/no findings/.test(checked), 'make check finds nothing', checked.slice(-800));
  const first = make(...at('compile'));
  const second = make(...at('compile'));
  const art = (s) => (s.match(/^(build\/artifacts\/\S+\/)$/m) || [])[1];
  check(art(first) && art(first) === art(second) && second.includes('already compiled, unchanged'),
    'make compile, twice: the same path, the second time already compiled, unchanged', second.slice(-800));
  const h = path.basename(art(first));
  const loaded = make(...at('load'));
  check(new RegExp(`^release: ${h} LOADED`, 'm').test(loaded) && loaded.includes('current is none')
    && !fs.existsSync(path.join(DEMO, p.artifacts, 'current')), 'make load marks it LOADED, with nothing current', loaded.slice(-800));
  const early = refused(...at('release'));
  check(early.includes(`${h} is not through the onboarding gate`), 'make release is refused: not through the onboarding gate', early.slice(-800));
  const gate = make(...at('margin', `HASH=${h}`));
  for (const kind of ['Within a unit', 'Across units', 'Shelf', 'Abstain']) {
    check(new RegExp(`^  V01 golden v1, ${kind}: graph on passed (\\d+) of \\1 \\(100%\\), at least 100%: passed$`, 'm').test(gate),
      `V01, ${kind}: every golden question passes on graph on`, gate.slice(-3000));
  }
  for (const kind of ['Across units', 'Shelf']) {
    check(gate.includes(`  V02 golden v1, ${kind}: graph on less graph off, +100 pts, at least +50 pts: passed`),
      `V02, ${kind}: graph on is 100 points ahead of graph off, where its pack asks for 50`, gate.slice(-3000));
  }
  check(gate.includes('  V03 heldout v1, held-out: graph on passed Within a unit 1 of 1, Across units 1 of 1, Shelf 1 of 1; '
    + 'graph off Within a unit 0 of 1, Across units 0 of 1, Shelf 0 of 1: reported, never gated'),
    'V03: its held-out set reported, never gated: graph on passes each of the three paraphrases, graph off none', gate.slice(-3000));
  check(gate.includes('  V04 every set, held-out included: no graph-on answer cites an uncertified or off-tier document: passed'),
    'V04: no off-tier citation', gate.slice(-3000));
  check(gate.includes(`no-fall check: skipped, profile ${TOY} names no baseline`), 'no baseline, so no fall check, and it says so', gate.slice(-3000));
  const runId = (gate.match(/^GATE: \S+: passed, run (\S+)$/m) || [])[1];
  check(runId && gate.includes("passed: every post-load check against the pack's gates"), 'passed against its own pack\'s gates', gate.slice(-3000));
  const analyst = refused(...at('sign-off', 'PRINCIPAL=network_analyst'));
  check(analyst.includes('network_analyst may not perform certification'), 'make sign-off PRINCIPAL=network_analyst is refused: no certification', analyst.slice(-800));
  const signed = make(...at('sign-off', 'PRINCIPAL=fares_steward'));
  check(signed.includes(`${h} signed off by fares_steward`), 'make sign-off PRINCIPAL=fares_steward signs it off', signed.slice(-800));
  const released = make(...at('release'));
  check(released.includes(`current is now ${h}`) && released.includes('signed off by fares_steward'), 'make release swaps current to it', released.slice(-800));
  // Its page: on PORT3, a page already serving there stopped first
  for (const s of [state.meridian, state.thornbury]) if (s && !s.exited && s.port === PORT3 && s.host === HOST) await stop(s);
  state.fellmoor = await serve(['serve', `PROFILE=${TOY}`, `PORT=${PORT3}`, 'DEMO_MODE=1'], PORT3);
  // Its page in dark mode, as the system asks for it: the whole step runs there, then the viewer's choice is cycled.
  state.context3 = await state.browser.newContext({ locale: 'en-US', viewport: { width: 1400, height: 900 }, colorScheme: 'dark' });
  const page = await open(state.context3, PORT3);
  await look(page, 'dark', 'system');
  await page.locator('#theme').click();
  await look(page, 'light', 'light');
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#profile').textContent.trim() !== '');
  await page.waitForFunction(() => document.querySelectorAll('#principal option').length > 1 && document.querySelectorAll('#arm option').length > 0);
  await page.waitForFunction(() => document.querySelectorAll('#chips button').length > 0);
  await look(page, 'light', 'light');
  await page.locator('#theme').click();
  await look(page, 'dark', 'dark');
  await page.locator('#theme').click();
  await look(page, 'dark', 'system');
  const title = flat(await page.locator('#title').innerText());
  const tile = flat(await page.locator('#brand-tile').innerText());
  check(title === 'Context Layer' && (await page.title()) === title && tile === 'F', `Context Layer, with its own initial: ${tile}`, { title, tile });
  check((await header(page)).startsWith(`Profile ${TOY}, client ${TOY},`), `the header names the profile, ${TOY}`, await header(page));
  const samples = await page.locator('#chips button[data-q]').evaluateAll((bs) => bs.map((b) => b.dataset.q));
  const own = JSON.parse(fs.readFileSync(path.join(DEMO, 'clients', TOY, 'fixture', 'golden.json'), 'utf8')).map((i) => i.question);
  check(samples.length > 0 && samples.every((q) => own.includes(q)), 'its own sample questions, its golden set\'s', samples);
  await signIn(page, 'network_analyst');
  figure(await ask(page, 'Fare revenue for June 2025', { sample: true }), '438.66');
  const cross = await ask(page, 'Join journeys to fleet vehicles on vehicle_ref and count the matches', { sample: true });
  check(cross.behaviour === 'refuse' && cross.text.includes('No certified join path between Fares and Ticketing and Fleet Engineering')
    && cross.text.includes('never checked against fleet.vehicle'), `its cross question: refused on its own no-join fact: ${cross.reason}`, cross.text);
  await noFigure(cross);
  const meridians = await ask(page, 'Total retail revenue for June 2025');
  check(/^abstain/.test(meridians.behaviour), 'Meridian\'s question abstains', meridians);
  check(await tab(page, 'plan') === 'No warehouse query was run.', 'no warehouse queried');
  const rt = await runTrace(page);
  check(rt.profile === TOY && rt.graph.artifact === h && (await header(page)).endsWith(`artifact ${h.slice(0, 12)}`),
    'the Trace tab names the profile and the artifact', { profile: rt.profile, artifact: rt.graph.artifact });
  await page.waitForFunction((id) => document.querySelector('#ab').textContent.includes(id), runId);
  const ab = await page.locator('#ab a').getAttribute('href');
  check(ab === `/ab/${runId}/report.html`, 'the A/B panel lists the gate\'s run and links its report', ab);
  const grep = spawnSync('sh', ['-c', TOY_GREP], { cwd: DEMO, encoding: 'utf8' });
  say(`$ ${TOY_GREP}`);
  check(grep.status === 1 && grep.stdout === '', `${TOY_GREP}: prints nothing`, grep.stdout);
}

async function ddlBusinessUnit() {
  const page = state.page;
  const p = profile('meridian');
  state.onboarding = { unit: path.join(DEMO, 'clients', 'meridian', 'units', 'leasing') };
  check(!fs.existsSync(state.onboarding.unit), 'the leasing unit is absent before onboarding', state.onboarding.unit);
  await signIn(page, 'knowledge_engineer');
  check(await viewNow(page) === 'onboard' && !(await views(page)).includes('review'), 'the knowledge engineer lands on Onboard, and has no Review queue', await views(page));
  await waitForOnboard(page, 'Create source');
  check(await page.locator('#onboard').isVisible(), 'the Onboard panel is loaded and visible to the knowledge engineer');
  for (const id of ['ob-add', 'ob-source-label', 'ob-upload', 'ob-row-source'])
    check(await page.locator('#' + id).isVisible(), `${id} is visible to the knowledge engineer`);
  await quickstartShot(page, 'onboard', '#onboard');
  check(await page.getByRole('button', { name: 'Create source', exact: true }).isVisible(), 'Create source is available');
  check(await page.locator('#ob-proposal-card').isHidden(), 'No proposal card before a unit is onboarded');
  check((await page.locator('#ob-rows').innerText()).includes('from these CSV files'), 'from these CSV files is available');
  check((await page.locator('#ob-rows').innerText()).includes('no rows yet: draft only'), 'no rows yet: draft only is available');
  check((await page.locator('#ob-rows').innerText()).includes('invented from the DDL, labelled invented'),
    'invented from the DDL, labelled invented is available');

  await page.locator('#ob-unit').fill('leasing');
  await page.locator('#ob-label').fill('Asset Finance');
  await page.locator('#ob-file').setInputFiles(path.join(DEMO, 'examples', 'leasing', 'unsafe.sql'));
  await page.locator('#ob-msg').filter({ hasText: 'CREATE TABLE AS SELECT' }).waitFor();
  check((await page.locator('#ob-msg').innerText()).includes('statement 1'), 'unsafe DDL is refused by statement and kind', await page.locator('#ob-msg').innerText());

  await page.locator('#ob-file').setInputFiles(path.join(DEMO, 'examples', 'leasing', 'schema.sql'));
  await page.locator('#ob-rows').selectOption('none');
  await page.locator('#ob-tables').filter({ hasText: 'drafting only' }).waitFor();
  const tables = flat(await page.locator('#ob-tables').innerText());
  // The section heading is upper-cased by CSS, and innerText returns it as shown: compare its words case-blind.
  check(['lessee', 'lease', 'rental'].every((name) => tables.includes(name)) && tables.toLowerCase().includes('0 rows, none: drafting only')
    && !tables.includes('invented rows'), 'Choosing a schema shows all three tables with no rows, none called invented', tables);
  await onboardClick(page, 'Create source', '/onboard/source');
  check((await page.locator('#ob-msg').innerText()).includes('source created'), 'Create source creates the warehouse', await page.locator('#ob-msg').innerText());
  await waitForOnboard(page, 'Create source');
  await onboardClick(page, 'Create source', '/onboard/source');
  check((await page.locator('#ob-msg').innerText()).includes('source exists already; nothing changed'),
    'Create source is idempotent', await page.locator('#ob-msg').innerText());
  await signIn(page, 'business_team');
  await page.locator('#next-step').filter({ hasText: 'knowledge_engineer' }).waitFor();
  check((await page.locator('#next-step').innerText()).includes('Waiting on'), 'before drafting the business team waits on the named engineer');
  await signIn(page, 'knowledge_engineer');
  await waitForOnboard(page, 'Draft');
  await onboardClick(page, 'Draft', '/onboard/draft');
  await page.locator('.unit-card[data-unit="leasing"] button.primary').filter({ hasText: 'Add rows' }).waitFor();
  await page.waitForFunction(() => document.querySelector('#onboard-count').textContent === '1');
  check(await page.locator('#onboard-count').textContent() === '1', 'one onboarding next step after Draft');
  const draft = flat(await page.locator('.unit-card[data-unit="leasing"] .items').textContent());
  for (const expected of ['source', 'Lease Rentals', 'Lease rentals for the latest month with rows'])
    check(draft.includes(expected), `Draft shows ${expected}`, draft);

  await page.locator('.unit-card[data-unit="leasing"] .more').evaluate(d => { d.open = true; });
  await onboardClick(page, 'Run checks', '/onboard/run', 409);   // the no-data stop: refused, naming the rows it needs
  await page.locator('#ob-msg').filter({ hasText: 'CSVs beside the DDL' }).waitFor();
  const stopped = flat(await page.locator('#ob-msg').innerText());
  check(stopped.includes('CSVs beside the DDL') && stopped.includes('invented rows'), 'checks stop and name the rows they need', stopped);
  check(stopped.startsWith('needs attention') && !stopped.includes('ROWS='), 'a stop that waits on rows is a warning, not a refusal, in plain words', stopped);
  // The page redraws the stepper from GET /onboard after the draft; wait for that redraw, then check it.
  const unitCard = page.locator('.unit-card[data-unit="leasing"]');
  await unitCard.locator('div.next').filter({ hasText: "Next: add Asset Finance's rows" }).waitFor();
  const next = await page.locator('.unit-card button.primary').allTextContents();
  check(next.length === 1 && next[0] === 'Add rows' && flat(await unitCard.locator('div.next').innerText()).includes("Next: add Asset Finance's rows"),
    'one primary Add rows button on the unit card', { next, line: await unitCard.locator('div.next').innerText() });
  await page.locator('#rows-mode-leasing').selectOption('csv');
  await page.locator('#rows-csv-leasing').setInputFiles(['data/lessee.csv', 'data/lease.csv', 'data/rental.csv', 'questions.json', 'heldout.json'].map((f) => path.join(DEMO, 'examples', 'leasing', f)));
  await onboardClick(page, 'Add rows', '/onboard/rows');
  // The page redraws the items from GET /onboard after the rows are added; wait for that redraw, then check it.
  await page.locator('.unit-card[data-unit="leasing"] .items').filter({ hasText: 'Lease rentals for June 2025' }).waitFor({ state: 'attached', timeout: 15000 });
  const filled = flat(await page.locator('.unit-card[data-unit="leasing"] .items').textContent());
  for (const expected of ['Lease rentals for June 2025', '1,461.25']) check(filled.includes(expected), `Add rows shows ${expected}`, filled);
  await unitCard.locator('.more').evaluate(d => { d.open = true; });
  await waitForOnboardJob(page, await onboardClick(page, 'Run checks', '/onboard/run'), 'checks', 'failed');
  const failed = flat(await page.locator('#ob-gate').innerText());
  check(failed.includes('V05 drafted items: 4 not certified'), 'the first gate fails V05 on the draft items', failed);
  await signIn(page, 'steward_glossary');
  const earlyRelease = await page.evaluate(async () => {
    const r = await fetch('/onboard/run', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({step: 'release'})});
    return {status: r.status, body: await r.json()};
  });
  check(earlyRelease.status === 409 && earlyRelease.body.detail.includes('run checks for this candidate'),
    'Release is refused after the failed gate', earlyRelease);
  await signIn(page, 'business_team');
  // Signing in redraws Onboard from GET /onboard after the name changes; Accept all is the business team's and not the
  // steward's, so once it shows, the buttons are the business team's, not the ones the steward left.
  await waitForOnboard(page, 'Accept all');
  for (const id of ['ob-add'])
    check(!(await page.locator('#' + id).isVisible()), `${id} is hidden from the business team`);
  check((await page.locator('#next-step').innerText()).includes('Accept'), 'the next step asks the business team to Accept');
  await onboardClick(page, 'Accept all', '/onboard/accept');
  await page.waitForFunction(() => [...document.querySelectorAll('.unit-card[data-unit="leasing"] .items .badge')].every(b => b.textContent === 'accepted'));
  const accepted = await page.locator('.unit-card[data-unit="leasing"] .items').locator('.badge').allTextContents();
  check(accepted.length === 4 && accepted.every((x) => x.trim() === 'accepted'),
    'the business team accepts all four items: the source, the term, its question and its mapping to an industry standard',
    await page.locator('.unit-card[data-unit="leasing"] .items').innerText());

  await signIn(page, 'steward_glossary');
  await waitForOnboard(page, 'Certify all');
  check((await page.locator('#next-step').innerText()).includes('Certify 4 accepted items'), 'the steward sees the next certification step');
  for (const id of ['ob-add'])
    check(!(await page.locator('#' + id).isVisible()), `${id} is hidden from the steward`);
  for (const label of ['Certify all'])
    check(await page.getByRole('button', { name: label, exact: true }).isVisible(), `${label} is available to the steward`);
  await onboardClick(page, 'Certify all', '/onboard/certify');
  await page.waitForFunction(() => [...document.querySelectorAll('.unit-card[data-unit="leasing"] .items .badge')].every(b => b.textContent === 'certified'));
  const certified = await page.locator('.unit-card[data-unit="leasing"] .items').locator('.badge').allTextContents();
  check(certified.length === 4 && certified.every((x) => x.trim() === 'certified'),
    'the steward certifies all four accepted items', await page.locator('.unit-card[data-unit="leasing"] .items').innerText());
  await signIn(page, 'knowledge_engineer');
  await waitForOnboard(page, 'Run checks');
  await waitForOnboardJob(page, await onboardClick(page, 'Run checks', '/onboard/run'), 'checks', 'done');
  const passed = flat(await page.locator('#ob-gate').innerText());
  check(passed.includes('V05') && !passed.includes('failed'), 'the second gate passes, including V05', passed);
  const candidate = await page.evaluate(async () => (await (await fetch('/onboard')).json()).job?.hash);
  check(/^[0-9a-f]{64}$/.test(candidate || ''), 'the passing gate names its candidate hash', candidate);
  await signIn(page, 'steward_glossary');
  await waitForOnboard(page, 'Sign off');
  await waitForOnboardJob(page, await onboardClick(page, 'Sign off', '/onboard/run'), 'sign-off', 'done');
  check(flat(await page.locator('#ob-job').innerText()).includes('Signed off'), 'the steward signs off', await page.locator('#ob-job').innerText());
  const beforeRelease = await header(page);
  await waitForOnboardJob(page, await onboardClick(page, 'Release', '/onboard/run'), 'release', 'done');
  await page.waitForFunction((before) => document.querySelector('#profile').textContent.trim() !== before, beforeRelease);
  check((await header(page)) !== beforeRelease && (await header(page)).endsWith(`artifact ${candidate.slice(0, 12)}`),
    'Release switches the page to the signed-off candidate hash', await header(page));
  check((await page.locator('#ob-job').innerText()).includes('Released: Asset Finance is live'), 'the release outcome stays on the live unit card');
  await signIn(page, 'knowledge_engineer');
  await waitForOnboard(page, 'Improve: Run');
  await unitCard.locator('.more').evaluate(d => { d.open = true; });
  await unitCard.getByRole('button', { name: 'Update this unit', exact: true }).click();
  check(await page.locator('#ob-add-title').innerText() === 'Update Asset Finance' && await page.locator('#ob-unit').evaluate(e => e.readOnly), 'Update this unit opens the form with a fixed unit id');
  await page.locator('#ob-cancel').click();
  check(await page.locator('#ob-add-title').innerText() === 'Add a unit' && !(await page.locator('#ob-unit').evaluate(e => e.readOnly)), 'Cancel restores Add a unit');
  await signIn(page, 'analyst_enterprise');
  const leasing = await ask(page, 'Lease rentals for June 2025');
  figure(leasing, '1,461.25');
  const retail = await ask(page, 'Total retail revenue for June 2025');
  figure(retail, '13,287.67');
}

async function inventedDatabaseClient() {
  // README wording: Candidate <candidate>, not released.
  const registry = path.join(DEMO, 'deploy', 'profiles.yaml');
  const before = fs.readFileSync(registry);
  make('client', 'CLIENT=brackenridge', 'LABEL=Brackenridge Brewery',
    'DDL=examples/brackenridge/schema.sql', 'CSV=examples/brackenridge/data');
  state.newClient = true;
  for (const unit of ['taproom', 'cellar']) {
    make('accept', 'PROFILE=brackenridge', `UNIT=${unit}`, 'ITEM=all', 'PRINCIPAL=business_team');
    make('certify-draft', 'PROFILE=brackenridge', `UNIT=${unit}`, 'ITEM=all', 'PRINCIPAL=steward');
  }
  const out = make('onboard', 'PROFILE=brackenridge', 'REPEATS=1');
  const match = out.match(/onboard: candidate ([0-9a-f]{64})/);
  check(match, 'make onboard names the new client candidate', out.slice(-1000));
  const candidate = match[1];
  const preview = await serve(['serve', 'PROFILE=brackenridge', `HASH=${candidate}`, `PORT=${Number(PORT) + 3}`, 'DEMO_MODE=1'], Number(PORT) + 3);
  const p = await (await fetch(`http://${HOST}:${Number(PORT) + 3}/profile`)).json();
  check(p.preview?.banner === `Candidate ${candidate}, not released`, 'the candidate is labelled not released', p);
  check(!fs.existsSync(path.join(DEMO, 'build/artifacts/brackenridge/current')), 'preview does not move the released graph');
  await stop(preview);
  make('sign-off', 'PROFILE=brackenridge', `HASH=${candidate}`, 'PRINCIPAL=steward');
  make('release', 'PROFILE=brackenridge', `HASH=${candidate}`);
  const answer = make('agent', 'PROFILE=brackenridge', 'PRINCIPAL=analyst', 'AGENT_ARM=treatment', 'Q=Takings for June 2025');
  check(answer.includes('figure 91.50'), 'Takings for June 2025 is 91.50', answer);
  make('reset-client', 'CLIENT=brackenridge');
  state.newClient = false;
  check(fs.readFileSync(registry).equals(before), 'reset restores deploy/profiles.yaml byte for byte');
}

async function richAnswers() {
  const page = state.page;
  await signIn(page, 'analyst_enterprise');
  await view(page, 'ask');
  await unfold(page, 'settings');
  await page.selectOption('#arm', 'graph');
  check(!(await page.isChecked('#rich')), 'Rich answers is off when the page loads');
  await page.check('#rich');
  const division = await ask(page, 'NNA for FY26-Q1 by division');
  const card = page.locator('#thread .exchange').first().locator('.msg:not(.q)');
  const table = await card.locator('table.rich-table tr').evaluateAll((rows) => rows.slice(1).map((r) => [...r.cells].map((c) => c.textContent.trim())));
  check(table.length === 2 && table[0][0] === 'EAST' && table[0][1] === '12,371,673.22'
    && table[1][0] === 'WEST' && table[1][1] === '8,831,519.54',
  'the figures table reads EAST 12,371,673.22 and WEST 8,831,519.54 from the answer', table);
  check((await card.locator('svg rect.bar').count()) === 2, 'the chart draws two bars from the table');
  const values = (await card.locator('.chart .bv').allTextContents()).map(flat);
  const size = await card.locator('.chart .bl').first().evaluate((l) => parseFloat(getComputedStyle(l).fontSize));
  check(values.join() === '12,371,673.22,8,831,519.54' && size >= 12, 'each bar is labelled with its value, in text 12px or larger', { values, size });
  check((await card.locator('p.ans').innerText()).includes('wealth · flow.amount_usd'),
    'the sentence names the answer\'s unit and column as its source', await card.locator('p.ans').innerText());
  check(division.text.includes('gate passed · 2 checked queries · SQL by planner · text by template'),
    'the card names the two checked queries', division.text);
  await ask(page, 'Combined Retail and SME revenue for June 2025');
  const combined = page.locator('#thread .exchange').first().locator('.msg:not(.q)');
  check((await combined.locator('svg rect.bar').count()) === 2 && (await combined.textContent()).includes('334,418.32'),
    'the combined total draws its two checked parts', await combined.innerText());
  await ask(page, 'Total revenue across the bank for the quarter to 30 June 2025');
  const refused = page.locator('#thread .exchange').first().locator('.msg:not(.q)');
  check((await refused.locator('svg rect.bar').count()) === 0 && (await refused.innerText()).includes('No chart:'),
    'the cross-unit answer draws no chart and gives the graph reason', await refused.innerText());
  check((await refused.locator('table.rich-table tr').filter({hasText: 'Total'}).count()) === 0,
    'the cross-unit answer has no Total row', await refused.innerText());
  await page.uncheck('#rich');
  check(!(await page.isChecked('#rich')), 'Rich answers is off again');
  await ask(page, 'NNA for FY26-Q1 by division');
  const ordinary = page.locator('#thread .exchange').first().locator('.msg:not(.q)');
  check((await ordinary.locator('table.rich-table').count()) === 0 && (await ordinary.locator('table.parts').count()) === 1,
    'the answer card is ordinary again after switching Rich answers off', await ordinary.innerText());
}

async function improveJob() {
  const page = state.page;
  await signIn(page, 'analyst_enterprise');
  const before = await ask(page, 'Lease income for June 2025');
  check(/^abstain/.test(before.behaviour), 'before the improvement job, "Lease income for June 2025" abstains', before);

  await signIn(page, 'knowledge_engineer');
  await waitForOnboard(page, 'Improve: Run', '#ob-proposal-card');
  check(await page.locator('#onboard').isVisible(), 'the *Onboard* panel is visible to the knowledge engineer');
  await waitForOnboardJob(page, await onboardPress(page, page.locator('#ob-proposal-card').getByRole('button', { name: 'Improve: Run', exact: true }), '/onboard/run', null, 'Improve: Run'), 'improve', 'done');
  const prop = flat(await page.locator('#ob-proposal').innerText());
  check(prop.includes('1.5.0') && prop.includes('1.6.0') && prop.includes('lease income') && prop.includes('lease-rentals'),
    'the improvement proposal bumps 1.5.0 to 1.6.0 and proposes alias lease income for lease-rentals', prop);
  const notes = flat(await page.locator('#ob-notes').innerText());
  check(notes.includes('steward') || notes.includes('platform'), 'the Wealth misses are listed as steward notes and platform requests', notes);
  const still = await ask(page, 'Lease income for June 2025');
  check(/^abstain/.test(still.behaviour), 'while the proposal is drafted, "Lease income for June 2025" still abstains', still);

  await signIn(page, 'steward_glossary');
  const earlyRelease = await page.evaluate(async () => {
    const r = await fetch('/onboard/run', {method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({step: 'release'})});
    return {status: r.status, body: await r.json()};
  });
  check(earlyRelease.status === 409, 'Release is refused while the proposal is drafted', earlyRelease);

  await signIn(page, 'business_team');
  await waitForOnboard(page, 'Accept all', '#ob-proposal-card');
  await onboardClick(page, 'Accept all', '/onboard/accept', null, '#ob-proposal-card');
  await page.waitForFunction(() => document.querySelector('#ob-proposal').textContent.includes('accepted'));
  check((await page.locator('#ob-proposal').innerText()).includes('accepted'), 'the business team presses Accept all on the proposal');

  await signIn(page, 'steward_glossary');
  await waitForOnboard(page, 'Certify all', '#ob-proposal-card');
  await onboardClick(page, 'Certify all', '/onboard/certify', null, '#ob-proposal-card');
  await page.waitForFunction(() => document.querySelector('#ob-proposal').textContent.includes('certified'));
  check((await page.locator('#ob-proposal').innerText()).includes('certified'), 'the steward presses Certify all on the proposal');

  await signIn(page, 'knowledge_engineer');
  await waitForOnboard(page, 'Run checks', '#ob-proposal-card');
  await waitForOnboardJob(page, await onboardClick(page, 'Run checks', '/onboard/run', null, '#ob-proposal-card'), 'checks', 'done');
  await signIn(page, 'steward_glossary');
  await waitForOnboard(page, 'Sign off', '#ob-proposal-card');
  await waitForOnboardJob(page, await onboardClick(page, 'Sign off', '/onboard/run', null, '#ob-proposal-card'), 'sign-off', 'done');
  const beforeRelease = await header(page);
  await waitForOnboardJob(page, await onboardClick(page, 'Release', '/onboard/run', null, '#ob-proposal-card'), 'release', 'done');
  await page.waitForFunction((b) => document.querySelector('#profile').textContent.trim() !== b, beforeRelease);

  const versions = flat(await page.locator('#ob-versions').innerText());
  check(versions.includes('1.5.0') && versions.includes('1.6.0') && versions.includes('current') && versions.includes('13/18'),
    '*Versions* lists 1.5.0 and 1.6.0 (current, with 13/18 on the held-out sets)', versions);

  await signIn(page, 'analyst_enterprise');
  const income = await ask(page, 'Lease income for June 2025');
  figure(income, '1,461.25');

  await signIn(page, 'steward_glossary');
  const beforeRollback = await header(page);
  await view(page, 'onboard');
  const again = page.locator('#ob-versions tr', { hasText: '1.5.0' }).getByRole('button', { name: 'Release again', exact: true }).last();
  await waitForOnboardJob(page, await onboardPress(page, again, '/onboard/run', null, 'Release again'), 'release', 'done');
  await page.waitForFunction((b) => document.querySelector('#profile').textContent.trim() !== b, beforeRollback);

  await signIn(page, 'analyst_enterprise');
  const reverted = await ask(page, 'Lease income for June 2025');
  check(/^abstain/.test(reverted.behaviour), 'after Release again on 1.5.0, "Lease income for June 2025" abstains again', reverted);
  const rentals = await ask(page, 'Lease rentals for June 2025');
  figure(rentals, '1,461.25');
}

// Step 14 in two halves, which the walkthrough's parts may run apart (HALVES): the OSI YAML unit, its update and its
// rollback; then, with the API stopped and the unit reset, the Markdown form. In a row the one follows the other.
async function semanticBusinessUnit() {
  await semanticOsi();
  await semanticMarkdown();
}

async function semanticOsi() {
  make('source', 'UNIT=bancassurance', 'LABEL=Bancassurance', 'SEMANTIC=examples/bancassurance-osi', 'PRINCIPAL=knowledge_engineer');
  const drafted = make('draft', 'UNIT=bancassurance', 'PRINCIPAL=knowledge_engineer');
  check(drafted.includes('drafted'), 'the OSI YAML model drafts the unit', drafted);
  const recipeDraft = fs.readFileSync(path.join(DEMO, 'clients/meridian/units/bancassurance/draft.yaml'), 'utf8');
  check(recipeDraft.includes("figure: '1200.00'") && recipeDraft.includes("figure: '2000.00'"),
    'the filtered and month-end golden figures are 1,200.00 and 2,000.00', recipeDraft);
  check(recipeDraft.includes('id: BR-01') && recipeDraft.includes('id: BR-02') && !recipeDraft.includes('id: BR-03'),
    'the two matching client figures are drafted and the mismatch is held', recipeDraft);
  const status = make('onboard-status');
  check(status.includes("Your figure for 'Board-reported active-policy written premium for June 2025' is 1,250.00; your SQL returns 1,200.00"),
    'the deliberate mismatch is an open request: 1,250.00 stated, 1,200.00 returned', status);
  make('accept', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=business_team');
  make('certify-draft', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=steward_glossary');
  const onboarded = make('onboard', 'REPEATS=1');
  const hash = (onboarded.match(/onboard: candidate ([0-9a-f]{64})/) || [])[1];
  check(hash && onboarded.includes('onboard: gate passed'), 'the semantic candidate passes its post-load checks', onboarded.slice(-3000));
  make('sign-off', `HASH=${hash}`, 'PRINCIPAL=steward_glossary');
  make('release', `HASH=${hash}`);
  const answer = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Gross written premium for June 2025');
  check(answer.includes('figure 1,200 from premium.premium_amount'),
    'Gross written premium for June 2025: figure 1,200 from premium.premium_amount', answer);
  const inForce = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=In force premium for June 2025');
  check(inForce.includes('figure 2,000 from premium_snapshot.in_force_premium'),
    'In force premium for June 2025: 2,000 at the latest June month end, not 3,900 over both snapshots', inForce);
  const g042 = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Total bank revenue in GBP for Q2 2025');
  check(g042.includes('abstain') && g042.includes('No GBP conversion standard exists'),
    'the GBP unit moves no other answer: Total bank revenue in GBP for Q2 2025 still abstains', g042);
  await state.page.reload();
  await signIn(state.page, 'business_team');
  const card = state.page.locator('.unit-card').filter({hasText: 'Bancassurance'});
  await card.first().waitFor();   // the cards render after sign-in; count() alone does not wait for them
  check((await card.count()) === 1 && (await card.innerText()).includes('bancassurance v1:'),
    'the Bancassurance card shows its one-line status', await card.innerText());
  check((await card.innerText()).includes('2 from client · 2 drafted'),
    'the Bancassurance card splits client and drafted golden questions', await card.innerText());
  const opened = state.context.waitForEvent('page');
  await state.page.locator('#ob-guide').click();
  const guide = await opened;
  await guide.waitForLoadState();
  check((await guide.locator('h1').innerText()) === 'How onboarding works', 'the onboarding guide link opens');
  await guide.close();

  // Onboarding is continuous: the live unit is updated by the same command with a second folder. The update is drafted
  // beside it and compared item by item; only what changed is reviewed; build lands it and the same gates release it.
  make('source', 'UNIT=bancassurance', 'LABEL=Bancassurance', 'SEMANTIC=examples/bancassurance-osi-v2', 'PRINCIPAL=knowledge_engineer');
  make('draft', 'UNIT=bancassurance', 'PRINCIPAL=knowledge_engineer');
  const pending = make('onboard-status');
  check(pending.includes('bancassurance v2 (update): 4 changed, 3 added, 4 removed — awaiting acceptance; v1 stays live'),
    'the update is compared with the live unit: 4 changed, 3 added, 4 removed, awaiting acceptance, v1 live', pending);
  check(pending.includes('added ontology-commission-fibo-monetary-amount') && pending.includes('removed ontology-in_force_premium-fibo-monetary-amount'),
    'the new term and the removed one each bring their candidate mapping to an industry standard', pending);
  check(pending.includes('changed BANCASSURANCE-D01: expected figure 1200.00 -> 1650.00'),
    'the drafted golden figure moves from 1,200.00 to 1,650.00', pending);
  check(pending.includes("recipe policy.policy_id = premium.policy_id AND policy.status = 'active' -> " +
    "policy.policy_id = premium.policy_id AND policy.status IN ('active', 'lapsed')") &&
    pending.includes('table premium: column commission_amount added') && pending.includes('added commission: new term: Commission'),
    'what changed lists the widened recipe, the new column and the new measure', pending);
  check(pending.includes('removed in_force_premium: removed term: In Force Premium') &&
    pending.includes("Released golden question BR-01 ('In force premium at 30 June 2025') depends on in_force_premium"),
    'the removed metric is a proposed removal, and the released golden questions resting on it open requests', pending);
  const updateDraft = fs.readFileSync(path.join(DEMO, 'clients/meridian/units/bancassurance/update/draft.yaml'), 'utf8');
  check(updateDraft.includes('title: Commission for June 2025') && updateDraft.includes("figure: '245.05'"),
    'Commission for June 2025 is drafted new at 245.05', updateDraft);
  await state.page.reload();
  await signIn(state.page, 'business_team');
  const updating = state.page.locator('.unit-card').filter({hasText: 'Bancassurance'});
  await updating.first().waitFor();
  check((await updating.innerText()).includes('bancassurance v2 (update): 4 changed, 3 added, 4 removed') &&
    (await updating.innerText()).includes('What changed in v2'),
    'the Bancassurance card shows the update and its what-changed list', await updating.innerText());
  const stillV1 = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Gross written premium for June 2025');
  check(stillV1.includes('figure 1,200 from premium.premium_amount'), 'while v2 is reviewed, nothing live changes: v1 answers 1,200', stillV1);
  make('accept', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=business_team');
  make('certify-draft', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=steward_glossary');
  const updatedRun = make('onboard', 'REPEATS=1');
  const hash2 = (updatedRun.match(/onboard: candidate ([0-9a-f]{64})/) || [])[1];
  check(hash2 && hash2 !== hash && updatedRun.includes('bancassurance v2 landed (4 changed, 3 added, 4 removed)') &&
    updatedRun.includes('onboard: gate passed'), 'onboarding lands v2 and its candidate passes the same checks', updatedRun.slice(-3000));
  // v2 landed in a warehouse of its own: until its release, a fresh process answers v1's definitions on v1's rows. v2's
  // second 30 June snapshot would make v1's in-force figure 3,950 if it read v2's rows.
  const landedGwp = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Gross written premium for June 2025');
  check(landedGwp.includes('figure 1,200 from premium.premium_amount'),
    'v2 landed but not released: Gross written premium for June 2025 still answers 1,200', landedGwp);
  const landedInForce = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=In force premium for June 2025');
  check(landedInForce.includes('figure 2,000 from premium_snapshot.in_force_premium'),
    'v2 landed but not released: In force premium for June 2025 still 2,000, from v1\'s rows, not 3,950 from v2\'s', landedInForce);
  make('sign-off', `HASH=${hash2}`, 'PRINCIPAL=steward_glossary');
  make('release', `HASH=${hash2}`);
  const widened = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Gross written premium for June 2025');
  check(widened.includes('figure 1,650 from premium.premium_amount'),
    'after v2 is released, Gross written premium for June 2025 answers 1,650 (active and lapsed)', widened);
  const commission = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Commission for June 2025');
  check(commission.includes('figure 245.05 from premium.commission_amount'), 'Commission for June 2025 answers 245.05', commission);
  const released = make('onboard-status');
  check(released.includes(`bancassurance v2: 9/9 accepted, 9/9 certified, released in ${hash2.slice(0, 12)}`),
    'the status line says v2 is released', released);
  // The rollback: the first candidate's hash names v1's warehouse, so v1's definitions answer on v1's rows again
  make('release', `HASH=${hash}`);
  const rolledGwp = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=Gross written premium for June 2025');
  check(rolledGwp.includes('figure 1,200 from premium.premium_amount'),
    'after the rollback, Gross written premium for June 2025 answers 1,200 again', rolledGwp);
  const rolledInForce = make('agent', 'PRINCIPAL=analyst_enterprise', 'AGENT_ARM=treatment', 'Q=In force premium for June 2025');
  check(rolledInForce.includes('figure 2,000 from premium_snapshot.in_force_premium'),
    'after the rollback, In force premium for June 2025 is 2,000 again, v1\'s definitions on v1\'s rows', rolledInForce);
  // The reset reloads the graph the API holds open: stop the API first, as the README's step 14 does
  await stop(state.meridian);
  make('reset-onboard', 'UNIT=bancassurance');
}

async function semanticMarkdown() {
  // The Markdown form goes through the drafting flow: on the labelled stand-in, whose one refused recipe is corrected
  // on retry, then with no model, where rules read only the documented table and the prose becomes open requests.
  const markdown = ['UNIT=bancassurance', 'LABEL=Bancassurance', 'SEMANTIC=examples/bancassurance-markdown', 'PRINCIPAL=knowledge_engineer'];
  const unitDir = path.join(DEMO, 'clients/meridian/units/bancassurance');
  run('make', ['source', ...markdown], { MODEL_PROVIDER: 'mock' });
  run('make', ['draft', 'UNIT=bancassurance', 'PRINCIPAL=knowledge_engineer'], { MODEL_PROVIDER: 'mock' });
  const history = fs.readFileSync(path.join(unitDir, 'history.jsonl'), 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
  const recipes = history.find((entry) => entry.step === 'draft-recipes');
  check(recipes && recipes.detail.made_by === 'model' && recipes.detail.tries === 2,
    'the stand-in\'s refused recipe is corrected on retry: draft-recipes, tries 2', JSON.stringify(recipes));
  const modelDraft = fs.readFileSync(path.join(unitDir, 'draft.yaml'), 'utf8');
  check((modelDraft.match(/made_by: model/g) || []).length === 4 && modelDraft.includes('model: mock:mock') &&
    modelDraft.includes('title: Gross written premium for June 2025') && modelDraft.includes("figure: '2000.50'") &&
    modelDraft.includes('id: ontology-written_premium-fibo-monetary-amount'),
    'the source, the term, Gross written premium for June 2025 (2,000.50) and the term\'s mapping are made_by: model', modelDraft);
  const standardsStep = history.find((entry) => entry.step === 'draft-standards');
  check(standardsStep && standardsStep.detail.made_by === 'model' && standardsStep.detail.tries === 2 &&
    standardsStep.detail.reason.includes('is not one of the platform\'s preloaded concepts'),
    'the stand-in\'s IRI the platform does not preload is refused and corrected on retry: draft-standards, tries 2', JSON.stringify(standardsStep));
  make('reset-onboard', 'UNIT=bancassurance');
  run('make', ['source', ...markdown], { MODEL_PROVIDER: 'none' });
  run('make', ['draft', 'UNIT=bancassurance', 'PRINCIPAL=knowledge_engineer'], { MODEL_PROVIDER: 'none' });
  const rulesDraft = fs.readFileSync(path.join(unitDir, 'draft.yaml'), 'utf8');
  check((rulesDraft.match(/made_by: rules/g) || []).length === 1 && !rulesDraft.includes('made_by: model'),
    'with no model, rules draft only the source', rulesDraft);
  const requests = make('onboard-status');
  check(requests.includes('Restate the prose in semantic.md') && requests.includes('Define written_premium as plain conditions'),
    'the skipped prose and the prose recipe are open requests', requests);
  make('reset-onboard', 'UNIT=bancassurance');
}

// Industry standards and data policies (#57): Meridian's NNA and AUM are mapped to the platform's preloaded standards;
// a new unit's terms are drafted mappings to them, and its personal column a candidate policy.
const NNA_MAP = 'What is NNA?';
async function industryStandards() {
  make('source', 'UNIT=bancassurance', 'LABEL=Bancassurance', 'SEMANTIC=examples/bancassurance-osi', 'PRINCIPAL=knowledge_engineer');
  make('draft', 'UNIT=bancassurance', 'PRINCIPAL=knowledge_engineer');
  const drafted = fs.readFileSync(path.join(DEMO, 'clients/meridian/units/bancassurance/draft.yaml'), 'utf8');
  check(drafted.includes('id: ontology-written_premium-fibo-monetary-amount') && drafted.includes('id: ontology-in_force_premium-fibo-monetary-amount')
    && drafted.includes('relation: broadMatch') && drafted.includes('structural: a figure in GBP is an amount of money'),
    'each term is drafted a broadMatch mapping to FIBO\'s monetary amount: a figure in GBP is an amount of money', drafted);
  check(drafted.includes('id: policy-policyholder-name') && drafted.includes('column: bancassurance.policyholder.name') &&
    drafted.includes('iri: https://w3id.org/dpv#PersonalData') && (drafted.match(/kind: policy/g) || []).length === 1,
    'the policyholder\'s name, and no key, status, date or amount, is a candidate policy mapped to DPV\'s personal data', drafted);
  const asked = make('onboard-status');
  check(asked.includes("Confirm there's no industry equivalent for Written Premium, or name the nearest.") &&
    asked.includes("Confirm there's no industry equivalent for In Force Premium, or name the nearest."),
    'no preloaded concept matches a premium by name: the steward is asked to confirm, or name the nearest', asked);
  make('accept', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=business_team');
  make('certify-draft', 'UNIT=bancassurance', 'ITEM=all', 'PRINCIPAL=steward_glossary');
  const closed = make('onboard-status');
  check(!closed.includes("Confirm there's no industry equivalent"), 'certifying each term\'s mapping closes both requests', closed);
  state.meridian = await serve(['serve', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  await state.page.close();
  const page = state.page = await open(state.context, PORT);
  await signIn(page, 'business_team');
  const card = page.locator('.unit-card').filter({ hasText: 'Bancassurance' });
  await card.first().waitFor();
  const rows = await card.first().locator('table.progress tr').evaluateAll((r) => r.slice(1).map((x) => [...x.cells].map((c) => c.textContent.trim())));
  const row = (name) => (rows.find((r) => r[0] === name) || []).slice(1).join('/');
  check(row('Industry ontology') === '2/2/2' && row('Governance') === '1/1/1',
    'the card\'s progress by sub-graph: two industry-ontology items and one governance item, accepted and certified', rows);
  await signIn(page, 'analyst_enterprise');
  check(await page.inputValue('#arm') === 'graph', 'as analyst_enterprise, on the graph arm', await page.inputValue('#arm'));
  const nna = await ask(page, NNA_MAP);
  check(nna.behaviour === 'glossary_definition', `"${NNA_MAP}": the pack's glossary definition, as step 2 gives it`, nna.text);
  const nnaMap = await tab(page, 'map');
  check(/industry standards/i.test(nnaMap) && nnaMap.includes('cash flow') && nnaMap.includes('monetary amount') &&
    nnaMap.includes('MoneyTransfer') && nnaMap.includes('relatedMatch') && nnaMap.includes('master_2026Q2') &&
    nnaMap.includes('Financial Industry Business Ontology (FIBO)') && nnaMap.includes('30.1 (2026-09-16)') &&
    nnaMap.includes('https://spec.edmcouncil.org/fibo/ontology/FND/Accounting/CashFlows/CashFlow'),
    'the Map tab lists NNA\'s industry standards: cash flow and monetary amount (broadMatch, FIBO), MoneyTransfer (relatedMatch, schema.org)', nnaMap);
  check(!(await page.locator('#tab-map a[href*="edmcouncil"]').count()), 'each IRI is plain text, not a link');
  const w16 = await ask(page, W16);
  figure(w16, '7,641,422.24', `"${W16}", as step 6 gives it`);
  const aumMap = await tab(page, 'map');
  check(/industry standards/i.test(aumMap) && aumMap.includes('market value') && aumMap.includes('holding') &&
    aumMap.includes('monetary amount'), 'the Map tab lists AUM\'s: market value and monetary amount (broadMatch), holding (relatedMatch)', aumMap);
  await stop(state.meridian);
  make('reset-onboard', 'UNIT=bancassurance');
}

const STEPS = [
  ['Build and serve', buildAndServe],
  ['The Wealth questions', wealthQuestions],
  ['Retail and Wealth, Option B', optionB],
  ['A pack edit through the onboarding gate (steps 4 to 8)', packEdit],
  ['A failed check leaves a candidate', failedCheck],
  ['AUM, added later by a pack edit alone', aum],
  ['The overlay survives a rebuild', overlay],
  ['Two profiles side by side', twoProfiles],
  ['A new client, onboarded from its pack alone', newClient],
  ['A new business unit, from a DDL file and its data, on the page', ddlBusinessUnit],
  ['Point at an invented database (a new client)', inventedDatabaseClient],
  ['Rich answers, a page switch', richAnswers],
  ['The improvement job', improveJob],
  ['A new business unit, from semantic files, documents and client figures', semanticBusinessUnit],
  ['Industry standards and data policies', industryStandards],
];

// The walkthrough dealt into parts, each run alone from a clean build (PART=i/N, the full check's legs e2e-i/N): every
// step, each half of step 14 (14a, 14b) and the discovery portal alone in exactly one part. Steps 10 to 13 stay together
// (12 and 13 ask what 10 onboards); each part takes its steps in the README's order. Without PART the whole walkthrough
// runs, in a row.
const HALVES = { 14: [['14a', semanticOsi], ['14b', semanticMarkdown]] };
const PARTS = [
  [1, 2, 3, 4, 5, 'portal'],
  [6, 7, 8, 9, '14b', 15],
  [10, 11, 12, 13],
  ['14a'],
];
const PART = process.env.PART || '';
const PIECES = [...STEPS.flatMap((_, i) => (HALVES[i + 1] || [[i + 1]]).map(([id]) => id)), 'portal'];

// A part that does not start at step 1, or that skips the piece before one of its own, starts as that piece would find
// the demo in a row: Meridian served on PORT with its page open (what step 1 leaves), or, before step 14's second half
// and step 15, stopped (what the first half leaves). It checks nothing, so the parts' checks add up to the whole's.
const STOPPED_BEFORE = new Set(['14b', 15]);
async function arrive(id) {
  if (STOPPED_BEFORE.has(id)) {
    if (state.meridian) { await stop(state.meridian); state.meridian = null; }
    if (!state.page) state.page = await state.context.newPage();
    return;
  }
  if (state.meridian && !state.meridian.exited) return;
  const journal = path.join(DEMO, profile('meridian').store, 'overlay', 'decisions.jsonl');
  if (fs.existsSync(journal) && fs.readFileSync(journal, 'utf8').trim() !== '')
    throw new Failure(`not a fresh steward queue (${journal}): make reset-overlay graph pipeline, with the API stopped`);
  if (fs.readFileSync(PACK, 'utf8').includes(ALIAS)) throw new Failure('the pack is not as committed: a takings alias is in it');
  say('(this part starts as step 1 leaves the demo: Meridian served, its page open)');
  state.meridian = await serve(['demo', 'DEMO_MODE=1', `PORT=${PORT}`], PORT);
  state.oldHash = profile('meridian').current;
  if (state.page) await state.page.close();
  state.page = await open(state.context, PORT);
}

// The part PART names, as its pieces (steps' numbers, step 14's halves, the portal); every piece, in a row, without it. A
// PART that is not i/N for the N parts above is refused before anything runs.
function chosenPart() {
  const all = PIECES;
  const dealt = PARTS.flat();
  if (dealt.length !== all.length || !all.every((s) => dealt.includes(s)))
    throw new Error(`PARTS deals ${JSON.stringify(dealt)}, not every step and the portal once`);
  if (!PART) return { label: '', steps: new Set(all) };
  const m = /^(\d+)\/(\d+)$/.exec(PART);
  if (!m || Number(m[2]) !== PARTS.length || Number(m[1]) < 1 || Number(m[1]) > PARTS.length) {
    console.error(`make demo-script: PART is i/${PARTS.length}, i from 1 to ${PARTS.length}, not '${PART}'`);
    process.exit(2);
  }
  return { label: PART, steps: new Set(PARTS[Number(m[1]) - 1]) };
}

// The discovery portal alone (issue #163), as the README's "Portal alone" row runs it: KGE_URL empty, its own catalogue
// file and its own people. In dark and in light, as the system asks: the paper, the navy bar, the switcher Discover alone;
// then the viewer's choice, set before the page draws and kept across a reload.
async function portalAlone() {
  const people = fs.mkdtempSync(path.join(require('os').tmpdir(), 'kge-e2e-portal-'));
  fs.writeFileSync(path.join(people, 'principals.json'), JSON.stringify([{ principal: 'reader', role: 'Reader', reads: ['retail'], processes: ['querying'] }]));
  run('node', ['portal/server/src/passwords.js', people]);
  const password = fs.readFileSync(path.join(people, 'passwords.txt'), 'utf8').trim().split(/\s+/)[1];
  const secret = fs.readFileSync(path.join(people, 'session_secret'), 'utf8').trim();
  state.portal = await serve(['portal', 'KGE_URL=', 'PORTAL_CATALOGUE_FILE=examples/meridian-portal/catalogue.yaml', `PORTAL_PORT=${PORT4}`], PORT4, HOST, {
    PORTAL_IDENTITIES_FILE: path.join(people, 'principals.json'), PORTAL_PASSWORDS_FILE: path.join(people, 'passwords.json'),
    PORTAL_SESSION_SECRET: secret, PORTAL_COOKIE_SECURE: 'false' });
  try {
    for (const mode of ['dark', 'light']) {
      const context = await state.browser.newContext({ locale: 'en-US', viewport: { width: 1400, height: 900 }, colorScheme: mode });
      await context.addInitScript(() => document.addEventListener('DOMContentLoaded', () => { window.THEME_AT_LOAD = document.documentElement.dataset.theme || 'system'; }));
      const page = await context.newPage();
      await page.goto(`http://${HOST}:${PORT4}/`);
      await page.getByRole('textbox', { name: 'Username' }).fill('reader');
      await page.getByRole('textbox', { name: 'Password' }).fill(password);
      await page.getByRole('button').filter({ hasText: /Login|Sign in/ }).first().click();
      await page.waitForURL(/\/home/);
      await page.locator('header.v3-topbar').waitFor();
      const seen = async () => page.evaluate(() => ({
        paper: getComputedStyle(document.body).backgroundColor,
        bar: getComputedStyle(document.querySelector('header.v3-topbar')).backgroundColor,
        scheme: getComputedStyle(document.documentElement).colorScheme,
        chosen: document.documentElement.dataset.theme || 'system',
        atLoad: window.THEME_AT_LOAD,
        stored: (() => { try { return localStorage.getItem('v3-theme'); } catch (e) { return 'unreadable'; } })(),
        products: [...document.querySelectorAll('nav[aria-label="Products"] a')].map((a) => `${a.textContent.trim()}${a.getAttribute('aria-current') ? '*' : ''}`),
      }));
      let now = await seen();
      check(now.paper === PAPER[mode] && now.bar === BAR && now.scheme === mode && now.chosen === 'system' && now.stored === null,
        `the portal alone in ${mode} mode, as the system asks: its paper, the navy bar`, now);
      check(JSON.stringify(now.products) === '["Discover*"]', 'the portal alone: its switcher shows Discover alone, current', now);
      if (mode === 'dark') {
        // The Ask button is on every page with the header, whatever PORTAL_ASK says; only the switcher's Ask tab (the
        // link to KGE) follows it, and alone there is no KGE to link to. Home, Explore and a data asset's page.
        const ask = page.locator('header.v3-topbar button.v3-btn-ai');
        check(await ask.count() === 1 && flat(await ask.textContent()) === 'Ask', 'the portal alone: the Ask button in its header on its home page', await ask.count());
        await ask.click();
        await page.locator('.show-chatbot aside.chatbot').waitFor();
        check(await ask.getAttribute('aria-expanded') === 'true', 'and it opens the Ask panel');
        await page.goto(`http://${HOST}:${PORT4}/search`);
        const row = page.locator('main.cx-explore [id^="cx-row-"]').first();
        await row.waitFor();
        const label = await ask.getAttribute('aria-label');
        check(await ask.count() === 1 && /^Ask about /.test(label || ''), `on Explore, Ask is scoped to the data assets in view: ${label}`, label);
        const id = (await row.getAttribute('id')).slice('cx-row-'.length);
        await page.goto(`http://${HOST}:${PORT4}/productDetails/${encodeURIComponent(id)}`);
        await page.locator('#DPDRoot h1').first().waitFor();
        const scoped = await ask.getAttribute('aria-label');
        check(await ask.count() === 1 && /^Ask about /.test(scoped || ''), `on a data asset's page, Ask is scoped to it: ${scoped}`, scoped);
        check(JSON.stringify((await seen()).products) === '["Discover*"]', 'and no Ask tab to KGE in the switcher, alone', (await seen()).products);
        await page.goto(`http://${HOST}:${PORT4}/home`);
        await page.locator('header.v3-topbar').waitFor();
      }
      const other = mode === 'dark' ? 'light' : 'dark';
      await page.locator('button.v3-theme').click();
      if (mode === 'light') await page.locator('button.v3-theme').click();
      now = await seen();
      check(now.chosen === other && now.stored === other && now.paper === PAPER[other] && now.scheme === other,
        `the portal: the viewer chooses ${other}, remembered as v3-theme=${other}`, now);
      await page.reload();
      await page.locator('header.v3-topbar').waitFor();
      now = await seen();
      check(now.atLoad === other && now.chosen === other && now.stored === other && now.paper === PAPER[other] && now.bar === BAR,
        `the portal after a reload: ${other} already set before the page drew, and kept`, now);
      await context.close();
    }
  } finally {
    await stop(state.portal);
    state.portal = null;
    fs.rmSync(people, { recursive: true, force: true });
  }
}

// Whatever happens, the pack is put back, the old hash released, the servers stopped.
async function cleanUp() {
  if (state.pack !== null) { fs.writeFileSync(PACK, state.pack); console.log('   (the pack put back as it was)'); }
  for (const s of [...SERVERS]) await stop(s);
  if (state.onboarding) {
    try { make('reset-onboard', 'UNIT=bancassurance'); } catch (e) { console.log(`   Bancassurance reset failed: ${e.message}`); }
    try { make('reset-onboard', 'UNIT=leasing'); console.log('   (the DDL onboarding step reset)'); }
    catch (e) { console.log(`   DDL onboarding reset failed: ${e.message}`); }
    state.onboarding = null;
  }
  try { make('reset-onboard', 'UNIT=bancassurance'); } catch (_) { /* already reset above, or absent */ }
  if (state.newClient) {
    try { make('reset-client', 'CLIENT=brackenridge'); } catch (e) { console.log(`   Brackenridge reset failed: ${e.message}`); }
    state.newClient = false;
  }
  if (state.released && state.oldHash) {
    try { make('release', `HASH=${state.oldHash}`); } catch (e) { console.log(`   make release HASH=${state.oldHash} failed: ${e.message}`); }
  }
  if (state.decided) console.log('   The steward decision of the overlay step is journaled: make reset-overlay graph pipeline sets it aside.');
  if (state.browser) await state.browser.close();
}

async function main() {
  process.chdir(DEMO);
  const part = chosenPart();
  fs.rmSync(OUT, { recursive: true, force: true });   // this run's logs and screenshots, none of an earlier run's
  console.log(`The README's Demo script${part.label ? `, part ${part.label} (steps ${[...part.steps].join(', ')})` : ''}, driven in Chromium: Meridian on ${HOST}:${PORT}, Thornbury on ${HOST2}:${PORT2}, Fellmoor on ${HOST}:${PORT3}.`);
  let n = 0;
  let ran = 0;
  try {
    state.browser = await playwright.chromium.launch({ headless: !HEADED, slowMo: HEADED ? 300 : 0 });
    state.context = await state.browser.newContext({ locale: 'en-US', viewport: { width: 1400, height: 900 } });
    // A run killed before its clean-up can leave an onboarded or partial Leasing unit; set it aside before step 1
    // builds, so the graph starts as the README's. Nothing to reset is a no-op; a unit onboarding did not make stops it.
    make('reset-onboard', 'UNIT=bancassurance');
    make('reset-onboard', 'UNIT=leasing');
    const brackenridge = spawnSync('make', ['reset-client', 'CLIENT=brackenridge'], { cwd: DEMO, env: env({}), encoding: 'utf8' });
    if (![0, 2].includes(brackenridge.status)) throw new Failure(`initial Brackenridge reset exited ${brackenridge.status}`);
    for (const [name, step] of STEPS) {
      n += 1;
      const pieces = part.label ? (HALVES[n] || [[n, step]]) : [[n, step]];
      if (!pieces.some(([id]) => part.steps.has(id))) continue;
      console.log(`\n${n}. ${name}`);
      for (const [id, piece] of pieces) {
        if (!part.steps.has(id)) { say(`(step ${id} runs in another part)`); continue; }
        const at = PIECES.indexOf(id);
        if (at > 0 && !part.steps.has(PIECES[at - 1])) await arrive(id);
        await piece();
      }
      ran += 1;
    }
    if (part.steps.has('portal')) {
      console.log('\nThe discovery portal, alone, in dark and light mode');
      await portalAlone();
    }
  } catch (e) {
    console.log(`\nFAILED at step ${n}: ${e instanceof Failure ? e.message : e.stack}`);
    const pages = [state.context, state.context2, state.context3].filter(Boolean).flatMap((c) => c.pages());
    for (const [i, page] of pages.entries()) {
      const shot = path.join(OUT, `step-${n}-page-${i + 1}.png`);
      try { await page.screenshot({ path: shot, fullPage: true }); console.log(`   screenshot: ${path.relative(DEMO, shot)}`); } catch (err) { /* closed */ }
    }
    await cleanUp();
    process.exit(1);
  }
  await cleanUp();
  const twoPorts = SIDE_BY_SIDE ? '' : ' (the two profiles one after the other, on one port)';
  if (part.label) console.log(`\nPart ${part.label} of the Demo script ran: ${ran} of its ${STEPS.length} steps, ${checked} checks${twoPorts}.`);
  else console.log(`\nThe Demo script ran end to end: ${STEPS.length} steps, ${checked} checks${twoPorts}.`);
}

process.on('SIGINT', async () => { await cleanUp(); process.exit(130); });
main();
