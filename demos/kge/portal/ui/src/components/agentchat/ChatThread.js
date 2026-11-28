import React, { useLayoutEffect, useMemo, useRef, useState } from "react";
import { FetchStreamTransport, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage, HumanMessage } from "./Messages";
import { GenericInterruptView, ThreadView } from "./Inbox";
import { ChatInput } from "./ChatInput";
import { isAgentInboxInterruptSchema } from "./utils";
import "./agentchat.css";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/index.tsx, the
   conversation column. Here the stream is the pinned useStream hook on its custom-backend adapter against the
   portal server's stream route (pages/Cerebro/README-check.md). Since issue 625 the server keeps the conversation, by
   the thread id the hook sends in config.configurable.thread_id: a question sends the one new message as
   input.messages, a resume sends command.resume and no message, and an edited question names the person's message it
   replaces (context.rewind). */

/** The pinned hook on the portal's stream route. `fetch` is for tests; the page passes nothing. `threadId`, when
 *  given, is the open conversation's id, sent with every request: a new one clears the stream (the hook's own
 *  behaviour).
 *  `onCustomEvent(data)`, when given, is called with each custom event the stream carries (Cerebro's ask progress);
 *  the hook reads its options as a question is sent, so the latest callback is read through a ref. */
const keepThread = () => {};
export function useAgentChatStream({ apiUrl = "/api/cerebro/stream", fetch, threadId, onCustomEvent } = {}) {
  const transport = useMemo(() => new FetchStreamTransport({ apiUrl, ...(fetch ? { fetch } : {}) }), [apiUrl, fetch]);
  const custom = useRef(onCustomEvent);
  custom.current = onCustomEvent;
  const relay = useMemo(() => (data) => custom.current?.(data), []);
  const options = { transport, onCustomEvent: relay };
  return useStream(threadId === undefined ? options : { ...options, threadId, onThreadId: keepThread });
}

let counter = 0;
const newId = () => (globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `human-${(counter += 1)}`);

/** The request bodies the thread sends (issue 625): a question, the one new message alone, and a resume, no message at
 *  all (input: null). `held` is the conversation on screen: optimisticValues keeps it there, with the new question,
 *  while the request runs and after a failed one (the hook otherwise clears its values on submit); it is never sent.
 *  `context` (optional) rides beside them as the adapter's `context` field, such as Cerebro's { node }, and an edited
 *  question's { rewind: <the id of the person's message it replaces> }; it is sent only when it carries something. It
 *  never carries the AI fallback, which is a deployment switch, not a person's choice (owner, 2026-11-16). */
const withContext = (options, context) => (context && Object.keys(context).length ? { ...options, context } : options);
export function questionSubmit(held, text, context) {
  const message = { type: "human", id: newId(), content: text };
  return [{ messages: [message] }, withContext({ optimisticValues: { messages: [...held, message] } }, context)];
}
export function resumeSubmit(held, resume, context) {
  return [null, withContext({ command: { resume }, optimisticValues: { messages: held } }, context)];
}

/** Issue 651 (A1): how near the foot of the thread (px) still counts as at the foot: new content scrolls into view while the
 *  person is there, and never while they have scrolled up to read; a question they send always scrolls to the foot. */
export const STICK_PX = 80;
const atFoot = (el) => el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_PX;

/** `context` is sent with each question and resume; `Message` renders an assistant or tool message in place of
 *  AssistantMessage (Cerebro's chat shows answers, and hides raw tool output from all but engineers), and `Human` a
 *  person's message in place of HumanMessage. Both are given the message, its `index` in the conversation, the
 *  conversation (`messages`), `busy`, `ask(text)`, which sends a question, and `askFrom(index, text)`, which sends one
 *  in place of the person's message at index (an edited question): the server rewinds to that message
 *  (context.rewind) and the conversation before it shows meanwhile; a message with no id is asked at the end.
 *  `askFrom(index, text, { rewind: false })` shows it in the same place but names no message to rewind to, for a
 *  question the server may never have kept (its save failed). `empty`,
 *  when given, renders the conversation's empty state in place of the line inviting a question: it is called with
 *  `ask`, which sends a question as the box would and moves focus to the box (Cerebro's "Try one of these" list).
 *  `suggest`, `highlight`, `waiting` and `footer` are the input's (ChatInput). `full`, when true, takes no new
 *  question at the end of the conversation; an edited one still starts from its point.
 *  Messages are also given `loading`: the stream is running and not paused on the person, and `rephrase(text)`, which
 *  puts text in the question box to be edited. `onQueue(text)`, when given, takes a question sent while a turn runs
 *  (ChatInput's); `queue` renders above the box (Cerebro's "Next:" line). `failure`, when given, renders in place of the
 *  fixed sentence a failed stream shows (Cerebro's error card is its working block's, so it passes null). Messages are
 *  also given `resume(value)`, which sends a resume, as an interrupt's answer is sent.
 *  `renderInterrupt(interrupt, { disabled, onResume })`, when given, renders an interrupt the page knows (Cerebro's
 *  question back to the person); where it returns undefined, the inbox's own views render it.
 *  Issue 651: `afterTurn({ human, index, end, messages, busy, loading })`, when given, renders after the last message of each
 *  of the person's turns (Cerebro's Copy, Like and Dislike): `human` is the question that opened it, at `index`, and
 *  `end` the index of the turn's last message. The messages scroll to new content while the person is at their foot
 *  (STICK_PX), and to the foot whenever the person sends a question. */
const fixedFailure = () => <p className="agentchat-error" role="alert">The assistant could not answer. Try again.</p>;
export function ChatThread({ stream, health, context, seed, onAsked, empty, Message = AssistantMessage, Human = HumanMessage, suggest, highlight, waiting, footer, full = false, onQueue, queue = null, failure = fixedFailure, renderInterrupt, interruptInComposer = false, afterTurn }) {
  const held = stream.messages || [];
  const interrupt = stream.interrupt;
  const busy = Boolean(stream.isLoading || interrupt);
  const loading = Boolean(stream.isLoading && !interrupt);
  // Issue 651 (A1): the messages' scroller, whether the person is at its foot, and a question just sent (scrolled to always).
  const scroller = useRef(null);
  const stick = useRef(true);
  const sentNow = useRef(false);
  const onScroll = () => { if (scroller.current) stick.current = atFoot(scroller.current); };
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || !(stick.current || sentNow.current)) return;
    sentNow.current = false;
    stick.current = true;
    el.scrollTop = el.scrollHeight;
  });
  const ask = (text) => {
    if (full) return;
    sentNow.current = true;
    stream.submit(...questionSubmit(held, text, context));
    onAsked?.();
  };
  const askFrom = (index, text, { rewind: named = true } = {}) => {
    sentNow.current = true;
    const rewind = held[index]?.id;
    if (rewind === undefined || rewind === null) stream.submit(...questionSubmit(held, text, context));
    else if (!named) stream.submit(...questionSubmit(held.slice(0, index), text, context));
    else stream.submit(...questionSubmit(held.slice(0, index), text, { ...context, rewind: String(rewind) }));
    onAsked?.();
    box.current?.focus();
  };
  const resume = (value) => stream.submit(...resumeSubmit(held, value, context));
  const own = interrupt && renderInterrupt ? renderInterrupt(interrupt, { disabled: Boolean(stream.isLoading), onResume: resume }) : undefined;
  const inComposer = Boolean(interrupt && own !== undefined && interruptInComposer);
  const [fill, setFill] = useState(null);
  const rephrase = (text) => setFill({ text: String(text ?? "") });
  // A question asked from the empty state's list: the list goes away with the button pressed, so focus moves to the box.
  const box = useRef(null);
  const hadComposerInterrupt = useRef(false);
  useLayoutEffect(() => {
    if (hadComposerInterrupt.current && !inComposer) box.current?.focus();
    hadComposerInterrupt.current = inComposer;
  }, [inComposer]);
  const askFromList = (text) => { ask(text); box.current?.focus(); };
  return (
    <div className="agentchat-thread">
      <div className="agentchat-messages" ref={scroller} onScroll={onScroll}>
        {held.length === 0 ? (empty ? empty(askFromList) : <p className="agentchat-muted">Ask a question about your data to start.</p>) : null}
        {held.map((m, i) => {
          const props = { message: m, index: i, messages: held, busy, loading, full, ask, askFrom, rephrase, resume };
          if (!afterTurn) return m.type === "human" ? <Human key={m.id || i} {...props} /> : <Message key={m.id || i} {...props} />;
          const at = held.slice(0, i + 1).map((x) => x?.type).lastIndexOf("human");
          const ends = at >= 0 && (!held[i + 1] || held[i + 1].type === "human");
          return (
            <React.Fragment key={m.id || i}>
              {m.type === "human" ? <Human {...props} /> : <Message {...props} />}
              {ends ? afterTurn({ human: held[at], index: at, end: i, messages: held, busy, loading }) : null}
            </React.Fragment>
          );
        })}
        {interrupt && own !== undefined ? (inComposer ? null : own) : interrupt ? (
          <div className="v3-controls">
            {isAgentInboxInterruptSchema(interrupt) ? (
              <ThreadView interrupt={interrupt} disabled={stream.isLoading} onResume={resume} />
            ) : (
              <GenericInterruptView interrupt={interrupt} disabled={stream.isLoading} onResume={resume} />
            )}
          </div>
        ) : null}
        {stream.error && failure ? failure() : null}
      </div>
      {queue}
      {inComposer ? own : null}
      <div hidden={inComposer}>
      <ChatInput
        inputRef={box}
        seed={seed}
        fill={fill}
        isLoading={stream.isLoading}
        onSend={ask}
        onStop={stream.stop}
        onQueue={onQueue && !full ? onQueue : undefined}
        health={health}
        suggest={suggest}
        highlight={highlight}
        waiting={waiting}
        footer={footer}
        full={full}
      />
      </div>
    </div>
  );
}
