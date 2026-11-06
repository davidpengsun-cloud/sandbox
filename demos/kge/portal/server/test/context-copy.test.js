import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const workspace = read("../../ui/src/pages/Context/Workspace.js");
const controller = read("../../ui/src/pages/Context/controller.js");
const original = read("../../../ui/index.html");

test("the released Context is mounted in the portal and has no independent page or tokens", () => {
  assert.equal(existsSync(new URL("../../context/index.html", import.meta.url)), false);
  assert.doesNotMatch(read("../../ui/src/pages/Context/ContextApp.js"), /<iframe/);
  assert.match(workspace, /Button, Panel, Tabs.*shared\/Controls/);
  assert.match(workspace, /mountContext\(root.current, bootstrap\)/);
  const css = read("../../ui/src/pages/Context/context.css");
  assert.doesNotMatch(css, /--v3-[\w-]+\s*:/);
  assert.doesNotMatch(css, /:root|@font-face/);
  assert.match(css, /var\(--v3-/);
});

test("every existing task control remains in Context, with shared identity and theme in the global bar", () => {
  const ids = [...original.split('<body>')[1].split('<script>')[0].matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
  const shell = new Set(['brand', 'brand-logo', 'brand-tile', 'title', 'products', 'product-ask', 'product-discover', 'theme', 'account', 'account-menu', 'who', 'account-detail', 'signin-box', 'principal-label', 'principal', 'username', 'token', 'password', 'signin']);
  for (const id of ids.filter((id) => !shell.has(id))) assert.ok(workspace.includes(`id={"${id}"}`), id);
});

test("all Context calls, reports and guide navigation stay on the portal", () => {
  assert.match(controller, /fetch\(contextApiBase \+ path, init\)/);
  assert.match(controller, /const contextApiBase = "\/api\/context"/);
  assert.doesNotMatch(controller, /fetch\(["'`]https?:/);
  assert.doesNotMatch(workspace, /(?:src|href)=\{["']https?:/);
  assert.equal((controller.match(/esc\("\/api\/context" \+ /g) || []).length, 3);
  assert.match(workspace, /\/api\/context\/onboarding-guide/);
  assert.doesNotMatch(workspace, /nav-tools|view-tools|sql-check/);
  assert.match(controller, /abort.abort\(\)/);
  assert.match(controller, /cleanups.forEach/);
});

test("steward decisions use the Context BFF before its JSON middleware", () => {
  assert.match(controller, /api\("\/steward\/decide", \{method:"POST"/);
  const app = read("../src/app.js");
  const mounted = app.indexOf('app.use("/api/context", contextProxy(');
  assert.ok(mounted >= 0);
  assert.ok(app.indexOf('app.use(express.json(') > mounted);
});
