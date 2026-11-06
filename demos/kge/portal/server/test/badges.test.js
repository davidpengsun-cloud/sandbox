import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { normaliseBadge, metadataPatch, portalBadges } from "../src/catalogue-record.js";
import { openFileCatalogueSource } from "../src/catalogue-source/file.js";
import { catalogue } from "./fixtures/catalogue.js";

const refused = [
  ...["CERTIFIED", "GOVERNED", "RESTRICTED", "PII"].flatMap((badge) =>
    ["", "\u034f", "\u3164", "\u1160", "\u115f", "\u2800", "\u0301", "\u0323"].map((suffix) => badge + suffix)),
  "\u202eDEIFITREC", "CERTlFlED", "CERT1F1ED", "G0VERNED", "Pll", "P1I",
  "CERTIΦIED", "CERTIFIEԀ", "ᏟᎬRᎢIFIED", "REՍTRICTED", "ᴄERTIFIED", "ↄERTIFIED",
  "CERTIFIE", "CERTIFIEX", "XCERTIFIED", "C_E-R T I F I E D", "P2I", "", "   ",
  "GOLD!", "GOLD\n", "GOLD\t", "GÖLD", "A".repeat(33), "certıfied",
];
const accepted = ["GOLD", "SILVER", "BRONZE", "TOP RATED", "Q3-2025"];

test("portal badge allow-list rejects report bypasses and one-edit governed names", () => {
  for (const badge of refused) {
    assert.throws(() => normaliseBadge(badge), /portal-owned badges/, JSON.stringify(badge));
    assert.throws(() => metadataPatch({ badges: [badge] }), undefined, JSON.stringify(badge));
  }
  for (const badge of accepted) assert.equal(normaliseBadge(badge), badge);
  assert.equal(normaliseBadge("  top   rated  "), "TOP RATED");
  assert.equal(normaliseBadge("ＧＯＬＤ"), "GOLD");
  assert.equal(normaliseBadge("A".repeat(32)), "A".repeat(32));
  assert.deepEqual(portalBadges([...refused, ...accepted]), accepted);
});

test("seed badges reuse the gate, log each refusal, and retain valid badges", async () => {
  const directory = await mkdtemp(join(tmpdir(), "badge-seed-"));
  const path = join(directory, "catalogue.json");
  const { unit_id, ...product } = catalogue.products[0];
  const seed = { format: 1, units: [unit_id], products: [{ ...product, id: `${unit_id}.${product.table}`, unit: unit_id, badges: [...refused, ...accepted, "  top   rated  ", null] }] };
  await writeFile(path, JSON.stringify(seed));
  const errors = [];
  const source = openFileCatalogueSource(path, { onInvalidBadge: (message) => errors.push(message) });
  const result = await source.load();
  assert.deepEqual(result.products[0].badges, [...accepted, "TOP RATED"]);
  assert.equal(errors.length, refused.length + 1);
  assert.ok(errors.every((message) => /products\[0\].badges\[\d+\]: Only portal-owned badges/.test(message)));
});
