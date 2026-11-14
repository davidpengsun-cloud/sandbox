import express from "express";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { brandRoutes, checkedAddress, checkedLogo } from "./brand.js";
import { kgeClient, relay, tooManyRequests } from "./kge.js";
import { openServiceAuth } from "./kgeauth.js";
import { openRepository } from "./repository.js";
import { clientAddress, createThrottle } from "./throttle.js";
import { openCatalogueSource } from "./catalogue-source/index.js";
import { validateCatalogue } from "./catalogue-source/contract.js";
import { openProviders, dataCall } from "./providers.js";
import { openIdentity } from "./identity.js";
import { knowledgeIdentity } from "./knowledge-identity.js";
import { classificationLabel, metadataPatch, metadataInput, placeholderRule, qualityRules } from "./catalogue-record.js";
import { openFileCatalogueSource } from "./catalogue-source/file.js";
import { openKgeCatalogueSource } from "./catalogue-source/kge.js";
import { unreachable, fallbackNotice, searchInput, productScope, matchReadableProduct, canonicalProductId, hitMatchesProduct } from "./knowledge.js";
import { normalizeUsername, portalAccess } from "./portal-access.js";
import { APP_NAMES, appSettings, notSetUp } from "./apps.js";
import { contextPage, contextProxy } from "./context.js";

const catalogueFacets = { typeFilter: ["Table", "View", "API/MCP", "Documents"], assetFilter: ["Structured", "Unstructured"], usageFilter: ["Analytical", "Operational"] };

// A blank fail % is shown blank, and so is the score made from it: never 0 or 100.
const blankFail = (rule) => rule?.fail_percent === null || rule?.fail_percent === undefined;
const ruleScore = (rule) => blankFail(rule) ? "" : Number((100 - rule.fail_percent).toFixed(1));

const asyncRoute = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

function legacyProduct(product, favourite = false) {
  return {
    PRODUCTID: product.id,
    UNIT_ID: product.unit_id,
    PRODUCTNAME: product.name,
    PRODUCTDISPLAYNAME: product.name,
    TECHNICALNAME: product.table,
    SCHEMANAME: product.schema || product.unit_id,
    SCHEMA: product.schema || "",
    DATA_PRODUCT_SOURCE: product.source,
    DATA_DOMAIN_NAME: product.domain,
    COMMUNITY: product.community,
    DOMAIN: product.domain,
    DESCRIPTION: product.description,
    PRODUCTTYPE: product.product_type,
    ASSETTYPE: product.asset_type || "",
    USAGEPURPOSE: product.usage_purpose || "",
    DATASETTYPE: product.dataset_type || "",
    APM_ID: product.APM_ID || "",
    PROJECT: product.project || "",
    PRODUCTSOURCE: product.source,
    APPLICATIONNAME: product.community,
    APPLICATION_IDENTIFIER: product.source,
    PLATFORM: "Portal",
    HIERARCHY: `${product.domain}->${product.community}->${product.name}`,
    PUBLISH_STATUS: product.published ? "Published" : "Unpublished",
    ISFAVOURITE: favourite ? "Y" : null,
    SUBSCRIBE_COUNT: product.counts.subscribe,
    ACCESS_COUNT: product.counts.access,
    VIEW_COUNT: product.counts.views,
    VIEWTIMES: product.counts.views,
    AVGRATING: product.rating,
    PRODUCT_SCORE: product.quality_score,
    BADGE_NAME: product.badges[0] || "",
    BADGES: product.badges,
    PRODUCT_OWNER: product.product_owner,
    BUSINESS_OWNER: product.business_owner,
    PRODUCTOWNER: product.product_owner,
    BUSINESSOWNER: product.business_owner,
    PRODUCTOWNEREMAIL: product.product_owner_email || "",
    BUSINESSOWNEREMAIL: product.business_owner_email || "",
    COUNTOFCOLUMNS: product.fields.length,
    COUNTOFRECORDS: product.records,
    PATH: product.path,
    DATA_FORMAT: product.data_format,
    SIZE: product.storage,
    DQRUNDATE: product.last_dq_run,
    LAST_REFRESH: product.last_refresh,
    REFRESH_CADENCE: product.refresh_cadence || "",
    INGESTED_AT: product.ingested_at,
    CATALOGUE_STATE: product.catalogue_state || "",
    PORTAL_ONLY: Boolean(product.portal_only),
    DOCUMENTS: Array.isArray(product.documents) ? product.documents : [],
  };
}

export function createApp(options = {}) {
  const env = options.env || process.env;
  const settings = appSettings(env);
  const contextOn = settings.apps.context && Boolean(env.KGE_URL || options.kge);
  const unavailableMessage = `${settings.knowledgeHome ? settings.productName : APP_NAMES[Object.keys(settings.apps).find((name) => settings.apps[name])]} is unavailable.`;
  const askSetting = env.PORTAL_ASK ?? "on";
  if (!["on", "off"].includes(askSetting)) throw new Error("PORTAL_ASK must be on or off");
  const askOn = askSetting === "on";
  const portalSyncToken = env.PORTAL_SYNC_TOKEN || "";
  const catalogueProvider = env.PORTAL_CATALOGUE_PROVIDER || (env.KGE_URL || options.kge ? "kge" : env.PORTAL_CATALOGUE_FILE ? "file" : options.catalogueSource || options.syncCatalogue || options.catalogueSync === false ? "file" : "");
  if (options.catalogueSync !== false && (catalogueProvider === "kge" || env.KGE_URL) && !options.kge && !env.K_SERVICE && portalSyncToken.length < 32) throw new Error("PORTAL_SYNC_TOKEN must be at least 32 characters");
  if (portalSyncToken && portalSyncToken.length < 32) throw new Error("PORTAL_SYNC_TOKEN must be at least 32 characters");
  checkedAddress("KGE_URL", env.KGE_URL);
  checkedAddress("KGE_PUBLIC_URL", env.KGE_PUBLIC_URL);
  const ui = resolve(options.uiPath || env.PORTAL_UI_PATH || "ui/build");
  const configuredLogo = checkedLogo(env.PORTAL_LOGO);
  const repository = options.repository || openRepository(env);
  const access = portalAccess(repository);
  const kge = options.kge || (env.KGE_URL ? kgeClient(env.KGE_URL, options.serviceAuth || openServiceAuth(env), portalSyncToken, env) : undefined);
  const catalogueSource = options.catalogueSource || (options.syncCatalogue || options.catalogueSync === false ? { identity: async () => ({}), load: async () => ({}) } : openCatalogueSource(env, kge));
  const refreshSource = options.catalogueSource || (kge ? openKgeCatalogueSource(kge) : catalogueSource);
  // Joined to KGE, a refresh brings catalogue.yaml's descriptive changes to
  // existing cards; alone, the portal's file only seeds missing records.
  const followsKge = Boolean(kge) && !options.catalogueSource;
  const providers = options.providers || openProviders(env, kge, catalogueProvider, repository);
  const localIdentity = kge ? undefined : openIdentity(env, options.clock);
  const identities = knowledgeIdentity(kge, localIdentity, options.clock);
  const seedSource = env.PORTAL_CATALOGUE_FILE ? openFileCatalogueSource(env.PORTAL_CATALOGUE_FILE, { onInvalidProduct: (message) => console.error(`Catalogue seed entry skipped: ${message}`) }) : undefined;
  const app = express();
  app.disable("x-powered-by");
  app.use(function stampArrival(req, _res, next) { req.receivedAt = Date.now(); next(); });
  app.get("/api/config", (_req, res) => res.json({
    knowledgeHome: settings.knowledgeHome,
    apps: settings.apps,
    certifyUrl: settings.certifyUrl,
    productName: settings.productName,
    productShortName: settings.productShortName,
    logoTitle: settings.logoTitle,
    // KGE's own MCP endpoint, for Context's MCP help: agents call KGE directly.
    contextMcpUrl: contextOn && env.KGE_URL ? new URL("mcp", `${(env.KGE_PUBLIC_URL || env.KGE_URL).replace(/\/+$/, "")}/`).href : null,
  }));
  // Before express.json(): Context uploads reach KGE as raw bytes.
  app.use("/api/context", contextProxy(contextOn ? kge : undefined, env.KGE_URL, settings.productName, { ...settings, env }));
  app.use(express.json({ limit: "1mb" }));
  // Discover's routes, when its flag is off: everything under /api but the
  // config, Context's proxy and the role SecureRoute reads for every app.
  const tablePreview = (_req, res, next) => settings.tablePreview ? next() : res.status(404).json({ message: "Table previews are not enabled." });
  const tableQuery = (_req, res, next) => settings.tableQuery ? next() : res.status(404).json({ message: "Table queries are not enabled." });
  const discover = (_req, res, next) => settings.apps.discover ? next() : notSetUp(res, "discover");
  const discoverApi = (req, res, next) => req.path === "/role" ? next() : discover(req, res, next);
  app.use("/api", discoverApi);
  let syncPromise;
  const syncCatalogue = options.syncCatalogue || (async () => {
    const identity = await refreshSource.identity();
    if (await repository.catalogueRelease() === identity.release) return { changed: false, release: identity.release };
    if (!identity.has_catalogue) {
      return { changed: await repository.syncCatalogue(identity.release, null, followsKge ? "follow" : false), release: identity.release, count: 0 };
    }
    const catalogue = validateCatalogue(await refreshSource.load());
    const products = catalogue.has_catalogue && Array.isArray(catalogue.products) ? catalogue.products : null;
    if (seedSource && !await repository.catalogueRelease()) {
      try {
        const seed = await seedSource.load();
        const released = new Set((products || []).map((p) => p.id));
        await repository.syncCatalogue(seed.release, seed.products.filter((p) => released.has(p.id)), true);
      } catch (error) {
        console.error(`Catalogue seed skipped: ${error.message}`);
      }
    }
    return { changed: await repository.syncCatalogue(catalogue.release, products, followsKge ? "follow" : true), release: catalogue.release, count: products?.length || 0 };
  });
  let firstSyncComplete = false;
  let retryDelay = Number(env.PORTAL_CATALOGUE_RETRY_MS || 5000);
  let retryTimer;
  const refreshCatalogue = () => {
    syncPromise = syncPromise || syncCatalogue().then((result) => {
      if (result?.changed) console.info(`Catalogue sync succeeded: release ${result.release}, ${result.count} products`);
      firstSyncComplete = true;
      retryDelay = Number(env.PORTAL_CATALOGUE_RETRY_MS || 5000);
      return result;
    }).catch((error) => {
      console.error(`Catalogue sync deferred: ${error.message}`);
      if (!firstSyncComplete && !retryTimer) {
        retryTimer = setTimeout(() => { retryTimer = undefined; refreshCatalogue(); }, retryDelay);
        retryTimer.unref();
        retryDelay = Math.min(retryDelay * 2, 60000);
      }
      return false;
    }).finally(() => { syncPromise = undefined; });
    return syncPromise;
  };
  let syncInterval;
  if (options.catalogueSync !== false) {
    refreshCatalogue();
    syncInterval = setInterval(refreshCatalogue, Number(env.PORTAL_CATALOGUE_SYNC_MS || 300000));
    syncInterval.unref();
  }
  const stopCatalogueSync = () => {
    if (syncInterval) clearInterval(syncInterval);
    if (retryTimer) clearTimeout(retryTimer);
    syncInterval = undefined;
    retryTimer = undefined;
  };
  const listen = app.listen.bind(app);
  app.listen = (...args) => {
    const server = listen(...args);
    server.once("close", stopCatalogueSync);
    return server;
  };

  const sessionRequest = async (...args) => {
    try { return await kge.request(...args); }
    catch (error) { if (unreachable(error)) throw Object.assign(new Error(unavailableMessage), { status: 502 }); throw error; }
  };
  const throttle = options.throttle || createThrottle();
  app.post("/session", asyncRoute(async (req, res) => {
    const address = clientAddress(req, env);
    if (!kge && (!req.body || typeof req.body.username !== "string" || !req.body.username || typeof req.body.password !== "string" || req.body.password.length > 1024 || Object.keys(req.body).some((key) => !["username", "password"].includes(key)))) return res.status(400).json({ detail: "username and password are required" });
    // The typed username goes to KGE unchanged; only the throttle key is normalised, so case and look-alike variants share one budget.
    const typed = String(req.body?.username || "");
    let throttleName;
    try { throttleName = normalizeUsername(typed); } catch { throttleName = typed; }
    const key = JSON.stringify([address, throttleName]);
    const wait = throttle.attempt(key);
    if (wait) return res.status(429).set("Retry-After", String(wait)).json({ detail: `too many sign-in attempts: try again in ${wait} s` });
    const failed = () => {
      const wait = throttle.failed(key);
      if (wait) res.set("Retry-After", String(wait));
      return res.status(401).json({ detail: "wrong username or password" });
    };
    if (!kge) {
      return localIdentity.signIn(req, res, { failed, passed: () => throttle.passed(key) });
    }
    const response = await sessionRequest(req, "/session", { method: "POST", body: JSON.stringify(req.body), headers: { "x-client-address": address } });
    if (response.status >= 400 && response.status < 500 && response.status !== 429) throttle.failed(key);
    if (response.ok) throttle.passed(key);
    return relay(response, res);
  }));
  for (const [method, path] of [["delete", "/session"], ["get", "/whoami"]]) {
    app[method](path, asyncRoute(async (req, res) => {
      if (method === "delete") identities.forget(req);
      if (kge && method === "delete") return relay(await sessionRequest(req, path, { method: method.toUpperCase() }), res);
      if (method === "delete") return localIdentity.signOut(req, res);
      const who = await identities.identity(req);
      if (who.who) return res.json(who.who);
      return res.json({ id: who.principal, principal: who.principal, reads: who.units, role: await repository.role(who.principal) });
    }));
  }
  app.get("/readable-units", asyncRoute(async (req, res) => res.json({ units: (await identities.identity(req)).units })));

  app.get(["/health", "/healthz"], (_req, res) => res.json({ status: "ok" }));

  const loadProducts = async (req, onIds, search = "") => {
    try {
      if (search) return await repository.listProducts(req.portalIdentity.units, search, onIds);
      if (!req.portalIdentity.products) req.portalIdentity.products = await repository.listProducts(req.portalIdentity.units, "", onIds);
      else if (typeof onIds === "function") onIds(req.portalIdentity.products.map((product) => product.id));
      return req.portalIdentity.products;
    } catch (error) {
      throw Object.assign(new Error(error.status === 401 ? "Sign in required." : unavailableMessage), { status: error.status || 502, retryAfter: error.retryAfter, field: "message" });
    }
  };
  const identity = asyncRoute(async (req, res, next) => {
    try {
      req.portalIdentity = await identities.identity(req);
      req.portalIdentity.loadProducts = (onIds) => loadProducts(req, onIds);
      if (req.portalIdentity.fallback) res.set("X-Portal-Notice", fallbackNotice);
      next();
    } catch (error) {
      if (error.status === 429) return tooManyRequests(res, error.retryAfter, "message");
      res.status(error.status || 502).json({ message: error.status === 401 ? "Sign in required." : unavailableMessage });
    }
  });
  app.use("/api", identity);
  app.post("/ask", discover, identity, asyncRoute((req, res) => askScoped(req, res, req.body)));
  app.post("/api/ask", asyncRoute((req, res) => askScoped(req, res, req.body)));

  const productsFor = async (req, search = "", options = {}) => {
    const needle = search.toLowerCase();
    let tail;
    const startTail = (ids) => {
      tail = ids.length ? Promise.all([repository.activities(ids), repository.publication(ids)]) : Promise.resolve([[], []]);
      tail.catch(() => {});
    };
    let rows, activityRows, publicationRows;
    try {
      rows = await loadProducts(req, startTail, search);
      if (!tail) startTail(rows.map((product) => product.id));
      [activityRows, publicationRows] = await tail;
    } finally {
      await tail?.catch(() => {});
    }
    const activities = new Map(activityRows.map((row) => [row.product_id, row]));
    const publication = new Map(publicationRows.map((row) => [row.product_id, row.published]));
    const products = rows.map((product) => {
      const activity = activities.get(product.id) || { views: 0, access: 0, subscribe: 0, feedback: { average: null, count: 0 } };
      const published = publication.get(product.id);
      const feedbackCount = Number(activity.feedback?.count || 0);
      const baselineWeight = Number(product.counts.rating || 1);
      const rating = feedbackCount ? ((product.rating * baselineWeight) + (Number(activity.feedback.average) * feedbackCount)) / (baselineWeight + feedbackCount) : product.rating;
      return { ...product, source: product.application, published: published == null ? true : Boolean(published), rating: Number(rating.toFixed(2)), counts: { subscribe: product.counts.subscribe + Number(activity.subscribe), access: product.counts.access + Number(activity.access), views: product.counts.views + Number(activity.views) } };
    });
    return products.filter((product) => (options.unpublished || product.published) && (!needle || [product.name, product.domain, product.community, product.application, product.description].some((value) => value.toLowerCase().includes(needle)))).sort((a, b) => a.name.localeCompare(b.name));
  };
  const askScoped = async (req, res, body) => {
    let scope;
    const scoped = ["productId", "productIds", "products"].some((key) => body[key] !== undefined);
    try { scope = productScope(scoped ? await productsFor(req) : [], body); }
    catch (error) {
      if (error.status === 404) return res.status(404).json({ message: "Product not found." });
      throw error;
    }
    return providers.ask.route(req, res, "/ask", { question: body.question, ...scope.metadata });
  };
  const productFor = async (req, id) => (await productsFor(req, "", { unpublished: true })).find((product) => product.id === String(id));
  const dataProduct = async (req, res, id) => {
    const product = await productFor(req, id);
    if (!product) res.status(404).json({ message: "Product not found." });
    return product;
  };
  const kgeData = (req, path, body, res) => providers.data.route(req, res, path, body);
  const requiredProduct = async (req, id) => {
    const product = await productFor(req, id);
    if (!product) throw Object.assign(new Error("Product not found."), { status: 404 });
    return product;
  };
  const fieldsFor = async (req, id) => (await requiredProduct(req, id)).fields;
  const lineageFor = async (req, id) => {
    const product = await productFor(req, id);
    return product ? [...product.lineage.upstream.map((upstream_id) => ({ upstream_id })), ...product.lineage.downstream.map((downstream_id) => ({ downstream_id })), ...product.lineage.origin_paths.map((origin_path) => ({ origin_path })), ...product.lineage.destination_paths.map((destination_path) => ({ destination_path }))] : [];
  };
  const visibleState = (rows, req) => {
    const ids = new Map(req.portalIdentity.products.map((product) => [product.id, product.unit_id]));
    return rows.filter((row) => ids.has(row.product_id)).map((row) => ({ ...row, unit_id: ids.get(row.product_id) }));
  };
  const visibleRows = async (rowsPromise, req) => {
    Promise.resolve(rowsPromise).catch(() => {});
    await loadProducts(req);
    return visibleState(await rowsPromise, req);
  };

  const matchProducts = (products, hits) => products.map((p) => {
    const matches = hits.filter((hit) => hitMatchesProduct(hit, p));
    return { ...p, matches, score: Math.max(0, ...matches.map((hit) => hit.score)) };
  }).filter((p) => p.matches.length).sort((a, b) => b.score - a.score);
  const parentProducts = (result, readable) => ({ ...result, hits: result.hits.map((hit) => {
    const parent = ["column", "document"].includes(hit.kind) && readable.find((p) => hitMatchesProduct(hit, p));
    return parent ? { ...hit, product: { id: parent.id, name: parent.name } } : hit;
  }) });
  app.get("/api/products", asyncRoute(async (req, res) => res.json(await productsFor(req, String(req.query.q || "")))));
  app.post("/api/search", asyncRoute(async (req, res) => {
    const found = await providers.search.run(req, req.body);
    const readable = (await productsFor(req)).filter((product) => !product.portal_only);
    const result = parentProducts(found, readable);
    const products = matchProducts(readable, result.hits);
    res.set("X-Portal-Notice", result.notice).json({ ...result, searchList: products, domainFilter: [...new Set(products.map((item) => item.domain))], sourceFilter: [...new Set(products.map((item) => item.source))], ...catalogueFacets });
  }));
  app.get("/api/products/:id", asyncRoute(async (req, res) => {
    const product = await productFor(req, req.params.id);
    if (!product) return res.status(404).json({ message: "Product not found." });
    res.json(product);
  }));
  app.get("/api/products/:id/fields", asyncRoute(async (req, res) => res.json(await fieldsFor(req, req.params.id))));
  app.get("/api/products/:id/lineage", asyncRoute(async (req, res) => res.json((await requiredProduct(req, req.params.id)).lineage)));
  app.get("/api/preview/:id", tablePreview, asyncRoute(async (req, res) => {
    const product = await dataProduct(req, res, req.params.id);
    if (product) await kgeData(req, "/data/preview", { unit: product.unit_id, table: product.table, limit: Number(req.query.limit || 20) }, res);
  }));
  app.get("/api/export/:id", tablePreview, asyncRoute(async (req, res) => {
    const product = await dataProduct(req, res, req.params.id);
    if (product) await kgeData(req, "/data/export", { unit: product.unit_id, table: product.table }, res);
  }));
  app.post("/api/query", tableQuery, asyncRoute(async (req, res) => {
    const unit = String(req.body.unit || "");
    if (!req.portalIdentity.units.includes(unit)) return res.status(404).json({ message: "Product not found." });
    await kgeData(req, "/data/query", { unit, sql: String(req.body.sql || "") }, res);
  }));
  app.get("/api/role", asyncRoute(async (req, res) => {
    const roles = await access.roles(req.portalIdentity);
    const role = ["admin", "publisher", "approver", "viewer"].find((value) => roles.includes(value));
    res.json({ role, roles });
  }));

  const ownProduct = async (req, res, next) => {
    if (!await productFor(req, req.params.id || req.body.productId)) return res.status(404).json({ message: "Product not found." });
    next();
  };
  app.get("/api/favourites", asyncRoute(async (req, res) => res.json(await visibleRows(repository.favourites(req.portalIdentity.principal, req.portalIdentity.units), req))));
  app.post("/api/favourites/:id", asyncRoute(ownProduct), asyncRoute(async (req, res) => { await repository.addFavourite(req.portalIdentity.principal, req.params.id); res.status(204).end(); }));
  app.delete("/api/favourites/:id", asyncRoute(async (req, res) => { await repository.removeFavourite(req.portalIdentity.principal, req.params.id); res.status(204).end(); }));
  app.get("/api/cart", asyncRoute(async (req, res) => res.json(await visibleRows(repository.cart(req.portalIdentity.principal, req.portalIdentity.units), req))));
  app.post("/api/cart/:id", asyncRoute(ownProduct), asyncRoute(async (req, res) => { await repository.addCart(req.portalIdentity.principal, req.params.id); res.status(204).end(); }));
  app.delete("/api/cart/:id", asyncRoute(async (req, res) => { await repository.removeCart(req.portalIdentity.principal, req.params.id); res.status(204).end(); }));
  app.delete("/api/cart", asyncRoute(async (req, res) => { await repository.emptyCart(req.portalIdentity.principal); res.status(204).end(); }));
  app.post("/api/requests", asyncRoute(ownProduct), asyncRoute(async (req, res) => { await repository.request(req.portalIdentity.principal, req.body.productId, req.body.kind || "access"); res.status(201).json({ status: "OPEN" }); }));
  app.get("/api/requests", asyncRoute(async (req, res) => res.json(await visibleRows(repository.requests(req.portalIdentity.principal, req.portalIdentity.units), req))));
  app.post("/api/feedback", asyncRoute(ownProduct), asyncRoute(async (req, res) => { const rating = req.body.rating; if (!Number.isInteger(rating) || rating < 1 || rating > 5) return res.status(400).json({ message: "rating must be a whole number from 1 to 5." }); await repository.feedback(req.portalIdentity.principal, req.body.productId, rating, String(req.body.comment || "")); res.status(201).end(); }));
  app.get("/api/feedback/:id", asyncRoute(async (req, res) => {
    if (!await productFor(req, req.params.id)) return res.status(404).json({ message: "Product not found." });
    res.json(await repository.averageFeedback(req.params.id));
  }));

  const roleRequired = (...allowed) => asyncRoute(async (req, res, next) => !req.portalIdentity.fallback && (await access.roles(req.portalIdentity)).some((role) => allowed.includes(role)) ? next() : res.status(403).json({ message: "Portal role required." }));
  app.get("/api/admin/prompt-direction", roleRequired("admin"), asyncRoute(async (_req, res) => res.json(await repository.promptDirection())));
  app.put("/api/admin/prompt-direction", roleRequired("admin"), asyncRoute(async (req, res) => {
    if (typeof req.body?.text !== "string" || req.body.text.length > 2000) return res.status(400).json({ message: "Prompt direction must be text of up to 2000 characters." });
    res.json(await repository.setPromptDirection(req.body.text, req.portalIdentity.principal));
  }));
  app.get("/api/admin/users", asyncRoute(async (req, res) => res.json(await access.list(req.portalIdentity))));
  app.get("/api/admin/users/audit", asyncRoute(async (req, res) => res.json(await access.audit(req.portalIdentity))));
  for (const [method, grant] of [["put", true], ["delete", false]]) {
    app[method]("/api/admin/users/:username/roles/:role", asyncRoute(async (req, res) => {
      if (req.body && Object.keys(req.body).length) return res.status(400).json({ message: "This operation takes no body." });
      res.json(await access.change(req.portalIdentity, req.params.username, req.params.role, grant));
    }));
  }
  app.put("/api/admin/products/:id/published", roleRequired("admin", "publisher"), asyncRoute(ownProduct), asyncRoute(async (req, res) => { await repository.setPublished(req.params.id, Boolean(req.body.published)); res.status(204).end(); }));
  app.patch("/api/admin/products/:id/metadata", roleRequired("admin", "publisher"), asyncRoute(ownProduct), asyncRoute(async (req, res) => {
    const patch = metadataPatch(req.body);
    const original = await repository.product(req.params.id, req.portalIdentity.units);
    if (patch.fields?.some((f) => !original.fields.some((held) => held.name === f.name))) return res.status(400).json({ message: "A field must already exist in the portal record." });
    await repository.updateMetadata(req.params.id, patch);
    req.portalIdentity.products = undefined;
    res.json(await productFor(req, req.params.id));
  }));
  app.post("/api/admin/onboarding-input", roleRequired("admin", "publisher"), asyncRoute(async (req, res) => {
    res.json(metadataInput(await loadProducts(req), req.body.client));
  }));
  const approver = roleRequired("admin", "approver");
  app.get("/api/admin/requests", approver, asyncRoute(async (req, res) => res.json(await visibleRows(repository.queue(), req))));
  app.post("/api/admin/requests/:id/decision", approver, asyncRoute(async (req, res) => {
    const status = { approve: "APPROVED", reject: "REJECTED" }[req.body.decision];
    if (!status) return res.status(400).json({ message: "decision must be approve or reject." });
    const [pending] = await visibleRows(Promise.resolve(repository.pending(req.params.id)).then((row) => row ? [row] : []), req);
    if (!pending) return res.status(404).json({ message: "No open request." });
    await repository.decide(req.params.id, status, req.portalIdentity.principal);
    res.json({ id: req.params.id, status });
  }));

  const legacy = express.Router();
  const route = (method, path, ...handlers) => legacy[method](path, discover, identity, ...handlers.map(asyncRoute));
  const visible = async (req, res, id) => {
    const value = await productFor(req, id);
    if (!value) res.status(404).json({ message: "Product not found." });
    return value;
  };
  const productIdOf = (req) => req.body.productId ?? req.body.technicalName ?? req.body.product_id;
  const legacyProducts = async (req, search = "", options = {}) => {
    const favsPromise = Promise.resolve(repository.favourites(req.portalIdentity.principal, req.portalIdentity.units));
    favsPromise.catch(() => {});
    const products = await productsFor(req, search, options);
    const favs = new Set((await favsPromise).map((item) => item.product_id));
    return products.map((product) => legacyProduct(product, favs.has(product.id)));
  };
  const searchTerm = (value) => value === undefined || value === "0" || value === "undefined" || value === "null" ? "" : String(value);
  const publishRow = (product) => ({ COMMUNITY_NAME: product.community, DOMAIN_NAME: product.domain, TECHNICAL_NAME: product.id, PRODUCTNAME: product.name, PUBLISHED_STATUS: Boolean(product.published) });
  const publishRows = async (req, search = "", domain = "") => (await productsFor(req, search, { unpublished: true })).filter((product) => !domain || product.domain === domain).map(publishRow);
  const facetFilter = (filterParams) => {
    const facets = new Map();
    for (const option of filterParams) {
      if (!["COMMUNITY", "DOMAIN", "PRODUCTSOURCE", "PRODUCTTYPE", "ASSETTYPE", "USAGEPURPOSE"].includes(option?.parent) || typeof option.value !== "string") continue;
      if (!facets.has(option.parent)) facets.set(option.parent, new Set());
      facets.get(option.parent).add(option.value);
    }
    return (item) => [...facets].every(([field, values]) => values.has(item[field]));
  };
  const searchResponse = async (req, inScope) => {
    const input = searchInput(req.body || {});
    const found = input.query ? await providers.search.run(req, input, inScope && ((p) => inScope(legacyProduct({ ...p, source: p.source ?? p.application })))) :{ provider: "portal", fallback: Boolean(req.portalIdentity.fallback), notice: req.portalIdentity.fallback ? fallbackNotice : "", hits: [] };
    const favourites = new Set((await repository.favourites(req.portalIdentity.principal, req.portalIdentity.units)).map((f) => f.product_id));
    const readable = await productsFor(req);
    const result = parentProducts(found, readable);
    const answering = input.query ? readable.filter((product) => !product.portal_only) : readable;
    const products = (input.query ? matchProducts(answering, result.hits) : readable).map((p) => ({ ...legacyProduct(p, favourites.has(p.id)), matches: p.matches }));
    return { ...result, searchList: products, communityFilter: [...new Set(products.map((item) => item.COMMUNITY).filter(Boolean))], domainFilter: [...new Set(products.map((item) => item.DOMAIN).filter(Boolean))], sourceFilter: [...new Set(products.map((item) => item.PRODUCTSOURCE).filter(Boolean))], ...catalogueFacets, hierarchy: [...new Set(products.map((item) => item.HIERARCHY))] };
  };

  route("get", "/product/:id", async (req, res) => {
    const value = await visible(req, res, req.params.id);
    if (!value) return;
    const favourites = await repository.favourites(req.portalIdentity.principal, req.portalIdentity.units);
    res.json(legacyProduct(value, favourites.some((item) => item.product_id === value.id)));
  });
  route("post", "/get-data-preview", tablePreview, async (req, res) => {
    const product = await visible(req, res, productIdOf(req));
    if (!product) return;
    if (!providers.data.enabled) return providers.data.route(req, res);
    res.removeHeader("X-Portal-Notice");
    const response = await dataCall(() => kge.request(req, "/data/preview", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ unit: product.unit_id, table: product.table, limit: Number(req.body.limit || req.body.maxLimit || 20) }) }));
    if (!response.ok) return relay(response, res);
    const value = await dataCall(() => response.json());
    res.removeHeader("X-Portal-Notice");
    res.json(value.rows);
  });
  route("get", "/preview/:id", tablePreview, async (req, res) => {
    const product = await visible(req, res, req.params.id);
    if (!product) return;
    if (!providers.data.enabled) return providers.data.route(req, res);
    res.removeHeader("X-Portal-Notice");
    const response = await dataCall(() => kge.request(req, "/data/preview", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ unit: product.unit_id, table: product.table, limit: Number(req.query.limit || 20) }) }));
    if (!response.ok) return relay(response, res);
    const value = await dataCall(() => response.json());
    res.removeHeader("X-Portal-Notice");
    res.json(value.rows);
  });
  route("get", "/get-data-export/:id", tablePreview, async (req, res) => {
    const product = await visible(req, res, req.params.id);
    if (product) await kgeData(req, "/data/export", { unit: product.unit_id, table: product.table }, res);
  });
  route("post", "/execute-query", tableQuery, async (req, res) => {
    const unit = String(req.body.unit || "");
    if (!req.portalIdentity.units.includes(unit)) return res.status(404).json({ message: "Product not found." });
    await kgeData(req, "/data/query", { unit, sql: String(req.body.sql || req.body.sqlText || "") }, res);
  });
  route("get", "/tables", async (req, res) => res.json((await loadProducts(req)).map((product) => ({ unit: product.unit_id, table: product.table, productId: product.id }))));
  route("post", "/updatePreviewStatus", async (_req, res) => res.json({ status: "Success" }));
  const fieldRows = async (req, id) => (await fieldsFor(req, id)).map((field) => ({ "Field Name": field.name, "Business Name": field.business_name, Description: field.description, "Data Type": field.data_type, Classification: classificationLabel(field.classification), PII: field.pii === true ? "Yes" : "", "Data Quality Score": ruleScore(qualityRules(field.quality).find((rule) => !blankFail(rule))), Terms: field.terms }));
  route("get", "/fields/:id", async (req, res) => res.json(await fieldRows(req, req.params.id)));
  route("get", "/lineage/:id", async (req, res) => {
    const product = await productFor(req, req.params.id);
    if (!product) return res.status(404).json({ message: "Product not found." });
    const incoming = [...product.lineage.origin_paths, ...product.lineage.upstream];
    const outgoing = [...product.lineage.destination_paths, ...product.lineage.downstream];
    const nodes = [{ id: product.id, data: { label: product.name }, position: { x: 320, y: 150 } }, ...incoming.map((name, index) => ({ id: `in-${index}`, data: { label: name }, position: { x: 0, y: index * 100 } })), ...outgoing.map((name, index) => ({ id: `out-${index}`, data: { label: name }, position: { x: 640, y: index * 100 } }))];
    res.json({ nodes, edges: [...incoming.map((_name, index) => ({ id: `in-${index}->${product.id}`, source: `in-${index}`, target: product.id })), ...outgoing.map((_name, index) => ({ id: `${product.id}->out-${index}`, source: product.id, target: `out-${index}` }))] });
  });
  route("post", "/search", async (req, res) => { const result = await searchResponse(req); res.set("X-Portal-Notice", result.notice).json(result); });
  for (const path of ["/sortSearchResults", "/groupSearchResults", "/filterSearchResults"]) route("post", path, async (req, res) => {
    const scope = req.body?.browseScope;
    const scoped = scope && typeof scope === "object" && !Array.isArray(scope) && typeof scope.community === "string" && typeof scope.domain === "string";
    const inFacets = path === "/filterSearchResults" && Array.isArray(req.body.filterParams) ? facetFilter(req.body.filterParams) : null;
    const result = await searchResponse(scoped ? { ...req, body: {} } : req, inFacets);
    let rows = result.searchList;
    if (scoped) rows = rows.filter((item) => (item.COMMUNITY || "Unclassified") === scope.community && item.DOMAIN === scope.domain);
    if (inFacets) rows = rows.filter(inFacets);
    res.set("X-Portal-Notice", result.notice).json(rows);
  });
  route("post", "/getCommunityProducts", async (req, res) => {
    const all = await searchResponse({ ...req, body: {} });
    const value = String(req.body.searchParam || "");
    const field = req.body.isDomain === true ? "DOMAIN" : "COMMUNITY";
    const searchList = value ? all.searchList.filter((item) => (field === "COMMUNITY" ? item.COMMUNITY || "Unclassified" : item.DOMAIN) === value && (!req.body.domain || item.DOMAIN === req.body.domain)) : all.searchList;
    const facets = (key) => [...new Set(searchList.map((item) => item[key]).filter(Boolean))];
    res.json({ ...all, searchList, communityFilter: facets("COMMUNITY"), domainFilter: facets("DOMAIN"), sourceFilter: facets("PRODUCTSOURCE"), ...catalogueFacets, hierarchy: facets("HIERARCHY") });
  });
  route("get", "/getSearchData/:searchVal", async (req, res) => res.json((await providers.search.suggest(req, { query: searchTerm(req.params.searchVal) })).map((hit) => ({ value: hit.title }))));
  route("post", "/add-to-cart", async (req, res) => {
    if (!await visible(req, res, productIdOf(req))) return;
    await repository.addCart(req.portalIdentity.principal, productIdOf(req));
    res.status(200).json({ message: "Item added." });
  });
  route("post", "/removeItem", async (req, res) => { await repository.removeCart(req.portalIdentity.principal, productIdOf(req)); res.status(200).json({ message: "Item removed." }); });
  route("get", "/cart-items/:userId", async (req, res) => res.json(await visibleRows(repository.cart(req.portalIdentity.principal, req.portalIdentity.units), req)));
  route("post", "/empty-cart/:userId", async (req, res) => { await repository.emptyCart(req.portalIdentity.principal); res.status(200).json({ message: "Cart emptied." }); });
  for (const [path, kind] of [["/submit-request", "access"], ["/subscribe", "subscription"], ["/checkout", "checkout"]]) route("post", path, async (req, res) => {
    if (!await visible(req, res, productIdOf(req))) return;
    await repository.request(req.portalIdentity.principal, productIdOf(req), kind);
    res.status(201).json({ status: "OPEN" });
  });
  route("get", "/myRequests/:userId", async (req, res) => res.json(await visibleRows(repository.requests(req.portalIdentity.principal, req.portalIdentity.units), req)));
  route("get", "/subscriptionStatus/:userId/:productId", async (req, res) => {
    if (!await visible(req, res, req.params.productId)) return;
    const open = visibleState(await repository.requests(req.portalIdentity.principal, req.portalIdentity.units), req).filter((item) => item.product_id === req.params.productId);
    res.json(open.map((item) => ({ STATUS: item.status, KIND: item.kind })));
  });
  route("post", "/submit-feedback", async (req, res) => {
    if (!await visible(req, res, productIdOf(req))) return;
    const rating = req.body.rating;
    if (!Number.isInteger(rating) || rating < 1 || rating > 5) return res.status(400).json({ message: "rating must be a whole number from 1 to 5." });
    await repository.feedback(req.portalIdentity.principal, productIdOf(req), rating, String(req.body.comment || ""));
    res.status(201).end();
  });
  route("get", "/avg-feedback/:id", async (req, res) => {
    if (!await productFor(req, req.params.id)) return res.status(404).json({ message: "Product not found." });
    res.json(await repository.averageFeedback(req.params.id));
  });
  route("post", "/feedback-add", async (req, res) => {
    const productId = productIdOf(req);
    const session = Number(req.body.session_id);
    const question = Number(req.body.user_query_seq_id);
    const rating = req.body.feedback_rating;
    if (!Number.isInteger(session) || !Number.isInteger(question) || ![0, 1].includes(rating)) return res.status(400).json({ message: "session, question and thumbs direction are required." });
    const readable = await productsFor(req);
    const product = productId ? matchReadableProduct(readable, productId) : null;
    if (productId && !product) return res.status(404).json({ message: "Product not found." });
    const requested = Array.isArray(req.body.productIds) ? req.body.productIds.map((id) => typeof id === "string" && matchReadableProduct(readable, id)) : [];
    if (requested.some((p) => !p)) return res.status(404).json({ message: "Product not found." });
    const scope = product ? { product: canonicalProductId(product) } : { domain: String(req.body.domain || ""), products: [...new Set(requested.map(canonicalProductId))] };
    if (!productId && !scope.domain && !scope.products.length) return res.status(400).json({ message: "feedback scope is required." });
    await repository.askFeedback(req.portalIdentity.principal, session, question, scope, rating === 1 ? "up" : "down", String(req.body.feedback_comment || ""));
    res.status(201).end();
  });
  for (const path of ["/ask-data-product", "/ask-domain"]) route("post", path, async (req, res) => askScoped(req, res, { ...req.body, question: String(req.body.question || req.body.userPrompt || "") }));
  route("get", "/dataQuality/:id/:type", async (req, res) => {
    const fields = await fieldsFor(req, req.params.id);
    const rules = fields.flatMap((field) => qualityRules(field.quality).map((rule) => ({ field, rule })));
    // All lists real rules first, then the placeholder and empty rows, each in field order.
    const named = ({ rule }) => Boolean(rule.rule) && !placeholderRule(rule);
    const selected = req.params.type === "all"
      ? [...rules.filter(named), ...rules.filter((row) => !named(row))]
      : rules.filter(({ rule }) => rule.rule_type.toLowerCase().startsWith(req.params.type));
    res.json(selected.map(({ field, rule }) => ({ "Data Element Name": field.business_name, "Data Quality Rule Name": rule.rule, "Rule description": rule.description || "", "Data Quality Rule Type": rule.rule_type, "Data Quality Dimension": rule.dimension, "Data Quality Score": ruleScore(rule), "Failing %": blankFail(rule) ? "" : rule.fail_percent })));
  });
  route("post", "/filteredFields", async (req, res) => {
    const value = String(req.body.value || "").toLowerCase();
    res.json((await fieldRows(req, productIdOf(req))).filter((row) => !value || String(row[req.body.field] ?? row["Field Name"]).toLowerCase().includes(value)));
  });
  route("get", "/dropdown-values/:fieldName", async (req, res) => res.json([...new Set((await legacyProducts(req)).map((product) => product[String(req.params.fieldName).toUpperCase()]).filter(Boolean))]));
  route("get", "/getFavourites", async (req, res) => {
    const favourites = new Set((await repository.favourites(req.portalIdentity.principal, req.portalIdentity.units)).map((item) => item.product_id));
    res.json((await productsFor(req)).filter((product) => favourites.has(product.id)).map((product) => ({ DATA_PRODUCT_ID: product.id, PRODUCT_NAME: product.name, TECHNICAL_NAME: product.id, DATA_PRODUCT_TYPE: product.product_type, SUBSCRIBERS: product.counts.subscribe, DATA_DOMAIN_NAME: product.domain })));
  });
  route("post", "/saveFavouriteProduct", async (req, res) => {
    if (!await visible(req, res, productIdOf(req))) return;
    await repository.addFavourite(req.portalIdentity.principal, productIdOf(req));
    res.json({ status: "Success" });
  });
  route("post", "/removeFavouriteProduct", async (req, res) => { await repository.removeFavourite(req.portalIdentity.principal, productIdOf(req)); res.json({ status: "Success" }); });
  route("post", "/saveViewedDataProduct", async (req, res) => {
    if (await productFor(req, productIdOf(req))) await repository.view(req.portalIdentity.principal, productIdOf(req));
    res.json({ status: "Success" });
  });
  for (const path of ["/getMostViewedProducts", "/getRelatedProducts", "/recent-products/:userId", "/recommended-products/:userId"]) route("get", path, async (req, res) => res.json(await legacyProducts(req)));
  for (const path of ["/filteredRecentProducts", "/filteredRecommendedProducts"]) route("post", path, async (req, res) => res.json(await legacyProducts(req, String(req.body.searchParam || ""))));
  route("get", "/getDataCommunities", async (req, res) => {
    const products = await legacyProducts(req);
    const groups = new Map();
    for (const item of products) {
      const community = item.COMMUNITY || "Unclassified";
      const domain = item.DOMAIN || "";
      const id = JSON.stringify([domain, community]);
      if (!groups.has(id)) groups.set(id, { id, kind: "community", name: community, val: community, domain, desc: domain, count: 0, children: [] });
      const group = groups.get(id);
      group.children.push({ id: item.PRODUCTID, kind: "product", name: item.PRODUCTNAME, val: item.PRODUCTNAME });
      group.count += 1;
    }
    const communityList = [...groups.values()];
    res.json({ communityList, treeExpandList: [], totalCount: products.length });
  });
  route("get", "/getCommunityHierarchy", async (req, res) => res.json((await legacyProducts(req)).map((item) => ({ COMMUNITY: item.COMMUNITY, DOMAIN: item.DOMAIN, PRODUCTNAME: item.PRODUCTNAME }))));
  route("get", "/metrics/:userId", async (req, res) => {
    const [products, favourites, requests] = await Promise.all([legacyProducts(req), repository.favourites(req.portalIdentity.principal, req.portalIdentity.units), repository.requests(req.portalIdentity.principal, req.portalIdentity.units)]);
    res.json({ products: products.length, favourites: visibleState(favourites, req).length, requests: visibleState(requests, req).length });
  });
  route("get", "/getConfig", async (req, res) => {
    res.json({ Application_Title: settings.productLongName, Application_Desc: settings.productSubtitle, preview: settings.tablePreview && providers.data.enabled, export: settings.tablePreview && providers.data.enabled, executeQuery: settings.tableQuery && providers.data.enabled, dqFilters: settings.dqFilters, ask: providers.ask.enabled });
  });
  route("post", "/trackMarkeplaceUser", async (_req, res) => res.json({ status: "Success" }));

  const publisher = roleRequired("admin", "publisher");
  route("get", "/getPublishedStatus", publisher, async (req, res) => res.json(await publishRows(req)));
  route("get", "/getPublishSearchData/:searchVal", publisher, async (req, res) => res.json((await publishRows(req, searchTerm(req.params.searchVal))).map((item) => ({ value: item.PRODUCTNAME, key: item.TECHNICAL_NAME }))));
  route("get", "/getPublishedSearchResult/:searchVal", publisher, async (req, res) => res.json(await publishRows(req, searchTerm(req.params.searchVal))));
  route("get", "/getFilterResult/:filterVal", publisher, async (req, res) => res.json(await publishRows(req, "", searchTerm(req.params.filterVal))));
  route("get", "/getDomainList", publisher, async (req, res) => res.json([...new Set((await publishRows(req)).map((item) => item.DOMAIN_NAME))].map((domain) => ({ value: domain, key: domain }))));
  route("post", "/updatePublishedStatus", publisher, async (req, res) => {
    const id = productIdOf(req);
    if (!await visible(req, res, id)) return;
    const flag = req.body.publishedStatus ?? req.body.published ?? req.body.publishStatus;
    const published = flag === true || flag === "Y" || flag === "true";
    await repository.setPublished(id, published);
    res.json([{ TECHNICAL_NAME: id, PUBLISHED_STATUS: published }]);
  });
  for (const path of ["/publishStatusLiveSync", "/updateApplicationLiveSync"]) route("get", path, publisher, async (_req, res) => res.json([{ status: "Success" }]));
  route("get", "/getUserData", roleRequired("admin"), async (_req, res) => res.json((await repository.users()).map((user) => ({ userId: user.principal_id, userName: user.principal_id, role: user.role }))));
  app.use(legacy);

  brandRoutes(app, { env, ui, configured: configuredLogo, ask: askOn });
  app.use("/context-app", contextPage(contextOn));
  app.use("/context", (_req, res, next) => contextOn ? next() : notSetUp(res, "context"));
  app.use(["/api", "/brand"], (_req, res) => res.status(404).json({ detail: "Not found." }));
  if (existsSync(ui)) {
    app.use(express.static(ui));
    app.get(/.*/, (_req, res) => res.sendFile(resolve(ui, "index.html")));
  }
  app.use((error, _req, res, _next) => {
    if (error.status === 429) return tooManyRequests(res, error.retryAfter, error.field);
    if (["TimeoutError", "AbortError"].includes(error.name)) return res.status(504).json({ detail: "The request exceeded its time limit." });
    if (error.status === 401) return res.status(401).json({ [error.field || "detail"]: "Sign in required." });
    if (["entity.parse.failed", "entity.too.large"].includes(error.type)) return res.status(error.status).json({ detail: "Invalid request body." });
    if (Number.isInteger(error.status) && error.status >= 400 && error.status < 500) return res.status(error.status).json({ detail: error.message });
    if ([502, 503].includes(error.status)) return res.status(error.status).json({ [error.field || "detail"]: error.message });
    console.error(error.message);
    res.status(500).json({ message: "Portal error." });
  });
  return { app, repository, stopCatalogueSync, refreshCatalogue };
}
