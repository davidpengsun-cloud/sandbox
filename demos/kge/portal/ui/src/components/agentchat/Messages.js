import React from "react";
import { ToolCalls, ToolResult } from "./ToolCalls";
import { getContentString } from "./utils";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/messages/ai.tsx and
   human.tsx. Ported to JavaScript and the portal's tokens. Not carried over: generative UI components,
   artifacts, regenerate from a checkpoint, branch switching and editing a sent message (they need a server
   that keeps threads), and Markdown rendering (the answer shows as plain text). */

export function HumanMessage({ message }) {
  return (
    <div className="agentchat-human">
      <p className="agentchat-bubble">{getContentString(message.content)}</p>
    </div>
  );
}

export function AssistantMessage({ message }) {
  if (!message) return null;
  if (message.type === "tool") return <div className="agentchat-ai"><ToolResult message={message} /></div>;
  const text = getContentString(message.content);
  const toolCalls = Array.isArray(message.tool_calls) ? message.tool_calls : [];
  return (
    <div className="agentchat-ai">
      {text ? <p className="agentchat-answer">{text}</p> : null}
      <ToolCalls toolCalls={toolCalls} />
    </div>
  );
}
