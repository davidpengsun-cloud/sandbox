export const governedBadges = new Set(["CERTIFIED", "GOVERNED", "RESTRICTED", "PII"]);
const badgeForm = (value) => value.replace(/[^A-Z0-9]/g, "").replace(/0/g, "O").replace(/[1L]/g, "I");
const withinOneEdit = (left, right) => {
  if (Math.abs(left.length - right.length) > 1) return false;
  if (left.length > right.length) return withinOneEdit(right, left);
  let i = 0, j = 0, edits = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) { i++; j++; }
    else {
      if (++edits > 1) return false;
      if (left.length === right.length) i++;
      j++;
    }
  }
  return edits + left.length - i + right.length - j <= 1;
};
export function normaliseBadge(badge) {
  const value = typeof badge === "string" ? badge.normalize("NFKC") : "";
  if (!value || /[^A-Za-z0-9 _-]/.test(value)) throw Object.assign(new Error("Only portal-owned badges using ASCII letters, digits, spaces, underscores and hyphens are accepted."), { status: 400 });
  const normalized = value.toUpperCase().replace(/ +/g, " ").trim();
  if (normalized.length < 1 || normalized.length > 32) throw Object.assign(new Error("Only portal-owned badges of 1-32 characters are accepted."), { status: 400 });
  if ([...governedBadges].some((owned) => withinOneEdit(badgeForm(normalized), badgeForm(owned)))) throw Object.assign(new Error("Only portal-owned badges are accepted; badges resembling KGE certification or governance badges are refused."), { status: 400 });
  return normalized;
}
const isGovernedBadge = (badge) => governedBadges.has(badge.toUpperCase());
export const portalBadges = (badges) => badges.flatMap((badge) => {
  try { return [normaliseBadge(badge)]; } catch { return []; }
});
export const businessFields = ["name", "description", "product_owner", "business_owner", "badges", "domain", "community", "application", "product_type"];
export const governedFields = ["table", "records", "storage", "data_format", "path", "last_refresh", "last_dq_run", "quality_score", "lineage"];
export function overlay(product, governed, visibleIds) {
  if (!governed) return product;
  const fields = governed.fields.map((field) => {
    const own = product.fields.find((f) => f.name === field.name);
    return { ...field, business_name: own?.business_name ?? field.business_name, description: own?.description ?? field.description };
  });
  const lineage = { ...governed.lineage,
    upstream: governed.lineage.upstream.filter((id) => !visibleIds || visibleIds.has(id)),
    downstream: governed.lineage.downstream.filter((id) => !visibleIds || visibleIds.has(id)),
  };
  const badges = [...portalBadges(product.badges), ...governed.badges.filter((b) => isGovernedBadge(b))];
  return { ...product, badges, ...Object.fromEntries(governedFields.map((key) => [key, governed[key]])), fields, lineage };
}
export function metadataPatch(body) {
  if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some((key) => ![...businessFields, "fields"].includes(key))) throw Object.assign(new Error("Only portal-owned metadata can be edited."), { status: 400 });
  for (const [key, value] of Object.entries(body)) {
    if (key === "fields") {
      if (!Array.isArray(value) || value.some((f) => !f || typeof f.name !== "string" || Object.keys(f).some((k) => !["name", "business_name", "description"].includes(k)) || Object.values(f).some((v) => typeof v !== "string" || !v.trim() || v.length > 10000))) throw Object.assign(new Error("Invalid field metadata."), { status: 400 });
    } else if (key === "badges" ? !Array.isArray(value) || value.some((v) => typeof v !== "string" || !v.trim()) : typeof value !== "string" || !value.trim() || value.length > 10000) throw Object.assign(new Error("Invalid product metadata."), { status: 400 });
  }
  return body.badges ? { ...body, badges: body.badges.map(normaliseBadge) } : body;
}
export function metadataInput(products, client) {
  if (typeof client !== "string" || !/^[a-z][a-z0-9_-]{0,39}$/.test(client)) throw Object.assign(new Error("A client pack ID is required."), { status: 400 });
  return { format: 1, client, products: products.map((p) => ({ id: p.id, unit: p.unit_id, table: p.table, ...Object.fromEntries(businessFields.map((key) => [key, key === "badges" ? portalBadges(p.badges) : p[key]])), fields: p.fields.map(({ name, business_name, description }) => ({ name, business_name, ...(description ? { description } : {}) })) })) };
}
