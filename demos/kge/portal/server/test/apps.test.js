import assert from "node:assert/strict";
import test from "node:test";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { join } from "node:path";
import { BRAND_NAME, BRAND_SHORT_NAME, BRAND_SUBTITLE, brandNames } from "../src/brand.js";
import { appSettings } from "../src/apps.js";

test("PORTAL_CLIENT_NAME names the product independently of the profile, trimming blanks and defaulting to Client", () => {
  for (const name of [undefined, "", "   "]) {
    assert.equal(appSettings({ PORTAL_CLIENT_NAME: name }).productName, "Client ALTA");
  }
  assert.equal(appSettings({ PORTAL_CLIENT_NAME: "  Meridian Group  ", PROFILE: "another-client" }).productName, "Meridian Group ALTA");
});

test("PORTAL_PRODUCT_NAME and PORTAL_PRODUCT_LONG_NAME name the product, trimming blanks and defaulting to ALTA", () => {
  assert.equal(BRAND_SHORT_NAME, "ALTA");
  assert.equal(BRAND_NAME, "ALTA: AI-powered Library for Trusted Assets");
  for (const name of [undefined, "", "   "]) {
    assert.deepEqual(brandNames({ PORTAL_PRODUCT_NAME: name, PORTAL_PRODUCT_LONG_NAME: name }), { shortName: "ALTA", longName: "ALTA: AI-powered Library for Trusted Assets", subtitle: BRAND_SUBTITLE });
    const settings = appSettings({ PORTAL_PRODUCT_NAME: name, PORTAL_PRODUCT_LONG_NAME: name });
    assert.equal(settings.productName, "Client ALTA");
    assert.equal(settings.productShortName, "ALTA");
    assert.equal(settings.productLongName, "ALTA: AI-powered Library for Trusted Assets");
  }
  const settings = appSettings({ PORTAL_CLIENT_NAME: "Meridian", PORTAL_PRODUCT_NAME: "  Beacon  ", PORTAL_PRODUCT_LONG_NAME: "  Beacon: the data library  " });
  assert.equal(settings.productName, "Meridian Beacon");
  assert.equal(settings.productShortName, "Beacon");
  assert.equal(settings.productLongName, "Beacon: the data library");
  assert.deepEqual(brandNames({}), brandNames());
});

test("PORTAL_TABLE_QUERY defaults to off, accepts only on or off, and names invalid values", () => {
  assert.equal(appSettings({}).tableQuery, false);
  assert.equal(appSettings({ PORTAL_TABLE_QUERY: "off" }).tableQuery, false);
  assert.equal(appSettings({ PORTAL_TABLE_QUERY: "on" }).tableQuery, true);
  for (const value of ["", "ON", "true", "1", " on"]) {
    assert.throws(() => appSettings({ PORTAL_TABLE_QUERY: value }), /^Error: PORTAL_TABLE_QUERY must be on or off$/);
  }
});

test("PORTAL_LOGO_TITLE defaults to off and accepts only on or off", () => {
  assert.equal(appSettings({}).logoTitle, false);
  assert.equal(appSettings({ PORTAL_LOGO_TITLE: "off" }).logoTitle, false);
  assert.equal(appSettings({ PORTAL_LOGO_TITLE: "on" }).logoTitle, true);
  for (const value of ["", "ON", "true", "1", " on"]) {
    assert.throws(() => appSettings({ PORTAL_LOGO_TITLE: value }), /^Error: PORTAL_LOGO_TITLE must be on or off$/);
  }
});

test("every portal page, static title and manifest uses the ALTA wording", () => {
  const ui = fileURLToPath(new URL("../../ui/", import.meta.url));
  const files = ["index.html", "public/manifest.json", ...readdirSync(join(ui, "src"), { recursive: true }).map((path) => `src/${path}`)];
  for (const file of files) {
    if (!/\.(js|html|json)$/.test(file) || /(?:\.test\.js|setupTests\.js|testSupport\.js)$/.test(file)) continue;
    const text = readFileSync(join(ui, file), "utf8");
    assert.doesNotMatch(text, /(?:Client|\[Client name\]|Meridian(?: Group)?|\$\{clientName\}|\$\{DEFAULT_CLIENT_NAME\}) Knowledge|Data Discovery Portal|governed dataset|Loading portal/i, file);
    assert.doesNotMatch(text, /\batlas\b/i, file);
  }
  const manifest = JSON.parse(readFileSync(join(ui, "public/manifest.json"), "utf8"));
  assert.equal(BRAND_NAME, "ALTA: AI-powered Library for Trusted Assets");
  assert.equal(manifest.name, BRAND_NAME);
  assert.equal(manifest.short_name, BRAND_SHORT_NAME);
  assert.match(readFileSync(join(ui, "src/services/brand.js"), "utf8"), /ALTA: AI-powered Library for Trusted Assets/);
});

test("PORTAL_TABLE_PREVIEW defaults to off, accepts only on or off, and names invalid values", () => {
  assert.equal(appSettings({}).tablePreview, false);
  assert.equal(appSettings({ PORTAL_TABLE_PREVIEW: "off" }).tablePreview, false);
  assert.equal(appSettings({ PORTAL_TABLE_PREVIEW: "on" }).tablePreview, true);
  for (const value of ["", "ON", "true", "1", " on"]) {
    assert.throws(() => appSettings({ PORTAL_TABLE_PREVIEW: value }), /^Error: PORTAL_TABLE_PREVIEW must be on or off$/);
  }
});


test("PORTAL_PRODUCT_SUBTITLE trims set values and defaults when unset or blank", () => {
  for (const subtitle of [undefined, "", "   "]) {
    assert.equal(brandNames({ PORTAL_PRODUCT_SUBTITLE: subtitle }).subtitle, BRAND_SUBTITLE);
    assert.equal(appSettings({ PORTAL_PRODUCT_SUBTITLE: subtitle }).productSubtitle, BRAND_SUBTITLE);
  }
  assert.equal(brandNames({ PORTAL_PRODUCT_SUBTITLE: "  Find trusted assets.  " }).subtitle, "Find trusted assets.");
});
