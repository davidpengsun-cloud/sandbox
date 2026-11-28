/* Tests only (#625): a stand-in for the portal server's Cerebro routes, as the page reads them, with no server code: the
   stream route (POST /api/cerebro/stream, LangGraph's server-sent events) and the conversation routes (list, open,
   delete). It keeps each conversation by its id, as the server's checkpointer does, so a turn runs on the saved
   conversation and the one new message the page sends; a resume runs on the saved conversation alone; `context.rewind`
   drops the person's message it names and everything after it first. It refuses what the server refuses: a thread id
   that is not a UUID, anything but exactly one new human message, a message with a resume, a rewind to no saved
   message (400), and another person's conversation (404). The turn's `values` carry `trimmed` and `title`; its question is saved as it starts, and
   its last `values` (with any `__interrupt__`) as it ends, unless it was stopped or its outcome says the conversation
   changed in another tab.
   #651: as the server does, it keeps one record per reply, by the id of the person's question that opened the turn
   ({ sent, duration_ms, outcome, failure?, steps, readouts, verdict? }): sent as a `{ kind: "reply" }` custom event right
   after the outcome, and as `replies` in every `values` event and in the open route; a paused turn writes none. The
   verdict route (PUT .../replies/:message/verdict { verdict }) saves Like or Dislike, making the record of a question
   that has none yet (an older conversation's), or 404 for a question it does not hold. */

const STREAM = "/api/cerebro/stream";
// The server takes a conversation id that is a UUID, the page makes one for a new chat (as route.js reads it).
const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LIST = "/api/cerebro/conversations";
const VERDICT = /^\/api\/cerebro\/conversations\/([^/]+)\/replies\/([^/]+)\/verdict$/;
const TITLE_MAX = 80;

/** One server-sent event in the framing the hook's SSE decoder reads. */
const encodeEvent = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const json = (status, body) => new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

/** A conversation's title as the server makes it: its first question, at most TITLE_MAX characters, cut at a word. */
export function titleOf(text) {
  const words = String(text ?? "").trim().replace(/\s+/g, " ");
  if (words.length <= TITLE_MAX) return words;
  const cut = words.slice(0, TITLE_MAX + 1);
  const at = cut.lastIndexOf(" ");
  return (at > 0 ? cut.slice(0, at) : words.slice(0, TITLE_MAX)).trim();
}

const contentText = (content) => (typeof content === "string" ? content
  : Array.isArray(content) ? content.filter((p) => p?.type === "text").map((p) => p.text).join(" ") : "");

// Ids here are UUIDs or the page's test ids; both are path-safe.
const idFrom = (path) => decodeURIComponent(path.slice(LIST.length + 1));

/**
 * The server. `runTurn({ messages, resume, node, rewind, signal })` is an async iterable of [event, data] pairs, as the
 * server's agent gives them. `principal` is the person signed in (a test may change it); `retentionDays` is what the
 * list says; `pageSize` the list's default page. Returns `fetch` (the page's), `requests` (each stream request's body),
 * `ran` (the messages each turn ran on), `calls` (every route call, "<METHOD> <url>"), `conversations` (the saved ones by
 * id) and `seed(conversation)`, which saves one as another tab or an earlier visit would have.
 */
export function conversationServer({ runTurn, principal = "me", retentionDays = 30, pageSize = 20 } = {}) {
  const conversations = new Map();
  const requests = [];
  const ran = [];
  const calls = [];
  let clock = Date.parse("2026-11-01T09:00:00Z");
  const tick = () => new Date((clock += 60000)).toISOString();
  const server = { principal, retentionDays, conversations, requests, ran, calls, verdicts: [] };

  server.seed = (c) => {
    const at = c.updated ?? tick();
    const saved = { owner: server.principal, title: "", created: at, updated: at, trimmed: 0, messages: [], interrupt: null, replies: {}, ...c };
    conversations.set(saved.id, saved);
    return saved;
  };

  const own = (id) => {
    const c = conversations.get(id);
    return c && c.owner === server.principal ? c : null;
  };

  function list(url) {
    const params = new URL(url, "http://portal.invalid").searchParams;
    const from = Number(params.get("page_token") || 0);
    const size = Number(params.get("page_size") || pageSize);
    const mine = [...conversations.values()].filter((c) => c.owner === server.principal).sort((a, b) => b.updated.localeCompare(a.updated));
    const page = mine.slice(from, from + size).map(({ id, title, created, updated }) => ({ id, title, created, updated }));
    const next = from + size < mine.length ? String(from + size) : null;
    return json(200, { conversations: page, next, retention_days: server.retentionDays });
  }

  /** The stream request, read as the server reads it: the turn's messages, or a reason it is refused. */
  function readTurn(body) {
    const id = body?.config?.configurable?.thread_id;
    if (typeof id !== "string" || !CONVERSATION_ID.test(id)) return { status: 400, detail: "Send the conversation's id, a UUID, as config.configurable.thread_id." };
    const saved = conversations.get(id);
    if (saved && saved.owner !== server.principal) return { status: 404, detail: "No such conversation." };
    const resume = body.command?.resume;
    const sent = body.input?.messages;
    if (resume !== undefined && sent !== undefined && sent !== null) return { status: 400, detail: "A resume carries no message." };
    if (resume === undefined) {
      if (!Array.isArray(sent) || sent.length !== 1) return { status: 400, detail: "Send exactly one new message." };
      const [message] = sent;
      if (!message || message.type !== "human" || typeof message.content !== "string") return { status: 400, detail: "The new message is the person's question." };
    }
    let held = saved?.messages ?? [];
    const rewind = body.context?.rewind;
    if (rewind !== undefined) {
      const at = held.findIndex((m) => m.type === "human" && m.id === rewind);
      if (at < 0) return { status: 400, detail: "Rewind to a question in this conversation." };
      held = held.slice(0, at);
    }
    // #651: as the server does (agent.js turnInput), a new message whose id the kept conversation already holds.
    if (resume === undefined && sent[0].id && held.some((m) => m.id === sent[0].id)) return { status: 400, detail: "A new message needs an id of its own." };
    // A resume answers the saved conversation's question back to the person: a pause's, or the offer to compare the
    // figures, which ends its turn without one (#624); the agent reads which from the saved state.
    if (resume !== undefined && !saved) return { status: 404, detail: "No such conversation." };
    return { id, saved, resume, node: body.context?.node, rewind, messages: resume === undefined ? [...held, sent[0]] : held };
  }

  function stream(init) {
    const body = JSON.parse(init.body);
    requests.push(body);
    const turn = readTurn(body);
    if (turn.status) return json(turn.status, { detail: turn.detail });
    ran.push(turn.messages);
    const title = turn.saved?.title || titleOf(contentText(turn.messages.find((m) => m.type === "human")?.content));
    const trimmed = turn.saved?.trimmed ?? 0;
    let controller;
    const response = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    // A stopped request ends its stream as a browser's fetch does; what the agent writes after is dropped.
    let open = true;
    const write = (event, data) => { if (open) controller.enqueue(encoder.encode(encodeEvent(event, data))); };
    const end = () => { if (open) { open = false; controller.close(); } };
    init.signal?.addEventListener("abort", () => { if (open) { open = false; controller.error(new DOMException("Aborted", "AbortError")); } });
    const save = (messages, interrupt) => {
      const at = tick();
      const was = conversations.get(turn.id);
      conversations.set(turn.id, {
        id: turn.id, owner: server.principal, title, created: was?.created ?? turn.saved?.created ?? at, updated: at, trimmed,
        messages, interrupt, replies: was?.replies ?? turn.saved?.replies ?? {},
      });
    };
    // #651: the reply's record, by the question that opened the turn (a resume's is the saved conversation's last).
    const human = [...turn.messages].reverse().find((m) => m?.type === "human");
    const started = Date.now();
    const steps = [];
    const readouts = [];
    const repliesNow = () => conversations.get(turn.id)?.replies ?? {};
    const record = (data) => {
      if (!human?.id || data.outcome === "paused") return null;
      const reply = {
        sent: new Date(started).toISOString(), duration_ms: Date.now() - started,
        outcome: data.outcome === "background" ? "background" : data.outcome === "failed" ? "failed" : "answered",
        ...(data.outcome === "failed" && data.error ? { failure: data.error } : {}), steps: [...steps], readouts: [...readouts],
      };
      const c = conversations.get(turn.id);
      if (c) c.replies = { ...c.replies, [human.id]: reply };
      return reply;
    };
    // As LangGraph's checkpointer does, the question is kept as the turn starts, so a turn stopped or failed on its way
    // still holds it (and Try again can rewind to it); the turn's last state is kept as it ends. A stopped turn ends
    // there.
    if (turn.resume === undefined) save(turn.messages, null);
    (async () => {
      let last = null;
      let changed = false;
      write("metadata", { run_id: `run-${requests.length}` });
      try {
        for await (const [event, data] of runTurn({ messages: turn.messages, resume: turn.resume, node: turn.node, rewind: turn.rewind, signal: init.signal })) {
          if (init.signal?.aborted) break;
          if (event === "values") {
            last = data;
            write(event, { ...data, trimmed, title, replies: repliesNow() });
          } else {
            if (event === "custom" && data?.kind === "outcome" && data.error?.case === "conversation_changed") changed = true;
            if (event === "custom" && data?.kind === "step" && typeof data.text === "string") steps.push({ kind: "step", step: data.step, text: data.text });
            if (event === "custom" && data?.kind === "readout" && typeof data.message === "string") readouts.push({ tool_call_id: data.tool_call_id, message: data.message });
            write(event, data);
            const reply = event === "custom" && data?.kind === "outcome" && !changed ? record(data) : null;
            if (reply) write("custom", { kind: "reply", id: human.id, reply });
          }
        }
      } catch {
        const error = { case: "model_failed", message: "The assistant couldn't respond.", action: "try_again" };
        write("custom", { kind: "outcome", outcome: "failed", steps: 0, error });
        const reply = record({ outcome: "failed", error });
        if (reply) write("custom", { kind: "reply", id: human.id, reply });
      }
      if (last && !changed && !init.signal?.aborted) save(last.messages ?? [], last.__interrupt__ ?? null);
      end();
    })();
    return new Response(response, { status: 200, headers: { "content-type": "text/event-stream" } });
  }

  async function fetch(url, init = {}) {
    const method = (init.method || "GET").toUpperCase();
    calls.push(`${method} ${url}`);
    const path = String(url).split("?")[0];
    if (path === STREAM && method === "POST") return stream(init);
    if (path === LIST && method === "GET") return list(url);
    const verdict = path.match(VERDICT);
    if (verdict && method === "PUT") {
      const c = own(decodeURIComponent(verdict[1]));
      const message = decodeURIComponent(verdict[2]);
      const value = JSON.parse(init.body || "{}")?.verdict;
      // #651: as the server does, a question with no record yet (an older conversation's) gets one.
      const asked = c && (c.replies?.[message] || c.messages.some((m) => m?.type === "human" && m.id === message));
      if (!asked) return json(404, { detail: "No such reply." });
      if (![null, "like", "dislike"].includes(value)) return json(400, { detail: "No such verdict." });
      server.verdicts.push({ conversation: c.id, message, verdict: value });
      c.replies = { ...c.replies, [message]: { ...c.replies[message], ...(value ? { verdict: value } : {}) } };
      if (!value) delete c.replies[message].verdict;
      return json(200, { verdict: value });
    }
    if (path.startsWith(`${LIST}/`)) {
      const c = own(idFrom(path));
      if (!c) return json(404, { detail: "No such conversation." });
      if (method === "DELETE") {
        conversations.delete(c.id);
        return new Response(null, { status: 204 });
      }
      const { id, title, created, updated, trimmed, messages, interrupt, replies } = c;
      return json(200, { id, title, created, updated, trimmed, messages, interrupt, replies: replies ?? {} });
    }
    return json(404, { detail: "Not found." });
  }
  server.fetch = fetch;
  return server;
}
