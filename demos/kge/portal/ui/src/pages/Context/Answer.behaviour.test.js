import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Answer, { CopyButton, WayForward, periodWords } from './Answer';
import Result, { general, ownSpans, resolvedQuestion } from './Result';

const me = { id: 'reader@example.com', reads: ['leasing'] };
const base = { question: 'Revenue', principal: me.id, arm: 'graph' };
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  vi.restoreAllMocks();
  if (clipboardDescriptor) Object.defineProperty(navigator, 'clipboard', clipboardDescriptor);
  else delete navigator.clipboard;
});

test.each([
  [{ behaviour: 'cite_certified', answer: 'Revenue means monthly takings.', cite: ['policy'] }, 'Revenue means monthly takings.'],
  [{ behaviour: 'glossary_definition', term: 'Revenue', definition: 'Gross takings', steward: 'steward', contexts: ['monthly'] }, 'certified through its context monthly'],
  [{ behaviour: 'glossary_definition', term: 'Revenue', definition: 'Gross takings' }, "No document defines it, so none is cited."],
  [{ behaviour: 'cite', cited_docs: [{ doc: 'policy', shelf: 'certified' }] }, 'policy'],
  [{ behaviour: 'cite', cite: ['fallback-policy'] }, 'fallback-policy'],
  [{ behaviour: 'abstain_route_steward', candidate_sources: ['candidate'], fact: { asserted_by: 'steward', asserted_on: 'today' }, notes: ['Needs a decision'] }, 'Asserted by steward on today.'],
  [{ behaviour: 'unknown_behaviour', reason: 'Unknown but preserved' }, 'Unknown behaviour'],
  [{ behaviour: 'figure', rows: { north: 0, south: 25 }, group_by: 'Region' }, 'Region'],
  [{ behaviour: 'figure', figure: null, context: { currency: 'GBP', time_mode: 'current', term: 'Revenue' } }, 'current'],
  [{ behaviour: 'figure', figure: 5, currency: 'USD', unit: 'leasing', column: 'rent.amount', context: { context: 'revenue', time_mode: 'as_reported' }, join_path: ['declared'] }, 'via declared'],
 ])('Answer renders governed behaviour and its evidence: %j', (answer, text) => {
  const { container } = render(<Answer answer={{ ...base, ...answer }} me={me} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent(text);
  expect(container.querySelector('[data-behaviour]')).toHaveAttribute('data-behaviour', answer.behaviour);
 });

test.each([undefined, ''])('certified definitions render governed answer_text without a legacy answer: %j', answer => {
  const definition = 'Money clients bring to the firm less money they take away. <script>untrusted</script>';
  const { container, rerender } = render(<Answer answer={{ ...base, behaviour: 'cite_certified', answer,
    answer_text: definition, cite: ['MD-NET-NEW-ASSETS'],
  }} me={me} />);
  expect(container.querySelector('.ans')).toHaveTextContent(definition);
  expect(screen.getByLabelText('Answer')).toHaveTextContent('MD-NET-NEW-ASSETS');
  expect(container.querySelector('script')).toBeNull();
  rerender(<Answer answer={{ ...base, behaviour: 'cite_certified', answer: 'Legacy definition', answer_text: definition }} me={me} />);
  expect(container.querySelector('.ans')).toHaveTextContent('Legacy definition');
  expect(container.querySelector('.ans')).not.toHaveTextContent(definition);
});

test('a certified agent definition renders its governed answer_text once', () => {
  const text = 'Net New Assets means money brought in less money taken away.';
  const { container } = render(<Answer answer={{ ...base, behaviour: 'cite_certified', answer_text: text,
    agent: { arm: 'reference', mode: 'mock' }, cite: ['MD-NET-NEW-ASSETS'],
  }} me={me} />);
  const answers = [...container.querySelectorAll('.ans')].filter(p => p.textContent.trim());
  expect(answers).toHaveLength(1);
  expect(answers[0]).toHaveTextContent(text);
});

test.each([
  [{ behaviour: 'blocked_access', denied_warehouses: ['private'], reason: 'private is unreadable' }, 'You may not read private. Ask about leasing instead'],
  [{ behaviour: 'blocked_access', denied_warehouses: ['private'], reason: 'Access refused' }, 'This question needs a unit you may not read'],
  [{ behaviour: 'refuse', reason: 'No certified join path' }, "Ask for each unit's figure on its own"],
  [{ behaviour: 'refuse', reason: 'Join refused', joins_refused: ['leasing/retail'] }, "Ask for each unit's figure on its own"],
  [{ behaviour: 'refuse', reason: 'Restricted personal data' }, 'Ask for the figure without the restricted columns'],
  [{ behaviour: 'refuse', reason: 'Will not sum these' }, 'Ask for each measure on its own'],
])('a refusal gives a safe, actionable explanation: %j', (answer, text) => {
  render(<Answer answer={{ ...base, ...answer }} me={me} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent(text);
  expect(screen.queryByRole('button', { name: 'Copy the figure' })).toBeNull();
});

test('a denial never invents a readable alternative or discloses an unnamed denied unit', () => {
  const { container, rerender } = render(<WayForward answer={{ denied_warehouses: ['hidden-unit'] }} />);
  expect(container).toHaveTextContent('Ask about the units you read instead');
  expect(container).not.toHaveTextContent('hidden-unit');
  rerender(<WayForward answer={{ reason: 'No suggestion available' }} me={me} />);
  expect(container).toBeEmptyDOMElement();
});

test('an answer error is escaped and cannot retain a figure', () => {
  const { container } = render(<Answer answer={{ ...base, error: '<script>Unavailable</script>', figure: 100 }} />);
  expect(screen.getByRole('alert')).toHaveTextContent('Not answered <script>Unavailable</script>');
  expect(container.querySelector('script, .fig')).toBeNull();
});

test('agent answers render top-level answer text and its citation links', () => {
  const onCite = vi.fn();
  render(<Answer answer={{ ...base, behaviour: 'abstain', purpose: 'analysis', trace: [], run_trace: {},
    answer_text: 'The agent answer cites [1]', agent: { arm: 'reference', mode: 'mock' },
  }} onCite={onCite} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent('The agent answer cites [1]');
  fireEvent.click(screen.getByRole('link', { name: '[1]' }));
  expect(onCite).toHaveBeenCalledWith('1');
});

test.each([true, false])('combined answers bind citations and show the %s SQL gate', passed => {
  const onCite = vi.fn(), onReview = vi.fn();
  render(<Answer answer={{ ...base, behaviour: 'combined', answer_text: 'Certified revenue [1]', guard: { passed }, sql_source: 'rules', phrased_by: 'rules',
    agent: { arm: 'reference', mode: 'mock' },
    figures: [{ unit: 'leasing', label: 'Revenue', kind: 'monthly', column: 'rent.amount', currency: 'GBP', value: 25, cite: 1 }, { label: 'No source', value: 0 }],
    citations: [{ n: 1, doc: 'policy', version: '2', title: 'Revenue policy', cited_as: 'authority', tier: 'gold' }],
    not_cited: [{ doc: 'unused' }], withheld: [{ doc: 'private' }], candidate_sources: ['candidate'],
  }} me={me} onCite={onCite} onReview={onReview} agentArms={{ reference: { side: 'graph on' } }} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent(`gate ${passed ? 'passed' : 'failed'}`);
  expect(screen.getByLabelText('Answer')).toHaveTextContent('policy v2');
  expect(screen.getByLabelText('Answer')).toHaveTextContent('reference, graph on');
  fireEvent.click(screen.getAllByRole('link', { name: '[1]' })[0]);
  expect(onCite).toHaveBeenCalledWith('1');
  fireEvent.click(screen.getByRole('link', { name: '1 cited · 1 not used · 1 held back' }));
  expect(onCite).toHaveBeenCalledWith('');
  fireEvent.click(screen.getByRole('link', { name: 'candidate' }));
  expect(onReview).toHaveBeenCalledWith('candidate');
});

test.each([
  ['sum_with_verdict', {}, 'commensurable'],
  ['sum_without_verdict', {}, 'no verdict'],
  ['parts_with_reason', { missing_parts: ['retail'] }, 'incomplete: no total'],
  ['parts_with_reason', {}, 'not commensurable'],
])('%s keeps parts and refuses to imply an unchecked total', (behaviour, extra, badge) => {
  const { container } = render(<Answer answer={{ ...base, behaviour, ...extra, figure: 25, agent: { arm: 'reference', mode: 'mock' },
    figures: [{ unit: 'leasing', value: 25, cite: 1, conversion: 'to GBP' }, { unit: 'retail', value: null, error: 'No certified value' }],
  }} me={me} onCite={vi.fn()} />);
  expect(container.querySelector('.parts')).toHaveTextContent('25');
  expect(container.querySelector('.parts')).toHaveTextContent('to GBP');
  expect(container.querySelector('.parts')).toHaveTextContent('No certified value');
  expect(container).toHaveTextContent(badge);
  expect(container).toHaveTextContent('reference, the reference agent');
});

test('an explicit parts plan preserves failed reasons and omits no-op conversions', () => {
  const { container } = render(<Answer answer={{ ...base, behaviour: 'parts_with_reason', plan: [{ unit: 'leasing', status: 'ok', value: 10, conversion: 'none' }, { unit: 'retail', status: 'error', reason: 'No join' }] }} />);
  expect(container.querySelector('.parts')).toHaveTextContent('10');
  expect(container.querySelector('.parts')).toHaveTextContent('No join');
  expect(container.querySelector('.parts')).not.toHaveTextContent('none');
});

test.each([
  ['result currency', { value: 10 }, { currency: 'USD' }, undefined, 'USD 10.00'],
  ['result count kind', { value: 14, currency: 'USD', kind: 'monthly' }, { kind: 'count' }, undefined, '14'],
  ['part count kind', { value: 14, kind: 'count' }, { currency: 'USD' }, undefined, '14'],
  ['part count aggregation', { value: 14, agg: 'count' }, { currency: 'USD' }, undefined, '14'],
  ['result count aggregation', { value: 14, currency: 'USD' }, { agg: 'count' }, undefined, '14'],
  ['part currency before result and answer', { value: 10, currency: 'EUR' }, { currency: 'USD' }, 'GBP', 'EUR 10.00'],
  ['result currency after a missing marker', { value: 10, currency: 'XXX' }, { currency: 'USD' }, 'GBP', 'USD 10.00'],
  ['answer currency after missing markers', { value: 10, currency: 'XXX' }, { currency: 'XXX' }, 'USD', 'USD 10.00'],
])('parts resolve %s before formatting', (source, part, column, currency, text) => {
  const { container } = render(<Answer answer={{ ...base, behaviour: 'parts_with_reason', figure: null, currency,
    plan: [{ unit: 'leasing', status: 'ok', ...part }], result: { columns: [{ kind: 'dimension' }, { kind: 'measure', ...column }] },
  }} />);
  expect(container.querySelector('.parts td.num').textContent).toBe(text);
});

test.each(['sum_with_verdict', 'sum_without_verdict'])('%s totals USD 10 and USD 20 as USD 30.00 from the plan', behaviour => {
  const { container } = render(<Answer answer={{ ...base, behaviour, figure: 30, plan: [
    { unit: 'leasing', status: 'ok', value: 10, currency: 'USD' },
    { unit: 'retail', status: 'ok', value: 20, currency: 'USD' },
  ] }} />);
  expect([...container.querySelectorAll('.parts td.num')].map(cell => cell.textContent)).toEqual(['USD 10.00', 'USD 20.00']);
  expect(container.querySelector('.figrow .cur').textContent).toBe('USD');
  expect(container.querySelector('.figrow .fig').textContent).toBe('30.00');
});

test.each([
  ['agreed currency', { currency: 'USD' }, ['USD 10.00', 'USD 20.00'], 'USD', '30.00'],
  ['count kind', { kind: 'count', currency: 'USD' }, ['10', '20'], '', '30'],
  ['count aggregation', { agg: 'count', currency: 'USD' }, ['10', '20'], '', '30'],
])('a total uses its parts resolved %s', (source, column, parts, currency, total) => {
  const { container } = render(<Answer answer={{ ...base, behaviour: 'sum_with_verdict', figure: 30, currency: 'GBP', plan: [
    { unit: 'leasing', status: 'ok', value: 10 },
    { unit: 'retail', status: 'ok', value: 20 },
  ], result: { columns: [{ kind: 'dimension' }, column, column] } }} />);
  expect([...container.querySelectorAll('.parts td.num')].map(cell => cell.textContent)).toEqual(parts);
  expect(container.querySelector('.figrow .cur').textContent).toBe(currency);
  expect(container.querySelector('.figrow .fig').textContent).toBe(total);
});

test.each([
  ['part', { parts: [{ currency: 'USD' }] }, 'USD', '30.00'],
  ['figure', { figures: [{ currency: 'USD' }] }, 'USD', '30.00'],
  ['result columns', { result: { columns: [{ kind: 'dimension' }, { kind: 'measure', currency: 'USD' }] } }, 'USD', '30.00'],
  ['result columns filling plan metadata', { plan: [{ status: 'ok', value: 30 }], result: { columns: [{ kind: 'measure', currency: 'USD' }] } }, 'USD', '30.00'],
  ['plan count kind', { plan: [{ currency: 'USD', kind: 'count' }] }, '', '30'],
  ['plan count aggregation', { plan: [{ currency: 'USD', agg: 'count' }] }, '', '30'],
  ['result count kind', { currency: 'USD', result: { columns: [{ kind: 'count' }] } }, '', '30'],
  ['result count overriding mapped kind', { parts: [{ kind: 'monthly', currency: 'USD' }], result: { columns: [{ kind: 'count' }] } }, '', '30'],
  ['missing currency marker', { currency: 'XXX', parts: [{ currency: 'USD' }] }, 'USD', '30.00'],
])('scalar figures use currency and kind from %s', (source, metadata, currency, figure) => {
  const { container } = render(<Answer answer={{ ...base, behaviour: 'figure', figure: 30, ...metadata }} />);
  expect(container.querySelector('.figrow .cur').textContent).toBe(currency);
  expect(container.querySelector('.figrow .fig').textContent).toBe(figure);
});

test.each(['GBP', undefined])('a total does not inherit USD when another part has currency %s', currency => {
  const { container } = render(<Answer answer={{ ...base, behaviour: 'sum_without_verdict', figure: 30, plan: [
    { unit: 'leasing', status: 'ok', value: 10, currency: 'USD' },
    { unit: 'retail', status: 'ok', value: 20, currency },
  ] }} />);
  expect(container.querySelector('.figrow .cur').textContent).toBe('');
  expect(container.querySelector('.figrow .fig').textContent).toBe('30');
});

const table = { columns: [{ key: 'region', label: 'Region', type: 'text' }, { key: 'amount', label: 'Amount', type: 'number' }, { key: 'cite', label: 'Source', type: 'cite' }],
  rows: [['North', -10, 1], ['South', 20, null], [null, null, null]], total: { label: 'Net', value: 10 } };
test.each([true, false])('rich answers preserve every row with chart=%s and working source links', chart => {
  const onCite = vi.fn();
  const { container } = render(<Answer answer={{ ...base, behaviour: 'sum_with_verdict', display: { headline: { text: 'Net revenue [1]' }, table,
    ...(chart ? { chart: { x: 'region', y: 'amount', max_bars: 1 } } : { chart_note: 'No certified chart' }),
    checked: { line: 'Checked against the figures', about: 'SQL gate' }, notes: ['Amounts in GBP'] } }} onCite={onCite} />);
  expect(container.querySelectorAll('.rich-table tbody tr')).toHaveLength(4);
  expect(container).toHaveTextContent('Checked against the figures');
  expect(container).toHaveTextContent('Amounts in GBP');
  expect(container).toHaveTextContent('commensurable');
  fireEvent.click(screen.getAllByRole('link', { name: '[1]', hidden: true })[0]);
  expect(onCite).toHaveBeenCalledWith('1');
  if (chart) {
    expect(screen.getByRole('figure')).toHaveAccessibleName('Bar chart drawn from the figures table');
    expect(container.querySelectorAll('.bar')).toHaveLength(1);
    expect(container).toHaveTextContent('1 of 3 bars shown; the table keeps every row.');
  } else expect(container).toHaveTextContent('No chart: No certified chart');
});

test('a zero chart uses a finite span and a rejected rich display falls back to the governed figure', () => {
  const { container, rerender } = render(<Answer answer={{ ...base, behaviour: 'figure', display: { table: { columns: table.columns, rows: [['Zero', 0, null]] }, chart: { x: 'region', y: 'amount' } } }} />);
  expect(container.querySelector('.bar')).toHaveAttribute('width', '0.5%');
  expect(container.querySelector('.zero')).toHaveAttribute('x1', '0%');
  rerender(<Answer answer={{ ...base, behaviour: 'figure', figure: 5, display: { table, error: 'Bad display' } }} />);
  expect(container.querySelector('.fig')).toHaveTextContent('5');
  expect(container.querySelector('.rich-table')).toBeNull();
});

test.each(['abstain', 'abstain_with_reason', 'parts_with_reason', 'refuse', 'blocked_access'])('the %s card keeps the walkthrough reason selector and shows no figure', behaviour => {
  const reason = 'No condition on the shared-client grain is certified.';
  const { container } = render(<Answer answer={{ ...base, behaviour, reason }} me={me} />);
  expect(container.querySelector('.msg .small')).toHaveTextContent(reason);
  expect(container.querySelector('.fig')).toBeNull();
  expect([...container.querySelectorAll('table.parts td')].some(cell => /\d/.test(cell.textContent))).toBe(false);
});

test('repeated abstentions keep the same reason while request activity timings change', () => {
  const reason = "'Digital Engagement Score' has no certified implementation in any warehouse. No figure is given.";
  const answer = {
    ...base,
    behaviour: 'abstain',
    reason,
    _live_ms: 200,
    path: { nodes: [{ id: 'terms', kind: 'terms', label: 'Digital Engagement Score', state: 'done', time: '0.1 ms' }] },
  };
  const { container, rerender } = render(<Answer answer={answer} />);
  const before = container.textContent;
  expect(container.querySelector('.msg .small')).toHaveTextContent(reason);
  rerender(<Answer answer={{ ...answer, _live_ms: 300, path: { nodes: [{ ...answer.path.nodes[0], time: '0.2 ms' }] } }} />);
  expect(container.textContent).not.toBe(before);
  expect(container.querySelector('.activity-summary')).toHaveTextContent('Worked 0.3 s');
  expect(container.querySelector('.msg')).toHaveAttribute('data-behaviour', 'abstain');
  expect(container.querySelector('.msg .small')).toHaveTextContent(reason);
  expect(container.querySelector('.fig')).toBeNull();
});

test.each([false, true])('copying a figure handles clipboard rejection=%s without propagating the card click', async fails => {
  const parent = vi.fn(), writeText = vi.fn(async () => { if (fails) throw new Error('Clipboard unavailable'); });
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<div onClick={parent}><CopyButton value={0} /></div>);
  fireEvent.click(screen.getByRole('button', { name: 'Copy the figure' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('0'));
  await waitFor(() => expect(screen.getByRole('button')).toHaveTextContent(fails ? 'Copy' : 'Copied'));
  expect(parent).not.toHaveBeenCalled();
});

const clarification = { ...base, behaviour: 'clarify', question: 'Revenue?', history: [{ turn_id: 'turn-1' }], clarification: { spans: [{ turn: 'turn-1', start: 0, end: 7 }], choices: [{ id: 'gross', label: 'Gross revenue' }] } };
test('clarification offers only choices bound to this question and stops the card click', () => {
  const onChoice = vi.fn(), parent = vi.fn();
  const { rerender } = render(<div onClick={parent}><Answer answer={clarification} onChoice={onChoice} /></div>);
  expect(screen.getByText('Which meaning of “Revenue”?')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Gross revenue' }));
  expect(onChoice).toHaveBeenCalledWith('Gross revenue');
  expect(parent).not.toHaveBeenCalled();
  rerender(<Answer answer={clarification} />);
  expect(screen.getByRole('button', { name: 'Gross revenue' })).toBeDisabled();
});

test.each([
  [{ turn: 'old', start: 0, end: 7 }], [{ turn: 'turn-1', start: -1, end: 7 }],
  [{ turn: 'turn-1', start: 0, end: 99 }], [{ turn: 'turn-1', start: 1.5, end: 7 }],
  [{ turn: 'turn-1', start: 7, end: 0 }], [{ turn: 'turn-1', start: 0, end: 7 }, { turn: 'turn-1', start: 1, end: 3 }],
].map(spans => ({ spans })))('invalid clarification spans never rewrite the question: %j', ({ spans }) => {
  const answer = { ...clarification, clarification: { ...clarification.clarification, spans } };
  expect(ownSpans(answer)).toEqual([]);
  expect(resolvedQuestion(answer, 'Gross revenue')).toBeNull();
  render(<Answer answer={answer} onChoice={vi.fn()} />);
  expect(screen.getByRole('button', { name: 'Gross revenue' })).toBeDisabled();
});

test('clarification replacement uses Unicode character positions and replaces repeated spans from the end', () => {
  const answer = { question: '💰 rev + rev', history: [{ turn_id: 't' }], clarification: { spans: [{ turn: 't', start: 2, end: 5 }, { turn: 't', start: 8, end: 11 }] } };
  expect(resolvedQuestion(answer, 'Revenue')).toBe('💰 Revenue + Revenue');
  expect(ownSpans({})).toEqual([]);
});

test.each([
  [{ months: ['2026-01'] }, 'January 2026'], [{ period: 'month 2026-02' }, 'February 2026'],
  [{ trace: [{ period: 'fiscal_year 2025' }], period: 'ignored' }, '2025'], [{}, ''],
])('period labels preserve the question period: %j', (answer, text) => expect(periodWords(answer)).toBe(text));

test('general results keep panels, dimensions, typed values, missing reasons and citations', () => {
  const onCite = vi.fn();
  const result = { columns: [{ id: 'Region', kind: 'dimension' }, { id: 'Revenue', kind: 'measure', currency: 'GBP', lineage: ['rent.amount'] }, { id: 'Flag', kind: 'measure' }, { id: 'Missing', kind: 'measure' }, { id: 'Absent', kind: 'measure' }], rows: [
    { panel: 'Current', dimensions: [{ kind: 'string', value: 'North' }], cells: [{ value: { kind: 'decimal', value: 10 }, citations: [1, 'policy'], null_reasons: ['partial'] }, { value: { kind: 'boolean', value: true } }, { value: { kind: 'null' }, null_reasons: ['not certified'] }] },
    { dimensions: [{ kind: 'null' }], cells: [{ value: { kind: 'integer', value: 0 } }, { value: { kind: 'boolean', value: false } }, { value: null }] },
  ] };
  const { container } = render(<Answer answer={{ ...base, behaviour: 'figure', result }} onCite={onCite} />);
  expect(container.querySelectorAll('.result')).toHaveLength(2);
  for (const text of ['Current', 'North', 'GBP 10.00 · partial', 'yes', 'no', 'no value: not certified', 'none', 'GBP 0']) expect(container).toHaveTextContent(text);
  expect(container.querySelector('td[title="policy"]')).toBeTruthy();
  fireEvent.click(screen.getByRole('link', { name: '[1]' }));
  expect(onCite).toHaveBeenCalledWith(1);
});

test('an integer result column with currency displays a whole count', () => {
  const result = { columns: [{ id: 'complaints', kind: 'measure', type: 'integer', currency: 'USD' }], rows: [
    { dimensions: [], cells: [{ value: { kind: 'integer', value: 14 } }] },
  ] };
  const { container } = render(<Result result={result} />);
  expect(container.querySelector('.result tbody td').textContent).toBe('14');
});

test('no observations is explicit and a scalar figure takes precedence over tabular results', () => {
  const { rerender, container } = render(<Result result={{ columns: [] }} />);
  expect(screen.getByText('No observations matched the checked request.')).toBeVisible();
  rerender(<Answer answer={{ ...base, behaviour: 'figure', figure: 0, result: { columns: [], rows: [] } }} />);
  expect(container.querySelector('.fig')).toHaveTextContent('0');
  for (const answer of [{}, { result: {} }, { result: { columns: [] }, rows: {} }, { result: { columns: [] }, figures: [{}] }, { result: { columns: [] }, parts: [] }]) expect(general(answer)).toBe(false);
});

test('activity line folds steps and links to the Graph tab, and answer actions handle Copy, Cite, Send to review, and Helpful?', async () => {
  const onCite = vi.fn(), onReview = vi.fn(), onOpenGraph = vi.fn();
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const path = {
    nodes: [
      { id: 'question', kind: 'question', label: 'Revenue?', state: 'done', time: '0.1 ms' },
      { id: 'figure', kind: 'figure', label: 'USD 25', state: 'done', time: '0.1 ms' },
    ],
    edges: [{ from: 'question', to: 'figure', label: '' }],
  };
  const { container, rerender } = render(
    <Answer
      answer={{
        ...base,
        behaviour: 'figure',
        figure: 25,
        answer_text: 'Revenue is USD 25.',
        citations: [{ n: 1, doc: 'D1', title: 'Doc 1' }],
        cite: ['D1'],
        candidate_sources: ['EM-004'],
        path,
        _live_ms: 200,
      }}
      me={me}
      onCite={onCite}
      onReview={onReview}
      onOpenGraph={onOpenGraph}
    />
  );
  const summary = container.querySelector('.activity-summary');
  expect(summary).toHaveTextContent('Worked 0.2 s · 2 steps ›');
  rerender(
    <Answer
      answer={{
        ...base,
        behaviour: 'figure',
        figure: 25,
        answer_text: 'Revenue is USD 25.',
        citations: [{ n: 1, doc: 'D1', title: 'Doc 1' }],
        cite: ['D1'],
        candidate_sources: ['EM-004'],
        path,
      }}
      me={me}
      onCite={onCite}
      onReview={onReview}
      onOpenGraph={onOpenGraph}
    />
  );
  expect(summary).toHaveTextContent('Worked · 2 steps ›');
  fireEvent.click(container.querySelector('.activity-graph-link'));
  expect(onOpenGraph).toHaveBeenCalledTimes(1);

  const actions = screen.getByRole('group', { name: 'Answer actions' });
  fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('Revenue is USD 25.'));
  await waitFor(() => expect(actions).toHaveTextContent('Copied'));

  fireEvent.click(screen.getByRole('button', { name: 'Cite' }));
  await waitFor(() => expect(onCite).toHaveBeenCalledWith(1));
  await waitFor(() => expect(actions).toHaveTextContent('Cited'));

  fireEvent.click(screen.getByRole('button', { name: 'Send to review' }));
  expect(onReview).toHaveBeenCalledWith('EM-004');
  expect(actions).toHaveTextContent('Sent to review');

  const helpfulBtn = screen.getByRole('button', { name: 'Helpful?' });
  expect(helpfulBtn).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(helpfulBtn);
  expect(helpfulBtn).toHaveAttribute('aria-pressed', 'true');
  expect(helpfulBtn).toHaveTextContent('Helpful ✓');
});

test.each([
  { candidate_sources: undefined, handler: true },
  { candidate_sources: [], handler: true },
  { candidate_sources: ['candidate'], handler: false },
])('review stays unsent and disabled when sending is unavailable: %j', ({ candidate_sources, handler }) => {
  const onReview = vi.fn();
  render(<Answer answer={{ ...base, behaviour: 'figure', figure: 10, candidate_sources }} onReview={handler ? onReview : undefined} />);
  const button = screen.getByRole('button', { name: 'Send to review' });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(onReview).not.toHaveBeenCalled();
  expect(button).toHaveTextContent('Send to review');
  expect(button).not.toHaveTextContent('Sent to review');
});
