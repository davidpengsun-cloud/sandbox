import React, { useEffect, useMemo, useState } from "react";
import { Button } from "../shared/Controls";
import AskSettingsPicker from "../shared/AskSettingsPicker";
import WaitingStatus from "../shared/WaitingStatus";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): the input form of
   src/components/thread/index.tsx. One free-text box, sent on Enter (the owner's prototype, Chat mode),
   with the shared answer-settings picker beside it and the shared waiting status while a turn runs.
   File upload and the hide-tool-calls switch are not carried over. `seed`, when it changes, fills the box (Cerebro's
   "Ask about this" fills in the node, cerebro.md §7.3); the person finishes the question and sends it.
   `suggest(text)`, when given, lists suggestions for what is typed ({ text, tag }), as Context's Ask offers them: the
   list over the box, the top one's rest faint after the text, ArrowUp/ArrowDown to move, Tab to take one, Escape to
   close, a click to ask it; `highlight(text, typed)` marks the match. `full` takes no question: Send is disabled. `waiting` false leaves the waiting status to the
   conversation (Cerebro shows it where the answer will be); `footer` shows under the box.
   `onQueue(text)`, when given, keeps the box open while a turn runs: Enter, or "Ask next" (shown only once something
   is typed), hands the question to it to be sent when the turn ends, and the bar otherwise shows Stop alone.
   `fill` ({ text }), when a new object is given, puts its text in the box and focuses it (Cerebro's Rephrase). */

const ghostOf = (suggestions, text) => {
  if (!suggestions.length || !text.trim()) return "";
  const top = suggestions[0].text;
  return top.toLowerCase().startsWith(text.toLowerCase()) ? top.slice(text.length) : "";
};

export function ChatInput({ isLoading = false, onSend, onStop, onQueue, health, seed = "", fill, inputRef, suggest, highlight, waiting = true, footer = null, full = false }) {
  const [text, setText] = useState(seed);
  const [closed, setClosed] = useState(false);
  const [active, setActive] = useState(0);
  useEffect(() => {
    if (seed) setText(seed);
  }, [seed]);
  useEffect(() => {
    if (!fill || typeof fill.text !== "string") return;
    setText(fill.text);
    setClosed(true);
    inputRef?.current?.focus();
  }, [fill, inputRef]);
  const queueing = Boolean(isLoading && onQueue);
  const suggestions = useMemo(
    () => (!suggest || closed || isLoading || !text.trim() ? [] : suggest(text) || []),
    [suggest, closed, isLoading, text],
  );
  const ghost = ghostOf(suggestions, text);
  const sendText = (value) => {
    const question = value.trim();
    if (!question || full) return;
    if (queueing) onQueue(question);
    else if (isLoading) return;
    else onSend(question);
    setText("");
    setClosed(true);
  };
  const send = (e) => {
    e.preventDefault();
    sendText(text);
  };
  const onKeyDown = (e) => {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((at) => (at + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((at) => (at - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Tab") {
      const picked = suggestions[active] || suggestions[0];
      if (picked) {
        e.preventDefault();
        setText(picked.text);
        setClosed(true);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setClosed(true);
    }
  };
  return (
    <form className="agentchat-input v3-controls" onSubmit={send}>
      {suggestions.length > 0 ? (
        <ul className="agentchat-suggestions" role="listbox" aria-label="Question suggestions">
          {suggestions.map((s, i) => (
            <li
              key={s.text}
              role="option"
              aria-selected={i === active}
              className={`agentchat-suggestion${i === active ? " active" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                sendText(s.text);
              }}
            >
              <span className="agentchat-suggestion-text">{highlight ? highlight(s.text, text) : s.text}</span>
              <span className="agentchat-suggestion-tag">{s.tag}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="agentchat-input-wrap">
        {ghost ? (
          <div className="agentchat-ghost" aria-hidden="true">
            <span className="agentchat-ghost-typed">{text}</span>
            <span className="agentchat-ghost-rest">{ghost}</span>
          </div>
        ) : null}
        <input
          ref={inputRef}
          type="text"
          aria-label="Ask the Assistant"
          placeholder="Ask the Assistant…"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setClosed(false);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
        />
      </div>
      {footer}
      <div className="agentchat-input-bar">
        {/* Issue 651 (B7): the model chip, the current model only (the same chip as the Playground's). */}
        <AskSettingsPicker id="agentchat-settings" model={health?.model || "none"} />
        {isLoading && waiting ? <WaitingStatus /> : null}
        {queueing && text.trim() ? <Button type="submit" disabled={full}>Ask next</Button> : null}
        {isLoading && onStop ? (
          <Button onClick={onStop}>Stop</Button>
        ) : (
          <Button type="submit" className="primary" disabled={isLoading || full || !text.trim()}>Send</Button>
        )}
      </div>
    </form>
  );
}
