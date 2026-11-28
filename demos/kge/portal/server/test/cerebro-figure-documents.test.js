import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { RESULT_MAX_BYTES, cerebroAgent, savedConversation } from "../src/cerebro/agent.js";
import { followRun } from "../src/cerebro/runs.js";
import { openConversations } from "../src/conversations/index.js";
import { kgeStub, standIn } from "./fixtures/cerebro-stand-in.js";

// The figure and SQL are from real W03; the document bindings below are a controlled, valid-period test setup.
const golden = JSON.parse(readFileSync(new URL("./fixtures/cerebro-w03-golden.json", import.meta.url), "utf8"));
const realContext = JSON.parse(readFileSync(new URL("./fixtures/cerebro-w03-context.json", import.meta.url), "utf8"));
// Real MCP responses from a scratch graph with a valid-period silver document and a synthetic bronze reference.
const scratch = JSON.parse(readFileSync(new URL("./fixtures/cerebro-w03-scratch-evidence.json", import.meta.url), "utf8"));
const question = golden.question;
const figure = { ...golden, certified_definitions: [{ ...golden.certified_definitions[0], documents: ["DD-WEALTH", "MD-NET-NEW-ASSETS"] }] };
const sources = [
  { n: 1, doc: "DD-WEALTH", title: "Wealth Management Data Dictionary", citable: true, cert_from: "2024-01-01", cert_to: "", terms: ["Net New Assets"] },
  { n: 2, doc: "MD-NET-NEW-ASSETS", title: "Metric Definition: Net New Assets", citable: true, cert_from: "2024-01-01", cert_to: "", terms: ["Net New Assets"] },
  { n: 3, doc: "TEST-BRONZE", title: "Synthetic bronze", citable: false },
];
const exact = [
  { doc: "DD-WEALTH", title: sources[0].title, chunk: "DD-WEALTH#0", tier: "gold", citable: true,
    passage: { text: "Gold describes a declared NNA measure.", truncated: false } },
  { doc: "MD-NET-NEW-ASSETS", title: sources[1].title, chunk: "MD-NET-NEW-ASSETS#0", tier: "silver", citable: true,
    attribution: "Wealth Data Steward", passage: { text: "Money clients bring to the firm less money they take away.", truncated: false } },
  { doc: "TEST-BRONZE", title: sources[2].title, chunk: "TEST-BRONZE#0", tier: "bronze", citable: false,
    passage: { text: "Bronze may not be cited.", truncated: false } },
];

for (const [number, expected] of [[1, "DD-WEALTH"], [2, "MD-NET-NEW-ASSETS"]]) test(`NNA figure keeps SQL and displays ${expected} evidence without bronze`, async () => {
  const stub = kgeStub((name) => ({ value: name === "ask" ? figure : name === "get_context"
    ? { in_window: [1, 2, 3], sources, block: "" } : name === "retrieve" ? { exact } : {} }));
  const model = standIn((_messages, n) => n === 1 ? { call: ["ask", { question }] }
    : { say: JSON.stringify({ answer: exact[number - 1].passage.text, sources: [number] }) });
  const run = cerebroAgent({ model, kge: stub, conversations: openConversations(), retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }],
    req: { portalIdentity: { principal: "reader" } }, roles: ["business"] })) events.push(event);
  const message = events.filter(([kind]) => kind === "values").at(-1)[1].messages.findLast((m) => m.type === "tool" && m.name === "ask");
  const stages = events.filter(([kind]) => kind === "values").map(([, state]) => state.messages.findLast((m) => m.type === "tool" && m.name === "ask")).filter(Boolean);
  const reply = JSON.parse(message.content);
  assert.ok(stages.some((m) => !JSON.parse(m.content).document_answer), "the figure streams before evidence is generated");
  assert.ok(stages.every((m) => m.id === message.id), "the evidence updates the same card");
  assert.equal(message.card, true);
  assert.equal(reply.figure, figure.figure);
  assert.equal(reply.sql, figure.sql);
  assert.equal(reply.answer_text, figure.answer_text);
  assert.deepEqual(reply.document_answer.excerpts.map((p) => p.doc), [expected]);
  assert.doesNotMatch(JSON.stringify(reply.document_answer), /TEST-BRONZE|Bronze may not be cited/);
  if (number === 2) assert.equal(reply.document_answer.excerpts[0].attribution, "Wealth Data Steward");
});

test("real W03 does not cite its later-dated NNA definition for June 2025", async () => {
  const stub = kgeStub((name) => ({ value: name === "ask" ? golden : name === "get_context" ? realContext : { exact } }));
  const model = standIn((_messages, n) => n === 1 ? { call: ["ask", { question }] } : { say: "Unexpected" });
  const run = cerebroAgent({ model, kge: stub, conversations: openConversations(), retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }],
    req: { portalIdentity: { principal: "reader" } }, roles: ["business"] })) events.push(event);
  const message = events.filter(([kind]) => kind === "values").at(-1)[1].messages.findLast((m) => m.type === "tool" && m.name === "ask");
  const answer = JSON.parse(message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.sql, golden.sql);
  assert.equal(answer.document_answer, undefined);
  assert.equal(stub.calls.some((c) => c.name === "retrieve"), true);
});

test("real scratch MCP evidence binds both NNA sources without attributing the bronze reference", async () => {
  const stub = kgeStub((name) => ({ value: name === "ask" ? golden : name === "get_context" ? scratch.context : name === "retrieve" ? scratch.passages : {} }));
  const model = standIn((messages, n) => {
    if (n === 1) return { call: ["ask", { question }] };
    const input = JSON.parse(messages[1].content);
    const gold = input.sources.find((s) => s.text.includes("| nna_counts |"));
    const silver = input.sources.find((s) => s.text.includes("Money clients bring to the firm"));
    assert.ok(gold && silver);
    return { say: JSON.stringify({ selections: [
      { source: gold.number, text: "| nna_counts | BOOLEAN | True where flows of this type count towards net new assets |" },
      { source: silver.number, text: "**Definition.** Money clients bring to the firm less money they take away, in USD, over released months: eligible flows only, test accounts included." },
    ] }) };
  });
  const run = cerebroAgent({ model, kge: stub, conversations: openConversations(), retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }],
    req: { portalIdentity: { principal: "reader" } }, roles: ["business"] })) events.push(event);
  const message = events.filter(([kind]) => kind === "values").at(-1)[1].messages.findLast((m) => m.type === "tool" && m.name === "ask");
  const answer = JSON.parse(message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.sql, golden.sql);
  assert.deepEqual(answer.document_answer.excerpts.map((p) => p.doc), ["DD-WEALTH", "MD-NET-NEW-ASSETS"]);
  assert.match(answer.document_answer.excerpts[1].attribution, /Wealth Data Steward/);
  assert.match(answer.document_answer.intro, /\[1\].*\[2\]/);
  assert.doesNotMatch(JSON.stringify(answer.document_answer), /EM-003|SYNTHETIC TEST BRONZE/);
});

test("an unavailable evidence service leaves the authoritative figure usable", async () => {
  const stub = kgeStub((name) => ({ value: name === "ask" ? figure : {} }));
  const request = stub.request;
  stub.request = async (ctx, mount, init) => {
    if (init.body && JSON.parse(init.body).params.name === "get_context") throw Error("unavailable");
    return request(ctx, mount, init);
  };
  const run = cerebroAgent({ model: standIn(() => ({ call: ["ask", { question }] })), kge: stub, conversations: openConversations(), retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread: globalThis.crypto.randomUUID(), messages: [{ type: "human", content: question }],
    req: { portalIdentity: { principal: "reader" } }, roles: ["business"] })) events.push(event);
  const message = events.filter(([kind]) => kind === "values").at(-1)[1].messages.findLast((m) => m.type === "tool" && m.name === "ask");
  const answer = JSON.parse(message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.sql, golden.sql);
  assert.equal(answer.document_answer, undefined);
});

test("background figure retains the golden value and SQL with permitted silver evidence", async () => {
  const runId = "feedc0de".repeat(4);
  const stub = kgeStub((name) => name === "ask" ? { value: { run: { id: runId, state: "running" } } }
    : name === "question_run" ? { value: { id: runId, state: "done", answer: figure } }
      : { value: name === "get_context" ? { in_window: [1, 2, 3], sources, block: "" } : name === "retrieve" ? { exact } : {} });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const req = { portalIdentity: { principal: "reader" } };
  const run = cerebroAgent({ model: standIn(() => ({ call: ["ask", { question }] })), kge: stub, conversations, retryAfterMs: 0 });
  for await (const unused of run({ thread, messages: [{ type: "human", content: question }], req, roles: ["business"] })) void unused;
  const pending = (await savedConversation(conversations, "reader", thread)).messages.find((m) => m.type === "tool" && m.name === "ask");
  const model = { invoke: async () => ({ content: JSON.stringify({ answer: exact[1].passage.text, sources: [2] }) }) };
  const args = { conversations, kge: stub, req, roles: ["business"], id: thread, messageId: pending.id, model };
  const first = await followRun(args);
  assert.equal(first.evidence_pending, true);
  assert.equal(JSON.parse(first.message.content).figure, golden.figure);
  assert.equal(JSON.parse(first.message.content).document_answer, undefined);
  const result = await followRun(args);
  assert.equal(result.evidence_pending, false);
  const answer = JSON.parse(result.message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.sql, golden.sql);
  assert.deepEqual(answer.document_answer.excerpts.map((p) => p.doc), ["MD-NET-NEW-ASSETS"]);
  assert.equal(answer.document_answer.excerpts[0].attribution, "Wealth Data Steward");
  assert.doesNotMatch(JSON.stringify(answer.document_answer), /TEST-BRONZE/);
});

for (const background of [false, true]) test(`${background ? "background" : "foreground"} omits evidence that would truncate an authoritative near-limit figure`, async () => {
  const base = { ...figure, sql: "" };
  const longSql = "s".repeat(RESULT_MAX_BYTES - Buffer.byteLength(JSON.stringify(base)) - 150);
  const large = { ...base, sql: longSql };
  const runId = "dcbaabcd".repeat(4);
  const stub = kgeStub((name) => name === "ask" ? { value: background ? { run: { id: runId, state: "running" } } : large }
    : name === "question_run" ? { value: { id: runId, state: "done", answer: large } }
      : { value: name === "get_context" ? { in_window: [1, 2], sources, block: "" } : name === "retrieve" ? { exact } : {} });
  const model = standIn((_messages, n) => n === 1 ? { call: ["ask", { question }] }
    : { say: JSON.stringify({ answer: exact[1].passage.text, sources: [2] }) });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const req = { portalIdentity: { principal: "reader" } };
  const run = cerebroAgent({ model, kge: stub, conversations, retryAfterMs: 0 });
  const events = [];
  for await (const event of run({ thread, messages: [{ type: "human", content: question }], req, roles: ["business"] })) events.push(event);
  let message = events.filter(([kind]) => kind === "values").at(-1)[1].messages.findLast((m) => m.type === "tool" && m.name === "ask");
  if (background) {
    const args = { conversations, kge: stub, req, roles: ["business"], id: thread, messageId: message.id,
      model: { invoke: async () => ({ content: JSON.stringify({ answer: exact[1].passage.text, sources: [2] }) }) } };
    const first = await followRun(args);
    const final = first.evidence_pending ? await followRun(args) : first;
    message = final.message;
  }
  const answer = JSON.parse(message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.sql, longSql);
  assert.equal(answer.document_answer, undefined);
});

test("a pending background figure survives reload and uses current access for its evidence", async () => {
  const runId = "facecafe".repeat(4);
  let readable = true;
  const stub = kgeStub((name) => name === "ask" ? { value: { run: { id: runId, state: "running" } } }
    : name === "question_run" ? { value: { id: runId, state: "done", answer: figure } }
      : { value: name === "get_context" ? { in_window: readable ? [1, 2] : [], sources, block: "" } : name === "retrieve" ? { exact } : {} });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const req = { portalIdentity: { principal: "reader" } };
  const run = cerebroAgent({ model: standIn(() => ({ call: ["ask", { question }] })), kge: stub, conversations, retryAfterMs: 0 });
  for await (const unused of run({ thread, messages: [{ type: "human", content: question }], req, roles: ["business"] })) void unused;
  const pending = (await savedConversation(conversations, "reader", thread)).messages.find((m) => m.type === "tool" && m.name === "ask");
  const args = { conversations, kge: stub, req, roles: ["business"], id: thread, messageId: pending.id,
    model: { invoke: async () => { throw new Error("revoked evidence must never reach the model"); } } };
  assert.equal((await followRun(args)).evidence_pending, true);
  const reloaded = (await savedConversation(conversations, "reader", thread)).messages.find((m) => m.id === pending.id);
  assert.equal(JSON.parse(reloaded.content).document_evidence_pending, true);
  readable = false;
  const result = await followRun(args);
  const answer = JSON.parse(result.message.content);
  assert.equal(result.evidence_pending, false);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.document_answer, undefined);
  assert.equal(answer.document_evidence_pending, undefined);
});

test("Stop clears pending evidence without calling a model or changing the figure", async () => {
  const runId = "acdcabcd".repeat(4);
  const stub = kgeStub((name) => name === "ask" ? { value: { run: { id: runId, state: "running" } } }
    : name === "question_run" ? { value: { id: runId, state: "done", answer: figure } } : { value: {} });
  const conversations = openConversations();
  const thread = globalThis.crypto.randomUUID();
  const req = { portalIdentity: { principal: "reader" } };
  const run = cerebroAgent({ model: standIn(() => ({ call: ["ask", { question }] })), kge: stub, conversations, retryAfterMs: 0 });
  for await (const unused of run({ thread, messages: [{ type: "human", content: question }], req, roles: ["business"] })) void unused;
  const pending = (await savedConversation(conversations, "reader", thread)).messages.find((m) => m.type === "tool" && m.name === "ask");
  const args = { conversations, kge: stub, req, roles: ["business"], id: thread, messageId: pending.id };
  assert.equal((await followRun(args)).evidence_pending, true);
  const ended = await followRun({ ...args, stop: true, model: { invoke: async () => { throw Error("must not run"); } } });
  const answer = JSON.parse(ended.message.content);
  assert.equal(answer.figure, golden.figure);
  assert.equal(answer.document_evidence_pending, undefined);
  assert.equal(stub.calls.some((c) => c.name === "retrieve"), false);
});
