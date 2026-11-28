import express from "express";
import { notSetUp } from "../apps.js";
import { validateAskSettings } from "../providers.js";
import { openConversationStore, openConversations } from "../conversations/index.js";
import { cerebroAgent } from "./agent.js";
import { conversationRoutes, retentionOf, retentionSweep } from "./conversations.js";
import { openCerebroModel } from "./model.js";
import { cerebroStream } from "./route.js";

// The Cerebro roles that open the chat, as the page's door rule (ui shell/apps.js cerebroRolesOf and canUse): KGE's
// /whoami states them in cerebro_roles (cerebro.md §8.1); admin alone has no door. The portal only refuses here what
// the page would not show; KGE checks every tool call itself.
const DOOR_ROLES = new Set(["business", "steward", "engineer"]);

export function cerebroRoles(identity) {
  // Joined to KGE the identity carries /whoami's answer as who (kge.js identity()); alone it has no Cerebro role.
  const stated = identity?.who?.cerebro_roles;
  const raw = Array.isArray(stated) ? stated : typeof stated === "string" ? [stated] : [];
  return raw.filter((role) => typeof role === "string").map((role) => role.trim().toLowerCase());
}

// The portal's door to a part of Cerebro: a 403 unless the person holds one of roles (by default the chat's door
// roles; Curate's routes pass steward alone, the matrix's rows 4 and 5). KGE still checks every call itself.
export function cerebroDoor(settings, roles = DOOR_ROLES) {
  const allowed = new Set(roles);
  return (req, res, next) => (cerebroRoles(req.portalIdentity).some((r) => allowed.has(r))
    ? next()
    : res.status(403).json({ detail: `${settings.cerebroName} needs a Cerebro role.`, message: `${settings.cerebroName} needs a Cerebro role.` }));
}

/**
 * Cerebro's chat. stream: POST /api/cerebro/stream's handlers, in order: Cerebro's flag and a model, the portal's
 * sign-in (identity: 401 when no one is signed in), a Cerebro role (403), the JSON body (1 MB, read only for a person
 * let in), then the stream. conversations: #625, the person's own conversations (conversations.js), behind the same
 * flag, sign-in and role. Both are mounted ahead of /api/cerebro's graph read and of Discover's flag, so either's 404
 * never hides them. The conversations are kept in the store PORTAL_CONVERSATIONS_URL names (conversations/store.js;
 * store, a test's own), and a daily sweep deletes those past retention; stop() ends it.
 */
export function cerebroChat({ settings, env, identity, model, kge, store }) {
  const modelSettings = validateAskSettings(env);
  const enabled = Boolean(settings.apps.cerebro && (model || (modelSettings.modelProvider === "mock" ? modelSettings.model : kge)));
  const modelFor = (req) => enabled ? openCerebroModel(modelSettings, env, { model, kge, req }) : undefined;
  const issueQuestion = enabled && !model && modelSettings.modelProvider !== "mock" && kge
    ? (req, id, { signal } = {}) => kge.modelQuestion(req, id, "agent", { signal, expectedModel: modelSettings.model || undefined }) : undefined;
  const on = (_req, res, next) => (enabled ? next() : notSetUp(res, "cerebro", settings));
  const role = cerebroDoor(settings);
  const conversations = enabled ? openConversations({ store: store ?? openConversationStore(env) }) : undefined;
  const retention = retentionOf(kge);
  const sweep = conversations ? retentionSweep(conversations, retention) : undefined;
  // The agent's tools run as the person: their Cerebro roles pick the tools, and KGE checks every call itself.
  const agentTurn = enabled ? cerebroAgent({ modelFor, issueQuestion, kge, settings, conversations }) : undefined;
  const stream = agentTurn ? cerebroStream({ runTurn: (turn) => agentTurn({ ...turn, roles: cerebroRoles(turn.req?.portalIdentity) }) }) : undefined;
  const routes = conversations ? conversationRoutes({ conversations, retention, settings, kge, modelFor, issueQuestion, rolesOf: cerebroRoles }) : undefined;
  return {
    stream: [on, identity, role, express.json({ limit: "1mb" }), (req, res) => stream(req, res)],
    conversations: [on, identity, role, (req, res, next) => routes(req, res, next)],
    stop: () => sweep?.stop(),
    // #651 B7: the chat's model name, for GET /api/cerebro/health: PORTAL_ASK_MODEL, or "none" with no chat model.
    model: enabled ? (model || modelSettings.modelProvider === "mock" ? modelSettings.model || "mock" : "gateway") : "none",
  };
}
