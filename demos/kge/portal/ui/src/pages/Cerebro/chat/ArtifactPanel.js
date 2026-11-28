import React, { useCallback, useContext, useEffect, useId, useMemo, useRef, useState } from "react";
import { Button } from "../../../components/shared/Controls";
import Answer, { SqlPart } from "../../Context/Answer";
import BehindAnswer, { Sources } from "../../Context/BehindAnswer";
import Result from "../../Context/Result";
import { stripAnswerLabel } from "../../Context/contextState";
import { TAB_NAMES, answerTabs, artifactsOf, cardAnswer, cardTabs, csvName, keptRows, matrixOf, sortedRows, toCsv, toTsv, truncatedOf } from "./artifacts";
import { answerOf, answeredAs, callOf, compactOf, contextOf, richOf } from "./chatAnswer";
import { ChatContext } from "./chatContext";
import { exploreGraph, exploreOf, graphPageOf, searchOf, termMapOf } from "./chatTools";
import { DefinitionCard, ExploreCard, GraphPageCard, SearchCard, TermMapCard } from "./ToolCards";
import { ExplainedPaths } from "./PathCards";
import { savedSteps, stepLines } from "./working";

/* #626: the side panel, to the right of the chat, where a card opens whole while the chat stays usable beside it. It
   holds the answer in tabs: the full table (sortable, copyable, as a CSV file), the chart, the SQL (#624's part, headed
   "SQL: not certified (AI-Generated)" where it is), what "More" held (Context's own card, #607), the sources, the
   graph path and ✓/✗; for an engineer, Context's Behind the answer with the plan and trace. A business user's panel has
   the SQL and no plan or trace. Every other card opens with the content it already has (definitions, search results,
   the graph's nodes and small graph, a term map's parts), and one with explained paths (#638) opens on its Graph path
   tab, the whole path. A button makes the panel full-screen and Esc closes it; on a phone, and over the graph, it
   opens full-screen. The artifacts list beside the chat (ArtifactList) names every card of the conversation by its
   title and reopens it here; it is derived from the messages (artifacts.js), never stored.
   #651 (B6): the panel lists "Answers in this chat" as mini cards, newest first (only answer cards, B4); opening one
   shows it with "← All answers" back to the list. An answer's tabs no longer hold the graph path or ✓/✗: Like and
   Dislike sit under each reply in the chat (ChatView.js), and (B5) its turn's finished steps are its Steps tab. */

export const PANEL_ID = "cerebro-panel";

/** What Behind the answer reads beyond the answer: the resolved map of its term, from the Context routes as the
 *  person (KGE decides whether they may). Any other path, or a refusal, is an error the tab shows. */
const NOT_HERE = "The resolved map cannot be read here; Context's Ask shows it.";
export async function contextRead(path, { fetcher = globalThis.fetch } = {}) {
  if (typeof path !== "string" || !path.startsWith("/map/")) return { error: NOT_HERE };
  try {
    const response = await fetcher(`/api/context${path}`, { credentials: "same-origin" });
    if (!response.ok) return { error: NOT_HERE };
    const body = await response.json();
    return body && typeof body === "object" ? body : { error: NOT_HERE };
  } catch {
    return { error: NOT_HERE };
  }
}

/** A compact table: its caption, its column labels, its rows, numbers right-aligned. */
export function CompactTable({ table }) {
  return (
    <div className="scroll">
      <table className="cerebro-compact-table">
        {table.caption ? <caption>{table.caption}</caption> : null}
        <thead><tr>{table.columns.map((c, i) => <th key={i} className={table.numeric[i] ? "num" : undefined}>{c}</th>)}</tr></thead>
        <tbody>
          {table.rows.map((row, i) => (
            <tr key={i}>{row.map((cell, j) => <td key={j} className={table.numeric[j] ? "num" : undefined}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A bar chart, drawn as Context's rich answer draws its chart (Context/Answer.js, Rich). */
export function CompactChart({ chart }) {
  const lo = Math.min(0, ...chart.bars.map((r) => r.value)), hi = Math.max(0, ...chart.bars.map((r) => r.value)), span = hi - lo || 1;
  return (
    <>
    <div className="chart" role="figure" aria-label="Bar chart">
      {chart.bars.map((r, i) => (
        <div className="bar-row" key={i}>
          <span className="bl">{r.label}</span>
          <svg aria-hidden="true">
            <line className="zero" x1={`${(-lo / span) * 100}%`} x2={`${(-lo / span) * 100}%`} y1="0" y2="18" />
            <rect className="bar" x={`${((Math.min(0, r.value) - lo) / span) * 100}%`} y="2" width={`${Math.max(0.5, (Math.abs(r.value) / span) * 100)}%`} height="14" />
          </svg>
          <span className="bv num">{r.text}</span>
        </div>
      ))}
    </div>
    {chart.of > chart.bars.length ? <p className="cerebro-compact-note">{chart.bars.length} of {chart.of} bars shown; the Table tab has every row.</p> : null}
    </>
  );
}

/** Copy (tab-separated, for a spreadsheet) and Download CSV of a table, as it is shown. `matrix()` gives its cells. */
function TableTools({ matrix, title }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(toTsv(matrix()));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([`﻿${toCsv(matrix())}`], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = csvName(title);
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return (
    <div className="cerebro-table-tools" role="group" aria-label="Table actions">
      <Button type="button" onClick={copy} aria-label="Copy the table">{copied ? "Copied" : "Copy"}</Button>
      <Button type="button" onClick={download}>Download CSV</Button>
    </div>
  );
}

const ARROWS = { ascending: "▲", descending: "▼" };
/** The full table: every row and column, sortable by any column (a click sorts ascending, a second descending), a total
 *  row kept last; Copy and Download CSV take it as shown. */
export function FullTable({ table, title }) {
  const [sort, setSort] = useState({ by: null, direction: "ascending" });
  const rows = useMemo(() => sortedRows(table, sort.by, sort.direction), [table, sort]);
  const matrix = () => [table.columns, ...rows.map((r) => r.cells)];
  const toggle = (i) => setSort((s) => ({ by: i, direction: s.by === i && s.direction === "ascending" ? "descending" : "ascending" }));
  const last = table.total ? table.rows.length - 1 : -1;
  return (
    <div className="cerebro-full">
      <TableTools matrix={matrix} title={title} />
      <div className="scroll">
        <table className="cerebro-full-table">
          {table.caption ? <caption>{table.caption}</caption> : null}
          <thead>
            <tr>
              {table.columns.map((c, i) => (
                <th key={i} className={table.numeric[i] ? "num" : undefined} aria-sort={sort.by === i ? sort.direction : "none"}>
                  <Button type="button" className="cerebro-sort" onClick={() => toggle(i)}>
                    {c}<span aria-hidden="true" className="cerebro-sort-mark">{sort.by === i ? ARROWS[sort.direction] : ""}</span>
                  </Button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.i} data-total={row.i === last ? "true" : undefined}>
                {row.cells.map((cell, j) => <td key={j} className={table.numeric[j] ? "num" : undefined}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** A general result in full, as Context's Result draws it; Copy and Download CSV take its cells as drawn. */
function ResultTable({ result, title }) {
  const held = useRef(null);
  return (
    <div className="cerebro-full">
      <TableTools matrix={() => matrixOf(held.current)} title={title} />
      <div ref={held}><Result result={result} /></div>
    </div>
  );
}

/** The panel's tabs: a tab list, arrow keys moving between its tabs, and the selected tab's content alone. */
function PanelTabs({ tabs, tab, onTab, children }) {
  const base = useId().replace(/:/g, "");
  const keys = (event) => {
    const at = tabs.indexOf(tab);
    const next = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: tabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const name = tabs[(next + tabs.length) % tabs.length];
    onTab(name);
    event.currentTarget.querySelector(`#${base}-tab-${name}`)?.focus();
  };
  return (
    <>
      <div className="cerebro-panel-tabs" role="tablist" aria-label="The answer's parts" onKeyDown={keys}>
        {tabs.map((name) => (
          <Button key={name} type="button" role="tab" id={`${base}-tab-${name}`} aria-controls={`${base}-part-${name}`} aria-selected={name === tab}
            tabIndex={name === tab ? 0 : -1} onClick={() => onTab(name)}>{TAB_NAMES[name]}</Button>
        ))}
      </div>
      <div className="cerebro-panel-body cerebro-chat-card" role="tabpanel" id={`${base}-part-${tab}`} aria-labelledby={`${base}-tab-${tab}`} tabIndex={0} data-tab={tab}>
        {children}
      </div>
    </>
  );
}

/** #651 (B5): the finished steps of the turn a message is in: its working block's lines once it has ended, else the
 *  steps its reply record saved (after a reload). [] while the turn runs or where none are known. */
export function turnSteps(messages, message, blocks, replies) {
  const all = Array.isArray(messages) ? messages : [];
  const at = all.indexOf(message);
  const human = at < 0 ? undefined : all.slice(0, at).reverse().find((m) => m?.type === "human");
  const id = human?.id ? String(human.id) : "";
  if (!id) return [];
  const block = (blocks?.[id] || []).at(-1);
  if (block && block.state !== "running") return stepLines(block);
  if (block) return [];
  return savedSteps(replies?.[id]).map((text, i) => ({ key: `saved-${i}`, kind: "done", text: `✓ ${text}` }));
}

/** An answer in the panel: its badge above its tabs. */
function AnswerParts({ artifact, messages }) {
  const { engineer, me, blocks, replies } = useContext(ChatContext);
  const steps = turnSteps(messages, artifact.message, blocks, replies);
  // Read once per message, so Behind the answer's map (cached by the answer) is read once.
  const reply = useMemo(() => answerOf(artifact.message), [artifact.message]);
  const answer = useMemo(() => cardAnswer(reply, engineer), [reply, engineer]);
  const asked = answeredAs(reply, callOf(messages, artifact.message));
  const c = useMemo(() => compactOf(answer, asked), [answer, asked]);
  const tabs = answerTabs(answer, c, engineer, steps.length > 0);
  // #625: a reply the server kept only the first rows of says so over its table.
  const truncated = truncatedOf(artifact.message);
  const [tab, setTab] = useState(tabs[0]);
  const [source, setSource] = useState(null);
  const [mapping, setMapping] = useState(undefined);
  // #651: the tab Behind the answer is asked to show ({ tab, seq }, as Context's Ask asks it), or none.
  const [requested, setRequested] = useState(null);
  const onCurrency = useCallback((_answer, currency) => setMapping(currency), []);
  const shown = tabs.includes(tab) ? tab : tabs[0];
  // An engineer's citation opens the sources with it marked. #651: there is no Graph path tab; the activity line's
  // graph link opens Behind the answer on its own Graph tab, as Context's Ask does. A tab the person picks after
  // drops that request, so Behind the answer opens on its own first tab again.
  const onCite = (n) => { setSource(n || null); setRequested(null); setTab("sources"); };
  const onOpenGraph = () => { setRequested((r) => ({ tab: "graph", seq: (r?.seq || 0) + 1 })); setTab("behind"); };
  const onTab = (name) => { setRequested(null); setTab(name); };
  // A reply KGE gave no behaviour (none does today) still shows its words.
  const bare = !answer.behaviour && !richOf(answer) && !answer.result && typeof answer.answer_text === "string" ? stripAnswerLabel(answer.answer_text, answer.answer_mode) : "";
  return (
    <>
      {c.badge ? (
        <div className="cerebro-panel-meta">
          <span className={`badge ${c.badge.tone}`} data-answer-mode={c.badge.mode}>{c.badge.text}</span>
        </div>
      ) : null}
      <PanelTabs tabs={tabs} tab={shown} onTab={onTab}>
        {shown === "table" && truncated ? <p className="cerebro-compact-note cerebro-kept-rows">{keptRows(truncated.rows_kept, truncated.rows_total)}</p> : null}
        {shown === "table" ? (c.table ? <FullTable table={c.table} title={artifact.title} /> : <ResultTable result={c.result} title={artifact.title} />) : null}
        {shown === "chart" ? <CompactChart chart={c.chart} /> : null}
        {shown === "sql" ? <SqlPart answer={answer} /> : null}
        {shown === "answer" ? (
          <div className="cerebro-chat-full">
            {bare ? <p className="cerebro-chat-text">{bare}</p> : null}
            <Answer answer={answer} me={me} onCite={engineer ? onCite : undefined} onOpenGraph={engineer ? onOpenGraph : undefined} mapping={mapping}
              sqlShown={false} verdictsShown={false} choicesShown={false} />
          </div>
        ) : null}
        {shown === "sources" ? <Sources answer={answer} source={source} /> : null}
        {shown === "steps" ? (
          <ul className="cerebro-working-lines cerebro-steps">
            {steps.map((line) => <li key={line.key} data-kind={line.kind}>{line.text}</li>)}
          </ul>
        ) : null}
        {shown === "behind" ? (
          <div className="cerebro-chat-behind">
            <BehindAnswer api={contextRead} answer={answer} me={me} source={source} requestedTab={requested} onCurrency={onCurrency} />
          </div>
        ) : null}
      </PanelTabs>
    </>
  );
}

/** Any other card in the panel (#626): the content its card already has, in its own tab (a get_context reply's
 *  definitions, search's results, read_graph's and explore's nodes with their small graph, map_term's parts), drawn
 *  without its Open; and, where it carries explained paths (#638), the Graph path tab, the whole path, first. */
function CardParts({ artifact }) {
  const { engineer, openNode } = useContext(ChatContext);
  const message = artifact.message;
  const explore = artifact.kind === "explore" ? exploreOf(message, engineer) : null;
  const map = artifact.kind === "map" ? termMapOf(message, engineer) : null;
  const paths = explore?.paths || map?.paths || [];
  const tabs = cardTabs(artifact.kind, paths);
  const [tab, setTab] = useState(tabs[0]);
  const shown = tabs.includes(tab) ? tab : tabs[0];
  const page = artifact.kind === "graph" ? graphPageOf(message) : null;
  return (
    <PanelTabs tabs={tabs} tab={shown} onTab={setTab}>
      {shown === "graph" ? <ExplainedPaths paths={paths} /> : null}
      {shown === "definitions" ? <DefinitionCard pkg={contextOf(message)} /> : null}
      {shown === "results" ? <SearchCard hits={searchOf(message) || []} /> : null}
      {shown === "nodes" && explore ? <ExploreCard explore={{ ...explore, paths: undefined }} graph={exploreGraph(explore)} onOpen={openNode} /> : null}
      {shown === "nodes" && page ? <GraphPageCard page={page} onOpen={openNode} /> : null}
      {shown === "map" && map ? <TermMapCard map={{ ...map, paths: undefined }} /> : null}
    </PanelTabs>
  );
}

/** The side panel. `full`: it fills the view; `canFull`: it offers the button that makes it so (not on a phone, nor
 *  over the graph, where it always does). Focus moves to its title as a card opens in it. #651 (B6): with no card
 *  open it lists `answers` (ArtifactList), each opened with `onOpen(key)`; an open card offers "← All answers"
 *  (`onBack`) where the conversation has answers. */
export function ArtifactPanel({ artifact, answers = [], messages, full, canFull, onFull, onClose, onOpen, onBack }) {
  const title = useRef(null);
  const headId = useId().replace(/:/g, "");
  const key = artifact?.key ?? "";
  const listed = useRef(false);
  useEffect(() => {
    // The list's title takes focus only when the person comes back to it from a card.
    if (artifact || listed.current) title.current?.focus();
    listed.current = Boolean(artifact);
  }, [key, artifact]);
  if (!artifact) {
    return (
      <aside id={PANEL_ID} className={`cerebro-panel cerebro-answers${full ? " full" : ""}`} aria-labelledby={headId} data-kind="answers">
        <header className="cerebro-panel-head">
          <h2 id={headId} ref={title} tabIndex={-1}>Answers in this chat</h2>
          <div className="cerebro-panel-actions">
            <Button type="button" aria-label="Close the panel" onClick={onClose}>Close</Button>
          </div>
        </header>
        <ArtifactList artifacts={answers} onOpen={onOpen} />
      </aside>
    );
  }
  return (
    <aside id={PANEL_ID} className={`cerebro-panel${full ? " full" : ""}`} aria-labelledby={headId} data-kind={artifact.kind}>
      {onBack && answers.length ? <Button type="button" className="cerebro-panel-back" onClick={onBack}>← All answers</Button> : null}
      <header className="cerebro-panel-head">
        <h2 id={headId} ref={title} tabIndex={-1}>{artifact.title}</h2>
        <div className="cerebro-panel-actions">
          {canFull ? <Button type="button" aria-pressed={full} onClick={onFull}>{full ? "Exit full screen" : "Full screen"}</Button> : null}
          <Button type="button" aria-label="Close the panel" onClick={onClose}>Close</Button>
        </div>
      </header>
      {artifact.kind === "answer" ? <AnswerParts key={artifact.key} artifact={artifact} messages={messages} /> : <CardParts key={artifact.key} artifact={artifact} />}
    </aside>
  );
}

/** One answer's mini card in the panel's list (#651, B6): its title, its badge and its figure, as React text. The
 *  button's name is all of it (no aria-label hides the badge and the figure). */
function MiniAnswer({ artifact }) {
  const { engineer } = useContext(ChatContext);
  const c = useMemo(() => compactOf(cardAnswer(answerOf(artifact.message), engineer), artifact.title), [artifact, engineer]);
  return (
    <>
      <span className="cerebro-mini-answer-title">{artifact.title}</span>
      {c.badge?.text ? <span className="cerebro-mini-answer-meta">{c.badge.text}</span> : null}
      {c.figure ? <span className="cerebro-mini-answer-meta num">{c.figure.currency} {c.figure.value}</span> : null}
    </>
  );
}

/** #651 (B6): "Answers in this chat" in the side panel: the conversation's answer cards as mini cards, newest first
 *  (the caller orders them); a click opens one. A line says so while there are none. */
export function ArtifactList({ artifacts, onOpen }) {
  if (!artifacts?.length) return <p className="cerebro-muted">No answers in this chat yet.</p>;
  return (
    <ul className="cerebro-artifacts" aria-label="Answers in this chat">
      {artifacts.map((a) => (
        <li key={a.key}>
          <Button type="button" className="cerebro-artifact" onClick={() => onOpen(a.key)}><MiniAnswer artifact={a} /></Button>
        </li>
      ))}
    </ul>
  );
}

const NARROW = "(max-width: 759.98px)";
const narrowNow = () => (typeof window === "undefined" ? false
  : typeof window.matchMedia === "function" ? window.matchMedia(NARROW).matches : window.innerWidth < 760);
/** True below 760 px of width, where the panel opens full-screen. */
export function useNarrow() {
  const [narrow, setNarrow] = useState(narrowNow);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const query = window.matchMedia(NARROW);
    const changed = () => setNarrow(query.matches);
    query.addEventListener?.("change", changed);
    return () => query.removeEventListener?.("change", changed);
  }, []);
  return narrow;
}

/**
 * The panel's state for a conversation: the cards (derived from its messages, never stored), the one open and whether it
 * is full-screen. `always`: it opens full-screen (over the graph). Esc closes it (#651: while a card is open in it or
 * focus is inside it), and focus goes back to what opened it.
 * A card that leaves the conversation (another session chosen, a question edited) closes it.
 */
export function usePanel(messages, engineer, { always = false } = {}) {
  const narrow = useNarrow();
  const [state, setState] = useState(null);
  const artifacts = useMemo(() => artifactsOf(messages, engineer), [messages, engineer]);
  // #651 (B6): the answers, newest first, and whether their list shows: beside a wide chat it does unless closed; on
  // a narrow screen, and over the graph, only once asked for ("Answers (n)").
  const answers = useMemo(() => artifacts.filter((a) => a.kind === "answer").reverse(), [artifacts]);
  const [list, setList] = useState(null);
  const listShown = answers.length > 0 && (list ?? (!narrow && !always));
  const artifact = state ? artifacts.find((a) => a.key === state.key) || null : null;
  const opener = useRef(null);
  const isOpen = useRef(false);
  const open = useCallback((key) => {
    if (!isOpen.current) opener.current = typeof document === "undefined" ? null : document.activeElement;
    isOpen.current = true;
    setState((s) => ({ key, full: s?.full ?? false }));
  }, []);
  const close = useCallback(() => {
    isOpen.current = false;
    setState(null);
    setList(false);
    const back = opener.current;
    opener.current = null;
    if (back?.isConnected) back.focus();
  }, []);
  const toggleFull = useCallback(() => setState((s) => (s ? { ...s, full: !s.full } : s)), []);
  // "← All answers": the open card gives way to the list. "Answers (n)": the list opens.
  const back = useCallback(() => { setState((s) => (s ? null : s)); setList(true); }, []);
  const openList = useCallback(() => {
    opener.current = typeof document === "undefined" ? null : document.activeElement;
    isOpen.current = true;
    setList(true);
  }, []);
  useEffect(() => {
    if (state && !artifact) { isOpen.current = false; setState(null); }
  }, [state, artifact]);
  const shown = Boolean(artifact || listShown);
  // #651: Esc closes the panel while a card is open in it, or while focus is inside it; with the answers list alone
  // beside the chat, Esc pressed elsewhere (the question box, a menu) leaves it be.
  const cardOpen = Boolean(artifact);
  useEffect(() => {
    if (!shown) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      const inside = typeof document !== "undefined" && Boolean(document.activeElement?.closest?.(`#${PANEL_ID}`));
      if (cardOpen || inside) close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [shown, cardOpen, close]);
  return {
    artifacts, answers, artifact, shown, opened: artifact?.key || "", full: Boolean(state?.full || narrow || always), canFull: Boolean(artifact) && !narrow && !always,
    open, close, toggleFull, back, openList,
  };
}
