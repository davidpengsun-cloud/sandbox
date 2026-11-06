import assert from "node:assert/strict";
import test from "node:test";
import { normalizeUsername, portalAccess } from "../src/portal-access.js";
import { kgeClient } from "../src/kge.js";
import { openIdentity } from "../src/identity.js";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes, scryptSync } from "node:crypto";
import vectors from "./username-vectors.json" with { type: "json" };

test("usernames are stripped, casefolded and NFKC normalized with shared guardrails", () => {
  for (const [input, expected] of [["  USER@EXAMPLE.COM  ", "user@example.com"], ["Straße", "strasse"], ["Σς", "σσ"], ["ＡＢＣ", "abc"], ["admin", "admin"], ["reader", "reader"]]) assert.equal(normalizeUsername(input), expected);
  for (const input of [null, "", "a b", "a\nb", "\nreader", "a\u0000b", "a\u200bb", "a".repeat(255), "ACCESS_ADMIN", "role:reader", "ℝOLE:reader"]) assert.throws(() => normalizeUsername(input), { status: 400 });
  assert.equal(normalizeUsername("a".repeat(254)).length, 254);
  for (const input of ["𝐀", "Ａ", "Straße", "K", "Σς"]) assert.equal(normalizeUsername(normalizeUsername(input)), normalizeUsername(input));
  assert.equal(normalizeUsername("𝐀"), "a");
});

test("the shared username vectors normalise or refuse exactly as listed, and normalising twice changes nothing", () => {
  assert.ok(vectors.vectors.length >= 20);
  for (const { why, input, expected } of vectors.vectors) {
    if (expected === null) { assert.throws(() => normalizeUsername(input), { status: 400 }, why); continue; }
    assert.equal(normalizeUsername(input), expected, why);
    assert.equal(normalizeUsername(expected), expected, why);
  }
});

function fixture() {
  const rows = new Map();
  const audit = [];
  const repository = {
    roles: async (principal) => rows.get(principal) || ["viewer"],
    users: async () => [...rows].map(([principal_id, roles]) => ({ principal_id, roles })),
    changeRole: async (principal, role, grant, by) => {
      const before = rows.get(principal) || [];
      const after = grant ? [...new Set([...before, role])] : before.filter((held) => held !== role);
      rows.set(principal, after);
      const entry = { id: audit.length + 1, at: new Date().toISOString(), by, principal, before, after };
      audit.push(entry);
      return entry;
    },
    roleAudit: async () => audit,
  };
  return { access: portalAccess(repository), rows };
}

test("KGE access admins bootstrap portal administration without a name grant", async () => {
  const { access } = fixture();
  for (const principal of ["admin", "steward", "publisher"]) {
    assert.deepEqual(await access.roles({ principal, who: { roles: ["admin"] } }), ["viewer"]);
    await assert.rejects(access.list({ principal }), { status: 403 });
  }
  const administrator = { principal: "operator@example.com", who: { admin: true, roles: [] } };
  assert.deepEqual(await access.roles(administrator), ["viewer", "admin"]);
  assert.equal((await access.list(administrator)).users.length, 0);
  await access.change(administrator, " PERSON@EXAMPLE.COM ", "publisher", true);
  const person = { principal: "person@example.com" };
  assert.deepEqual(await access.roles(person), ["publisher"]);
  await access.change(administrator, person.principal, "approver", true);
  assert.deepEqual(await access.roles(person), ["publisher", "approver"]);
  await access.change(administrator, person.principal, "publisher", false);
  assert.deepEqual(await access.roles(person), ["approver"]);
  const changes = (await access.audit(administrator)).audit;
  assert.equal(changes.length, 3);
  assert.deepEqual(changes[2].before, ["publisher", "approver"]);
  assert.deepEqual(changes[2].after, ["approver"]);
  assert.equal(changes[2].by, administrator.principal);
  assert.ok(Date.parse(changes[2].at));
  await assert.rejects(access.change(administrator, person.principal, "owner", true), { status: 400 });
  await assert.rejects(access.change(person, person.principal, "admin", true), { status: 403 });
  await assert.rejects(access.audit(person), { status: 403 });
  administrator.who.admin = false;
  await assert.rejects(access.list(administrator), { status: 403 });
});

test("portal admin grants are unioned and removed at the next check; stale KGE identity cannot administer", async () => {
  const { access, rows } = fixture();
  const person = { principal: "person@example.com", who: { admin: false } };
  rows.set(person.principal, ["admin"]);
  assert.ok((await access.list(person)).roles.includes("approver"));
  rows.delete(person.principal);
  await assert.rejects(access.list(person), { status: 403 });
  person.who.admin = true;
  person.fallback = true;
  assert.deepEqual(await access.roles(person), ["viewer"]);
  await assert.rejects(access.change(person, "other", "admin", true), { status: 403 });
});

test("shared KGE credentials keep username, live display name and explicit admin flag", async (context) => {
  const requests = [];
  context.mock.method(globalThis, "fetch", async (url, options) => {
    requests.push(options.headers);
    return Response.json(url.pathname === "/whoami" ? { id: "person@example.com", name: "Display Person", roles: ["reader"], admin: true } : { units: ["retail"] });
  });
  const value = await kgeClient("http://kge.invalid").identity({ headers: { cookie: "kge_session=opaque", authorization: "Bearer opaque" } });
  assert.equal(value.principal, "person@example.com");
  assert.equal(value.who.name, "Display Person");
  assert.equal(value.who.admin, true);
  assert.deepEqual(value.units, ["retail"]);
  assert.ok(requests.every((headers) => headers.cookie === "kge_session=opaque" && headers.authorization === "Bearer opaque"));
});

test("KGE's whoami id is the portal's key as given, never re-normalised", async (context) => {
  for (const id of ["Mixed.Case@Example.com", "legacy principal", "Analyst_Retail"]) {
    context.mock.method(globalThis, "fetch", async (url) => Response.json(url.pathname === "/whoami" ? { id } : { units: [] }));
    assert.equal((await kgeClient("http://kge.invalid").identity({ headers: {} })).principal, id);
  }
});

test("standalone identity normalizes credential keys and sign-in consistently and refuses collisions", async (context) => {
  const directory = mkdtempSync(join(tmpdir(), "portal-normalization-"));
  context.after(() => rmSync(directory, { recursive: true }));
  const env = { PORTAL_IDENTITIES_FILE: join(directory, "people.json"), PORTAL_PASSWORDS_FILE: join(directory, "hashes.json"), PORTAL_SESSION_SECRET: randomBytes(32).toString("hex") };
  const password = randomBytes(16).toString("hex"), salt = randomBytes(16);
  const hash = `scrypt$16384$8$1$${salt.toString("base64url")}$${scryptSync(password, salt, 32).toString("base64url")}`;
  const person = { principal: "Straße", role: "reader", reads: ["retail"], processes: ["querying"] };
  writeFileSync(env.PORTAL_IDENTITIES_FILE, JSON.stringify([person]));
  writeFileSync(env.PORTAL_PASSWORDS_FILE, JSON.stringify({ Straße: hash }));
  const identity = openIdentity(env);
  let cookie;
  const res = { cookie: (name, value) => { cookie = `${name}=${value}`; }, json: (value) => value };
  assert.deepEqual(await identity.signIn({ headers: {}, body: { username: " STRASSE ", password } }, res), { principal: "strasse" });
  assert.deepEqual(identity.identity({ headers: { cookie } }), { principal: "strasse", units: ["retail"] });
  let failed = false;
  await identity.signIn({ headers: {}, body: { username: "role:reader", password } }, res, { failed: () => { failed = true; } });
  assert.ok(failed);
  writeFileSync(env.PORTAL_IDENTITIES_FILE, JSON.stringify([person, { ...person, principal: "STRASSE" }]));
  assert.throws(() => openIdentity(env), /distinct normalized/);
});
