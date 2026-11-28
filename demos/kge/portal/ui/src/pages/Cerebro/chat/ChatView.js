import React, { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { ChatThread, ConversationList, ToolCalls, ToolResult, getContentString } from "../../../components/agentchat";
import { Button } from "../../../components/shared/Controls";
import { renderHighlightedText } from "../../Context/Ask";
import Result from "../../Context/Result";
import { askSuggestions, feedbackBody, stripAnswerLabel } from "../../Context/contextState";
import { ArtifactPanel, CompactTable, PANEL_ID, usePanel } from "./ArtifactPanel";
import { PendingRunCard, RunEndingCard, SuggestCard, runOf } from "./BackgroundRun";
import { cardAnswer, firstRows, isCard, keyOf, openLabel, sizeOf, truncatedOf } from "./artifacts";
import {
  CHOOSE_TOOL, answerOf, answeredAs, askedBack, callOf, chooseCalls, chooseOf, chosenOf, chosenText, citableSources,
  compactOf, contextOf, definitionsOf, framingOf, isEngineer, isFrameCall, monthWords, pastQuestions, picksOf, turnCards,
} from "./chatAnswer";
import { ChatContext } from "./chatContext";
import { HitlCard } from "./HitlCard";
import { exploreGraph, exploreOf, graphPageOf, searchOf, termMapOf, toolCardTexts } from "./chatTools";
import { ChatProvider, TRIMMED_NOTE, useChatSession } from "./chatSession";
import { DefinitionCard, DocumentExcerpts, DocumentIntro, ExploreCard, GraphPageCard, SearchCard, TermMapCard, documentExcerptItems, documentIntroText } from "./ToolCards";

export { DefinitionCard } from "./ToolCards";
import { WorkingBlock } from "./WorkingBlock";
import { blockOfReply, tookText } from "./working";
import "./chat.css";

/* Cerebro's Chat mode (cerebro.md §7.1; the owner's prototype, Chat mode): the conversation in the middle, on the
   agent's stream (§4.3), with the shared answer-settings picker in the input bar. #626: the chat shows messages and
   compact cards only (#613): a card's title (#619), its label, its figure or its table's first rows, in readable words
   with no internal name, and "Open" with the table's size. Open shows the card whole in the side panel to the right of
   the chat (ArtifactPanel.js), the chat usable beside it: the full table, the chart, the SQL behind the answer for
   every role who may ask (#624), what "More" held (Context's own card, #607), the sources, the graph path and ✓/✗; the
   plan and trace are the engineer's (§2.1), in Context's Behind the answer there. The left rail lists every card of the
   conversation, "Answers in this chat", each reopening it in the panel. An answer is never held for a Confirm. Every
   question back to the person (a clarification, the agent's own question, the offer to compare the figures after a
   difference answer) is one card (HitlCard.js). Raw tool calls and results are an engineer's view, since they can
   hold the plan. Each question has its turn's working block under it
   (#622: the step under way, what is being computed, then its final state and any error card), and the non-figure
   tools have compact cards of their own (ToolCards.js); a question sent while a turn runs waits as "Next:"; the input
   offers Context's suggestions; each of the person's questions can be edited and asked again from that point.
   The conversation is the page's (chatSession.js), so a mode switch keeps it; the left rail holds the sessions (#625:
   the person's conversations the server keeps, each with its title and date, a page at a time, each deletable) and
   folds; there is no question limit (#625); Graph mode floats the same conversation over the
   graph as the assistant, which hides to a button; a card opened there fills the view over the graph.
   #651: the thread scrolls to new content (agentchat's ChatThread); the rail holds the conversations only (B1); each
   reply shows when it was sent and how long it took at its top, and Copy, Like and Dislike at its foot (B2), from the
   server's reply record; an Ask reply's readout message sits above its card while the warehouse runs and after (B3);
   only a reply the server marks `card: true` is an answer card, any other a message only (B4); the running turn's
   status is pinned above the input, and a finished turn's steps are its answer's Steps tab (B5); the side panel lists
   "Answers in this chat", newest first, and opens one with "← All answers" (B6), behind an "Answers (n)" button on a
   narrow screen (B8). A failed turn keeps its failure card after a reload (A4), and Try again after a refusal or a
   lost connection sends the question afresh where the server never had it (A3). #672: a turn reads in order: the
   question, its Edit, when the reply was sent and how long it took, the reply, then Copy, Like and Dislike as icons in
   the page's grey ink, never the accent. */

export { contextRead } from "./ArtifactPanel";

/** The person as Context's components read them: their id, their Cerebro roles (engineer opens the plan, SQL and
 *  trace) and the units they read, where the page knows them. */
export function meOf(state) {
  const roles = Array.isArray(state?.roles) ? state.roles : [];
  return { id: typeof state?.userId === "string" ? state.userId : "", cerebro_roles: roles, ...(Array.isArray(state?.reads) ? { reads: state.reads } : {}) };
}

/** #613: the compact card, what the chat shows of an answer: its label, its figure or the first rows of its table
 *  (#626: a chart's too; the chart itself is the side panel's), a definition's or a decline's words. A clarification's
 *  choices are its question back to the person, the HITL card, never buttons here (#624). `title` names what a figure,
 *  table or chart answered (#619). Its citations are the panel's sources. Every field is React text. */
export function CompactAnswer({ answer, title = "" }) {
  const c = useMemo(() => compactOf(answer, title), [answer, title]);
  const shown = firstRows(c);
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
      {shown.table ? <CompactTable table={shown.table} /> : null}
      {shown.result ? <Result result={shown.result} /> : null}
      {c.definitions?.length === 1 ? <p className="cerebro-compact-text">{c.definitions[0].text}</p> : null}
      {c.definitions?.length > 1 ? (
        <dl className="cerebro-compact-definitions">
          {c.definitions.map((d, i) => <div key={i}><dt>{d.term}</dt><dd>{d.text}</dd></div>)}
        </dl>
      ) : null}
      {c.text ? <p className="cerebro-compact-text">{c.text}</p> : null}
    </div>
  );
}

/** One Ask reply in the chat (#626): the compact card and "Open · <rows> × <columns>" ("Open" where it has no table),
 *  which shows the whole answer in the side panel. `title` names what it answered, from the question sent to Ask (#619).
 *  `truncated` is the server's mark on a reply it kept only the first rows of (#625): the size says "First N of M rows". */
export function ChatAnswer({ id, answer: reply, title, truncated = null }) {
  const { engineer, opened, onOpen } = useContext(ChatContext);
  const answer = useMemo(() => cardAnswer(reply, engineer), [reply, engineer]);
  const size = useMemo(() => sizeOf(compactOf(answer, title), truncated), [answer, title, truncated]);
  const key = String(id ?? "");
  return (
    <article className="cerebro-chat-card cerebro-answer-thumbnail" aria-label="Answer" data-open={opened && opened === key ? "true" : undefined}>
      <CompactAnswer answer={answer} title={title} />
      {onOpen ? (
        <Button type="button" className="cerebro-chat-open" aria-controls={opened === key ? PANEL_ID : undefined} onClick={() => onOpen(key)}>{openLabel(size)}</Button>
      ) : null}
    </article>
  );
}

/** #651 (B4): an Ask reply that is no answer card (chat, help, definitions, no rows, a refusal) as a message only: its
 *  label, definitions and words, as React text, with no card, figure, table or Open. */
export function ReplyText({ answer }) {
  const c = useMemo(() => compactOf(answer, ""), [answer]);
  const documentAnswer = answer?.document_answer;
  const documentSources = [...new Map(documentExcerptItems(documentAnswer).map((s) => [s.doc, s])).values()];
  const words = c.label || c.text || c.definitions?.length ? "" : stripAnswerLabel(typeof answer?.answer_text === "string" ? answer.answer_text : "", answer?.answer_mode);
  return (
    <div className="cerebro-reply-text">
      <DocumentIntro documentAnswer={documentAnswer} />
      {!documentAnswer && c.label ? <p className="agentchat-answer">{c.label}</p> : null}
      {!documentAnswer && c.definitions?.length ? (
        <dl className="cerebro-compact-definitions">
          {c.definitions.map((d, i) => <div key={i}><dt>{d.term}</dt><dd>{d.text}</dd></div>)}
        </dl>
      ) : null}
      {!documentAnswer && c.text ? <p className="agentchat-answer">{c.text}</p> : null}
      {!documentAnswer && words ? <p className="agentchat-answer">{words}</p> : null}
      {documentAnswer && documentSources.length ? (
        <section aria-label="Sources"><h3>Sources</h3><ul>
          {documentSources.map((source) => <li key={source.doc}>{source.title ? `${source.title} · ` : ""}{source.doc}</li>)}
        </ul></section>
      ) : !documentAnswer && c.citations?.length && c.definitions?.length ? (
        <section aria-label="Sources"><h3>Sources</h3><ul>
          {c.citations.map((source, i) => <li key={`${source.n}-${i}`}>{source.n != null ? `[${source.n}] ` : ""}{source.text}</li>)}
        </ul></section>
      ) : null}
      <DocumentExcerpts documentAnswer={documentAnswer} />
    </div>
  );
}

/** A non-figure tool's reply as its compact card (#622), or null where the reply is no card (chatTools.js). Each opens
 *  in the side panel with `onPanel` (#626). */
function toolCard(message, engineer, openNode, onOpen) {
  const panel = onOpen ? () => onOpen(keyOf(message)) : undefined;
  const explore = exploreOf(message, engineer);
  if (explore) return <ExploreCard explore={explore} graph={exploreGraph(explore)} onOpen={openNode} onPanel={panel} />;
  const page = graphPageOf(message);
  if (page) return <GraphPageCard page={page} onOpen={openNode} onPanel={panel} />;
  const hits = searchOf(message);
  if (hits) return <SearchCard hits={hits} onPanel={panel} />;
  const map = termMapOf(message, engineer);
  if (map) return <TermMapCard map={map} onPanel={panel} />;
  return null;
}

/** #622: whether the message at `index` is agent text after Ask's not-sure card in a turn whose block ended not
 *  planned. The not-sure card and the error card already say why, so the agent's sentence is not shown a third time. */
export function afterNotPlanned(messages, index, blocks) {
  const all = messages || [];
  const at = all.slice(0, index).map((m) => m?.type).lastIndexOf("human");
  if (at < 0 || !all.slice(at + 1, index).some((m) => answerOf(m))) return false;
  const block = (blocks?.[all[at]?.id] || []).at(-1);
  return block?.state === "failed" && block.error?.case === "not_planned";
}

/** #624: a person's answer to a choose call, shown as theirs while its resume runs: answered locally, then the resume
 *  sent; once the resume settles (its stream ends or errors, or it throws), the answer is marked settled, and the card
 *  clears it where no answer to the call came back (ChooseCard). A resume that returns no promise is never settled. */
function resumeLocally(answerLocally, callId, answer, resume, value) {
  answerLocally(callId, answer);
  const settle = () => answerLocally(callId, { ...answer, settled: true });
  let request;
  try {
    request = resume(value);
  } catch {
    settle();
    return;
  }
  if (request && typeof request.then === "function") request.then(settle, settle);
}

/** #624: a question back to the person in the conversation, its choose call. Answered, it shows the person's answer
 *  as theirs. The live interrupt's own call is the thread's to show (HitlInterrupt). The card is live while it is the
 *  conversation's last message, unanswered, and no turn runs: for the offer to compare the figures, after its turn;
 *  for a clarification or the agent's question, once its interrupt has gone with no answer to it (a failed resume). A
 *  pick sends a resume with the conversation, "No thanks" is answered here and sends nothing, and a question typed
 *  instead moves on and closes it. A pick, here or on the interrupt's card, shows as the person's while its resume
 *  runs; where the resume fails (the stream errors, or the turn ends with no answer to this call), it is cleared and
 *  the card can be answered again. */
function ChooseCard({ call, last, busy, messages, resume }) {
  const { answered, answerLocally, live } = useContext(ChatContext);
  const choose = chooseOf(call.args);
  const chosen = chosenOf(messages, call.id);
  const local = answered?.[call.id];
  const settled = Boolean(local?.settled);
  useEffect(() => {
    if (settled && !busy && !chosen) answerLocally(call.id, null);
  }, [settled, busy, chosen, answerLocally, call.id]);
  if (!chosen && call.id === live) return null;
  const answer = chosen ? chosenText(chosen) : local?.text || "";
  const picked = chosen?.picked?.id ?? local?.id;
  const error = typeof chosen?.error === "string" ? chosen.error : "";
  const offer = choose.source === "offer";
  const active = !chosen && !local && last && !busy && Boolean(resume);
  const pick = (id, label) => {
    if (id === "no" && offer) answerLocally(call.id, { id, text: label });
    else resumeLocally(answerLocally, call.id, { id, text: label }, resume, { pick: id });
  };
  const month = (m) => resumeLocally(answerLocally, call.id, { text: monthWords(m) }, resume, { month: m });
  const other = (text) => resumeLocally(answerLocally, call.id, { text }, resume, { other: text });
  // #713: a multiple-question card's Send, every pick at once; answered, its picks.
  const send = (picks, text) => resumeLocally(answerLocally, call.id, { picks, text }, resume, { picks });
  return (
    <HitlCard
      choose={offer ? choose : { ...choose, months: [] }}
      active={active}
      other={!offer}
      answer={answer}
      picked={picked}
      picks={picksOf(chosen) ?? local?.picks}
      error={error}
      onPick={pick}
      onSend={send}
      onMonth={month}
      onOther={other}
      onCancel={!offer && active ? () => resumeLocally(answerLocally, call.id, { text: "Cancelled" }, resume, { cancel: true }) : undefined}
    />
  );
}

/** The live question back to the person (an interrupt of kind "hitl"), on the HITL card: a pick resumes the turn with
 *  { pick: id }, a multiple-question card's Send with { picks } (#713), the person's own words with
 *  { other: text }, each shown as theirs while it runs (resumeLocally). */
function HitlInterrupt({ value, disabled, onResume }) {
  const { answerLocally } = useContext(ChatContext);
  const choose = chooseOf(value);
  const callId = typeof value.tool_call_id === "string" ? value.tool_call_id : "";
  return (
    <div className="agentchat-ai">
      <HitlCard
        choose={{ ...choose, months: [] }}
        active={!disabled}
        autoFocus
        other={value.other !== false}
        onPick={(id, label) => resumeLocally(answerLocally, callId, { id, text: label }, onResume, { pick: id })}
        onSend={(picks, text) => resumeLocally(answerLocally, callId, { picks, text }, onResume, { picks })}
        onOther={(text) => resumeLocally(answerLocally, callId, { text }, onResume, { other: text })}
        onCancel={() => resumeLocally(answerLocally, callId, { text: "Cancelled" }, onResume, { cancel: true })}
      />
    </div>
  );
}
/** The interrupt's value when it is a question back to the person, else null. */
export function hitlOf(interrupt) {
  const first = Array.isArray(interrupt) ? interrupt[0] : interrupt;
  const value = first && typeof first === "object" && "value" in first ? first.value : first;
  return value && typeof value === "object" && value.kind === "hitl" ? value : null;
}

/** One assistant or tool message, as this person may see it. `messages`, `index`, `busy` and `resume` are the thread's. */
export function CerebroMessage({ message, index = 0, messages = [], busy = false, resume }) {
  const { engineer, cards, blocks, openNode, onOpen, stopRun, stopping, askNext, readouts } = useContext(ChatContext);
  if (!message) return null;
  // #630: a question running in the background, or how its run ended other than with an answer.
  const run = runOf(message);
  if (run?.pending) {
    const key = String(message.id ?? "");
    return <div className="agentchat-ai"><PendingRunCard pending={run.pending} stopping={Boolean(stopping?.[key])} disabled={busy} onStop={stopRun && key ? () => stopRun(key) : undefined} /></div>;
  }
  if (run?.ended) return <div className="agentchat-ai"><RunEndingCard ended={run.ended} /></div>;
  // #621: the server's framing get_context is the model's alone; an engineer sees its raw result.
  if (message.type === "tool" && isFrameCall(message.tool_call_id)) return engineer ? <div className="agentchat-ai"><ToolResult message={message} /></div> : null;
  const answer = answerOf(message);
  if (answer) {
    // #624: a clarification whose question back to the person follows is that question, on its HITL card.
    if (answer.behaviour === "clarify" && askedBack(messages, message)) return null;
    // #651 (B4): only a reply the server marks card: true is an answer card.
    if (!isCard(message)) return <div className="agentchat-ai"><ReplyText answer={cardAnswer(answer, engineer)} /></div>;
    const title = answeredAs(answer, callOf(messages, message));
    const evidence = answer.document_answer;
    return <div className="agentchat-ai">
      <ChatAnswer id={message.id || message.tool_call_id} answer={answer} title={title} truncated={truncatedOf(message)} />
      {documentExcerptItems(evidence).length ? <div className="cerebro-reply-text"><DocumentIntro documentAnswer={evidence} /><DocumentExcerpts documentAnswer={evidence} /></div> : null}
    </div>;
  }
  // The person's answer to a question back to them shows on its card.
  if (message.type === "tool" && message.name === CHOOSE_TOOL) return null;
  const pkg = contextOf(message);
  if (pkg) {
    if (!engineer && !definitionsOf(pkg).length && !citableSources(pkg).length && !pkg.document_answer) return null;
    return (
      <div className="agentchat-ai">
        <DefinitionCard pkg={pkg} onPanel={onOpen ? () => onOpen(keyOf(message)) : undefined} />
        {engineer ? <ToolResult message={message} /> : null}
      </div>
    );
  }
  if (message.type === "tool") {
    const card = toolCard(message, engineer, openNode, onOpen);
    if (!card && !engineer) return null;
    return (
      <div className="agentchat-ai">
        {card}
        {engineer ? <ToolResult message={message} /> : null}
      </div>
    );
  }
  // The card is the answer (#602): agent text that repeats a card of its turn is not shown again.
  const said = afterNotPlanned(messages, index, blocks) ? "" : getContentString(message.content);
  const text = framingOf(said, cards?.get?.(message.id) || []);
  const calls = engineer && Array.isArray(message.tool_calls) ? message.tool_calls.filter((c) => c?.name !== CHOOSE_TOOL) : [];
  const chooses = chooseCalls(message);
  // #651 (B3): the readout message of each of its Ask calls, above the card the call brings, from the stream while the
  // warehouse runs and from the reply's record after.
  const said3 = (Array.isArray(message.tool_calls) ? message.tool_calls : []).map((c) => (c?.id ? readouts?.[c.id] : "")).filter(Boolean);
  if (!text && !calls.length && !chooses.length && !said3.length) return null;
  const last = index === messages.length - 1;
  return (
    <div className="agentchat-ai">
      {said3.map((words, i) => <p key={i} className="agentchat-answer cerebro-readout">{words}</p>)}
      {text ? <p className="agentchat-answer">{text}</p> : null}
      <ToolCalls toolCalls={calls} />
      {chooses.map((call) => (chooseOf(call.args).source === "suggest"
        ? <SuggestCard key={call.id} choose={chooseOf(call.args)} onAsk={askNext} />
        : <ChooseCard key={call.id} call={call} last={last} busy={busy} messages={messages} resume={resume} />))}
    </div>
  );
}

/** #622: the working blocks of a question's turns (a resume after a pause has one of its own), each in its state, a
 *  failure with its error card. Try again resends the question: in its place when it is the conversation's last (the
 *  server rewinds to it, #625; not after a save that failed, which may have kept nothing to rewind to), else at the end. Rephrase puts it in the box; a suggestion asks itself; Reload reopens
 *  the conversation from the server (#625: it changed in another tab). */
function TurnBlocks({ message, index, messages, busy, ask, askFrom, rephrase }) {
  const { engineer, blocks, reload, replies, holdsQuestion } = useContext(ChatContext);
  const words = getContentString(message?.content);
  // #651: a running block is pinned above the input (LiveStatus) and an answered one's steps are its answer's Steps
  // tab; (A4) after a reload, a failed turn's block is rebuilt from its reply record, so its failure card stays.
  const held = blocks?.[message?.id] || [];
  const saved = held.length ? null : blockOfReply(replies?.[message?.id], words);
  const list = (saved ? [saved] : held).filter((b) => b.state !== "running" && b.state !== "answered");
  if (!list.length) return null;
  const last = messages.map((m) => m?.type).lastIndexOf("human") === index;
  // #625: a turn whose answer could not be saved (not_saved) may have left nothing on the server, so its Try again
  // names no message to rewind to (the server would refuse a rewind to a question it does not hold). #651 (A3): nor
  // does one the server refused, or whose connection was lost, before its question reached the server's values.
  const unsaved = list.at(-1)?.error?.case === "not_saved" || Boolean(list.at(-1)?.unsent);
  // #651 (A3): a connection lost after the server saved the question but before any of its values reached the page
  // leaves it marked unsent though the server holds it. Try again then reads the conversation again first: where the
  // server holds the question it rewinds to it, as for a saved one; else (or with no conversation yet) it sends it
  // afresh.
  const unsent = Boolean(list.at(-1)?.unsent) && message?.id != null && Boolean(holdsQuestion);
  const again = async () => {
    const held = unsent ? await holdsQuestion(String(message.id)) : !unsaved;
    askFrom(index, words, held ? undefined : { rewind: false });
  };
  const tryAgain = ask ? () => (last && askFrom ? again() : ask(words)) : undefined;
  return list.map((block, i) => (
    <WorkingBlock
      key={`${block.started}-${i}`}
      block={block}
      engineer={engineer}
      disabled={busy}
      onTryAgain={tryAgain}
      onRephrase={rephrase ? () => rephrase(words) : undefined}
      onAsk={ask}
      onReload={reload}
    />
  ));
}

/** #651 (B2): when a reply was sent and how long it took: from its reply record (sent, duration_ms), else from its
 *  turn's working block on this page; the duration once the turn has ended. #672: just above the reply, left-aligned
 *  with it, under the question's Edit. */
const timeOf = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
/** #651: "Sent" with the time alone for a reply sent today, and with its date too for one sent on another day. */
export function sentText(ms, now = Date.now()) {
  const sent = new Date(ms);
  if (sent.toDateString() === new Date(now).toDateString()) return `Sent ${timeOf(ms)}`;
  const year = sent.getFullYear() === new Date(now).getFullYear() ? undefined : "numeric";
  return `Sent ${sent.toLocaleDateString([], { day: "numeric", month: "short", year })} ${timeOf(ms)}`;
}
export function replyTimes(record, block) {
  const sent = Date.parse(record?.sent);
  if (Number.isFinite(sent)) return { sent, took: Number.isFinite(record.duration_ms) ? record.duration_ms : null };
  if (!block) return null;
  return { sent: block.started, took: block.state === "running" || block.ended == null ? null : block.ended - block.started };
}
function ReplyMeta({ message }) {
  const { blocks, replies } = useContext(ChatContext);
  const id = message?.id;
  const times = id ? replyTimes(replies?.[id], (blocks?.[id] || []).at(-1)) : null;
  if (!times) return null;
  const took = times.took == null ? "" : tookText(times.took);
  return (
    <p className="cerebro-reply-meta">
      <time dateTime={new Date(times.sent).toISOString()}>{sentText(times.sent)}</time>
      {took ? <span> · {took}</span> : null}
    </p>
  );
}

/** #651 (B2): the words Copy takes of a reply: its readout messages, its agent's words and its answers' words. */
export function replyText(messages, from, end, readouts) {
  const out = [];
  for (const m of (messages || []).slice(from + 1, end + 1)) {
    for (const c of Array.isArray(m?.tool_calls) ? m.tool_calls : []) if (c?.id && readouts?.[c.id]) out.push(readouts[c.id]);
    const answer = answerOf(m);
    const documentAnswer = answer?.document_answer || contextOf(m)?.document_answer;
    if (documentAnswer) {
      if (answer && isCard(m) && typeof answer.answer_text === "string") {
        const words = stripAnswerLabel(answer.answer_text, answer.answer_mode);
        if (words) out.push(words);
      }
      const excerpts = documentExcerptItems(documentAnswer);
      const names = excerpts.map((s, i) => `[${i + 1}] ${s.title ? `${s.title} · ` : ""}${s.doc}${s.attribution ? ` · ${s.attribution}` : ""}`);
      out.push(documentIntroText(documentAnswer));
      if (names.length) out.push(`Sources:\n${names.join("\n")}`);
      if (excerpts.length) out.push(`Summary from source excerpts:\n${excerpts.map((s) => `${s.title ? `${s.title} · ` : ""}${s.doc}\n${s.text}`).join("\n\n")}`);
    } else if (answer) {
      const words = typeof answer.answer_text === "string" ? stripAnswerLabel(answer.answer_text, answer.answer_mode) : "";
      if (words) out.push(words);
    } else if (m?.type === "ai") {
      const words = getContentString(m.content).trim();
      if (words) out.push(words);
    }
  }
  return out.join("\n\n");
}

/** #672: the reply's icons, outlines drawn in the button's own colour (chat.css: the page's grey ink). A chosen verdict's
 *  thumb is filled by its button's aria-pressed, in the same grey. */
function Icon({ children }) {
  return (
    <svg className="cerebro-reply-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.75"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{children}</svg>
  );
}
const CopyIcon = () => (
  <Icon><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></Icon>
);
const CopiedIcon = () => <Icon><path d="M5 12.5l4.5 4.5L19 7.5" /></Icon>;
/** A thumb up, or down (the same thumb turned over). */
const ThumbIcon = ({ down = false }) => (
  <Icon>
    <g transform={down ? "rotate(180 12 12)" : undefined}>
      <rect x="3" y="10" width="4" height="10" rx="1" />
      <path d="M7 11l3.6-6.6A1.9 1.9 0 0 1 14 5.6V10h4.6a2 2 0 0 1 2 2.4l-1.3 6A2 2 0 0 1 17.3 20H7z" />
    </g>
  </Icon>
);
const VERDICTS = [["like", "Like", false, "confirmed"], ["dislike", "Dislike", true, "not_what_i_meant"]];
/** #651: how long Copy says "Copied" before it reads Copy again. */
export const COPIED_MS = 2000;
/** #651 (B2): Copy, Like and Dislike at the foot of a finished reply. #672: each an icon button in the page's grey ink
 *  (cerebro-icon-btn), named for a screen reader; Copy's tooltip says Copy, then Copied. Like and Dislike are one
 *  verdict per reply, as Context's ✓ and ✗ are one send: the first press saves it on the server, and from then on both
 *  are locked showing the choice (no second press changes or clears it), here and after a reload. For a reply with
 *  answer cards (several where the agent asked in parallel) the one choice also posts today's KGE feedback for each of
 *  them (like: confirmed, dislike: not what I meant). A reply of an older conversation with a card but no record yet
 *  offers them too: the verdict route makes its record. Copy says "Copied" for COPIED_MS, announced in a polite live
 *  region. Not shown while the turn runs or waits on the person. */
export function ReplyActions({ human, index, end, messages, loading }) {
  const { replies, readouts, saveVerdict, sendFeedback, feedback, engineer, blocks, live } = useContext(ChatContext);
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), COPIED_MS);
    return () => clearTimeout(timer);
  }, [copied]);
  const id = human?.id ? String(human.id) : "";
  const record = id ? replies?.[id] : undefined;
  const lastTurn = messages.map((m) => m?.type).lastIndexOf("human") === index;
  const running = (blocks?.[id] || []).at(-1)?.state === "running" || (lastTurn && (loading || Boolean(live)));
  if (!id || running || end <= index) return null;
  const words = replyText(messages, index, end, readouts);
  const cards = messages.slice(index + 1, end + 1).filter((m) => isCard(m) && answerOf(m));
  const verdict = record?.verdict === "like" || record?.verdict === "dislike" ? record.verdict : null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(words);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  const judge = async (value, kge) => {
    if (verdict || pending) return;
    setPending(true);
    setFailed(false);
    const ok = await saveVerdict?.(id, value);
    setPending(false);
    if (!ok) { setFailed(true); return; }
    if (!sendFeedback) return;
    for (const card of cards) {
      const answer = cardAnswer(answerOf(card), engineer);
      if (answer?.receipt) sendFeedback(keyOf(card), kge, feedbackBody(answer, kge));
    }
  };
  const feedbackError = cards.map((card) => feedback?.[keyOf(card)]?.error).find(Boolean);
  return (
    <div className="cerebro-reply-actions" role="group" aria-label="This reply">
      {words ? (
        <Button type="button" className="cerebro-icon-btn cerebro-copy" data-copied={copied ? "true" : undefined} onClick={copy} aria-label={copied ? "Copied" : "Copy this reply"}>
          {copied ? <CopiedIcon /> : <CopyIcon />}
          <span className="cerebro-tip" aria-hidden="true">{copied ? "Copied" : "Copy"}</span>
        </Button>
      ) : null}
      <span className="vh" role="status" aria-live="polite">{copied ? "Reply copied." : ""}</span>
      {record || cards.length ? VERDICTS.map(([value, label, down, kge]) => (
        <Button key={value} type="button" className="cerebro-icon-btn cerebro-verdict" aria-label={label} aria-pressed={verdict === value}
          disabled={pending || Boolean(verdict)} onClick={() => judge(value, kge)}><ThumbIcon down={down} /></Button>
      )) : null}
      {failed ? <p className="cerebro-verdict-error" role="alert">Your verdict was not saved. Try again.</p> : null}
      {feedbackError ? <p className="cerebro-verdict-error" role="alert">{feedbackError}</p> : null}
    </div>
  );
}

/** #651 (B5): the turn under way's working block, pinned above the input. */
export function LiveStatus({ messages }) {
  const { blocks, engineer } = useContext(ChatContext);
  const human = [...(messages || [])].reverse().find((m) => m?.type === "human" && (blocks?.[m.id] || []).at(-1)?.state === "running");
  const block = human ? blocks[human.id].at(-1) : null;
  if (!block) return null;
  return <div className="cerebro-live-status"><WorkingBlock block={block} engineer={engineer} /></div>;
}

/** One of the person's questions, with Edit: the question in a box to change and ask again from that point (#625: the
 *  server rewinds the conversation to it). Not offered while an answer is on its way. #672: Edit sits right under the
 *  question, then when the reply was sent and how long it took (ReplyMeta), just above the reply. */
export function CerebroHuman({ message, index = 0, messages = [], busy = false, ask, askFrom, rephrase }) {
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
  const turn = <TurnBlocks message={message} index={index} messages={messages} busy={busy} ask={ask} askFrom={askFrom} rephrase={rephrase} />;
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
        {turn}
      </div>
    );
  }
  return (
    <div className="agentchat-human cerebro-human">
      <p className="agentchat-bubble">{words}</p>
      {busy || !askFrom ? null : (
        <Button type="button" ref={editButton} className="cerebro-edit-q" aria-label={`Edit “${words}”`} onClick={() => { setText(words); setEditing(true); }}>Edit</Button>
      )}
      <ReplyMeta message={message} />
      {turn}
    </div>
  );
}

/** #622 requirement 9: the questions waiting for the turn under way, each "Next: …" with its remove control. A polite
 *  live region, always present, announces each question as it is queued. */
export function NextQuestions({ queue, onRemove }) {
  const list = Array.isArray(queue) ? queue : [];
  // The button pressed goes away with its line, so focus returns to the question box.
  const removed = (e, id) => {
    e.currentTarget.closest(".agentchat-thread")?.querySelector(".agentchat-input input[type='text']")?.focus();
    onRemove(id);
  };
  return (
    <div className="cerebro-next-live" aria-live="polite">
      {list.length ? (
        <ul className="cerebro-next" aria-label="Questions to ask next">
          {list.map((q) => (
            <li key={q.id}>
              <span className="cerebro-next-text">Next: {q.text}</span>
              {onRemove ? <Button type="button" className="cerebro-next-remove" aria-label={`Remove “${q.text}”`} onClick={(e) => removed(e, q.id)}>×</Button> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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

const NONE = [];
const NO_REPLIES = {};
/** What the conversation's cards and its side panel read (chatContext.js): one value around both. `opened` is the card
 *  open in the panel and `onOpen(key)` opens one (#626). */
function ConversationProvider({ chat, opened = "", onOpen, children }) {
  const engineer = isEngineer(chat.roles);
  const messages = chat.stream?.messages || NONE;
  const meKey = JSON.stringify(meOf(chat));
  const me = useMemo(() => JSON.parse(meKey), [meKey]);
  const cards = useMemo(() => turnCards(messages, stripAnswerLabel, toolCardTexts), [messages]);
  const hitl = hitlOf(chat.stream?.interrupt);
  const live = typeof hitl?.tool_call_id === "string" ? hitl.tool_call_id : "";
  // #651 (B3): every readout message by its ask call: the reply records' and the stream's.
  const replies = chat.replies || NO_REPLIES;
  // Only the records of questions still in the conversation count: a rewind leaves the replaced turns' records behind.
  const asked = useMemo(() => new Set(messages.filter((m) => m?.type === "human" && m.id != null).map((m) => String(m.id))), [messages]);
  const readouts = useMemo(() => {
    const out = {};
    for (const [id, record] of Object.entries(replies)) {
      if (!asked.has(id)) continue;
      for (const r of Array.isArray(record?.readouts) ? record.readouts : []) {
        if (typeof r?.tool_call_id === "string" && typeof r.message === "string" && r.message.trim()) out[r.tool_call_id] = r.message.trim();
      }
    }
    return { ...out, ...(chat.readouts || {}) };
  }, [replies, asked, chat.readouts]);
  const value = useMemo(() => ({
    engineer, me, feedback: chat.feedback, sendFeedback: chat.sendFeedback, cards, blocks: chat.blocks || {}, openNode: chat.openNode,
    answered: chat.answered || {}, answerLocally: chat.answerLocally || (() => {}), live, opened, onOpen, reload: chat.reload,
    stopRun: chat.stopRun, stopping: chat.stopping, askNext: chat.enqueue, replies, readouts, saveVerdict: chat.saveVerdict, holdsQuestion: chat.holdsQuestion,
  }), [engineer, me, chat.feedback, chat.sendFeedback, cards, chat.blocks, chat.openNode, chat.answered, chat.answerLocally, live, opened, onOpen, chat.reload,
    chat.stopRun, chat.stopping, chat.enqueue, replies, readouts, chat.saveVerdict, chat.holdsQuestion]);
  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

/** The conversation column on the page's chat: Chat mode's middle and Graph mode's dock show the same one. In Chat
 *  mode, `node` is the page's `asking`, set by "Ask about this" alone: it fills the question box, and the next question
 *  carries it (§4, the request: "the node the question is about when it comes from Ask about this"); `onAsked` clears it. */
function Conversation({ chat, node, onAsked }) {
  const { me } = useContext(ChatContext);
  const messages = chat.stream?.messages || NONE;
  const renderInterrupt = useCallback((interrupt, { disabled, onResume }) => {
    const question = hitlOf(interrupt);
    return question ? <HitlInterrupt value={question} disabled={disabled} onResume={onResume} /> : undefined;
  }, []);
  const context = node ? { node } : undefined;
  const afterTurn = useCallback((props) => <ReplyActions {...props} />, []);
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
  return (
    <>
    <OpenNotes chat={chat} />
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
      waiting={false}
      failure={null}
      onQueue={chat.enqueue ? (text) => { chat.enqueue(text, context); if (node) onAsked?.(); } : undefined}
      queue={<><LiveStatus messages={messages} /><NextQuestions queue={chat.queue} onRemove={chat.unqueue} /></>}
      renderInterrupt={renderInterrupt}
      interruptInComposer
      afterTurn={afterTurn}
    />
    </>
  );
}

/** #625: what the top of the conversation says of the conversation itself: that it is opening, or could not be
 *  opened, and, once its oldest turns are no longer kept on the server, TRIMMED_NOTE. */
const OPENING = {
  loading: "Opening this chat…",
  missing: "This chat is no longer kept. Start a new one.",
  failed: "This chat could not be opened. Try again from the list.",
};
export function OpenNotes({ chat }) {
  const opening = OPENING[chat?.opening] || "";
  const trimmed = chat?.trimmed > 0 && !opening;
  if (!opening && !trimmed) return null;
  return (
    <div className="cerebro-open-notes">
      {opening ? <p className="cerebro-muted" role="status">{opening}</p> : null}
      {trimmed ? <p className="cerebro-muted cerebro-trimmed">{TRIMMED_NOTE}</p> : null}
    </div>
  );
}

/** Chat mode (the owner's prototype, Chat mode): a left rail of sessions that folds to a narrow strip, with New chat
 *  and the sessions alone (#651, B1); the conversation centred beside it, its ask box pinned to the foot; and the side
 *  panel to its right (#651, B6): "Answers in this chat", or a card opened, taking 70% of the width and leaving the
 *  chat at least 360 px. "Answers (n)" opens the list where it is not shown (B8: a narrow screen, or once closed). */
function ChatScreen({ state, actions }) {
  const chat = { ...useChatSession(), roles: state?.roles, userId: state?.userId, reads: state?.reads };
  const [railOpen, setRailOpen] = useState(true);
  const messages = chat.stream?.messages || NONE;
  const panel = usePanel(messages, isEngineer(chat.roles));
  return (
    <ConversationProvider chat={chat} opened={panel.opened} onOpen={panel.open}>
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
            <ConversationList
              conversations={chat.sessions} activeId={chat.activeId} onSelect={chat.select} onNew={chat.startNew}
              onDelete={chat.remove} more={chat.more} onMore={chat.showMore} loading={chat.railLoading} error={chat.railError}
              busyId={chat.switching ? chat.activeId : undefined}
            />
            {chat.switching ? <p className="cerebro-muted">Sessions change once this answer is in.</p> : null}
          </div>
        </section>
        <div className={`cerebro-workspace${panel.shown ? " with-panel" : ""}`}>
          <section className="cerebro-conversation" aria-label="Conversation">
            {!panel.shown && panel.answers.length ? (
              <Button type="button" className="cerebro-answers-open" aria-controls={PANEL_ID} aria-expanded={false} onClick={panel.openList}>
                Answers ({panel.answers.length})
              </Button>
            ) : null}
            <Conversation chat={chat} node={state?.asking} onAsked={actions?.asked} />
          </section>
          {panel.shown ? (
            <ArtifactPanel artifact={panel.artifact} answers={panel.answers} messages={messages} full={panel.full} canFull={panel.canFull}
              onFull={panel.toggleFull} onClose={panel.close} onOpen={panel.open} onBack={panel.back} />
          ) : null}
        </div>
      </div>
    </ConversationProvider>
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
 *  canvas's bottom right; the card stays mounted while hidden, so a question being typed is kept. Its cards are the
 *  same compact cards; one opened fills the view over the graph, full-width, until closed (#626). */
export function ChatDock({ state }) {
  const held = useChatSession();
  const chat = { ...held, roles: state?.roles, userId: state?.userId, reads: state?.reads };
  const messages = held?.stream?.messages || NONE;
  const panel = usePanel(messages, isEngineer(state?.roles), { always: true });
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
    <ConversationProvider chat={chat} opened={panel.opened} onOpen={panel.open}>
      <aside className={`cerebro-dock${open ? "" : " folded"}`} aria-label="Assistant">
        <section id="cerebro-dock-card" className="cerebro-dock-card" aria-labelledby="cerebro-dock-title" hidden={!open}>
          <header className="cerebro-dock-head">
            <h2 id="cerebro-dock-title">Assistant</h2>
            <Button type="button" aria-expanded={open} aria-controls="cerebro-dock-card" aria-label="Hide assistant" ref={hide} onClick={() => toggle(false)}>Hide</Button>
          </header>
          <Conversation chat={chat} />
        </section>
        {open ? null : (
          <Button type="button" className="cerebro-dock-show" aria-expanded={false} aria-controls="cerebro-dock-card" ref={show} onClick={() => toggle(true)}>Show assistant</Button>
        )}
      </aside>
      {panel.artifact ? (
        <ArtifactPanel artifact={panel.artifact} messages={messages} full canFull={false} onFull={panel.toggleFull} onClose={panel.close} />
      ) : null}
    </ConversationProvider>
  );
}
