import assert from "node:assert/strict";
import test from "node:test";
import { kgeClient } from "../src/kge.js";
import { knowledgeProvider } from "../src/knowledge.js";

for (const [label, env, expected] of [
  ["defaults", {}, [30000, 30000, 30000, 30000, 285000]],
  ["overrides", { PORTAL_KGE_TIMEOUT: "45", PORTAL_SEARCH_TIMEOUT: "60", PORTAL_ASK_TIMEOUT: "290" }, [45000, 45000, 45000, 60000, 290000]],
]) test(`KGE identity, session, Search and Ask use their timeout ${label}`, async (context) => {
  const seen = [];
  context.mock.method(AbortSignal, "timeout", (ms) => { seen.push(ms); return new AbortController().signal; });
  context.mock.method(globalThis, "fetch", async (url) => Response.json(url.pathname === "/whoami" ? { id: "person" }
    : url.pathname === "/readable-units" ? { units: ["retail"] }
    : url.pathname === "/search" ? { hits: [] } : { answer_text: "Catalogue answer" }));
  const kge = kgeClient("https://kge.invalid", undefined, "", env);
  const req = { headers: {}, portalIdentity: { units: ["retail"] } };
  await kge.identity(req);
  await kge.request(req, "/session", { method: "POST", body: "{}" });
  for (const kind of ["search", "ask"]) {
    const provider = knowledgeProvider(kind, {}, kge);
    const response = await provider.run(req, { query: "accounts", question: "Describe accounts" });
    assert.equal(response.provider, "kge");
  }
  assert.deepEqual(seen, expected);
});

test("invalid timeout settings fail before serving requests", () => {
  for (const name of ["PORTAL_KGE_TIMEOUT", "PORTAL_SEARCH_TIMEOUT", "PORTAL_ASK_TIMEOUT"]) {
    for (const value of ["0", "-1", "300", "1.5", "bad", " "]) {
      assert.throws(() => kgeClient("https://kge.invalid", undefined, "", { [name]: value }), new RegExp(name));
    }
    assert.doesNotThrow(() => kgeClient("https://kge.invalid", undefined, "", { [name]: "" }));
  }
});
