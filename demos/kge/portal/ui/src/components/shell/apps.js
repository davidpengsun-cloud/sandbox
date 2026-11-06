/* The portal's apps, in the doors' order, and the rules the global bar and the routes share:
   which app a path belongs to, whether an install has it switched on, and whether the signed-in
   person may use it. Permission is decided only from what the portal already knows; #196 owns
   who may enter each area and replaces canUse when it lands. */

import { DEFAULT_PRODUCT_SHORT_NAME, shortNameOf } from "../../services/config";

export const CERTIFY_PLACEHOLDER = "[Power App link]";
export const DOCS_AT_A_GLANCE = "/knowledge/docs/at-a-glance";
export const DOCS_START_HERE = "/knowledge/docs/start-here";
export const CONTEXT_GUIDE = "/api/context/onboarding-guide";

export const APPS = [
  { id: "certify", label: "Certify", path: "/certify", external: true },
  { id: "context", label: "Context", path: "/context" },
  { id: "discover", label: "Discover", path: "/home" },
];

export const KNOWLEDGE = { id: "knowledge", label: `${DEFAULT_PRODUCT_SHORT_NAME} home`, path: "/" };

/* The home entry, named after the configured product: "ALTA home" by default. */
export const knowledgeFor = (config) => ({ ...KNOWLEDGE, label: `${shortNameOf(config)} home` });

export const appById = (id, config) => APPS.find((app) => app.id === id) || (id === KNOWLEDGE.id ? knowledgeFor(config) : null);

export function appForPath(pathname = "/") {
  const path = pathname.toLowerCase();
  if (path === "/" || path === "/knowledge" || path.startsWith("/knowledge/")) return KNOWLEDGE.id;
  if (path === "/context" || path.startsWith("/context/")) return "context";
  if (path === "/certify" || path.startsWith("/certify/")) return "certify";
  return "discover";
}

/* The normaliser combines app flags and service availability for every entry point. */
export function isSetUp(config, id) {
  if (!config) return false;
  if (id === KNOWLEDGE.id) return config.knowledgeHome;
  return Boolean(config.apps[id]);
}

/* The roles that let a person into Context, from the identity /whoami answered: KGE's plural
   roles, its Access admin flag, and the graph role a KGE answer carries beside its processes.
   The portal's own /whoami (no KGE configured) carries no processes, so its portal role does
   not count. */
export function contextRolesOf(identity) {
  if (!identity || typeof identity !== "object") return [];
  const roles = Array.isArray(identity.roles) ? identity.roles : typeof identity.roles === "string" ? [identity.roles] : [];
  const held = roles.filter((role) => typeof role === "string" && role);
  if (identity.admin === true) held.push("Access admin");
  if (Array.isArray(identity.processes) && typeof identity.role === "string" && identity.role) held.push(identity.role);
  return [...new Set(held)];
}

/* Discover follows today's rule (any signed-in person), Certify is a link that is always
   reachable, and Context needs a KGE role. */
export function canUse(user, id) {
  if (id === "context") return Array.isArray(user?.contextRoles) && user.contextRoles.length > 0;
  return true;
}

/* The single app's route when the Knowledge home is off: the first app that is on. The server
   refuses to start unless exactly one is. */
export function singleAppPath(config) {
  const enabled = config?.configuredApps || config?.apps;
  const app = APPS.find((candidate) => enabled?.[candidate.id]);
  return app ? app.path : null;
}

/* What Docs offers where the person is. Inside an app, that app's own docs come first; the
   Knowledge docs follow when the Knowledge home is on. An app with no docs of its own opens the
   Knowledge docs, and none at all when the Knowledge home is off. Never KGE's /docs. */
export function docsFor(config, id) {
  const knowledge = config?.knowledgeHome
    ? [
        { label: "At a glance", to: DOCS_AT_A_GLANCE },
        { label: "Start here", to: DOCS_START_HERE },
      ]
    : [];
  if (id === "context" && isSetUp(config, "context")) {
    const own = [{ title: "Context docs", items: [{ label: "Context docs", href: CONTEXT_GUIDE }] }];
    return knowledge.length ? [...own, { title: `${shortNameOf(config)} docs`, items: knowledge }] : own;
  }
  return knowledge.length ? [{ title: `${shortNameOf(config)} docs`, items: knowledge }] : [];
}
