import { notSetUp } from "../apps.js";
import { relay } from "../kge.js";
import { showcaseOf } from "./tools.js";

// The query parameters KGE's GET /graph takes, and the only ones forwarded:
// a question never rides on a URL, so it never reaches an access log.
const GRAPH_PARAMS = ["cursor", "limit", "node"];

// Cerebro's graph read, through the portal: GET /api/cerebro/graph to KGE's
// GET /graph, POST /api/cerebro/explore to KGE's POST /graph/explore, POST
// /api/cerebro/ask/feedback to KGE's POST /ask/feedback, GET /api/cerebro/health
// to KGE's GET /health, answering its `ask` object alone, GET
// /api/cerebro/questions to KGE's GET /golden, answering the pack's showcase questions alone. On when
// Cerebro's flag is on and KGE is behind the portal, whatever Context's flag.
// Every call runs as the signed-in person: their cookie, and their own KGE
// bearer in x-kge-authorization, never the portal's trusted caller token.
// KGE decides what the person may read; its status and body pass unchanged.
// Mounted before express.json(), so explore's body reaches KGE as sent. #651 B7: model is the Cerebro chat's model
// name for the health reply (chat.js cerebroChat's model: PORTAL_ASK_MODEL, or "none").
export function cerebroGraph(kge, settings, { model = "none" } = {}) {
  return async (req, res) => {
    if (!kge) return notSetUp(res, "cerebro", settings);
    const route = req.path;
    let target;
    let body = {};
    if (req.method === "GET" && route === "/graph") {
      const query = new URLSearchParams();
      for (const name of GRAPH_PARAMS) if (typeof req.query[name] === "string") query.set(name, req.query[name]);
      target = query.size ? `/graph?${query}` : "/graph";
    } else if (req.method === "POST" && route === "/explore") {
      target = "/graph/explore";
      body = rawBody(req);
    } else if (req.method === "POST" && route === "/ask/feedback") {
      // ✓/✗ on an answer, with its receipt, to KGE's POST /ask/feedback as the person (A1 adds the KGE route).
      target = "/ask/feedback";
      body = rawBody(req);
    } else if (req.method === "GET" && route === "/health") {
      return askHealth(kge, settings, req, res, model);
    } else if (req.method === "GET" && route === "/questions") {
      return goldenQuestions(kge, settings, req, res);
    } else {
      return res.status(404).json({ detail: "Not found." });
    }
    return asPerson(kge, settings, req, res, target, body);
  };
}

// One call to KGE as the signed-in person (their cookie, and their own bearer in
// x-kge-authorization, never the trusted caller token); KGE's status and body pass
// unchanged, and an unreachable KGE is a 502 that names the app.
export async function asPerson(kge, settings, req, res, target, init = {}) {
  const bearer = personBearer(req);
  try {
    const response = await kge.request(req, target, { trusted: false, ...init, headers: { ...init.headers, ...bearer } });
    return await relay(response, res);
  } catch {
    if (res.headersSent) return res.destroy();
    res.removeHeader("X-Portal-Notice");
    return res.status(502).json({ detail: `${settings.cerebroName} is unavailable right now. Try again shortly.` });
  }
}

// The Ask settings KGE's GET /health reports (default, modes, model), for the
// chat's settings picker: the `ask` object alone, never the rest of KGE's build.
// #651 B7: its model is the Cerebro chat's own (PORTAL_ASK_MODEL, or "none"), the
// model the chat's chip shows, in place of KGE's; { model } alone when KGE reports
// no ask object. A refusal passes unchanged; an unreachable or failing KGE (a 5xx),
// or a reply that is not JSON, is a 502 that names the app and still carries the
// chat's model, which does not depend on KGE, so the chip names it all the same.
async function askHealth(kge, settings, req, res, model) {
  const bearer = personBearer(req);
  let response;
  let ask;
  try {
    response = await kge.request(req, "/health", { trusted: false, headers: bearer });
    if (response.ok) {
      const body = await response.json();
      ask = body && typeof body.ask === "object" && !Array.isArray(body.ask) ? body.ask : null;
    }
  } catch {
    response = undefined;
  }
  if (response && !response.ok && response.status < 500) return relay(response, res);
  res.removeHeader("X-Portal-Notice");
  if (!response?.ok) return res.status(502).json({ detail: `${settings.cerebroName} is unavailable right now. Try again shortly.`, model });
  return res.json({ ...ask, model });
}

// The chat's "Try one of these" list: the showcase questions the client's pack
// declares, which each set of KGE's GET /golden names under golden20, as text
// alone: those its A/B baseline answered as expected first, then the rest, its known
// misses last; empty where the pack declares none. KGE leaves out every
// question it ties to a unit the person may not read, so the portal filters nothing
// more; asking any of them is still checked by KGE. A refusal passes unchanged; an
// unreachable KGE, or a reply that is not JSON, is a 502 that names the app.
async function goldenQuestions(kge, settings, req, res) {
  const bearer = personBearer(req);
  let response;
  let questions = [];
  let glossary = [];
  try {
    response = await kge.request(req, "/golden", { trusted: false, headers: bearer });
    if (response.ok) {
      const body = await response.json();
      const text = (list) => (Array.isArray(list) ? list.filter((question) => typeof question === "string" && question.trim()) : []);
      questions = showcaseOf(body);
      // #607: the glossary's terms (KGE's own list for the person) feed the input's suggestions, as Context's Ask has them.
      glossary = (Array.isArray(body?.glossary) ? body.glossary : [])
        .filter((g) => g && typeof g.term === "string" && g.term.trim())
        .map((g) => ({ term: g.term, units: text(g.units) }));
    }
  } catch {
    res.removeHeader("X-Portal-Notice");
    return res.status(502).json({ detail: `${settings.cerebroName} is unavailable right now. Try again shortly.` });
  }
  if (!response.ok) return relay(response, res);
  const reads = await readableUnits(kge, req, bearer);
  res.removeHeader("X-Portal-Notice");
  return res.json({ questions, ...(glossary.length ? { glossary } : {}), ...(reads ? { reads } : {}) });
}

// #607: the units the person reads (KGE's GET /readable-units as the person), which Context's answer card names in a
// refusal's way forward; undefined where KGE does not say, and the list goes out without them.
async function readableUnits(kge, req, bearer) {
  try {
    const response = await kge.request(req, "/readable-units", { trusted: false, headers: bearer });
    const body = response.ok ? await response.json() : undefined;
    return Array.isArray(body?.units) ? body.units.filter((unit) => typeof unit === "string" && unit) : undefined;
  } catch {
    return undefined;
  }
}

// The person's own KGE bearer, as x-kge-authorization, when they sent one.
export function personBearer(req) {
  return /^bearer\s+\S/i.test(req.headers.authorization || "") ? { "x-kge-authorization": req.headers.authorization } : {};
}

// A request body forwarded as sent (the route is mounted before express.json()).
export function rawBody(req) {
  const sendsBody = Number(req.headers["content-length"] || 0) > 0 || Boolean(req.headers["transfer-encoding"]);
  return { method: "POST", ...(sendsBody ? { body: req, duplex: "half" } : {}) };
}
