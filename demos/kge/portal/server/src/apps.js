// The Knowledge home and its four apps (issue #203, Cerebro #466): five on/off
// flags, Cerebro's display name and Certify's link, read from the environment as
// the portal starts.
import { BRAND_SHORT_NAME, brandNames } from "./brand.js";
import { validateAskSettings } from "./providers.js";

export const DEFAULT_CLIENT_NAME = "Client";
export const PRODUCT_NAME = `${DEFAULT_CLIENT_NAME} ${BRAND_SHORT_NAME}`;

// Cerebro's default name; an install renames it with PORTAL_CEREBRO_NAME.
export const DEFAULT_CEREBRO_NAME = "Cerebro";
// #707 decision 9 (#714): the limit on Cerebro's KGE tool calls before ask, for one question (cerebro/beforeAsk.js):
// the read of the question's open items, then one call for each name or word it checks. An install sets it with
// PORTAL_CEREBRO_CALLS_BEFORE_ASK, a whole number from CALLS_BEFORE_ASK_MIN to CALLS_BEFORE_ASK_MAX; any other value
// stops startup and names the setting. When the limit is spent, what is still open goes on the card. The lowest value is
// 2, the read and the call for a name's meanings: with 1 a name's meanings are never read, so the card could offer only
// the one meaning KGE bound it to (#714 acceptance 13).
export const DEFAULT_CALLS_BEFORE_ASK = 6;
export const CALLS_BEFORE_ASK_MIN = 2;
export const CALLS_BEFORE_ASK_MAX = 20;
const CALLS_BEFORE_ASK = "PORTAL_CEREBRO_CALLS_BEFORE_ASK";
function callsBeforeAsk(env) {
  const given = env[CALLS_BEFORE_ASK]?.trim();
  if (!given) return DEFAULT_CALLS_BEFORE_ASK;
  const limit = /^\d{1,2}$/.test(given) ? Number(given) : NaN;
  if (!(limit >= CALLS_BEFORE_ASK_MIN && limit <= CALLS_BEFORE_ASK_MAX)) {
    throw new Error(`${CALLS_BEFORE_ASK} must be a whole number from ${CALLS_BEFORE_ASK_MIN} to ${CALLS_BEFORE_ASK_MAX}, and is ${JSON.stringify(given)}: set it to a whole number from ${CALLS_BEFORE_ASK_MIN} to ${CALLS_BEFORE_ASK_MAX}, or leave it unset for ${DEFAULT_CALLS_BEFORE_ASK}.`);
  }
  return limit;
}

const FLAGS = { certify: "PORTAL_APP_CERTIFY", context: "PORTAL_APP_CONTEXT", discover: "PORTAL_APP_DISCOVER", cerebro: "PORTAL_APP_CEREBRO" };
export const APP_NAMES = { certify: "Certify", context: "Context", discover: "Discover", cerebro: DEFAULT_CEREBRO_NAME };
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
  const cerebroName = env.PORTAL_CEREBRO_NAME?.trim() || DEFAULT_CEREBRO_NAME;
  const { shortName, longName, subtitle } = brandNames(env);
  return { knowledgeHome, apps, tableQuery: flag(env, "PORTAL_TABLE_QUERY", "off"), tablePreview: flag(env, "PORTAL_TABLE_PREVIEW", "off"), dqFilters: flag(env, "PORTAL_DQ_FILTERS"), askGuide: flag(env, "PORTAL_ASK_GUIDE"), logoTitle: flag(env, "PORTAL_LOGO_TITLE", "off"), certifyUrl: url, cerebroName, cerebroCallsBeforeAsk: callsBeforeAsk(env), productName: `${clientName} ${shortName}`, productShortName: shortName, productLongName: longName, productSubtitle: subtitle, contextAskTitle: env.PORTAL_CONTEXT_ASK_TITLE?.trim() || CONTEXT_ASK_TITLE, contextAskSubtitle: env.PORTAL_CONTEXT_ASK_SUBTITLE?.trim() || CONTEXT_ASK_SUBTITLE };
}

// An app's display name: Cerebro's from its name setting, the others fixed.
export function appName(app, settings) {
  return app === "cerebro" && settings?.cerebroName ? settings.cerebroName : APP_NAMES[app];
}

export function notSetUp(res, app, settings) {
  const message = `${appName(app, settings)} is not set up here`;
  return res.status(404).json({ detail: message, message });
}
