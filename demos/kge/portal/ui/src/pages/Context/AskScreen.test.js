import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import Ask, { ASK_SUBTITLE, ASK_TITLE } from './Ask';
import Answer from './Answer';
import { GraphPath } from './BehindAnswer';
import { amount, businessName, count, declineText, glossaryNotices, money, periodText, readablePeriods, withoutNoCurrency } from './contextState';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const me = { id: 'analyst@example.com', reads: ['wealth'] };
const presets = { default: 'graph', presets: { graph: { arm: 'graph', paths: ['ask'] } }, agent_arms: {} };
const questions = Array.from({ length: 9 }, (_, i) => `Example question number ${i + 1} about net new assets by region?`);
const golden = { sets: [{ name: 'golden', version: 'v1', questions }] };
const css = readFileSync(join(process.cwd(), 'src/pages/Context/context.css'), 'utf8');
const rule = selector => {
  const at = css.indexOf(`  ${selector} {`);
  expect(at, selector).toBeGreaterThanOrEqual(0);
  return css.slice(at, css.indexOf('}', at));
};
const submit = text => {
  fireEvent.change(screen.getByLabelText('Question'), { target: { value: text } });
  fireEvent.click(screen.getByRole('button', { name: 'Ask', exact: true }));
};
const figure = (question, extra = {}) => ({ question, principal: me.id, arm: 'graph', behaviour: 'figure', figure: 1062980, currency: 'USD', history: [], ...extra });
afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage?.clear();
});

test('requirement 1: Examples is a dropdown at the right of the header that opens over the conversation, by keyboard too', async () => {
  const api = vi.fn(async (_path, body) => figure(body.question));
  const { container } = render(<Ask api={api} me={me} presets={presets} golden={golden} tester />);
  const row = container.querySelector('.ask-title-row');
  const button = within(row).getByRole('button', { name: 'Examples ▾' });
  expect(row.firstElementChild).toHaveTextContent(ASK_TITLE);
  expect(row.lastElementChild).toBe(button);
  expect(button).toHaveAttribute('aria-haspopup', 'dialog');
  expect(button).toHaveAttribute('aria-controls', 'examples');
  expect(button).toHaveAttribute('aria-expanded', 'false');
  expect(container.querySelector('#examples')).not.toBeVisible();
  const panel = container.querySelector('#examples');
  expect(panel).toHaveAttribute('role', 'dialog');
  expect(panel).toHaveAttribute('aria-label', 'Example questions');
  expect(panel.id).toBe('examples');
  expect(container.querySelector('#thread #examples, .ask-exchanges #examples')).toBeNull();
  expect(container.querySelector('details#examples')).toBeNull();
  for (let n = 1; n <= 5; n++) {
    submit(`Turn ${n}?`);
    await waitFor(() => expect(container.querySelectorAll('#thread .exchange')).toHaveLength(n));
  }
  expect(panel).not.toBeVisible();
  expect(button).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(button);
  expect(panel).toBeVisible();
  expect(button).toHaveAttribute('aria-expanded', 'true');
  fireEvent.click(within(panel).getByRole('button', { name: 'All 9 sample questions' }));
  for (const question of questions) expect(within(panel).getByRole('button', { name: question })).toHaveTextContent(question);
  fireEvent.keyDown(within(panel).getByRole('button', { name: questions[0] }), { key: 'Escape' });
  expect(panel).not.toBeVisible();
  expect(button).toHaveFocus();
  fireEvent.click(button);
  expect(panel).toBeVisible();
  fireEvent.mouseDown(screen.getByLabelText('Question'));
  expect(panel).not.toBeVisible();
  fireEvent.click(button);
  fireEvent.click(within(panel).getByRole('button', { name: questions[2] }));
  await waitFor(() => expect(api).toHaveBeenLastCalledWith('/ask', expect.objectContaining({ question: questions[2] })));
  expect(panel).not.toBeVisible();
});

test('requirement 2: the title row is pinned at the top of the conversation scroller and the subtitle scrolls away under it', () => {
  const { container } = render(<Ask api={vi.fn()} me={me} presets={presets} golden={golden} tester />);
  const thread = container.querySelector('#thread');
  const row = container.querySelector('.ask-title-row'), subtitle = container.querySelector('.ask-subtitle');
  expect(row.parentElement).toBe(thread);
  expect(subtitle.parentElement).toBe(thread);
  expect([...thread.children].at(-1)).toBe(row);
  expect([...thread.children].at(-2)).toBe(subtitle);
  expect(rule('#thread')).toMatch(/flex-direction:column-reverse/);
  expect(rule('#thread')).toMatch(/overflow-y:auto/);
  expect(rule('.ask-title-row')).toMatch(/position:sticky; top:0/);
  expect(rule('#view-ask .ask-subtitle')).not.toMatch(/sticky/);
  expect(rule('#thread')).toMatch(/min-height:min\(16rem, 60%\)/);
});

test('requirements 3 to 6: one reading width, a shrinking question box, words kept whole and one left edge', () => {
  expect(rule('#view-ask p')).toMatch(/max-width:none/);
  expect(rule('#view-ask p')).not.toMatch(/anywhere/);
  expect(rule('.msg.q')).toMatch(/max-width:100%/);
  expect(rule('.msg.q')).toMatch(/font-size:15px/);
  expect(rule('.ans')).toMatch(/font-size:15px/);
  expect(rule('.answer-card p, .answer-card .ans, .answer-card .bound')).toMatch(/font-size:15px/);
  expect(rule('.composer-input-wrap')).not.toMatch(/320px/);
  expect(rule('.composer-input-wrap')).toMatch(/min-width:0/);
  expect(rule('.answer-facts dd')).not.toMatch(/anywhere/);
  expect(rule('.answer-facts')).toMatch(/auto-fit/);
  expect(rule('.answer-card :is(th, td):first-child')).toMatch(/padding-left:0/);
  expect(rule('.answer-action:first-child')).toMatch(/padding-left:0/);
});

test('requirements 8 and 9: one Copy in the action row, one new-conversation control and a bubble holding just the question', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const api = vi.fn(async (_path, body) => figure(body.question, { history: [{ turn_id: 't1' }] }));
  const { container } = render(<Ask api={api} me={me} presets={presets} golden={golden} tester />);
  submit('NNA for March 2025?');
  const card = await screen.findByLabelText('Answer');
  const copies = within(container.querySelector('#thread')).getAllByRole('button', { name: /copy/i });
  expect(copies).toHaveLength(1);
  expect(copies[0].closest('.answer-actions')).toBeTruthy();
  fireEvent.click(copies[0]);
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('1,062,980.00'));
  expect(container.querySelector('#conversation')).not.toHaveAttribute('hidden');
  expect(screen.getAllByRole('button', { name: /new conversation/i })).toHaveLength(1);
  expect(container.querySelector('#new-conversation')).toBeNull();
  const bubble = container.querySelector('.msg.q');
  expect(bubble.textContent).toBe('NNA for March 2025?');
  expect(bubble).not.toHaveTextContent(me.id);
  expect(bubble).not.toHaveTextContent('analysis');
  expect(container.querySelector('#about')).toHaveTextContent(`as ${me.id} for analysis`);
  expect(card).toHaveTextContent('USD');
});

test('requirement 10: amounts show two decimals and sit right-aligned, counts stay whole', () => {
  expect(money(1062980)).toBe('1,062,980.00');
  expect(money(1176801.456)).toBe('1,176,801.46');
  expect(count(1040)).toBe('1,040');
  const result = { columns: [{ id: 'office.region_code', kind: 'dimension' }, { id: 'nna', label: 'Net new assets', kind: 'measure', currency: 'USD' }, { id: 'households', kind: 'count', type: 'integer' }], rows: [
    { dimensions: [{ kind: 'text', value: 'MIDWEST' }], cells: [{ value: { kind: 'decimal', value: '1062980' } }, { value: { kind: 'integer', value: 12 } }] },
    { dimensions: [{ kind: 'text', value: 'PACIFIC' }], cells: [{ value: { kind: 'decimal', value: '1176801.46' } }, { value: { kind: 'integer', value: 7 } }] },
  ] };
  const { container } = render(<Answer answer={{ question: 'NNA by region', principal: me.id, arm: 'graph', behaviour: 'figure', result }} />);
  const cells = [...container.querySelectorAll('.result tbody tr:first-child td')];
  expect(cells.map(c => c.textContent)).toEqual(['MIDWEST', 'USD 1,062,980.00', '12']);
  expect(cells[1]).toHaveClass('num');
  expect(cells[0]).not.toHaveClass('num');
  expect(container.querySelectorAll('.result thead th')[1]).toHaveClass('num');
  expect(rule('.num')).toMatch(/text-align:right/);
});

test('requirement 10: a breakdown keeps its currency on every amount, right-aligned', () => {
  const { container } = render(<Answer answer={figure('NNA by region', { figure: undefined, rows: { MIDWEST: 1062980, PACIFIC: 1176801.46 }, group_by: 'office.region_code' })} />);
  expect([...container.querySelectorAll('.parts th')].map(th => th.textContent)).toEqual(['Region', 'Figure (USD)']);
  expect([...container.querySelectorAll('.parts td.num')].map(td => td.textContent)).toEqual(['USD 1,062,980.00', 'USD 1,176,801.46']);
});

test('requirement 11: readable column names, periods as people write them and a decline in a full sentence', () => {
  expect(businessName('office.region_code')).toBe('Region');
  expect(businessName('region.region_name')).toBe('Region');
  expect(businessName('calendar_quarter')).toBe('Quarter');
  expect(businessName('Net new assets')).toBe('Net new assets');
  expect(periodText('calendar_quarter 2025-Q1')).toBe('Q1 2025');
  expect(periodText('fiscal_quarter FY25-Q2')).toBe('FY25-Q2');
  expect(declineText('by; month. No figure is given.', 'NNA by; month')).toBe("No figure is given: the question's words “by” and “month” could not be applied.");
  expect(declineText('by. No figure is given.', 'NNA by')).toBe("No figure is given: the question's word “by” could not be applied.");
  expect(declineText('by; month. No figure is given. Declared measures here are Net new assets; AUM. Which would you like?', 'Assets by month')).toBe("No figure is given: the question's words “by” and “month” could not be applied. Declared measures here are Net new assets; AUM. Which would you like?");
  expect(declineText("advisor's office. No figure is given.", "AUM by advisor's office")).toBe("No figure is given: the question's word “advisor's office” could not be applied.");
  expect(declineText('home branch; NNA; Q1 2025. No figure is given.', 'NNA for my home branch in Q1  2025?')).toBe("No figure is given: the question's words “home branch”, “NNA” and “Q1 2025” could not be applied.");
  for (const said of [declineText("advisor's office. No figure is given.", "AUM by advisor's office"), declineText('home branch; NNA. No figure is given.', 'NNA by home branch')]) expect(said).not.toMatch(/no certified|not declared|unknown/i);
  for (const [kept, question] of [['a declared path. No figure is given.', 'NNA across retail and wealth'], ['by; month. No figure is given.', 'NNA by region'], ['by; month. No figure is given.', undefined]]) expect(declineText(kept, question)).toBe(kept);
  for (const kept of ['The requested measure is not declared. No figure is given.', 'The requested measure is not declared. No figure is given. Declared measures here are AUM. Which would you like?', 'denominator is zero; partial', 'Stopped.']) expect(declineText(kept)).toBe(kept);
  const { container, rerender } = render(<Answer answer={figure('NNA for Q1 2025', { trace: [{ period: 'calendar_quarter 2025-Q1', term: 'Net New Assets' }] })} />);
  expect(container.querySelector('.figlabel')).toHaveTextContent('Net New Assets · Q1 2025');
  expect(container).not.toHaveTextContent('calendar_quarter');
  const result = { columns: [{ id: 'calendar_quarter', kind: 'dimension' }, { id: 'nna', label: 'Net new assets', kind: 'measure', currency: 'USD' }], rows: [{ dimensions: [{ kind: 'text', value: '2025-Q1' }], cells: [{ value: { kind: 'decimal', value: 5 } }] }] };
  rerender(<Answer answer={figure('NNA by quarter', { figure: undefined, result })} />);
  expect(container.querySelector('.result thead th')).toHaveTextContent('Quarter');
  expect(container.querySelector('.result tbody td')).toHaveTextContent('Q1 2025');
  rerender(<Answer answer={{ question: 'NNA across retail and wealth', principal: me.id, arm: 'graph', behaviour: 'abstain', reason: 'a declared path. No figure is given.' }} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent('a declared path. No figure is given.');
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('could not be applied');
  rerender(<Answer answer={{ question: 'NNA by; month', principal: me.id, arm: 'graph', behaviour: 'abstain', reason: 'by; month. No figure is given.' }} />);
  expect(screen.getByLabelText('Answer')).toHaveTextContent("No figure is given: the question's words “by” and “month” could not be applied.");
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('by; month');
});

test('requirement 12: a definition answer states the definition once, with its citation', () => {
  const definition = 'Net new assets are inflows less outflows of client assets.';
  const answer = { question: 'What is NNA?', principal: me.id, arm: 'graph', agent: { arm: 'graph', mode: 'mock' }, behaviour: 'glossary_definition', term: 'Net New Assets', definition, steward: 'wealth steward', contexts: ['nna'],
    answer_text: `Net New Assets: ${definition} This is the pack's glossary's definition: no document defines it, so nothing is cited as its definition.`,
    reason: "No document defines Net New Assets: this definition is the pack's glossary's (steward: wealth steward); it is certified through its context nna. What the question retrieved is listed as a reference, never cited as its definition." };
  render(<Answer answer={answer} />);
  const text = screen.getByLabelText('Answer').textContent;
  expect(text.split(definition)).toHaveLength(2);
  expect(text.match(/No document defines/g)).toHaveLength(1);
  expect(text).toContain("Source: the pack's glossary (steward: wealth steward), certified through its context nna.");
});

test('requirement 12: a glossary definition with withheld details keeps the governance notice, once', () => {
  const notice = 'Some certified definition details are (withheld) because they name data you may not read.';
  const definition = 'Net new assets are inflows to (withheld) less outflows.';
  const answer = { question: 'What is NNA?', principal: me.id, arm: 'graph', behaviour: 'glossary_definition', term: 'Net New Assets', definition, steward: 'steward.wealth@example.com', contexts: ['nna'],
    reason: `No document defines Net New Assets: this definition is the pack's glossary's (steward: steward.wealth@example.com); it is certified through its context nna. What the question retrieved is listed as a reference, never cited as its definition. ${notice}` };
  const { rerender } = render(<Answer answer={answer} />);
  const text = screen.getByLabelText('Answer').textContent;
  expect(text.split(notice)).toHaveLength(2);
  expect(text.split(definition)).toHaveLength(2);
  expect(text.match(/No document defines/g)).toHaveLength(1);
  expect(text).not.toContain('What the question retrieved');
  expect(glossaryNotices(answer.reason)).toBe(notice);
  rerender(<Answer answer={{ ...answer, reason: answer.reason.replace(` ${notice}`, '') }} />);
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('withheld) because');
  expect(document.querySelector('.definition-notice')).toBeNull();
});

test('requirement 12: a certified definition names its citation once', () => {
  render(<Answer answer={{ question: 'What is NNA?', principal: me.id, arm: 'graph', behaviour: 'cite_certified', cite: ['EM-001'], answer_text: 'Net New Assets: inflows less outflows [1]\nCited: EM-001 Net new assets [1], authority.' }} />);
  const text = screen.getByLabelText('Answer').textContent;
  expect(text.match(/EM-001/g)).toHaveLength(1);
  expect(text).not.toContain('Cites:');
});

test('requirement 13: a follow-up Graph tab marks the words carried over from earlier turns', () => {
  const words = name => ({ id: `Words:${name}`, type: 'Words', name, label: name, group: 'words', used: true, state: 'done' });
  const answer = { question: 'And for Q2 2025?', history: [{ turn_id: 't1', question: 'NNA for Q1 2025?' }, { turn_id: 't2', question: 'And for Q2 2025?' }], graph_path: { nodes: [words('NNA'), words('Q2 2025'), { id: 'Term:nna', type: 'Term', name: 'net-new-assets', group: 'vocabulary', used: true }], edges: [{ from: 'Words:NNA', to: 'Term:nna', type: 'matches', label: 'matches' }] } };
  const { container } = render(<GraphPath answer={answer} />);
  const carried = container.querySelector('[data-node-id="Words:NNA"]'), own = container.querySelector('[data-node-id="Words:Q2 2025"]');
  expect(carried).toHaveAttribute('data-carried', 'true');
  expect(carried).toHaveClass('carried');
  expect(carried).toHaveTextContent('Words · earlier turn');
  expect(own).not.toHaveAttribute('data-carried');
  expect(own).not.toHaveTextContent('earlier turn');
  expect(container.querySelector('.graph-legend')).toHaveTextContent('words carried over from an earlier turn');
  fireEvent.click(carried);
  expect(container.querySelector('#path-detail')).toHaveTextContent('from an earlier turn');
  const { container: first } = render(<GraphPath answer={{ question: 'NNA for Q1 2025?', history: [{ turn_id: 't1', question: 'NNA for Q1 2025?' }], graph_path: { nodes: [words('NNA'), words('Q1 2025'), words('net new assets')], edges: [] } }} />);
  expect(first.querySelector('[data-carried]')).toBeNull();
  expect(first.querySelector('.graph-legend')).not.toHaveTextContent('carried over');
});

test('requirement 13: carried-over words follow the parser\'s normalising, not raw substrings', () => {
  const words = name => ({ id: `Words:${name}`, type: 'Words', name, label: name, group: 'words', used: true, state: 'done' });
  const history = [{ turn_id: 't1', question: 'NNA by region for Q1 2025?' }, { turn_id: 't2', question: 'And NNAS for  Q2  2025?' }];
  const { container } = render(<GraphPath answer={{ question: 'And NNAS for  Q2  2025?', history, graph_path: { nodes: [words('Q2 2025'), words('NNA'), words('region'), words('withheld')], edges: [] } }} />);
  expect(container.querySelector('[data-node-id="Words:Q2 2025"]')).not.toHaveAttribute('data-carried');
  expect(container.querySelector('[data-node-id="Words:NNA"]')).toHaveAttribute('data-carried', 'true');
  expect(container.querySelector('[data-node-id="Words:region"]')).toHaveAttribute('data-carried', 'true');
  expect(container.querySelector('[data-node-id="Words:withheld"]')).not.toHaveAttribute('data-carried');
  const { container: streaming } = render(<GraphPath answer={{ question: 'And NNAS?', graph_path: { nodes: [words('NNA')], edges: [] } }} />);
  expect(streaming.querySelector('[data-carried]')).toBeNull();
});

test('requirement 11: rich answers show readable periods and names, amounts with two decimals and counts and ranks whole', () => {
  const display = { headline: { text: 'Net New Assets · 2025-Q1 · by region · USD [1]' }, checked: { line: 'Checked for calendar_quarter 2025-Q2' }, notes: ['2025-Q2 is partial'], table: { caption: 'Net New Assets · 2025-Q1 · by region · USD', columns: [
    { key: '__end__', label: 'End', type: 'text' }, { key: '__rank__', label: 'Rank', type: 'number' }, { key: 'calendar_quarter', label: 'calendar_quarter', type: 'text' },
    { key: 'nna', label: 'Net new assets', type: 'number', currency: 'USD' }, { key: 'households', label: 'households.household_id', type: 'number' }],
  rows: [['top', 1, '2025-Q1', 1062980, 12], ['top', 2, '2025-Q2', 5.5, 7]] }, chart: { x: 'calendar_quarter', y: 'nna' } };
  const { container } = render(<Answer answer={figure('NNA by quarter', { figure: undefined, display })} />);
  expect([...container.querySelectorAll('.rich-table th')].map(th => th.textContent)).toEqual(['End', 'Rank', 'Quarter', 'Net new assets', 'Household']);
  expect([...container.querySelectorAll('.rich-table tbody tr:first-child td')].map(td => td.textContent)).toEqual(['top', '1', 'Q1 2025', 'USD 1,062,980.00', '12']);
  expect([...container.querySelectorAll('.bar-row .bl')].map(b => b.textContent)).toEqual(['Q1 2025', 'Q2 2025']);
  expect([...container.querySelectorAll('.bar-row .bv')].map(b => b.textContent)).toEqual(['USD 1,062,980.00', 'USD 5.50']);
  expect(container.querySelector('.ans')).toHaveTextContent('Net New Assets · Q1 2025 · by region · USD [1]');
  expect(container.querySelector('.rich-table caption')).toHaveTextContent('Net New Assets · Q1 2025 · by region · USD');
  expect(container).toHaveTextContent('Checked for Q2 2025');
  expect(container).toHaveTextContent('Q2 2025 is partial');
  expect(container).not.toHaveTextContent('2025-Q1');
  expect(container).not.toHaveTextContent('2025-Q2');
  const combined = { table: { columns: [{ key: 'figure', label: 'Figure', type: 'text' }, { key: 'value', label: 'Value', type: 'number' }, { key: 'currency', label: 'Currency', type: 'text' }],
    rows: [['Accounts (count)', 1040, null], ['Revenue (sum)', 2000000, 'USD']] } };
  const { container: mixed } = render(<Answer answer={figure('Accounts and revenue', { figure: undefined, display: combined })} />);
  expect([...mixed.querySelectorAll('.rich-table td.num')].map(td => td.textContent)).toEqual(['1,040', '2,000,000.00']);
});

test('counts stay whole on the figure card, its copy and a count breakdown', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const { container, rerender } = render(<Answer answer={figure('How many accounts?', { figure: 1040, currency: undefined })} />);
  expect(container.querySelector('.fig').textContent).toBe('1,040');
  fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('1,040'));
  rerender(<Answer answer={figure('Accounts by region', { figure: undefined, currency: undefined, rows: { MIDWEST: 12, PACIFIC: 7 }, group_by: 'office.region_code' })} />);
  expect([...container.querySelectorAll('.parts td.num')].map(td => td.textContent)).toEqual(['12', '7']);
  rerender(<Answer answer={figure('NNA for March 2025?')} />);
  expect(container.querySelector('.fig').textContent).toBe('1,062,980.00');
});

test('a count with currency XXX, the ISO code for none, shows and copies a whole number and never shows XXX', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const complaints = figure('Number of open retail complaints as at 30 June 2025', { figure: 14, currency: 'XXX', trace: [{ period: 'as_of 2025-06-30', term: 'Complaint', agg: 'count' }], context: { term: 'Complaint' } });
  const { container, rerender } = render(<Answer answer={complaints} />);
  expect(container.querySelector('.fig').textContent).toBe('14');
  expect(container.querySelector('[data-currency]').textContent).toBe('');
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('XXX');
  expect(container.querySelector('.figlabel')).toHaveTextContent('Complaint · as at 30 June 2025');
  fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('14'));
  rerender(<Answer answer={{ ...complaints, figure: undefined, rows: { North: 9, South: 5 }, group_by: 'office.region_code' }} />);
  expect([...container.querySelectorAll('.parts th')].map(th => th.textContent)).toEqual(['Region', 'Figure']);
  expect([...container.querySelectorAll('.parts td.num')].map(td => td.textContent)).toEqual(['9', '5']);
  rerender(<Answer answer={{ ...complaints, currency: 'USD', figure: 14 }} />);
  expect(container.querySelector('.fig').textContent).toBe('14');
  rerender(<Answer answer={figure('Complaints by region', { figure: undefined, result: { columns: [{ id: 'region', kind: 'dimension' }, { id: 'complaints', label: 'Complaints', kind: 'measure', currency: 'XXX' }], rows: [{ dimensions: [{ kind: 'text', value: 'North' }], cells: [{ value: { kind: 'decimal', value: '9' } }] }] } })} />);
  expect([...container.querySelectorAll('.result td')].map(td => td.textContent)).toEqual(['North', '9']);
  expect(container.querySelector('.result')).not.toHaveTextContent('XXX');
});

test('a count_distinct answer from the API never shows or copies XXX, and its count columns and totals stay whole', async () => {
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  const api = {
    question: 'Number of open retail complaints as at 30 June 2025', principal: me.id, arm: 'graph', behaviour: 'figure', figure: 14, currency: 'XXX',
    trace: [{ period: 'as_of 2025-06-30', term: 'Complaint', agg: 'count_distinct' }], context: { term: 'Complaint' }, phrased_by: 'template',
    answer_text: 'Open retail complaints as at 30 June 2025: XXX 14 [1].',
    result: { columns: [{ id: 'complaints', label: 'Open complaints', kind: 'measure', currency: 'XXX' }], rows: [{ panel: null, dimensions: [], cells: [{ value: { kind: 'decimal', value: '14' }, citations: [], null_reasons: [] }] }] },
  };
  const { container, rerender } = render(<Answer answer={api} />);
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('XXX');
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('14.00');
  expect(container.querySelector('.fig').textContent).toBe('14');
  fireEvent.click(screen.getByRole('button', { name: 'Copy answer' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith('Open retail complaints as at 30 June 2025: 14 [1].'));
  rerender(<Answer answer={{ ...api, agent: { arm: 'graph', mode: 'mock' } }} />);
  expect(container.querySelector('.ans')).toHaveTextContent('Open retail complaints as at 30 June 2025: 14 [1].');
  expect(container.querySelector('.ans')).not.toHaveTextContent('XXX');
  for (const currency of [undefined, null, '']) {
    rerender(<Answer answer={figure('Complaints by region', { currency: undefined, figure: undefined, result: { columns: [{ id: 'region', kind: 'dimension' }, { id: 'complaints', label: 'Complaints', kind: 'measure', currency }], rows: [
      { dimensions: [{ kind: 'text', value: 'North' }], cells: [{ value: { kind: 'decimal', value: '9' } }] },
      { total: true, dimensions: [{ kind: 'null' }], cells: [{ value: { kind: 'decimal', value: '14' } }] }] } })} />);
    expect([...container.querySelectorAll('.result td')].map(td => td.textContent)).toEqual(['North', '9', 'Total', '14']);
  }
  const display = { headline: { text: 'Open complaints by region: XXX 14 in total' }, table: { caption: 'Open complaints (XXX) · as_of 2025-06-30 · by region', columns: [{ key: 'region', label: 'Region', type: 'text' }, { key: 'complaints', label: 'Complaints', type: 'number', currency: 'XXX' }],
    rows: [['North', 9], ['South', 5]], total: { label: 'Total', value: 14 } } };
  rerender(<Answer answer={{ ...api, answer_text: undefined, result: undefined, display }} />);
  expect(container.querySelector('.ans').textContent).toBe('Open complaints by region: 14 in total');
  expect(container.querySelector('.rich-table caption').textContent).toBe('Open complaints · as at 30 June 2025 · by region');
  expect([...container.querySelectorAll('.rich-table td.num')].map(td => td.textContent)).toEqual(['9', '5', '14']);
  expect(screen.getByLabelText('Answer')).not.toHaveTextContent('XXX');
  rerender(<Answer answer={{ ...api, answer_text: undefined, result: undefined, currency: 'USD', trace: [], display: { ...display, table: { ...display.table, columns: [display.table.columns[0], { ...display.table.columns[1], currency: 'USD' }] } } }} />);
  expect([...container.querySelectorAll('.rich-table td.num')].map(td => td.textContent)).toEqual(['USD 9.00', 'USD 5.00', 'USD 14.00']);
  expect(withoutNoCurrency('MAXXXIMUM XXX-1 stays; XXX 14 goes')).toBe('MAXXXIMUM XXX-1 stays; 14 goes');
});

test.each([
  ['Complaint · as_of 2025-06-30 · by region · XXX', 'Complaint · as_of 2025-06-30 · by region'],
  ['Complaint · XXX · by region', 'Complaint · by region'],
  ['Complaint · MAXXXIMUM · XXX-1', 'Complaint · MAXXXIMUM · XXX-1'],
])('removes only standalone missing-currency caption segments: %s', (caption, expected) => {
  expect(withoutNoCurrency(caption)).toBe(expected);
});

test('one rule decides money or a whole count: amount() for every kind, currency and aggregation', () => {
  expect(amount(1062980, { currency: 'USD' })).toBe('1,062,980.00');
  expect(amount(5.5, { currency: 'GBP', kind: 'measure' })).toBe('5.50');
  for (const about of [{}, { currency: null }, { currency: '' }, { currency: 'XXX' }, { currency: 'xxx' }, { currency: 'USD', kind: 'count' }, { currency: 'USD', kind: 'count_distinct' }, { currency: 'USD', agg: 'count' }, { currency: 'USD', agg: 'COUNT_DISTINCT' }])
    expect(amount(14, about), JSON.stringify(about)).toBe('14');
  expect(amount(14, { amount: true })).toBe('14.00');
  expect(amount(14, { amount: true, kind: 'count' })).toBe('14');
});

test('combined figures and parts show money only for a real-currency non-count, exact text', () => {
  const figures = [
    { label: 'Open complaints', kind: 'count_distinct', column: 'complaints.complaint_id', currency: 'XXX', value: 14 },
    { label: 'Accounts', kind: 'count', column: 'accounts.account_id', currency: 'USD', value: 1040 },
    { label: 'Net new assets', kind: 'sum', column: 'flows.nna', currency: 'USD', value: 1062980 },
    { label: 'Fees', kind: 'sum', column: 'fees.amount', currency: null, value: 12.5 },
  ];
  const { container, rerender } = render(<Answer answer={{ question: 'Complaints, accounts, NNA and fees', principal: me.id, arm: 'graph', behaviour: 'combined', answer_text: 'Open complaints XXX 14; accounts 1040; NNA USD 1,062,980.', figures, citations: [], guard: { passed: true } }} />);
  expect([...container.querySelectorAll('.figs td.num')].map(td => td.textContent)).toEqual(['14', '1,040', 'USD 1,062,980.00', '12.5']);
  expect(container.querySelector('.ans').textContent).toBe('Open complaints 14; accounts 1040; NNA USD 1,062,980.');
  expect(container).not.toHaveTextContent('XXX');
  const plan = [
    { unit: 'retail', status: 'ok', value: 1040, grain: 'month', calendar: 'calendar' },
    { unit: 'sme', status: 'ok', value: 899746.55, currency: 'USD', grain: 'month', calendar: 'calendar' },
    { unit: 'wealth', status: 'ok', value: 14, currency: 'XXX', grain: 'month', calendar: 'calendar' },
    { unit: 'corporate', status: 'ok', value: 7, currency: 'USD', agg: 'count_distinct', grain: 'quarter', calendar: 'fiscal' },
    { unit: 'leasing', status: 'ok', value: 2000000, currency: 'EUR', grain: 'month', calendar: 'calendar' },
  ];
  rerender(<Answer answer={{ question: 'Revenue across units', principal: me.id, arm: 'graph', behaviour: 'parts_with_reason', plan }} />);
  expect([...container.querySelectorAll('table.parts tbody td.num')].map(td => td.textContent)).toEqual(['1,040', 'USD 899,746.55', '14', '7', 'EUR 2,000,000.00']);
});

test('as_of periods read as people write them', () => {
  expect(periodText('as_of 2025-06-30')).toBe('as at 30 June 2025');
  expect(readablePeriods('Complaint · as_of 2025-06-30 · by region')).toBe('Complaint · as at 30 June 2025 · by region');
});

test('a physical column name is kept as it is unless the answer gives a business label', () => {
  expect(businessName('F_NNA_FIN.NNA_AM')).toBe('F_NNA_FIN.NNA_AM');
  expect(businessName('wealth.F_NNA_FIN.NNA_AM')).toBe('wealth.F_NNA_FIN.NNA_AM');
  const result = { columns: [{ id: 'F_NNA_FIN.RGN_CD', kind: 'dimension' }, { id: 'F_NNA_FIN.NNA_AM', label: 'Net new assets', kind: 'measure', currency: 'USD' }, { id: 'F_NNA_FIN.CNT', kind: 'measure' }], rows: [{ dimensions: [{ kind: 'text', value: 'MIDWEST' }], cells: [{ value: { kind: 'decimal', value: '1' } }, { value: { kind: 'decimal', value: '2' } }] }] };
  const { container } = render(<Answer answer={figure('NNA by region code', { figure: undefined, result })} />);
  expect([...container.querySelectorAll('.result th')].map(th => th.textContent)).toEqual(['F_NNA_FIN.RGN_CD', 'Net new assets (USD)', 'F_NNA_FIN.CNT']);
});

test('requirement 13: a word the follow-up repeats from an earlier turn is its own, not carried over', () => {
  const words = name => ({ id: `Words:${name}`, type: 'Words', name, label: name, group: 'words', used: true, state: 'done' });
  const history = [{ turn_id: 't1', question: 'NNA for March 2025' }, { turn_id: 't2', question: 'NNA for April 2025' }];
  const { container } = render(<GraphPath answer={{ question: 'NNA for April 2025', history, graph_path: { nodes: [words('NNA'), words('April 2025'), words('March 2025')], edges: [] } }} />);
  expect(container.querySelector('[data-node-id="Words:NNA"]')).not.toHaveAttribute('data-carried');
  expect(container.querySelector('[data-node-id="Words:April 2025"]')).not.toHaveAttribute('data-carried');
  expect(container.querySelector('[data-node-id="Words:March 2025"]')).toHaveAttribute('data-carried', 'true');
});

test('the Examples dialog works from the keyboard: it opens, focus moves into it and Escape returns focus', () => {
  const { container } = render(<Ask api={vi.fn()} me={me} presets={presets} golden={golden} />);
  const button = screen.getByRole('button', { name: 'Examples ▾' });
  expect(container.querySelector('#examples')).not.toBeVisible();
  act(() => button.focus());
  fireEvent.keyDown(button, { key: 'Enter' });
  fireEvent.click(button);
  const panel = screen.getByRole('dialog', { name: 'Example questions' });
  expect(panel).toBeVisible();
  expect(button).toHaveAttribute('aria-expanded', 'true');
  expect(panel.contains(document.activeElement)).toBe(true);
  expect(document.activeElement).toBe(within(panel).getAllByRole('button')[0]);
  fireEvent.keyDown(document.activeElement, { key: 'Escape' });
  expect(panel).not.toBeVisible();
  expect(button).toHaveFocus();
});

test.each([
  [undefined, ASK_TITLE, ASK_SUBTITLE],
  [{ title: '', subtitle: '   ' }, ASK_TITLE, ASK_SUBTITLE],
  [{ title: 'Ask Meridian', subtitle: '' }, 'Ask Meridian', ASK_SUBTITLE],
  [{ subtitle: 'Only what you may read.' }, ASK_TITLE, 'Only what you may read.'],
])('requirement 14: the header reads its settings, falling back to today\'s text: %j', (header, title, subtitle) => {
  expect(ASK_TITLE).toBe('Ask a governed question');
  expect(ASK_SUBTITLE).toBe('Certified definitions and figures, answered only from data you may read.');
  const { container } = render(<Ask api={vi.fn()} me={me} presets={presets} golden={golden} header={header} />);
  expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(title);
  expect(container.querySelector('.ask-subtitle').textContent).toBe(subtitle);
});

test('the examples panel sits under the Examples button and follows it', () => {
  const { container } = render(<Ask api={vi.fn()} me={me} presets={presets} golden={golden} />);
  const button = screen.getByRole('button', { name: 'Examples ▾' });
  vi.spyOn(button, 'getBoundingClientRect').mockReturnValue({ top: 120, bottom: 164 });
  vi.spyOn(container.querySelector('.ask-main-col'), 'getBoundingClientRect').mockReturnValue({ top: 100, bottom: 900 });
  act(() => { fireEvent.click(button); });
  expect(container.querySelector('#examples').style.top).toBe('70px');
  act(() => { window.dispatchEvent(new Event('resize')); });
  expect(container.querySelector('#examples').style.top).toBe('70px');
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(container.querySelector('#examples')).not.toBeVisible();
});
