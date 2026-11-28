// The useStream check (cerebro.md §4.3, D2), option B: the pinned hook on its custom-backend adapter, against the
// portal server's own stream handler, with no LangGraph server. See README-check.md.
import { expect, test } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FetchStreamTransport, useStream } from "@langchain/langgraph-sdk/react";
import { cerebroStream } from "../../../../server/src/cerebro/route.js";

// #625: the turn keeps the conversation by its id, as the server's checkpointer does; a request carries the new
// message alone, or the resume alone.
const requests = [];
const kept = new Map();
async function* fakeTurn({ thread, messages, resume }) {
  requests.push({ thread, messages, resume });
  const held = kept.get(thread) ?? [];
  if (resume === undefined) {
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    yield ["values", { messages: [...held, ...messages, call] }];
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: "{\"behaviour\":\"clarify\"}" };
    // #624: the pause is a question back to the person, the HITL card's interrupt.
    const value = { kind: "hitl", tool_call_id: "hitl-1", source: "clarify", question: "Which revenue?", options: [{ id: "net", label: "Net revenue" }], other: true };
    const state = [...held, ...messages, call, result];
    kept.set(thread, state);
    yield ["values", { messages: state, __interrupt__: [{ id: "int-1", value }] }];
    return;
  }
  yield ["values", { messages: [...held, { type: "ai", id: "ai-2", content: "Revenue was 42." }] }];
}

// The portal server's handler, called in process: the fetch the adapter makes becomes a request to it.
const handler = cerebroStream({ runTurn: fakeTurn });
async function portalFetch(_url, init) {
  let controller;
  const body = new ReadableStream({ start: (c) => (controller = c) });
  const encoder = new TextEncoder();
  let status = 200;
  const res = {
    on() {},
    setHeader() {},
    writeHead: (s) => (status = s),
    write: (chunk) => controller.enqueue(encoder.encode(chunk)),
    end: () => controller.close(),
  };
  void handler({ body: JSON.parse(init.body) }, res);
  return new Response(body, { status, headers: { "content-type": "text/event-stream" } });
}

const THREAD = "00000000-0000-4000-8000-feed00000625";
function Chat() {
  const stream = useStream({ transport: new FetchStreamTransport({ apiUrl: "/api/cerebro/stream", fetch: portalFetch }), threadId: THREAD, onThreadId: () => {} });
  const ask = () => stream.submit({ messages: [{ type: "human", id: "h-1", content: "Revenue?" }] });
  // Resume: the person's pick alone, on the same conversation id; the server holds the conversation (#625).
  const pick = () => stream.submit(null, { command: { resume: { pick: "net" } } });
  return (
    <div>
      <button onClick={ask}>Ask</button>
      {stream.messages.map((m) => (
        <p key={m.id}>
          {m.type}: {m.content || (m.tool_calls || []).map((c) => `tool ${c.name}`).join(",")}
        </p>
      ))}
      {stream.interrupt ? <button onClick={pick}>Pick {stream.interrupt.value.kind}</button> : null}
    </div>
  );
}

test("useStream streams, shows a tool call, pauses and resumes on the portal server's handler", async () => {
  render(<Chat />);
  await act(async () => fireEvent.click(screen.getByText("Ask")));
  await waitFor(() => expect(screen.getByText("Pick hitl")).toBeInTheDocument());
  expect(screen.getByText("ai: tool ask")).toBeInTheDocument();
  expect(requests[0]).toEqual({ thread: THREAD, messages: [{ type: "human", id: "h-1", content: "Revenue?" }], resume: undefined });

  await act(async () => fireEvent.click(screen.getByText("Pick hitl")));
  await waitFor(() => expect(screen.getByText("ai: Revenue was 42.")).toBeInTheDocument());
  expect(screen.queryByText(/^Pick/)).not.toBeInTheDocument();
  expect(requests[1]).toEqual({ thread: THREAD, messages: undefined, resume: { pick: "net" } });
});
