/* Cerebro's shared state (cerebro.md §7.2). The mode, the selected node and the open conversation's
   id (issue 625) live in the address, `/cerebro?mode=<m>&node=<id>&chat=<conversation id>`, so a
   link or a reload reopens them; the last answer's path and the graph's position live in the page
   only and are kept across modes. The conversation itself is the portal server's (issue 625), read
   by its id; no question text goes into the address. */

export const MODES = Object.freeze(["chat", "graph", "curate"]);
export const DEFAULT_MODE = "graph";
export const MODE_LABELS = Object.freeze({ chat: "Chat", graph: "Graph", curate: "Curate" });
export const MODE_KEY = "`";
const NODE_MAX = 512;
// A conversation's id is a UUID, the server's own pattern (route.js CONVERSATION_ID); anything else in the address
// opens a new chat and leaves the address.
export const CHAT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const chatOf = (value) => (typeof value === "string" && CHAT_ID.test(value) ? value : null);

/* Curate is the steward's; Business without Engineer or Steward has Chat alone. */
export const isSteward = (roles) => Array.isArray(roles) && roles.includes("steward");
export const modesFor = (roles) => {
  if (!Array.isArray(roles)) return ["chat"];
  if (roles.includes("business") && !roles.includes("engineer") && !isSteward(roles)) return ["chat"];
  return MODES.filter((mode) => mode !== "curate" || isSteward(roles));
};

const nodeOf = (value) => {
  const node = typeof value === "string" ? value.trim() : "";
  return node && node.length <= NODE_MAX ? node : null;
};

/* The mode and node an address asks for, as this person may have them: an unknown mode, or
   Curate without the steward role, opens the default mode. */
export function readAddress(search, roles) {
  const params = new URLSearchParams(search || "");
  const asked = params.get("mode");
  const available = modesFor(roles);
  const mode = available.includes(asked) ? asked : available.includes(DEFAULT_MODE) ? DEFAULT_MODE : available[0];
  return { mode, node: nodeOf(params.get("node")), chat: chatOf(params.get("chat")) };
}

/* The address for a mode, node and conversation: the mode always, the node only when one is
   selected, the conversation only when one the server keeps is open. */
export function writeAddress({ mode, node, chat }) {
  const params = new URLSearchParams({ mode: MODES.includes(mode) ? mode : DEFAULT_MODE });
  const selected = nodeOf(node);
  if (selected) params.set("node", selected);
  const open = chatOf(chat);
  if (open) params.set("chat", open);
  return `?${params.toString()}`;
}

/* The mode after this one, for the switch key: Curate is skipped without the steward role. */
export function nextMode(mode, roles) {
  const modes = modesFor(roles);
  const at = modes.indexOf(mode);
  return modes[(at + 1) % modes.length];
}

/* The page-held part of the state. `conversation` is the list of turns sent with each question
   (D1); `lastPath` is the last answer's `graph_path`; `graphPosition` is the graph's pan and zoom. */
/* `graph` is the graph as read and laid out, kept so a return to Graph mode neither reads nor lays
   it out again. */
/* `asking` is the node "Ask about this" fills in to the next question (§7.3), once: it is cleared when that question
   is sent or the mode changes, so a node merely selected in Graph mode is never sent with a question. */
/* `fittedPath` names the answer's path the graph's view was last fitted to, so a return to Graph mode with a kept
   view fits to a path that is new since. */
export const INITIAL_PAGE_STATE = Object.freeze({ conversation: [], lastPath: null, graphPosition: null, graph: null, asking: null, fittedPath: null });

export function pageReducer(state, action) {
  switch (action?.type) {
    case "turn":
      return { ...state, conversation: [...state.conversation, action.turn] };
    case "conversation":
      return { ...state, conversation: Array.isArray(action.conversation) ? action.conversation : [] };
    case "path":
      return { ...state, lastPath: action.path ?? null };
    case "graph":
      return { ...state, graph: action.graph ?? null };
    case "asking":
      return state.asking === (action.node ?? null) ? state : { ...state, asking: action.node ?? null };
    case "position":
      return { ...state, graphPosition: action.position ?? null };
    case "fitted":
      return state.fittedPath === (action.path ?? null) ? state : { ...state, fittedPath: action.path ?? null };
    default:
      return state;
  }
}

/* Keys typed into a field are the person's text, not the mode switch. */
export function isTyping(target) {
  if (!target || typeof target !== "object") return false;
  if (target.isContentEditable) return true;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}
