import React from "react";
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChatView from "./ChatView";
import { ChatProvider } from "./chatSession";
import { askTurn, mockAgent } from "./chatTestAgent";

afterEach(cleanup);
test("a saved figure is shown while evidence is pending, then the same card is updated", async () => {
  const figure = { question: "Revenue?", behaviour: "figure", figure: 42, currency: "USD", answer_text: "Certified: USD 42.00.", answer_mode: "certified", document_evidence_pending: true };
  const agent = mockAgent([askTurn(figure)]);
  let polls = 0;
  const fetch = async (url, init) => {
    if (String(url).includes('/runs/')) {
      polls += 1;
      const value = polls === 1 ? figure : { ...figure, document_evidence_pending: false, document_answer: { intro: "Source evidence.", excerpts: [{ doc: "policy", title: "Policy", text: "An authorized excerpt." }] } };
      return new Response(JSON.stringify({ state: "ended", saved: polls !== 2, evidence_pending: polls < 3, message: { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(value) } }), { status: 200, headers: { "content-type": "application/json" } });
    }
    return agent.fetch(url, init);
  };
  render(<ChatView state={{ roles: ["business"], userId: "reader" }} actions={{ setLastPath: vi.fn() }} fetch={fetch} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: "Revenue?" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  expect(await screen.findByRole("article", { name: "Answer" })).toHaveTextContent("42.00");
  await waitFor(() => expect(polls).toBeGreaterThanOrEqual(3), { timeout: 10000 });
  await screen.findByText("An authorized excerpt.");
  expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1);
});

test("reopening a saved figure resumes its pending evidence", async () => {
  const id = "d58573df-5d77-4f10-9e15-cbef9bd8c221";
  const figure = { question: "Revenue?", behaviour: "figure", figure: 42, currency: "USD", answer_text: "Certified: USD 42.00.", answer_mode: "certified", document_evidence_pending: true };
  const agent = mockAgent([]);
  agent.server.seed({ id, title: "Revenue?", messages: [{ type: "human", id: "human-1", content: "Revenue?" }, { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(figure) }] });
  let polls = 0;
  const fetch = async (url, init) => {
    if (String(url).includes("/runs/")) {
      polls += 1;
      const value = { ...figure, document_evidence_pending: false, document_answer: { intro: "Evidence.", excerpts: [{ doc: "policy", text: "Saved evidence." }] } };
      return new Response(JSON.stringify({ state: "ended", saved: true, evidence_pending: false, message: { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(value) } }), { status: 200 });
    }
    return agent.fetch(url, init);
  };
  render(<ChatProvider actions={{}} openId={id} fetch={fetch}><ChatView state={{ roles: ["business"], userId: "reader" }} actions={{}} /></ChatProvider>);
  expect(await screen.findByRole("article", { name: "Answer" })).toHaveTextContent("42.00");
  expect(await screen.findByText("Saved evidence.", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(polls).toBe(1);
});

test("a background result shows its figure before evidence finishes", async () => {
  const question = "Revenue?";
  const pending = { question, run: { id: "abc", state: "running", poll_after: 0.01 } };
  const figure = { question, behaviour: "figure", figure: 42, currency: "USD", answer_text: "Certified: USD 42.00.", answer_mode: "certified", document_evidence_pending: true };
  const agent = mockAgent([askTurn(pending)]);
  let polls = 0, finish;
  const gate = new Promise((resolve) => { finish = resolve; });
  const fetch = async (url, init) => {
    if (!String(url).includes("/runs/")) return agent.fetch(url, init);
    polls += 1;
    if (polls > 1) await gate;
    const value = polls === 1 ? figure : { ...figure, document_evidence_pending: false, document_answer: { intro: "Evidence.", excerpts: [{ doc: "policy", text: "Later evidence." }] } };
    return new Response(JSON.stringify({ state: "ended", saved: true, evidence_pending: polls === 1, message: { type: "tool", id: "tool-1", tool_call_id: "tc-1", name: "ask", card: true, content: JSON.stringify(value) } }), { status: 200 });
  };
  render(<ChatView state={{ roles: ["business"], userId: "reader" }} actions={{ setLastPath: vi.fn() }} fetch={fetch} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Ask the Assistant" }), { target: { value: question } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
  await screen.findByRole("article", { name: "Running in the background" });
  expect(await screen.findByRole("article", { name: "Answer" }, { timeout: 5000 })).toHaveTextContent("42.00");
  expect(screen.queryByText("Later evidence.")).toBeNull();
  finish();
  expect(await screen.findByText("Later evidence.", {}, { timeout: 5000 })).toBeInTheDocument();
  expect(screen.getAllByRole("article", { name: "Answer" })).toHaveLength(1);
});
