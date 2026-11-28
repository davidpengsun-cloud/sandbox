import React, { useState } from "react";
import { isComplexValue } from "./utils";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/messages/tool-calls.tsx.
   Ported to JavaScript; Tailwind classes replaced by agentchat.css on the portal's v3 tokens. */

function ArgsTable({ args }) {
  const entries = Object.entries(args || {});
  if (entries.length === 0) return <code className="agentchat-code-block">{"{}"}</code>;
  return (
    <table className="agentchat-args">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <th scope="row">{key}</th>
            <td>{isComplexValue(value) ? <code>{JSON.stringify(value, null, 2)}</code> : String(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ToolCalls({ toolCalls }) {
  if (!toolCalls || toolCalls.length === 0) return null;
  return (
    <div className="agentchat-tool-calls">
      {toolCalls.map((tc, idx) => (
        <section key={tc.id || idx} className="agentchat-tool" aria-label={`Tool call ${tc.name}`}>
          <h3 className="agentchat-tool-head">
            <span className="agentchat-tool-label">Tool call</span> {tc.name}
            {tc.id ? <code className="agentchat-tool-id">{tc.id}</code> : null}
          </h3>
          <ArgsTable args={tc.args} />
        </section>
      ))}
    </div>
  );
}

const PREVIEW_LINES = 4;
const PREVIEW_CHARS = 500;

export function ToolResult({ message }) {
  const [expanded, setExpanded] = useState(false);
  let parsed = message.content;
  let isJson = false;
  if (typeof message.content === "string") {
    try {
      parsed = JSON.parse(message.content);
      isJson = isComplexValue(parsed);
    } catch {
      parsed = message.content;
    }
  }
  const text = isJson ? JSON.stringify(parsed, null, 2) : String(parsed ?? "");
  const lines = text.split("\n");
  const long = lines.length > PREVIEW_LINES || text.length > PREVIEW_CHARS;
  const shown = long && !expanded ? `${lines.slice(0, PREVIEW_LINES).join("\n").slice(0, PREVIEW_CHARS)}…` : text;
  return (
    <section className="agentchat-tool agentchat-tool-result" aria-label={`Tool result${message.name ? ` ${message.name}` : ""}`}>
      <h3 className="agentchat-tool-head">
        <span className="agentchat-tool-label">Tool result</span> {message.name || ""}
        {message.tool_call_id ? <code className="agentchat-tool-id">{message.tool_call_id}</code> : null}
      </h3>
      <pre className="agentchat-code-block">{shown}</pre>
      {long ? (
        <button type="button" className="agentchat-link" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show less" : "Show all"}
        </button>
      ) : null}
    </section>
  );
}
