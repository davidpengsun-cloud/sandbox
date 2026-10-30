import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { after, test } from "node:test";
import { createApp } from "../src/app.js";
import { checkedLogo, safeSvg } from "../src/brand.js";

const here = dirname(fileURLToPath(import.meta.url));
const scratch = mkdtempSync(join(tmpdir(), "portal-logo-"));
after(() => rmSync(scratch, { recursive: true }));

const repository = {};
const kge = {};

async function servedLogo(options) {
  const { app } = createApp({ repository, kge, ...options });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  try {
    return await fetch(`http://127.0.0.1:${server.address().port}/brand/logo.svg`);
  } finally {
    server.close();
  }
}

test("the built-in logo is served as PNG", async () => {
  const response = await servedLogo({ uiPath: resolve(here, "../../ui/public") });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^image\/png/);
});

test("PORTAL_LOGO serves a PNG with its own content type", async () => {
  const logo = join(scratch, "logo.png");
  writeFileSync(logo, Buffer.from("89504e470d0a1a0a", "hex"));
  const response = await servedLogo({ env: { PORTAL_LOGO: logo }, uiPath: "not-present" });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^image\/png/);
});

test("PORTAL_LOGO accepts a JPEG suffix", async () => {
  const logo = join(scratch, "logo.jpeg");
  writeFileSync(logo, Buffer.from("ffd8ffd9", "hex"));
  const response = await servedLogo({ env: { PORTAL_LOGO: logo }, uiPath: "not-present" });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^image\/jpeg/);
});

test("the extensionless logo path remains an alias", async () => {
  const { app } = createApp({ repository, kge, uiPath: resolve(here, "../../ui/public") });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  try {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/brand/logo`);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /^image\/png/);
  } finally {
    server.close();
  }
});

test("closing the server stops catalogue sync", async () => {
  let calls = 0;
  const { app } = createApp({ repository, kge, env: { PORTAL_CATALOGUE_SYNC_MS: "10" }, syncCatalogue: async () => { calls += 1; }, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  await new Promise((ready) => setTimeout(ready, 25));
  await new Promise((ready) => server.close(ready));
  const stoppedAt = calls;
  await new Promise((ready) => setTimeout(ready, 25));
  assert.equal(calls, stoppedAt);
});

test("startup catalogue sync retries quickly until its first success", async () => {
  let calls = 0;
  const { app } = createApp({ repository, kge, env: { PORTAL_CATALOGUE_RETRY_MS: "5", PORTAL_CATALOGUE_SYNC_MS: "10000" }, syncCatalogue: async () => {
    calls += 1;
    if (calls < 3) throw new Error("app is starting");
    return { changed: true, release: "ready", count: 47 };
  }, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  for (let attempt = 0; attempt < 20 && calls < 3; attempt += 1) await new Promise((ready) => setTimeout(ready, 5));
  await new Promise((ready) => server.close(ready));
  assert.equal(calls, 3);
});

test("a missing PORTAL_LOGO is refused at startup", () => {
  assert.throws(
    () => createApp({ repository, kge, env: { PORTAL_LOGO: join(scratch, "missing.svg") }, uiPath: "not-present" }),
    /PORTAL_LOGO/
  );
});

async function get(options, path) {
  const { app } = createApp({ repository, kge, ...options });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  try {
    return await fetch(`http://127.0.0.1:${server.address().port}${path}`);
  } finally {
    server.close();
  }
}

function logoFile(name, content) {
  const path = join(scratch, name);
  writeFileSync(path, content);
  return path;
}

const refused = /PORTAL_LOGO must name an svg, png, jpg, jpeg or ico image of 256 KB or less; an SVG must be a plain drawing, with no doctype, script, event handler, animation, style sheet, embedded image, character reference or external link\.$/;

for (const [name, content] of [
  ["wrong-magic.png", Buffer.from("GIF89a")],
  ["svg-as.png", "<svg xmlns='http://www.w3.org/2000/svg'/>"],
  ["png-as.svg", Buffer.from("89504e470d0a1a0a", "hex")],
  ["oversize.png", Buffer.concat([Buffer.from("89504e470d0a1a0a", "hex"), Buffer.alloc(262144)])],
  ["logo.gif", Buffer.from("89504e470d0a1a0a", "hex")],
  ["not-svg.svg", "<html><body/></html>"],
  ["script.svg", "<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"],
  ["onload.svg", "<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>"],
  ["external.svg", "<svg xmlns='http://www.w3.org/2000/svg'><image href='https://example.invalid/x.png'/></svg>"],
  ["xlink.svg", "<svg xmlns='http://www.w3.org/2000/svg'><use xlink:href=\"data:image/svg+xml,x\"/></svg>"],
  ["entity.svg", "<?xml version='1.0'?><!DOCTYPE svg [<!ENTITY x 'y'>]><svg xmlns='http://www.w3.org/2000/svg'/>"],
  ["foreign.svg", "<svg xmlns='http://www.w3.org/2000/svg'><foreignObject/></svg>"],
  ["binary.svg", Buffer.from("3c7376673eff", "hex")],
]) test(`PORTAL_LOGO ${name} is refused at startup with one sentence`, () => {
  assert.throws(() => createApp({ repository, kge, env: { PORTAL_LOGO: logoFile(name, content) }, uiPath: "not-present" }), refused);
});

test("a PORTAL_LOGO that is a directory is refused", () => {
  assert.throws(() => createApp({ repository, kge, env: { PORTAL_LOGO: scratch }, uiPath: "not-present" }), refused);
});

test("a clean SVG with a prolog, comment and local href is served as configured", async () => {
  const logo = logoFile("clean.svg", "﻿<?xml version=\"1.0\"?>\n<!-- mark -->\n<svg xmlns=\"http://www.w3.org/2000/svg\"><defs><path id=\"a\" d=\"M0 0h1\"/></defs><use href=\"#a\"/></svg>");
  for (const path of ["/brand/logo", "/brand/logo.svg", "/brand/logo.png"]) {
    const response = await get({ env: { PORTAL_LOGO: logo }, uiPath: "not-present" }, path);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type"), /^image\/svg\+xml/);
    assert.equal(response.headers.get("x-brand-logo"), "configured");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
    assert.equal(response.headers.get("cache-control"), "public, max-age=300");
  }
});

test("an ICO logo is served as image/x-icon", async () => {
  const response = await get({ env: { PORTAL_LOGO: logoFile("logo.ico", Buffer.from([0, 0, 1, 0, 1])) }, uiPath: "not-present" }, "/brand/logo");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^image\/x-icon/);
});

test("the tab icon's path, /brand/logo.png, serves a configured PORTAL_LOGO, not the built-in file beside the page", async () => {
  const logo = logoFile("tab.svg", "<svg xmlns=\"http://www.w3.org/2000/svg\"><path d=\"M0 0h1\"/></svg>");
  const response = await get({ env: { PORTAL_LOGO: logo }, uiPath: resolve(here, "../../ui/public") }, "/brand/logo.png");
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /^image\/svg\+xml/);
  assert.equal(response.headers.get("x-brand-logo"), "configured");
  assert.ok(Buffer.from(await response.arrayBuffer()).equals(readFileSync(logo)));
});

test("the built-in mark says so in its headers", async () => {
  const response = await get({ uiPath: resolve(here, "../../ui/public") }, "/brand/logo");
  assert.match(response.headers.get("content-type"), /^image\/png/);
  assert.ok(Buffer.from(await response.arrayBuffer()).equals(readFileSync(resolve(here, "../../ui/public/brand/logo.png"))));
  assert.equal(response.headers.get("x-brand-logo"), "built-in");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("cache-control"), "public, max-age=300");
});

test("/brand reports the built-in logo and no KGE when neither is configured", async () => {
  const response = await get({ uiPath: resolve(here, "../../ui/public") }, "/brand");
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { name: "Data Discovery Portal", logo: true, kge_url: null });
});

test("/brand reports a configured logo and the KGE URL without sign-in", async () => {
  const logo = logoFile("brand.png", Buffer.from("89504e470d0a1a0a", "hex"));
  const response = await get({ env: { PORTAL_LOGO: logo, KGE_URL: "https://kge.example.invalid" }, catalogueSync: false, uiPath: "not-present" }, "/brand");
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { name: "Data Discovery Portal", logo: true, kge_url: "https://kge.example.invalid" });
});

test("/brand keeps the portal name independent of the KGE profile", async () => {
  const titled = await get({ kge: { profile: async () => ({ title: "Sample Union governance graph" }) }, uiPath: "not-present" }, "/brand");
  assert.equal((await titled.json()).name, "Data Discovery Portal");
  const down = await get({ kge: { profile: async () => { throw new Error("KGE returned 503"); } }, env: { PROFILE: "fellmoor-transit" }, uiPath: "not-present" }, "/brand");
  assert.equal(down.status, 200);
  assert.equal((await down.json()).name, "Data Discovery Portal");
});

test("/brand links to KGE's browser address when KGE_PUBLIC_URL is set, and only when KGE is configured", async () => {
  const joined = await get({ env: { KGE_URL: "http://app:8000", KGE_PUBLIC_URL: "http://127.0.0.1:8000" }, catalogueSync: false, uiPath: "not-present" }, "/brand");
  assert.equal((await joined.json()).kge_url, "http://127.0.0.1:8000");
  const alone = await get({ env: { KGE_PUBLIC_URL: "http://127.0.0.1:8000" }, uiPath: "not-present" }, "/brand");
  assert.equal((await alone.json()).kge_url, null);
  const off = await get({ env: { KGE_URL: "http://app:8000", KGE_PUBLIC_URL: "http://127.0.0.1:8000", PORTAL_ASK: "off" }, catalogueSync: false, uiPath: "not-present" }, "/brand");
  assert.equal((await off.json()).kge_url, null, "PORTAL_ASK=off hides the link to KGE");
  const on = await get({ env: { KGE_URL: "http://app:8000", KGE_PUBLIC_URL: "http://127.0.0.1:8000", PORTAL_ASK: "on" }, catalogueSync: false, uiPath: "not-present" }, "/brand");
  assert.equal((await on.json()).kge_url, "http://127.0.0.1:8000", "PORTAL_ASK=on shows the link to KGE");
});

const probes = JSON.parse(readFileSync(new URL("./fixtures/logo-probes.json", import.meta.url), "utf8"));

for (const probe of probes) test(`logo probe ${probe.name}: ${probe.accepted ? "accepted" : "refused"}, as KGE's KGE_LOGO check decides it`, () => {
  assert.equal(safeSvg(probe.svg), probe.accepted);
  const file = logoFile(`probe-${probe.name}.svg`, probe.svg);
  if (probe.accepted) assert.equal(checkedLogo(file).contentType, "image/svg+xml");
  else assert.throws(() => checkedLogo(file), refused);
});

test("the logo is served under a policy that runs nothing and fetches nothing", async () => {
  for (const options of [{ uiPath: resolve(here, "../../ui/public") }, { env: { PORTAL_LOGO: logoFile("policy.png", Buffer.from("89504e470d0a1a0a", "hex")) }, uiPath: "not-present" }]) {
    for (const path of ["/brand/logo", "/brand/logo.svg", "/brand/logo.png"]) {
      const response = await get(options, path);
      assert.equal(response.headers.get("content-security-policy"), "default-src 'none'; style-src 'unsafe-inline'; sandbox");
    }
  }
});

test("/brand does not ask KGE for the portal's name", async () => {
  let asked = 0;
  const { app } = createApp({ repository, kge: { profile: async () => { asked += 1; return { title: "Sample Union governance graph" }; } }, uiPath: "not-present" });
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  try {
    const names = await Promise.all(Array.from({ length: 20 }, async () => (await (await fetch(`http://127.0.0.1:${server.address().port}/brand`)).json()).name));
    assert.deepEqual(new Set(names), new Set(["Data Discovery Portal"]));
    assert.equal(asked, 0);
  } finally {
    server.close();
  }
  let again = 0;
  const expired = createApp({ repository, kge: { profile: async () => { again += 1; return { title: "T" }; } }, brandCacheMs: 0, uiPath: "not-present" }).app;
  const second = expired.listen(0, "127.0.0.1");
  await new Promise((ready) => second.once("listening", ready));
  try {
    for (let i = 0; i < 2; i += 1) await fetch(`http://127.0.0.1:${second.address().port}/brand`);
    assert.equal(again, 0);
  } finally {
    second.close();
  }
});

for (const [setting, value] of [["KGE_URL", "kge-without-scheme"], ["KGE_URL", "ftp://kge"], ["KGE_PUBLIC_URL", "javascript:alert(1)"],
  ["KGE_PUBLIC_URL", "https://kge.example.com/?a=1"], ["KGE_PUBLIC_URL", "https://kge.example.com/#x"], ["KGE_PUBLIC_URL", "https://"]]) {
  test(`${setting}=${value} is refused at startup, naming the setting`, () => {
    assert.throws(() => createApp({ repository, kge, env: { [setting]: value, PORTAL_SYNC_TOKEN: "s".repeat(32) }, uiPath: "not-present" }),
      new RegExp(`${setting} must be KGE's http or https address`));
  });
}

test("PORTAL_LOGO is read once, as the portal starts: a file changed afterwards is not served until a restart", async () => {
  const logo = logoFile("once.png", Buffer.from("89504e470d0a1a0a00", "hex"));
  const { app } = createApp({ repository, kge, env: { PORTAL_LOGO: logo }, uiPath: "not-present" });
  writeFileSync(logo, Buffer.from("89504e470d0a1a0a01", "hex"));
  const server = app.listen(0, "127.0.0.1");
  await new Promise((ready) => server.once("listening", ready));
  try {
    const served = Buffer.from(await (await fetch(`http://127.0.0.1:${server.address().port}/brand/logo`)).arrayBuffer());
    assert.equal(served.toString("hex"), "89504e470d0a1a0a00");
  } finally {
    server.close();
  }
});
