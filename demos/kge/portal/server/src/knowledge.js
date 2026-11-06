export const fallbackNotice = "Live data is unavailable. Using the portal catalogue; data answers and figures are unavailable.";
export const unreachable = (error) => !error.status || error.status >= 500 || error.status === 408;
const kinds = new Set(["product", "term", "metric", "column", "document"]);
const invalid = (detail) => Object.assign(new Error(detail), { status: 400 });
export function searchInput(body = {}) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw invalid("Search needs an object body.");
  const query = body.query ?? body.searchParam ?? "";
  const limit = body.limit ?? 20;
  if (typeof query !== "string" || query.length > 2000 || !Number.isInteger(limit) || limit < 1 || limit > 100 || (body.kinds != null && (!Array.isArray(body.kinds) || body.kinds.some((kind) => !kinds.has(kind))))) throw invalid("Invalid search query, kinds or limit.");
  return { query: query.trim(), limit, ...(body.kinds ? { kinds: body.kinds } : {}) };
}
const words = (text) => text.toLowerCase().match(/[a-z0-9_]+/g) || [];
function score(query, text) {
  const tokens = words(query);
  if (!tokens.length) return 1;
  const value = text.toLowerCase();
  return tokens.filter((token) => value.includes(token)).length / tokens.length;
}
export function catalogueSearch(products, input) {
  const hits = [];
  const add = (kind, product, id, title, text) => {
    const rank = score(input.query, text);
    if (rank && (!input.kinds || input.kinds.includes(kind))) hits.push({ kind, unit: product.unit_id, id, title, score: rank, why: { kind: "description", text } });
  };
  for (const p of products) {
    add("product", p, p.id, p.name, [p.name, p.description, p.product_owner, p.business_owner, p.domain, p.community, p.application, p.APM_ID].join(" · "));
    if (input.query || input.kinds?.includes("column")) for (const f of p.fields) add("column", p, `${p.id}.${f.name}`, `${p.name}: ${f.business_name}`, [f.name, f.business_name, f.description || "", ...(f.terms || [])].join(" · "));
  }
  hits.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  return { hits: hits.slice(0, input.limit) };
}
export function catalogueDiscovery(products, input, partial = true) {
  const tokens = input.query.toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  if ((input.query && !tokens.length) || (input.kinds && !input.kinds.includes("product"))) return [];
  const candidates = products.flatMap((p) => {
    const fields = [p.name, p.description, p.domain, p.community, p.application, p.APM_ID,
      ...p.fields.flatMap((f) => [f.name, f.business_name, f.description, ...(f.terms || [])])].filter(Boolean);
    const weights = tokens.map((token) => Math.max(0, ...fields.map((field) => field.toLowerCase().includes(token) ? field === p.name ? 1 : field === p.description ? .9 : .8 : 0)));
    if (tokens.length && !weights.some(Boolean)) return [];
    const full = weights.every(Boolean);
    const rank = full ? .8 : (.6 * weights.filter(Boolean).length + .1 * weights.reduce((sum, weight) => sum + weight, 0)) / tokens.length;
    return [{ kind: "product", unit: p.unit_id, id: p.id, title: p.name, score: rank, why: { kind: "description", text: fields.filter((field) => tokens.some((token) => field.toLowerCase().includes(token))).join(" · ") } }];
  });
  const full = candidates.filter((hit) => hit.score === .8);
  return full.length ? full : partial && tokens.length > 1 ? candidates.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id)) : [];
}
export const hitMatchesProduct = (hit, p) => hit.unit === p.unit_id &&
  [p.id, p.table, canonicalProductId(p)].filter(Boolean).some((id) => hit.id === id || (hit.kind !== "document" && hit.id.startsWith(`${id}.`)));
export const canonicalProductId = (p) => p.unit_id && p.table ? `${p.unit_id}.${p.table}` : p.id;
export const matchReadableProduct = (products, rawId) => products.find((p) => p.id === String(rawId || "").trim() || canonicalProductId(p) === String(rawId || "").trim()) || null;
export function productScope(products, body) {
  const groups = [];
  if (body.productId !== undefined) groups.push([body.productId]);
  for (const key of ["productIds", "products"]) if (body[key] !== undefined) {
    if (!Array.isArray(body[key])) throw invalid(`${key} must be an array.`);
    groups.push(body[key]);
  }
  if (!groups.length) return { scoped: false, metadata: body.domain ? { domain: String(body.domain) } : {} };
  const resolved = groups.map((ids) => ids.map((id) => {
    const product = typeof id === "string" && matchReadableProduct(products, id);
    if (!product) throw Object.assign(new Error("Product not found."), { status: 404 });
    return product;
  }));
  const selected = [...new Set(resolved[0])].filter((p) => resolved.every((group) => group.some((other) => other.id === p.id)));
  const ids = [...new Set(selected.map(canonicalProductId))];
  const metadata = { products: ids, ...(body.productId !== undefined ? { productId: resolved[0][0].id } : {}), ...(body.domain ? { domain: String(body.domain) } : {}) };
  return { scoped: true, selected, metadata, name: ids.length ? `${body.domain ? `${body.domain}: ` : ""}${ids.join(", ")}` : "an empty product view" };
}
export function catalogueAsk(products, body) {
  if (!body || typeof body.question !== "string" || !body.question.trim() || body.question.length > 2000) throw invalid("A question is required (up to 2000 characters).");
  const scope = productScope(products, body);
  const question = body.question.trim().replace(/[?.!]$/, "").trim();
  const owner = /^who owns (.+)$/i.exec(question);
  const fields = /^(?:(?:what|which) (?:fields|columns) (?:are in (.+)|does (.+) (?:have|hold|contain))|what does (.+) contain)$/i.exec(question);
  const subject = (owner?.[1] ?? fields?.slice(1).find(Boolean))?.toLowerCase();
  const candidates = (scope.scoped ? scope.selected : products).filter((p) =>
    [p.name, p.id, canonicalProductId(p)].some((name) => name.toLowerCase() === subject));
  let answer = "Catalogue only: the readable portal catalogue cannot answer this question. It can identify product owners and fields; other answers require Context.";
  let behaviour = scope.scoped ? "abstain" : "abstain_catalogue_only";
  if (candidates.length) {
    answer = candidates.slice(0, 10).map((p) => owner ? `${p.name}: product owner ${p.product_owner}; business owner ${p.business_owner}.` : `${p.name} contains these fields: ${p.fields.map((f) => `${f.business_name} (${f.name})`).join(", ")}.`).join("\n\n");
    behaviour = "catalogue";
  }
  return { answer_text: answer, behaviour, figures: [], citations: [], phrased_by: "catalogue", ...scope.metadata };
}
export function localKnowledge(repository) {
  const hiddenIds = async (rows) => new Set((rows.length ? await (repository.publication ? repository.publication(rows.map((p) => p.id)) : Promise.all(rows.map(async (p) => ({ product_id: p.id, published: await repository.published(p.id) })))) : [])
    .filter((row) => row.published === 0 || row.published === false).map((row) => row.product_id));
  const loadRows = async (req) => {
    let hiddenPromise;
    const startHidden = (ids) => {
      hiddenPromise = hiddenIds(ids.map((id) => ({ id })));
      hiddenPromise.catch(() => {});
    };
    try {
      if (!req.portalIdentity.products) {
        req.portalIdentity.products = req.portalIdentity.loadProducts
          ? await req.portalIdentity.loadProducts(startHidden)
          : await repository.listProducts(req.portalIdentity.units, "", startHidden);
      }
      const rows = req.portalIdentity.products;
      const hidden = await (hiddenPromise || hiddenIds(rows));
      return { rows, hidden };
    } finally {
      await hiddenPromise?.catch(() => {});
    }
  };
  const products = async (req) => {
    const { rows, hidden } = await loadRows(req);
    return rows.filter((p) => !hidden.has(p.id));
  };
  return {
    async visibleHits(req, hits, input) {
      const { rows, hidden: ids } = await loadRows(req);
      const readable = rows.filter((p) => req.portalIdentity.units.includes(p.unit_id) && !ids.has(p.id));
      const visible = hits.filter((hit) => readable.some((p) => hitMatchesProduct(hit, p)));
      const extra = catalogueDiscovery(readable, input);
      const partial = (hit) => hit.kind === "product" && hit.score < .8 && hit.why.kind === "description" && !hit.why.via?.length;
      const candidates = [...visible, ...extra];
      const full = candidates.some((hit) => hit.kind === "product" && !partial(hit));
      const merged = new Map();
      for (const hit of candidates.filter((hit) => !full || !partial(hit))) {
        const product = hit.kind === "product" && readable.find((p) => hitMatchesProduct(hit, p));
        const key = JSON.stringify([hit.kind, hit.unit, product ? product.id : hit.id]);
        if (!merged.has(key) || merged.get(key).score < hit.score) merged.set(key, hit);
      }
      return [...merged.values()]
        .sort((a, b) => b.score - a.score || a.kind.localeCompare(b.kind) || a.unit.localeCompare(b.unit) || a.id.localeCompare(b.id)).slice(0, input.limit);
    },
    async suggest(req, body) {
      const { rows, hidden } = await loadRows(req);
      return catalogueDiscovery(rows.filter((p) => !hidden.has(p.id)), searchInput(body));
    },
    async search(req, body) {
      const input = searchInput(body);
      return { hits: catalogueDiscovery(await products(req), input).slice(0, input.limit) };
    },
    ask: async (req, body) => catalogueAsk(await products(req), body),
  };
}
export function knowledgeProvider(kind, local, kge) {
  return {
    enabled: true,
    suggest: (req, body) => local.suggest(req, body),
    async run(req, body) {
      const input = kind === "search" ? searchInput(body) : body;
      if (kge && (kind === "ask" || !req.portalIdentity.fallback) && !(kind === "search" && !input.query)) {
        let value;
        try {
          const response = await kge.request(req, kind === "ask" ? "/assist" : `/${kind}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(kind === "ask" ? Object.fromEntries(Object.entries(input).filter(([key]) => !["productId", "productIds", "domain"].includes(key))) : input) });
          if (!response.ok) {
            const body = await response.json().catch(() => ({}));
            throw Object.assign(new Error(typeof body.detail === "string" ? body.detail : `Context returned ${response.status}`), { status: response.status, retryAfter: response.headers.get("retry-after") });
          }
          value = await response.json();
          if (kind === "search" && (!Array.isArray(value.hits) || value.hits.some((h) => !h || !kinds.has(h.kind) || typeof h.unit !== "string" || typeof h.id !== "string" || typeof h.title !== "string" || !Number.isFinite(h.score) || h.score < 0 || h.score > 1 || !h.why || !["term", "synonym", "passage", "name", "description"].includes(h.why.kind) || typeof h.why.text !== "string"))) throw new Error("Invalid Context search response");
          if (kind === "ask") {
            if (typeof value.answer_text !== "string" || (value.figures != null && !Array.isArray(value.figures)) || (value.citations != null && !Array.isArray(value.citations))) throw new Error("Invalid Context Ask response");
            value.figures ??= [];
            value.citations ??= [];
          }
        } catch (error) {
          if (!unreachable(error)) throw error;
          return { ...await local[kind](req, input), provider: "portal", fallback: true, notice: fallbackNotice };
        }
        if (kind === "search") {
          value.hits = value.hits.filter((h) => req.portalIdentity.units.includes(h.unit));
          if (local.visibleHits) value.hits = await local.visibleHits(req, value.hits, input);
        }
        if (kind === "ask") {
          if (input.products !== undefined) value.products ??= input.products;
          if (input.productId !== undefined) value.productId = input.productId;
          if (input.domain !== undefined) value.domain = input.domain;
        }
        return { ...value, provider: "kge", notice: typeof value.notice === "string" ? value.notice : "", fallback: false };
      }
      const fallback = Boolean(kge && req.portalIdentity.fallback);
      return { ...await local[kind](req, input), provider: "portal", fallback, notice: fallback ? fallbackNotice : "" };
    },
    async route(req, res, _path, body) {
      const value = await this.run(req, body);
      res.set("X-Portal-Notice", value.notice).json(value);
    },
  };
}
