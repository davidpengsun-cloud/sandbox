import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { test } from "node:test";

test("a portal that cannot listen on its port says so and exits non-zero", async () => {
  const taken = createServer();
  await new Promise((resolve) => taken.listen(0, "0.0.0.0", resolve));
  try {
    const run = spawnSync(process.execPath, ["--experimental-sqlite", "src/index.js"], {
      env: { ...process.env, PORT: String(taken.address().port), PORTAL_SQLITE_PATH: ":memory:", PORTAL_SYNC_TOKEN: "s".repeat(32), KGE_URL: "http://127.0.0.1:1" }, encoding: "utf8", timeout: 20000,
    });
    assert.equal(run.status, 1, run.stdout + run.stderr);
    assert.match(run.stderr, new RegExp(`Portal cannot listen on ${taken.address().port}: EADDRINUSE`));
    assert.doesNotMatch(run.stdout, /listening/);
  } finally {
    taken.close();
  }
});
