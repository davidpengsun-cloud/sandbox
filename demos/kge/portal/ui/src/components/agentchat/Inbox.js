import React, { useState } from "react";
import { Button } from "../shared/Controls";
import { actionName, allowedDecisions, isComplexValue } from "./utils";

/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/agent-inbox/ (the
   human-in-the-loop view and its decisions) and src/components/thread/messages/generic-interrupt.tsx.
   Ported to JavaScript and the portal's tokens. The inbox does not submit itself: it hands its resume value to
   onResume, and the thread that holds the conversation sends it with the conversation (the server keeps nothing). */

const DECISION_LABELS = { approve: "Accept", edit: "Edit", reject: "Reject" };

function Values({ value }) {
  const entries = isComplexValue(value) && !Array.isArray(value) ? Object.entries(value) : null;
  if (!entries) return <code className="agentchat-code-block">{JSON.stringify(value, null, 2)}</code>;
  return (
    <table className="agentchat-args">
      <tbody>
        {entries.map(([key, v]) => (
          <tr key={key}>
            <th scope="row">{key}</th>
            <td>{isComplexValue(v) ? <code>{JSON.stringify(v, null, 2)}</code> : String(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One action request waiting for the person: its arguments and the decisions its review config allows. */
function ActionRequest({ request, config, disabled, onDecide }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => JSON.stringify(request.args, null, 2));
  const [error, setError] = useState("");
  const allowed = allowedDecisions(config) || ["approve", "reject"];
  const saveEdit = () => {
    try {
      // Both spellings: the hook does not normalise a resume on the custom transport.
      const action = { name: request.name, args: JSON.parse(draft) };
      onDecide({ type: "edit", edited_action: action, editedAction: action });
    } catch {
      setError("The arguments are not valid JSON.");
    }
  };
  return (
    <section className="agentchat-inbox-item" aria-label={`Waiting for you: ${request.name}`}>
      <h3 className="agentchat-tool-head">
        <span className="agentchat-tool-label">Waiting for you</span> {request.name}
      </h3>
      {request.description ? <p className="agentchat-muted">{request.description}</p> : null}
      {editing ? (
        <label className="agentchat-edit">
          <span className="agentchat-tool-label">Arguments</span>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={6} />
          {error ? <small role="alert">{error}</small> : null}
        </label>
      ) : (
        <Values value={request.args} />
      )}
      <div className="agentchat-actions">
        {editing ? (
          <>
            <Button className="primary" disabled={disabled} onClick={saveEdit}>Send edit</Button>
            <Button disabled={disabled} onClick={() => setEditing(false)}>Cancel</Button>
          </>
        ) : (
          allowed.map((type) => (
            <Button
              key={type}
              className={type === "approve" ? "primary" : ""}
              disabled={disabled}
              onClick={() => (type === "edit" ? setEditing(true) : onDecide({ type }))}
            >
              {DECISION_LABELS[type] || type}
            </Button>
          ))
        )}
      </div>
    </section>
  );
}

/** The human-in-the-loop inbox: one decision per action request, sent together as { decisions }. */
export function ThreadView({ interrupt, disabled = false, onResume }) {
  // One interrupt at a time, as in Agent Chat UI: when the agent raises several, the first is shown and resumed.
  const first = Array.isArray(interrupt) ? interrupt[0] : interrupt;
  const { action_requests: requests, review_configs: configs } = first.value;
  const [decisions, setDecisions] = useState([]);
  const decide = (index, decision) => {
    const next = [...decisions];
    next[index] = decision;
    setDecisions(next);
    if (requests.every((_, i) => next[i])) onResume({ decisions: next });
  };
  return (
    <div className="agentchat-inbox">
      {requests.map((request, i) => (
        <ActionRequest
          key={`${request.name}-${i}`}
          request={request}
          config={configs.find((c) => actionName(c) === request.name)}
          disabled={disabled || Boolean(decisions[i])}
          onDecide={(decision) => decide(i, decision)}
        />
      ))}
    </div>
  );
}

/** Any other interrupt: its value as a table, and Confirm / Decline when the thread can resume it. The resume is
   { decision, question }, naming the question the person saw, so an agent that replays a different reading pauses
   again rather than taking the decision for it; a value with no question resumes with { decision } alone. */
export function GenericInterruptView({ interrupt, disabled = false, onResume }) {
  const first = Array.isArray(interrupt) ? interrupt[0] : interrupt;
  const value = first && typeof first === "object" && "value" in first ? first.value : first;
  const seen = value && typeof value === "object" && typeof value.question === "string" ? { question: value.question } : {};
  const resumeWith = (decision) => onResume({ decision, ...seen });
  return (
    <section className="agentchat-inbox-item" aria-label="Waiting for you">
      <h3 className="agentchat-tool-head"><span className="agentchat-tool-label">Waiting for you</span></h3>
      <Values value={value} />
      {onResume ? (
        <div className="agentchat-actions">
          <Button className="primary" disabled={disabled} onClick={() => resumeWith("confirmed")}>Confirm</Button>
          <Button disabled={disabled} onClick={() => resumeWith("declined")}>Decline</Button>
        </div>
      ) : null}
    </section>
  );
}
