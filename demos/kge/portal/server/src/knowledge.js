export const fallbackNotice = "KGE is unavailable. Using the portal catalogue; data answers and figures are unavailable.";
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
    add("product", p, p.id, p.name, [p.name, p.description, p.product_owner, p.business_owner, p.domain, p.community, p.application].join(" · "));
    if (input.query || input.kinds?.includes("column")) for (const f of p.fields) add("column", p, `${p.id}.${f.name}`, `${p.name}: ${f.business_name}`, [f.name, f.business_name, f.description || ""].join(" · "));
  }
  hits.sort((a, b) => b.score - a.score || a.id.localeCompare(b.id));
  return { hits: hits.slice(0, input.limit) };
}
export function catalogueAsk(products, body) {
  if (!body || typeof body.question !== "string" || !body.question.trim() || body.question.length > 2000) throw invalid("A question is required (up to 2000 characters).");
  const q = body.question.toLowerCase();
  const owner = /\b(owns?|owner|responsible)\b/.test(q);
  const contains = /\b(contains?|holds?|fields?|columns?|products?|catalogue|catalog|describ\w*)\b/.test(q);
  const subjectFree = products.reduce((text, p) => text.replaceAll(p.name.toLowerCase(), "").replaceAll(p.table.toLowerCase(), ""), q);
  const data = /\b(total|sum|average|count|how many|how much|revenue|balance|amount|figure|rows?|records?|select)\b/.test(subjectFree);
  const candidates = products.map((p) => ({ p, score: Math.max(score(p.name, q), score(p.table, q), ...p.fields.map((f) => q.includes(f.name.toLowerCase()) || q.includes(f.business_name.toLowerCase()) ? 1 : 0)) })).filter((item) => item.score >= 0.5).map(({ p }) => p);
  let answer = "Catalogue only: I can describe products, their owners and fields. Data answers and figures require KGE.";
  let behaviour = "abstain_catalogue_only";
  if ((owner || contains) && (!data || /\b(field|column|contains?|holds?)\b/.test(q)) && candidates.length) {
    answer = candidates.slice(0, 10).map((p) => owner ? `${p.name}: product owner ${p.product_owner}; business owner ${p.business_owner}.` : `${p.name} contains these fields: ${p.fields.map((f) => `${f.business_name} (${f.name})`).join(", ")}.`).join("\n\n");
    behaviour = "catalogue";
  }
  return { answer_text: answer, behaviour, figures: [], citations: [], phrased_by: "catalogue" };
}
export function localKnowledge(repository) {
  const products = async (req) => {
    const rows = await repository.listProducts(req.portalIdentity.units);
    return (await Promise.all(rows.map(async (p) => await repository.published(p.id) === 0 || await repository.published(p.id) === false ? null : p))).filter(Boolean);
  };
  return {
    async visibleHits(req, hits) {
      const rows = await repository.listProducts(req.portalIdentity.units);
      const hidden = (await Promise.all(rows.map(async (p) => [p, await repository.published(p.id)]))).filter(([, published]) => published === 0 || published === false).map(([p]) => p);
      return hits.filter((hit) => !hidden.some((p) => [p.id, `${p.unit_id}.${p.table}`].some((id) => hit.id === id || hit.id.startsWith(`${id}.`))));
    },
    search: async (req, body) => catalogueSearch(await products(req), searchInput(body)),
    ask: async (req, body) => catalogueAsk(await products(req), body),
  };
}
export function knowledgeProvider(kind, local, kge) {
  return {
    enabled: true,
    async run(req, body) {
      const input = kind === "search" ? searchInput(body) : body;
      if (kge && !req.portalIdentity.fallback && !(kind === "search" && !input.query)) {
        try {
          const response = await kge.request(req, `/${kind}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(input) });
          if (!response.ok) {
            const value = await response.json().catch(() => ({}));
            throw Object.assign(new Error(typeof value.detail === "string" ? value.detail : `KGE returned ${response.status}`), { status: response.status, retryAfter: response.headers.get("retry-after") });
          }
          const value = await response.json();
          if (kind === "search" && (!Array.isArray(value.hits) || value.hits.some((h) => !h || !kinds.has(h.kind) || typeof h.unit !== "string" || typeof h.id !== "string" || typeof h.title !== "string" || !Number.isFinite(h.score) || h.score < 0 || h.score > 1 || !h.why || !["term", "synonym", "passage", "name", "description"].includes(h.why.kind) || typeof h.why.text !== "string"))) throw new Error("Invalid KGE search response");
          if (kind === "ask") {
            if (typeof value.answer_text !== "string" || (value.figures != null && !Array.isArray(value.figures)) || (value.citations != null && !Array.isArray(value.citations))) throw new Error("Invalid KGE Ask response");
            value.figures ??= [];
            value.citations ??= [];
          }
          if (kind === "search") {
            value.hits = value.hits.filter((h) => req.portalIdentity.units.includes(h.unit));
            if (local.visibleHits) value.hits = await local.visibleHits(req, value.hits);
          }
          return { ...value, provider: "kge", fallback: false, notice: "" };
        } catch (error) {
          if (!unreachable(error)) throw error;
          return { ...await local[kind](req, input), provider: "portal", fallback: true, notice: fallbackNotice };
        }
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
