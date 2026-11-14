// The Knowledge home and its three apps (issue #203): four on/off flags and
// Certify's link, read from the environment as the portal starts.
import { BRAND_SHORT_NAME, brandNames } from "./brand.js";
import { validateAskSettings } from "./providers.js";

export const DEFAULT_CLIENT_NAME = "Client";
export const PRODUCT_NAME = `${DEFAULT_CLIENT_NAME} ${BRAND_SHORT_NAME}`;

const FLAGS = { certify: "PORTAL_APP_CERTIFY", context: "PORTAL_APP_CONTEXT", discover: "PORTAL_APP_DISCOVER" };
export const APP_NAMES = { certify: "Certify", context: "Context", discover: "Discover" };
export const CONTEXT_ASK_TITLE = "Ask a governed question";
export const CONTEXT_ASK_SUBTITLE = "Certified definitions and figures, answered only from data you may read.";

function flag(env, name, defaultValue = "on") {
  const value = env[name] ?? defaultValue;
  if (value !== "on" && value !== "off") throw new Error(`${name} must be on or off`);
  return value === "on";
}

function certifyUrl(value) {
  if (!value) return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    url = null;
  }
  if (!url || !["http:", "https:"].includes(url.protocol) || !url.host) {
    throw new Error("PORTAL_CERTIFY_URL must be an http or https address, such as https://apps.powerapps.com/play/<app>.");
  }
  return url.href;
}

export function appSettings(env = process.env) {
  validateAskSettings(env);
  const knowledgeHome = flag(env, "PORTAL_KNOWLEDGE_HOME");
  const apps = Object.fromEntries(Object.entries(FLAGS).map(([app, name]) => [app, flag(env, name)]));
  const url = certifyUrl(env.PORTAL_CERTIFY_URL);
  if (!knowledgeHome) {
    const on = Object.keys(FLAGS).filter((app) => apps[app]);
    if (on.length === 0) {
      throw new Error(`With PORTAL_KNOWLEDGE_HOME=off exactly one app must be on, and none is: set one of ${Object.values(FLAGS).join(", ")} to on, or set PORTAL_KNOWLEDGE_HOME=on.`);
    }
    if (on.length > 1) {
      throw new Error(`With PORTAL_KNOWLEDGE_HOME=off exactly one app must be on, and ${on.length} are (${on.map((app) => `${FLAGS[app]}=on`).join(", ")}): set all but one of them to off, or set PORTAL_KNOWLEDGE_HOME=on.`);
    }
  }
  const clientName = env.PORTAL_CLIENT_NAME?.trim() || DEFAULT_CLIENT_NAME;
  const { shortName, longName, subtitle } = brandNames(env);
  return { knowledgeHome, apps, tableQuery: flag(env, "PORTAL_TABLE_QUERY", "off"), tablePreview: flag(env, "PORTAL_TABLE_PREVIEW", "off"), dqFilters: flag(env, "PORTAL_DQ_FILTERS"), logoTitle: flag(env, "PORTAL_LOGO_TITLE", "off"), certifyUrl: url, productName: `${clientName} ${shortName}`, productShortName: shortName, productLongName: longName, productSubtitle: subtitle, contextAskTitle: env.PORTAL_CONTEXT_ASK_TITLE?.trim() || CONTEXT_ASK_TITLE, contextAskSubtitle: env.PORTAL_CONTEXT_ASK_SUBTITLE?.trim() || CONTEXT_ASK_SUBTITLE };
}

export function notSetUp(res, app) {
  const message = `${APP_NAMES[app]} is not set up here`;
  return res.status(404).json({ detail: message, message });
}
