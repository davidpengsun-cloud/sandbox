import { relay, tooManyRequests } from "./kge.js";
import { localKnowledge, knowledgeProvider, unreachable } from "./knowledge.js";

const unavailable = (name) => ({
  enabled: false,
  route: async (_req, res) => res.status(503).type("text/plain").send(`${name} is unavailable without KGE.\n`),
});

export async function dataCall(call) {
  try {
    const result = await call();
    if (result?.status >= 500) throw Object.assign(new Error("KGE is unavailable."), { status: 503 });
    return result;
  } catch (error) {
    if (unreachable(error)) throw Object.assign(new Error("KGE is unavailable."), { status: 503 });
    throw error;
  }
}

const kgeProvider = (kge) => ({
  enabled: true,
  route: async (req, res, path, body) => {
    res.removeHeader("X-Portal-Notice");
    return dataCall(async () => {
      const response = await dataCall(() => kge.request(req, path, body === undefined ? {} : { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }));
      if (response.status === 429) return tooManyRequests(res, response.headers.get("retry-after"));
      return relay(response, res);
    });
  },
});

function choice(env, setting, catalogueProvider, kge) {
  const value = env[setting] || (catalogueProvider === "kge" ? "kge" : "none");
  if (!new Set(["kge", "none"]).has(value)) throw new Error(`${setting} must be kge or none`);
  if (value === "kge" && !env.KGE_URL && !kge) throw new Error(`${setting}=kge requires KGE_URL`);
  return value;
}

export function openProviders(env, kge, catalogueProvider, repository) {
  const dataName = choice(env, "PORTAL_DATA_PROVIDER", kge ? "kge" : catalogueProvider, kge);
  const local = localKnowledge(repository);
  const select = (setting) => {
    const name = env[setting] || (kge ? "kge" : "portal");
    if (!["portal", "kge", "none"].includes(name)) throw new Error(`${setting} must be portal or kge (none is a legacy alias for portal)`);
    if (name === "kge" && !kge) throw new Error(`${setting}=kge requires KGE_URL`);
    return name === "kge" ? kge : undefined;
  };
  return {
    data: dataName === "kge" ? kgeProvider(kge) : unavailable("Portal data access"),
    ask: knowledgeProvider("ask", local, select("PORTAL_ASK_PROVIDER")),
    search: knowledgeProvider("search", local, select("PORTAL_SEARCH_PROVIDER")),
  };
}
