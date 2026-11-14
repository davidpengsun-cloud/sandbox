'use strict';

// The Ask page's conversation and general results (#316), on stubbed API responses: the page stores the history each
// answer returns and sends it back with a follow-up, resolves a clarification's one slot through a choice, draws the
// general result table (several dimensions and measures in the order returned, a null with its reason, never 0), keeps
// the old figure and rows cards, and drops the conversation when who asks, the arm or the purpose changes.

const assert = require('node:assert/strict');

const UNSAFE = '<img src=x onerror="window.injected=true">';
const SPAN = (turn, start, end) => ({turn, start, end});
const NAME = (id) => ({id, evidence: []});
const CAL = NAME('calendar');

function plan(measures) {
  return {schema: 'query-plan/v1', layout: 'aligned',
    measures: measures.map((id) => ({kind: 'measure', id, measure: NAME('m.' + id), context: NAME('ctx'), mode: 'current', filters: []})),
    dimensions: [], filters: [], time: {kind: 'all_available', calendar: CAL}, comparisons: [], derived: [], order: [], limit: null, obligations: []};
}

const BINDING = {release_identity: 'release-a', access_fingerprint: 'fp-a', products: [], purpose: 'analysis'};
const turn = (turn_id, question, p, clarification = null) => ({turn_id, question, plan: p, clarification, binding: BINDING});

function base(question, extra) {
  return {question, principal: 'analyst@example.com', arm: 'graph', trace: [], versions: {}, run_trace: {}, guard: {passed: true, checks: []},
    sql_source: 'query-plan-v1', ...extra};
}

const scalar = (kind, value) => (kind === 'null' ? {kind} : {kind, value});
const cell = (kind, value, reasons = []) => ({value: scalar(kind, value), citations: [], null_reasons: reasons});

const GENERAL_COLUMNS = [
  {id: 'region', kind: 'dimension', type: 'text', currency: null, lineage: []},
  {id: `month${UNSAFE}`, kind: 'dimension', type: 'date', currency: null, lineage: []},
  {id: 'zeta_assets', kind: 'measure', type: 'decimal', currency: 'USD', lineage: ['m.zeta_assets']},
  {id: 'alpha_households', kind: 'count', type: 'integer', currency: null, lineage: ['m.alpha_households']},
];
const GENERAL_ROWS = [
  {panel: null, dimensions: [scalar('text', `North${UNSAFE}`), scalar('date', '2025-06-01')],
    cells: [cell('decimal', '1250.5'), cell('null', null, [`no observation for the period ${UNSAFE}`])]},
  {panel: null, dimensions: [scalar('text', 'South'), scalar('date', '2025-06-01')],
    cells: [cell('null', null, ['denominator is zero']), cell('integer', 0)]},
];

const Q1 = 'Assets and households by region and month';
const H1 = [turn('t1', Q1, plan(['zeta_assets', 'alpha_households']))];
const Q2 = '\u{1F4C8} instead by office';
const at = Array.from(Q2).indexOf('o');
const C2 = {spans: [SPAN('t2', at, at + 'office'.length)], slot: 'meaning',
  choices: [{id: 'office.account', label: `account office ${UNSAFE}`}, {id: 'office.advisor', label: "advisor's office"}]};
const H2 = [...H1, turn('t2', Q2, null, C2)];
const Q3 = "\u{1F4C8} instead by advisor's office";
const H3 = [...H2, turn('t3', Q3, plan(['zeta_assets']))];

async function askPlanner(browser) {
  const context = await browser.newContext({locale: 'en-US'});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const sent = [];
  const replies = [];
  let who = 'analyst@example.com';
  await require('./context_fixture.cjs')(context, () => {
    return {
      '/mode': {demo_mode: false, released: true},
      '/profile': {profile: 'meridian', client: 'meridian', title: 'Meridian', units: ['wealth']},
      '/presets': {presets: {graph: {arm: 'graph', paths: ['ask', 'answer'], resolution: 'rules', context: 'graph', sql_source: 'planner', citation_rule: 'shelves', renderer: 'template'},
        schema: {arm: 'schema', paths: ['answer'], resolution: 'rules', context: 'schema', sql_source: 'planner', citation_rule: 'shelves', renderer: 'template'}},
        agent_arms: {}, default: 'graph'},
      '/whoami': {id: who, role: 'analyst', reads: ['wealth'], processes: ['querying', 'building']},
      '/golden': {
        sets: [{name: 'golden', version: 'v1', questions: ['Wealth NNA for Q1 2025?', 'Wealth AUM for March 2025?', 'Corporate loan exposure?']}],
        question_units: {
          'Wealth NNA for Q1 2025?': ['wealth'],
          'Wealth AUM for March 2025?': ['wealth'],
          'Corporate loan exposure?': ['corporate'],
        },
        glossary: [
          {term: 'Net New Assets', units: ['wealth']},
          {term: 'Corporate Covenant', units: ['corporate']},
        ],
        retention_days: 30,
      },
      '/runtimes': {
        runtimes: [
          {id: 'wealth_bq', label: 'Wealth BigQuery runtime', certified: true, dialect: 'bigquery', tables: ['wealth.fact_flows']},
          {id: 'shadow_rt', label: 'Shadow runtime', certified: false, dialect: 'bigquery', tables: ['wealth.fact_flows']},
        ],
      },
      '/versions': {},
      '/healthz': {version: 'test'},
      '/steward/queue': [],
    };
  });
  await context.route('**/api/context/{ask,answer,agent}*', async route => {
    const request = route.request();
    sent.push({ path: new URL(request.url()).pathname.slice('/api/context'.length), body: request.postDataJSON() });
    const reply = replies.shift();
    assert.ok(reply, 'an unexpected question');
    if (reply.held) { await reply.held; delete reply.held; }
    if (reply.ndjson) {
      return route.fulfill({
        status: 200,
        headers: { 'content-type': 'application/x-ndjson' },
        body: reply.ndjson.map(ev => JSON.stringify(ev)).join('\n') + '\n',
      });
    }
    return route.fulfill(reply.status ? { status: reply.status, json: reply.json } : { json: reply });
  });

  async function ask(question, reply) {
    replies.push(reply);
    const before = sent.length;
    await page.fill('#q', question);
    await page.click('#ask');
    await answered(before);
    return sent.at(-1);
  }
  async function answered(before) {
    for (let i = 0; sent.length === before; i++) {
      assert.ok(i < 500, 'the page sent no question');
      await page.waitForTimeout(10);
    }
    await page.waitForFunction(() => !document.querySelector('#ask[aria-busy], #thread .exchange[aria-busy]'));
    assert.equal(sent.length, before + 1, 'one question at a time');
  }
  const newest = () => page.locator('#thread .exchange').first();

  try {
    await page.goto('http://kge-ask.test/context');
    await page.locator('#nav-ask').click();
    await page.waitForFunction(() => !document.querySelector('#ask').disabled);

    let req = await ask(Q1, base(Q1, {behaviour: 'combined', combined: true, figures: [], citations: [], not_cited: [], withheld: [],
      answer_text: 'plain text', phrased_by: 'template', result: {columns: GENERAL_COLUMNS, rows: GENERAL_ROWS}, history: H1}));
    assert.equal(req.path, '/ask');
    assert.ok(!('history' in req.body), 'the first question sends no history');
    const table = newest().locator('table.result');
    assert.equal(await table.count(), 1, 'a general result is drawn as one table');
    const heads = await table.locator('th').allTextContents();
    assert.deepEqual(heads.map((h) => h.replace(/\s+/g, ' ').trim()), ['region', `month${UNSAFE}`, 'Zeta assets (USD)', 'Alpha households'],
      'columns in the order the result gives');
    const rows = await table.locator('tr').evaluateAll((trs) => trs.slice(1).map((tr) => [...tr.cells].map((td) => td.textContent.trim())));
    assert.deepEqual(rows[0].slice(0, 2), [`North${UNSAFE}`, '2025-06-01']);
    assert.equal(rows[0][2], 'USD 1,250.50');
    assert.equal(rows[0][3], `no value: no observation for the period ${UNSAFE}`);
    assert.equal(rows[1][2], 'no value: denominator is zero', 'a null is shown with its reason');
    assert.ok(!/\b0\b/.test(rows[1][2]) && !/\b0\b/.test(rows[0][3]), 'a null is never shown as 0');
    assert.equal(rows[1][3], '0', 'an observed zero is shown as 0');
    assert.equal(await page.locator('#conversation').isVisible(), true, 'the page says a follow-up builds on the conversation');
    console.log('PASS: a general result renders several dimensions and measures in the returned order, nulls with their reasons');

    req = await ask(Q2, base(Q2, {behaviour: 'clarify', reason: `Choose the intended meaning ${UNSAFE}`, clarification: C2, history: H2}));
    assert.deepEqual(req.body.history, H1, 'a follow-up sends the history the last answer returned, unchanged');
    assert.equal(req.body.history[0].plan.schema, 'query-plan/v1');
    const card = newest().locator('.msg[data-behaviour="clarify"]');
    assert.match(await card.innerText(), /Which meaning of “office”\?/, 'the unresolved phrase, read by code point');
    const choices = card.locator('.choices button');
    assert.deepEqual(await choices.allInnerTexts(), [`account office ${UNSAFE}`, "advisor's office"], 'readable alternatives, escaped');
    console.log('PASS: a clarification shows the unresolved phrase and readable alternatives');

    replies.push(base(Q3, {behaviour: 'figure', figure: 1200, unit: 'wealth', column: 'aum.value', notes: [],
      result: {columns: [GENERAL_COLUMNS[2]], rows: [{panel: null, dimensions: [], cells: [cell('decimal', '1200')]}]}, history: H3}));
    const beforeChoice = sent.length;
    await choices.nth(1).click();
    await answered(beforeChoice);
    req = sent.at(-1);
    assert.equal(req.body.question, Q3, 'only the unresolved phrase is replaced by the chosen meaning');
    assert.deepEqual(req.body.history, H2, 'the choice resends the history ending in the clarification');
    assert.equal(await page.locator('#thread .choices button:not([disabled])').count(), 0, 'an answered clarification cannot be chosen again');
    assert.equal(await newest().locator('.fig').innerText(), '1,200.00', 'an answer with a figure keeps its figure card');
    assert.equal(await newest().locator('[data-currency]').innerText(), 'USD');
    assert.equal(await newest().locator('table.result').count(), 0);
    console.log('PASS: choosing a meaning resends the question with the history resolving only that slot');

    req = await ask('And by segment?', base('And by segment?', {behaviour: 'figure', rows: {Retail: 10, Private: 0}, group_by: 'segment', unit: 'wealth', column: 'aum.value', notes: []}));
    assert.deepEqual(req.body.history, H3);
    assert.deepEqual(await newest().locator('table.parts th').allTextContents(), ['segment', 'Figure'], 'grouped rows keep their old table');
    assert.equal(await newest().locator('table.result').count(), 0);
    assert.equal(await page.locator('#conversation').isVisible(), false, 'an answer without history ends the conversation');
    req = await ask('Total assets', base('Total assets', {behaviour: 'abstain', reason: 'Nothing to answer.', history: H1}));
    assert.ok(!('history' in req.body), 'an answer without history leaves none to send');
    console.log('PASS: old figure and rows shapes render as before; an answer without history ends the conversation');

    await page.locator('#settings summary').click();
    await page.selectOption('#purpose', 'assistant');
    req = await ask('Total assets again', base('Total assets again', {behaviour: 'abstain', reason: 'Nothing.', history: H1}));
    assert.ok(!('history' in req.body), 'a changed purpose sends no earlier history');
    assert.equal(req.body.purpose, 'assistant');
    await page.selectOption('#arm', 'schema');
    req = await ask('Total assets, schema', base('Total assets, schema', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', history: H1}));
    assert.equal(req.path, '/answer');
    assert.ok(!('history' in req.body), 'a changed arm sends no earlier history');
    req = await ask('Total assets, follow-up', base('Total assets, follow-up', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', history: H2}));
    assert.deepEqual(req.body.history, H1, 'on the same arm and purpose the history is sent');
    const stale = page.locator('#thread .exchange').first();
    who = 'other@example.com';
    await page.reload();
    await page.locator('#ask').waitFor({ state: 'attached' });
    await page.locator('#nav-ask').click();
    await page.locator('#settings summary').click();
    await page.selectOption('#arm', 'schema');
    await page.selectOption('#purpose', 'assistant');
    await page.waitForFunction(() => document.querySelectorAll('#thread .exchange').length === 0);
    assert.equal(await stale.count(), 0);
    req = await ask('Total assets, someone else', base('Total assets, someone else', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', principal: who, history: H1}));
    assert.ok(!('history' in req.body), 'another person never sends the last person’s history');
    req = await ask('Too long a conversation', {status: 422, json: {detail: 'Conversation history exceeds the interpretation bounds.'}});
    assert.deepEqual(req.body.history, H1);
    assert.match(await newest().innerText(), /Not answered/);
    req = await ask('Start again', base('Start again', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', principal: who, history: H1}));
    assert.ok(!('history' in req.body), 'history the server refused is not sent again');
    req = await ask('Set the arm quietly', base('Set the arm quietly', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', principal: who, history: H1}));
    await page.selectOption('#arm', 'graph');
    replies.push(base('Asked on another arm', {behaviour: 'abstain', reason: 'Nothing.', principal: who, history: H2}));
    const beforeQuiet = sent.length;
    await page.fill('#q', 'Asked on another arm');
    await page.click('#ask');
    await answered(beforeQuiet);
    assert.equal(sent.at(-1).path, '/ask');
    assert.ok(!('history' in sent.at(-1).body), 'history is sent only to the endpoint, arm and purpose it was made on');
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    replies.push({...base('Slow answer', {behaviour: 'abstain', reason: 'Nothing.', principal: who, history: H1}), held});
    const beforeSlow = sent.length;
    await page.fill('#q', 'Slow answer');
    await page.click('#ask');
    for (let i = 0; sent.length === beforeSlow && i < 500; i++) await page.waitForTimeout(10);
    await page.selectOption('#purpose', 'analysis');
    release();
    await page.waitForFunction(() => !document.querySelector('#ask[aria-busy], #thread .exchange[aria-busy]'));
    assert.equal(await page.locator('#conversation').isVisible(), false, 'an answer asked under the old purpose starts no conversation');
    req = await ask('After the slow answer', base('After the slow answer', {behaviour: 'abstain', reason: 'Nothing.', principal: who, history: H1}));
    assert.ok(!('history' in req.body), 'an answer that arrives after the purpose changed leaves no history');
    await page.click('[data-testid="rail-new-conversation"]');
    assert.equal(await page.locator('#conversation').isVisible(), false);
    req = await ask('Fresh', base('Fresh', {behaviour: 'abstain', reason: 'Nothing.', arm: 'schema', principal: who}));
    assert.ok(!('history' in req.body), 'New conversation drops the history');
    console.log('PASS: incompatible history is cleared when the person, the arm or the purpose changes, after a 422, or on request');

    await page.selectOption('#arm', 'graph');
    const panels = [{...GENERAL_ROWS[0], panel: `Underwriting ${UNSAFE}`, dimensions: GENERAL_ROWS[0].dimensions}, {...GENERAL_ROWS[1], panel: 'Claims'}];
    await ask('Premium and paid loss separately', base('Premium and paid loss separately', {behaviour: 'combined', combined: true, figures: [], citations: [], not_cited: [], withheld: [],
      answer_text: 'x', phrased_by: 'template', principal: who, result: {columns: GENERAL_COLUMNS, rows: panels}}));
    assert.deepEqual(await newest().locator('table.result caption').allTextContents(), [`Underwriting ${UNSAFE}`, 'Claims'], 'separate figures are labelled panels');

    await page.check('#rich');
    const display = {v: 1, headline: {text: 'A checked general result.', by: 'template'}, chart: null,
      chart_note: 'A general result has no declared chart.', queries: [], checked: null, notes: [],
      table: {caption: 'Checked result', cite: null, total: null,
        columns: [{key: 'region', label: 'region', type: 'text'}, {key: 'month', label: 'month', type: 'text'},
          {key: 'ratio', label: 'ratio', type: 'number'}, {key: '__sources__', label: 'Sources', type: 'text'},
          {key: '__notes__', label: 'No value because', type: 'note'}],
        rows: [[`North${UNSAFE}`, '2025-06', null, `ratio: pack:left${UNSAFE}, pack:right`, 'ratio: missing denominator']],
        cell_citations: [[[`pack:left${UNSAFE}`, 'pack:right']]], null_reasons: [[['missing denominator']]]}};
    await ask('Ratio by region and month', base('Ratio by region and month', {behaviour: 'figure', principal: who,
      result: {columns: [GENERAL_COLUMNS[0], {id: 'month', kind: 'dimension', type: 'text', currency: null, lineage: []},
        {id: 'ratio', kind: 'derived', type: 'decimal', currency: null, lineage: ['left', 'right']}],
        rows: [{panel: null, dimensions: [scalar('text', `North${UNSAFE}`), scalar('text', '2025-06')],
          cells: [{value: scalar('null'), citations: [`pack:left${UNSAFE}`, 'pack:right'], null_reasons: ['missing denominator']}]}]}, display}));
    const rich = newest().locator('table.rich-table');
    assert.deepEqual(await rich.locator('th').allTextContents(), ['region', 'month', 'ratio', 'Sources', 'No value because']);
    assert.deepEqual(await rich.locator('td').allTextContents(),
      [`North${UNSAFE}`, '2025-06', 'no value', `ratio: pack:left${UNSAFE}, pack:right`, 'ratio: missing denominator']);
    assert.equal(await newest().locator('.chart, table.rich-table tr th:has-text("Total")').count(), 0,
      'general display preserves dimensions, cell evidence and null reasons without a chart or total');
    await page.uncheck('#rich');

    const pathPayload = {
      nodes: [
        {id: 'question', kind: 'question', label: 'What was wealth NNA in Q1 2025?', state: 'done', time: '0.3 ms', certified: false, detail: 'Question: What was wealth NNA in Q1 2025?'},
        {id: 'terms', kind: 'terms', label: 'net new assets · 2025-Q1', state: 'done', time: '0.5 ms', certified: false, detail: 'Terms: net new assets\nPeriod: 2025-Q1'},
        {id: 'measure', kind: 'measure', label: 'fact_flows.nna_usd', state: 'done', time: '1.0 ms', certified: false, detail: 'Measure: wealth_nna'},
        {id: 'tables', kind: 'tables', label: 'wealth.fact_flows', state: 'done', time: '1.0 ms', certified: false, detail: 'Reference tables: wealth.fact_flows'},
        {id: 'access', kind: 'access', label: 'Access check (other@example.com)', state: 'done', time: '0.2 ms', certified: false, detail: 'Verdict: allowed'},
        {id: 'runtime', kind: 'runtime', label: 'Wealth BigQuery runtime', state: 'done', time: '1.0 ms', certified: true, detail: 'Runtime: wealth_bq\nCertified: yes'},
        {id: 'runtime:shadow_rt', kind: 'runtime', label: 'Shadow runtime', state: 'skipped', time: null, certified: false, reason: 'table wealth.dim_client has no IMPLEMENTS mapping in runtime shadow_rt', detail: 'Skipped runtime: shadow_rt'},
        {id: 'query', kind: 'query', label: 'Query (bigquery)', state: 'done', time: '12 ms', certified: false, detail: 'SELECT SUM(nna_usd) FROM wealth_fact_flows'},
        {id: 'figure', kind: 'figure', label: 'USD 2,500,000', state: 'done', time: '0.2 ms', certified: true, detail: 'Wealth NNA: USD 2,500,000.'},
      ],
      edges: [
        {from: 'question', to: 'terms', label: ''},
        {from: 'terms', to: 'measure', label: ''},
        {from: 'measure', to: 'tables', label: ''},
        {from: 'tables', to: 'runtime', label: ''},
        {from: 'access', to: 'runtime', label: 'access check'},
        {from: 'tables', to: 'runtime:shadow_rt', label: 'skipped'},
        {from: 'runtime', to: 'query', label: 'query & cost checks'},
        {from: 'query', to: 'figure', label: ''},
      ],
    };
    const graphPayload = JSON.parse(require('node:fs').readFileSync(require('node:path').resolve(__dirname, '../../portal/ui/src/pages/Context/fixtures/w01_graph_path.json'), 'utf8'));
    const usedNodes = graphPayload.nodes.filter(n => n.used !== false), usedEdges = graphPayload.edges.filter(e => e.used !== false);
    const firstOf = type => usedNodes.find(n => n.type === type);
    await ask('What was wealth NNA in Q1 2025?', base('What was wealth NNA in Q1 2025?', {
      behaviour: 'figure', principal: who, figure: 2500000, unit: 'wealth', column: 'fact_flows.nna_usd',
      sql: 'SELECT SUM(nna_usd) FROM wealth_fact_flows', path: pathPayload, graph_path: graphPayload,
    }));
    assert.equal(await page.locator('#detail').isVisible(), true, 'Behind the answer is always shown');
    await page.locator('#tabbtn-graph').click();
    const graphPanel = page.locator('#tab-graph');
    assert.equal(await graphPanel.isVisible(), true, 'Graph tab opens and displays the context-graph path');
    assert.ok((await graphPanel.innerText()).includes(`The path this answer read: ${usedNodes.length} nodes`), 'the chip counts the nodes the answer read');
    assert.equal(await graphPanel.locator('[data-node-id]').count(), usedNodes.length, 'only used nodes are drawn by default');
    assert.equal(await graphPanel.locator('[data-node-id="query"]').count(), 0, 'the pipeline spine is not drawn on the Graph tab');
    const contextNode = firstOf('Context');
    assert.equal(await graphPanel.locator(`[data-node-id="${contextNode.id}"]`).getAttribute('aria-pressed'), 'true', 'the Context node is selected by default');
    assert.equal(await graphPanel.locator('#path-detail pre').innerText(), contextNode.detail, 'its detail shows under the graph');
    assert.equal(await graphPanel.locator('.graph-stage .gnode-scd2').count(), usedNodes.filter(n => (n.badges || []).some(b => b.kind === 'SCD2')).length, 'each versioned table carries its SCD2 badge');
    assert.equal(await graphPanel.locator('.gedge[data-type="JoinRule"]').count(), usedEdges.filter(e => e.type === 'JoinRule').length, 'every join the answer made is drawn');
    const planner = usedEdges.find(e => e.planner);
    assert.equal(await graphPanel.locator(`[data-edge="${planner.from}->${planner.to}"]`).getAttribute('data-planner'), 'true', 'the planner match is drawn dashed');
    const nodeX = node => graphPanel.locator(`[data-node-id="${node.id}"]`).evaluate(el => el.getBoundingClientRect().left);
    const nodeY = node => graphPanel.locator(`[data-node-id="${node.id}"]`).evaluate(el => el.getBoundingClientRect().top);
    const xs = [];
    for (const type of ['Words', 'Acronym', 'Context', 'Filter', 'Table']) xs.push(await nodeX(firstOf(type)));
    assert.ok(xs.every((x, i) => i === 0 || x > xs[i - 1]), `groups are laid out left to right: ${xs}`);
    const lowestTable = Math.max(...await Promise.all(usedNodes.filter(n => n.type === 'Table').map(nodeY)));
    assert.ok(await nodeX(firstOf('Runtime')) === await nodeX(firstOf('Table')) && await nodeY(firstOf('Runtime')) > lowestTable
      && await nodeY(firstOf('Principal')) > lowestTable, 'the runtime sits under the tables and access in the bottom row');
    await graphPanel.locator(`[data-node-id="${firstOf('Runtime').id}"]`).focus();
    await page.keyboard.press('Enter');
    assert.equal(await graphPanel.locator('#path-detail .graph-detail-text').innerText(), firstOf('Runtime').detail, 'selecting the runtime node by keyboard shows its detail');
    assert.equal(await graphPanel.locator('#path-detail .codeblock pre').innerText(), firstOf('Runtime').sql, 'runtime detail shows the SQL as run');
    assert.equal(await graphPanel.getByRole('button', {name: 'Copy the query'}).count(), 1, 'runtime SQL can be copied');
    assert.equal(usedNodes.filter(n => n.type === 'Entity' && n.id === 'Entity:wealth_branch_division').length, 1, 'W01 retains its branch division entity');
    const join = usedEdges.find(e => e.type === 'JoinRule' && e.detail);
    await graphPanel.locator(`[data-edge-label="${join.from}->${join.to}"]`).click();
    assert.equal(await graphPanel.locator('#path-detail pre').innerText(), join.detail, 'selecting a join by its label shows its kind and condition');
    const unusedToggle = graphPanel.getByRole('checkbox', {name: `Also show what was read but not used (${graphPayload.unused})`});
    await unusedToggle.check();
    const unusedNode = graphPayload.nodes.find(n => n.used === false);
    const unusedCard = graphPanel.locator(`[data-node-id="${unusedNode.id}"]`);
    assert.ok(await unusedCard.getAttribute('data-used') === 'false' && /not used/i.test(await unusedCard.innerText()), 'the toggle shows what was read but not used, marked as such');
    assert.equal(await unusedCard.evaluate(el => getComputedStyle(el).opacity), '1', 'an unused card is not faded');
    await unusedToggle.uncheck();
    assert.equal(await unusedCard.count(), 0, 'unused nodes hide again');
    console.log('PASS: Graph tab renders the context-graph path by group, its badges, the unused toggle and node detail');

    // Slice 2 checks: activity line, Runtimes chip, suggestions, upward growth, 1360px two-column alignment, 390px phone stacking
    const activitySummary = newest().locator('.activity-summary');
    assert.match(await activitySummary.innerText(), /^Worked \d+\.\d s · \d+ steps ›$/, 'folded activity line shows time and step count');
    await newest().locator('.activity-line').evaluate(d => { d.open = true; });
    await page.locator('#tabbtn-sources').click();
    await newest().locator('.activity-graph-link').click();
    assert.equal(await page.locator('#tabbtn-graph').getAttribute('aria-selected'), 'true', 'activity line Graph tab link switches Behind the answer to Graph');
    assert.equal(await newest().locator('.answer-actions button').count(), 4, 'Copy · Cite · Send to review · Helpful? actions are shown');

    // Suggestions while typing: at most 2, ordered Sample -> Past -> Glossary, filtered to readable units
    await page.fill('#q', 'Wealth');
    const sugItems = page.locator('.ask-suggestions [role="option"]');
    assert.equal(await sugItems.count(), 2, 'suggestions show at most two items');
    assert.match(await sugItems.nth(0).innerText(), /Sample question/, 'golden sample tagged Sample question');
    assert.equal(await page.locator('.composer-ghost').isVisible(), true, 'ghost text shown for top prefix match');
    await page.fill('#q', 'Corporate');
    assert.equal(await sugItems.count(), 0, 'never suggests questions or terms about unreadable units');
    await page.fill('#q', 'Net New');
    assert.equal(await sugItems.count(), 1, 'glossary suggestion shown for readable unit');
    assert.match(await sugItems.nth(0).innerText(), /Glossary term/, 'glossary item tagged Glossary term');
    await page.locator('#q').press('Tab');
    assert.equal(await page.inputValue('#q'), 'What is Net New Assets?', 'Tab accepts highlighted suggestion');

    // Runtimes chip: visible to everyone, lists runtimes with certified marked, sends runtimes in request
    await page.locator('#runtimes-chip > summary').click();
    await page.locator('#runtimes-chip .runtime-option').first().waitFor();
    assert.match(await page.locator('#runtimes-chip').innerText(), /Certified/, 'certified runtime marked');
    await page.locator('#runtimes-chip input[type="checkbox"]').nth(1).check();
    req = await ask('Runtime-selected question', base('Runtime-selected question', {behaviour: 'figure', principal: who, figure: 42, unit: 'wealth'}));
    assert.deepEqual(req.body.runtimes, ['shadow_rt'], 'selected runtime sent in request');

    // Layout checks: at 1360px wide, two columns top and bottom edges line up; at phone width (390px), panel stacks under conversation with no horizontal overflow
    await page.setViewportSize({width: 1360, height: 900});
    const wideRects = await page.evaluate(() => {
      const left = document.querySelector('.ask-main-col').getBoundingClientRect();
      const right = document.querySelector('.ask-behind-col').getBoundingClientRect();
      return {topDiff: Math.abs(left.top - right.top), bottomDiff: Math.abs(left.bottom - right.bottom), leftRight: left.right <= right.left + 1};
    });
    assert.ok(wideRects.topDiff <= 2 && wideRects.bottomDiff <= 2 && wideRects.leftRight, `at 1360px wide, two columns top and bottom edges line up: ${JSON.stringify(wideRects)}`);

    const railFold = page.getByRole('button', {name: 'Hide conversations'});
    await railFold.click();
    const layoutKey = key => `${key}:${encodeURIComponent(who || 'anonymous')}`;
    const foldedRail = await page.locator('.ask-rail').evaluate((el, key) => ({width: el.getBoundingClientRect().width, expanded: document.querySelector('.rail-fold').getAttribute('aria-expanded'), stored: localStorage.getItem(key)}), layoutKey('kge.ask.railFolded'));
    assert.ok(foldedRail.width <= 60 && foldedRail.expanded === 'false' && foldedRail.stored === '1', `the conversation rail folds to a strip and is remembered: ${JSON.stringify(foldedRail)}`);
    await page.getByRole('button', {name: 'Show conversations'}).click();

    const splitter = page.getByRole('separator', {name: 'Resize the chat and Behind the answer'});
    const chatWidth = () => page.locator('.ask-main-col').evaluate(el => el.getBoundingClientRect().width);
    const chatBefore = await chatWidth(), shareBefore = Number(await splitter.getAttribute('aria-valuenow'));
    await splitter.focus();
    await page.keyboard.press('ArrowLeft');
    assert.ok(Number(await splitter.getAttribute('aria-valuenow')) === shareBefore - 2 && await chatWidth() < chatBefore, 'the splitter moves with the keyboard and narrows the chat');
    assert.equal(await page.evaluate(key => localStorage.getItem(key), layoutKey('kge.ask.chatShare')), String(shareBefore - 2), 'the divider position is remembered for this viewer');
    await page.keyboard.press('Enter');
    console.log('PASS: the conversation rail folds and the splitter resizes the columns');

    await page.setViewportSize({width: 390, height: 844});
    const phoneLayout = await page.evaluate(() => {
      const left = document.querySelector('.ask-main-col').getBoundingClientRect();
      const right = document.querySelector('.ask-behind-col').getBoundingClientRect();
      return {stacked: right.top >= left.bottom - 1, overflow: document.documentElement.scrollWidth > window.innerWidth};
    });
    assert.ok(phoneLayout.stacked && !phoneLayout.overflow, `at phone width, panel stacks under conversation and nothing scrolls sideways: ${JSON.stringify(phoneLayout)}`);
    console.log('PASS: Slice 2 layout, activity line, suggestions, Runtimes chip, and responsive viewports verified');

    // Slice 3 checks: NDJSON streaming events and Stop button
    await page.setViewportSize({width: 1360, height: 900});
    let releaseHold;
    const heldPromise = new Promise(r => { releaseHold = r; });
    replies.push({ held: heldPromise, json: base('Slow query to stop', { behaviour: 'figure', principal: who, figure: 99 }) });
    await page.fill('#q', 'Slow query to stop');
    await page.click('#ask');
    await page.locator('#stop-ask').waitFor();
    await page.click('#stop-ask');
    releaseHold();
    await page.waitForFunction(() => !document.querySelector('#ask[aria-busy]'));
    assert.match(await newest().innerText(), /Stopped\./, 'Stop button cancels the active query and marks turn Stopped.');

    const streamFinal = base('Streamed NNA question?', {
      behaviour: 'figure', principal: who, figure: 314159, unit: 'wealth', column: 'fact_flows.nna_usd',
      sql: 'SELECT SUM(nna_usd) FROM wealth_fact_flows', path: pathPayload, graph_path: graphPayload,
    });
    await ask('Streamed NNA question?', {
      ndjson: [
        { event: 'node_added', node: { id: 'question', kind: 'question', label: 'Streamed NNA question?', state: 'running', detail: 'Q' } },
        { event: 'state_changed', id: 'question', state: 'done', time: '0.2 ms' },
        { event: 'graph_summary', unused: graphPayload.unused },
        ...graphPayload.nodes.map(node => ({ event: 'graph_node_added', node })),
        ...graphPayload.edges.map(edge => ({ event: 'graph_edge_added', edge })),
        { event: 'answer', answer: streamFinal },
      ],
    });
    assert.match(await newest().innerText(), /314,159/, 'NDJSON stream final answer renders in conversation');
    assert.ok(await page.locator('#tabbtn-graph').getAttribute('aria-selected') === 'true'
      && await page.locator('#tab-graph [data-node-id]').count() === usedNodes.length, 'the graph events sent before the answer fill the Graph tab, which stays open when the answer lands');
    console.log('PASS: Slice 3 NDJSON streaming and Stop button verified');

    assert.equal(await page.locator('#thread img, #thread script').count(), 0, 'no markup from the API is rendered');
    assert.equal(await page.evaluate(() => window.injected), undefined);
    assert.deepEqual(errors, []);
    console.log('PASS: separate panels are captioned; every label, phrase, column id and cell is escaped');
  } finally {
    await context.close();
  }
}

module.exports = askPlanner;
if (require.main === module) {
  (async () => {
    const browser = await require('playwright').chromium.launch({headless: true});
    try { await askPlanner(browser); } finally { await browser.close(); }
  })().catch((error) => { console.error(error); process.exitCode = 1; });
}
