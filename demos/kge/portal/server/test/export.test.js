import test from "node:test";
import assert from "node:assert/strict";
import { relay } from "../src/kge.js";

for (const capped of [false, true]) {
  test(`export relay preserves conditional cap headers: ${capped}`, async () => {
    const headers = { "content-type": "text/csv" };
    if (capped) {
      headers["X-Export-Capped-At"] = "10";
      headers["X-Export-Note"] = "Export limited to 10 rows; it may be incomplete.";
    }
    const sent = new Map();
    const res = {
      removeHeader: (key) => sent.delete(key), status: () => {},
      set: (key, value) => sent.set(key, value), append: () => {}, type: () => {}, send: () => {},
    };
    await relay(new Response("id\n1\n", { headers }), res);
    assert.equal(sent.has("X-Export-Capped-At"), capped);
    assert.equal(sent.has("X-Export-Note"), capped);
    if (capped) assert.equal(sent.get("X-Export-Capped-At"), "10");
  });
}
