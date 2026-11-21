import React, { useMemo, useRef } from "react";
import { FetchStreamTransport, useStream } from "@langchain/langgraph-sdk/react";
import { AssistantMessage, HumanMessage } from "./Messages";
import { GenericInterruptView, ThreadView } from "./Inbox";
import { ChatInput } from "./ChatInput";
import { isAgentInboxInterruptSchema } from "./utils";
import "./agentchat.css";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/index.tsx, the
   conversation column. Here the stream is the pinned useStream hook on its custom-backend adapter against the
   portal server's stateless stream route (pages/Cerebro/README-check.md): every request carries the whole
   conversation the page holds, a question as input.messages, a resume as command.resume beside them. */

/** The pinned hook on the portal's stream route. `fetch` is for tests; the page passes nothing. `threadId`, when
 *  given, is the conversation the page holds: a new one clears the stream (the hook's own behaviour).
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

/** The request bodies the thread sends: a question, and a resume with the conversation (never input: null). */
// optimisticValues keeps the conversation on screen while the request runs, and after a failed one: the hook
// otherwise clears its values on submit.
// `context` (optional) rides beside them as the adapter's `context` field, such as Cerebro's { node }; it is sent
// only when it carries something. It never carries the AI fallback, which is a deployment switch, not a person's
// choice (owner, 2026-11-16).
const withContext = (options, context) => (context && Object.keys(context).length ? { ...options, context } : options);
export function questionSubmit(held, text, context) {
  const messages = [...held, { type: "human", id: newId(), content: text }];
  return [{ messages }, withContext({ optimisticValues: { messages } }, context)];
}
export function resumeSubmit(held, resume, context) {
  return [{ messages: held }, withContext({ command: { resume }, optimisticValues: { messages: held } }, context)];
}

/** `context` is sent with each question and resume; `Message` renders an assistant or tool message in place of
 *  AssistantMessage (Cerebro's chat shows answers, and hides raw tool output from all but engineers), and `Human` a
 *  person's message in place of HumanMessage. Both are given the message, its `index` in the conversation, the
 *  conversation (`messages`), `busy`, `ask(text)`, which sends a question, and `askFrom(index, text)`, which sends one
 *  in place of the person's message at index, with the conversation before it alone (an edited question). `empty`,
 *  when given, renders the conversation's empty state in place of the line inviting a question: it is called with
 *  `ask`, which sends a question as the box would and moves focus to the box (Cerebro's "Try one of these" list).
 *  `suggest`, `highlight`, `waiting` and `footer` are the input's (ChatInput). `full`, when true, takes no new
 *  question at the end of the conversation (Context's eight follow-ups); an edited one still starts from its point.
 *  Messages are also given `loading`: the stream is running and not paused on the person. */
export function ChatThread({ stream, health, context, seed, onAsked, empty, Message = AssistantMessage, Human = HumanMessage, suggest, highlight, waiting, footer, full = false }) {
  const held = stream.messages || [];
  const interrupt = stream.interrupt;
  const busy = Boolean(stream.isLoading || interrupt);
  const loading = Boolean(stream.isLoading && !interrupt);
  const ask = (text) => {
    if (full) return;
    stream.submit(...questionSubmit(held, text, context));
    onAsked?.();
  };
  const askFrom = (index, text) => {
    stream.submit(...questionSubmit(held.slice(0, index), text, context));
    onAsked?.();
    box.current?.focus();
  };
  const resume = (value) => stream.submit(...resumeSubmit(held, value, context));
  // A question asked from the empty state's list: the list goes away with the button pressed, so focus moves to the box.
  const box = useRef(null);
  const askFromList = (text) => { ask(text); box.current?.focus(); };
  return (
    <div className="agentchat-thread">
      <div className="agentchat-messages">
        {held.length === 0 ? (empty ? empty(askFromList) : <p className="agentchat-muted">Ask a question about your data to start.</p>) : null}
        {held.map((m, i) => {
          const props = { message: m, index: i, messages: held, busy, loading, full, ask, askFrom };
          return m.type === "human" ? <Human key={m.id || i} {...props} /> : <Message key={m.id || i} {...props} />;
        })}
        {interrupt ? (
          <div className="v3-controls">
            {isAgentInboxInterruptSchema(interrupt) ? (
              <ThreadView interrupt={interrupt} disabled={stream.isLoading} onResume={resume} />
            ) : (
              <GenericInterruptView interrupt={interrupt} disabled={stream.isLoading} onResume={resume} />
            )}
          </div>
        ) : null}
        {stream.error ? <p className="agentchat-error" role="alert">The assistant could not answer. Try again.</p> : null}
      </div>
      <ChatInput
        inputRef={box}
        seed={seed}
        isLoading={stream.isLoading}
        onSend={ask}
        onStop={stream.stop}
        health={health}
        suggest={suggest}
        highlight={highlight}
        waiting={waiting}
        footer={footer}
        full={full}
      />
    </div>
  );
}
