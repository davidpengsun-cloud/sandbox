import assert from "node:assert/strict";
import express from "express";
import { after, before, test } from "node:test";
import { cerebroStream, encodeEvent, readTurn } from "../src/cerebro/route.js";

// A fake turn: the first request calls a tool and pauses for Confirm; a request carrying command.resume answers.
const calls = [];
async function* fakeTurn({ messages, resume, signal }) {
  calls.push({ messages, resume });
  const asked = [...messages];
  if (resume === undefined) {
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    yield ["values", { messages: [...asked, call] }];
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: "confirm_first" };
    yield ["values", { messages: [...asked, call, result], __interrupt__: [{ id: "int-1", value: { kind: "confirm_first", question: "Revenue?" } }] }];
    return;
  }
  if (messages[0]?.content === "throw") throw new Error("upstream secret detail");
  if (messages[0]?.content === "slow") {
    await new Promise((resolve) => signal.addEventListener("abort", resolve));
    calls.push({ aborted: signal.aborted });
    return;
  }
  yield ["values", { messages: [...asked, { type: "ai", id: "ai-2", content: `Answer after ${resume}.` }] }];
}

let server;
let url;
before(async () => {
  const app = express();
  app.post("/", express.json({ limit: "1mb" }), cerebroStream({ runTurn: fakeTurn }));
  server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  url = `http://127.0.0.1:${server.address().port}/`;
});
after(() => server.close());

const post = (body) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const events = (text) =>
  text
    .split("\n\n")
    .filter(Boolean)
    .map((frame) => {
      const [, event] = frame.match(/^event: (.*)$/m);
      const [, data] = frame.match(/^data: (.*)$/m);
      return [event, JSON.parse(data)];
    });

test("encodeEvent frames one server-sent event", () => {
  assert.equal(encodeEvent("values", { a: 1 }), 'event: values\ndata: {"a":1}\n\n');
});

test("readTurn refuses a body with no conversation", () => {
  assert.match(readTurn(null).error, /JSON body/);
  assert.match(readTurn({ input: { messages: [] } }).error, /input.messages/);
  assert.match(readTurn({ input: { messages: [{}] } }).error, /type/);
  assert.match(readTurn({ input: { messages: Array.from({ length: 201 }, () => ({ type: "human" })) } }).error, /at most/);
});

test("a first turn streams a tool call and pauses for the person", async () => {
  const human = { type: "human", id: "h-1", content: "Revenue?" };
  const res = await post({ input: { messages: [human] }, config: { configurable: { thread_id: "t-1" } } });
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type"), /text\/event-stream/);
  const seen = events(await res.text());
  assert.deepEqual(seen.map(([event]) => event), ["metadata", "values", "values"]);
  assert.equal(seen[1][1].messages[1].tool_calls[0].name, "ask");
  assert.equal(seen[2][1].__interrupt__[0].value.kind, "confirm_first");
});

test("a resume runs from the conversation it carries alone", async () => {
  calls.length = 0;
  const conversation = [{ type: "human", id: "h-1", content: "Revenue?" }];
  const res = await post({ input: { messages: conversation }, command: { resume: "confirmed" } });
  const seen = events(await res.text());
  assert.equal(seen.at(-1)[1].messages.at(-1).content, "Answer after confirmed.");
  assert.deepEqual(calls, [{ messages: conversation, resume: "confirmed" }]);
});

test("a request with no conversation is refused before any stream", async () => {
  const res = await post({ input: null });
  assert.equal(res.status, 400);
  assert.match((await res.json()).detail, /input.messages/);
  const raw = await fetch(url, { method: "POST", headers: { "content-type": "text/plain" }, body: JSON.stringify({ input: { messages: [{ type: "human" }] } }) });
  assert.equal(raw.status, 400);
  assert.match((await raw.json()).detail, /JSON body/);
});

test("a failing turn ends in a fixed error event that names nothing upstream", async () => {
  const res = await post({ input: { messages: [{ type: "human", content: "throw" }] }, command: { resume: "confirmed" } });
  const text = await res.text();
  assert.doesNotMatch(text, /secret/);
  assert.deepEqual(events(text).at(-1), ["error", { error: "AgentError", message: "The agent could not answer." }]);
});

test("a page that goes away aborts the turn", async () => {
  calls.length = 0;
  const controller = new AbortController();
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ input: { messages: [{ type: "human", content: "slow" }] }, command: { resume: "go" } }),
    signal: controller.signal,
  });
  const reader = res.body.getReader();
  await reader.read();
  controller.abort();
  for (let i = 0; i < 50 && !calls.some((c) => c.aborted); i += 1) await new Promise((r) => setTimeout(r, 10));
  assert.ok(calls.some((c) => c.aborted));
});

test("a body already parsed by express is read as given", async () => {
  const seen = [];
  const handler = cerebroStream({
    runTurn: async function* ({ messages }) {
      seen.push(messages);
      yield ["values", { messages }];
    },
  });
  const written = [];
  const res = { on() {}, writeHead() {}, write: (s) => written.push(s), end() {} };
  await handler({ body: { input: { messages: [{ type: "human", content: "hi" }] } } }, res);
  assert.equal(seen[0][0].content, "hi");
  assert.match(written.at(-1), /^event: values/);
});
