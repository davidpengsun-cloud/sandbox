import { expect, test } from 'vitest';
import { DEFAULT_MODE, INITIAL_PAGE_STATE, MODES, isTyping, modesFor, nextMode, pageReducer, readAddress, writeAddress } from './cerebroState';

test('Business-only opens Chat; Engineer and Steward retain their modes', () => {
  expect(MODES).toEqual(['chat', 'graph', 'curate']);
  expect(DEFAULT_MODE).toBe('graph');
  for (const roles of [['engineer'], ['business', 'engineer'], ['admin', 'engineer'], []]) expect(modesFor(roles)).toEqual(['chat', 'graph']);
  for (const roles of [['business'], ['business', 'admin'], undefined]) expect(modesFor(roles)).toEqual(['chat']);
  expect(modesFor(['steward'])).toEqual(['chat', 'graph', 'curate']);
  expect(modesFor(['business', 'steward'])).toEqual(['chat', 'graph', 'curate']);
});

test.each([
  ['?mode=chat&node=table%3Aorders', ['business'], { mode: 'chat', node: 'table:orders', chat: null }],
  ['?mode=graph', ['business'], { mode: 'chat', node: null, chat: null }],
  ['', ['business'], { mode: 'chat', node: null, chat: null }],
  ['?mode=overview&node=x', ['business'], { mode: 'chat', node: 'x', chat: null }],
  ['?mode=curate&node=x', ['business'], { mode: 'chat', node: 'x', chat: null }],
  ['?mode=curate&node=x', ['steward'], { mode: 'curate', node: 'x', chat: null }],
  ['?mode=chat&node=%20%20', ['business'], { mode: 'chat', node: null, chat: null }],
  [`?mode=chat&node=${'n'.repeat(513)}`, ['business'], { mode: 'chat', node: null, chat: null }],
  // Issue 625: the open conversation's id, when it is one: a UUID, as the server takes one; anything else is none.
  ['?mode=chat&chat=1b4e28ba-2fa1-11d2-883f-0016d3cca427', ['business'], { mode: 'chat', node: null, chat: '1b4e28ba-2fa1-11d2-883f-0016d3cca427' }],
  ['?mode=chat&chat=..%2Fsecret', ['business'], { mode: 'chat', node: null, chat: null }],
  ['?mode=chat&chat=c-1', ['business'], { mode: 'chat', node: null, chat: null }],
  ['?mode=chat&chat=not_a_uuid_at_all', ['business'], { mode: 'chat', node: null, chat: null }],
  ['?mode=chat&chat=1b4e28ba-2fa1-11d2-883f-0016d3cca427x', ['business'], { mode: 'chat', node: null, chat: null }],
])('the address %s, for %j, opens %j', (search, roles, expected) => {
  expect(readAddress(search, roles)).toEqual(expected);
});

test('the address carries the mode always and the node only when one is selected, never a question', () => {
  expect(writeAddress({ mode: 'chat', node: null })).toBe('?mode=chat');
  expect(writeAddress({ mode: 'graph', node: 'term:net revenue' })).toBe('?mode=graph&node=term%3Anet+revenue');
  expect(writeAddress({ mode: 'nope', node: '' })).toBe('?mode=graph');
  expect(writeAddress({ mode: 'chat', node: 'x', question: 'what is net revenue?' })).toBe('?mode=chat&node=x');
  expect(readAddress(writeAddress({ mode: 'graph', node: 'term:net revenue' }), [])).toEqual({ mode: 'graph', node: 'term:net revenue', chat: null });
});

test('the address carries the open conversation by its id alone, when one is open (issue 625)', () => {
  const id = '1b4e28ba-2fa1-11d2-883f-0016d3cca427';
  expect(writeAddress({ mode: 'chat', node: null, chat: id })).toBe(`?mode=chat&chat=${id}`);
  expect(writeAddress({ mode: 'graph', node: 'x', chat: id })).toBe(`?mode=graph&node=x&chat=${id}`);
  expect(writeAddress({ mode: 'chat', chat: 'c-1' })).toBe('?mode=chat');
  expect(writeAddress({ mode: 'chat', chat: null })).toBe('?mode=chat');
  expect(writeAddress({ mode: 'chat', chat: 'a b' })).toBe('?mode=chat');
});

test('the switch key cycles the modes, skipping Curate without the steward role', () => {
  expect(nextMode('chat', ['business'])).toBe('chat');
  expect(nextMode('graph', ['business'])).toBe('chat');
  expect(nextMode('chat', ['business', 'engineer'])).toBe('graph');
  expect(nextMode('graph', ['steward'])).toBe('curate');
  expect(nextMode('curate', ['steward'])).toBe('chat');
  expect(nextMode('curate', ['business'])).toBe('chat');
});

test('the page holds the conversation, the last path and the graph position and the laid-out graph, each changed alone', () => {
  let state = INITIAL_PAGE_STATE;
  state = pageReducer(state, { type: 'turn', turn: { role: 'user', text: 'a' } });
  state = pageReducer(state, { type: 'path', path: { nodes: ['t1'], edges: [] } });
  state = pageReducer(state, { type: 'position', position: { x: 1, y: 2, zoom: 1.5 } });
  expect(state).toEqual({ conversation: [{ role: 'user', text: 'a' }], lastPath: { nodes: ['t1'], edges: [] }, graphPosition: { x: 1, y: 2, zoom: 1.5 }, graph: null, asking: null, fittedPath: null });
  expect(pageReducer(state, { type: 'graph', graph: { status: 'ready' } })).toEqual({ ...state, graph: { status: 'ready' } });
  expect(pageReducer(state, { type: 'conversation', conversation: 'bad' }).conversation).toEqual([]);
  expect(pageReducer(state, { type: 'unknown' })).toBe(state);
  expect(INITIAL_PAGE_STATE.conversation).toEqual([]);
  // The path the graph's view was last fitted to: set alone, and an unchanged one changes nothing.
  const fitted = pageReducer(state, { type: 'fitted', path: '{"nodes":["t1"]}' });
  expect(fitted).toEqual({ ...state, fittedPath: '{"nodes":["t1"]}' });
  expect(pageReducer(fitted, { type: 'fitted', path: '{"nodes":["t1"]}' })).toBe(fitted);
});

test('typing in a field is not the switch key', () => {
  expect(isTyping({ tagName: 'INPUT' })).toBe(true);
  expect(isTyping({ tagName: 'TEXTAREA' })).toBe(true);
  expect(isTyping({ tagName: 'DIV', isContentEditable: true })).toBe(true);
  expect(isTyping({ tagName: 'BUTTON' })).toBe(false);
  expect(isTyping(null)).toBe(false);
});

test('the node Ask about this fills in is held and cleared alone', () => {
  const asking = pageReducer(INITIAL_PAGE_STATE, { type: 'asking', node: 'term:Net New Assets' });
  expect(asking).toEqual({ ...INITIAL_PAGE_STATE, asking: 'term:Net New Assets' });
  expect(pageReducer(asking, { type: 'asking' })).toEqual(INITIAL_PAGE_STATE);
  expect(pageReducer(INITIAL_PAGE_STATE, { type: 'asking', node: null })).toBe(INITIAL_PAGE_STATE);
});
