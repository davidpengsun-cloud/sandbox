import express from "express";
import { notSetUp } from "../apps.js";
import { validateAskSettings } from "../providers.js";
import { cerebroAgent } from "./agent.js";
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
 * POST /api/cerebro/stream's handlers, in order: Cerebro's flag and a model, the portal's sign-in (identity: 401
 * when no one is signed in), a Cerebro role (403), the JSON body (1 MB, read only for a person let in), then the stream. Mounted ahead of
 * /api/cerebro's graph read and of Discover's flag, so either's 404 never hides it.
 */
export function cerebroChat({ settings, env, identity, model, kge }) {
  const chatModel = settings.apps.cerebro ? openCerebroModel(validateAskSettings(env), env, { model }) : undefined;
  const on = (_req, res, next) => (chatModel ? next() : notSetUp(res, "cerebro", settings));
  const role = cerebroDoor(settings);
  // The agent's tools run as the person: their Cerebro roles pick the tools, and KGE checks every call itself.
  const agentTurn = chatModel ? cerebroAgent({ model: chatModel, kge, settings }) : undefined;
  const stream = agentTurn ? cerebroStream({ runTurn: (turn) => agentTurn({ ...turn, roles: cerebroRoles(turn.req?.portalIdentity) }) }) : undefined;
  return [on, identity, role, express.json({ limit: "1mb" }), (req, res) => stream(req, res)];
}
