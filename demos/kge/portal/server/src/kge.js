import { context } from "@opentelemetry/api";
import { noServiceAuth } from "./kgeauth.js";
import { outboundHeaders } from "./outbound.js";
import { originOf, traceDestinations, traceHeaders, traceMetadata } from "./tracing.js";

// Trace context is the tracing port's to add (traceHeaders), never a caller's;
// no trace or Google client-info header leaves otherwise (outboundHeaders).

function headers(req) {
  const value = { accept: "application/json" };
  if (req.headers.cookie) value.cookie = req.headers.cookie;
  if (req.headers.authorization) value.authorization = req.headers.authorization;
  if (req.headers["content-type"]) value["content-type"] = req.headers["content-type"];
  return value;
}

export function kgeClient(baseUrl, serviceAuth = noServiceAuth, portalSyncToken = "", env = {}, { modelServiceBearer } = {}) {
  const timeout = (name, fallback) => {
    const seconds = Number(env[name] || fallback);
    if (!Number.isInteger(seconds) || seconds <= 0 || seconds >= 300) throw new Error(`${name} must be a whole number of seconds between 1 and 299`);
    return seconds * 1000;
  };
  const identityTimeout = timeout("PORTAL_KGE_TIMEOUT", 30);
  const searchTimeout = timeout("PORTAL_SEARCH_TIMEOUT", 30);
  const askTimeout = timeout("PORTAL_ASK_TIMEOUT", 285);
  const syncHeaders = portalSyncToken ? { "x-portal-sync-token": portalSyncToken } : {};
  const destinations = traceDestinations(env);
  const storedTracing = (env.TRACING ?? "").trim() === "store";
  if (storedTracing && env.TRACE_PROPAGATE_TO === undefined && originOf(baseUrl)) destinations.add(originOf(baseUrl));
  // `trusted: false` sends only the Cloud Run ingress token, not the trusted
  // caller token KGE gives the whole catalogue and sign-in addresses to.
  async function request(req, path, { long = false, trusted = true, transport = fetch, signal, deadline = Infinity, now = Date.now, ...options } = {}) {
    const service = await serviceAuth.headers();
    const traceProof = service["x-caller-token"] || portalSyncToken;
    if (!trusted) delete service["x-caller-token"];
    const configured = long || path === "/ask" || path === "/assist" ? askTimeout : path === "/search" ? searchTimeout : identityTimeout;
    const budget = Math.floor(Math.min(configured, deadline - now()));
    if (budget <= 0) throw new Error("Context ran out of time.");
    const timeoutSignal = AbortSignal.timeout(budget);
    const url = new URL(path, baseUrl);
    const merged = outboundHeaders({ ...headers(req), ...options.headers, ...service });
    // The question's span, when tracing is on and TRACE_PROPAGATE_TO lists KGE: KGE continues this trace.
    const trace = traceHeaders(url, req.traceContext ?? context.active(), destinations);
    const metadata = trace.traceparent && originOf(url) === originOf(baseUrl) ? traceMetadata(req.traceContext ?? context.active()) : {};
    if (traceProof && metadata["x-kge-trace-agent"] && originOf(url) === originOf(baseUrl)) metadata["x-kge-trace-proof"] = traceProof;
    return transport(url, { ...options, signal: signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal, headers: { ...merged, ...trace, ...metadata }, redirect: "manual" });
  }
  async function json(req, path) {
    const response = await request(req, path);
    if (!response.ok) {
      const error = new Error(`Context returned ${response.status}`);
      error.status = response.status;
      error.retryAfter = response.headers.get("retry-after");
      throw error;
    }
    return response.json();
  }
  return {
    request,
    // Only KGE sees the trusted portal token and this person's short-lived bearer. Never forward browser headers.
    modelChat: async (req, payload, { signal } = {}) => {
      const held = await sessionBearer(req, { signal });
      if (!req.modelQuestionToken) throw new Error("The model question context is unavailable.");
      return request({ headers: {}, traceContext: req.traceContext }, "/model/chat", {
        method: "POST", trusted: true, long: true, signal,
        headers: { ...syncHeaders, "x-kge-authorization": `Bearer ${held}`, "x-kge-question-token": req.modelQuestionToken,
          "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify(payload),
      });
    },
    modelQuestion: async (req, questionId, purpose, { signal, expectedModel } = {}) => {
      const held = await sessionBearer(req, { signal });
      const response = await request({ headers: {}, traceContext: req.traceContext }, "/model/question", {
        method: "POST", trusted: true, signal,
        headers: { ...syncHeaders, "x-kge-authorization": `Bearer ${held}`, "content-type": "application/json" },
        body: JSON.stringify({ question_id: questionId, conversation_id: req.modelConversationId ?? questionId, purpose,
          ...(expectedModel ? { expected_model: expectedModel } : {}) }),
      });
      if (!response.ok) throw Object.assign(new Error("The model question could not be started."), { status: response.status });
      const body = await response.json();
      if (body?.question_id !== questionId || typeof body.correlation_token !== "string" || !body.correlation_token || body.correlation_token.length > 8192) {
        throw new Error("The model question context is unavailable.");
      }
      return body.correlation_token;
    },
    modelUsage: async (req, { purpose = "agent", finish = false, signal } = {}) => {
      if (!req.modelQuestionId || !req.modelConversationId || !req.modelQuestionToken) throw new Error("The model question context is unavailable.");
      const held = await sessionBearer(req, { signal });
      const response = await request({ headers: {}, traceContext: req.traceContext }, finish ? "/model/usage/finish" : "/model/usage", {
        method: "POST", trusted: true, signal,
        headers: { ...syncHeaders, "x-kge-authorization": `Bearer ${held}`, "x-kge-question-token": req.modelQuestionToken,
          "content-type": "application/json" },
        body: JSON.stringify({ question_id: req.modelQuestionId, conversation_id: req.modelConversationId, purpose }),
      });
      if (!response.ok) throw Object.assign(new Error("Model usage is unavailable."), { status: response.status });
      return response.json();
    },
    modelReserve: async (req, { signal } = {}) => {
      if (!req.modelQuestionId || !req.modelConversationId || !req.modelQuestionToken) throw new Error("The model question context is unavailable.");
      const held = await sessionBearer(req, { signal });
      const response = await request({ headers: {}, traceContext: req.traceContext }, "/model/usage/reserve", {
        method: "POST", trusted: true, signal,
        headers: { ...syncHeaders, "x-kge-authorization": `Bearer ${held}`, "x-kge-question-token": req.modelQuestionToken, "content-type": "application/json" },
        body: JSON.stringify({ question_id: req.modelQuestionId, conversation_id: req.modelConversationId, purpose: "agent" }),
      });
      if (!response.ok) throw Object.assign(new Error("Model usage is unavailable."), { status: response.status });
      const body = await response.json();
      if (typeof body?.invocation_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(body.invocation_id)) throw new Error("Model usage is unavailable.");
      return body.invocation_id;
    },
    // Explicit server-only service context. Person-bound calls never consult this provider, even after a 401.
    modelService: async (purpose, { signal, traceContext, expectedModel } = {}) => {
      if (typeof modelServiceBearer !== "function" || !["agent", "catalogue"].includes(purpose)) throw new Error("The model service identity is unavailable.");
      const questionId = globalThis.crypto.randomUUID();
      const conversationId = questionId;
      const send = async (path, body, token, nextSignal) => {
        const bearer = await modelServiceBearer({ signal: nextSignal });
        if (typeof bearer !== "string" || !/^[\x21-\x7e]{1,4096}$/.test(bearer)) throw new Error("The model service identity is unavailable.");
        return request({ headers: {}, traceContext }, path, { method: "POST", trusted: true, long: path === "/model/chat", signal: nextSignal,
          headers: { ...syncHeaders, "x-kge-authorization": `Bearer ${bearer}`, ...(token ? { "x-kge-question-token": token } : {}),
            "content-type": "application/json", accept: path === "/model/chat" ? "text/event-stream" : "application/json" }, body: JSON.stringify(body) });
      };
      const identified = { question_id: questionId, conversation_id: conversationId, purpose, ...(expectedModel ? { expected_model: expectedModel } : {}) };
      const issued = await send("/model/question", identified, undefined, signal);
      if (!issued.ok) throw Object.assign(new Error("The model service identity is unavailable."), { status: issued.status });
      const data = await issued.json();
      if (data?.question_id !== questionId || typeof data.correlation_token !== "string" || !data.correlation_token || data.correlation_token.length > 8192) {
        throw new Error("The model service question is unavailable.");
      }
      const token = data.correlation_token;
      const req = Object.freeze({ modelQuestionId: questionId, modelConversationId: conversationId });
      const client = Object.freeze({
        modelChat: async (_req, body, options = {}) => {
          if (body?.question_id !== questionId || body?.conversation_id !== conversationId || body?.purpose !== purpose || body?.expected_model !== expectedModel) {
            throw new Error("The model service question does not match its context.");
          }
          return send("/model/chat", body, token, options.signal);
        },
        modelUsage: async (_req, { finish = false, signal: usageSignal } = {}) => {
          const response = await send(finish ? "/model/usage/finish" : "/model/usage", identified, token, usageSignal);
          if (!response.ok) throw Object.assign(new Error("Model usage is unavailable."), { status: response.status });
          return response.json();
        },
      });
      return Object.freeze({ kge: client, req });
    },
    profile: (req) => json(req, "/profile"),
    catalogueIdentity: async () => {
      const response = await request({ headers: {} }, "/catalogue/release", { headers: syncHeaders });
      if (!response.ok) throw Object.assign(new Error(`Context returned ${response.status}`), { status: response.status });
      return response.json();
    },
    catalogue: async () => {
      const response = await request({ headers: {} }, "/catalogue/portal", { headers: syncHeaders });
      if (!response.ok) throw Object.assign(new Error(`Context returned ${response.status}`), { status: response.status });
      return response.json();
    },
    // The signed-in person's own bearer for one agent turn (KGE POST /session/bearer, cerebro.md §4.4): their session
    // cookie in, and the portal proving itself as the trusted caller (x-caller-token, or the sync token on this machine
    // and in Docker Compose). The browser's own authorization header is never sent, so a bearer cannot renew itself.
    sessionBearer,
    identity: async (req) => {
      const [who, readable] = await Promise.all([json(req, "/whoami"), json(req, "/readable-units")]);
      if (typeof who.id !== "string" || !who.id) throw Object.assign(new Error("Context named no principal"), { status: 401 });
      return { principal: who.id, units: Array.isArray(readable.units) ? readable.units : [], who };
    },
  };
  async function sessionBearer(req, { signal } = {}) {
      const person = { headers: req.headers.cookie ? { cookie: req.headers.cookie } : {}, traceContext: req.traceContext };
      const response = await request(person, "/session/bearer", { method: "POST", signal, headers: syncHeaders });
      if (!response.ok) throw Object.assign(new Error(`Context returned ${response.status}`), { status: response.status });
      const token = (await response.json())?.access_token;
      if (typeof token !== "string" || !token) throw new Error("Context returned no bearer");
      return token;
  }
}

export async function relay(response, res) {
  res.removeHeader("X-Portal-Notice");
  res.status(response.status);
  for (const name of ["X-Export-Capped-At", "X-Export-Note"]) {
    const value = response.headers.get(name);
    if (value) res.set(name, value);
  }
  for (const cookie of response.headers.getSetCookie()) res.append("set-cookie", cookie);
  const contentType = response.headers.get("content-type");
  if (contentType) res.type(contentType);
  const disposition = response.headers.get("content-disposition");
  if (disposition) res.set("Content-Disposition", disposition);
  const retry = response.headers.get("retry-after");
  if (retry) res.set("Retry-After", retry);
  res.send(Buffer.from(await response.arrayBuffer()));
}

export function tooManyRequests(res, retryAfter, field = "detail") {
  res.removeHeader("X-Portal-Notice");
  if (retryAfter) res.set("Retry-After", retryAfter);
  return res.status(429).json({ [field]: "Too many requests at once; try again in a moment." });
}
