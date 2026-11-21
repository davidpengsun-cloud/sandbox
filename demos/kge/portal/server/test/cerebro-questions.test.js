import assert from "node:assert/strict";
import { createServer } from "node:http";
import { after, before, test } from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";

// A fake KGE whose GET /golden answers as KGE does: the older golden set, which names no golden-20 question, and the
// sage set, which holds the golden 20 and other questions besides, each question of a unit the caller may not read
// already left out. The cookie's last part names the person: a steward reads every unit, an analyst reads wealth but
// not the unit of the golden 20's last five, a business user reads nothing.
const OLDER = Array.from({ length: 7 }, (_, i) => `Older golden question ${i + 1}?`);
const GOLDEN20 = Array.from({ length: 20 }, (_, i) => `Golden 20 question S${String(i + 6).padStart(2, "0")}?`);
const READS = { steward: GOLDEN20, analyst: GOLDEN20.slice(0, 15), reader: [] };
const seen = [];
let golden = { status: 200 };
let readable = { status: 200, units: [] };
const kge = createServer((req, res) => {
  const send = (status, value) => res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(value));
  const who = (req.headers.cookie || "").split("=").pop();
  if (req.url === "/whoami") return send(200, { id: who, cerebro_roles: who === "nobody" ? [] : [who === "reader" ? "business" : "steward"] });
  if (req.url === "/readable-units") return readable.status === 200 ? send(200, { units: readable.units }) : send(readable.status, { detail: "No." });
  seen.push({ url: req.url, headers: req.headers });
  if (req.url === "/golden") {
    if (golden.status !== 200) return send(golden.status, { detail: "Sign in." });
    if (golden.raw !== undefined) return res.writeHead(200, { "content-type": "text/plain" }).end(golden.raw);
    if (golden.body) return send(200, golden.body);
    const older = who === "reader" ? [] : OLDER;
    const golden20 = READS[who] || [];
    const sage = [...golden20, ...(who === "reader" ? [] : ["A sage question outside the golden 20?"])];
    return send(200, { sets: [{ name: "golden", version: "3", questions: older, samples: older, misses: [], golden20: [] }, { name: "sage", version: "1", questions: sage, samples: sage, misses: [], golden20 }], question_units: { [OLDER[0]]: ["retail"] }, glossary: [] });
  }
  return send(404, { detail: "Not found." });
});
const service = { headers: async () => ({ "x-serverless-authorization": "Bearer service-token", "x-caller-token": "portal-caller-token" }) };
const as = (who) => ({ cookie: `kge_session_meridian=${who}`, authorization: "Bearer person-token" });
let kgeUrl;
const servers = [];

before(async () => {
  await new Promise((resolve) => kge.listen(0, "127.0.0.1", resolve));
  kgeUrl = `http://127.0.0.1:${kge.address().port}`;
});
after(() => { kge.close(); for (const server of servers) server.close(); });

async function start(flags = {}) {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const { app } = createApp({ repository, env: { KGE_URL: kgeUrl, PORTAL_SYNC_TOKEN: "s".repeat(32), ...flags }, serviceAuth: service, catalogueSync: false, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise((resolve) => server.once("listening", resolve));
  return `http://127.0.0.1:${server.address().port}`;
}

test("GET /api/cerebro/questions answers exactly the golden 20, read from KGE's /golden as the person", async () => {
  const base = await start();
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { questions: GOLDEN20, reads: [] }, "neither the older golden set nor the sage set's other questions");
  assert.equal(seen.length, 1);
  assert.equal(seen[0].url, "/golden");
  assert.equal(seen[0].headers["x-kge-authorization"], "Bearer person-token");
  assert.equal(seen[0].headers["x-caller-token"], undefined, "never the portal's trusted caller token");
});

test("the list is exactly the golden-20 questions KGE serves the person, those on units they may read", async () => {
  const base = await start();
  const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("analyst") });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { questions: GOLDEN20.slice(0, 15), reads: [] });
});

test("a person KGE gives no readable golden question gets an empty list", async () => {
  const base = await start();
  const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("reader") });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { questions: [], reads: [] });
});

test("no golden 20, or one with no text, is an empty list; a refusal passes through; a reply that is not JSON is a 502", async () => {
  const base = await start();
  try {
    const none = [{ sets: [] }, {}, { sets: [null, { name: "golden", questions: ["Not golden 20?"], samples: ["Not golden 20?"] }] }, { sets: [{ name: "sage", questions: ["Q?"], golden20: "Q?" }] }];
    for (const body of [...none, { sets: [{ name: "sage", questions: ["Kept?"], golden20: [" ", 7, null, "Kept?"] }] }]) {
      golden = { status: 200, body };
      const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
      assert.equal(response.status, 200);
      assert.deepEqual(await response.json(), { questions: none.includes(body) ? [] : ["Kept?"], reads: [] }, JSON.stringify(body));
    }
    golden = { status: 401 };
    let response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { detail: "Sign in." });
    golden = { status: 200, raw: "not json" };
    response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /Cerebro is unavailable/);
  } finally {
    golden = { status: 200 };
  }
});

test("the questions are refused when Cerebro is off, and never reach KGE", async () => {
  const base = await start({ PORTAL_APP_CEREBRO: "off" });
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
  assert.equal(response.status, 404);
  assert.equal(seen.length, 0);
});

test("questions the baseline answered come first and its known misses last", async () => {
  const base = await start();
  try {
    golden = { status: 200, body: { sets: [
      { name: "golden", questions: ["D?", "X?"], samples: ["D?", "X?"], misses: [], golden20: [] },
      { name: "sage", questions: ["A?", "B?", "C?", "D?", "E?"], samples: ["C?", "B?", "E?"], misses: ["A?"], golden20: ["A?", "B?", "C?", "D?"] },
    ] } };
    const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("steward") });
    assert.deepEqual(await response.json(), { questions: ["C?", "B?", "D?", "A?"], reads: [] }, "another set's sample does not move a golden-20 question");
  } finally {
    golden = { status: 200 };
  }
});

test("a signed-in person with no Cerebro role is refused before KGE's /golden", async () => {
  const base = await start();
  seen.length = 0;
  const response = await fetch(`${base}/api/cerebro/questions`, { headers: as("nobody") });
  assert.equal(response.status, 403);
  assert.equal(seen.filter((call) => call.url === "/golden").length, 0);
});

test("the list carries the person's glossary terms and readable units for the input and the card (#607)", async () => {
  const url = await start();
  try {
    golden = { status: 200, body: { sets: [{ name: "sage", questions: ["Q?"], golden20: ["Q?"] }], glossary: [{ term: "Net New Assets", units: ["wealth", 7] }, { term: " " }, null, { term: "Revenue" }] } };
    readable = { status: 200, units: ["wealth", "", 3, "retail"] };
    const response = await fetch(`${url}/api/cerebro/questions`, { headers: as("steward") });
    assert.deepEqual(await response.json(), { questions: ["Q?"], glossary: [{ term: "Net New Assets", units: ["wealth"] }, { term: "Revenue", units: [] }], reads: ["wealth", "retail"] });
  } finally {
    golden = { status: 200 };
    readable = { status: 200, units: [] };
  }
});
