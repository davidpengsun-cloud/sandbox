// Tests only: a stand-in chat model and a stubbed KGE client for Cerebro's agent, shared by the page's tests that drive
// the real agent (portal/ui ChatServer.test.js). Synthetic data only.
import { AIMessage } from "@langchain/core/messages";

/** A stand-in model: decide(messages, n) gives each call's step ({ call: [name, args] }, { calls: [...] } or { say }). */
export function standIn(decide) {
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

/**
 * #707 requirement 17 (#714): the stand-in KGE reports bindings and open items. The step before ask's reads are told
 * from the model's and the portal's other calls by their arguments: get_context with open_items, and search with the
 * filter kind (tools.js call.reading and call.filters). isStepRead says so; stepReads(reading, filters) answers them:
 * reading(question, args) gives the question's { bindings, open_items } (kgeReading below builds one), or nothing,
 * which reads as a question with nothing bound and nothing open; filters(query) gives the filter phrases' titles.
 */
export const isStepRead = (name, args) => (name === "get_context" && args?.open_items === true) || (name === "search" && Array.isArray(args?.kinds) && args.kinds.includes("filter"));
export const NOTHING_OPEN = { bindings: [], open_items: [] };
export function stepReads(reading = () => undefined, filters = () => []) {
  return (name, args) => (name === "get_context"
    ? { value: reading(args.question, args) ?? NOTHING_OPEN }
    : { value: { hits: (filters(args.query) ?? []).map((title, at) => ({ kind: "filter", id: `filter:${at}`, title, restricted: false })) } });
}

/**
 * KGE's reading of a question, as get_context gives it with open_items: each binding and item placed where its text
 * stands in the question, bindings in the order given (each found after the one before it), an item at its text's
 * first place, or its at-th. A binding: [text, kind, fields]; an item: [item, resolution, fields], fields.text its
 * place.
 */
export function kgeReading(question, bindings = [], items = []) {
  let from = 0;
  const placed = bindings.map(([text, kind, fields = {}]) => {
    const start = question.indexOf(text, from);
    if (start < 0) throw new Error(`"${text}" is not in "${question}" after ${from}`);
    from = start + text.length;
    return { kind, text, start, end: from, ...fields };
  });
  const open = items.map(([item, resolution, { text, at = 0, ...fields } = {}]) => {
    if (text === undefined) return { item, resolution, ...fields };
    let start = -1;
    for (let n = 0; n <= at; n += 1) start = question.indexOf(text, start + 1);
    if (start < 0) throw new Error(`"${text}" is not in "${question}"`);
    return { item, resolution, text, start, end: start + text.length, ...fields };
  });
  return { bindings: placed, open_items: open };
}
/** A period item's options: the covered months, newest first, as KGE lists them. */
export const MONTHS = [["2025-06", "June 2025"], ["2025-05", "May 2025"], ["2025-04", "April 2025"], ["2025-03", "March 2025"], ["2025-02", "February 2025"]]
  .map(([value, label]) => ({ value, label }));
export const periodDefault = () => ["period", "default", { grain: "month", options: MONTHS }];
export const periodUncovered = (fields = {}) => ["period", "unanswerable", { grain: "month", latest: MONTHS[0], options: MONTHS, ...fields }];

/** The portal's KGE client, stubbed: reply(name, args) gives { value }, { status } or a tool's { error }; every call is recorded. A request
 *  with no body is a GET: golden is GET /golden's body (#621: the showcase questions), a 404 where none is given; each
 *  is recorded in gets, with the bearer it carried. #714: the step before ask's reads (isStepRead) are answered by
 *  step (stepReads; by default nothing is bound and nothing is open) and recorded in reads, apart from calls. */
export function kgeStub(reply, { golden, step = stepReads() } = {}) {
  const calls = [];
  const gets = [];
  const reads = [];
  return {
    calls,
    gets,
    reads,
    sessionBearer: async () => "bearer-1",
    modelReserve: async () => crypto.randomUUID(),
    request: async (_ctx, mount, init = {}) => {
      if (!init.body) {
        gets.push({ path: mount, authorization: init.headers?.["x-kge-authorization"] });
        return golden && mount === "/golden"
          ? new Response(JSON.stringify(golden), { status: 200, headers: { "content-type": "application/json" } })
          : new Response("{}", { status: 404, headers: { "content-type": "application/json" } });
      }
      const message = JSON.parse(init.body);
      const { name, arguments: args } = message.params;
      const read = isStepRead(name, args);
      (read ? reads : calls).push({ mount, name, args });
      const out = (read ? step(name, args) : reply(name, args)) ?? { value: {} };
      if (out.status) return new Response("{}", { status: out.status, headers: { "content-type": "application/json" } });
      if (out.error) return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { isError: true, content: [{ type: "text", text: out.error }] } }), { status: 200, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: message.id, result: { structuredContent: out.value, isError: false } }), { status: 200, headers: { "content-type": "application/json" } });
    },
  };
}

/** #713: hand-made multiple-question cards, sample data only (which groups open is 707.3's, from KGE's open items; no
 *  KGE fact states an option dependency yet, #709). sampleQuestions(question) is an openQuestions for Cerebro's agent:
 *  the open groups of the sample questions below, else none. The Segment group's "Branch region" is valid for
 *  branch-sales NNA only. */
export const RETAIL_QUESTION = "What was the retail NNA for Q1 2025 by segment?";
export const SHOW_QUESTION = "Show me NNA";
export const ONE_GROUP_QUESTION = "What was NNA for June 2025?";
const placeOf = (question, words) => {
  const start = question.indexOf(words);
  return { start, end: start + words.length };
};
export const SAMPLE_MEANINGS = [
  { id: "net-new-assets", label: "NNA (current)", words: "current NNA" },
  { id: "certified-nna", label: "Certified NNA", words: "certified NNA", suggested: true },
  { id: "branch-sales-nna", label: "Branch-sales NNA", words: "branch-sales NNA" },
];
export const SAMPLE_SEGMENTS = [
  { id: "client-segment", label: "Client segment", words: "client segment" },
  { id: "advisor-team", label: "Advisor team", words: "advisor team" },
  { id: "branch-region", label: "Branch region", words: "branch region", when: { group: "meaning", options: ["branch-sales-nna"] } },
];
export const SAMPLE_SHOW = [{ id: "definitions", label: "Definitions" }, { id: "figures", label: "Figures" }, { id: "both", label: "Both" }];
export const SAMPLE_PERIODS = [{ id: "2025-06", label: "June 2025" }, { id: "2025-05", label: "May 2025" }, { id: "2025-04", label: "April 2025" }];
export function sampleQuestions(question) {
  if (question === RETAIL_QUESTION) {
    return [
      { id: "meaning", label: "Meaning", kind: "meaning", spans: [placeOf(question, "retail NNA")], options: SAMPLE_MEANINGS },
      { id: "segment", label: "Segment", kind: "grouping", spans: [placeOf(question, "segment")], options: SAMPLE_SEGMENTS },
    ];
  }
  if (question === SHOW_QUESTION) {
    return [
      { id: "meaning", label: "Meaning", kind: "meaning", spans: [placeOf(question, "NNA")], options: SAMPLE_MEANINGS },
      { id: "show", label: "Show", kind: "show", options: SAMPLE_SHOW },
      { id: "period", label: "Period", kind: "period", options: SAMPLE_PERIODS },
    ];
  }
  if (question === ONE_GROUP_QUESTION) return [{ id: "meaning", label: "Meaning", kind: "meaning", spans: [placeOf(question, "NNA")], options: SAMPLE_MEANINGS }];
  return undefined;
}
