// Explore state is shareable and survives refresh and Back.
export const FILTER_PARENTS = ["COMMUNITY", "DOMAIN", "PRODUCTSOURCE", "PRODUCTTYPE"];

export function parseSearchParams(params) {
  const q = (params.get("q") || "").trim();
  const community = params.get("community") || "";
  const domain = params.get("domain") || "";
  const sort = params.get("sort") || "";
  const tier = ["GOLD", "SILVER", "BRONZE"].includes((params.get("tier") || "").toUpperCase()) ? params.get("tier").toUpperCase() : "";
  const fresh = params.get("fresh") === "1";
  const filters = params.getAll("f").map((raw) => {
    const i = raw.indexOf(":");
    const parent = raw.slice(0, i);
    const value = raw.slice(i + 1);
    return i > 0 && FILTER_PARENTS.includes(parent) && value ? { parent, value, label: value } : null;
  }).filter(Boolean);
  return { q, community, domain, sort, tier, fresh, filters,
    baseKey: community ? `c:${domain}:${community}` : `q:${q}` };
}

export function buildSearchUrl({ q, community, domain, sort, tier, fresh, filters } = {}) {
  const params = new URLSearchParams();
  if (community) {
    params.set("community", community);
    if (domain) params.set("domain", domain);
  } else if (q?.trim()) params.set("q", q.trim());
  if (sort) params.set("sort", sort);
  (filters || []).forEach(({ parent, value }) => params.append("f", `${parent}:${value}`));
  if (tier) params.set("tier", tier);
  if (fresh) params.set("fresh", "1");
  return `/search${params.size ? `?${params}` : ""}`;
}
