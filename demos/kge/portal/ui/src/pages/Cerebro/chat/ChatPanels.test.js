// Cerebro's chat panels (B7c) on the whole page with the real Chat view, on a stand-in for the portal server's Cerebro
// routes (chatTestServer.js) with a mock agent: the conversation survives mode switches and shows in Graph mode's dock,
// the sessions rail holds the person's conversations the server keeps (#625: listed a page at a time, opened, deleted),
// #651: no mini graph or answers in the rail, the rail folds, the model chip shows KGE's /health
// `ask`, and a reload keeps what §7.2 keeps and reopens the conversation the address names.
import React from "react";
import { readFileSync } from "node:fs";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Provider } from "react-redux";
import { configureStore } from "@reduxjs/toolkit";
import { MemoryRouter, useLocation } from "react-router-dom";
import { conversationServer } from "./chatTestServer";
import { RAIL_PAGE } from "./chatSession";
import CerebroApp from "../CerebroApp";
import MiniGraph, { miniLayout } from "./MiniGraph";

vi.mock("../../../components/shell/ShellPage", () => ({ default: ({ children }) => <div>{children}</div> }));
vi.mock("../graph/GraphView", () => ({ default: ({ state }) => <div data-testid="graph-view" data-path={JSON.stringify(state.lastPath)} /> }));
vi.mock("../curate/CurateView", () => ({ default: () => <section aria-label="Proposals" /> }));
vi.mock("../../../services/config", async (actual) => ({ ...(await actual()), useConfig: () => ({ cerebroName: "Cerebro" }) }));

afterEach(cleanup);

const PATH = {
  nodes: [
    { id: "table:sales", type: "table", label: "Sales", used: true },
    { id: "term:revenue", type: "term", label: "Revenue", used: true },
    { id: "table:unused", type: "table", label: "Unused", used: false },
  ],
  edges: [{ from: "term:revenue", to: "table:sales", type: "maps_to", used: true }],
  unused: 1,
};
const reply = (n) => ({ answer_text: `Certified: Revenue was ${n}.`, answer_mode: "certified", receipt: { id: `r-${n}` }, graph_path: PATH });

function mockAgent(options = {}) {
  let turn = 0;
  async function* runTurn({ messages }) {
    turn += 1;
    const call = { type: "ai", id: `ai-${turn}`, content: "", tool_calls: [{ id: `tc-${turn}`, name: "ask", args: {} }] };
    const result = { type: "tool", id: `tool-${turn}`, tool_call_id: `tc-${turn}`, name: "ask", content: JSON.stringify(reply(turn)) };
    yield ["values", { messages: [...messages, call, result] }];
  }
  const server = conversationServer({ runTurn, ...options });
  return { requests: server.requests, ran: server.ran, fetch: server.fetch, server };
}

function Where() {
  const { search } = useLocation();
  return <output data-testid="where">{search}</output>;
}

function renderPage(path = "/cerebro?mode=chat", chat = {}, agent = mockAgent(), roles = ["business"]) {
  const store = configureStore({ reducer: { user: () => ({ data: { cerebroRoles: roles } }) } });
  const page = <Provider store={store}><MemoryRouter initialEntries={[path]}><CerebroApp chat={{ fetch: agent.fetch, health: null, ...chat }} /><Where /></MemoryRouter></Provider>;
  return { agent, ...render(page), page };
}

async function ask(question) {
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
}
const answers = () => screen.queryAllByRole("article", { name: "Answer" });
const mode = (name) => act(async () => fireEvent.click(screen.getByRole("tab", { name })));

test("the conversation survives Chat → Graph → Chat, shows in Graph mode's dock, and the next question carries it all", async () => {
  const { agent } = renderPage(undefined, {}, undefined, ["business", "engineer"]);
  await ask("Revenue?");
  await screen.findByText("Revenue was 1.");
  await mode("Graph");
  const dock = screen.getByRole("complementary", { name: "Assistant" });
  expect(within(dock).getByText("Revenue?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  expect(within(dock).getByText("Revenue was 1.")).toBeInTheDocument();
  expect(JSON.parse(screen.getByTestId("graph-view").dataset.path)).toEqual(PATH);
  await mode("Chat");
  expect(screen.queryByRole("complementary", { name: "Assistant" })).not.toBeInTheDocument();
  expect(screen.getByText("Revenue was 1.")).toBeInTheDocument();
  await ask("And costs?");
  await screen.findByText("Revenue was 2.");
  // #625: the page sends the new question alone, on the same conversation; the server ran it on the whole of it.
  expect(agent.requests[1].input.messages.map((m) => m.content)).toEqual(["And costs?"]);
  expect(agent.requests[1].config.configurable.thread_id).toBe(agent.requests[0].config.configurable.thread_id);
  const ran = agent.ran[1];
  expect(ran.map((m) => m.type)).toEqual(["human", "ai", "tool", "human"]);
  expect(ran[0].content).toBe("Revenue?");
});

test("a question asked in Graph mode's dock is the same conversation Chat mode shows", async () => {
  const { agent } = renderPage("/cerebro?mode=graph", {}, undefined, ["business", "engineer"]);
  await ask("Docked?");
  await within(screen.getByRole("complementary", { name: "Assistant" })).findByText("Revenue was 1.");
  await mode("Chat");
  expect(within(screen.getByRole("region", { name: "Conversation" })).getByText("Docked?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  expect(agent.requests).toHaveLength(1);
});

test("Hide folds the assistant to a Show assistant button, keeping the conversation and the question being typed", async () => {
  renderPage("/cerebro?mode=graph", {}, undefined, ["business", "engineer"]);
  await ask("Fold?");
  const dock = screen.getByRole("complementary", { name: "Assistant" });
  await within(dock).findByText("Revenue was 1.");
  expect(within(dock).getByRole("region", { name: "Assistant" })).toBeVisible();
  fireEvent.change(within(dock).getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: "Half typed" } });
  const hide = within(dock).getByRole("button", { name: "Hide assistant" });
  expect(hide).toHaveTextContent("Hide");
  expect(hide).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(hide);
  expect(dock).toHaveClass("cerebro-dock", "folded");
  const show = within(dock).getByRole("button", { name: "Show assistant" });
  expect(show).toHaveAttribute("aria-expanded", "false");
  expect(show).toHaveFocus();
  expect(within(dock).getByRole("textbox", { name: "Ask the Assistant", hidden: true })).not.toBeVisible();
  expect(within(dock).queryByRole("button", { name: "Hide assistant" })).not.toBeInTheDocument();
  fireEvent.click(show);
  expect(dock).not.toHaveClass("folded");
  expect(within(dock).queryByRole("button", { name: "Show assistant" })).not.toBeInTheDocument();
  expect(within(dock).getByRole("button", { name: "Hide assistant" })).toHaveFocus();
  expect(within(dock).getByText("Revenue was 1.")).toBeVisible();
  expect(within(dock).getByRole("textbox", { name: "Ask the Assistant" })).toHaveValue("Half typed");
});

test("the assistant floats over the graph: the view's sibling after the graph, absolutely placed, never a column", () => {
  renderPage("/cerebro?mode=graph", {}, undefined, ["business", "engineer"]);
  const dock = screen.getByRole("complementary", { name: "Assistant" });
  expect(dock).toHaveClass("cerebro-dock");
  expect(dock.parentElement).toHaveClass("cerebro-view");
  expect(dock.previousElementSibling).toBe(screen.getByTestId("graph-view"));
  const css = readFileSync("src/pages/Cerebro/chat/chat.css", "utf8");
  const rule = (selector) => {
    const at = css.indexOf(`\n${selector} {`);
    expect(at, selector).toBeGreaterThanOrEqual(0);
    return css.slice(at, css.indexOf("}", at));
  };
  const open = rule(".cerebro-dock");
  expect(open).toMatch(/position:absolute/);
  expect(open).toMatch(/bottom:16px/);
  expect(open).toMatch(/transform:translateX\(-50%\)/);
  expect(open).toMatch(/width:min\(760px,/);
  // The lower third of the canvas at most; the graph's fit leaves its height free (GraphFit.test.js).
  expect(open).toMatch(/max-height:38%/);
  expect(open).not.toMatch(/(^|[\s;{])(flex:|flex-basis|grid-column)/);
  const folded = rule(".cerebro-dock.folded");
  expect(folded).toMatch(/left:auto; right:/);
  expect(folded).toMatch(/transform:none/);
  expect(rule(".cerebro-dock-card")).toMatch(/min-height:0/);
  expect(css).toMatch(/\.cerebro-dock-card\[hidden\]\s*\{\s*display:none\s*\}/);
  expect(css).toMatch(/\.cerebro-dock \.agentchat-thread \{[^}]*min-height:0/);
});

test("the sessions rail lists the person's conversations; New chat starts one and an old one comes back whole", async () => {
  const { agent } = renderPage();
  const rail = () => screen.getByRole("navigation", { name: "Conversations" });
  await ask("First chat?");
  await screen.findByText("Revenue was 1.");
  expect(within(rail()).getByRole("button", { name: "First chat?" })).toHaveAttribute("aria-current", "true");
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "New chat" })));
  expect(answers()).toHaveLength(0);
  await ask("Second chat?");
  await screen.findByText("Revenue was 2.");
  expect(agent.requests[1].input.messages.map((m) => m.content)).toEqual(["Second chat?"]);
  // #625: New chat is a new conversation id; nothing was created on the server for it before its question.
  const [first, second] = agent.requests.map((r) => r.config.configurable.thread_id);
  expect(second).not.toBe(first);
  expect(within(rail()).getAllByRole("button", { name: /chat\?$/ }).map((b) => b.textContent)).toEqual(["Second chat?", "First chat?"]);
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "First chat?" })));
  // Opening one reads it from the server.
  expect(agent.server.calls).toContain(`GET /api/cerebro/conversations/${first}`);
  await screen.findByText("Revenue was 1.");
  expect(screen.queryByText("Revenue was 2.")).not.toBeInTheDocument();
  await ask("Follow up?");
  await screen.findByText("Revenue was 3.");
  expect(agent.requests[2].input.messages.map((m) => m.content)).toEqual(["Follow up?"]);
  expect(agent.requests[2].config.configurable.thread_id).toBe(first);
  expect(agent.ran[2].map((m) => m.content)[0]).toBe("First chat?");
  expect(agent.ran[2]).toHaveLength(4);
});

test("#625: the rail lists from the route, newest first with its date, and Show more reads the next page", async () => {
  const agent = mockAgent();
  const many = RAIL_PAGE + 2;
  for (let n = 1; n <= many; n += 1) {
    const day = String(n).padStart(2, "0");
    agent.server.seed({ id: `c-${n}`, title: `Chat ${n}`, updated: `2026-10-${day}T10:00:00.000Z`, messages: [{ type: "human", id: `h-${n}`, content: `Chat ${n}` }] });
  }
  agent.server.seed({ id: "theirs", owner: "someone else", title: "Not mine", updated: "2026-11-04T10:00:00.000Z" });
  renderPage("/cerebro?mode=chat", {}, agent);
  const rail = () => screen.getByRole("navigation", { name: "Conversations" });
  await within(rail()).findByRole("button", { name: `Chat ${many}` });
  const titles = () => within(rail()).getAllByRole("button", { name: /^Chat \d+$/ }).map((b) => b.textContent);
  const newestFirst = Array.from({ length: many }, (_, i) => `Chat ${many - i}`);
  expect(titles()).toEqual(newestFirst.slice(0, RAIL_PAGE));
  expect(within(rail()).getByText(`${many} Oct 2026`)).toBeInTheDocument();
  expect(within(rail()).queryByText("Not mine")).toBeNull();
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "Show more" })));
  await waitFor(() => expect(titles()).toEqual(newestFirst));
  expect(within(rail()).queryByRole("button", { name: "Show more" })).toBeNull();
  expect(agent.server.calls.filter((c) => c.startsWith("GET /api/cerebro/conversations?"))).toEqual([
    `GET /api/cerebro/conversations?page_size=${RAIL_PAGE}`, `GET /api/cerebro/conversations?page_size=${RAIL_PAGE}&page_token=${RAIL_PAGE}`,
  ]);
});

test("#625: Delete, once confirmed, removes the conversation on the server and from the rail; the open one gives way to a new chat", async () => {
  const agent = mockAgent();
  agent.server.seed({ id: "c-old", title: "Old chat", messages: [{ type: "human", id: "h-old", content: "Old chat" }] });
  renderPage("/cerebro?mode=chat", {}, agent);
  const rail = () => screen.getByRole("navigation", { name: "Conversations" });
  await ask("Open one?");
  await screen.findByText("Revenue was 1.");
  const open = agent.requests[0].config.configurable.thread_id;
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "Delete “Old chat”" })));
  await act(async () => fireEvent.click(within(within(rail()).getByRole("group", { name: "Delete “Old chat”?" })).getByRole("button", { name: "Delete" })));
  await waitFor(() => expect(within(rail()).queryByRole("button", { name: "Old chat" })).toBeNull());
  expect(agent.server.conversations.has("c-old")).toBe(false);
  expect(screen.getByText("Revenue was 1.")).toBeInTheDocument();
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "Delete “Open one?”" })));
  await act(async () => fireEvent.click(within(within(rail()).getByRole("group", { name: "Delete “Open one?”?" })).getByRole("button", { name: "Delete" })));
  await waitFor(() => expect(answers()).toHaveLength(0));
  expect(agent.server.calls).toContain(`DELETE /api/cerebro/conversations/${open}`);
  expect(agent.server.conversations.size).toBe(0);
  expect(within(rail()).getByText("No conversations yet.")).toBeInTheDocument();
  expect(screen.getByTestId("where").textContent).toBe("?mode=chat");
});

test("Chat mode: the sessions rail on the left (New chat, the sessions, the graph card) beside the conversation", () => {
  renderPage();
  const rail = screen.getByRole("region", { name: "Sessions" });
  expect(rail).toHaveClass("cerebro-rail");
  expect(rail.parentElement).toHaveClass("cerebro-chat", "cerebro-chat-live");
  // #626: the conversation sits in the workspace beside the rail, where a card opens in the side panel to its right.
  expect(rail.nextElementSibling).toHaveClass("cerebro-workspace");
  expect(rail.nextElementSibling.firstElementChild).toBe(screen.getByRole("region", { name: "Conversation" }));
  expect(within(rail).getAllByRole("button", { name: "New chat" })[0]).toHaveClass("primary");
  expect(within(rail).getByRole("navigation", { name: "Conversations" })).toBeInTheDocument();
  // #651 (B1): the rail holds the conversations only: no mini graph, no answers list.
  expect(within(rail).queryByRole("figure")).toBeNull();
  expect(within(rail).queryByText("Answers in this chat")).toBeNull();
  expect(screen.queryByRole("complementary", { name: "Assistant" })).not.toBeInTheDocument();
});

test("the sessions rail folds to a strip and opens again", async () => {
  renderPage();
  const panel = screen.getByRole("region", { name: "Sessions" });
  fireEvent.click(within(panel).getByRole("button", { name: "Hide sessions" }));
  expect(panel).toHaveClass("folded");
  expect(panel.parentElement).toHaveClass("rail-folded");
  expect(within(panel).getByRole("button", { name: "Show sessions" })).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByRole("navigation", { name: "Conversations", hidden: true })).not.toBeVisible();
  expect(screen.getByRole("textbox", { name: "Ask the Assistant" })).toBeVisible();
  fireEvent.click(within(panel).getByRole("button", { name: "Show sessions" }));
  expect(panel).not.toHaveClass("folded");
  expect(screen.getByRole("navigation", { name: "Conversations" })).toBeVisible();
});

test("#651 (B1, B9): no mini graph in the rail and no Open graph; the last answer's path still lights up Graph mode", async () => {
  const { agent } = renderPage(undefined, {}, undefined, ["business", "engineer"]);
  await ask("Revenue?");
  await screen.findByText("Revenue was 1.");
  expect(screen.queryByRole("figure", { name: "The last answer's graph" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Open graph" })).toBeNull();
  await act(async () => fireEvent.click(screen.getByRole("tab", { name: "Graph" })));
  // #625: the open conversation stays in the address across modes.
  expect(screen.getByTestId("where").textContent).toBe(`?mode=graph&chat=${agent.requests[0].config.configurable.thread_id}`);
  expect(JSON.parse(screen.getByTestId("graph-view").dataset.path)).toEqual(PATH);
});

test("#651 (B9): the mini graph draws the used path read-only, with no Open graph button", () => {
  render(<MiniGraph path={PATH} />);
  const mini = screen.getByRole("figure", { name: "The last answer's graph" });
  expect(within(mini).getByRole("img")).toHaveAccessibleName("Path of 2 nodes: Sales, Revenue");
  expect(within(mini).queryByText("Unused")).not.toBeInTheDocument();
  expect(within(mini).getByText("The path the last answer read.")).toBeInTheDocument();
  expect(within(mini).queryByRole("button")).toBeNull();
});

test("miniLayout keeps used nodes and the links between them only", () => {
  const { nodes, edges, more } = miniLayout(PATH);
  expect(nodes.map((n) => n.id)).toEqual(["table:sales", "term:revenue"]);
  expect(edges).toHaveLength(1);
  expect(more).toBe(0);
  expect(miniLayout(null)).toEqual({ nodes: [], edges: [], more: 0 });
  expect(miniLayout(["a", "b"]).nodes).toEqual([]);
  render(<MiniGraph path={null} />);
  expect(screen.getByText("No answer with a path yet.")).toBeInTheDocument();
});

test("#651 (B7): the model chip under the input shows the health route's model only, with no settings and no AI-fallback control", () => {
  renderPage("/cerebro?mode=chat", { health: { default: "strict", modes: ["off", "strict"], model: "vertex:gemini-flash" } });
  const chip = document.getElementById("agentchat-settings");
  expect(chip.closest(".agentchat-input")).not.toBeNull();
  expect(chip).toHaveTextContent("vertex:gemini-flash");
  expect(chip).not.toHaveTextContent(/fallback/i);
  expect(screen.queryByRole("button", { name: /Answer settings/ })).toBeNull();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
});

test("#651 (B7): with no model stated, the chip says none", () => {
  renderPage("/cerebro?mode=chat", { health: null });
  expect(document.getElementById("agentchat-settings")).toHaveTextContent("none");
});

test("a reload keeps what §7.2 keeps (the mode, node and open conversation's id, in the address) and reopens the conversation from the server; no question text is stored in the browser", async () => {
  const agent = mockAgent();
  const { unmount } = renderPage("/cerebro?mode=chat&node=table%3Asales", {}, agent);
  await ask("Secret question?");
  await screen.findByText("Revenue was 1.");
  const id = agent.requests[0].config.configurable.thread_id;
  const address = screen.getByTestId("where").textContent;
  expect(address).toBe(`?mode=chat&node=table%3Asales&chat=${id}`);
  expect(address).not.toContain("Secret");
  const stored = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage });
  expect(stored).not.toContain("Secret");
  unmount();
  renderPage(address, {}, agent);
  expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("where").textContent).toBe(address);
  await screen.findByText("Revenue was 1.");
  expect(screen.getByText("Secret question?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  expect(agent.server.calls).toContain(`GET /api/cerebro/conversations/${id}`);
  // A new page with no conversation named starts empty.
  cleanup();
  renderPage("/cerebro?mode=chat", {}, agent);
  await waitFor(() => expect(answers()).toHaveLength(0));
  expect(screen.queryByText("Secret question?", { selector: ".agentchat-bubble" })).not.toBeInTheDocument();
});

test("a folded body is hidden by the stylesheet too: the rail's display rule gives way to [hidden]", () => {
  const css = readFileSync("src/pages/Cerebro/chat/chat.css", "utf8");
  expect(css).toMatch(/\.cerebro-rail-body\[hidden\]\s*\{\s*display:none\s*\}/);
});

test("the chat's stylesheet uses the portal's tokens only: no colour or font of its own", () => {
  const css = readFileSync("src/pages/Cerebro/chat/chat.css", "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
  expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i);
  expect(css).not.toMatch(/font-family:|font:[^;}]*(sans-serif|serif|monospace|Inter|Plex)/i);
});

test("#625: an address whose chat is not a UUID opens a new chat and leaves the address; nothing is read for it", async () => {
  const agent = mockAgent();
  renderPage("/cerebro?mode=chat&chat=c-1", {}, agent);
  await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("?mode=chat"));
  expect(agent.server.calls.filter((c) => c.startsWith("GET /api/cerebro/conversations/"))).toEqual([]);
  await ask("Fresh?");
  await screen.findByText("Revenue was 1.");
  const id = agent.requests[0].config.configurable.thread_id;
  expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  expect(screen.getByTestId("where").textContent).toBe(`?mode=chat&chat=${id}`);
});

test("#625: a question refused before its stream began (a 503) adds no rail row and leaves the address; the next, streamed, adds both", async () => {
  const agent = mockAgent();
  let refuse = true;
  const fetch = async (url, init) => {
    if (refuse && String(url).includes("/api/cerebro/stream")) {
      return new Response(JSON.stringify({ detail: "Conversations cannot be read right now. Try again shortly." }), { status: 503, headers: { "content-type": "application/json" } });
    }
    return agent.fetch(url, init);
  };
  renderPage("/cerebro?mode=chat", { fetch }, agent);
  const rail = () => screen.getByRole("navigation", { name: "Conversations" });
  await ask("Refused?");
  await screen.findByRole("article", { name: "Not answered" });
  expect(within(rail()).queryByRole("button", { name: "Refused?" })).toBeNull();
  expect(screen.getByTestId("where").textContent).toBe("?mode=chat");
  refuse = false;
  await ask("Streamed?");
  await screen.findByText("Revenue was 1.");
  const id = agent.requests[0].config.configurable.thread_id;
  expect(within(rail()).getByRole("button", { name: "Streamed?" })).toHaveAttribute("aria-current", "true");
  expect(screen.getByTestId("where").textContent).toBe(`?mode=chat&chat=${id}`);
});

test("#625 sign-off: a new chat whose first turn was not saved adds no rail row and leaves the address", async () => {
  const notSaved = { case: "not_saved", message: "This answer could not be saved: try again.", action: "try_again" };
  async function* runTurn({ messages }) {
    yield ["values", { messages, trimmed: 0 }];
    yield ["custom", { kind: "outcome", outcome: "failed", steps: 1, error: notSaved }];
  }
  const server = conversationServer({ runTurn });
  renderPage("/cerebro?mode=chat", {}, { requests: server.requests, ran: server.ran, fetch: server.fetch, server });
  await ask("Unsaved?");
  const card = await screen.findByRole("article", { name: "Not answered" });
  expect(within(card).getByRole("alert")).toHaveTextContent("This answer could not be saved: try again.");
  expect(within(screen.getByRole("navigation", { name: "Conversations" })).queryByRole("button", { name: "Unsaved?" })).toBeNull();
  expect(screen.getByTestId("where").textContent).toBe("?mode=chat");
});
