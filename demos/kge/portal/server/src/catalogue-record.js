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
  if ([...governedBadges].some((owned) => withinOneEdit(badgeForm(normalized), badgeForm(owned)))) throw Object.assign(new Error("Only portal-owned badges are accepted; badges resembling the platform's certification or governance badges are refused."), { status: 400 });
  return normalized;
}
const isGovernedBadge = (badge) => governedBadges.has(badge.toUpperCase());
// A card has one tier: of several, the first in the list's order stands.
const tierNames = new Set(["GOLD", "SILVER", "BRONZE"]);
const oneTier = (badges) => { const first = badges.find((badge) => tierNames.has(badge)); return badges.filter((badge) => !tierNames.has(badge) || badge === first); };
export const portalBadges = (badges) => badges.flatMap((badge) => {
  try { return [normaliseBadge(badge)]; } catch { return []; }
});
const cardBadges = (badges) => oneTier(portalBadges(Array.isArray(badges) ? badges : []));
export const businessFields = ["name", "description", "product_owner", "business_owner", "product_owner_email", "business_owner_email", "badges", "domain", "community", "application", "product_type"];
export const ownerEmailFields = ["product_owner_email", "business_owner_email"];
const emailShape = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
export const governedFields = ["asset_type", "usage_purpose", "dataset_type", "APM_ID", "schema", "project", "table", "records", "storage", "data_format", "path", "last_refresh", "refresh_cadence", "last_dq_run", "quality_score", "lineage"];
const classificationLabels = new Map([["public", "Public"], ["internal", "Internal"], ["confidential", "Confidential"], ["highly confidential", "Highly Confidential"], ["restricted", "Highly Confidential"]]);
export const classificationLabel = (value) => typeof value === "string" ? classificationLabels.get(value.trim().toLowerCase().replace(/[\s_-]+/g, " ")) || "" : "";
// A field's quality is one rule or a list of rules; readers take the list.
export const qualityRules = (quality) => Array.isArray(quality) ? quality : quality ? [quality] : [];
// One rule stays an object and several a list, so a single-rule catalogue keeps its shape.
export const qualityValue = (rules) => rules.length === 1 ? rules[0] : rules;
export function overlay(product, governed, visibleIds) {
  if (!governed) return product;
  const fields = governed.fields.map((field) => {
    const own = product.fields.find((f) => f.name === field.name);
    const ownRules = qualityRules(own?.quality);
    const quality = qualityValue(qualityRules(field.quality).map((rule, index) => ({ ...rule, fail_percent: rule.fail_percent ?? null, description: rule.description ?? ownRules[index]?.description ?? "" })));
    return { ...field, pii: field.pii ?? own?.pii ?? false, quality, classification: field.classification ?? own?.classification ?? "", business_name: own?.business_name ?? field.business_name, description: own?.description ?? field.description };
  });
  const lineage = { ...governed.lineage,
    upstream: governed.lineage.upstream.filter((id) => !visibleIds || visibleIds.has(id)),
    downstream: governed.lineage.downstream.filter((id) => !visibleIds || visibleIds.has(id)),
  };
  const badges = [...cardBadges(product.badges), ...governed.badges.filter((b) => isGovernedBadge(b))];
  return { ...product, badges, ...Object.fromEntries(governedFields.map((key) => [key, governed[key] !== undefined ? governed[key] : product[key]])), fields, lineage };
}
// The descriptive values a KGE catalogue sync can change on an existing card.
// The owners keep their own rule, applied by each adapter.
export const followedFields = ["name", "description", "domain", "community", "application", "product_type"];
export const followedFieldColumns = ["business_name", "description"];
const badgeSet = (badges) => [...new Set(cardBadges(badges))].sort();
const sameSet = (left, right) => left.length === right.length && left.every((value, index) => value === right[index]);
// The tier is one value, never a set: a card shows KGE's tier or the portal's.
const cardValues = (card) => ({
  ...Object.fromEntries(followedFields.map((key) => [key, card[key]])),
  tier: cardBadges(card.badges).find((badge) => tierNames.has(badge)) || "",
  badges: badgeSet(card.badges).filter((badge) => !tierNames.has(badge)),
  fields: Object.fromEntries((card.fields || []).map((field) => [field.name, { business_name: field.business_name, description: field.description }])),
});
// Decides what a catalogue sync writes to the cards the portal already holds,
// and the memory it keeps for the next sync, in the payload's `cards`: per card,
// KGE's last values and the values the card held after that sync. `previous`
// is the payload the last sync stored; `held` maps a product id to its stored
// card (portal-owned columns, `badges` parsed and `storedBadges` as stored,
// field rows); `products` is the fresh catalogue.
// With `follow` (a KGE refresh) a value takes KGE's new one when KGE changed it
// since the last sync and the card still holds what that sync left; an edit
// made since then stays. Portal-owned badges are one list: unless a portal
// edit changed any of them since the last sync, the tier (GOLD, SILVER,
// BRONZE, one value) takes KGE's and KGE's additions and removals of the
// others apply to the card's. A field or card absent from a release keeps KGE's last
// values for when it returns. Without `follow` (a seed or the portal's own
// file) nothing is written and the memory is carried forward; with no memory
// yet it stays absent, so the first KGE refresh after a seed treats the seed
// as KGE's last values: a seeded value still on the card follows KGE.
// With no memory yet (a payload stored before it existed), the last payload's
// values stand in for both, so a card follows only where it still holds KGE's
// last value, as the owners' rule does, and older portal edits stay.
// Each write names the value it expects, so an adapter applies it only while
// the card still holds that value.
export function followCatalogue(previous, held, products, follow) {
  const remembered = previous?.cards;
  if (!follow) return { cards: remembered, products: [], fields: [] };
  const lastGiven = new Map((previous?.products || []).map((product) => [product.id, product]));
  const cards = { ...remembered };
  if (!remembered) for (const [id, product] of lastGiven) if (held.has(id)) cards[id] = { kge: cardValues(product), card: cardValues(product) };
  const productWrites = [], fieldWrites = [];
  for (const product of products) {
    const card = held.get(product.id);
    const given = cardValues(product);
    if (!card) { cards[product.id] = { kge: given, card: given }; continue; }
    const now = cardValues(card);
    const entry = cards[product.id];
    if (!entry) { cards[product.id] = { kge: given, card: now }; continue; }
    const after = structuredClone(now);
    for (const key of followedFields) {
      if (given[key] !== entry.kge[key] && now[key] === entry.card[key] && now[key] !== given[key]) {
        productWrites.push({ id: product.id, target: key, held: card[key], value: given[key] });
        after[key] = given[key];
      }
    }
    const badgesKept = now.tier === entry.card.tier && sameSet(now.badges, entry.card.badges);
    if (badgesKept && given.tier !== entry.kge.tier) after.tier = given.tier;
    if (badgesKept && !sameSet(given.badges, entry.kge.badges)) {
      const removed = new Set(entry.kge.badges.filter((badge) => !given.badges.includes(badge)));
      after.badges = badgeSet([...now.badges.filter((badge) => !removed.has(badge)), ...given.badges.filter((badge) => !entry.kge.badges.includes(badge))]);
    }
    if (after.tier !== now.tier || !sameSet(after.badges, now.badges)) productWrites.push({ id: product.id, target: "badges", held: card.storedBadges, value: JSON.stringify([...(after.tier ? [after.tier] : []), ...after.badges]) });
    for (const [name, field] of Object.entries(given.fields)) {
      const own = now.fields[name], lastKge = entry.kge.fields[name], lastCard = entry.card.fields[name];
      if (!own) { after.fields[name] = field; continue; }
      if (!lastKge || !lastCard) continue;
      for (const column of followedFieldColumns) {
        if (field[column] !== lastKge[column] && own[column] === lastCard[column] && own[column] !== field[column]) {
          fieldWrites.push({ product_id: product.id, name, target: column, held: own[column], value: field[column] });
          after.fields[name][column] = field[column];
        }
      }
    }
    cards[product.id] = { kge: { ...given, fields: { ...entry.kge.fields, ...given.fields } }, card: after };
  }
  return { cards, products: productWrites, fields: fieldWrites };
}
// Records in the memory what the sync's writes actually left on each card:
// `actual` lists, per write, the value the card held once the writes ran
// ({ product_id, name, target, actual }; `name` is set for a field row), so a
// write an edit pre-empted leaves that edit as the card's post-sync value.
// Returns the memory and whether it changed.
export function settleCards(cards, actual) {
  let changed = false;
  for (const row of actual) {
    const card = cards[row.product_id]?.card;
    if (!card) continue;
    if (row.name) {
      if (card.fields[row.name] && card.fields[row.name][row.target] !== row.actual) { card.fields[row.name][row.target] = row.actual; changed = true; }
    } else if (row.target === "badges") {
      const held = cardValues({ badges: parseList(row.actual) });
      if (held.tier !== card.tier || !sameSet(held.badges, card.badges)) { Object.assign(card, { tier: held.tier, badges: held.badges }); changed = true; }
    } else if (card[row.target] !== row.actual) { card[row.target] = row.actual; changed = true; }
  }
  return changed;
}
// The stored cards a sync compares against, keyed by product id, from the
// adapter's product rows (portal-owned columns, badges as stored) and field rows.
export function heldCards(productRows, fieldRows) {
  const cards = new Map(productRows.map((row) => [row.id, { ...row, storedBadges: row.badges, badges: parseList(row.badges), fields: [] }]));
  for (const row of fieldRows) cards.get(row.product_id)?.fields.push({ name: row.name, business_name: row.business_name, description: row.description });
  return cards;
}
const parseList = (value) => { try { const list = JSON.parse(value); return Array.isArray(list) ? list : []; } catch { return []; } };
export function metadataPatch(body) {
  if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).some((key) => ![...businessFields, "fields"].includes(key))) throw Object.assign(new Error("Only portal-owned metadata can be edited."), { status: 400 });
  for (const [key, value] of Object.entries(body)) {
    if (key === "fields") {
      if (!Array.isArray(value) || value.some((f) => !f || typeof f.name !== "string" || Object.keys(f).some((k) => !["name", "business_name", "description"].includes(k)) || Object.values(f).some((v) => typeof v !== "string" || !v.trim() || v.length > 10000))) throw Object.assign(new Error("Invalid field metadata."), { status: 400 });
    } else if (ownerEmailFields.includes(key) ? typeof value !== "string" || value.length > 320 || (value.trim() !== "" && !emailShape.test(value.trim())) : key === "badges" ? !Array.isArray(value) || value.some((v) => typeof v !== "string" || !v.trim()) : typeof value !== "string" || !value.trim() || value.length > 10000) throw Object.assign(new Error("Invalid product metadata."), { status: 400 });
  }
  return body.badges ? { ...body, badges: body.badges.map(normaliseBadge) } : body;
}
export function metadataInput(products, client) {
  if (typeof client !== "string" || !/^[a-z][a-z0-9_-]{0,39}$/.test(client)) throw Object.assign(new Error("A client pack ID is required."), { status: 400 });
  return { format: 1, client, products: products.map((p) => ({ id: p.id, unit: p.unit_id, table: p.table, ...Object.fromEntries(businessFields.map((key) => [key, key === "badges" ? portalBadges(p.badges) : p[key]])), fields: p.fields.map(({ name, business_name, description }) => ({ name, business_name, ...(description ? { description } : {}) })) })) };
}
