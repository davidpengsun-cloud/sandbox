import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { validateCatalogue } from "../src/catalogue-source/contract.js";
import { openSqliteRepository } from "../src/repository/sqlite.js";

const served = JSON.parse(readFileSync(new URL("./fixtures/served-document-sets.json", import.meta.url), "utf8"));

test("a served document set passes the catalogue contract and syncs inside its unit", () => {
  const broken = structuredClone(served);
  const incomplete = broken.products.find((item) => item.id === "retail.document_set");
  for (const key of ["last_refresh", "ingested_at", "records", "storage", "last_dq_run"]) delete incomplete[key];
  assert.throws(() => validateCatalogue(broken), /last_refresh/);
  const accepted = validateCatalogue(served);
  const repository = openSqliteRepository({ PORTAL_SQLITE_PATH: ":memory:" });
  repository.syncCatalogue(accepted.release, accepted.products);
  const retail = repository.listProducts(["retail"]);
  const wealth = repository.listProducts(["wealth"]);
  const retailCard = retail.find((item) => item.id === "retail.document_set");
  const wealthCard = wealth.find((item) => item.id === "wealth.document_set");
  assert.equal(retailCard?.asset_type, "Unstructured");
  assert.equal(retail.some((item) => item.id === "wealth.document_set"), false);
  assert.ok(wealthCard);
  assert.equal(wealth.some((item) => item.id === "retail.document_set"), false);
  assert.equal(retail.some((item) => item.id === "retail.complaint"), true);
  repository.close();
});
