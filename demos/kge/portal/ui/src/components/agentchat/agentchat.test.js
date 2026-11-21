// The copied Agent Chat UI components on B1a's stream: the pinned hook, its custom-backend adapter, and the portal
// server's stateless stream handler called in process, with a mock agent (no model, no LangGraph server).
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import React from "react";
import { expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { cerebroStream } from "../../../../server/src/cerebro/route.js";
import { ChatThread, ConversationList, ThreadView, ToolResult, conversationTitle, isAgentInboxInterruptSchema, useAgentChatStream } from "./index";

const here = import.meta.url; // a variable, so Vite does not rewrite the URL as an asset
const dir = dirname(fileURLToPath(here));

test("the copied components and their styles use the portal tokens only: no hex colour, no new token", () => {
  const own = readdirSync(dir)
    .filter((f) => /\.(js|css)$/.test(f) && !f.endsWith(".test.js"))
    .map((f) => readFileSync(`${dir}/${f}`, "utf8"))
    .join("\n");
  expect(own).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  expect(own).not.toMatch(/--v3-[\w-]+\s*:/);
  expect(own).toMatch(/var\(--v3-surface\)/);
});

test("the MIT notice of Agent Chat UI stays with the copy", () => {
  const licence = readFileSync(`${dir}/LICENSE.md`, "utf8");
  expect(licence).toMatch(/MIT License/);
  expect(licence).toMatch(/Copyright \(c\) 2025 Brace Sproul/);
  expect(licence).toMatch(/agent-chat-ui/);
});

// A mock agent: a turn yields [event, data] pairs as the real one will. `gate` holds the answer half-streamed.
function mockAgent({ hitl = false } = {}) {
  const requests = [];
  let release;
  const gate = new Promise((r) => (release = r));
  async function* runTurn({ messages, resume }) {
    if (resume !== undefined) {
      yield ["values", { messages: [...messages, { type: "ai", id: "ai-9", content: "Done as you decided." }] }];
      return;
    }
    const call = { type: "ai", id: "ai-1", content: "", tool_calls: [{ id: "tc-1", name: "ask", args: { question: "Revenue?" } }] };
    if (hitl) {
      const value = {
        action_requests: [{ name: "ask", args: { question: "Revenue?" }, description: "Run this query?" }],
        review_configs: [{ action_name: "ask", allowed_decisions: ["approve", "reject"] }],
      };
      // As LangGraph sends it: the state, then the interrupt (the hook takes only the interrupt from that event).
      yield ["values", { messages: [...messages, call] }];
      yield ["values", { messages: [...messages, call], __interrupt__: [{ id: "int-1", value }] }];
      return;
    }
    const result = { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", content: '{"answer":42}' };
    yield ["values", { messages: [...messages, call, result] }];
    yield ["messages", [{ type: "AIMessageChunk", id: "ai-2", content: "Revenue " }, { langgraph_node: "agent" }]];
    await gate;
    yield ["messages", [{ type: "AIMessageChunk", id: "ai-2", content: "was 42." }, { langgraph_node: "agent" }]];
    yield ["values", { messages: [...messages, call, result, { type: "ai", id: "ai-2", content: "Revenue was 42." }] }];
  }
  const handler = cerebroStream({ runTurn });
  async function portalFetch(_url, init) {
    const body = JSON.parse(init.body);
    requests.push(body);
    let controller;
    const stream = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    let status = 200;
    const res = {
      on() {},
      writeHead: (s) => (status = s),
      write: (chunk) => controller.enqueue(encoder.encode(chunk)),
      end: () => controller.close(),
    };
    void handler({ body }, res);
    return new Response(stream, { status, headers: { "content-type": "text/event-stream" } });
  }
  return { requests, release, portalFetch };
}

function Harness({ fetch }) {
  const stream = useAgentChatStream({ fetch });
  return <ChatThread stream={stream} health={{ ai_fallback: "on", model: "mock" }} />;
}

async function ask(question) {
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}

test("one answer streams in parts on the portal's stream route, and the tool call it made is shown", async () => {
  const agent = mockAgent();
  render(<Harness fetch={agent.portalFetch} />);
  expect(screen.getByRole("button", { name: /^Answer settings:/ })).toBeInTheDocument();
  await ask("Revenue?");

  // The question as a right-aligned bubble, the tool call with its arguments, its result, and half the answer.
  await waitFor(() => expect(screen.getByText("Revenue", { selector: ".agentchat-answer" })).toBeInTheDocument());
  expect(screen.getByText("Revenue?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  const call = screen.getByRole("region", { name: "Tool call ask" });
  expect(within(call).getByText("question")).toBeInTheDocument();
  expect(within(call).getByText("tc-1")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Tool result ask" })).toHaveTextContent('"answer": 42');
  expect(screen.queryByText("Revenue was 42.")).not.toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Working out the answer");

  await act(async () => agent.release());
  await waitFor(() => expect(screen.getByText("Revenue was 42.")).toBeInTheDocument());
  await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());

  expect(agent.requests).toHaveLength(1);
  expect(agent.requests[0].input.messages).toEqual([expect.objectContaining({ type: "human", content: "Revenue?" })]);
  expect(agent.requests[0].command).toBeUndefined();
});

test("the inbox resumes with the conversation the page holds and the decision, never input: null", async () => {
  const agent = mockAgent({ hitl: true });
  render(<Harness fetch={agent.portalFetch} />);
  await ask("Revenue?");
  const item = await screen.findByRole("region", { name: "Waiting for you: ask" });
  expect(within(item).getByText("Run this query?")).toBeInTheDocument();
  expect(within(item).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

  await act(async () => fireEvent.click(within(item).getByRole("button", { name: "Accept" })));
  await waitFor(() => expect(screen.getByText("Done as you decided.")).toBeInTheDocument());
  expect(screen.queryByRole("region", { name: "Waiting for you: ask" })).not.toBeInTheDocument();

  const resume = agent.requests[1];
  expect(resume.input).not.toBeNull();
  expect(resume.input.messages.length).toBeGreaterThan(0);
  expect(resume.input.messages[0]).toEqual(expect.objectContaining({ type: "human", content: "Revenue?" }));
  expect(resume.command).toEqual({ resume: { decisions: [{ type: "approve" }] } });
});

test("a generic interrupt shows its value and Confirm resumes with the conversation the page holds", () => {
  const stream = { messages: [{ type: "human", id: "h", content: "Q" }], interrupt: { value: { kind: "confirm_first" } }, submit: vi.fn(), isLoading: false };
  render(<ChatThread stream={stream} />);
  expect(screen.getByText("confirm_first")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(stream.submit).toHaveBeenCalledWith({ messages: stream.messages }, { command: { resume: { decision: "confirmed" } }, optimisticValues: { messages: stream.messages } });
});

test("the confirm resumes with the decision and the question the person saw, never a bare string", () => {
  const messages = [{ type: "human", id: "h", content: "Revenue last quarter?" }];
  const value = { question: "Revenue for Q3 2026, all regions?", label: "The model's reading" };
  for (const [button, decision] of [["Confirm", "confirmed"], ["Decline", "declined"]]) {
    const stream = { messages, interrupt: [{ value, id: "i1" }], submit: vi.fn(), isLoading: false };
    const { unmount } = render(<ChatThread stream={stream} />);
    expect(screen.getByText(value.question)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: button }));
    expect(stream.submit).toHaveBeenCalledTimes(1);
    const [input, options] = stream.submit.mock.calls[0];
    expect(input).toEqual({ messages });
    expect(options.command).toEqual({ resume: { decision, question: value.question } });
    unmount();
  }
});

test("the HITL schema check accepts an inbox request and refuses anything else", () => {
  const ok = { value: { action_requests: [{ name: "a", args: {} }], review_configs: [{ action_name: "a", allowed_decisions: ["approve"] }] } };
  expect(isAgentInboxInterruptSchema(ok)).toBe(true);
  expect(isAgentInboxInterruptSchema([ok])).toBe(true);
  expect(isAgentInboxInterruptSchema({ value: { kind: "confirm_first" } })).toBe(false);
  expect(isAgentInboxInterruptSchema(null)).toBe(false);
});

test("the conversation list shows the page's conversations, names them by their first message, and starts a new one", () => {
  const onSelect = vi.fn();
  const onNew = vi.fn();
  const conversations = [
    { id: "a", messages: [{ type: "human", content: "Revenue last quarter?" }] },
    { id: "b", title: "Churn", messages: [] },
  ];
  render(<ConversationList conversations={conversations} activeId="a" onSelect={onSelect} onNew={onNew} />);
  const nav = screen.getByRole("navigation", { name: "Conversations" });
  expect(within(nav).getByRole("button", { name: "Revenue last quarter?" })).toHaveAttribute("aria-current", "true");
  fireEvent.click(within(nav).getByRole("button", { name: "Revenue last quarter?" }));
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.click(within(nav).getByRole("button", { name: "Churn" }));
  expect(onSelect).toHaveBeenCalledWith("b");
  fireEvent.click(within(nav).getByRole("button", { name: "New chat" }));
  expect(onNew).toHaveBeenCalled();
  expect(conversationTitle({ id: "c", messages: [] })).toBe("New chat");
});

test("a long tool result folds to a preview and opens on Show all", () => {
  const content = JSON.stringify({ rows: Array.from({ length: 20 }, (_, i) => i) });
  render(<ToolResult message={{ type: "tool", name: "ask", tool_call_id: "tc-1", content }} />);
  const toggle = screen.getByRole("button", { name: "Show all" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText(/…$/)).toBeInTheDocument();
  fireEvent.click(toggle);
  expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
});

test("an inbox edit refuses arguments that are not JSON and sends the edited action", () => {
  const onResume = vi.fn();
  const interrupt = { value: { action_requests: [{ name: "ask", args: { question: "Q" } }], review_configs: [{ action_name: "ask", allowed_decisions: ["approve", "edit", "reject"] }] } };
  render(<ThreadView interrupt={interrupt} onResume={onResume} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const box = screen.getByRole("textbox");
  fireEvent.change(box, { target: { value: "{not json" } });
  fireEvent.click(screen.getByRole("button", { name: "Send edit" }));
  expect(screen.getByRole("alert")).toHaveTextContent("not valid JSON");
  expect(onResume).not.toHaveBeenCalled();
  fireEvent.change(box, { target: { value: '{"question":"Q2"}' } });
  fireEvent.click(screen.getByRole("button", { name: "Send edit" }));
  const action = { name: "ask", args: { question: "Q2" } };
  expect(onResume).toHaveBeenCalledWith({ decisions: [{ type: "edit", edited_action: action, editedAction: action }] });
});

test("a LangGraph.js inbox request in camelCase is still an inbox request", () => {
  const camel = { value: { action_requests: [{ name: "a", args: {} }], review_configs: [{ actionName: "a", allowedDecisions: ["approve"] }] } };
  expect(isAgentInboxInterruptSchema(camel)).toBe(true);
  const onResume = vi.fn();
  render(<ThreadView interrupt={camel} onResume={onResume} />);
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  expect(onResume).toHaveBeenCalledWith({ decisions: [{ type: "approve" }] });
});

test("a failed request leaves the conversation on screen", async () => {
  const failing = async () => new Response("", { status: 500 });
  render(<Harness fetch={failing} />);
  await ask("Revenue?");
  await screen.findByRole("alert");
  expect(screen.getByText("Revenue?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
});
