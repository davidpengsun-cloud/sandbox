'use strict';

const assert = require('node:assert/strict');

async function checkSuggestions(browser) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const unsafe = '<img src=x onerror="window.injected=true">';
  const decisions = [];
  let queue = [
    {tag: 'suggested/high', family: 'suggested_mention', value: `Fiscal Year ${unsafe}`, confidence: .81,
      similarity: .81, definition: `Reporting calendar ${unsafe}`, passage: `April to June ${unsafe}`,
      evidence: `Steward review required ${unsafe}`, doc: 'EM-005', shelf: 'certified', chunk: 'EM-005#0'},
    {tag: 'suggested/lower', family: 'suggested_mention', value: 'Assets Under Management', confidence: .70,
      similarity: .70, definition: 'Managed assets', passage: 'Assets under advice are a different metric.',
      evidence: 'Steward review required', doc: 'MD-ASSETS-UNDER-ADVICE', shelf: 'certified', chunk: 'MD-ASSETS-UNDER-ADVICE#0'},
  ];
  await require('./cerebro_fixture.cjs')(context, () => {
    return {
      '/whoami': {id: 'steward@example.com', role: 'steward', reads: [], processes: ['certification'], cerebro_roles: ['steward']},
      '/api/cerebro/steward/queue': queue,
      '/api/cerebro/steward/queue?status=decided': [],
      '/api/cerebro/steward/gaps': {total: 0, gaps: []},
      '/api/cerebro/steward/candidate': {can: {}},
    };
  });
  await context.route('**/api/cerebro/steward/decide', async route => {
    const body = route.request().postDataJSON();
    decisions.push(body); queue = queue.filter(row => row.tag !== body.tag);
    return route.fulfill({ json: { decision: body.decision, superseded: body.tag, by: body.tag + '/human', mention_created: body.decision === 'accept' } });
  });

  try {
    await page.goto('http://kge-review.test/cerebro?mode=curate');
    const proposals = page.locator('section.curate-proposals');
    const rows = proposals.locator('[data-family="suggested_mention"]');
    await rows.first().waitFor();
    assert.equal(await rows.count(), 2);
    assert.match(await rows.first().innerText(), /Fiscal Year/);
    assert.match(await rows.first().innerText(), /81%/);
    assert.match(await rows.nth(1).innerText(), /70%/);
    assert.ok((await rows.first().innerText()).includes(unsafe));
    assert.equal(await page.locator('.cerebro-curate img, .cerebro-curate script').count(), 0);
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.match(await proposals.innerText(), /Similarity/i);
    assert.equal(await rows.first().locator('input').count(), 0);
    await rows.first().getByRole('button', {name: 'Accept', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('section.curate-proposals [data-family="suggested_mention"]').length === 1);
    assert.deepEqual(decisions[0], {tag: 'suggested/high', decision: 'accept'});
    assert.match(await proposals.locator('.curate-message').innerText(), /Accepted\. It goes into the next release with your name\./);
    await rows.first().getByRole('button', {name: 'Reject', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('section.curate-proposals').textContent.includes('Nothing waiting'));
    assert.deepEqual(decisions[1], {tag: 'suggested/lower', decision: 'reject'});
    assert.match(await proposals.locator('.curate-message').innerText(), /Rejected\. It goes into the next release with your name\./);
    assert.deepEqual(errors, []);
    console.log(`PASS: Cerebro Curate: suggested links display safe evidence and similarity; Accept/Reject send exact decisions and refresh the queue`);
  } finally {
    await context.close();
  }
}

async function mentionSuggestions(browser) {
  await checkSuggestions(browser);
}

module.exports = mentionSuggestions;
if (require.main === module) {
  (async () => {
    const browser = await require('playwright').chromium.launch({headless: true});
    try { await mentionSuggestions(browser); } finally { await browser.close(); }
  })().catch((error) => { console.error(error); process.exitCode = 1; });
}
