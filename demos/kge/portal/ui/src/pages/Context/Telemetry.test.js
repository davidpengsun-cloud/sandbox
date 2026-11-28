import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import Telemetry from './Telemetry';

const day = '2026-11-25', trace = { id: 't1', agent: 'cerebro', session_id: 's1', started_at: `${day}T10:00:00Z`, duration_ms: 100, outcome: 'ok', turn_index: 2, session_trace_count: 3, previous_trace_id: 't0', next_trace_id: 't2', model_calls: 2, input_tokens: 30, output_tokens: 10, thinking_tokens: 3, warehouse_reads: 2 };
const spans = [
  { id: 'root', name: 'kge.ask', started_at: `${day}T10:00:00Z`, duration_ms: 100, attributes: { prompt: 'PRIVATE PROMPT' } },
  { id: 'model', parent_id: 'root', name: 'kge.model', started_at: `${day}T10:00:00Z`, duration_ms: 40 },
  ...[1, 2].map(i => ({ id: `attempt${i}`, parent_id: 'model', name: 'kge.model.attempt', started_at: `${day}T10:00:00Z`, duration_ms: 10, attributes: { 'gen_ai.request.model': 'gemini', 'kge.stage': 'plan', 'gen_ai.usage.input_tokens': 15, prompt: 'PRIVATE PROMPT' } })),
  ...[1, 2].map(i => ({ id: `read${i}`, parent_id: 'root', name: 'kge.warehouse', started_at: `${day}T10:00:00Z`, duration_ms: 8, attributes: { sql: 'PRIVATE SQL' } })),
];
const summary = { sessions: 1, turns: 3, ok: 2, failed: 1, model_calls: 3, input_tokens: 30, output_tokens: 10, thinking_tokens: 2, cost_usd: 0.00015, no_model_turns: 1 };
function service(path) {
  const u = new URL(path, 'https://example.invalid');
  if (u.pathname.endsWith('/agents')) return Promise.resolve({ agents: [{ id: 'cerebro' }] });
  if (u.pathname.endsWith('/sessions')) return Promise.resolve({ sessions: [{ id: 's1' }] });
  if (u.pathname.endsWith('/overview')) return Promise.resolve({ summary, daily: [{ date: day, agent: 'cerebro', sessions: 1, turns: 3, failed: 1, models: [{ model: 'gemini', calls: 3, input_tokens: 30, output_tokens: 10, thinking_tokens: 2, cost_usd: 0.00015, input_tokens_reported: 2, output_tokens_reported: 2, thinking_tokens_reported: 2, cost_usd_reported: 2 }] }] });
  if (u.pathname.endsWith('/traces')) return Promise.resolve({ traces: [trace], next_cursor: u.searchParams.has('cursor') ? null : 'next' });
  if (u.pathname.includes('/traces/')) return Promise.resolve({ trace: { ...trace, id: u.pathname.split('/').pop(), spans } });
  return Promise.resolve({});
}
test('opens all turns at once, presents full-window totals and filters by the selected day', async () => {
  const api = vi.fn(service); render(<Telemetry api={api} />);
  expect(await screen.findByRole('heading', { name: 'Usage overview' })).toBeVisible();
  expect(await screen.findByText('2 of 3 calls reported tokens')).toBeVisible();
  expect(screen.getAllByText(/0\.00015/).length).toBeGreaterThan(0);
  const request = new URL(api.mock.calls.find(([p]) => p.startsWith('/telemetry/traces?'))[0], 'https://example.invalid');
  expect(request.searchParams.has('agent')).toBe(false); expect(request.searchParams.has('session')).toBe(false);
  expect(await screen.findByRole('button', { name: 'Next page' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Next page' }));
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.includes('cursor=next'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: day }));
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.includes(`day=${day}`) && p.includes('day_agent=cerebro'))).toBe(true));
});
test('drawer groups retries and reads, preserves neighbors and never displays arbitrary attributes', async () => {
  const api = vi.fn(service); render(<Telemetry api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open turn' }));
  const drawer = await screen.findByRole('dialog', { name: 'Turn details' });
  expect(await within(drawer).findByText(/Turn 2 of 3/)).toBeVisible();
  expect(within(drawer).getByText(/2 attempts/)).toBeVisible();
  expect(within(drawer).getByText(/2 warehouse reads · 16 ms/)).toBeVisible();
  expect(drawer).not.toHaveTextContent('PRIVATE PROMPT'); expect(drawer).not.toHaveTextContent('PRIVATE SQL');
  fireEvent.click(within(drawer).getByRole('button', { name: 'Previous turn' }));
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.includes('/traces/t0?'))).toBe(true));
  fireEvent.keyDown(drawer, { key: 'Escape' }); expect(screen.queryByRole('dialog')).toBeNull();
});
test('unknown values and partial field reporting remain explicit', async () => {
  const api = vi.fn(async path => path.includes('/overview') ? { summary: { sessions: null, turns: null }, daily: [{ date: day, agent: 'cerebro', models: [{ model: 'm', calls: 3, input_tokens: 5, input_tokens_reported: 3, thinking_tokens: 2, thinking_tokens_reported: 2 }] }] } : service(path));
  render(<Telemetry api={api} />); expect(await screen.findByText('2 of 3 calls reported thinking tokens')).toBeVisible();
  expect(screen.queryByText('3 of 3 calls reported input tokens')).toBeNull(); expect(screen.getAllByText('Not reported').length).toBeGreaterThan(1);
});


test('groups real emitter sibling attempts under their uniquely matching logical call', async () => {
  const recorded = {"id":"bafa525fdc85efc213079b556c244d43","agent":"context_ask","session_id":"conv-101","started_at":"2026-11-27T05:37:30.668702+00:00","duration_ms":16.455000000000002,"outcome":"ok","spans":[{"id":"aafd616a907d66af","parent_id":null,"name":"kge.ask","started_at":"2026-11-27T05:37:30.668702+00:00","ended_at":"2026-11-27T05:37:30.685157+00:00","duration_ms":16.455000000000002,"attributes":{"kge.path":"ask","kge.run_id":"d01d1e7b273443c8a5840a48fd3e8e28","kge.outcome":"ok","kge.client":"meridian","kge.agent":"context_ask","gen_ai.conversation.id":"conv-101","kge.telemetry.origin":"kge"}},{"id":"49b2a975488afa9f","parent_id":"aafd616a907d66af","name":"kge.stage.resolve","started_at":"2026-11-27T05:37:30.669179+00:00","ended_at":"2026-11-27T05:37:30.684139+00:00","duration_ms":14.959999999999999,"attributes":{"kge.stage":"resolve","kge.run_id":"d01d1e7b273443c8a5840a48fd3e8e28","kge.client":"meridian","kge.agent":"context_ask","gen_ai.conversation.id":"conv-101","kge.telemetry.origin":"kge"}},{"id":"ea7246a6248412c5","parent_id":"49b2a975488afa9f","name":"kge.model","started_at":"2026-11-27T05:37:30.669326+00:00","ended_at":"2026-11-27T05:37:30.682726+00:00","duration_ms":13.4,"attributes":{"gen_ai.request.model":"vertex-gemini:gemini-3.1-pro","gen_ai.response.model":"gemini-3.1-pro","gen_ai.usage.input_tokens":100,"gen_ai.usage.output_tokens":25,"kge.stage":"resolve","kge.duration_ms":13.4,"kge.outcome":"ok","kge.run_id":"d01d1e7b273443c8a5840a48fd3e8e28","gen_ai.usage.reasoning_tokens":5,"kge.call":"classify","kge.client":"meridian","kge.agent":"context_ask","gen_ai.conversation.id":"conv-101","kge.telemetry.origin":"kge"}},{"id":"c23fdf46d62bd998","parent_id":"aafd616a907d66af","name":"kge.model.attempt","started_at":"2026-11-27T05:37:30.669330+00:00","ended_at":"2026-11-27T05:37:30.674699+00:00","duration_ms":5.369,"attributes":{"gen_ai.request.model":"gemini-3.1-pro","kge.stage":"classify","kge.duration_ms":5.368755,"kge.outcome":"failed","kge.error":"failed","kge.client":"meridian","kge.agent":"context_ask","gen_ai.conversation.id":"conv-101","kge.telemetry.origin":"kge"}},{"id":"f34a31c5b6d20f10","parent_id":"aafd616a907d66af","name":"kge.model.attempt","started_at":"2026-11-27T05:37:30.676905+00:00","ended_at":"2026-11-27T05:37:30.681212+00:00","duration_ms":4.307,"attributes":{"gen_ai.request.model":"gemini-3.1-pro","gen_ai.response.model":"gemini-3.1-pro","gen_ai.usage.input_tokens":100,"gen_ai.usage.output_tokens":25,"gen_ai.usage.reasoning_tokens":5,"kge.model.cost_usd":0.00015,"kge.stage":"classify","kge.duration_ms":4.307435,"kge.outcome":"ok","kge.client":"meridian","kge.agent":"context_ask","gen_ai.conversation.id":"conv-101","kge.telemetry.origin":"kge"}}],"turn_index":1,"session_trace_count":1,"previous_trace_id":null,"next_trace_id":null,"model_calls":2,"warehouse_reads":0,"input_tokens":100,"output_tokens":25,"thinking_tokens":5};
  const api = vi.fn(async path => path.includes('/traces/t1?') ? { trace: recorded } : service(path));
  render(<Telemetry api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open turn' }));
  const drawer = await screen.findByRole('dialog', { name: 'Turn details' });
  const group = await within(drawer).findByText(/2 attempts/);
  fireEvent.click(group);
  expect(within(drawer).getAllByText(/classify.*tokens/)).toHaveLength(2);
});

test('agent, session and outcome filters reach the overview and turns; failures are shown', async () => {
  const api = vi.fn(service); render(<Telemetry api={api} />);
  await screen.findByRole('option', { name: 'cerebro' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'cerebro' } });
  await screen.findByRole('option', { name: 's1' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Session' }), { target: { value: 's1' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Outcome' }), { target: { value: 'failed' } });
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.startsWith('/telemetry/overview?') && p.includes('agent=cerebro') && p.includes('session=s1') && p.includes('outcome=failed'))).toBe(true));
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.startsWith('/telemetry/traces?') && p.includes('outcome=failed'))).toBe(true));
});

test('a reached backend limit is visible', async () => {
  const api = vi.fn(async path => path.includes('/traces?') ? { error: 'Use a shorter period.' } : service(path));
  render(<Telemetry api={api} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Use a shorter period.');
});

test('changing the date range clears agent and session selections', async () => {
  const api = vi.fn(service); render(<Telemetry api={api} />);
  await screen.findByRole('option', { name: 'cerebro' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Agent' }), { target: { value: 'cerebro' } });
  await screen.findByRole('option', { name: 's1' });
  fireEvent.change(screen.getByRole('combobox', { name: 'Session' }), { target: { value: 's1' } });
  fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-11-01' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply dates' }));
  expect(screen.getByRole('combobox', { name: 'Agent' })).toHaveValue('');
  expect(screen.getByRole('combobox', { name: 'Session' })).toHaveValue('');
  await waitFor(() => expect(api.mock.calls.some(([p]) => p.startsWith('/telemetry/traces?') && p.includes('2026-11-01') && !p.includes('agent=') && !p.includes('session='))).toBe(true));
});

test('overlapping attempts from a different tagged model are not grouped with the call', async () => {
  const started_at = `${day}T10:00:00Z`, ended_at = `${day}T10:00:00.100Z`;
  const recorded = { ...trace, spans: [
    { id: 'root', name: 'kge.ask', started_at, ended_at, duration_ms: 100 },
    { id: 'logical', parent_id: 'root', name: 'kge.model', started_at, ended_at, duration_ms: 100, attributes: { 'gen_ai.request.model': 'ollama:llama3.3:70b' } },
    ...[1, 2].map(i => ({ id: `other${i}`, parent_id: 'root', name: 'kge.model.attempt', started_at, ended_at, duration_ms: 100, attributes: { 'gen_ai.request.model': 'qwen2.5:70b' } })),
  ] };
  const api = vi.fn(async path => path.includes('/traces/t1?') ? { trace: recorded } : service(path));
  render(<Telemetry api={api} />); fireEvent.click(await screen.findByRole('button', { name: 'Open turn' }));
  const drawer = await screen.findByRole('dialog', { name: 'Turn details' });
  expect(await within(drawer).findByText(/llama3.3:70b/)).toBeVisible();
  expect(within(drawer).queryByText(/2 attempts/)).toBeNull();
  expect(within(drawer).getAllByText(/qwen2.5:70b/)).toHaveLength(2);
});

test('overlapping matching model calls do not claim an ambiguous sibling attempt', async () => {
  const started_at = `${day}T10:00:00Z`, ended_at = `${day}T10:00:00.100Z`;
  const logical = [1, 2].map(i => ({ id: `call${i}`, parent_id: 'root', name: 'kge.model', started_at, ended_at, duration_ms: 100, attributes: { 'gen_ai.request.model': 'gemini', 'kge.call': 'plan' } }));
  const attempts = [1, 2].map(i => ({ id: `attempt${i}`, parent_id: 'root', name: 'kge.model.attempt', started_at, ended_at, duration_ms: 100, attributes: { 'gen_ai.request.model': 'gemini', 'kge.stage': 'plan' } }));
  const api = vi.fn(async path => path.includes('/traces/t1?') ? { trace: { ...trace, spans: [{ id: 'root', name: 'kge.ask', started_at, ended_at, duration_ms: 100 }, ...logical, ...attempts] } } : service(path));
  render(<Telemetry api={api} />); fireEvent.click(await screen.findByRole('button', { name: 'Open turn' }));
  const drawer = await screen.findByRole('dialog', { name: 'Turn details' });
  expect(await within(drawer).findAllByText(/Model attempt · gemini/)).toHaveLength(2);
  expect(within(drawer).queryByText(/2 attempts/)).toBeNull();
});

test('a logical call with one linked attempt is labeled as a model call', async () => {
  const single = spans.filter(s => s.id !== 'attempt2');
  const api = vi.fn(async path => path.includes('/traces/t1?') ? { trace: { ...trace, spans: single } } : service(path));
  render(<Telemetry api={api} />); fireEvent.click(await screen.findByRole('button', { name: 'Open turn' }));
  const drawer = await screen.findByRole('dialog', { name: 'Turn details' });
  expect(await within(drawer).findByText(/Model call · gemini/)).toBeVisible();
  expect(within(drawer).queryByText(/Model attempt · gemini/)).toBeNull();
});
