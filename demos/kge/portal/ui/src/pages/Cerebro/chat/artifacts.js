/* #626: the cards of a conversation as the side panel and the artifacts list read them. Nothing is stored: the list is
   an index into the conversation's messages, derived from them each time they change (artifactsOf), and the panel
   reads the card it opens from the same messages. A card is every reply the chat draws as a card for this role: an Ask
   answer (but a clarification asked back on the HITL card, which is that question's card), a get_context reply's
   definitions, and a search, read_graph, explore, explore_question or map_term reply (#622; with explained paths,
   #638). Each is named by what the person asked (its call's question, query or term, as answeredAs names an answer),
   else by its card's own label. Plain data only; ChatView.js and ArtifactPanel.js draw it.
   #651 (B4): an Ask reply is an answer card only where the server marks its tool message `card: true` (its checked SQL
   returned rows); any other Ask reply is a message only and no card. (B6) The panel's tabs have no Graph path tab for
   an answer, and a Steps tab where the turn's finished steps are known. */

import { sqlOf } from "../../Context/Answer";
import { stripAnswerLabel } from "../../Context/contextState";
import { answerOf, answeredAs, askedBack, callOf, citableSources, compactOf, contextOf, definitionsOf, forRole, richOf, titleOf } from "./chatAnswer";
import { exploreOf, graphPageOf, searchOf, termMapOf } from "./chatTools";

/** How many rows of a table the compact card shows before "Open". */
export const CARD_ROWS = 3;

/** #651 (B4): whether an Ask reply has figures from the warehouse: a figure, a table, a chart, a general result or rows. */
export const hasFigures = (reply) => Boolean(reply && typeof reply === "object" && (typeof reply.figure === "number" || reply.display?.table
  || reply.display?.chart || reply.result || (reply.rows && reply.behaviour !== "clarify")));
/** #651 (B4): whether an Ask reply is an answer card: its tool message says `card: true`; `card: false` says it is not.
 *  A message saved before the server marked it (no `card` field) is a card where its reply has figures, so earlier
 *  conversations keep their cards. */
export function isCard(message) {
  if (message?.type !== "tool") return false;
  if (typeof message.card === "boolean") return message.card;
  return hasFigures(answerOf(message));
}

/** A message's key: the id the panel and the list name its card by. */
export const keyOf = (message) => String(message?.id || message?.tool_call_id || "");

// A general result's columns as Context's Result draws them: its dimensions, its measures that do not repeat, and, for
// a ranked result, its end and rank.
const resultColumns = (result) => result.columns.filter((c) => c.kind === "dimension" || !c.repeats).length
  + (result.rows?.some((row) => row.end) ? 2 : 0);

/** #625: the server's mark on a stored reply it kept only the first rows of (a tool result over 1 MB): { rows_kept,
 *  rows_total } on the tool message, or in its additional_kwargs, or in its JSON content; null where it has none. */
export function truncatedOf(message) {
  const whole = (n) => Number.isInteger(n) && n >= 0;
  const read = (mark) => (mark && typeof mark === "object" && whole(mark.rows_kept) && whole(mark.rows_total) && mark.rows_kept < mark.rows_total
    ? { rows_kept: mark.rows_kept, rows_total: mark.rows_total } : null);
  if (!message || message.type !== "tool") return null;
  const own = read(message.truncated) || read(message.additional_kwargs?.truncated);
  if (own) return own;
  const content = typeof message.content === "string" ? message.content : "";
  if (!content.includes("truncated")) return null;
  try {
    return read(JSON.parse(content)?.truncated);
  } catch {
    return null;
  }
}

/** The size of an answer's table: { rows, columns }, or null where it has none. A total row is not counted. Where the
 *  server kept only the first rows (`truncated`, #625), the size names both: { rows, columns, of }. */
export function sizeOf(c, truncated = null) {
  const size = c?.table ? { rows: c.table.keys ? c.table.keys.length : c.table.rows.length, columns: c.table.columns.length }
    : c?.result ? { rows: (c.result.rows || []).filter((row) => !row.total).length, columns: resultColumns(c.result) }
      : null;
  return size && truncated ? { ...size, rows: truncated.rows_kept, of: truncated.rows_total } : size;
}

const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
const count = (n) => n.toLocaleString("en-US");
/** "First N of M rows" (#625): the rows the server kept of a reply's table, and how many it had. */
export const keptRows = (kept, total) => `First ${count(kept)} of ${count(total)} rows`;
/** "Open · 12 rows × 5 columns" ("Open · First 100 of 5,000 rows × 5 columns" where only the first rows were kept),
 *  or "Open" where the card has no table. */
export function openLabel(size) {
  if (!size) return "Open";
  const rows = Number.isInteger(size.of) ? keptRows(size.rows, size.of) : plural(size.rows, "row");
  return `Open · ${rows} × ${plural(size.columns, "column")}`;
}

/** The compact card's first rows: the table cut to CARD_ROWS rows, or the result cut to as many. */
export function firstRows(c) {
  if (c?.table) return { table: { ...c.table, rows: c.table.rows.slice(0, CARD_ROWS) } };
  if (c?.result) return { result: { ...c.result, rows: (c.result.rows || []).slice(0, CARD_ROWS) } };
  return {};
}

/** The answer as this person's card reads it: the engineer's fields left out for anyone else (chatAnswer.forRole), and
 *  (#602) a rich answer's headline without the label the card's badge already shows. */
export function cardAnswer(reply, engineer) {
  const answer = forRole(reply, engineer);
  const display = richOf(answer);
  if (!display?.headline) return answer;
  return { ...answer, display: { ...display, headline: { ...display.headline, text: stripAnswerLabel(display.headline.text, answer.answer_mode) } } };
}

/** What the person asked a tool, as its call names it: the title of its question, query or term (titleOf, as answeredAs
 *  names an answer); "" where the call names none in words (a node's id is no words). */
function askedOf(call) {
  const args = call?.args && typeof call.args === "object" ? call.args : {};
  for (const field of ["question", "query", "term"]) {
    const title = typeof args[field] === "string" ? titleOf(args[field]) : "";
    if (title) return title;
  }
  return "";
}

/** The card a message draws for this role, as CerebroMessage draws it: { kind, label }, or null where it draws none.
 *  kind is definitions, explore, graph, search or map; label is the card's own name for it. */
export function toolCardOf(message, engineer = false) {
  const pkg = contextOf(message);
  if (pkg) {
    const terms = definitionsOf(pkg);
    return terms.length || citableSources(pkg).length ? { kind: "definitions", label: terms[0]?.term || "Definitions" } : null;
  }
  if (message?.type !== "tool") return null;
  const explore = exploreOf(message, engineer);
  if (explore) return { kind: "explore", label: explore.node?.name || explore.paths?.[0]?.term || "In the knowledge graph" };
  if (graphPageOf(message)) return { kind: "graph", label: "In the knowledge graph" };
  if (searchOf(message)) return { kind: "search", label: "Search results" };
  const map = termMapOf(message, engineer);
  if (map) return { kind: "map", label: map.term || map.paths?.[0]?.term || "Term map" };
  return null;
}

/**
 * Every card of the conversation, in order: [{ key, kind, title, message }], kind "answer", "definitions", "explore",
 * "graph", "search" or "map". An answer is named by the question it answered (#619), else its card's label; any other
 * card by what its call asked, else its card's label.
 */
export function artifactsOf(messages, engineer = false) {
  const all = Array.isArray(messages) ? messages : [];
  const out = [];
  for (const message of all) {
    const key = keyOf(message);
    if (!key) continue;
    const answer = answerOf(message);
    if (answer) {
      if (!isCard(message) || (answer.behaviour === "clarify" && askedBack(all, message))) continue;
      const title = answeredAs(answer, callOf(all, message));
      out.push({ key, kind: "answer", title: title || compactOf(cardAnswer(answer, engineer), title).label || "Answer", message });
      continue;
    }
    const card = toolCardOf(message, engineer);
    if (card) out.push({ key, kind: card.kind, title: askedOf(callOf(all, message)) || card.label, message });
  }
  return out;
}

/**
 * The panel's tabs for an answer, in order: Table, Chart, SQL where it has them, then the full answer (what "More"
 * held) and its sources, its turn's Steps where `steps` (#651), and, for an engineer, Behind the answer (the plan and
 * trace). The first is Table if there is a table, else Chart, else SQL, else the full answer. #651 (B6): no Graph path
 * tab, for any role.
 */
export function answerTabs(answer, c, engineer, steps = false) {
  return [
    ...(c?.table || c?.result ? ["table"] : []),
    ...(c?.chart ? ["chart"] : []),
    ...(sqlOf(answer).length ? ["sql"] : []),
    "answer", "sources",
    ...(steps ? ["steps"] : []),
    ...(engineer ? ["behind"] : []),
  ];
}

export const TAB_NAMES = {
  table: "Table", chart: "Chart", sql: "SQL", answer: "Full answer", sources: "Sources", graph: "Graph path", steps: "Steps", behind: "Behind the answer",
  definitions: "Definitions", results: "Results", nodes: "Graph", map: "Term map",
};

/** The panel's tabs for any other card: its card's own content in one tab, and, where it carries explained paths
 *  (#638), the Graph path tab, first, as such a card opens on it. */
export function cardTabs(kind, paths) {
  const own = { definitions: "definitions", search: "results", graph: "nodes", explore: "nodes", map: "map" }[kind];
  return [...(paths?.length ? ["graph"] : []), ...(own ? [own] : [])];
}

/** A table's rows in the order asked: by column `by`, ascending or descending, on KGE's own value where the table has
 *  it, else on the cell's text (a number read from it in a numeric column). A total row stays last. */
export function sortedRows(table, by, direction) {
  const rows = table.rows.map((cells, i) => ({ cells, key: table.keys?.[i], i }));
  const total = table.total ? rows.pop() : null;
  if (by == null) return total ? [...rows, total] : rows;
  const valueOf = (row) => {
    const raw = row.key?.[by];
    if (typeof raw === "number") return raw;
    if (table.numeric[by]) {
      const n = Number(String(row.cells[by] ?? "").replace(/[^\d.-]/g, ""));
      return String(row.cells[by] ?? "").match(/\d/) && Number.isFinite(n) ? n : null;
    }
    return raw != null ? String(raw) : String(row.cells[by] ?? "");
  };
  const sign = direction === "descending" ? -1 : 1;
  rows.sort((a, b) => {
    const x = valueOf(a), y = valueOf(b);
    if (x == null || y == null) return x == null && y == null ? a.i - b.i : x == null ? 1 : -1;
    const order = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
    return order * sign || a.i - b.i;
  });
  return total ? [...rows, total] : rows;
}

const csvCell = (cell) => {
  const text = String(cell ?? "");
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};
/** Rows of cells as CSV (RFC 4180: a cell with a comma, quote or line break quoted). */
export const toCsv = (matrix) => matrix.map((row) => row.map(csvCell).join(",")).join("\r\n");
/** Rows of cells as tab-separated text, for the clipboard (a spreadsheet pastes it as cells). */
export const toTsv = (matrix) => matrix.map((row) => row.map((cell) => String(cell ?? "").replace(/[\t\n\r]+/g, " ")).join("\t")).join("\n");

/** The cells of the tables inside an element, header rows included, as drawn: what copy and CSV take of a table the page
 *  did not build itself (Context's Result). */
export function matrixOf(element) {
  return [...(element?.querySelectorAll("table tr") || [])].map((tr) => [...tr.children].map((cell) => cell.textContent.trim()));
}

/** A file name for an answer's CSV: its title in lower-case words joined by hyphens. */
export const csvName = (title) => `${String(title || "answer").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "") || "answer"}.csv`;
