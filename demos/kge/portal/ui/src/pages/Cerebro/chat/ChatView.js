import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ChatThread, ConversationList, ToolCalls, ToolResult, getContentString } from "../../../components/agentchat";
import { Button } from "../../../components/shared/Controls";
import WaitingStatus from "../../../components/shared/WaitingStatus";
import Answer from "../../Context/Answer";
import BehindAnswer from "../../Context/BehindAnswer";
import { renderHighlightedText } from "../../Context/Ask";
import Result, { ownSpans, resolvedQuestion } from "../../Context/Result";
import { askSuggestions, stripAnswerLabel } from "../../Context/contextState";
import {
  answerOf, askedAs, callOf, citableSources, compactOf, contextOf, definitionsOf, followUpCount, forRole, framingOf, isEngineer,
  openAskIndex, pastQuestions, pendingAsks, richOf, turnCards, wordsBefore,
} from "./chatAnswer";
import { ChatProvider, useChatSession } from "./chatSession";
import MiniGraph from "./MiniGraph";
import "./chat.css";

/* Cerebro's Chat mode (cerebro.md §7.1; the owner's prototype, Chat mode): the conversation in the middle, on the
   agent's stream (§4.3), with the shared answer-settings picker in the input bar. An answer is a compact card (#613):
   its label, its figure, or its table or chart, and its citations, in readable words with no internal name; under
   "More", Context's own card (#607: Context/Answer.js, as Context's Ask renders it for the same role): the rich answer
   or figures, definitions and notes, a decline's way forward, ✓/✗ feedback and a clarification's choices. The plan, SQL and
   trace are the engineer's (§2.1), in Context's Behind the answer under the card; raw tool calls and results are an
   engineer's view too, since they can hold the plan and the SQL. While an ask is on its way the conversation shows
   Context's waiting status with the step KGE reports; the input offers Context's suggestions; each of the person's
   questions can be edited and asked again from that point.
   The conversation is the page's (chatSession.js), so a mode switch keeps it; the left rail holds the sessions and
   the last answer's mini-graph with "Open graph" (B7c) and folds; Graph mode floats the same conversation over the
   graph as the assistant, which hides to a button. */

const ChatContext = createContext({ engineer: false, me: { id: "" }, feedback: {}, sendFeedback: () => {}, cards: new Map(), progress: {} });

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

/** The person as Context's components read them: their id, their Cerebro roles (engineer opens the plan, SQL and
 *  trace) and the units they read, where the page knows them. */
export function meOf(state) {
  const roles = Array.isArray(state?.roles) ? state.roles : [];
  return { id: typeof state?.userId === "string" ? state.userId : "", cerebro_roles: roles, ...(Array.isArray(state?.reads) ? { reads: state.reads } : {}) };
}

// #602: a rich answer's headline drops the label the card's badge already shows.
function withoutHeadlineLabel(answer) {
  const display = richOf(answer);
  if (!display?.headline) return answer;
  return { ...answer, display: { ...display, headline: { ...display.headline, text: stripAnswerLabel(display.headline.text, answer.answer_mode) } } };
}

/** A compact table: its caption, its column labels, its rows, numbers right-aligned. */
function CompactTable({ table }) {
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

/** A compact bar chart, drawn as Context's rich answer draws its chart (Context/Answer.js, Rich). */
function CompactChart({ chart }) {
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
    {chart.of > chart.bars.length ? <p className="cerebro-compact-note">{chart.bars.length} of {chart.of} bars shown; More has every row.</p> : null}
    </>
  );
}

/** #613: the compact card, what the chat shows of an answer by default: its label, its figure, or its table or chart,
 *  and its citations (a clarification's choices, a definition's sentence). Every field is React text. */
export function CompactAnswer({ answer, onChoice }) {
  const c = useMemo(() => compactOf(answer), [answer]);
  const spans = c.kind === "clarification" ? ownSpans(answer) : [];
  return (
    <div className="cerebro-compact" data-kind={c.kind}>
      {c.badge ? (
        <div className="row">
          <span className={`badge ${c.badge.tone}`} data-answer-mode={c.badge.mode}>{c.badge.text}</span>
        </div>
      ) : null}
      {c.label ? <p className="cerebro-compact-label">{c.label}</p> : null}
      {c.figure ? (
        <div className="figrow"><span className="cur">{c.figure.currency}</span><div className="fig">{c.figure.value}</div></div>
      ) : null}
      {c.chart ? <CompactChart chart={c.chart} /> : c.table ? <CompactTable table={c.table} /> : null}
      {c.result ? <Result result={c.result} /> : null}
      {c.definitions?.length === 1 ? <p className="cerebro-compact-text">{c.definitions[0].text}</p> : null}
      {c.definitions?.length > 1 ? (
        <dl className="cerebro-compact-definitions">
          {c.definitions.map((d, i) => <div key={i}><dt>{d.term}</dt><dd>{d.text}</dd></div>)}
        </dl>
      ) : null}
      {c.text ? <p className="cerebro-compact-text">{c.text}</p> : null}
      {c.choices?.length ? (
        <div className="choices row" role="group" aria-label="Choose a meaning">
          {c.choices.map((choice, i) => (
            <Button key={i} type="button" data-choice={i} disabled={!onChoice || !spans.length} onClick={() => onChoice(choice.label)}>{choice.label}</Button>
          ))}
        </div>
      ) : null}
      {c.citations.length ? (
        <ul className="cerebro-compact-cites" aria-label="Citations">
          {c.citations.map((cite, i) => <li key={i}>{cite.n != null ? `[${cite.n}] ` : ""}{cite.text}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

/** One Ask reply in the chat: the compact card (#613) and, under More, the reply as Context's Ask shows it to the same
 *  role: Context's Answer card (notes, definitions, the complete text, ✓/✗). For an engineer, Context's Behind the
 *  answer (plan, SQL, trace) stays under the card. `asked` is the question KGE answered where it differs from the
 *  person's words; `onChoice` asks a clarification's choice. */
export function ChatAnswer({ id, answer: reply, asked, onChoice }) {
  const { engineer, me, feedback, sendFeedback } = useContext(ChatContext);
  const answer = useMemo(() => withoutHeadlineLabel(forRole(reply, engineer)), [reply, engineer]);
  const sent = feedback[id] || {};
  const [more, setMore] = useState(false);
  const [behind, setBehind] = useState(false);
  const [source, setSource] = useState(null);
  const [requestedTab, setRequestedTab] = useState(null);
  const [mapping, setMapping] = useState(undefined);
  const seq = useRef(0);
  const onCurrency = useCallback((_answer, currency) => setMapping(currency), []);
  const onCite = (n) => { setBehind(true); setSource(n || null); };
  const onOpenGraph = () => { setBehind(true); setSource(null); setRequestedTab({ tab: "graph", seq: (seq.current += 1) }); };
  // A reply KGE gave no behaviour (none does today) still shows its words.
  const bare = !answer.behaviour && !richOf(answer) && !answer.result && typeof answer.answer_text === "string" ? stripAnswerLabel(answer.answer_text, answer.answer_mode) : "";
  const fullId = `cerebro-more-${String(id ?? "").replace(/[^\w-]/g, "_")}`;
  return (
    <article className="cerebro-chat-card" aria-label="Answer">
      {asked ? <p className="cerebro-asked-as">Asked as: {asked}</p> : null}
      <CompactAnswer answer={answer} onChoice={onChoice} />
      <Button type="button" className="cerebro-chat-more" aria-expanded={more} aria-controls={more ? fullId : undefined} onClick={() => setMore((open) => !open)}>
        {more ? "Less" : "More"}
      </Button>
      {more ? (
        <div id={fullId} className="cerebro-chat-full">
          {bare ? <p className="cerebro-chat-text">{bare}</p> : null}
          <Answer
            answer={answer}
            me={me}
            onCite={engineer ? onCite : undefined}
            onOpenGraph={engineer ? onOpenGraph : undefined}
            mapping={mapping}
            onFeedback={(verdict, body) => sendFeedback(id, verdict, body)}
            feedback={sent.verdict}
            feedbackError={sent.error}
            onChoice={onChoice}
          />
        </div>
      ) : null}
      {engineer ? (
        <div className="cerebro-chat-behind">
          <Button type="button" className="cerebro-chat-behind-toggle" aria-expanded={behind} onClick={() => setBehind((open) => !open)}>
            Behind the answer
          </Button>
          {behind ? (
            <BehindAnswer api={contextRead} answer={answer} me={me} source={source} requestedTab={requestedTab} onCurrency={onCurrency} />
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

/** A get_context reply as a person may read it: each term's meaning and steward, and the citable sources. The package's
 *  own decisions (what it decided, needs or held back, the plan, the certified columns) are not shown. */
export function DefinitionCard({ pkg }) {
  const terms = definitionsOf(pkg);
  const sources = citableSources(pkg);
  if (!terms.length && !sources.length) return null;
  return (
    <article className="cerebro-chat-card cerebro-definitions" aria-label="Definitions">
      {terms.length ? (
        <dl>
          {terms.map((t, i) => (
            <div key={`${i}-${t.term}`} className="cerebro-definition">
              <dt>{t.term}</dt>
              <dd>
                <p>{t.meaning || "No definition is recorded."}</p>
                {t.steward ? <p className="cerebro-steward">Steward: {t.steward}</p> : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {sources.length ? (
        <section aria-label="Sources">
          <h3>Sources</h3>
          <ul>
            {sources.map((s) => (
              <li key={`${s.n}-${s.doc}`}>
                {s.n != null ? `[${s.n}] ` : ""}{s.title ? `${s.title} · ` : ""}<code>{s.doc}{s.version ? ` v${s.version}` : ""}</code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </article>
  );
}

/** One assistant or tool message, as this person may see it. `messages`, `index`, `busy` and `ask` are the thread's. */
export function CerebroMessage({ message, index = 0, messages = [], busy = false, loading = false, full = false, ask }) {
  const { engineer, cards, progress } = useContext(ChatContext);
  if (!message) return null;
  const answer = answerOf(message);
  if (answer) {
    const asked = askedAs(answer, callOf(messages, message), wordsBefore(messages, index));
    // As Context's Ask: only the latest reply's choices, with no question after it, and not while one runs.
    const open = !busy && !full && ask && openAskIndex(messages) === index;
    const onChoice = open ? (label) => { const text = resolvedQuestion(answer, label); if (text) ask(text); } : undefined;
    return <div className="agentchat-ai"><ChatAnswer id={message.id || message.tool_call_id} answer={answer} asked={asked} onChoice={onChoice} /></div>;
  }
  const pkg = contextOf(message);
  if (pkg) {
    return (
      <div className="agentchat-ai">
        <DefinitionCard pkg={pkg} />
        {engineer ? <ToolResult message={message} /> : null}
      </div>
    );
  }
  if (message.type === "tool") return engineer ? <div className="agentchat-ai"><ToolResult message={message} /></div> : null;
  // The card is the answer (#602): agent text that repeats a card of its turn is not shown again.
  const text = framingOf(getContentString(message.content), cards?.get?.(message.id) || []);
  const calls = engineer && Array.isArray(message.tool_calls) ? message.tool_calls : [];
  const pending = loading ? new Set(pendingAsks(messages)) : new Set();
  const waiting = Array.isArray(message.tool_calls) ? message.tool_calls.filter((c) => c?.id && pending.has(c.id)) : [];
  if (!text && !calls.length && !waiting.length) return null;
  return (
    <div className="agentchat-ai">
      {text ? <p className="agentchat-answer">{text}</p> : null}
      <ToolCalls toolCalls={calls} />
      {waiting.map((c) => (
        <WaitingStatus key={c.id} className="cerebro-ask-progress" data-testid="ask-progress" detail={progress?.[c.id]?.step || undefined} />
      ))}
    </div>
  );
}

/** One of the person's questions, with Edit: the question in a box to change and ask again from that point, the
 *  conversation before it alone threaded with it. Not offered while an answer is on its way. */
export function CerebroHuman({ message, index = 0, busy = false, askFrom }) {
  const words = getContentString(message?.content);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(words);
  // Cancel returns focus to the Edit button it came from; Ask again leaves it in the question box (the thread's).
  const editButton = useRef(null);
  const backToEdit = useRef(false);
  useEffect(() => {
    if (editing || !backToEdit.current) return;
    backToEdit.current = false;
    editButton.current?.focus();
  }, [editing]);
  if (editing && !busy && askFrom) {
    const submit = (e) => {
      e.preventDefault();
      const question = text.trim();
      if (!question) return;
      setEditing(false);
      askFrom(index, question);
    };
    return (
      <div className="agentchat-human">
        <form className="cerebro-edit v3-controls" aria-label="Edit your question" onSubmit={submit}>
          <input type="text" aria-label="Your question" value={text} onChange={(e) => setText(e.target.value)} autoFocus />
          <div className="cerebro-edit-bar">
            <Button type="button" onClick={() => { backToEdit.current = true; setEditing(false); }}>Cancel</Button>
            <Button type="submit" className="primary" disabled={!text.trim()}>Ask again</Button>
          </div>
        </form>
      </div>
    );
  }
  return (
    <div className="agentchat-human cerebro-human">
      <p className="agentchat-bubble">{words}</p>
      {busy || !askFrom ? null : (
        <Button type="button" ref={editButton} className="cerebro-edit-q" aria-label={`Edit “${words}”`} onClick={() => { setText(words); setEditing(true); }}>Edit</Button>
      )}
    </div>
  );
}

const MAX_FOLLOW_UPS = 8;
/** The follow-up line under the box, as Context's Ask says it: how many questions a follow-up builds on. */
export function FollowUp({ count }) {
  return (
    <p className="cerebro-followup" role="status" hidden={!count}>
      {count >= MAX_FOLLOW_UPS ? "This conversation has reached its eight follow-ups. Start a new chat to continue."
        : count ? `A follow-up builds on the last ${count === 1 ? "question" : `${count} questions`}.` : ""}
    </p>
  );
}

/** The question box's text for "Ask about this" (cerebro.md §7.3): the node from the address, filled in to the
 *  question; the person finishes it. A graph read id is `<kind>:<identifier>`. */
export function askAboutSeed(node) {
  if (typeof node !== "string" || !node) return "";
  const at = node.indexOf(":");
  return at > 0 ? `About the ${node.slice(0, at)} ${node.slice(at + 1)}: ` : `About ${node}: `;
}

/** How many golden questions the empty conversation shows before "Show all". */
export const TRY_FIRST = 5;

/** The empty conversation's "Try one of these" (as Discover's Ask has): the golden questions this person may ask, the
 *  first TRY_FIRST, and "Show all" for the rest. A click asks the question. */
export function TryThese({ questions, onAsk }) {
  const [all, setAll] = useState(false);
  const shown = all ? questions : questions.slice(0, TRY_FIRST);
  return (
    <section className="cerebro-try" aria-label="Suggested questions">
      <p className="cerebro-try-lbl">Try one of these</p>
      <ul>
        {shown.map((question) => (
          <li key={question}><Button type="button" className="cerebro-try-q" onClick={() => onAsk(question)}>{question}</Button></li>
        ))}
      </ul>
      {questions.length > TRY_FIRST ? (
        <Button type="button" className="cerebro-try-more" aria-expanded={all} onClick={() => setAll((open) => !open)}>
          {all ? "Show fewer" : `Show all ${questions.length}`}
        </Button>
      ) : null}
    </section>
  );
}

/** The conversation column on the page's chat: Chat mode's middle and Graph mode's dock show the same one. In Chat
 *  mode, `node` is the page's `asking`, set by "Ask about this" alone: it fills the question box, and the next question
 *  carries it (§4, the request: "the node the question is about when it comes from Ask about this"); `onAsked` clears it. */
const NONE = [];
function Conversation({ chat, node, onAsked }) {
  const engineer = isEngineer(chat.roles);
  const messages = chat.stream?.messages || NONE;
  const meKey = JSON.stringify(meOf(chat));
  const me = useMemo(() => JSON.parse(meKey), [meKey]);
  const cards = useMemo(() => turnCards(messages, stripAnswerLabel), [messages]);
  const value = useMemo(() => ({ engineer, me, feedback: chat.feedback, sendFeedback: chat.sendFeedback, cards, progress: chat.progress || {} }),
    [engineer, me, chat.feedback, chat.sendFeedback, cards, chat.progress]);
  const context = node ? { node } : undefined;
  // The golden list shows in the empty conversation, but not while "Ask about this" fills the question box.
  const questions = Array.isArray(chat.questions) ? chat.questions : NONE;
  const empty = !node && questions.length ? (ask) => <TryThese questions={questions} onAsk={ask} /> : undefined;
  // The input's suggestions, Context's own (contextState.askSuggestions): the golden questions (KGE has already left
  // out those the person may not read), their questions in this chat, and the glossary's terms where KGE gives them.
  const past = useMemo(() => pastQuestions(messages), [messages]);
  const glossary = Array.isArray(chat.glossary) ? chat.glossary : NONE;
  const suggest = useCallback(
    (text) => askSuggestions(text, { sets: [{ questions }], past, glossary, reads: me.reads }),
    [questions, past, glossary, me.reads],
  );
  // While an ask is on its way, the waiting status shows where its answer will be, not in the input bar too.
  const asking = Boolean(chat.stream?.isLoading && !chat.stream?.interrupt) && pendingAsks(messages).length > 0;
  const followUps = followUpCount(messages);
  return (
    <ChatContext.Provider value={value}>
      <ChatThread
        stream={chat.stream}
        health={chat.health}
        context={context}
        seed={askAboutSeed(node)}
        onAsked={node ? onAsked : undefined}
        empty={empty}
        Message={CerebroMessage}
        Human={CerebroHuman}
        suggest={suggest}
        highlight={renderHighlightedText}
        waiting={!asking}
        footer={<FollowUp count={followUps} />}
        full={followUps >= MAX_FOLLOW_UPS}
      />
    </ChatContext.Provider>
  );
}

/** Chat mode (the owner's prototype, Chat mode): a left rail of sessions that folds to a narrow strip, with New chat,
 *  the sessions and, at its foot, the small graph of the last answer; the conversation centred beside it, its ask box
 *  pinned to the foot. */
function ChatScreen({ state, actions }) {
  const chat = { ...useChatSession(), roles: state?.roles, userId: state?.userId, reads: state?.reads };
  const [railOpen, setRailOpen] = useState(true);
  return (
    <div className={`cerebro-chat cerebro-chat-live${railOpen ? "" : " rail-folded"}`}>
      <section className={`cerebro-rail${railOpen ? "" : " folded"}`} aria-labelledby="cerebro-sessions-title">
        <header className="cerebro-rail-head">
          <h2 id="cerebro-sessions-title">Sessions</h2>
          <Button
            type="button"
            className="cerebro-rail-fold"
            aria-expanded={railOpen}
            aria-controls="cerebro-sessions"
            aria-label={railOpen ? "Hide sessions" : "Show sessions"}
            onClick={() => setRailOpen((o) => !o)}
          >
            <span className="cerebro-rail-chevron" aria-hidden="true" />
          </Button>
        </header>
        <div id="cerebro-sessions" className="cerebro-rail-body" hidden={!railOpen}>
          <ConversationList conversations={chat.sessions} activeId={chat.activeId} onSelect={chat.select} onNew={chat.startNew} />
          {chat.busy ? <p className="cerebro-muted">Sessions change once this answer is in.</p> : null}
          <MiniGraph path={chat.path} onOpen={() => actions?.setMode?.("graph")} />
        </div>
      </section>
      <section className="cerebro-conversation" aria-label="Conversation">
        <Conversation chat={chat} node={state?.asking} onAsked={actions?.asked} />
      </section>
    </div>
  );
}

/** The Chat mode's view. It reads the page's chat; on its own (in tests) it holds one. `fetch`, `health`, `questions`,
 *  `glossary` and `sendFeedback` are for that case; the page passes them to the ChatProvider instead. */
export default function ChatView({ state, actions, fetch, health, questions, glossary, sendFeedback }) {
  const held = useChatSession();
  if (held) return <ChatScreen state={state} actions={actions} />;
  return (
    <ChatProvider actions={actions} fetch={fetch} health={health} questions={questions} glossary={glossary} sendFeedback={sendFeedback}>
      <ChatScreen state={state} actions={actions} />
    </ChatProvider>
  );
}

/** Graph mode's assistant (the owner's prototype, Graph mode): a card floating over the foot of the graph, never a
 *  column of its own, with the same conversation as Chat mode. Hide folds it to a small "Show assistant" button at the
 *  canvas's bottom right; the card stays mounted while hidden, so a question being typed is kept. */
export function ChatDock({ state }) {
  const held = useChatSession();
  const [open, setOpen] = useState(true);
  // The button pressed goes away, so focus moves to the one that takes its place.
  const hide = useRef(null);
  const show = useRef(null);
  const toggled = useRef(false);
  useEffect(() => {
    if (!toggled.current) return;
    (open ? hide : show).current?.focus();
  }, [open]);
  const toggle = (next) => { toggled.current = true; setOpen(next); };
  if (!held) return null;
  return (
    <aside className={`cerebro-dock${open ? "" : " folded"}`} aria-label="Assistant">
      <section id="cerebro-dock-card" className="cerebro-dock-card" aria-labelledby="cerebro-dock-title" hidden={!open}>
        <header className="cerebro-dock-head">
          <h2 id="cerebro-dock-title">Assistant</h2>
          <Button type="button" aria-expanded={open} aria-controls="cerebro-dock-card" aria-label="Hide assistant" ref={hide} onClick={() => toggle(false)}>Hide</Button>
        </header>
        <Conversation chat={{ ...held, roles: state?.roles, userId: state?.userId, reads: state?.reads }} />
      </section>
      {open ? null : (
        <Button type="button" className="cerebro-dock-show" aria-expanded={false} aria-controls="cerebro-dock-card" ref={show} onClick={() => toggle(true)}>Show assistant</Button>
      )}
    </aside>
  );
}
