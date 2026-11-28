// #714 (review of #713): the page and the server read a multiple-question card's picks alike. The page's shownGroups
// (HitlCard.js) and the server's visibleGroups (server/src/cerebro/questions.js) are the same rule written twice: which
// groups the card shows for the picks so far, which options each offers then, and which picks still count. The server
// checks a Send against its own reading, so a page that read a card differently would offer a Send the server refuses,
// or hide a group the server waits for. This runs one pick matrix through both, on sample cards the server's own
// questionsCard builds, and holds the two to the same { groups, picks }.
import { describe, expect, test } from "vitest";
import { shownGroups } from "./HitlCard";
import { questionsCard, visibleGroups } from "../../../../../server/src/cerebro/questions.js";

const ORIGINAL = "What was the retail NNA by segment?";
const MEANING = { id: "meaning", label: "Meaning", kind: "meaning", spans: [{ start: 13, end: 23 }], options: [
  { id: "certified-nna", label: "Certified NNA", suggested: true },
  { id: "net-new-assets", label: "NNA (current)" },
  { id: "branch-sales-nna", label: "Branch-sales NNA" }] };
const SHOW = { id: "show", label: "Show", kind: "show", options: [{ id: "definitions", label: "Definitions" }, { id: "figures", label: "Figures" }, { id: "both", label: "Both" }] };
// The four-group cards take two meanings and two Show options, which keeps the matrix to a few hundred combinations.
const TWO_MEANINGS = { ...MEANING, options: [MEANING.options[0], MEANING.options[2]] };
const TWO_SHOWS = { ...SHOW, options: SHOW.options.slice(0, 2) };
const PERIOD = { id: "period", label: "Period", kind: "period", options: [{ id: "2025-06", label: "June 2025" }, { id: "2025-05", label: "May 2025" }] };
// "Branch region" is valid for branch-sales NNA only.
const SEGMENT = { id: "segment", label: "Segment", kind: "grouping", options: [
  { id: "client-segment", label: "Client segment" },
  { id: "branch-region", label: "Branch region", when: { group: "meaning", options: ["branch-sales-nna"] } }] };
// A group every option of which waits for an earlier pick: left with no option, and hidden, until that pick is made.
const CHANNEL = { id: "channel", label: "Channel", kind: "grouping", spans: [{ start: 27, end: 34 }], options: [
  { id: "branch", label: "Branch", when: { group: "meaning", options: ["branch-sales-nna"] } },
  { id: "desk", label: "Desk", when: { group: "meaning", options: ["branch-sales-nna", "net-new-assets"] } }] };
// A grouping option that waits for a Period pick, a group a Show pick of definitions hides.
const REGION = { id: "__proto__x", label: "Region", kind: "grouping", options: [
  { id: "all-regions", label: "All regions" },
  { id: "june-regions", label: "Regions open in June", when: { group: "period", options: ["2025-06"] } }] };

const CARDS = {
  "the Show group first": [SHOW, TWO_MEANINGS, SEGMENT],
  "the Show group last": [TWO_MEANINGS, PERIOD, SEGMENT, TWO_SHOWS],
  "a group left with no option, and no Show group": [MEANING, CHANNEL],
  "an option that waits for a Period pick, the Show group in the middle": [PERIOD, SHOW, REGION],
};
/** An id no card offers, and one group's option offered as another's pick. */
const NOT_ON_CARD = "not-on-the-card";

/** Every combination of picks for the card: each group with no pick, each option id on the card for it (offered then
 *  or not), and an id not on the card. */
function pickMatrix(card) {
  let rows = [{}];
  for (const group of card.groups) {
    const values = [undefined, ...group.options.map((o) => o.id), NOT_ON_CARD];
    rows = rows.flatMap((row) => values.map((value) => (value === undefined ? row : { ...row, [group.id]: value })));
  }
  return rows;
}

/** Picks no page sends but the server must read safely, and the page as the server does: a "__proto__" key of its own,
 *  a key for a group not on the card, inherited keys, values that are no id, and no picks object at all. */
function oddPicks(card) {
  const [first, second] = card.groups;
  const ownProto = (value, rest = {}) => Object.defineProperty({ ...rest }, "__proto__", { value, enumerable: true, configurable: true, writable: true });
  return [
    ownProto(first.options[0].id),
    ownProto({ [first.id]: first.options[0].id }),
    ownProto(first.options[0].id, { [first.id]: first.options[0].id, [second.id]: second.options[0].id }),
    JSON.parse(`{"__proto__":"${first.options[0].id}","${second.id}":"${second.options[0].id}"}`),
    Object.create({ [first.id]: first.options[0].id, [second.id]: second.options[0].id }),
    { "no-such-group": first.options[0].id, [first.id]: first.options[0].id },
    { [first.id]: second.options[0].id, [second.id]: first.options[0].id },
    { [first.id]: 7, [second.id]: null },
    { [first.id]: [first.options[0].id], [second.id]: { id: second.options[0].id } },
    { toString: first.options[0].id, constructor: first.options[0].id, hasOwnProperty: first.options[0].id },
  ];
}

describe("the page and the server read a multiple-question card's picks alike", () => {
  let combinations = 0;
  for (const [name, groups] of Object.entries(CARDS)) {
    test(`${name}: every combination of picks gives the same groups and the same picks that count`, () => {
      const card = questionsCard(groups, ORIGINAL);
      expect(card, "the sample groups hold as a card").toBeDefined();
      const matrix = [...pickMatrix(card), ...oddPicks(card)];
      combinations += matrix.length;
      for (const picks of matrix) {
        const page = shownGroups(card.groups, picks);
        const server = visibleGroups(card, picks);
        expect(page, `picks ${JSON.stringify(picks)}`).toEqual(server);
        expect(Object.keys(page.picks), `picks ${JSON.stringify(picks)}`).toEqual(Object.keys(server.picks));
      }
      expect(shownGroups(card.groups), "no picks given").toEqual(visibleGroups(card));
    });
  }

  test("a group whose id is \"__proto__\" is a key like another, on both sides", () => {
    const card = questionsCard([MEANING, { ...PERIOD, id: "__proto__" }, SHOW], ORIGINAL);
    expect(card).toBeDefined();
    const own = (value, rest = {}) => Object.defineProperty({ ...rest }, "__proto__", { value, enumerable: true, configurable: true, writable: true });
    for (const picks of [{}, own("2025-06"), own("2025-06", { meaning: "certified-nna", show: "figures" }), own("2025-06", { show: "definitions" }), own(NOT_ON_CARD, { show: "both" }), { meaning: "certified-nna" }]) {
      const page = shownGroups(card.groups, picks);
      const server = visibleGroups(card, picks);
      expect(page).toEqual(server);
      expect(Object.hasOwn(page.picks, "__proto__")).toBe(Object.hasOwn(server.picks, "__proto__"));
      expect(Object.getOwnPropertyDescriptor(page.picks, "__proto__")?.value).toBe(Object.getOwnPropertyDescriptor(server.picks, "__proto__")?.value);
    }
    expect(Object.getOwnPropertyDescriptor(shownGroups(card.groups, own("2025-06", { show: "figures" })).picks, "__proto__")?.value).toBe("2025-06");
  });

  test("the samples cover what the rule decides, and the matrix stays small", () => {
    const read = (name, picks) => visibleGroups(questionsCard(CARDS[name], ORIGINAL), picks);
    const ids = (result) => result.groups.map((g) => g.id);
    // A Show pick of definitions hides the Period and grouping groups, wherever the Show group sits.
    expect(ids(read("the Show group first", { show: "definitions" }))).toEqual(["show", "meaning"]);
    expect(ids(read("the Show group last", {}))).toEqual(["meaning", "period", "segment", "show"]);
    expect(ids(read("the Show group last", { show: "definitions", period: "2025-06" }))).toEqual(["meaning", "show"]);
    expect(read("the Show group last", { show: "definitions", period: "2025-06" }).picks).toEqual({ show: "definitions" });
    // An option with `when` is offered once its earlier group's pick is one it names.
    expect(read("the Show group first", { meaning: "branch-sales-nna" }).groups.at(-1).options.map((o) => o.id)).toEqual(["client-segment", "branch-region"]);
    expect(read("the Show group first", { meaning: "certified-nna", segment: "branch-region" }).picks).toEqual({ meaning: "certified-nna" });
    // A group left with no option is hidden.
    expect(ids(read("a group left with no option, and no Show group", {}))).toEqual(["meaning"]);
    expect(ids(read("a group left with no option, and no Show group", { meaning: "net-new-assets" }))).toEqual(["meaning", "channel"]);
    expect(combinations).toBeGreaterThan(100);
    expect(combinations).toBeLessThan(500);
  });
});
