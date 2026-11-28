// The meaning check (#707 requirements 7, 8, 13 and 15; #714). Whether a meaning is open, and which meanings a card
// offers, come from KGE's reading of the question as the signed-in person: get_context's bindings and open items,
// map_term's resolves_to and search's filter phrases. This module holds no word list: it compares KGE's results and the
// places KGE gives in the question, and nothing here names a client, a pack, a term or a segment. The one business word
// it matches on is the named, temporary rule SUGGESTED_UNTIL_709.

const words = (value) => String(value ?? "").match(/[\p{L}\p{N}]+/gu) ?? [];
const normal = (value) => words(value).join(" ").toLowerCase();
const holds = (value, phrase) => (` ${normal(value)} `).includes(` ${normal(phrase)} `);
const text = (value) => (typeof value === "string" ? value : "");

/**
 * #707 decisions 8 and 17: the card marks the "certified" option as its suggested meaning, as on kit 0.14.58. This is
 * the only business word the portal matches on, kept until #709 lets a pack declare a family's suggested default,
 * which KGE then reports and the card marks instead. It is never the default meaning, the one ask binds by default
 * (KGE's default_alias), and where no option, or more than one, carries the word, no option is marked.
 */
export const SUGGESTED_UNTIL_709 = /\bcertified\b/i;
export function suggestedOf(options) {
  const named = options.filter((option) => SUGGESTED_UNTIL_709.test(option.label));
  return named.length === 1 ? named[0] : undefined;
}

const given = (list) => (Array.isArray(list) ? list : []).filter((entry) => entry && typeof entry === "object");
/** An open item's options, or map_term's resolves_to, as the portal reads them: each with a label, and its id,
 *  definition, value (a month) and phrases (a grouping dimension) where KGE gives them. */
export const optionsOf = (list) => given(list).filter((option) => text(option.label).trim()).map((option) => ({
  label: option.label.replace(/\s+/g, " ").trim(),
  ...(text(option.id) ? { id: option.id } : {}),
  ...(text(option.definition).trim() ? { definition: option.definition.replace(/\s+/g, " ").trim() } : {}),
  ...(text(option.value) ? { value: option.value } : {}),
  ...(Array.isArray(option.phrases) ? { phrases: option.phrases.filter((phrase) => text(phrase).trim()) } : {}),
}));

/** KGE counts a place in characters, JavaScript in UTF-16 units: the unit index of each character index. */
function unitsOf(question) {
  if (!/[\uD800-\uDBFF]/.test(question)) return (index) => index;
  const at = [0];
  for (const character of question) at.push(at.at(-1) + character.length);
  return (index) => at[index];
}

/**
 * KGE's reading of the question (get_context with open_items), held to what the portal reads of it: { bindings, items },
 * or undefined where the reply carries none (a KGE without the capability, an error). A binding or an item keeps its
 * place in the question (span) only where the question holds its words there.
 */
export function readingOf(value, question) {
  if (!value || typeof value !== "object" || !Array.isArray(value.bindings) || !Array.isArray(value.open_items) || typeof question !== "string") return undefined;
  const unit = unitsOf(question);
  const place = (entry) => {
    if (!Number.isInteger(entry.start) || !Number.isInteger(entry.end)) return undefined;
    const start = unit(entry.start);
    const end = unit(entry.end);
    return Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end > start && end <= question.length && question.slice(start, end) === entry.text ? { start, end } : undefined;
  };
  const bindings = given(value.bindings).map((binding) => ({
    kind: text(binding.kind), text: text(binding.text), span: place(binding),
    ...(text(binding.term) ? { term: binding.term } : {}), ...(text(binding.by) ? { by: binding.by } : {}),
    ...(text(binding.value) ? { value: binding.value } : {}), ...(text(binding.time_mode) ? { time_mode: binding.time_mode } : {}),
  }));
  const items = given(value.open_items).map((item) => ({
    item: text(item.item), resolution: text(item.resolution), text: text(item.text), span: place(item),
    ...(text(item.reason).trim() ? { reason: item.reason.trim() } : {}),
    ...(text(item.latest?.label).trim() ? { latest: item.latest.label.trim() } : {}),
    options: optionsOf(item.options),
  }));
  return { bindings, items };
}

const placed = (reading) => reading.bindings.filter((binding) => binding.span).sort((a, b) => a.span.start - b.span.start);

/**
 * Whether the question's own words settle a term item: a time mode KGE read directly before the mention, with only
 * periods KGE read between them ("<mode> Q3 2024 <TERM>"), which is the mode of the meaning KGE bound. The person
 * named the meaning ask binds; nothing is asked (#707 requirement 13: the bindings settle it).
 */
function settledByMode(question, reading, item) {
  const order = placed(reading);
  const at = order.findIndex((binding) => binding.kind === "term" && binding.span.start === item.span.start && binding.span.end === item.span.end);
  if (at < 0) return false;
  let before = at - 1;
  while (before >= 0 && order[before].kind === "period") before -= 1;
  const mode = order[before];
  if (mode?.kind !== "time_mode" || (order[at].time_mode && mode.value !== order[at].time_mode)) return false;
  return order.slice(before, at).every((binding, n, run) => !question.slice(binding.span.end, (run[n + 1] ?? order[at]).span.start).trim());
}

/**
 * The mentions whose meaning is open, each { token, said, spans, cut, bound, options }: token is the name as the person
 * wrote it, said the whole phrase where a qualifier came with it, spans the places a pick's words go, options KGE's
 * meanings in KGE's order. cut is the qualifier's places and bound the term KGE bound the phrase to: a pick of that
 * term takes the qualifier out (its name says it), and any other pick leaves the person's qualifier where it stands.
 *
 * - A term item (KGE bound the name by its default alias, and it names several readable meanings) is open, unless
 *   the question's own words settle it (settledByMode).
 * - A term KGE bound explicitly by its own name is settled: the words it bound are the term's own words, written
 *   together or apart ("<qualifier> Q2 2025 <TERM>").
 * - A term KGE bound explicitly by another name is checked with KGE's tools (#707 requirements 13 and 15): where the
 *   name's last word alone resolves to several meanings (map_term), the words before it are a qualifier, and a
 *   qualifier that also names a filter value (search, kind filter) leaves the meaning open: the word names two
 *   things. It is a mention of its own, apart from a bare mention of the same name. The same name written more than
 *   once with no qualifier at all is open too.
 *
 * tools.meanings(name) and tools.filters(phrase) call KGE within the limit on tool calls; each gives undefined once
 * the limit is spent or the call fails, and tools.refused() counts the calls the spent limit turned away. A check the
 * limit cut short leaves its mention open (#707 requirement 13: when the limit is spent, what is still open goes on
 * the card, the fail-safe outcome), with the options it already has: its meanings where they are known, else the one
 * meaning KGE bound it to, which the person confirms or answers past with "Something else". A call that fails within
 * the limit is not a spent limit: where it leaves the meanings unknown, the mention stays as KGE bound it.
 */
export async function openMeanings(question, reading, tools) {
  const mentions = new Map();
  const add = (key, token, spans, options, more = {}) => {
    const held = mentions.get(key) ?? { token, spans: [], cut: [], options, ...more };
    for (const span of spans) if (!held.spans.some((each) => each.start === span.start)) held.spans.push(span);
    mentions.set(key, held);
  };
  const known = new Map();
  for (const item of reading.items) {
    if (item.item !== "term" || !item.span || item.options.length < 2) continue;
    known.set(normal(item.text), item.options);
    if (!settledByMode(question, reading, item)) add(normal(item.text), item.text, [item.span], item.options);
  }
  // A name's meanings are read once; a read the spent limit turned away is not kept as an answer: the name is
  // unchecked, here and wherever it is written again.
  const unchecked = new Set();
  const meaningsOf = async (name) => {
    const key = normal(name);
    if (!known.has(key) && !unchecked.has(key)) {
      const turnedAway = tools.refused?.() ?? 0;
      const read = await tools.meanings(name);
      if (!read && (tools.refused?.() ?? 0) > turnedAway) unchecked.add(key);
      else known.set(key, read);
    }
    return known.get(key);
  };
  const explicit = new Map();
  for (const binding of placed(reading)) {
    if (binding.kind === "term" && binding.by === "explicit" && binding.term) explicit.set(binding.term, [...(explicit.get(binding.term) ?? []), binding]);
  }
  for (const [term, bindings] of explicit) {
    const said = bindings.flatMap((binding) => words(binding.text));
    const own = new Set(words(term).map((word) => word.toLowerCase()));
    if (said.every((word) => own.has(word.toLowerCase())) && new Set(said.map((word) => word.toLowerCase())).size === own.size) continue;
    const head = said.at(-1);
    if (!head) continue;
    const qualifier = said.filter((word) => word.toLowerCase() !== head.toLowerCase()).join(" ");
    if (!qualifier && (bindings.length < 2 || reading.bindings.some((binding) => binding.kind === "time_mode" && binding.term === term))) continue;
    const meanings = await meaningsOf(head);
    // The limit was spent before this name's meanings were read: it is asked with the one meaning KGE bound it to.
    const cutShort = unchecked.has(normal(head));
    const family = cutShort ? optionsOf([{ label: term }]) : meanings;
    if (!family?.length || (!cutShort && (family.length < 2 || !family.some((option) => normal(option.label) === normal(term))))) continue;
    if (!qualifier) {
      add(normal(head), head, bindings.map((binding) => binding.span), family);
      continue;
    }
    const hits = await tools.filters(qualifier);
    if (hits && !hits.some((hit) => holds(hit, qualifier))) continue;
    // The name's own place is its last word's; what stands before it in the phrase, and any part bound apart, is
    // the qualifier's.
    const last = bindings.at(-1);
    const at = last.span.end - [...question.slice(last.span.start, last.span.end).matchAll(/[\p{L}\p{N}]+/gu)].at(-1)[0].length;
    const lead = question.slice(last.span.start, at).trimEnd();
    const cut = [...bindings.slice(0, -1).map((binding) => binding.span), ...(lead ? [{ start: last.span.start, end: last.span.start + lead.length }] : [])];
    const phrase = `${qualifier} ${head}`;
    add(normal(phrase), head, [{ start: at, end: last.span.end }], family, { said: phrase, cut, bound: term });
  }
  return [...mentions.values()].map((mention) => ({ ...mention, spans: mention.spans.sort((a, b) => a.start - b.start) }));
}

const ID = /^[\w.:-]{1,64}$/;
/** Each meaning with an id the card can carry: KGE's own, else one made from its place in the list. */
function identified(options) {
  const taken = new Set();
  return options.map((option) => {
    let id = ID.test(option.id ?? "") && !taken.has(option.id) ? option.id : undefined;
    for (let n = 1; !id; n += 1) if (!taken.has(`meaning-${n}`) && !options.some((other) => other.id === `meaning-${n}`)) id = `meaning-${n}`;
    taken.add(id);
    return { ...option, id };
  });
}
/** The suggested meaning first, then the others in KGE's order (#707 decisions 3 and 18). */
function ordered(options) {
  const all = identified(options);
  const suggested = suggestedOf(all);
  return { suggested, options: suggested ? [suggested, ...all.filter((option) => option !== suggested)] : all };
}

/** The suggested option's marker and the one-question card's line length, as on kit 0.14.58. */
const MARKER = " (default)";
const LINE_MAX = 80;
const JOIN = " — ";

/** The Meaning group of the card for one open mention (questions.js): each option's label is KGE's label and
 *  definition, unmarked (the card marks the suggested one), and its words are KGE's label alone, so a pick puts the
 *  meaning's name, and nothing else, in the question. token is the name as the person wrote it. Where a qualifier
 *  came with the name, a pick of the meaning KGE bound the phrase to takes the qualifier out (cut, cutFor); any other
 *  pick leaves the person's qualifier in the question (#707 decision 4). */
export function meaningGroup(mention, at, total) {
  const { suggested, options } = ordered(mention.options);
  return {
    id: at ? `meaning-${at + 1}` : "meaning", label: total > 1 ? `Meaning of ${mention.said ?? mention.token}`.slice(0, 40) : "Meaning", kind: "meaning", token: mention.token,
    spans: mention.spans,
    ...(mention.cut.length ? { cut: mention.cut, cutFor: options.filter((option) => normal(option.label) === normal(mention.bound)).map((option) => option.id) } : {}),
    options: options.map((option) => ({ id: option.id, label: option.definition ? `${option.label}${JOIN}${option.definition}` : option.label, words: option.label,
      ...(option === suggested ? { suggested: true } : {}) })),
  };
}

/**
 * The one-question meaning card for a single open Meaning group, as on kit 0.14.58 (#707 requirement 10): the
 * question, with the suggested default named, and one line an option: the meaning's name, the marker on the suggested
 * one, then as much of its definition as the line holds. Each line keeps its term (the words a pick puts in the
 * question). The order is the group's: the suggested option first (#707 decisions 3 and 18).
 */
export function meaningCard(group, original) {
  const token = text(group.token).trim() || original.slice(group.spans.at(-1).start, group.spans.at(-1).end);
  const suggested = group.options.find((option) => option.suggested === true);
  const lines = group.options.map((option) => {
    const term = option.words;
    const description = option.label.startsWith(`${term}${JOIN}`) ? option.label.slice(term.length + JOIN.length) : "";
    const marker = option === suggested ? MARKER : "";
    const room = LINE_MAX - marker.length;
    const name = Array.from(term);
    let label = `${name.length > room ? `${name.slice(0, room - 1).join("")}…` : term}${marker}`;
    const available = Math.max(0, LINE_MAX - label.length - JOIN.length);
    if (description && available >= 12) {
      const characters = Array.from(description);
      label = `${label}${JOIN}${characters.length > available ? `${characters.slice(0, available - 1).join("").replace(/\s+\S*$/, "")}…` : description}`;
    }
    return { id: option.id, label, term, ...(description ? { definition: description } : {}) };
  });
  return { token, question: `Which meaning of ${token} do you intend?${suggested ? ` Suggested default: ${suggested.words}.` : ""}`, options: lines };
}
