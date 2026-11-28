// The copied Agent Chat UI components on B1a's stream: the pinned hook, its custom-backend adapter, and a stand-in for
// the portal server's stream route (issue 625: it keeps the conversation by its thread id), with a mock agent (no
// model, no LangGraph server).
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import React from "react";
import { expect, test, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { conversationServer } from "../../pages/Cerebro/chat/chatTestServer";
import { ChatInput, ChatThread, ConversationList, ThreadView, ToolResult, conversationTitle, isAgentInboxInterruptSchema, useAgentChatStream } from "./index";

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
  const server = conversationServer({ runTurn });
  return { requests: server.requests, ran: server.ran, release, portalFetch: server.fetch };
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
  // Issue 651 (B7): the model chip, static (no settings to open), under the input.
  expect(screen.queryByRole("button", { name: /^Answer settings:/ })).toBeNull();
  expect(document.getElementById("agentchat-settings")).toHaveTextContent("Model:");
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
  // Issue 625: the thread id names the conversation the server keeps.
  expect(agent.requests[0].config.configurable.thread_id).toEqual(expect.any(String));
});

test("the inbox resumes with the decision alone: no message, the server's saved conversation (issue 625)", async () => {
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
  expect(resume.input).toBeNull();
  expect(resume.command).toEqual({ resume: { decisions: [{ type: "approve" }] } });
  expect(resume.config.configurable.thread_id).toBe(agent.requests[0].config.configurable.thread_id);
  // The turn resumed on the conversation the server saved, the person's question first.
  expect(agent.ran[1][0]).toEqual(expect.objectContaining({ type: "human", content: "Revenue?" }));
});

// #624: an answer is never held for a Confirm; a generic interrupt shows its value and offers no decision of its own.
test("a generic interrupt shows its value with no Confirm or Decline", () => {
  const stream = { messages: [{ type: "human", id: "h", content: "Q" }], interrupt: { value: { kind: "unknown_pause" } }, submit: vi.fn(), isLoading: false };
  render(<ChatThread stream={stream} />);
  expect(screen.getByText("unknown_pause")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
  expect(stream.submit).not.toHaveBeenCalled();
});

test("a page renders an interrupt it knows in place of the inbox, and its answer resumes with no message (issue 625)", () => {
  const messages = [{ type: "human", id: "h", content: "Revenue?" }];
  const value = { kind: "hitl", question: "Which revenue?" };
  const stream = { messages, interrupt: [{ value, id: "i1" }], submit: vi.fn(), isLoading: false };
  const renderInterrupt = (interrupt, { onResume }) => (interrupt[0].value.kind === "hitl"
    ? <button type="button" onClick={() => onResume({ pick: "net" })}>{interrupt[0].value.question}</button> : undefined);
  const { unmount } = render(<ChatThread stream={stream} renderInterrupt={renderInterrupt} />);
  expect(screen.queryByRole("region", { name: "Waiting for you" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Which revenue?" }));
  expect(stream.submit).toHaveBeenCalledWith(null, { command: { resume: { pick: "net" } }, optimisticValues: { messages } });
  unmount();
  // One it does not know falls back to the inbox's own view.
  render(<ChatThread stream={{ ...stream, interrupt: [{ value: { kind: "other" }, id: "i2" }] }} renderInterrupt={renderInterrupt} />);
  expect(screen.getByRole("region", { name: "Waiting for you" })).toBeInTheDocument();
});

test("the HITL schema check accepts an inbox request and refuses anything else", () => {
  const ok = { value: { action_requests: [{ name: "a", args: {} }], review_configs: [{ action_name: "a", allowed_decisions: ["approve"] }] } };
  expect(isAgentInboxInterruptSchema(ok)).toBe(true);
  expect(isAgentInboxInterruptSchema([ok])).toBe(true);
  expect(isAgentInboxInterruptSchema({ value: { kind: "hitl" } })).toBe(false);
  expect(isAgentInboxInterruptSchema(null)).toBe(false);
});

test("the conversation list shows the conversations with their dates, names them by their first message, and starts a new one", () => {
  const onSelect = vi.fn();
  const onNew = vi.fn();
  const conversations = [
    { id: "a", messages: [{ type: "human", content: "Revenue last quarter?" }] },
    { id: "b", title: "Churn", updated: "2026-11-03T10:00:00Z" },
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
  expect(within(nav).getByText("3 Nov 2026")).toHaveAttribute("dateTime", "2026-11-03T10:00:00.000Z");
  expect(within(nav).queryByRole("button", { name: /^Delete/ })).toBeNull();
  expect(within(nav).queryByRole("button", { name: "Show more" })).toBeNull();
});

test("issue 625: Delete asks first, and Show more asks for the next page", () => {
  const onDelete = vi.fn();
  const onMore = vi.fn();
  render(<ConversationList conversations={[{ id: "a", title: "Churn", updated: Date.UTC(2026, 10, 3, 12) }]} onDelete={onDelete} more onMore={onMore} />);
  const nav = screen.getByRole("navigation", { name: "Conversations" });
  fireEvent.click(within(nav).getByRole("button", { name: "Delete “Churn”" }));
  const confirm = within(nav).getByRole("group", { name: "Delete “Churn”?" });
  // Focus goes to the confirm's Delete, and back to Delete on Cancel (issue 625 sign-off).
  expect(within(confirm).getByRole("button", { name: "Delete" })).toHaveFocus();
  fireEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
  expect(onDelete).not.toHaveBeenCalled();
  expect(within(nav).getByRole("button", { name: "Delete “Churn”" })).toHaveFocus();
  fireEvent.click(within(nav).getByRole("button", { name: "Delete “Churn”" }));
  fireEvent.click(within(within(nav).getByRole("group", { name: "Delete “Churn”?" })).getByRole("button", { name: "Delete" }));
  expect(onDelete).toHaveBeenCalledWith("a");
  fireEvent.click(within(nav).getByRole("button", { name: "Show more" }));
  expect(onMore).toHaveBeenCalledTimes(1);
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

// #622: the input takes a question to queue while a turn runs, and a fill puts text in the box.
test("while a turn runs, the box still takes a question: Enter or Ask next queues it, and the bar shows Stop alone otherwise", () => {
  const onSend = vi.fn();
  const onQueue = vi.fn();
  const { container, rerender } = render(<ChatInput isLoading onSend={onSend} onStop={vi.fn()} onQueue={onQueue} waiting={false} />);
  const named = () => within(container.querySelector(".agentchat-input-bar")).getAllByRole("button").map((b) => b.textContent).filter((t) => !t.startsWith("Answer"));
  expect(named()).toEqual(["Stop"]);
  expect(container.querySelector(".waiting-status")).toBeNull();
  const box = screen.getByRole("textbox", { name: "Ask the Assistant" });
  fireEvent.change(box, { target: { value: "Next one?" } });
  expect(named()).toEqual(["Ask next", "Stop"]);
  fireEvent.submit(box.closest("form"));
  expect(onQueue).toHaveBeenCalledWith("Next one?");
  expect(onSend).not.toHaveBeenCalled();
  expect(box).toHaveValue("");
  // Without onQueue, a running turn takes no question (Context's own use).
  rerender(<ChatInput isLoading onSend={onSend} onStop={vi.fn()} waiting={false} />);
  fireEvent.change(box, { target: { value: "Later?" } });
  fireEvent.submit(box.closest("form"));
  expect(onSend).not.toHaveBeenCalled();
  expect(named()).toEqual(["Stop"]);
});

test("a fill puts its text in the box and focuses it, each time it is given", () => {
  const ref = React.createRef();
  const { rerender } = render(<ChatInput onSend={vi.fn()} inputRef={ref} fill={{ text: "Again?" }} />);
  const box = screen.getByRole("textbox", { name: "Ask the Assistant" });
  expect(box).toHaveValue("Again?");
  expect(box).toHaveFocus();
  fireEvent.change(box, { target: { value: "" } });
  rerender(<ChatInput onSend={vi.fn()} inputRef={ref} fill={{ text: "Again?" }} />);
  expect(box).toHaveValue("Again?");
});

// Issue 651 (A1): the thread scrolls to new content while the person is at its foot, never while they have scrolled
// up to read, and always to the foot when they send a question.
test("the thread follows new content at its foot, leaves a person reading above alone, and scrolls to a question sent", () => {
  const msgs = (n) => Array.from({ length: n }, (_, i) => ({ type: i % 2 ? "ai" : "human", id: `m-${i}`, content: `Message ${i}` }));
  const stream = (n) => ({ messages: msgs(n), isLoading: false, submit: vi.fn(), stop: vi.fn() });
  const { rerender, container } = render(<ChatThread stream={stream(2)} />);
  const scroller = container.querySelector(".agentchat-messages");
  let height = 1000;
  Object.defineProperty(scroller, "scrollHeight", { configurable: true, get: () => height });
  Object.defineProperty(scroller, "clientHeight", { configurable: true, get: () => 300 });
  height = 1200;
  rerender(<ChatThread stream={stream(3)} />);
  expect(scroller.scrollTop).toBe(1200);
  // The person scrolls up to read: new content does not move them.
  scroller.scrollTop = 100;
  fireEvent.scroll(scroller);
  height = 1400;
  rerender(<ChatThread stream={stream(4)} />);
  expect(scroller.scrollTop).toBe(100);
  // Back near the foot (within STICK_PX), new content follows again.
  scroller.scrollTop = 1400 - 300 - 40;
  fireEvent.scroll(scroller);
  height = 1600;
  rerender(<ChatThread stream={stream(5)} />);
  expect(scroller.scrollTop).toBe(1600);
  // Scrolled up, a question sent still scrolls to the foot.
  scroller.scrollTop = 0;
  fireEvent.scroll(scroller);
  const sending = stream(5);
  rerender(<ChatThread stream={sending} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: "Next?" } });
  height = 1800;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect(sending.submit).toHaveBeenCalled();
  // The stream shows the question sent (the hook's optimistic values).
  rerender(<ChatThread stream={stream(6)} />);
  expect(scroller.scrollTop).toBe(1800);
});
