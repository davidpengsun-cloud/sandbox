'use strict';

// The Ask screen's layout (#440), measured in Chromium on stubbed answers at four layouts: 1440 px, 1600 px with the
// conversations panel folded and the chat widened, a narrow chat at 1440 px, and a 390 px phone. At each it measures
// that nothing scrolls sideways, that messages and answers wrap at one reading width that follows the chat column, that
// an answer's text, figure, table, notes and action row share one left edge, and that no word in Behind the answer or in
// an answer is broken across lines. After five turns it opens Examples ▾ and checks every example question shows in
// full, that the title row stays pinned while the conversation scrolls and the subtitle shows at the top, that Behind the
// answer is open with one title, one Copy, one new-conversation control and bubbles holding only the question, and that
// the by-region, by-quarter, declined and definition answers read as people write them.

const assert = require('node:assert/strict');

const WHO = 'analyst_enterprise.meridian@example.com';
const LONG = 'What were the certified net new assets for the wealth unit in March 2025, as reported at the month-end freeze, across every office and every client segment together?';
const EXAMPLES = Array.from({ length: 12 }, (_, i) => `Example ${i + 1}: what were net new assets for the wealth unit by region and by client segment in Q${(i % 4) + 1} 2025, as reported?`);
const DEFINITION = 'Net new assets are the inflows of client assets less the outflows over the period, excluding market movements and fees.';
const base = (question, extra) => ({ question, principal: WHO, arm: 'graph', trace: [], versions: {}, run_trace: {}, guard: { passed: true, checks: [] }, history: [], ...extra });
const cell = value => ({ value: { kind: 'decimal', value: String(value) }, citations: [], null_reasons: [] });
const TURNS = [
  [LONG, base(LONG, { behaviour: 'figure', figure: 5637481.28, currency: 'USD', unit: 'wealth', column: 'F_NNA_FIN.NNA_AM', context: { term: 'Net New Assets', time_mode: 'as_reported' }, trace: [{ period: 'month 2025-03', term: 'Net New Assets' }],
    notes: ["Finance's net new assets from the month-end rows marked frozen, over released months only, using month-end account and client-code versions, in US dollars, and never a figure a later restatement changed."] })],
  ['NNA for Q1 2025 by region', base('NNA for Q1 2025 by region', { behaviour: 'figure', trace: [{ period: 'calendar_quarter 2025-Q1', term: 'Net New Assets' }], context: { term: 'Net New Assets' },
    result: { columns: [{ id: 'office.region_code', kind: 'dimension' }, { id: 'nna', label: 'Net new assets', kind: 'measure', currency: 'USD' }],
      rows: [['MIDWEST', 1062980], ['NORTHEAST', 13437576.5], ['PACIFIC', 1176801.46]].map(([region, value]) => ({ panel: null, dimensions: [{ kind: 'text', value: region }], cells: [cell(value)] })) } })],
  ['NNA by quarter in 2025', base('NNA by quarter in 2025', { behaviour: 'figure', context: { term: 'Net New Assets' },
    result: { columns: [{ id: 'calendar_quarter', kind: 'dimension' }, { id: 'nna', label: 'Net new assets', kind: 'measure', currency: 'USD' }],
      rows: [['2025-Q1', 15677357.96], ['2025-Q2', 2000000]].map(([quarter, value]) => ({ panel: null, dimensions: [{ kind: 'text', value: quarter }], cells: [cell(value)] })) } })],
  ['NNA by; month', base('NNA by; month', { behaviour: 'abstain', reason: 'by; month. No figure is given.' })],
  ['What is NNA?', base('What is NNA?', { behaviour: 'glossary_definition', term: 'Net New Assets', definition: DEFINITION, steward: 'wealth steward', contexts: ['nna'], citations: [], cite: [],
    reason: "No document defines Net New Assets: this definition is the pack's glossary's (steward: wealth steward); it is certified through its context nna. What the question retrieved is listed as a reference, never cited as its definition." })],
];

async function measure(page) {
  return page.evaluate(() => {
    const px = n => Math.round(n * 10) / 10;
    const box = el => el.getBoundingClientRect();
    const textNodes = root => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: n => n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
      const out = [];
      while (walker.nextNode()) out.push(walker.currentNode);
      return out;
    };
    const textLeft = el => {
      const node = textNodes(el)[0];
      if (!node) return null;
      const range = document.createRange();
      const start = node.textContent.search(/\S/);
      range.setStart(node, start); range.setEnd(node, start + 1);
      return range.getBoundingClientRect().left;
    };
    const broken = root => {
      const found = [];
      for (const node of textNodes(root)) {
        const re = /[^\s-]+-?/g;
        let m;
        while ((m = re.exec(node.textContent))) {
          const range = document.createRange();
          range.setStart(node, m.index); range.setEnd(node, m.index + m[0].length);
          const lines = new Set([...range.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top)));
          if (lines.size > 1) found.push(m[0]);
        }
      }
      return found;
    };
    const visible = el => { const r = box(el); return r.width > 0 && r.height > 0; };
    const sideways = ['html', '#thread', '.ask-main-col', '.question-form', '.ask-composer-dock', '#detail', '.ask-workspace']
      .map(sel => [sel, document.querySelector(sel)]).filter(([, el]) => el && visible(el))
      .map(([sel, el]) => ({ sel, over: el === document.documentElement ? el.scrollWidth - window.innerWidth : el.scrollWidth - el.clientWidth })).filter(r => r.over > 0);
    const thread = document.querySelector('#thread');
    const reading = box(thread).width - parseFloat(getComputedStyle(thread).paddingLeft) - parseFloat(getComputedStyle(thread).paddingRight) - (thread.offsetWidth - thread.clientWidth);
    const widths = [];
    const edges = [];
    for (const card of document.querySelectorAll('#thread .answer-card')) {
      const c = box(card);
      widths.push(['card', px(c.width)]);
      for (const p of card.querySelectorAll(':scope > p, .ans')) widths.push(['text', px(box(p).width)]);
      const parts = [['badge row', null], ['figure', card.querySelector('.figrow')], ['label', card.querySelector('.figlabel')], ['table', card.querySelector('table th')], ['text', card.querySelector('.ans, :scope > p')],
        ['actions', card.querySelector('.answer-actions button')]].filter(([, el]) => el);
      for (const [name, el] of parts) edges.push({ name, offset: px(textLeft(el) - c.left) });
    }
    const bubbles = [...document.querySelectorAll('#thread .msg.q')];
    const longest = bubbles.reduce((a, b) => (box(a).width >= box(b).width ? a : b), bubbles[0]);
    const words = [...document.querySelectorAll('#detail > h2, #detail #about, #detail .answer-facts, #thread .answer-card p, #thread .msg.q, #thread th, #thread td')].flatMap(broken);
    const breakdown = [...document.querySelectorAll('#thread .result td.num')].find(td => td.textContent.includes('1,062,980'));
    return {
      sideways,
      reading: px(reading),
      widths: [...new Set(widths.map(([, w]) => w))],
      longBubble: longest ? px(box(longest).width) : null,
      edges,
      edgeSpread: px(Math.max(...edges.map(e => e.offset)) - Math.min(...edges.map(e => e.offset))),
      broken: words,
      breakdown: breakdown && { text: breakdown.textContent, align: getComputedStyle(breakdown).textAlign, gap: px(box(breakdown).right - parseFloat(getComputedStyle(breakdown).paddingRight) - textNodes(breakdown).map(n => { const r = document.createRange(); r.selectNodeContents(n); return r.getBoundingClientRect().right; }).at(-1)) },
    };
  });
}

async function askLayout(browser) {
  const context = await browser.newContext({ locale: 'en-US', viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const replies = [];
  await require('./context_fixture.cjs')(context, () => ({
    '/mode': { demo_mode: false, released: true },
    '/profile': { profile: 'meridian', client: 'meridian', title: 'Meridian', units: ['wealth'] },
    '/presets': { presets: { graph: { arm: 'graph', paths: ['ask'], resolution: 'rules', context: 'graph', sql_source: 'planner', citation_rule: 'shelves', renderer: 'template' } }, agent_arms: {}, default: 'graph' },
    '/whoami': { id: WHO, role: 'analyst', reads: ['retail', 'sme', 'corporate', 'wealth'], processes: ['querying'] },
    '/golden': { sets: [{ name: 'golden', version: 'v1', questions: EXAMPLES }], question_units: {}, glossary: [], retention_days: 30 },
    '/versions': {}, '/healthz': { version: 'test' }, '/steward/queue': [], '/runtimes': { runtimes: [] },
    '/map/Net%20New%20Assets': { contexts: [], implementations: [] },
  }));
  await context.route('**/api/context/{ask,answer,agent}*', route => {
    const reply = replies.shift();
    assert.ok(reply, 'an unexpected question');
    return route.fulfill({ json: reply });
  });
  const report = {};
  try {
    await page.goto('http://kge-ask-layout.test/context');
    await page.locator('#ask').waitFor();
    assert.equal(await page.locator('.ask-title-row h1').innerText(), 'Ask a governed question');
    for (const [question, answer] of TURNS) {
      const before = await page.locator('#thread .exchange').count();
      replies.push(answer);
      await page.fill('#q', question);
      await page.click('#ask');
      await page.waitForFunction(n => document.querySelectorAll('#thread .exchange').length === n + 1 && !document.querySelector('#thread .exchange[aria-busy]'), before);
    }

    const button = page.getByRole('button', { name: 'Examples ▾' });
    await button.click();
    const panel = page.locator('#examples');
    assert.equal(await panel.isVisible(), true, 'Examples ▾ opens the example questions');
    await panel.getByRole('button', { name: 'All 12 sample questions' }).click();
    const chips = await panel.locator('button[data-q]').evaluateAll(bs => bs.map(b => {
      const r = b.getBoundingClientRect(), p = b.closest('#examples').getBoundingClientRect();
      return { q: b.dataset.q, full: b.textContent.includes(b.dataset.q) && b.scrollWidth <= b.clientWidth && r.left >= p.left && r.right <= p.right };
    }));
    assert.equal(chips.length, EXAMPLES.length, 'every example question is listed');
    assert.deepEqual(chips.filter(c => !c.full), [], 'every example question shows in full');
    await page.keyboard.press('Escape');
    assert.equal(await panel.isVisible(), false, 'Escape closes Examples');
    assert.equal(await button.evaluate(b => b === document.activeElement), true, 'and returns focus to its button');

    const pinned = async () => page.evaluate(() => {
      const t = document.querySelector('#thread').getBoundingClientRect(), row = document.querySelector('.ask-title-row').getBoundingClientRect(), sub = document.querySelector('.ask-subtitle').getBoundingClientRect();
      return { rowAtTop: Math.abs(row.top - t.top) < 1, subtitleShown: sub.top >= row.bottom - 1 && sub.bottom <= t.bottom };
    });
    await page.locator('#thread').evaluate(t => { t.scrollTop = -t.scrollHeight; });
    const top = await pinned();
    await page.locator('#thread').evaluate(t => { t.scrollTop = -Math.round((t.scrollHeight - t.clientHeight) / 2); });
    const mid = await pinned();
    assert.ok(top.rowAtTop && top.subtitleShown, `at the top the title row and the subtitle show: ${JSON.stringify(top)}`);
    assert.ok(mid.rowAtTop && !mid.subtitleShown, `scrolled, the title row stays pinned and the subtitle has tucked away: ${JSON.stringify(mid)}`);
    report.pinned = { top, mid };

    assert.equal(await page.locator('#detail').isVisible(), true, 'Behind the answer is open');
    assert.equal(await page.locator('#detail > summary, details#detail').count(), 0, 'with no fold');
    assert.equal(await page.locator('#detail h2').count(), 1, 'and one title');
    assert.equal(await page.locator('#thread').getByRole('button', { name: /copy/i }).count(), TURNS.length, 'one Copy per answer');
    assert.equal(await page.locator('.answer-card').first().getByRole('button', { name: /copy/i }).count(), 1);
    assert.equal(await page.getByRole('button', { name: /new conversation/i }).count(), 1, 'one new-conversation control');
    const bubbles = await page.locator('#thread .msg.q').allInnerTexts();
    assert.deepEqual([...bubbles].reverse(), TURNS.map(([q]) => q), 'bubbles hold only the question');

    const cards = page.locator('#thread .exchange .answer-card');
    const text = async question => flat(await page.locator('#thread .exchange').filter({ has: page.locator('.msg.q', { hasText: question }) }).locator('.answer-card').innerText());
    const region = await text('NNA for Q1 2025 by region');
    assert.ok(/\bRegion\b/.test(region) && !/region_code/.test(region) && /USD 1,062,980\.00/.test(region) && /Q1 2025/.test(region) && !/calendar_quarter/.test(region), `the by-region answer reads as people write it: ${region}`);
    const quarter = await text('NNA by quarter in 2025');
    assert.ok(/Quarter/.test(quarter) && /Q1 2025/.test(quarter) && /USD 2,000,000\.00/.test(quarter) && !/2025-Q1/.test(quarter), `the by-quarter answer: ${quarter}`);
    const declined = await text('NNA by; month');
    assert.ok(declined.includes("No figure is given: the question's words “by” and “month” could not be applied.") && !declined.includes('by; month.'), `the declined answer is a full sentence: ${declined}`);
    const defined = await text('What is NNA?');
    assert.equal(defined.split(DEFINITION).length, 2, `the definition answer states its definition once: ${defined}`);
    assert.equal((defined.match(/No document defines/g) || []).length, 1, 'and its citation once');
    assert.ok(await cards.count() === TURNS.length);

    const layouts = [
      ['1440 px', { width: 1440, height: 900 }, async () => {}],
      ['1600 px, panel folded, chat widened', { width: 1600, height: 900 }, async () => {
        await page.getByRole('button', { name: 'Hide conversations' }).click();
        await page.getByRole('separator', { name: 'Resize the chat and Behind the answer' }).focus();
        await page.keyboard.press('End');
      }],
      ['1440 px, narrow chat', { width: 1440, height: 900 }, async () => {
        await page.getByRole('button', { name: 'Show conversations' }).click();
        await page.getByRole('separator', { name: 'Resize the chat and Behind the answer' }).focus();
        await page.keyboard.press('Home');
      }],
      ['390 px', { width: 390, height: 844 }, async () => {}],
    ];
    report.layouts = {};
    for (const [name, size, arrange] of layouts) {
      await page.setViewportSize(size);
      await arrange();
      await page.waitForTimeout(100);
      const m = await measure(page);
      report.layouts[name] = { chat: await page.locator('.ask-main-col').evaluate(el => Math.round(el.getBoundingClientRect().width)), ...m };
      assert.deepEqual(m.sideways, [], `${name}: nothing scrolls sideways`);
      assert.equal(m.widths.length, 1, `${name}: every answer and its text wraps at one width: ${JSON.stringify(m.widths)}`);
      assert.ok(Math.abs(m.widths[0] - m.reading) <= 1, `${name}: the reading width follows the chat column (${m.widths[0]} of ${m.reading})`);
      assert.ok(m.longBubble <= m.reading + 0.5 && m.longBubble >= m.reading - 30, `${name}: a long question wraps at the same width (${m.longBubble} of ${m.reading})`);
      assert.ok(m.edgeSpread <= 1, `${name}: one left edge: ${JSON.stringify(m.edges)}`);
      assert.deepEqual(m.broken, [], `${name}: no broken words`);
      assert.ok(m.breakdown && m.breakdown.text === 'USD 1,062,980.00' && m.breakdown.align === 'right' && Math.abs(m.breakdown.gap) <= 1, `${name}: a whole amount shows two decimals, right-aligned: ${JSON.stringify(m.breakdown)}`);
    }
    assert.deepEqual(errors, []);
    console.log(`PASS: the Ask screen keeps one reading width, one left edge, whole words and no sideways scroll at 1440 px, 1600 px folded, narrow chat and 390 px; Examples ▾ shows every question; the title row stays pinned\n${JSON.stringify(report)}`);
    return report;
  } finally {
    await context.close();
  }
}

function flat(text) { return String(text).replace(/\s+/g, ' ').trim(); }

module.exports = askLayout;
if (require.main === module) {
  (async () => {
    const browser = await require('playwright').chromium.launch({ headless: true });
    try { await askLayout(browser); } finally { await browser.close(); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
