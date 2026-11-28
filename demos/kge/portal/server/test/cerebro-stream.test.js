import assert from "node:assert/strict";
import express from "express";
import { after, before, test } from "node:test";
import { cerebroStream, encodeEvent, readTurn } from "../src/cerebro/route.js";

// #625: the page sends the person's one new message, or their answer to the HITL card, and the conversation's id; the
// server keeps the conversation. A fake turn: a first message calls a tool and pauses at the HITL card (#624); a request
// carrying command.resume answers; a conversation the person does not have is refused before any stream.
const THREAD = "0f8fad5b-d9cb-469f-a165-7086f772895e";
const OTHER = "7c9e6679-7425-40de-944b-e07fc1f90ae7";
const calls = [];
async function* fakeTurn({ thread, messages, resume, rewind, signal }) {
  calls.push({ thread, messages, resume, rewind });
  if (thread === OTHER) throw Object.assign(new Error("No such conversation."), { status: 404 });
  if (resume === undefined) {
    if (messages[0].content === "throw first") throw new Error("upstream secret detail");
    const asked = [{ ...messages[0] }];
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    yield ["values", { messages: [...asked, call], title: "Revenue?", trimmed: 0 }];
    if (messages[0].content === "slow") {
      await new Promise((resolve) => signal.addEventListener("abort", resolve));
      calls.push({ aborted: signal.aborted });
      return;
    }
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: "{\"behaviour\":\"clarify\"}" };
    const card = { type: "ai", id: "ai-2", content: "", tool_calls: [{ id: "hitl-1", name: "choose", args: { source: "clarify", question: "Which revenue?", options: [{ id: "a", label: "Gross" }], ask_call: "tc-1" } }] };
    yield ["values", { messages: [...asked, call, result, card], title: "Revenue?", trimmed: 0, __interrupt__: [{ id: "int-1", value: { kind: "hitl", tool_call_id: "hitl-1", source: "clarify", question: "Which revenue?", options: [{ id: "a", label: "Gross" }], other: true } }] }];
    return;
  }
  if (resume === "throw") throw new Error("upstream secret detail");
  if (resume === "kge") {
    yield ["custom", { kind: "step", step: "understanding", text: "Understanding your question" }];
    throw Object.assign(new Error("upstream secret detail"), { failure: "kge_unreachable" });
  }
  if (resume === "carried") {
    throw Object.assign(new Error("upstream secret detail"), { outcome: { kind: "outcome", outcome: "failed", steps: 3, error: { case: "too_long", message: "This took too long; try a narrower period or fewer groups.", action: "narrow" } } });
  }
  yield ["values", { messages: [{ type: "ai", id: "ai-3", content: `Answer after ${JSON.stringify(resume)}.` }], title: "Revenue?", trimmed: 0 }];
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
const config = { configurable: { thread_id: THREAD } };
const human = (content, id = "h-1") => ({ type: "human", id, content });

test("encodeEvent frames one server-sent event", () => {
  assert.equal(encodeEvent("values", { a: 1 }), 'event: values\ndata: {"a":1}\n\n');
});

test("readTurn takes the conversation's id and the person's one new message, or their answer to the card (#625)", () => {
  assert.deepEqual(readTurn({ input: { messages: [human("q")] }, config }), { thread: THREAD, messages: [human("q")] });
  assert.deepEqual(readTurn({ input: null, command: { resume: { pick: "a" } }, config }), { thread: THREAD, resume: { pick: "a" } });
  assert.deepEqual(readTurn({ command: { resume: "go" }, config }), { thread: THREAD, resume: "go" });
  assert.deepEqual(readTurn({ input: { messages: [] }, command: { resume: "go" }, config }), { thread: THREAD, resume: "go" });
  assert.deepEqual(readTurn({ input: { messages: [human("q", "h-2")] }, config, context: { rewind: "h-1" } }), { thread: THREAD, messages: [human("q", "h-2")], rewind: "h-1" });
  assert.deepEqual(readTurn({ input: { messages: [{ type: "human", content: [{ type: "text", text: "hi" }] }] }, config }).messages[0].content, [{ type: "text", text: "hi" }]);
  assert.equal(readTurn({ input: { messages: [{ type: "human" }] }, config }).error, undefined, "an empty message is the model's to answer");
});

test("readTurn refuses a body with no conversation id, no message, more than one, or an earlier answer (#625)", () => {
  assert.match(readTurn(null).error, /JSON body/);
  for (const body of [{ input: { messages: [human("q")] } }, { input: { messages: [human("q")] }, config: { configurable: { thread_id: "t-1" } } }, { input: { messages: [human("q")] }, config: { configurable: {} } }]) {
    assert.match(readTurn(body).error, /thread_id/, JSON.stringify(body));
  }
  for (const messages of [undefined, [], [human("a"), human("b")]]) assert.match(readTurn({ input: { messages }, config }).error, /one new message/, JSON.stringify(messages));
  for (const message of [{ type: "ai", content: "earlier" }, { type: "tool", tool_call_id: "c", content: "{}" }, { type: "system", content: "x" }, null,
    { type: "human", content: "q", tool_calls: [] }, { type: "human", content: "q", tool_call_id: "c" }]) {
    assert.match(readTurn({ input: { messages: [message] }, config }).error, /earlier answers are the server's/, JSON.stringify(message));
  }
  assert.match(readTurn({ input: { messages: [{ type: "human", content: [{ type: "image_url", image_url: "gs://x" }] }] }, config }).error, /text only/);
  for (const id of [42, "", "x".repeat(129), "has space"]) assert.match(readTurn({ input: { messages: [{ type: "human", id, content: "q" }] }, config }).error, /id is a short plain string/);
  assert.match(readTurn({ input: { messages: [human("q")] }, command: { resume: "go" }, config }).error, /resume carries no message/);
  assert.match(readTurn({ command: { resume: "go" }, config, context: { rewind: "h-1" } }).error, /goes back to no question/);
  for (const rewind of [3, "", "has space"]) assert.match(readTurn({ input: { messages: [human("q")] }, config, context: { rewind } }).error, /context\.rewind/);
});

test("a first message streams a tool call and pauses for the person; the turn gets the id and the message alone", async () => {
  calls.length = 0;
  const res = await post({ input: { messages: [human("Revenue?")] }, config });
  assert.equal(res.status, 200);
  assert.match(res.headers.get("content-type"), /text\/event-stream/);
  const seen = events(await res.text());
  assert.deepEqual(seen.map(([event]) => event), ["metadata", "values", "values"]);
  assert.equal(seen[1][1].messages[1].tool_calls[0].name, "ask");
  assert.equal(seen[2][1].__interrupt__[0].value.kind, "hitl");
  assert.deepEqual(calls, [{ thread: THREAD, messages: [human("Revenue?")], resume: undefined, rewind: undefined }]);
});

test("a resume carries the person's answer and the conversation's id, and no message", async () => {
  calls.length = 0;
  const res = await post({ input: null, command: { resume: { pick: "a" } }, config });
  const seen = events(await res.text());
  assert.equal(seen.at(-1)[1].messages.at(-1).content, "Answer after {\"pick\":\"a\"}.");
  assert.deepEqual(calls, [{ thread: THREAD, messages: undefined, resume: { pick: "a" }, rewind: undefined }]);
});

test("a request with no conversation, or for a conversation the person does not have, is refused before any stream (#625)", async () => {
  const res = await post({ input: null, config });
  assert.equal(res.status, 400);
  assert.match((await res.json()).detail, /one new message/);
  const raw = await fetch(url, { method: "POST", headers: { "content-type": "text/plain" }, body: JSON.stringify({ input: { messages: [human("q")] }, config }) });
  assert.equal(raw.status, 400);
  assert.match((await raw.json()).detail, /JSON body/);
  const theirs = await post({ input: { messages: [human("q")] }, config: { configurable: { thread_id: OTHER } } });
  assert.equal(theirs.status, 404);
  assert.match(theirs.headers.get("content-type"), /application\/json/);
  assert.deepEqual(await theirs.json(), { detail: "No such conversation." });
});

// #622: a failing turn ends, as every turn does, in one outcome event: the failed one the turn carried, else the
// model's (or KGE's, where the error says so) with its plain sentence and no detail; never a fixed error event.
test("a failing turn ends in a failed outcome that names nothing upstream (#622)", async () => {
  const original = console.error;
  console.error = () => {};
  try {
    const failed = async (resume) => events(await (await post({ command: { resume }, config })).text());
    const model = await failed("throw");
    assert.doesNotMatch(JSON.stringify(model), /secret/);
    assert.equal(model.some(([event]) => event === "error"), false);
    assert.deepEqual(model.at(-1), ["custom", { kind: "outcome", outcome: "failed", steps: 0, error: { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" } }]);
    const kge = await failed("kge");
    assert.deepEqual(kge.at(-1), ["custom", { kind: "outcome", outcome: "failed", steps: 1, error: { case: "kge_unreachable", message: "Couldn't reach the knowledge graph.", action: "try_again" } }]);
    const carried = await failed("carried");
    assert.equal(carried.at(-1)[1].error.case, "too_long", "the outcome the turn put on its error");
    assert.equal(carried.filter(([, data]) => data?.kind === "outcome").length, 1);
    // A turn that fails before its first event, with no status, is a stream too, ending in its failed outcome.
    const first = events(await (await post({ input: { messages: [human("throw first")] }, config })).text());
    assert.deepEqual(first.map(([event]) => event), ["metadata", "custom"]);
    assert.equal(first.at(-1)[1].error.case, "model_failed");
  } finally {
    console.error = original;
  }
});

test("a page that goes away aborts the turn", async () => {
  calls.length = 0;
  const controller = new AbortController();
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ input: { messages: [human("slow")] }, config }),
    signal: controller.signal,
  });
  const reader = res.body.getReader();
  await reader.read();
  controller.abort();
  for (let i = 0; i < 50 && !calls.some((c) => c.aborted); i += 1) await new Promise((r) => setTimeout(r, 10));
  assert.ok(calls.some((c) => c.aborted));
});

test("a body already parsed by express is read as given; a turn the page left is ended, so its clean-up runs (#625)", async () => {
  const seen = [];
  let cleaned = false;
  const handler = cerebroStream({
    runTurn: async function* ({ messages }) {
      try {
        seen.push(messages);
        yield ["values", { messages }];
        yield ["values", { messages }];
        yield ["values", { messages }];
      } finally {
        cleaned = true;
      }
    },
  });
  const written = [];
  let close;
  const res = { on: (_event, listener) => { close = listener; }, setHeader() {}, writeHead() {}, write: (s) => { written.push(s); if (written.length === 2) close(); }, end() {} };
  await handler({ body: { input: { messages: [{ type: "human", content: "hi" }] }, config } }, res);
  assert.equal(seen[0][0].content, "hi");
  assert.match(written.at(-1), /^event: values/);
  assert.equal(written.length, 2, "nothing is written once the page has gone");
  assert.ok(cleaned, "the turn was ended");
});
