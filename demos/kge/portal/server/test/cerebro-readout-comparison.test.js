import { test } from "node:test";
import assert from "node:assert/strict";
import { readoutOf, readoutMessage } from "../src/cerebro/status.js";

test("a comparison does not describe its single context as the whole result", () => {
  const readout = readoutOf("Compare current Q1 2026 NNA versus certified");
  assert.equal(readout.take({ event: "context", context: { label: "Certified NNA", time_mode: "certified" } }), undefined);
  assert.equal(readout.take({ event: "node_added", node: { id: "Words:period", type: "Words", name: "Q1 2026" } }), undefined);
  const period = readout.take({ event: "edge_added", edge: { from: "Words:period", to: "Calendar:calendar", type: "matches" } });
  assert.equal(period.period, "Q1 2026");
  assert.equal(period.measure, undefined);
  assert.equal(period.mode, undefined);
  const withSource = readout.take({ event: "node_added", node: { id: "Unit:wealth", type: "Unit", label: "Wealth Management", used: true } });
  assert.match(readoutMessage(withSource), /Q1 2026/);
  assert.match(readoutMessage(withSource), /Wealth Management/);
  assert.doesNotMatch(readoutMessage(withSource), /Certified NNA|certified basis/i);
});

test("a single measure still identifies its context and basis", () => {
  const readout = readoutOf("Current NNA for Q1 2026");
  const value = readout.take({ event: "context", context: { label: "Current NNA", time_mode: "current" } });
  assert.equal(value.measure, "Current NNA");
  assert.equal(value.mode, "Current");
  assert.match(readoutMessage(value), /This reads Current NNA/);
});
