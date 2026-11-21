/* Tests only: a mock agent on the portal server's own stream handler, called in process (as ChatView.test.js and
   streamCheck.test.js do), whose turns answer each question with an `ask` call and its reply, or with a get_context
   call and its package, and can send ask progress as custom events and hold a turn open until released. */
import { cerebroStream } from "../../../../../server/src/cerebro/route.js";

const HOLD = Symbol("hold");
const lastHuman = (messages) => [...messages].reverse().find((m) => m.type === "human")?.content;

/** A turn that asks KGE: the ask call, any progress events, a hold, then the reply and the agent's own words. */
export function askTurn(reply, { args, events = [], hold = false, say = "" } = {}) {
  return (messages, n) => {
    const question = lastHuman(messages);
    const id = `tc-${n}`;
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id, name: "ask", args: args ?? { question } }] };
    const result = { type: "tool", id: `tool-${n}`, tool_call_id: id, name: "ask", content: JSON.stringify(typeof reply === "function" ? reply(question) : reply) };
    const after = say ? [{ type: "ai", id: `ai-${n}-say`, content: say }] : [];
    return [
      ["values", { messages: [...messages, call] }],
      ...events.map((event) => ["custom", { kind: "ask_progress", tool_call_id: id, event }]),
      ...(hold ? [HOLD] : []),
      ["values", { messages: [...messages, call, result, ...after] }],
    ];
  };
}

/** A turn that reads the context package: the get_context call and its package. */
export function contextTurn(pkg) {
  return (messages, n) => {
    const id = `ctx-${n}`;
    const call = { type: "ai", id: `ai-${n}`, content: "", tool_calls: [{ id, name: "get_context", args: { question: lastHuman(messages) } }] };
    const result = { type: "tool", id: `tool-${n}`, tool_call_id: id, name: "get_context", content: JSON.stringify(pkg) };
    return [["values", { messages: [...messages, call, result] }]];
  };
}

/** The agent: `turns[i]` answers the i-th question (the last one answers the rest). `fetch` is the page's stream. */
export function mockAgent(turns) {
  const requests = [];
  const gates = [];
  let n = 0;
  async function* runTurn({ messages }) {
    const turn = turns[Math.min(n, turns.length - 1)];
    n += 1;
    for (const item of turn(messages, n)) {
      if (item === HOLD) await new Promise((resolve) => gates.push(resolve));
      else yield item;
    }
  }
  const handler = cerebroStream({ runTurn });
  async function fetch(_url, init) {
    const body = JSON.parse(init.body);
    requests.push(body);
    let controller;
    const stream = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    let status = 200;
    // A stopped request ends its stream as a browser's fetch does; what the agent writes after is dropped.
    let open = true;
    init.signal?.addEventListener("abort", () => { if (open) { open = false; controller.error(new DOMException("Aborted", "AbortError")); } });
    const res = {
      on() {}, writeHead: (s) => (status = s),
      write: (chunk) => { if (open) controller.enqueue(encoder.encode(chunk)); },
      end: () => { if (open) { open = false; controller.close(); } },
    };
    void handler({ body }, res);
    return new Response(stream, { status, headers: { "content-type": "text/event-stream" } });
  }
  return { requests, fetch, release: () => gates.splice(0).forEach((resolve) => resolve()) };
}
