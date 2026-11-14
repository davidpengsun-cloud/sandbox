import assert from "node:assert/strict";
import test from "node:test";
import { createApp } from "../src/app.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";
import { catalogue } from "./fixtures/catalogue.js";

test("portal admin flow, auditing and next-request capabilities use the shared session", async (context) => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(catalogue.release, catalogue.products);
  let admin = true;
  const kge = { identity: async (req) => {
    const principal = req.headers.cookie === "session=operator" ? "operator@example.com" : "person@example.com";
    return { principal, units: ["retail"], who: { id: principal, name: "Display name", roles: ["reader"], admin: principal === "operator@example.com" && admin } };
  } };
  const { app } = createApp({ repository, kge, catalogueSync: false });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((resolve, reject) => { server.once("listening", resolve); server.once("error", reject); });
  context.after(() => { server.close(); repository.close(); });
  const request = (path, method = "GET", body, operator = true) => fetch(`http://127.0.0.1:${server.address().port}${path}`, {
    method, headers: { cookie: operator ? "session=operator" : "session=person", "content-type": "application/json" }, ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const membership = (role) => `/api/admin/users/person%40example.com/roles/${role}`;
  assert.equal((await (await request("/whoami")).json()).name, "Display name");
  assert.equal((await request("/api/admin/users")).status, 200);
  assert.equal((await request("/api/admin/users", "GET", undefined, false)).status, 403);
  assert.equal((await request(membership("admin"), "PUT", undefined, false)).status, 403);
  assert.equal((await request(membership("owner"), "PUT")).status, 400);
  assert.equal((await request(membership("publisher"), "PUT", { principal: "operator@example.com" })).status, 400);
  const publish = () => request("/api/admin/products/retail-accounts/published", "PUT", { published: true }, false);
  assert.equal((await publish()).status, 403);
  assert.equal((await request(membership("publisher"), "PUT")).status, 200);
  assert.equal((await publish()).status, 204);
  assert.equal((await request("/api/admin/products/wealth-holdings/published", "PUT", { published: true }, false)).status, 404);
  assert.equal((await request(membership("approver"), "PUT")).status, 200);
  await request("/api/requests", "POST", { productId: "retail-accounts" }, false);
  const pending = await (await request("/api/admin/requests", "GET", undefined, false)).json();
  assert.equal(pending.length, 1);
  assert.equal((await request(`/api/admin/requests/${pending[0].id}/decision`, "POST", { decision: "approve" }, false)).status, 200);
  assert.equal((await request(membership("publisher"), "DELETE")).status, 200);
  assert.equal((await publish()).status, 403);
  assert.equal((await request(membership("approver"), "DELETE")).status, 200);
  assert.equal((await request("/api/admin/requests", "GET", undefined, false)).status, 403);
  const audit = (await (await request("/api/admin/users/audit")).json()).audit;
  assert.equal(audit.length, 4);
  assert.ok(audit.every((entry) => entry.by === "operator@example.com" && Date.parse(entry.at)));
  assert.equal((await request("/api/admin/users/audit", "GET", undefined, false)).status, 403);
  assert.equal((await request("/api/admin/users/%20Person%40EXAMPLE.com%20/roles/publisher", "PUT")).status, 200);
  assert.equal((await publish()).status, 204, "an operator-typed variant of the name reaches KGE's id person@example.com on the next request");
  assert.equal((await request(membership("publisher"), "DELETE")).status, 200);
  assert.equal((await publish()).status, 403);
  await request(membership("admin"), "PUT");
  assert.equal((await request("/api/admin/users", "GET", undefined, false)).status, 200);
  await request(membership("admin"), "DELETE");
  assert.equal((await request("/api/admin/users", "GET", undefined, false)).status, 403);
  admin = false;
  assert.equal((await request("/api/admin/users")).status, 403);
});

test("sign-in relays the typed username to KGE unchanged and throttles on its normalised form", async (context) => {
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  const relayed = [], keys = [];
  const kge = {
    identity: async () => ({ principal: "Legacy Principal", units: [], who: { id: "Legacy Principal", name: "Legacy" } }),
    request: async (_req, path, options) => {
      const body = JSON.parse(options.body);
      relayed.push({ path, body });
      return body.username.length > 128 ? Response.json({ detail: "invalid" }, { status: 422 }) : Response.json({ detail: "wrong username or password" }, { status: 401 });
    },
  };
  const failures = [];
  const throttle = { attempt: (key) => { keys.push(key); return 0; }, failed: (key) => { failures.push(key); return 0; }, passed: () => {} };
  const { app } = createApp({ repository, kge, catalogueSync: false, throttle });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((resolve, reject) => { server.once("listening", resolve); server.once("error", reject); });
  context.after(() => { server.close(); repository.close(); });
  const url = (path) => `http://127.0.0.1:${server.address().port}${path}`;
  const typed = [" ADMIN.Meridian@Example.com ", "admin.meridian@example.com", "ＡＤＭＩＮ.meridian@example.com", "a b"];
  for (const username of typed) {
    const response = await fetch(url("/session"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username, password: "example" }) });
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { detail: "wrong username or password" });
  }
  assert.deepEqual(relayed.map(({ path, body }) => [path, body.username]), typed.map((username) => ["/session", username]));
  assert.deepEqual(keys.map((key) => JSON.parse(key)[1]), ["admin.meridian@example.com", "admin.meridian@example.com", "admin.meridian@example.com", "a b"]);
  assert.deepEqual(failures, keys);
  const long = "x".repeat(200);
  assert.equal((await fetch(url("/session"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ username: long, password: "example" }) })).status, 422);
  assert.equal(relayed.at(-1).body.username, long);
  assert.equal(failures.length, keys.length, "a refusal other than 401 still counts against the throttle");
  const role = await fetch(url("/api/role"), { headers: { cookie: "session=legacy" } });
  assert.equal(role.status, 200, "KGE's identity is the key as given, so an id the portal would not accept as typed input is not refused");
  assert.deepEqual(await role.json(), { role: "viewer", roles: ["viewer"] });
  assert.equal((await (await fetch(url("/whoami"), { headers: { cookie: "session=legacy" } })).json()).id, "Legacy Principal");
});
