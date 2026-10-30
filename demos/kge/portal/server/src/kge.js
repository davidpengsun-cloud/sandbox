import { noServiceAuth } from "./kgeauth.js";

function headers(req) {
  const value = { accept: "application/json" };
  if (req.headers.cookie) value.cookie = req.headers.cookie;
  if (req.headers.authorization) value.authorization = req.headers.authorization;
  if (req.headers["content-type"]) value["content-type"] = req.headers["content-type"];
  return value;
}

export function kgeClient(baseUrl, serviceAuth = noServiceAuth, portalSyncToken = "", env = {}) {
  const timeout = (name, fallback) => {
    const seconds = Number(env[name] || fallback);
    if (!Number.isInteger(seconds) || seconds <= 0 || seconds >= 300) throw new Error(`${name} must be a whole number of seconds between 1 and 299`);
    return seconds * 1000;
  };
  const identityTimeout = timeout("PORTAL_KGE_TIMEOUT", 30);
  const searchTimeout = timeout("PORTAL_SEARCH_TIMEOUT", 30);
  const askTimeout = timeout("PORTAL_ASK_TIMEOUT", 285);
  const syncHeaders = portalSyncToken ? { "x-portal-sync-token": portalSyncToken } : {};
  async function request(req, path, options = {}) {
    const service = await serviceAuth.headers();
    const budget = path === "/ask" ? askTimeout : path === "/search" ? searchTimeout : identityTimeout;
    return fetch(new URL(path, baseUrl), { signal: AbortSignal.timeout(budget), ...options, headers: { ...headers(req), ...options.headers, ...service }, redirect: "manual" });
  }
  async function json(req, path) {
    const response = await request(req, path);
    if (!response.ok) {
      const error = new Error(`KGE returned ${response.status}`);
      error.status = response.status;
      error.retryAfter = response.headers.get("retry-after");
      throw error;
    }
    return response.json();
  }
  return {
    request,
    profile: (req) => json(req, "/profile"),
    catalogueIdentity: async () => {
      const response = await request({ headers: {} }, "/catalogue/release", { headers: syncHeaders });
      if (!response.ok) throw Object.assign(new Error(`KGE returned ${response.status}`), { status: response.status });
      return response.json();
    },
    catalogue: async () => {
      const response = await request({ headers: {} }, "/catalogue/portal", { headers: syncHeaders });
      if (!response.ok) throw Object.assign(new Error(`KGE returned ${response.status}`), { status: response.status });
      return response.json();
    },
    identity: async (req) => {
      const [who, readable] = await Promise.all([json(req, "/whoami"), json(req, "/readable-units")]);
      if (typeof who.id !== "string" || !who.id) throw Object.assign(new Error("KGE named no principal"), { status: 401 });
      return { principal: who.id, units: Array.isArray(readable.units) ? readable.units : [], who };
    },
  };
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
