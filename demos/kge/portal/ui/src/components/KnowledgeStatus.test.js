import { it, expect } from "vitest";
import React from "react";
import { render, screen, act, fireEvent } from "@testing-library/react";
import { KnowledgeNotice, KnowledgeMatches, publishKnowledge, clearKnowledge, knowledgeEpoch } from "./KnowledgeStatus";

it("shows match explanations on hover or focus, then clears them on recovery", () => {
  render(<><KnowledgeNotice /><KnowledgeMatches /></>);
  act(() => publishKnowledge({ headers: {}, data: { notice: "Live data is unavailable. Using the portal catalogue.", hits: [{ kind: "term", unit: "retail", id: "balance", title: "Balance", score: 0.95, why: { kind: "synonym", text: "deposits" } }] } }));
  expect(screen.getByRole("status")).toHaveTextContent("Using the portal catalogue");
  const help = screen.getByRole("button", { name: "Why these results matched" });
  expect(help).toHaveTextContent("?");
  expect(help).toHaveAttribute("aria-expanded", "false");
  fireEvent.mouseEnter(help);
  expect(help).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByRole("tooltip")).toHaveTextContent("synonym: deposits");
  fireEvent.mouseLeave(help);
  expect(help).toHaveAttribute("aria-expanded", "false");
  fireEvent.focus(help);
  const tooltip = screen.getByRole("tooltip");
  fireEvent.blur(help, { relatedTarget: tooltip });
  expect(help).toHaveAttribute("aria-expanded", "true");
  fireEvent.blur(help, { relatedTarget: document.body });
  expect(help).toHaveAttribute("aria-expanded", "false");
  act(() => publishKnowledge({ headers: {}, data: { notice: "", hits: [] } }));
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("button", { name: "Why these results matched" })).toBeNull();
});

it("discards knowledge results from requests made before a session change", () => {
  const epoch = knowledgeEpoch();
  render(<KnowledgeNotice />);
  act(() => {
    clearKnowledge();
    publishKnowledge({ data: { notice: "Previous session notice" } }, epoch);
  });
  expect(screen.queryByText("Previous session notice")).toBeNull();
});

it("loading the API client preserves the current page", async () => {
  window.history.replaceState({}, "", "/search?query=accounts");
  await import("../api/api");
  expect(window.location.pathname + window.location.search).toBe("/search?query=accounts");
});

it("shows name reasons and the restricted column flag", () => {
  render(<KnowledgeMatches />);
  act(() => publishKnowledge({ data: { hits: [{ kind: "column", unit: "retail", id: "retail.customer.email", title: "email", score: .8,
    restricted: true, why: { kind: "name", text: "retail.customer.email" } }] } }));
  fireEvent.focus(screen.getByRole("button", { name: "Why these results matched" }));
  const matches = screen.getByRole("tooltip");
  expect(matches).toHaveTextContent("name: retail.customer.email");
  expect(matches).toHaveTextContent("Restricted column");
  act(() => clearKnowledge());
});

it("shows a fallback explanation when a match has no why data", () => {
  render(<KnowledgeMatches />);
  act(() => publishKnowledge({ data: { hits: [{ kind: "term", unit: "retail", id: "balance", title: "Balance", score: .8 }] } }));
  fireEvent.focus(screen.getByRole("button", { name: "Why these results matched" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent("No additional reason provided.");
  act(() => clearKnowledge());
});

it("names and links the parent product for column and passage matches", () => {
  render(<KnowledgeMatches />);
  act(() => publishKnowledge({ data: { hits: [
    { kind: "column", unit: "wealth", id: "wealth.LGCY_ACCT_1.CR_INT_RATE_IN", title: "CR_INT_RATE_IN", score: .8,
      product: { id: "wealth.LGCY_ACCT_1", name: "Legacy Brokerage Accounts" }, why: { kind: "description", text: "Credit interest rate indicator" } },
    { kind: "document", unit: "retail", id: "DD-RETAIL", title: "Retail passage", score: .35,
      product: { id: "retail.DD-RETAIL", name: "Retail Banking Data Dictionary" }, why: { kind: "passage", text: "Delinquency" } },
  ] } }));
  fireEvent.focus(screen.getByRole("button", { name: "Why these results matched" }));
  expect(screen.getByRole("link", { name: "Legacy Brokerage Accounts (wealth.LGCY_ACCT_1)" })).toHaveAttribute("href", "/productDetails/wealth.LGCY_ACCT_1");
  expect(screen.getByRole("link", { name: "Retail Banking Data Dictionary (retail.DD-RETAIL)" })).toHaveAttribute("href", "/productDetails/retail.DD-RETAIL");
  expect(screen.getByRole("tooltip")).toHaveTextContent("column · wealth · 80%");
  expect(screen.getByRole("tooltip")).toHaveTextContent("passage: Delinquency");
  act(() => clearKnowledge());
});
