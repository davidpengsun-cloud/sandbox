import React from "react";
import { Button } from "../shared/Controls";
import { getContentString } from "./utils";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/history/index.tsx.
   The original lists threads a LangGraph server keeps; here the server keeps nothing, so the list shows the
   conversations the page holds, passed in as props: [{ id, title?, messages }]. A conversation with no title
   is named by its first message, as the original does. */

export function conversationTitle(conversation) {
  if (conversation.title) return conversation.title;
  const first = Array.isArray(conversation.messages) ? conversation.messages[0] : null;
  return (first && getContentString(first.content)) || "New chat";
}

export function ConversationList({ conversations = [], activeId, onSelect, onNew }) {
  return (
    <nav className="agentchat-history v3-controls" aria-label="Conversations">
      {onNew ? <Button className="primary" onClick={onNew}>New chat</Button> : null}
      {conversations.length === 0 ? (
        <p className="agentchat-muted">No conversations yet.</p>
      ) : (
        <ul>
          {conversations.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="agentchat-history-item"
                aria-current={c.id === activeId ? "true" : undefined}
                onClick={() => c.id !== activeId && onSelect?.(c.id)}
              >
                {conversationTitle(c)}
              </button>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}
