import { notSetUp } from "../apps.js";
import { asPerson, rawBody } from "./graph.js";

// Curate's proposals, through the portal: GET /api/cerebro/steward/queue to KGE's
// GET /steward/queue (status=pending|decided, the only parameter forwarded), and
// POST /api/cerebro/steward/decide to KGE's POST /steward/decide, its body as sent.
// The gaps people hit: GET /gaps to KGE's GET /steward/gaps (steward only, no asker named).
// The candidate to review: GET /candidate to KGE's GET /onboard; POST /accept and
// /certify to KGE's /onboard/accept and /onboard/certify, their bodies as sent; and
// POST /sign-off to KGE's POST /onboard/run with step sign-off, the body built here
// from the candidate's hash alone, so no other step (release, rollback) runs from Curate.
// On when Cerebro's flag is on and KGE is behind the portal, whatever Context's flag.
// Every call runs as the signed-in person; KGE checks the steward's role
// (certification) and records the decision with their name.
const STATUSES = new Set(["pending", "decided"]);
const TRANSITIONS = { "/accept": "/onboard/accept", "/certify": "/onboard/certify" };
const HASH = /^[A-Za-z0-9_-]{1,128}$/;
const SIGN_OFF_LIMIT = 4096;

// The sign-off body: { hash } at most, read here because the route sits before express.json().
async function signOffBody(req) {
  let text = "";
  for await (const chunk of req) {
    text += chunk;
    if (text.length > SIGN_OFF_LIMIT) return null;
  }
  if (!text.trim()) return { step: "sign-off" };
  let sent;
  try { sent = JSON.parse(text); } catch { return null; }
  if (sent === null || typeof sent !== "object" || Array.isArray(sent)) return null;
  if (sent.hash === undefined) return { step: "sign-off" };
  return typeof sent.hash === "string" && HASH.test(sent.hash) ? { step: "sign-off", hash: sent.hash } : null;
}

export function cerebroSteward(kge, settings) {
  return async (req, res) => {
    if (!kge) return notSetUp(res, "cerebro", settings);
    if (req.method === "GET" && req.path === "/queue") {
      const status = req.query.status;
      if (status !== undefined && !(typeof status === "string" && STATUSES.has(status))) {
        return res.status(400).json({ detail: "status must be pending or decided." });
      }
      return asPerson(kge, settings, req, res, status ? `/steward/queue?status=${status}` : "/steward/queue");
    }
    if (req.method === "POST" && req.path === "/decide") return asPerson(kge, settings, req, res, "/steward/decide", rawBody(req));
    if (req.method === "GET" && req.path === "/gaps") return asPerson(kge, settings, req, res, "/steward/gaps");
    if (req.method === "GET" && req.path === "/candidate") return asPerson(kge, settings, req, res, "/onboard");
    if (req.method === "POST" && TRANSITIONS[req.path]) return asPerson(kge, settings, req, res, TRANSITIONS[req.path], rawBody(req));
    if (req.method === "POST" && req.path === "/sign-off") {
      const body = await signOffBody(req);
      if (!body) return res.status(400).json({ detail: "A sign-off names at most the candidate's hash." });
      return asPerson(kge, settings, req, res, "/onboard/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    }
    return res.status(404).json({ detail: "Not found." });
  };
}
