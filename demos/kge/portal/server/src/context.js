import http from "node:http";
import https from "node:https";
import { Readable } from "node:stream";
import { posix } from "node:path";
import { CONTEXT_ASK_SUBTITLE, CONTEXT_ASK_TITLE, PRODUCT_NAME, notSetUp } from "./apps.js";
import { clientAddress } from "./throttle.js";

const RELAYED = ["content-type", "retry-after", "content-disposition", "cache-control", "x-accel-buffering"];

// Node's fetch replaces Host; request() preserves the loopback override.
function loopbackRequest(url, { method, headers, body, signal }) {
  return new Promise((resolve, reject) => {
    const upstream = (url.protocol === "https:" ? https : http).request(url, { method, headers, signal }, (incoming) => {
      const headers = new Headers();
      for (let i = 0; i < incoming.rawHeaders.length; i += 2) headers.append(incoming.rawHeaders[i], incoming.rawHeaders[i + 1]);
      const empty = method === "HEAD" || [204, 205, 304].includes(incoming.statusCode);
      if (empty) incoming.resume();
      resolve(new Response(empty ? null : Readable.toWeb(incoming), { status: incoming.statusCode, headers }));
    });
    upstream.on("error", reject);
    if (body) {
      body.once("error", (error) => upstream.destroy(error));
      body.pipe(upstream);
    } else {
      upstream.end();
    }
  });
}

function guideLink(value, baseUrl) {
  if (/^https?:\/\//i.test(value)) {
    const target = new URL(value);
    if (target.hostname !== "context.portal.invalid" && (!baseUrl || target.origin !== new URL(baseUrl).origin)) return value;
    value = target.pathname + target.search + target.hash;
  }
  return value.replace(/^\/(onboarding-guide|docs|redoc|openapi.json)(?=[/?#]|$)/, "/api/context/$1");
}

function guideHtml(html, productName, serviceUrl) {
  const escape = (value) => value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const apiUrl = serviceUrl ? new URL(serviceUrl).href.replace(/\/+$/, "") : "The API address is not set up here.";
  const mcpUrl = serviceUrl ? new URL("mcp", `${apiUrl}/`).href : "The MCP address is not set up here.";
  const config = serviceUrl ? JSON.stringify({ mcpServers: { context: { type: "http", url: mcpUrl, headers: { Authorization: "Bearer <access_token>" } } } }, null, 2) : "The MCP address is not set up here.";
  const title = productName.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  return html
    .replace(/(<code id="connection-api">)[\s\S]*?(<\/code>)/g, (_match, open, close) => `${open}${escape(apiUrl)}${close}`)
    .replace(/(<code id="connection-mcp">)[\s\S]*?(<\/code>)/g, (_match, open, close) => `${open}${escape(mcpUrl)}${close}`)
    .replace(/(<pre id="connection-config">)[\s\S]*?(<\/pre>)/g, (_match, open, close) => `${open}${escape(config)}${close}`)
    .replace(/(<title>)[\s\S]*?(<\/title>)/i, (_match, open, close) => `${open}${title} — Docs${close}`)
    .replace(/governed dataset/gi, (text) => text[0] === "G" ? "Curated dataset" : "curated dataset")
    .replace(/(["'])\/(onboarding-guide|docs|redoc|openapi.json)(?=[/?#"'])/g, "$1/api/context/$2")
    .replace(/href="[^"]*" target="_top"/, 'href="/context" target="_top"');
}

export const UNAVAILABLE = "Context is unavailable right now. Try again shortly.";
const BOOTSTRAP = { person: "/whoami", profile: "/profile", mode: "/mode", presets: "/presets", build: "/health" };

// The BFF composes the opening screen at /bootstrap from KGE and the Ask header's
// title and subtitle from the portal's settings. Its task resources go
// to KGE unchanged: method, query and the raw request body, with the person's cookie or bearer token and the Cloud Run
// ingress token that kge.request() adds. Only credential POSTs carry the portal's trusted caller
// token and client address for sign-in limits. KGE decides who may do what; the portal does
// not. Mounted before express.json(), so a large upload is streamed, not parsed.
export function contextProxy(kge, baseUrl, productName = PRODUCT_NAME, { tablePreview = false, tableQuery = false, contextAskTitle = CONTEXT_ASK_TITLE, contextAskSubtitle = CONTEXT_ASK_SUBTITLE, env = process.env } = {}) {
  const hostname = baseUrl ? new URL(baseUrl).hostname : "";
  const loopback = hostname === "localhost" || hostname === "[::1]" || /^127\.\d+\.\d+\.\d+$/.test(hostname);
  // Never lend a loopback peer and Host to KGE's demo-only routes.
  const proxyHeaders = loopback ? { host: "context.portal.invalid" } : {};
  return async (req, res, next) => {
    if (!kge) return notSetUp(res, "context");
    const rest = req.url.replace(/^[/\\]+/, "");
    const path = `/${rest}`;
    if (!rest || rest.startsWith("?") || rest.split("?")[0].includes("\\")) return res.status(404).json({ detail: "Not found." });
    let route = path.split(/[?#]/)[0];
    try {
      route = decodeURIComponent(route);
    } catch (error) {
      if (!(error instanceof URIError)) throw error;
    }
    route = posix.normalize(route.replace(/\\/g, "/")).replace(/\/+$/, "").toLowerCase();
    // A loopback proxy must not lend its peer address to demo sign-in.
    if (route === "/session/demo") return res.status(404).json({ detail: "Demo sign-in is unavailable through the portal. Sign in with your credentials." });
    if (["/data/preview", "/data/export"].includes(route) && !tablePreview) return res.status(404).json({ message: "Table previews are not enabled." });
    if (route === "/data/query" && !tableQuery) return res.status(404).json({ message: "Table queries are not enabled." });
    const method = req.method;
    const signIn = method === "POST" && ["/session", "/session/token"].includes(path.split(/[?#]/)[0]);
    const sendsBody = !["GET", "HEAD"].includes(method) && (Number(req.headers["content-length"] || 0) > 0 || Boolean(req.headers["transfer-encoding"]));
    const controller = new AbortController();
    const request = (target, options = {}) => kge.request(req, target, {
      long: true, trusted: signIn, signal: controller.signal,
      headers: { accept: req.headers.accept || "*/*", ...proxyHeaders, ...(signIn ? { "x-client-address": clientAddress(req, env) } : {}) },
      ...(loopback ? { transport: loopbackRequest } : {}),
      ...options,
    });
    let clientClosed = false;
    let upstream;
    res.once?.("close", () => {
      if (!res.writableFinished) {
        clientClosed = true;
        controller.abort();
        upstream?.destroy();
      }
    });
    let response;
    try {
      if (route === "/bootstrap" && method === "GET") {
        const responses = await Promise.all(Object.entries(BOOTSTRAP).map(async ([key, target]) => [key, await request(target)]));
        const refused = responses.find(([, value]) => !value.ok);
        if (refused) {
          response = refused[1];
          await Promise.allSettled(responses.filter(([, value]) => value !== response).map(([, value]) => value.body?.cancel()));
        }
        else {
          const values = await Promise.all(responses.map(async ([key, value]) => [key, await value.json()]));
          for (const [, value] of responses) for (const cookie of value.headers.getSetCookie()) res.append("set-cookie", cookie);
          res.removeHeader("X-Portal-Notice");
          return res.set("Cache-Control", "no-store").json({ ...Object.fromEntries(values), ask: { title: contextAskTitle, subtitle: contextAskSubtitle } });
        }
      } else response = await request(route === "/healthz" ? `/health${path.slice(path.split(/[?#]/)[0].length)}` : path, {
        method,
        ...(sendsBody ? { body: req, duplex: "half" } : {}),
      });
    } catch (error) {
      if (clientClosed) return;
      return res.status(502).json({ detail: UNAVAILABLE });
    }
    res.removeHeader("X-Portal-Notice");
    res.status(response.status);
    for (const name of RELAYED) {
      const value = response.headers.get(name);
      if (value) res.set(name, value);
    }
    for (const cookie of response.headers.getSetCookie()) res.append("set-cookie", cookie);
    const guide = ["/onboarding-guide", "/docs", "/redoc", "/docs/oauth2-redirect"].includes(route);
    if (guide && response.headers.has("location")) res.set("location", guideLink(response.headers.get("location"), baseUrl));
    if (!response.body || method === "HEAD") return res.end();
    if (route === "/openapi.json" && response.ok) {
      try {
        const schema = await response.json();
        return res.json({ ...schema, servers: [{ url: "/api/context" }] });
      } catch {
        return res.status(502).set("content-type", "application/json").json({ detail: UNAVAILABLE });
      }
    }
    if (guide && response.ok && /^text\/html\b/i.test(response.headers.get("content-type") || "")) {
      try {
        return res.send(guideHtml(await response.text(), productName, env.KGE_PUBLIC_URL || baseUrl));
      } catch {
        return res.status(502).set("content-type", "application/json").json({ detail: UNAVAILABLE });
      }
    }
    upstream = Readable.fromWeb(response.body);
    if (clientClosed || res.destroyed) return upstream.destroy();
    upstream.on("error", () => res.destroy()).pipe(res);
  };
}

export function contextPage(on) {
  return (req, res) => {
    if (!on) return notSetUp(res, "context");
    if (req.path !== "/" || !["GET", "HEAD"].includes(req.method)) return res.status(404).json({ detail: "Not found." });
    return res.redirect(302, "/context");
  };
}
