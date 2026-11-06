import assert from "node:assert/strict";
import test from "node:test";
import { validateCatalogue } from "../src/catalogue-source/contract.js";
import { catalogue } from "./fixtures/catalogue.js";
import { openKgeCatalogueSource } from "../src/catalogue-source/kge.js";

test("KGE portal catalogue fixture satisfies contract v1", () => {
  assert.equal(validateCatalogue({ contract_version: "1", ...catalogue }).release, catalogue.release);
});

test("KGE adapter does not invent a missing provider contract version", async () => {
  const { contract_version, ...unversioned } = catalogue;
  const source = openKgeCatalogueSource({ catalogue: async () => unversioned });
  assert.throws(() => validateCatalogue(unversioned), /contract_version/);
  assert.deepEqual(await source.load(), unversioned);
});

test("contract v1 refuses unknown and missing fields", () => {
  assert.throws(() => validateCatalogue({ contract_version: "1", ...catalogue, unknown: true }), /additional properties/);
  const broken = structuredClone(catalogue);
  delete broken.products[0].fields[0].quality;
  assert.throws(() => validateCatalogue({ contract_version: "1", ...broken }), /required property/);
});
