import assert from "node:assert/strict";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openIdentity } from "../src/identity.js";
import { catalogue } from "./fixtures/catalogue.js";
import { localCredentials, password } from "./fixtures/identity.js";

async function setup(context, options = {}) {
  const directory = await mkdtemp(join(tmpdir(), "portal-identity-"));
  const env = { ...await localCredentials(directory), PORTAL_SQLITE_PATH: ":memory:" };
  const { app, repository } = createApp({ env, catalogueSync: false, ...options });
  await repository.syncCatalogue(catalogue.release, catalogue.products);
  const server = app.listen(0, "127.0.0.1");
  await new Promise((resolve) => server.once("listening", resolve));
  context.after(() => { server.close(); repository.close(); });
  const request = (path, options = {}) => fetch(`http://127.0.0.1:${server.address().port}${path}`, { ...options, headers: { "content-type": "application/json", ...options.headers } });
  const signIn = (username = "analyst_retail", supplied = password, headers = {}) => request("/session", { method: "POST", headers, body: JSON.stringify({ username, password: supplied }) });
  return { request, signIn, env };
}

test("standalone passwords, session, units and roles ignore identity headers", async (context) => {
  const { request, signIn } = await setup(context);
  for (const path of ["/whoami", "/readable-units", "/api/products", "/getFavourites", "/getPublishedStatus"]) {
    assert.equal((await request(path, { headers: { "X-Portal-Principal": "admin" } })).status, 401, path);
  }
  for (const name of ["admin", "missing", "machine"]) {
    const refused = await signIn(name, name === "machine" ? password : "wrong");
    assert.equal(refused.status, 401);
    assert.equal(refused.headers.getSetCookie().length, 0);
    assert.deepEqual(await refused.json(), { detail: "wrong username or password" });
  }
  const signed = await signIn();
  assert.equal(signed.status, 200);
  const setCookie = signed.headers.getSetCookie()[0];
  assert.match(setCookie, /HttpOnly/);
  assert.match(setCookie, /Secure/);
  assert.match(setCookie, /SameSite=Lax/);
  const cookie = setCookie.split(";")[0];
  const headers = { cookie, "X-Portal-Principal": "admin" };
  assert.equal((await (await request("/whoami", { headers })).json()).id, "analyst_retail");
  assert.deepEqual(await (await request("/readable-units", { headers })).json(), { units: ["retail"] });
  assert.deepEqual(await (await request("/api/role", { headers })).json(), { role: "viewer" });
  const products = await (await request("/api/products", { headers })).json();
  assert.ok(products.length > 0 && products.every((p) => p.unit_id === "retail"));
  assert.equal((await request("/api/products/wealth-holdings", { headers })).status, 404);
  assert.equal((await request("/getPublishedStatus", { headers })).status, 403);
  assert.equal((await request("/whoami", { headers: { cookie: `${cookie}forged` } })).status, 401);
  assert.equal((await request("/session", { method: "DELETE", headers })).status, 200);
  assert.equal((await request("/whoami", { headers })).status, 401);
});

test("standalone sessions expire and credential removal revokes access", async (context) => {
  let now = 1000;
  const { request, signIn, env } = await setup(context, { clock: () => now });
  const first = await signIn();
  const headers = { cookie: first.headers.getSetCookie()[0].split(";")[0] };
  now += 12 * 3600 * 1000;
  assert.equal((await request("/whoami", { headers })).status, 401);
  const next = await signIn();
  headers.cookie = next.headers.getSetCookie()[0].split(";")[0];
  await writeFile(env.PORTAL_PASSWORDS_FILE, "{}");
  assert.equal((await request("/whoami", { headers })).status, 401);
});

test("standalone file changes narrow existing sessions and roles never grant extra units", async (context) => {
  const { request, signIn, env } = await setup(context);
  const signed = await signIn("admin");
  const headers = { cookie: signed.headers.getSetCookie()[0].split(";")[0] };
  assert.deepEqual(await (await request("/api/role", { headers })).json(), { role: "admin" });
  assert.equal((await request("/api/products/retail-accounts", { headers })).status, 404);
  const people = JSON.parse(await readFile(env.PORTAL_IDENTITIES_FILE, "utf8"));
  people.find((p) => p.principal === "admin").reads = [];
  await writeFile(env.PORTAL_IDENTITIES_FILE, JSON.stringify(people));
  assert.deepEqual(await (await request("/api/products", { headers })).json(), []);
});

test("standalone sign-in limits address-principal pairs and refuses extra identity fields", async (context) => {
  const { request, signIn } = await setup(context);
  const malformed = await request("/session", { method: "POST", body: '{"password":' });
  assert.equal(malformed.status, 400);
  assert.deepEqual(await malformed.json(), { detail: "Invalid request body." });
  assert.equal((await request("/session", { method: "POST", body: JSON.stringify({ username: "admin", password, principal: "admin" }) })).status, 400);
  for (let n = 0; n < 5; n++) assert.equal((await signIn("admin", "wrong", { "x-forwarded-for": `192.0.2.${n}` })).status, 401);
  const blocked = await signIn("admin", "wrong");
  assert.equal(blocked.status, 429);
  assert.ok(Number(blocked.headers.get("retry-after")) > 0);
  assert.equal((await signIn("analyst_retail")).status, 200);
  assert.equal((await signIn("admin")).status, 429);
});


test("standalone startup refuses absent credentials, plaintext hashes and a weak secret", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-settings-"));
  const env = await localCredentials(directory);
  assert.throws(() => openIdentity({ ...env, PORTAL_SESSION_SECRET: "short" }), /PORTAL_SESSION_SECRET/);
  assert.throws(() => openIdentity({ ...env, PORTAL_IDENTITIES_FILE: "" }), /PORTAL_IDENTITIES_FILE/);
  await writeFile(env.PORTAL_PASSWORDS_FILE, JSON.stringify({ admin: password }));
  assert.throws(() => openIdentity(env), /scrypt password hashes/);
});

test("standalone credential generator writes private compatible credentials and never overwrites them", async () => {
  const directory = await mkdtemp(join(tmpdir(), "portal-provision-"));
  const principals = [{ principal: "reader", role: "Reader", reads: ["retail"], processes: ["querying"] }];
  await writeFile(join(directory, "principals.json"), JSON.stringify(principals));
  const script = fileURLToPath(new URL("../src/passwords.js", import.meta.url));
  const run = promisify(execFile);
  const result = await run(process.execPath, [script, directory]);
  const plain = (await readFile(join(directory, "passwords.txt"), "utf8")).trim().split(" ")[1];
  const secret = (await readFile(join(directory, "session_secret"), "utf8")).trim();
  assert.ok(!result.stdout.includes(plain) && !result.stdout.includes(secret));
  for (const name of ["passwords.json", "passwords.txt", "session_secret"]) assert.equal((await stat(join(directory, name))).mode & 0o777, 0o600);
  const identity = openIdentity({ PORTAL_IDENTITIES_FILE: join(directory, "principals.json"), PORTAL_PASSWORDS_FILE: join(directory, "passwords.json"), PORTAL_SESSION_SECRET: secret });
  let session;
  const response = { cookie: (_name, value) => { session = value; }, json: (value) => value };
  assert.deepEqual(await identity.signIn({ headers: {}, body: { username: "reader", password: plain } }, response), { principal: "reader" });
  assert.deepEqual(identity.identity({ headers: { cookie: `portal_session=${session}` } }), { principal: "reader", units: ["retail"] });
  const restarted = openIdentity({ PORTAL_IDENTITIES_FILE: join(directory, "principals.json"), PORTAL_PASSWORDS_FILE: join(directory, "passwords.json"), PORTAL_SESSION_SECRET: secret });
  assert.throws(() => restarted.identity({ headers: { cookie: `portal_session=${session}` } }), /Sign in required/);
  await assert.rejects(run(process.execPath, [script, directory]), /Could not create credentials/);
  assert.equal((await readFile(join(directory, "session_secret"), "utf8")).trim(), secret);
});
