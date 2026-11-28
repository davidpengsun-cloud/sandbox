import React from "react";
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { ReplyText, replyText } from "./ChatView";
import { DefinitionCard } from "./ToolCards";

afterEach(cleanup);

const documentAnswer = {
  intro: "The available sources contain these excerpts; they may not fully answer your question.",
  excerpts: [{ doc: "STD-DOCUMENT-READING", title: "Document Reading Standard", text: "The document reading standard preserves source words, tables and page order." }],
  incomplete: true,
};

test("a citation-only answer shows an honest introduction, sources, and attributed evidence in order", () => {
  const answer = { behaviour: "cite_certified", answer_text: "Cited: STD-DOCUMENT-READING, MD-CUSTOMER-ATTRITION.", cite: ["STD-DOCUMENT-READING", "MD-CUSTOMER-ATTRITION"], document_answer: documentAnswer };
  const { container } = render(<ReplyText answer={answer} />);
  const intro = screen.getByText(documentAnswer.intro);
  const sources = screen.getByRole("region", { name: "Sources" });
  const excerpts = screen.getByRole("region", { name: "Summary from source excerpts" });
  expect(intro.compareDocumentPosition(sources) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(sources.compareDocumentPosition(excerpts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(excerpts).getByText(documentAnswer.excerpts[0].text)).toBeInTheDocument();
  expect(sources).not.toHaveTextContent("MD-CUSTOMER-ATTRITION");
  expect(container.querySelector(".cerebro-compact-definitions")).toBeNull();
  const copied = replyText([{ type: "human", content: "Question" }, { type: "tool", name: "ask", content: JSON.stringify(answer) }], 0, 1, {});
  expect(copied).toContain(documentAnswer.intro);
  expect(copied).toContain(documentAnswer.excerpts[0].text);
  expect(copied).toContain("Summary from source excerpts:");
  expect(copied).toContain("STD-DOCUMENT-READING");
  expect(copied).not.toContain("MD-CUSTOMER-ATTRITION");
});

test("a context card shows only approved excerpts, and renders document text as text", () => {
  render(<DefinitionCard pkg={{ block: "", sources: [
    { n: 1, doc: "STD-DOCUMENT-READING", title: "Document Reading Standard", citable: true, excerpt: "internal-only" },
    { n: 2, doc: "PRIVATE", citable: false, excerpt: "private-only" },
  ], citable_passages: [{ text: "raw-only" }], document_answer: { ...documentAnswer, excerpts: [{ doc: "STD-DOCUMENT-READING", text: "<script>safe text</script>" }] } }} />);
  const excerpts = screen.getByRole("region", { name: "Summary from source excerpts" });
  expect(excerpts).toHaveTextContent("<script>safe text</script>");
  expect(excerpts.querySelector("script")).toBeNull();
  expect(screen.queryByText(/internal-only|private-only|raw-only|PRIVATE/)).toBeNull();
});

test("no supporting excerpts means no source card or unsupported citation in copied text", () => {
  const empty = { intro: "I could not find enough accessible evidence to answer this question.", excerpts: [], incomplete: true, summary: "Unsupported summary", summary_kind: "model" };
  const pkg = { block: "", sources: [{ doc: "IRRELEVANT", citable: true }], document_answer: empty };
  render(<DefinitionCard pkg={pkg} />);
  expect(screen.getByText(empty.intro)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Sources" })).toBeNull();
  expect(screen.queryByText("Unsupported summary")).toBeNull();
  const copied = replyText([{ type: "human", content: "Q" }, { type: "tool", name: "get_context", content: JSON.stringify(pkg) }], 0, 1, {});
  expect(copied).toBe(empty.intro);
});

test("a definition with a source makes its citation visible", () => {
  render(<ReplyText answer={{ behaviour: "cite_certified", certified_definitions: [{ term: "NNA", definition: "Money in less money out." }], cite: ["MD-NNA"] }} />);
  expect(screen.getByText("Money in less money out.")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Sources" })).toHaveTextContent("MD-NNA");
});
