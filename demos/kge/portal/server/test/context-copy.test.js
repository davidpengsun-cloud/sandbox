import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const read = path => readFileSync(new URL(path, import.meta.url), "utf8");
const source = name => read(`../../ui/src/pages/Context/${name}.js`);

test("Context has five React screens, shared controls and tokens, with no imperative workspace", () => {
  for (const retired of ["Workspace.js", "controller.js"]) assert.equal(existsSync(new URL(`../../ui/src/pages/Context/${retired}`, import.meta.url)), false);
  for (const name of ["Ask", "Docs", "Review", "Onboard", "Quality"]) {
    assert.match(source("ContextScreens"), new RegExp(`import ${name} from`));
    assert.match(source(name), /export default function/);
    assert.doesNotMatch(source(name), /innerHTML|insertAdjacentHTML|dangerouslySetInnerHTML|input[^>]*type="file"/);
  }
  assert.match(source("ContextApp"), /<ShellPage app="context" contextPerson=\{bootstrap\?\.person\}/);
  assert.match(source("ContextScreens"), /APP_NAMES.context/);
  const css = read("../../ui/src/pages/Context/context.css");
  assert.match(css, /var\(--v3-/);
  assert.doesNotMatch(css, /--v3-[\w-]+\s*:|:root|@font-face/);
  assert.match(css, /min-height:44px/);
});

test("React decisions and report links stay on the Context BFF and never impersonate the actor", () => {
  const api = source("contextState");
  assert.match(api, /fetch\("\/api\/context" \+ path/);
  assert.match(api, /lifecycle.current.abort\(\)/);
  assert.match(api, /fetch\("\/session", \{ method: "DELETE"/);
  assert.match(source("Review"), /api\("\/steward\/decide", \{ tag: row.tag, decision/);
  assert.doesNotMatch(source("Onboard"), /principal:|role:|person:|onboard\/(source|rows|tables|draft)/);
  const app = read("../src/app.js");
  assert.ok(app.indexOf('app.use("/api/context", contextProxy(') < app.indexOf('app.use(express.json('));
  assert.match(source("Docs"), /\/api\/context\/onboarding-guide/);
  assert.match(source("Docs"), /href="\/api\/context\/"/);
});
