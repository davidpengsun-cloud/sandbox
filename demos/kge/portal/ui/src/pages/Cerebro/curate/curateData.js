/* Curate's proposals (cerebro.md §6.2), grouped by the `kind` KGE gives each queue item: term tags
   (suggested_mention), new words (candidate_term), filter values, documents (document_tier and
   privacy), business terms and cross-references (xref_match). An item with no kind or one this page
   does not know is listed under Other, never dropped. */
export const KINDS = Object.freeze([
  { id: "term_tags", label: "Term tags", one: "Term tag" },
  { id: "new_words", label: "New words", one: "New word" },
  { id: "filter_values", label: "Filter values", one: "Filter value" },
  { id: "documents", label: "Documents", one: "Document" },
  { id: "business_terms", label: "Business terms", one: "Business term" },
  { id: "cross_references", label: "Cross-reference matches", one: "Cross-reference" },
]);
export const OTHER = Object.freeze({ id: "other", label: "Other", one: "Other" });
/* The filter that shows every kind at once, Curate's default. */
export const ALL = "all";

export const FAMILY_LABELS = Object.freeze({
  suggested_mention: "Suggested passage-to-term link",
  candidate_term: "Candidate term",
  filter_value: "Filter value",
  document_tier: "Document tier",
  privacy: "Personal data in a document",
  business_term: "Business term",
  xref_match: "Client cross-reference match",
});

const KNOWN = new Set(KINDS.map((kind) => kind.id));
export const kindOf = (row) => (KNOWN.has(row?.kind) ? row.kind : OTHER.id);

/** The proposals grouped by kind, in the kinds' order; each kind with its rows and the count still waiting. */
export function byKind(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const kinds = list.some((row) => kindOf(row) === OTHER.id) ? [...KINDS, OTHER] : KINDS;
  return kinds.map((kind) => {
    const mine = list.filter((row) => kindOf(row) === kind.id)
      .sort((a, b) => (a.upstream === "rejected") - (b.upstream === "rejected"));
    return { ...kind, rows: mine, waiting: mine.filter(waiting).length };
  });
}

/** The kind filter's chips: All, then each kind with a proposal, and the kind the steward picked even once it is
    empty, so deciding its last proposal says so in place. */
export function filterChips(kinds, shown) {
  return kinds.filter((kind) => kind.rows.length || kind.id === shown);
}

/** The proposals the filter shows, with each one's kind: every kind's in the kinds' order for All, else that kind's. */
export function shownRows(kinds, shown) {
  return kinds.filter((kind) => shown === ALL || kind.id === shown).flatMap((kind) => kind.rows.map((row) => ({ row, kind })));
}

/** A proposal still waits for a decision unless its upstream already rejected it. */
export const waiting = (row) => row?.upstream !== "rejected";

/** The graph node a proposal opens on: the node KGE names on the queue row, by the graph read's id rule (KGE's
    steward queue, node_of). The page builds no graph id of its own; a row KGE names no node for opens on nothing. */
export function nodeOf(row) {
  return typeof row?.node === "string" && row.node ? row.node : null;
}

/** The decide body: a candidate term's accept carries its definition. */
export function decideBody(row, decision, definition = "") {
  return { tag: row.tag, decision, ...(row.family === "candidate_term" && decision === "accept" ? { definition: definition.trim() } : {}) };
}

/** One line on what a decision did, from /steward/decide's reply. */
export function outcome(result, decision) {
  if (result?.status === "pending_second_check") return `Waiting on a second steward${result.reason ? ` · ${result.reason}` : ""}.`;
  const done = decision === "accept" ? "Accepted" : "Rejected";
  return [`${done}. It goes into the next release with your name.`, result?.term_created && `Term created: ${result.term_created}.`, result?.note]
    .filter(Boolean).join(" ");
}

/** A JSON call to the portal's Cerebro steward routes: the reply, or { error } with KGE's detail. */
export async function stewardCall(fetcher, path, body, signal) {
  try {
    const response = await fetcher(`/api/cerebro/steward/${path}`, {
      credentials: "same-origin", signal,
      ...(body ? { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    const value = await response.json().catch(() => null);
    if (!response.ok) return { error: (value && typeof value.detail === "string" && value.detail) || `The request failed (${response.status}).` };
    return value;
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    return { error: "The steward queue could not be reached. Try again shortly." };
  }
}

/** Share of the readable columns with a certified term: the column nodes that are the source of a certified
    `maps_to` link, over every column node the graph read returned (never the drawn graph, which folds columns
    into their tables). `null` when the read has no column. */
export function certifiedShare(graph) {
  const columns = new Set((graph?.nodes || []).filter((node) => node?.kind === "column").map((node) => node.id));
  if (!columns.size) return null;
  const certified = new Set((graph?.links || []).filter((link) => link?.kind === "maps_to" && link.status === "certified" && columns.has(link.source)).map((link) => link.source));
  return certified.size / columns.size;
}

const GATE_RESULT = { true: "Passed", false: "Failed" };

/** The candidate's check results, from KGE's onboarding status: the release gate, each set's result when the
    gate names them, the pre-release evaluation, and the sign-off. */
export function candidateChecks(ob) {
  const candidate = ob?.candidate;
  if (!candidate) return [];
  const gate = candidate.gate;
  const sets = gate && typeof gate.sets === "object" && gate.sets ? Object.entries(gate.sets) : [];
  return [
    { name: "Release gate", result: gate ? GATE_RESULT[Boolean(gate.passed)] : "Not checked" },
    ...sets.map(([name, result]) => ({ name, result: String(result) })),
    ...(ob.eval_pre ? [{ name: "Evaluation before release", result: GATE_RESULT[Boolean(ob.eval_pre.passed)] }] : []),
    { name: "Sign-off", result: candidate.signed_off ? `${candidate.signed_off.principal || "recorded"}${candidate.signed_off.at ? ` · ${candidate.signed_off.at}` : ""}` : "Not signed off" },
  ];
}

/** The tone of a check result's chip: passed is ok, failed is bad, a recorded sign-off is ok, anything else neutral. */
export function checkTone(check) {
  const result = String(check?.result ?? "").trim().toLowerCase();
  if (result === "passed" || result === "pass") return "ok";
  if (result === "failed" || result === "fail") return "bad";
  if (check?.name === "Sign-off") return result && result !== "not signed off" ? "ok" : "neutral";
  return "neutral";
}

/** What the candidate holds to review: each unit's drafted items and the open proposal's, with how many are
    accepted and certified, and the body /accept and /certify take for them. */
export function reviewGroups(ob) {
  const count = (items) => ({ items: items.length, accepted: items.filter((item) => item?.accepted_by).length, certified: items.filter((item) => item?.certified_by).length });
  const units = (ob?.units || []).filter((unit) => unit?.items?.length)
    .map((unit) => ({ id: `unit:${unit.unit}`, label: unit.label || unit.unit, body: { unit: unit.unit, items: "all" }, ...count(unit.items) }));
  const proposal = ob?.proposal?.items?.length ? [{ id: `proposal:${ob.proposal.id}`, label: "Improvement proposal", body: { proposal: ob.proposal.id, items: "all" }, ...count(ob.proposal.items) }] : [];
  return [...units, ...proposal];
}

/* A passage as plain text for a row: the document's Markdown marks (headings, emphasis, code ticks) taken out and its
   lines run together, so a quote reads as words. Only the text shown changes; a decision sends the row as KGE gave it. */
export function plainPassage(text) {
  if (typeof text !== "string") return "";
  return text
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/(\*\*|__|`)/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
