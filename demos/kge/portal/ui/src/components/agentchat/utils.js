/* Adapted from Agent Chat UI (MIT, see LICENSE.md in this folder): src/components/thread/utils.ts and
   src/lib/agent-inbox-interrupt.ts. */

/** The text of a message's content, a string or a list of content parts. */
export function getContentString(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => part && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join(" ");
}

/** True when an interrupt carries a human-in-the-loop request: action requests and their review configs. */
export function isAgentInboxInterruptSchema(value) {
  const interrupt = Array.isArray(value) ? value[0] : value;
  if (!interrupt || typeof interrupt !== "object") return false;
  const hitl = interrupt.value;
  if (!hitl || typeof hitl !== "object") return false;
  const { action_requests: requests, review_configs: configs } = hitl;
  if (!Array.isArray(requests) || requests.length === 0) return false;
  if (!Array.isArray(configs) || configs.length === 0) return false;
  return (
    requests.every((r) => r && typeof r === "object" && typeof r.name === "string" && r.args && typeof r.args === "object") &&
    configs.every((c) => c && typeof c === "object" && typeof actionName(c) === "string" && Array.isArray(allowedDecisions(c)))
  );
}

// LangGraph.js agents send the review config in camelCase, Python ones in snake_case.
export const actionName = (config) => config?.actionName ?? config?.action_name;
export const allowedDecisions = (config) => config?.allowedDecisions ?? config?.allowed_decisions;

export const isComplexValue = (value) => Array.isArray(value) || (typeof value === "object" && value !== null);
