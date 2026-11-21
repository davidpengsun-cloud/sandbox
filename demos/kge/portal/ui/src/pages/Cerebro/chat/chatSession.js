import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useAgentChatStream } from "../../../components/agentchat";
import { progressStep } from "../../Context/Ask";
import { lastPathOf, answerOf, postFeedback } from "./chatAnswer";

/* Cerebro's chat, held by the page (cerebro.md §7.2, D1): the agent's stream, the sessions the page holds, the active
   one and the feedback sent. The page mounts it once, above every mode, so a mode switch
   neither drops the conversation nor stops an answer on its way; Chat mode shows it in the middle and Graph mode in
   the dock. Nothing is stored in the portal's server or the browser: a reload starts with no conversation, and the
   address keeps only the mode and the node. */

let counter = 0;
const newSessionId = () => (globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `chat-${(counter += 1)}`);
const freshSession = () => ({ id: newSessionId(), messages: [] });

export const ChatSessionContext = createContext(null);

/** The page's chat. `fetch` and `sendFeedback` are for tests; `health` is KGE's /health `ask` object for the picker;
 *  `questions` are the golden questions the person may ask, for the empty conversation's "Try one of these" and the
 *  input's suggestions, and `glossary` the glossary terms ({ term, units }) the suggestions offer, where KGE gives them. */
const NO_QUESTIONS = [];
export function ChatProvider({ children, actions, fetch, health, questions = NO_QUESTIONS, glossary = NO_QUESTIONS, sendFeedback: post = postFeedback }) {
  const [sessions, setSessions] = useState(() => [freshSession()]);
  const [activeId, setActiveId] = useState(() => sessions[0].id);
  const [feedback, setFeedback] = useState({});
  // Each ask call's progress as KGE reports it ({ path, stages, step }, Context's Ask's step under way), by its tool
  // call id: the stream's custom events, { kind: "ask_progress", tool_call_id, event }.
  const [progress, setProgress] = useState({});
  const onCustomEvent = useCallback((data) => {
    if (!data || data.kind !== "ask_progress" || typeof data.tool_call_id !== "string" || !data.tool_call_id) return;
    setProgress((old) => ({ ...old, [data.tool_call_id]: progressStep(old[data.tool_call_id], data.event) }));
  }, []);
  // The stream's thread is the active session: a switch clears the stream, and the held messages show instead.
  const stream = useAgentChatStream({ fetch, threadId: activeId, onCustomEvent });
  const active = sessions.find((s) => s.id === activeId) || sessions[0];
  // The stream's values are the active session's only once it has sent from it; until the switch's clear has run they
  // are still the last session's.
  const sentFrom = useRef(null);
  const streamed = stream.values && Array.isArray(stream.messages) && stream.messages.length ? stream.messages : null;
  const live = streamed && sentFrom.current === activeId ? streamed : null;
  const messages = live || active.messages;

  // The stream's messages are copied into the active session as they arrive, so a switch keeps them.
  useEffect(() => {
    if (!live) return;
    setSessions((old) => old.map((s) => (s.id === activeId && s.messages !== live ? { ...s, messages: live } : s)));
  }, [live, activeId]);

  // The shared state (§7.2, §7.4): the conversation sent with each question, and the last answer's path. A session
  // with no answer yet clears the path, so a stale path is never highlighted.
  // The page's actions change with the address; they are read through a ref so a mode switch writes nothing.
  const act = useRef(actions);
  act.current = actions;
  const hasAnswer = messages.some((m) => answerOf(m));
  const path = hasAnswer ? lastPathOf(messages) : null;
  const pathKey = JSON.stringify(path);
  const pathRef = useRef(path);
  pathRef.current = path;
  useEffect(() => { act.current?.setConversation?.(messages); }, [messages]);
  useEffect(() => { act.current?.setLastPath?.(pathRef.current); }, [pathKey]);

  const sendFeedback = useCallback(async (id, verdict, body) => {
    setFeedback((old) => ({ ...old, [id]: { sending: true } }));
    const result = await post(body);
    setFeedback((old) => ({
      ...old,
      [id]: result?.error ? { error: `Your feedback was not recorded: ${result.error}` } : { verdict },
    }));
  }, [post]);

  // A session is changed only between answers: a switch would stop the one on its way, or drop a question the agent
  // is waiting on the person to answer (an interrupt is not held in the session).
  const busy = Boolean(stream.isLoading || stream.interrupt);
  const select = useCallback((id) => { if (!busy) setActiveId(id); }, [busy]);
  const startNew = useCallback(() => {
    if (busy) return;
    if (!active.messages.length && !live) return;
    const next = freshSession();
    setSessions((old) => [next, ...old]);
    setActiveId(next.id);
  }, [active.messages.length, busy, live]);

  const submit = stream.submit;
  // A question sent starts its progress afresh: the last turn's steps are done with.
  const send = useCallback((...args) => { sentFrom.current = activeId; setProgress({}); return submit(...args); }, [activeId, submit]);
  const value = useMemo(() => ({
    stream: { ...stream, messages, submit: send },
    sessions: sessions.filter((s) => s.messages.length || s.id === activeId),
    activeId, select, startNew, busy, health, questions, glossary, feedback, sendFeedback, path, progress,
  }), [stream, messages, send, sessions, activeId, select, startNew, busy, health, questions, glossary, feedback, sendFeedback, path, progress]);
  return <ChatSessionContext.Provider value={value}>{children}</ChatSessionContext.Provider>;
}

export const useChatSession = () => useContext(ChatSessionContext);
