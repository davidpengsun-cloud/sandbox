import React from "react";
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CerebroMessage, replyText } from "./ChatView";

afterEach(cleanup);

test("a figure remains visible before cited source evidence, and copy retains its answer", () => {
  const answer = { behaviour: "figure", answer_mode: "certified", figure: 9947562.78,
    answer_text: "Certified: NNA was USD 9,947,562.78.", sql: "SELECT checked_value FROM checked_source",
    document_answer: { intro: "Selected source text: Money clients bring to the firm less money they take away.",
      excerpts: [{ doc: "MD-NET-NEW-ASSETS", title: "Metric Definition: Net New Assets", attribution: "Wealth Data Steward",
        text: "Money clients bring to the firm less money they take away." }], incomplete: true } };
  const message = { type: "tool", name: "ask", id: "figure", card: true, content: JSON.stringify(answer) };
  render(<CerebroMessage message={message} messages={[message]} />);
  const card = screen.getByRole("article", { name: "Answer" });
  const evidence = screen.getByRole("region", { name: "Summary from source excerpts" });
  expect(card.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(evidence).toHaveTextContent("Wealth Data Steward");
  const copied = replyText([{ type: "human", content: "NNA for June 2025" }, message], 0, 1, {});
  expect(copied).toContain("9,947,562.78");
  expect(copied).toContain("MD-NET-NEW-ASSETS");
  expect(copied).toContain("Wealth Data Steward");
});
