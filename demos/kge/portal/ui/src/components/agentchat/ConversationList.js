import React, { useEffect, useRef, useState } from "react";
import { Button } from "../shared/Controls";
import { getContentString } from "./utils";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/history/index.tsx.
   The original lists threads a LangGraph server keeps; since issue 625 the portal's server keeps them too, and the page
   passes the person's conversations in as props, as its list route gives them, newest first:
   [{ id, title?, updated?, messages? }]. Each shows its title and the date it last changed. A conversation with no
   title is named by its first message, as the original does. `more` offers "Show more" for the next page; `onDelete`
   offers Delete on each, which asks first. */

export function conversationTitle(conversation) {
  if (conversation.title) return conversation.title;
  const first = Array.isArray(conversation.messages) ? conversation.messages[0] : null;
  return (first && getContentString(first.content)) || "New chat";
}

/** The date a conversation last changed, as "1 Nov 2026" in the reader's time zone; "" where it has none. */
export function conversationDate(conversation) {
  const value = conversation?.updated ?? conversation?.created;
  if (value === undefined || value === null || value === "") return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

function Conversation({ conversation, active, onSelect, onDelete, deleting }) {
  const [asking, setAsking] = useState(false);
  // Issue 625 sign-off: Delete gives way to its confirm, so focus moves to the confirm's Delete; Cancel brings it back.
  const askButton = useRef(null);
  const confirmButton = useRef(null);
  const toggled = useRef(false);
  useEffect(() => {
    if (!toggled.current) return;
    toggled.current = false;
    (asking ? confirmButton : askButton).current?.focus();
  }, [asking]);
  const ask = (next) => { toggled.current = true; setAsking(next); };
  const title = conversationTitle(conversation);
  const date = conversationDate(conversation);
  const value = conversation.updated ?? conversation.created;
  return (
    <li className="agentchat-history-row">
      <button
        type="button"
        className="agentchat-history-item"
        aria-current={active ? "true" : undefined}
        onClick={() => !active && onSelect?.(conversation.id)}
      >
        {title}
      </button>
      {date ? <time className="agentchat-history-date" dateTime={new Date(value).toISOString()}>{date}</time> : null}
      {onDelete && !asking ? (
        <Button type="button" className="agentchat-history-delete" aria-label={`Delete “${title}”`} ref={askButton} disabled={deleting} onClick={() => ask(true)}>Delete</Button>
      ) : null}
      {onDelete && asking ? (
        <div className="agentchat-history-confirm" role="group" aria-label={`Delete “${title}”?`}>
          <span>Delete this chat?</span>
          <Button type="button" className="primary" ref={confirmButton} disabled={deleting} onClick={() => { setAsking(false); onDelete(conversation.id); }}>Delete</Button>
          <Button type="button" onClick={() => ask(false)}>Cancel</Button>
        </div>
      ) : null}
    </li>
  );
}

/** The list. `busyId` is a conversation whose Delete waits (the one with an answer on its way); `loading` says a page
 *  is being read; `error` is a sentence to show where the list could not be read or a conversation deleted. */
export function ConversationList({ conversations = [], activeId, onSelect, onNew, onDelete, more = false, onMore, loading = false, error = "", busyId }) {
  return (
    <nav className="agentchat-history v3-controls" aria-label="Conversations">
      {onNew ? <Button className="primary" onClick={onNew}>New chat</Button> : null}
      {error ? <p className="agentchat-muted agentchat-history-error">{error}</p> : null}
      {conversations.length === 0 ? (
        error ? null : <p className="agentchat-muted">{loading ? "Loading your chats…" : "No conversations yet."}</p>
      ) : (
        <ul>
          {conversations.map((c) => (
            <Conversation key={c.id} conversation={c} active={c.id === activeId} onSelect={onSelect} onDelete={onDelete} deleting={busyId === c.id} />
          ))}
        </ul>
      )}
      {more && onMore ? <Button type="button" className="agentchat-history-more" disabled={loading} onClick={onMore}>Show more</Button> : null}
    </nav>
  );
}
