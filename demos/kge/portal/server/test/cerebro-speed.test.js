import assert from "node:assert/strict";
import { isStepRead, stepReads } from "./fixtures/cerebro-stand-in.js";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { AIMessage, ToolMessage } from "@langchain/core/messages";
import { ASKED_AGAIN, CARD_RULE, CATALOGUE_ABSTENTION, CHOICE_RULE, FOLLOW_UP_RULE, LATEST_RULE, PHRASE_RULE, PLAIN_RULE, RECOVER_RULE, ROUTING_EXAMPLES, ROUTING_RULE, SHAPE_RULE, SMALL_TALK_RULE, SPOKEN_MAX, busy, cerebroAgent, isCard, netTool, outcomeOf } from "../src/cerebro/agent.js";
import { memoryStore, openConversations } from "../src/conversations/index.js";
import { openCerebroModel, thinkingLevelFor } from "../src/cerebro/model.js";
import { FAILURES, failureOf, givesFigure, plainWords, readoutOf } from "../src/cerebro/status.js";
import { allowedTools } from "../src/cerebro/tools.js";

// #622: the right tool per question, the safety net, non-ask cards, the working block's events, one outcome per turn,
// failures by case, one retry for a busy model, and thinking level low. A stand-in model and a stubbed KGE client (the
// portal's kge object: sessionBearer and request), synthetic data only; nothing leaves the process.
// #625: the conversations every turn here is saved in, so a pick resumes the turn it paused.
const conversations = openConversations({ store: memoryStore() });

// A stand-in model: decide(messages, n) gives each call's step ({ call: [name, args] }, { calls: [[name, args], ...] }
// in parallel, { say }, or throws).
function standIn(decide) {
  const model = { calls: 0, read: [] };
  model.bindTools = () => model;
  model.invoke = async (messages) => {
    model.calls += 1;
    model.read.push(messages);
    const step = decide(messages, model.calls);
    if (step.call) return new AIMessage({ content: "", tool_calls: [{ id: `c-${model.calls}`, name: step.call[0], args: step.call[1] }] });
    if (step.calls) return new AIMessage({ content: "", tool_calls: step.calls.map(([name, args], i) => ({ id: `c-${model.calls}-${i}`, name, args })) });
    return new AIMessage(step.say ?? "");
  };
  return model;
}
const toolsThisTurn = (messages) => messages.slice(messages.findLastIndex((m) => m._getType() === "human")).filter((m) => m._getType() === "tool");
// Calls ask with the question once; afterwards says one sentence.
const asksOnce = (question) => standIn((messages) => (toolsThisTurn(messages).length ? { say: "I could not find that." } : { call: ["ask", { question }] }));

// The stubbed KGE client: reply(name, args) gives { value } (an MCP result), { events, value } (ask's event stream),
// { isError: text }, { status }, { body } (raw), { throws }, or the portal's own time limit ({ timeout } before the
// response, { streamTimeout } mid-stream).
function kgeStub(reply) {
  const calls = [];
  const sse = (frames) => new Response(frames.map((m) => `event: message\ndata: ${JSON.stringify(m)}\n\n`).join(""), { status: 200, headers: { "content-type": "text/event-stream" } });
  return {
    calls,
    sessionBearer: async () => "bearer-1",
    request: async (_ctx, mount, init) => {
      const message = JSON.parse(init.body);
      const { name, arguments: args } = message.params;
      // #714: the step before ask's own reads find nothing bound and nothing open, and are not among the calls counted.
      if (isStepRead(name, args)) return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { structuredContent: stepReads()(name, args).value, isError: false } }), { status: 200, headers: { "content-type": "application/json" } });
      calls.push({ mount, name, args });
      const out = reply(name, args) ?? { value: {} };
      if (out.throws) throw new Error(out.throws);
      if (out.timeout) throw new DOMException("The operation was aborted due to timeout", "TimeoutError");
      if (out.streamTimeout) {
        // The portal's ask timeout firing mid-answer: the stream has started, then its body fails as the abort does.
        const body = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(": open\n\n")); }, pull(controller) { controller.error(new DOMException("The operation was aborted due to timeout", "TimeoutError")); } });
        return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      if (out.status) return new Response("{}", { status: out.status, headers: { "content-type": "application/json" } });
      if (out.body !== undefined) return new Response(out.body, { status: 200, headers: { "content-type": "application/json" } });
      const result = out.isError !== undefined ? { content: [{ type: "text", text: out.isError }], isError: true } : { structuredContent: out.value, isError: false };
      if (name === "ask" && message.params._meta?.progressToken !== undefined) {
        const token = message.params._meta.progressToken;
        return sse([...(out.events ?? []).map((event, i) => ({ jsonrpc: "2.0", method: "notifications/progress", params: { progressToken: token, progress: i + 1, message: JSON.stringify(event) } })), { jsonrpc: "2.0", id: message.id, result }]);
      }
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result }), { status: 200, headers: { "content-type": "application/json" } });
    },
  };
}

/** One turn through runTurn, as route.js runs it: its events, and the error (with its outcome) where it threw. A resume
 *  answers the card of the conversation thread names. */
// #621: the person's message is a full, standalone question by default, so the portal frames none of these turns.
async function turn(model, kge, { roles = ["business"], question = "What were net new assets?", thread = globalThis.crypto.randomUUID(), resume } = {}) {
  const events = [];
  let error;
  try {
    const runTurn = cerebroAgent({ model, kge, conversations, retryAfterMs: 0 });
    const asked = resume === undefined ? { messages: [{ type: "human", id: "h-1", content: question }] } : { resume };
    for await (const event of runTurn({ thread, ...asked, roles, signal: new AbortController().signal, req: { portalIdentity: { principal: "person-1" } } })) events.push(event);
  } catch (caught) {
    error = caught;
  }
  // #651: the reply's record follows the outcome (replies); the outcome is the last event before it.
  const custom = events.filter(([e]) => e === "custom").map(([, d]) => d).filter((d) => d.kind !== "reply");
  const replies = events.filter(([e, d]) => e === "custom" && d.kind === "reply").map(([, d]) => d);
  const values = events.filter(([e]) => e === "values").at(-1)?.[1];
  return { events, custom, replies, status: custom.filter((d) => d.kind !== "ask_progress"), outcome: error?.outcome ?? custom.at(-1), values, error, thread };
}
const ANSWER = { behaviour: "figure", answer_mode: "certified", answer_text: "Certified: net new assets were USD 12.", value: 12 };
const ABSTAIN = { behaviour: "abstain", answer_mode: "not_sure", answer_text: `No answer: ${CATALOGUE_ABSTENTION}`, reason: CATALOGUE_ABSTENTION };
const CONTEXT = { arm: "treatment", block: "Terms:\n  Wealth client: a client of the wealth desk. Steward: Wealth Data Steward.", sources: [] };
const EXPLORED = { node: { kind: "term", id: "term:Net New Assets", name: "Net New Assets" }, summary: "The question names Net New Assets (term).", link: "/cerebro?mode=graph" };

test("(a) the system prompt routes each question type to its tool, with worked examples, and keeps #619's rules (#622)", async () => {
  const model = standIn(() => ({ say: "ok" }));
  await turn(model, undefined);
  const prompt = model.read[0][0].content;
  for (const rule of [SMALL_TALK_RULE, ROUTING_RULE, ROUTING_EXAMPLES, CARD_RULE, LATEST_RULE, PLAIN_RULE, FOLLOW_UP_RULE, CHOICE_RULE, PHRASE_RULE, RECOVER_RULE]) assert.ok(prompt.includes(rule));
  assert.match(prompt, /Figures, tables and trends \(how much, how many, a total, by month, by region\): call ask/);
  assert.match(prompt, /One term's meaning \("what does X mean"\), stewards, owners, sources, and what data there is about a subject: call get_context/);
  assert.match(prompt, /The difference between two terms \("what is the difference between X and Y", "how is X different from Y", "X vs Y meaning"\): call ask/);
  assert.match(prompt, /Which tables or columns hold something, how things connect, and lineage: call explore_question, or explore/);
  assert.match(prompt, /in the catalogue by name or keyword: call search/);
  assert.match(prompt, /A business term's data sets and documents: call map_term/);
  for (const [question, tool] of [["What was net new assets in June 2025?", "ask"], ["What is the difference between current NNA and certified NNA?", "ask"],
    ["What does net new assets mean?", "get_context"], ["Who is the steward of assets under advice?", "get_context"],
    ["What data do we have about wealth clients?", "get_context"], ["Which tables hold net new assets?", "explore_question"], ["Find the customer data products", "search"],
    ["Where is the term Net New Assets used?", "map_term"]]) {
    assert.ok(prompt.includes(`"${question}" calls ${tool}`), `${question} → ${tool}`);
  }
  // #714: the portal's step before ask reads what a question leaves open; the model is told of no discovery tool.
  assert.doesNotMatch(prompt, /discover_meanings/);
  assert.ok(prompt.includes(SHAPE_RULE), "a question's shape may be rewritten for ask, never its meaning");
  assert.match(prompt, /the portal has checked with the knowledge graph what it leaves open and asked the person on its own card/);
  assert.doesNotMatch(prompt, /for a business question call ask/);
});

test("(b) Ask's catalogue decline runs get_context in the same step; its card ends the turn with no second model call (#622)", async () => {
  const model = asksOnce("What data do we have about wealth clients?");
  const kge = kgeStub((name) => (name === "ask" ? { value: ABSTAIN } : name === "get_context" ? { value: CONTEXT } : undefined));
  const { values, outcome, status } = await turn(model, kge);
  assert.equal(model.calls, 1, "no second model call");
  assert.deepEqual(kge.calls.map((c) => [c.name, c.args.question]), [["ask", "What data do we have about wealth clients?"], ["get_context", "What data do we have about wealth clients?"]]);
  assert.deepEqual(values.messages.map((m) => [m.type, m.name ?? m.tool_calls?.map((c) => c.name).join()]), [["human", undefined], ["ai", "ask"], ["tool", "ask"], ["ai", "get_context"], ["tool", "get_context"]]);
  const net = values.messages[3].tool_calls[0];
  assert.match(net.id, /^net-/);
  assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  assert.deepEqual(status.filter((d) => d.kind === "step").map((d) => d.step), ["understanding", "planning", "context"]);
});

test("(b) a question about where data is held or how it connects nets to explore_question; the net runs once a turn (#622)", async () => {
  for (const question of ["Which tables hold net new assets?", "Which columns hold the branch?", "How is the branch table connected to accounts?", "Show the lineage of revenue"]) {
    assert.equal(netTool(question), "explore_question", question);
  }
  for (const question of ["Who is the steward of assets under advice?", "What data do we have about wealth clients?", "What does NNA mean?"]) assert.equal(netTool(question), "get_context", question);
  const model = asksOnce("Which tables hold net new assets?");
  const kge = kgeStub((name) => (name === "ask" ? { value: ABSTAIN } : { value: EXPLORED }));
  const { outcome } = await turn(model, kge);
  assert.deepEqual(kge.calls.map((c) => c.name), ["ask", "explore_question"]);
  assert.equal(model.calls, 1);
  assert.equal(outcome.outcome, "answered");

  // An empty net result is no card: the model is called again; asking again gets the same decline, and no second net.
  const again = standIn((messages) => (toolsThisTurn(messages).filter((m) => m.name === "ask").length < 2 ? { call: ["ask", { question: "What data do we have about clients?" }] } : { say: "Try Search in the catalogue." }));
  const empty = kgeStub((name) => (name === "ask" ? { value: ABSTAIN } : { value: { arm: "treatment", block: "", sources: [] } }));
  const second = await turn(again, empty);
  assert.deepEqual(empty.calls.map((c) => c.name), ["ask", "get_context", "ask"], "one net a turn");
  assert.equal(again.calls, 3);
  assert.deepEqual(second.outcome, { kind: "outcome", outcome: "failed", steps: 6, error: { case: "not_planned", message: CATALOGUE_ABSTENTION, action: "rephrase" } }, "Ask's whole reason, its pointer to Search included");

  // Another decline reason is no catalogue decline: no net.
  const other = kgeStub(() => ({ value: { ...ABSTAIN, reason: "No glossary term matches the question." } }));
  await turn(asksOnce("NNA?"), other);
  assert.deepEqual(other.calls.map((c) => c.name), ["ask"]);
});

test("(c) a non-ask tool's card ends the turn at its card; an empty or failed result does not (#622)", async () => {
  const cards = {
    get_context: CONTEXT, explore_question: EXPLORED, explore: EXPLORED, read_graph: { nodes: [{ id: "term:x" }], links: [] },
    search: { hits: [{ kind: "metric", title: "Net New Assets" }] }, map_term: { term: "Net New Assets", implementations: [{ unit: "wealth" }] },
  };
  for (const [name, value] of Object.entries(cards)) {
    const args = name === "search" ? { query: "nna" } : name === "map_term" ? { term: "nna" } : name === "explore" || name === "read_graph" ? { node: "term:x" } : { question: "nna" };
    const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "Here it is." } : { call: [name, args] }));
    const { values, outcome } = await turn(model, kgeStub(() => ({ value })));
    assert.equal(model.calls, 1, `${name}: no second model call`);
    assert.equal(values.messages.at(-1).type, "tool", `${name}: the card is the turn's last message`);
    assert.equal(outcome.outcome, "answered");
    assert.equal(isCard(name, value), true);
  }
  assert.equal(isCard("get_context", { arm: "treatment", block: "", sources: [{ doc: "d", title: "Data dictionary", citable: true }] }), true, "citable sources alone are a card");
  // The page draws a get_context card only for a term it parses or a citable source (chatAnswer.js definitionsOf).
  for (const pkg of [{ block: "Columns by role:\n  x", sources: [{ n: 1, doc: "b", citable: false }] }, { block: "Decided: nothing.", sources: [] },
    { block: "Terms:\n   deeper: not a term line", sources: [{ doc: "", citable: true }] }, { block: "{not json", sources: [{ doc: "d", citable: "yes" }] }]) {
    assert.equal(isCard("get_context", pkg), false, JSON.stringify(pkg));
  }
  for (const pkg of [{ block: "Not glossary terms:\n  x: y\nTerms:\n  churn: Customers lost." }, { block: JSON.stringify({ contexts: [{ id: "ctx.a", label: "A" }] }) },
    { plan_vocabulary: { contexts: [{ id: "c", label: "Churn" }] }, block: "" }]) {
    assert.equal(isCard("get_context", pkg), true, JSON.stringify(pkg));
  }
  const noCard = standIn((messages) => (toolsThisTurn(messages).length ? { say: "No terms matched." } : { call: ["get_context", { question: "q" }] }));
  const spoken = await turn(noCard, kgeStub(() => ({ value: { arm: "treatment", block: "Columns by role:\n  x", sources: [{ n: 1, doc: "b", citable: false }] } })));
  assert.equal(noCard.calls, 2, "a package the page draws no card for: the model speaks");
  assert.equal(spoken.values.messages.at(-1).content, "No terms matched.");
  assert.equal(isCard("map_term", { term: "Net New Assets", principal: "p", compact: true, data_sets: [{ unit: "wealth", unit_label: "Wealth Management" }] }), true, "KGE's compact map is a card");
  assert.equal(isCard("map_term", { term: "x", principal: "p", compact: true, data_sets: [], defined_by: [], contexts: [] }), false, "a map naming no data set, document or context is not");
  const empty = { get_context: { arm: "treatment", block: " ", sources: [] }, explore_question: { node: null, summary: "No node.", link: "/" }, read_graph: { nodes: [], links: [] },
    search: { hits: [] }, map_term: { term: "x", implementations: [], defined_by: [] }, explore: { error: "404: no" }, retrieve: { chunks: [1] } };
  for (const [name, value] of Object.entries(empty)) assert.equal(isCard(name, value), false, name);
  const model = standIn((messages) => (toolsThisTurn(messages).length ? { say: "Nothing matched." } : { call: ["search", { query: "zz" }] }));
  const { values } = await turn(model, kgeStub(() => ({ value: { hits: [] } })));
  assert.equal(model.calls, 2, "no hits: the model speaks");
  assert.equal(values.messages.at(-1).content, "Nothing matched.");
});

// Two compact ask streams as KGE streams them to a business principal, recorded from the local KGE and given invented
// names: the graph path's nodes and edges as they are added and change, the context, and the warehouse's progress per
// statement. KGE records a Words node for each term the question spells, matched or not ("office area", "online").
const RECORDED = JSON.parse(readFileSync(new URL("./fixtures/ask-stream-compact.json", import.meta.url), "utf8"));
// An engineer's stream adds the stages and the plan, nested as KGE sends it; its SQL is never forwarded.
const PLAN = { event: "plan", plan: { sql: "SELECT SUM(AMT_9) FROM advisory.FLOW_FACT_9", sql_source: "rules", guard: { passed: true, problems: [], failed_at: null, checks: [] }, unit: "advisory" } };
const STREAM = [{ event: "stage", stage: "plan", state: "running" }, ...RECORDED.flow.events.flatMap((e) => (e.event === "context" ? [PLAN, e] : [e]))];
const finalReadout = (question, events) => {
  const readout = readoutOf(question);
  return events.map((e) => readout.take(e)).filter(Boolean);
};

test("(d) a turn's events: its steps, the graph path, the readout, the warehouse's state and one outcome, last (#622)", async () => {
  const kge = kgeStub(() => ({ events: RECORDED.flow.events, value: ANSWER }));
  const { custom, status, outcome, events } = await turn(asksOnce(RECORDED.flow.question), kge);
  const kinds = status.map((d) => (d.kind === "step" ? `step:${d.step}` : d.kind));
  assert.equal(kinds[0], "step:understanding");
  assert.equal(kinds[1], "step:planning");
  assert.deepEqual(kinds.filter((k) => !k.startsWith("readout")).slice(2), ["step:warehouse", "warehouse", "warehouse", "warehouse", "warehouse", "step:checking", "outcome"]);
  assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 4 });
  assert.equal(status.filter((d) => d.kind === "outcome").length, 1, "exactly one outcome");
  assert.equal(custom.at(-1).kind, "outcome", "the outcome is the last event");
  // #651: then the reply's record alone, its steps and its readout's last message.
  assert.deepEqual(events.slice(-2).map(([, d]) => d.kind), ["outcome", "reply"]);
  assert.equal(events.at(-1)[1].id, "h-1");
  assert.deepEqual(events.at(-1)[1].reply.steps.map((d) => d.step), ["understanding", "planning", "warehouse", "checking"]);
  assert.deepEqual(events.at(-1)[1].reply.readouts.map((r) => r.tool_call_id), ["c-1"]);
  assert.match(events.at(-1)[1].reply.readouts[0].message, /^This reads Net client inflow\./);
  assert.equal(status[1].tool_call_id, "c-1");
  assert.deepEqual(status.filter((d) => d.kind === "warehouse").map(({ statement, state }) => [statement, state]), [[1, "running"], [1, "done"], [2, "running"], [2, "done"]]);

  const progress = custom.filter((d) => d.kind === "ask_progress").map((d) => d.event.event);
  assert.ok(progress.includes("edge_added") && progress.includes("node_added") && progress.includes("state_changed"));
  assert.equal(progress.includes("stage"), false, "stages are the engineer's");
  for (const forbidden of ["plan", "context", "progress", "graph_summary"]) assert.equal(progress.includes(forbidden), false, forbidden);
  assert.doesNotMatch(JSON.stringify(custom), /SELECT|sql|AMT|bytes_processed|"detail"/);

  const readouts = status.filter((d) => d.kind === "readout");
  assert.ok(readouts.length >= 2, "sent again as it grows");
  assert.deepEqual(readouts.at(-1), { kind: "readout", tool_call_id: "c-1", readout: { filters: ["Eligible for net client inflow", "Closed periods only"], grouping: [], measure: "Net client inflow", mode: "Current", period: "March 2024", source: "Advisory Services" },
    message: "This reads Net client inflow. It covers March 2024 on a current basis. It is filtered to Eligible for net client inflow and Closed periods only. The source is Advisory Services." });
  for (const { message } of readouts) assert.equal(givesFigure(message), false, `#651 B3: ${message}`);
  assert.doesNotMatch(JSON.stringify(readouts), /office area/, "words the path matched to nothing are no filter and no grouping");
  for (const { readout } of readouts) {
    const values = [readout.measure, readout.mode, readout.period, readout.source, ...readout.filters, ...readout.grouping].filter(Boolean);
    for (const value of values) {
      assert.equal(givesFigure(value), false, value);
      assert.doesNotMatch(value, /_|\w\.\w|SELECT|:/, value);
    }
  }
});

test("(d) an engineer's progress adds the stages; nobody gets the plan; the readout's words drop physical names and figures (#622)", async () => {
  const kge = kgeStub(() => ({ events: STREAM, value: ANSWER }));
  const { custom } = await turn(asksOnce("NNA"), kge, { roles: ["engineer"] });
  assert.ok(custom.some((d) => d.kind === "ask_progress" && d.event.event === "stage"));
  assert.doesNotMatch(JSON.stringify(custom), /SELECT/);
  for (const value of ["FLOW_CD_1", "ACCT_TYPE", "wealth.flows", "wealth.FLOW_FACT_1", "Amount over 5,000", "x".repeat(81), "", 42, null]) assert.equal(plainWords(value), undefined, String(value));
  for (const value of ["Net new assets", "June 2025", "FY26-Q1", "Region NW"]) assert.equal(plainWords(value), value);
  const readout = readoutOf("q");
  assert.equal(readout.take({ event: "plan", sql: "SELECT 1" }), undefined, "the plan is never read");
  assert.equal(readout.take({ event: "node_added", node: { id: "Words:zz", type: "Words", name: "zz" } }), undefined, "unmatched words are no filter");
  assert.equal(readout.take({ event: "context", context: { term: "Fees" } }).filters.length, 0, "nor once the context arrives");
  assert.equal(readout.take(null), undefined);
});

test("(d) the readout takes a filter or a grouping on positive evidence alone: a used rule, or words matched to what they mean (#622)", () => {
  // The recorded fees stream: "online" is a Words node no edge matches (the unit the question names), so it is left out.
  assert.deepEqual(finalReadout(RECORDED.fees.question, RECORDED.fees.events).at(-1), { filters: [], grouping: [], measure: "Fees", period: "March 2024", source: "Online Banking" });
  assert.ok(RECORDED.fees.events.some((e) => e.node?.id === "Words:online"), "the fixture keeps KGE's unmatched Words node");
  // Words matched (matches, or plans in a refused plan) to a node that is neither a measure nor the calendar; a used
  // RowSelector; a matched edge that arrives as edge_changed. An unused rule and an unmatched word are left out.
  const node = (id, extra = {}) => ({ event: "node_added", node: { id, type: id.split(":")[0], name: id.split(":")[1], label: "", used: true, state: "done", ...extra } });
  const edge = (from, to, type = "matches", event = "edge_added") => ({ event, edge: { from, to, type, label: type, planner: true, used: true, state: "done" } });
  const events = [
    node("Words:fees"), node("Words:desk"), node("Words:gold tier"), node("Words:last quarter"), node("Words:direct"), node("Words:spare"),
    node("Context:fees", { label: "Fees" }), node("Entity:desk", { label: "desk" }), node("Value:gold", { label: "Gold" }),
    node("RowSelector:active", { label: "Active holders only" }), node("Filter:unused", { label: "Not applied", used: false }),
    edge("Words:fees", "Context:fees"), edge("Words:desk", "Entity:desk"), edge("Words:gold tier", "Value:gold", "matches", "edge_changed"),
    edge("Words:last quarter", "Calendar:fiscal"), edge("Words:direct", "Plan:rules", "plans"), edge("Term:fees", "Entity:desk"),
    { event: "context", context: { term: "Fees" } },
  ];
  assert.deepEqual(finalReadout("Fees by desk for gold tier last quarter", events).at(-1),
    { filters: ["gold tier", "direct", "Active holders only"], grouping: ["desk"], measure: "Fees", period: "last quarter" });
});

// #622 requirement 8: each failure, simulated, ends the turn at once in a failed outcome with its case, sentence and
// action; the raw reason is an engineer's detail alone.
const CASES = [
  ["unsafe_query", "an ask reply the gate declined", () => ({ value: { behaviour: "refuse", answer_mode: "not_sure", answer_text: "No answer: SQL gate: unknown column FLOW_FACT_1.ZZ", reason: "SQL gate: unknown column FLOW_FACT_1.ZZ" } })],
  ["unsafe_query", "an ask error from the dry run", () => ({ isError: "Error executing tool ask: dry run failed: Binder Error: column ZZ" })],
  ["warehouse_failed", "a progress event with state failed", () => ({ events: [{ event: "progress", stage: "warehouse", state: "failed", statement: 1, elapsed_ms: 5 }], value: { behaviour: "abstain", answer_mode: "not_sure", answer_text: "No answer." } })],
  ["warehouse_failed", "the warehouse's error", () => ({ isError: "503: BigQuery error (HTTP 500): backendError" })],
  ["too_long", "the warehouse's time limit", () => ({ events: [{ event: "progress", stage: "warehouse", state: "failed", statement: 1, elapsed_ms: 90000 }], isError: "503: The warehouse exceeded its time limit; ask a narrower question." })],
  ["too_long", "the request budget's 503", () => ({ isError: "503: The request took longer than its time budget; try again." })],
  ["too_long", "a reply over the warehouse's limits", () => ({ value: { behaviour: "abstain", answer_mode: "not_sure", answer_text: "No answer.", reason: "The warehouse could not finish this question within its limits." } })],
  ["kge_unreachable", "fetch failed", () => ({ throws: "fetch failed" })],
  ["kge_unreachable", "a 502", () => ({ status: 502 })],
  ["kge_unreachable", "a body that is not JSON", () => ({ body: "<html>" })],
  ["kge_unreachable", "a stream with no reply", () => ({ body: "" })],
  ["too_long", "the portal's own ask timeout", () => ({ timeout: true })],
  ["too_long", "the portal's own ask timeout mid-stream", () => ({ streamTimeout: true })],
];
test("(e) each failure ends the turn at once in a failed outcome with its case, sentence and action (#622)", async () => {
  for (const [kind, what, reply] of CASES) {
    for (const roles of [["business"], ["engineer"]]) {
      const model = asksOnce("NNA for June 2025");
      const { outcome, values } = await turn(model, kgeStub(reply), { roles });
      assert.equal(model.calls, 1, `${what}: no second model call`);
      assert.equal(outcome.outcome, "failed", what);
      assert.equal(outcome.error.case, kind, what);
      assert.equal(outcome.error.message, FAILURES[kind].message, what);
      assert.equal(outcome.error.action, FAILURES[kind].action, what);
      const raw = reply().isError;
      if (roles[0] === "engineer" && raw) assert.equal(outcome.error.detail, raw, `${what}: an engineer reads KGE's own words`);
      if (roles[0] === "business") {
        assert.equal(outcome.error.detail, undefined, `${what}: no detail for a business user`);
        assert.doesNotMatch(JSON.stringify(values.messages), /BigQuery|Binder|KeyError|FLOW_FACT/, `${what}: KGE's own words stay the engineer's`);
      }
    }
  }
  const { outcome } = await turn(asksOnce("q"), kgeStub(() => ({ isError: "503: The request took longer than its time budget; try again." })), { roles: ["engineer"] });
  assert.equal(outcome.error.detail, "503: The request took longer than its time budget; try again.");
});

test("(e) not planned, paused and a refusal the model answers: each turn ends in its own outcome (#622)", async () => {
  const unsure = await turn(asksOnce("q"), kgeStub(() => ({ value: { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: "Not sure: no declared term matches \"book\". Try again." } })));
  assert.deepEqual(unsure.outcome.error, { case: "not_planned", message: "no declared term matches \"book\". Try again.", action: "rephrase" });
  // Ask's whole reason, up to the first sentence that gives a figure, and at most SPOKEN_MAX characters.
  const partly = await turn(asksOnce("q"), kgeStub(() => ({ value: { behaviour: "abstain", answer_mode: "not_sure", reason: "No released month matches. Use Search in the catalogue. Only 3 of 12 months were released. Ask again." } })));
  assert.equal(partly.outcome.error.message, "No released month matches. Use Search in the catalogue.");
  const long = await turn(asksOnce("q"), kgeStub(() => ({ value: { behaviour: "abstain", answer_mode: "not_sure", reason: `${"Plain words here. ".repeat(20)}` } })));
  assert.ok(long.outcome.error.message.length <= SPOKEN_MAX && long.outcome.error.message.endsWith("…"), long.outcome.error.message);
  const figure = await turn(asksOnce("q"), kgeStub(() => ({ value: { behaviour: "abstain", answer_mode: "not_sure", reason: "Only 3 of 12 months were released." } })));
  assert.equal(figure.outcome.error.message, "Ask could not plan this question as asked.", "never a figure in an error card");
  const clarify = await turn(asksOnce("q"), kgeStub(() => ({ value: { behaviour: "clarify", answer_mode: "not_sure", answer_text: "Which NNA?", clarification: { choices: [{ id: "a", label: "All" }] } } })));
  assert.deepEqual(clarify.outcome, { kind: "outcome", outcome: "paused", steps: 2 }, "#624: the HITL card pauses the turn, with no second model call");
  assert.equal(clarify.custom.filter((d) => d.kind === "outcome").length, 1);
  // #624 requirement 1: an AI-Generated answer is delivered, never held: the turn is answered at its card.
  const generated = await turn(asksOnce("q"), kgeStub(() => ({ value: { ...ANSWER, answer_mode: "ai_generated" } })));
  assert.deepEqual(generated.outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  const refused = await turn(asksOnce("q"), kgeStub(() => ({ isError: "403: You may not read the wealth unit." })));
  assert.equal(refused.outcome.outcome, "answered", "a refusal is no failure: the model answers it");
  const words = await turn(standIn(() => ({ say: "Hello." })), kgeStub(() => undefined));
  assert.deepEqual(words.outcome, { kind: "outcome", outcome: "answered", steps: 1 });
  for (const [text, status, kind] of [["403: no", undefined, undefined], ["", 404, undefined], ["", 429, "kge_unreachable"], ["", 500, "kge_unreachable"],
    ["400: The request is too large.", undefined, undefined], ["403: You may not query the warehouse.", undefined, undefined],
    ["Error executing tool ask: the warehouse argument is not allowed", undefined, undefined], ["Error executing tool read_graph: 1 validation error for read_graphArguments", undefined, undefined],
    ["Unknown tool: zz", undefined, undefined], ["400: SQL gate: unknown column", undefined, "unsafe_query"], ["503: The warehouse is temporarily unavailable; retry.", undefined, "warehouse_failed"],
    ["503: query exceeded the 60 second time limit", undefined, "too_long"], ["503: upstream reset", undefined, "kge_unreachable"],
    ["503: The service has no warehouse settings loaded", undefined, "kge_unreachable"]]) {
    assert.equal(failureOf(text, status), kind, `${text} ${status}`);
  }
});

// #622: an error with no status (the MCP framework's own: an argument that fails validation, an unhandled error), a 4xx
// and a JSON-RPC error reply are refusals the model reads and answers: no failure, and the turn goes on.
const REFUSALS = [
  ["a validation error", () => ({ isError: "Error executing tool ask: 1 validation error for askArguments\nquestion\n  Field required" })],
  ["an unhandled tool error", () => ({ isError: "Error executing tool ask: KeyError 'x'" })],
  ["a refusal naming the warehouse", () => ({ isError: "Error executing tool ask: the warehouse argument is not allowed" })],
  ["a request too large", () => ({ isError: "400: The request is too large." })],
  ["a 4xx status", () => ({ status: 413 })],
  ["a JSON-RPC error reply", () => ({ body: JSON.stringify({ jsonrpc: "2.0", id: 1, error: { code: -32602, message: "Invalid params" } }) })],
];
test("(e) a refusal is no failure: the model reads it and answers in words (#622)", async () => {
  for (const [what, reply] of REFUSALS) {
    const model = asksOnce("NNA for June 2025");
    const { outcome, values } = await turn(model, kgeStub(reply));
    assert.equal(model.calls, 2, `${what}: the model answers it`);
    assert.equal(outcome.outcome, "answered", what);
    assert.equal(values.messages.at(-1).content, "I could not find that.", what);
    const tool = JSON.parse(values.messages.find((m) => m.type === "tool").content);
    assert.equal(tool.failure, undefined, what);
  }
});

test("(e) a third ask the agent blocked ends the turn as not planned, in the last words Ask gave (#622)", async () => {
  const reason = "No declared term matches \"book\". Use Search in the catalogue.";
  const model = standIn((messages) => (toolsThisTurn(messages).some((m) => JSON.parse(m.content).error) ? { say: "What do you mean by the book?" } : { call: ["ask", { question: "How did the book do?" }] }));
  const kge = kgeStub(() => ({ value: { behaviour: "abstain_with_reason", answer_mode: "not_sure", answer_text: `Not sure: ${reason}`, reason } }));
  const { outcome, values } = await turn(model, kge);
  assert.equal(kge.calls.length, 2, "the question and its one retry");
  assert.deepEqual(JSON.parse(values.messages.filter((m) => m.type === "tool").at(-1).content), { error: ASKED_AGAIN });
  assert.deepEqual(outcome, { kind: "outcome", outcome: "failed", steps: 6, error: { case: "not_planned", message: reason, action: "rephrase" } });
  // A turn whose only ask is the blocked one (a resumed turn) is not planned too, in the plain sentence.
  const blocked = outcomeOf({ turnStart: 0, messages: [new AIMessage({ content: "", tool_calls: [{ id: "c-3", name: "ask", args: { question: "q" } }] }),
    new ToolMessage({ content: JSON.stringify({ error: ASKED_AGAIN }), tool_call_id: "c-3", name: "ask" }), new AIMessage("Which book?")] });
  assert.deepEqual(blocked.error, { case: "not_planned", message: "Ask could not plan this question as asked.", action: "rephrase" });
});

test("(e) in a parallel step, a card wins over another call's failure: the turn is answered at the card (#622)", async () => {
  for (const order of [[["ask", { question: "NNA for June 2025" }], ["search", { query: "nna" }]], [["search", { query: "nna" }], ["ask", { question: "NNA for June 2025" }]]]) {
    const model = standIn(() => ({ calls: order }));
    const kge = kgeStub((name) => (name === "ask" ? { status: 502 } : { value: { hits: [{ kind: "metric", title: "Net New Assets" }] } }));
    const { outcome, values } = await turn(model, kge);
    assert.equal(model.calls, 1, "no second model call");
    assert.deepEqual(values.messages.filter((m) => m.type === "tool").map((m) => m.name).sort(), ["ask", "search"]);
    assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 3 });
  }
  // With no card in the step, the failure ends the turn.
  const model = standIn(() => ({ calls: [["ask", { question: "q" }], ["search", { query: "zz" }]] }));
  const { outcome } = await turn(model, kgeStub((name) => (name === "ask" ? { status: 502 } : { value: { hits: [] } })));
  assert.equal(outcome.error.case, "kge_unreachable");
});

test("(e) a turn resumed by a pick sends the picked ask's step, readout and warehouse state, and one outcome, last (#622, #624)", async () => {
  const question = RECORDED.flow.question;
  const clarify = { behaviour: "clarify", answer_mode: "not_sure", answer_text: "Not sure: which inflow?", clarification: { choices: [{ id: "net", label: "Net client inflow" }] } };
  const first = await turn(asksOnce(question), kgeStub(() => ({ value: clarify })));
  assert.equal(first.outcome.outcome, "paused");
  const kge = kgeStub(() => ({ events: RECORDED.flow.events, value: { ...ANSWER, answer_mode: "ai_generated", answer_text: "AI-generated: net client inflow was USD 12." } }));
  const { custom, status, outcome, values } = await turn(standIn(() => ({ say: "unused" })), kge, { thread: first.thread, resume: { pick: "net" } });
  const kinds = status.map((d) => (d.kind === "step" ? `step:${d.step}` : d.kind));
  assert.deepEqual(kinds.filter((k) => k.startsWith("step:")), ["step:understanding", "step:planning", "step:warehouse", "step:checking"]);
  assert.ok(kinds.includes("readout") && kinds.includes("warehouse"), kinds.join());
  const picked = values.messages.at(-2).tool_calls[0];
  assert.match(picked.id, /^pick-/);
  assert.ok(custom.some((d) => d.kind === "ask_progress" && d.tool_call_id === picked.id));
  assert.equal(status.filter((d) => d.kind === "outcome").length, 1, "exactly one outcome");
  assert.deepEqual(outcome, { kind: "outcome", outcome: "answered", steps: 4 });
  assert.equal(custom.at(-1).kind, "outcome", "the outcome is the last event");
  assert.equal(JSON.parse(values.messages.at(-1).content).answer_text, "AI-generated: net client inflow was USD 12.");
  assert.deepEqual(kge.calls.map((c) => c.args.question), ["Net client inflow"], "with no history to restate it, the label as picked");
});

test("(f) a busy model is retried once, then the turn fails as model_failed; any other model failure is not retried (#622)", async () => {
  const busyError = () => Object.assign(new Error("Google request failed with status code 429: RESOURCE_EXHAUSTED"), { response: { status: 429 } });
  const once = standIn((_messages, n) => { if (n === 1) throw busyError(); return { say: "Hello." }; });
  assert.equal((await turn(once, undefined)).outcome.outcome, "answered");
  assert.equal(once.calls, 2, "one retry");
  const twice = standIn(() => { throw busyError(); });
  const failed = await turn(twice, undefined, { roles: ["engineer"] });
  assert.equal(twice.calls, 2, "one retry and no more");
  assert.deepEqual(failed.outcome, { kind: "outcome", outcome: "failed", steps: 1, error: { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again", detail: busyError().message } });
  const broken = standIn(() => { throw Object.assign(new Error("Google request failed with status code 400: bad"), { response: { status: 400 } }); });
  const refused = await turn(broken, undefined);
  assert.equal(broken.calls, 1, "a refusal is not retried");
  assert.deepEqual(refused.outcome.error, { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" });
  for (const error of [{ status: 503 }, { message: "UNAVAILABLE: try later" }, { code: 429 }]) assert.equal(busy(error), true, JSON.stringify(error));
  for (const error of [{ status: 500 }, { message: "deadline" }, null]) assert.equal(busy(error), false, JSON.stringify(error));
  // KGE's failure thrown from a tool call is kge_unreachable, not the model's.
  const thrown = await turn(asksOnce("q"), { sessionBearer: async () => "b", request: async () => { throw new Error("x"); } });
  assert.equal(thrown.outcome.error.case, "kge_unreachable");
});

test("(g) search and map_term are offered to every Cerebro role; retrieve and assist to engineers alone (#622)", () => {
  for (const role of ["business", "steward"]) {
    const allowed = allowedTools([role]);
    for (const tool of ["search", "map_term", "ask", "get_context", "explore_question"]) assert.ok(allowed.has(tool), `${role}: ${tool}`);
    for (const tool of ["retrieve", "assist"]) assert.equal(allowed.has(tool), false, `${role}: ${tool}`);
  }
  for (const tool of ["search", "map_term", "retrieve", "assist"]) assert.ok(allowedTools(["engineer"]).has(tool));
});

test("(h) Cerebro delegates thinking and output budgets to KGE, without choosing the model", async () => {
  const bodies = [];
  const kge = { modelChat: async (_req, body) => { bodies.push(body); return new Response('', { status: 503 }); } };
  for (const id of ["gemini-3.1-pro-preview", "gemini-2.5-flash"]) {
    const chat = openCerebroModel({ model: id, modelProvider: "vertex" }, {}, { kge, req: { modelQuestionId: "123e4567-e89b-42d3-a456-abcdefabcdef", modelConversationId: "123e4567-e89b-42d3-a456-abcdefabcdef" } });
    await assert.rejects(chat.invoke("q"));
  }
  assert.equal(bodies.length, 2, "the gateway model does not retry on its own");
  for (const [index, body] of bodies.entries()) {
    assert.equal(body.purpose, "agent");
    assert.equal(body.expected_model, ["gemini-3.1-pro-preview", "gemini-2.5-flash"][index]);
    for (const field of ["model", "thinking", "input_tokens", "output_tokens"]) assert.equal(body[field], undefined);
  }
  assert.equal(thinkingLevelFor("gemini-10-pro"), "low");
  assert.equal(thinkingLevelFor("claude-x"), undefined);
});
