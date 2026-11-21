// The useStream check (cerebro.md §4.3, D2), option B: the pinned hook on its custom-backend adapter, against the
// portal server's own stream handler, with no LangGraph server. See README-check.md.
import { expect, test } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FetchStreamTransport, useStream } from "@langchain/langgraph-sdk/react";
import { cerebroStream } from "../../../../server/src/cerebro/route.js";

const requests = [];
async function* fakeTurn({ messages, resume }) {
  requests.push({ messages, resume });
  if (resume === undefined) {
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    yield ["values", { messages: [...messages, call] }];
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: "confirm_first" };
    yield ["values", { messages: [...messages, call, result], __interrupt__: [{ id: "int-1", value: { kind: "confirm_first" } }] }];
    return;
  }
  yield ["values", { messages: [...messages, { type: "ai", id: "ai-2", content: "Revenue was 42." }] }];
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
    writeHead: (s) => (status = s),
    write: (chunk) => controller.enqueue(encoder.encode(chunk)),
    end: () => controller.close(),
  };
  void handler({ body: JSON.parse(init.body) }, res);
  return new Response(body, { status, headers: { "content-type": "text/event-stream" } });
}

function Chat() {
  const stream = useStream({ transport: new FetchStreamTransport({ apiUrl: "/api/cerebro/stream", fetch: portalFetch }) });
  const ask = () => stream.submit({ messages: [{ type: "human", id: "h-1", content: "Revenue?" }] });
  // Resume: the page sends the conversation it holds with the confirmation; the server holds nothing.
  const confirm = () =>
    stream.submit({ messages: stream.messages.filter((m) => m.type === "human") }, { command: { resume: "confirmed" } });
  return (
    <div>
      <button onClick={ask}>Ask</button>
      {stream.messages.map((m) => (
        <p key={m.id}>
          {m.type}: {m.content || (m.tool_calls || []).map((c) => `tool ${c.name}`).join(",")}
        </p>
      ))}
      {stream.interrupt ? <button onClick={confirm}>Confirm {stream.interrupt.value.kind}</button> : null}
    </div>
  );
}

test("useStream streams, shows a tool call, pauses and resumes on the portal server's handler", async () => {
  render(<Chat />);
  await act(async () => fireEvent.click(screen.getByText("Ask")));
  await waitFor(() => expect(screen.getByText("Confirm confirm_first")).toBeInTheDocument());
  expect(screen.getByText("ai: tool ask")).toBeInTheDocument();
  expect(requests[0]).toEqual({ messages: [{ type: "human", id: "h-1", content: "Revenue?" }], resume: undefined });

  await act(async () => fireEvent.click(screen.getByText("Confirm confirm_first")));
  await waitFor(() => expect(screen.getByText("ai: Revenue was 42.")).toBeInTheDocument());
  expect(screen.queryByText(/^Confirm/)).not.toBeInTheDocument();
  expect(requests[1].resume).toBe("confirmed");
  expect(requests[1].messages).toEqual([{ type: "human", id: "h-1", content: "Revenue?" }]);
});
