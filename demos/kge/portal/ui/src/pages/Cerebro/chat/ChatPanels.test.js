// Cerebro's chat panels (B7c) on the whole page with the real Chat view, on the portal server's own stream handler
// called in process with a mock agent: the conversation survives mode switches and shows in Graph mode's dock, the
// sessions rail holds the page's conversations, the mini-graph draws the last answer's path with "Open graph", both
// panels fold, the picker shows KGE's /health `ask`, and a reload keeps only what §7.2 keeps.
import React from "react";
import { readFileSync } from "node:fs";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Provider } from "react-redux";
import { configureStore } from "@reduxjs/toolkit";
import { MemoryRouter, useLocation } from "react-router-dom";
import { cerebroStream } from "../../../../../server/src/cerebro/route.js";
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

function mockAgent() {
  const requests = [];
  let turn = 0;
  async function* runTurn({ messages }) {
    turn += 1;
    const call = { type: "ai", id: `ai-${turn}`, content: "", tool_calls: [{ id: `tc-${turn}`, name: "ask", args: {} }] };
    const result = { type: "tool", id: `tool-${turn}`, tool_call_id: `tc-${turn}`, name: "ask", content: JSON.stringify(reply(turn)) };
    yield ["values", { messages: [...messages, call, result] }];
  }
  const handler = cerebroStream({ runTurn });
  async function fetch(_url, init) {
    const body = JSON.parse(init.body);
    requests.push(body);
    let controller;
    const stream = new ReadableStream({ start: (c) => (controller = c) });
    const encoder = new TextEncoder();
    let status = 200;
    const res = { on() {}, writeHead: (s) => (status = s), write: (chunk) => controller.enqueue(encoder.encode(chunk)), end: () => controller.close() };
    void handler({ body }, res);
    return new Response(stream, { status, headers: { "content-type": "text/event-stream" } });
  }
  return { requests, fetch };
}

function Where() {
  const { search } = useLocation();
  return <output data-testid="where">{search}</output>;
}

function renderPage(path = "/cerebro?mode=chat", chat = {}) {
  const agent = mockAgent();
  const store = configureStore({ reducer: { user: () => ({ data: { cerebroRoles: ["business"] } }) } });
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
  const { agent } = renderPage();
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
  const sent = agent.requests[1].input.messages;
  expect(sent.map((m) => m.type)).toEqual(["human", "ai", "tool", "human"]);
  expect(sent[0].content).toBe("Revenue?");
});

test("a question asked in Graph mode's dock is the same conversation Chat mode shows", async () => {
  const { agent } = renderPage("/cerebro?mode=graph");
  await ask("Docked?");
  await within(screen.getByRole("complementary", { name: "Assistant" })).findByText("Revenue was 1.");
  await mode("Chat");
  expect(within(screen.getByRole("region", { name: "Conversation" })).getByText("Docked?", { selector: ".agentchat-bubble" })).toBeInTheDocument();
  expect(agent.requests).toHaveLength(1);
});

test("Hide folds the assistant to a Show assistant button, keeping the conversation and the question being typed", async () => {
  renderPage("/cerebro?mode=graph");
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
  renderPage("/cerebro?mode=graph");
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

test("the sessions rail lists the page's conversations; New chat starts one and an old one comes back whole", async () => {
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
  await act(async () => fireEvent.click(within(rail()).getByRole("button", { name: "First chat?" })));
  expect(screen.getByText("Revenue was 1.")).toBeInTheDocument();
  expect(screen.queryByText("Revenue was 2.")).not.toBeInTheDocument();
  await ask("Follow up?");
  await screen.findByText("Revenue was 3.");
  expect(agent.requests[2].input.messages.map((m) => m.content)[0]).toBe("First chat?");
  expect(agent.requests[2].input.messages).toHaveLength(4);
});

test("Chat mode: the sessions rail on the left (New chat, the sessions, the graph card) beside the conversation", () => {
  renderPage();
  const rail = screen.getByRole("region", { name: "Sessions" });
  expect(rail).toHaveClass("cerebro-rail");
  expect(rail.parentElement).toHaveClass("cerebro-chat", "cerebro-chat-live");
  expect(rail.nextElementSibling).toBe(screen.getByRole("region", { name: "Conversation" }));
  expect(within(rail).getAllByRole("button", { name: "New chat" })[0]).toHaveClass("primary");
  expect(within(rail).getByRole("navigation", { name: "Conversations" })).toBeInTheDocument();
  expect(within(rail).getByRole("figure", { name: "The last answer's graph" })).toBeInTheDocument();
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
  expect(screen.getByRole("figure", { name: "The last answer's graph", hidden: true })).not.toBeVisible();
  expect(screen.getByRole("textbox", { name: "Ask the Assistant" })).toBeVisible();
  fireEvent.click(within(panel).getByRole("button", { name: "Show sessions" }));
  expect(panel).not.toHaveClass("folded");
  expect(screen.getByRole("navigation", { name: "Conversations" })).toBeVisible();
});

test("the mini-graph draws the last answer's used path, and Open graph goes to Graph mode with that path", async () => {
  renderPage();
  expect(screen.getByRole("button", { name: "Open graph" })).toBeDisabled();
  expect(screen.getByText("No answer with a path yet.")).toBeInTheDocument();
  expect(screen.getByRole("figure", { name: "The last answer's graph" }).querySelector("svg")).toBeNull();
  await ask("Revenue?");
  await screen.findByText("Revenue was 1.");
  const mini = screen.getByRole("figure", { name: "The last answer's graph" });
  expect(within(mini).getByRole("img")).toHaveAccessibleName("Path of 2 nodes: Sales, Revenue");
  expect(within(mini).queryByText("Unused")).not.toBeInTheDocument();
  expect(within(mini).getByText("The path the last answer read.")).toBeInTheDocument();
  expect(within(mini).getByRole("button", { name: "Open graph" })).toBeEnabled();
  await act(async () => fireEvent.click(within(mini).getByRole("button", { name: "Open graph" })));
  expect(screen.getByTestId("where").textContent).toBe("?mode=graph");
  expect(JSON.parse(screen.getByTestId("graph-view").dataset.path)).toEqual(PATH);
});

test("miniLayout keeps used nodes and the links between them only", () => {
  const { nodes, edges, more } = miniLayout(PATH);
  expect(nodes.map((n) => n.id)).toEqual(["table:sales", "term:revenue"]);
  expect(edges).toHaveLength(1);
  expect(more).toBe(0);
  expect(miniLayout(null)).toEqual({ nodes: [], edges: [], more: 0 });
  expect(miniLayout(["a", "b"]).nodes).toEqual([]);
  render(<MiniGraph path={null} onOpen={() => {}} />);
  expect(screen.getByText("No answer with a path yet.")).toBeInTheDocument();
});

test("the picker shows KGE's /health ask model and no AI-fallback control", () => {
  renderPage("/cerebro?mode=chat", { health: { default: "strict", modes: ["off", "strict"], model: "vertex:gemini-flash" } });
  const picker = screen.getByRole("button", { name: /Answer settings/ });
  expect(picker).toHaveTextContent("vertex:gemini-flash");
  expect(picker).not.toHaveTextContent(/fallback/i);
  fireEvent.click(picker);
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
});

test("a reload keeps what §7.2 keeps (the mode and node, in the address) and nothing else; no question text is stored", async () => {
  const { unmount } = renderPage("/cerebro?mode=chat&node=table%3Asales");
  await ask("Secret question?");
  await screen.findByText("Revenue was 1.");
  const address = screen.getByTestId("where").textContent;
  expect(address).toBe("?mode=chat&node=table%3Asales");
  expect(address).not.toContain("Secret");
  const stored = JSON.stringify({ ...window.localStorage }) + JSON.stringify({ ...window.sessionStorage });
  expect(stored).not.toContain("Secret");
  unmount();
  renderPage(address);
  expect(screen.getByRole("tab", { name: "Chat" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("where").textContent).toBe(address);
  expect(screen.queryByText("Secret question?")).not.toBeInTheDocument();
  await waitFor(() => expect(answers()).toHaveLength(0));
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
