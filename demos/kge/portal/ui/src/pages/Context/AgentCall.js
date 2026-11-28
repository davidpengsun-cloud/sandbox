import React, { useId } from "react";
import { CopyButton } from "./Answer";
import { agentCall } from "./playgroundState";

/** A block of text a person copies: a heading, the text as it is, and its Copy. */
function Copyable({ title, value, label }) {
  return <div className="pg-copyable"><p className="pg-copy-head">{title}</p><div className="codeblock"><CopyButton label={label || `Copy ${title.toLowerCase()}`} value={value} /><pre>{value}</pre></div></div>;
}

const requestText = (request) => [`${request.method} ${request.url}`, ...Object.entries(request.headers).map(([k, v]) => `${k}: ${v}`), "", request.body].join("\n");
const rawText = (reply) => String(reply ?? "");

/** #623 requirement 5: the exact MCP request and its raw reply, copyable, and a short example of calling it from an agent. */
export default function AgentCall({ request, reply, base, equivalent, historyUnknown }) {
  const title = useId();
  if (!request) return <p>No request was made.</p>;
  const example = agentCall(request, base);
  return <section className="pg-agent" aria-labelledby={title}>
    <h3 id={title}>As an agent would call it</h3>
    {equivalent ? <p className="muted">The agent&apos;s equivalent: this page asked over HTTP; an agent sends this MCP request for the same question.</p>
      : <p className="muted">The JSON-RPC body exactly as the portal sent it, with the address and token header an agent uses (your token shown as {"<access_token>"}), and the reply exactly as it came.</p>}
    {historyUnknown && <p className="muted">This turn was restored, and the follow-up history it was asked with is not kept, so the request shows the question alone.</p>}
    <Copyable title="Request" label="Copy the request" value={requestText({ ...request, url: example.url })} />
    {reply !== undefined && <Copyable title="Raw reply" label="Copy the raw reply" value={rawText(reply)} />}
    <p className="pg-copy-head">From an agent</p>
    <p className="muted small">Get a token with your username and password, then send the call with it. The token lasts 15 minutes.</p>
    <Copyable title="Token" label="Copy the token step" value={example.token} />
    <Copyable title="curl" label="Copy the curl call" value={example.curl} />
    <Copyable title="Python" label="Copy the Python example" value={example.python} />
  </section>;
}
