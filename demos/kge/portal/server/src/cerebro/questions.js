import { framedQuestion } from "./intent.js";

// #707 requirement 12 (#713): the multiple-question card. With two or more open questions before `ask`, Cerebro shows
// one card, the one HITL card extended with groups: one labelled group per open question, single choice, one line per
// option, nothing preselected (the suggested option carries the one-question card's marker, " (default)", and comes
// first). A pick selects; Send takes every group's pick at once. The Period and grouping groups apply to figures, so a
// Show pick of definitions hides them; an option that names an earlier group's options (`when`) shows only once one of
// them is picked. Which groups open is the caller's (707.3, from KGE's open items); this module holds the card's data,
// the pick check and the questions built from the picks. Nothing here calls a tool or the model. As for the
// one-question card since #625, the picks are checked against the card the server saved in the conversation's
// checkpoint, never against anything the page sends back.
//
// A group: { id, label, kind, options, spans?, cut?, cutFor? }. kind is "meaning", "show", "period" or "grouping". spans, the
// places in the person's question the group's answer replaces (a meaning's mention, "segment" in "by segment"), are
// required for a meaning; a period or grouping group without them adds its pick's words at the end ("for …", "by …"),
// so the caller must give spans wherever the question already names the item ("by segment"), or the item would be named
// twice; a card holds at most one grouping group without spans. cut, the places a pick of the group takes out of the
// question (a qualifier written apart from its term, "<qualifier> Q1 2025 <TERM>", once the pick names the meaning),
// needs spans; with cutFor, the ids of the group's options that take it out, any other pick leaves those places as
// the person wrote them. A grouping group lists every dimension KGE reports, with no cap (#707 decision 20); any other group at
// most OPTIONS_MAX. An option: { id, label, words?, suggested?, when? }: words are
// what its pick puts in the question (by default its whole label, without the marker, which only the suggested option
// carries; explicit words are the caller's, used as given; at most WORDS_MAX characters). A label longer than the
// one-question card's limit (80, with room for the marker on the suggested option) is cut at a word's end with "…", as
// meaning.js cuts one, so a long label never makes the card vanish. when is { group, options }, an earlier group's id
// and the ids of its options it is valid for (never on a Show option: the Show pick decides what else is shown).
// The card holds only what its data says: every qualifier the question gains is a pick's words (decision 4).

export const QUESTIONS = "questions";
export const QUESTIONS_HEADING = "Before I ask, choose one option in each group.";
export const KINDS = new Set(["meaning", "show", "period", "grouping"]);
/** The groups that apply to figures alone: hidden, and needing no pick, when the Show pick is definitions. */
export const FIGURES_ONLY = new Set(["period", "grouping"]);
export const SHOW_OPTIONS = ["definitions", "figures", "both"];
/** The suggested option's marker, as the one-question card's (meaning.js). */
export const SUGGESTED = " (default)";
export const GROUPS_MAX = 6;
export const OPTIONS_MAX = 6;
export const GROUP_LABEL_MAX = 40;
export const OPTION_LABEL_MAX = 80;
/** The longest words a pick may put in the question: its explicit words, or its whole label without the marker. */
export const WORDS_MAX = 200;
const ID = /^[\w.:-]{1,64}$/;

const textOf = (value, max) => (typeof value === "string" && value.trim() && value.trim().length <= max ? value.trim().replace(/\s+/g, " ") : undefined);
/** A label without its trailing suggested markers, one or more: a label that already carries one (meaning.js makes such
 *  labels) is marked once, and no marker enters the question through a default `words`. */
const unmarked = (value) => (typeof value === "string" ? value.replace(/(?:\s*\(default\))+\s*$/i, "") : value);
/** A label as the card shows it, at most max characters: a longer one is cut at a word's end and ends with "…", as
 *  meaning.js cuts a meaning's description to fit the one-question card (CHOICE_OPTION_MAX, 80), never refused. A label
 *  with no space is cut between characters, never through one (an emoji is two UTF-16 units). */
export function fitted(text, max) {
  if (text.length <= max) return text;
  const cut = text.slice(0, max - 1);
  const at = cut.lastIndexOf(" ");
  if (at > 0) return `${cut.slice(0, at).trimEnd()}…`;
  let whole = "";
  for (const character of text) {
    if (whole.length + character.length > max - 1) break;
    whole += character;
  }
  return `${whole.trimEnd()}…`;
}
const idOf = (value) => (typeof value === "string" && ID.test(value) ? value : undefined);

function spansOf(given, original) {
  if (given === undefined) return [];
  if (!Array.isArray(given) || !given.length) return undefined;
  const spans = given.map((s) => (s && Number.isInteger(s.start) && Number.isInteger(s.end) && s.start >= 0 && s.end > s.start && s.end <= original.length
    ? { start: s.start, end: s.end } : undefined));
  return spans.every(Boolean) ? spans.sort((a, b) => a.start - b.start) : undefined;
}

function optionOf(given, earlier, kind) {
  if (!given || typeof given !== "object") return undefined;
  const id = idOf(given.id);
  const base = textOf(unmarked(given.label), Infinity);
  const words = given.words === undefined ? base && textOf(base, WORDS_MAX) : textOf(given.words, WORDS_MAX);
  if (!id || !base || !words) return undefined;
  const label = given.suggested === true ? `${fitted(base, OPTION_LABEL_MAX - SUGGESTED.length)}${SUGGESTED}` : fitted(base, OPTION_LABEL_MAX);
  const option = { id, label, words };
  if (given.suggested === true) option.suggested = true;
  if (given.when !== undefined) {
    const on = earlier.find((group) => group.id === given.when?.group);
    const ids = Array.isArray(given.when?.options) ? given.when.options : [];
    if (!on || !ids.length || !ids.every((each) => on.options.some((o) => o.id === each))) return undefined;
    // An option never depends on the Show group (its pick decides what else is shown), and an option that always shows
    // never depends on a group a Show pick of definitions hides.
    if (on.kind === "show" || (FIGURES_ONLY.has(on.kind) && !FIGURES_ONLY.has(kind))) return undefined;
    option.when = { group: on.id, options: [...new Set(ids)] };
  }
  return option;
}

function groupOf(given, earlier, original) {
  if (!given || typeof given !== "object" || !KINDS.has(given.kind)) return undefined;
  const id = idOf(given.id);
  const label = textOf(given.label, GROUP_LABEL_MAX);
  const spans = spansOf(given.spans, original);
  const cut = spansOf(given.cut, original);
  const cutFor = given.cutFor === undefined ? undefined : Array.isArray(given.cutFor) && given.cutFor.every((each) => idOf(each)) ? [...new Set(given.cutFor)] : null;
  const listed = Array.isArray(given.options) ? given.options : [];
  if (!id || !label || !spans || !cut || !listed.length || (given.kind !== "grouping" && listed.length > OPTIONS_MAX)) return undefined;
  if (given.kind === "meaning" && !spans.length) return undefined;
  if (cut.length && !spans.length) return undefined;
  if (cutFor === null || (cutFor && (!cut.length || !cutFor.every((each) => listed.some((o) => o?.id === each))))) return undefined;
  const options = listed.map((o) => optionOf(o, earlier, given.kind));
  if (!options.every(Boolean) || new Set(options.map((o) => o.id)).size !== options.length) return undefined;
  if (given.kind === "show" && !options.every((o) => SHOW_OPTIONS.includes(o.id) && !o.when)) return undefined;
  const suggested = options.filter((o) => o.suggested);
  if (suggested.length > 1) return undefined;
  const ordered = [...suggested, ...options.filter((o) => !o.suggested)];
  return { id, label, kind: given.kind, options: ordered, ...(spans.length ? { spans } : {}), ...(cut.length ? { cut } : {}), ...(cutFor ? { cutFor } : {}) };
}

/** One group held to the card's shape, for the one-question card of a single open group (agent.js): the group, or
 *  undefined where it does not hold. */
export const oneGroup = (given, original) => (typeof original === "string" && original.trim() ? groupOf(given, [], original) : undefined);

/**
 * The multiple-question card for the person's question original, from its open groups: { question, groups }, or
 * undefined where it is no such card: fewer than two groups (one open question is the one-question card, none goes
 * straight to `ask`), or any group or option that does not hold (a group with no options, a duplicate id, a second Show
 * or Period group, a second grouping group with no places, a meaning with no place in the question, places that
 * overlap, a `when` naming no earlier option).
 */
export function questionsCard(groups, original) {
  if (!Array.isArray(groups) || groups.length < 2 || groups.length > GROUPS_MAX || typeof original !== "string" || !original.trim()) return undefined;
  const out = [];
  for (const given of groups) {
    const group = groupOf(given, out, original);
    if (!group || out.some((g) => g.id === group.id)) return undefined;
    if ((group.kind === "show" || group.kind === "period") && out.some((g) => g.kind === group.kind)) return undefined;
    if (group.kind === "grouping" && !group.spans && out.some((g) => g.kind === "grouping" && !g.spans)) return undefined;
    out.push(group);
  }
  const spans = out.flatMap((g) => [...(g.spans ?? []), ...(g.cut ?? [])]).sort((a, b) => a.start - b.start);
  if (spans.some((span, at) => at > 0 && span.start < spans[at - 1].end)) return undefined;
  return { question: QUESTIONS_HEADING, groups: out };
}

/**
 * The groups the card shows for the picks so far, each with the options it offers then, and the picks that still
 * count: a group that applies to figures is hidden when the Show pick is definitions; an option with `when` is offered
 * only while its earlier group's pick is one it names; a group left with no option is hidden; a pick of a hidden group
 * or of an option no longer offered does not count. The Show pick is read first, wherever its group sits; the other
 * groups are read in order, so a later group sees only the earlier picks that count. Picks are held with no prototype,
 * so any group id, "__proto__" included, is a key like another.
 */
const ownPick = (picks, id) => (picks && typeof picks === "object" && Object.hasOwn(picks, id) ? picks[id] : undefined);
export function visibleGroups(card, picks = {}) {
  const counted = Object.create(null);
  const shown = [];
  const show = card.groups.find((g) => g.kind === "show");
  const output = show && show.options.some((o) => o.id === ownPick(picks, show.id)) ? ownPick(picks, show.id) : undefined;
  for (const group of card.groups) {
    if (FIGURES_ONLY.has(group.kind) && output === "definitions") continue;
    const options = group.options.filter((o) => !o.when || o.when.options.includes(counted[o.when.group]));
    if (!options.length) continue;
    shown.push({ ...group, options });
    const pick = ownPick(picks, group.id);
    if (options.some((o) => o.id === pick)) counted[group.id] = pick;
  }
  return { groups: shown, picks: { ...counted } };
}

/**
 * The person's picks, accepted: [{ group, label, id, option, words, kind, spans? }] in the card's order, or undefined
 * where they are not one offered pick per group: a group shown with no pick, a pick that is not one of its group's
 * options as offered then, or a pick for a group not shown or not on the card.
 */
export function acceptPicks(card, picks) {
  if (!picks || typeof picks !== "object" || Array.isArray(picks)) return undefined;
  const { groups, picks: counted } = visibleGroups(card, picks);
  if (groups.some((g) => !Object.hasOwn(counted, g.id))) return undefined;
  if (Object.keys(picks).some((key) => !Object.hasOwn(counted, key))) return undefined;
  return groups.map((group) => {
    const option = group.options.find((o) => o.id === counted[group.id]);
    return { group: group.id, label: group.label, id: option.id, option: option.label, words: option.words, kind: group.kind,
      ...(group.spans ? { spans: group.spans } : {}), ...(group.cut && (!group.cutFor || group.cutFor.includes(option.id)) ? { cut: group.cut } : {}) };
  });
}

/** The picks as the saved conversation keeps them, the choose call's result: each group's label and its pick. */
export const pickedOf = (accepted) => ({ picks: accepted.map(({ group, label, id, option }) => ({ group, label, id, option })) });

/** The person's question with each pick's words in its places: a pick with places in the question replaces them and
 *  takes its cuts out (the gap closed); a period's words without places follow "for", a grouping's "by", before the
 *  question's own ending ("?", ".", "!" or "…"). */
export function completedQuestion(original, accepted) {
  const places = accepted.flatMap((a) => [...(a.spans ?? []).map((span) => ({ ...span, words: a.words })), ...(a.cut ?? []).map((span) => ({ ...span, words: "" }))]);
  const replaced = places.sort((a, b) => b.start - a.start).reduce((text, span) => {
    const after = text.slice(span.end);
    return text.slice(0, span.start) + span.words + (span.words ? after : after.replace(/^\s+/, ""));
  }, original);
  const added = [
    ...accepted.filter((a) => a.kind === "period" && !a.spans).map((a) => ` for ${a.words}`),
    ...accepted.filter((a) => a.kind === "grouping" && !a.spans).map((a) => ` by ${a.words}`),
  ].join("");
  const ending = original.match(/[?.!…]+(?=\s*$)/)?.[0] ?? "";
  return added ? `${replaced.replace(/[\s?.!…]+$/, "")}${added}${ending}` : replaced;
}

/** The question for one output (kind: "definitions", "figures", or undefined with no Show group), from the person's
 *  own words and the picks that apply to it, framed for that output in shape alone (intent.js framedQuestion). A
 *  definitions question leaves out the figures-only picks. */
function questionFor(original, accepted, kind) {
  const used = accepted.filter((a) => a.kind !== "show" && !(kind === "definitions" && FIGURES_ONLY.has(a.kind)));
  const asked = completedQuestion(original, used);
  return kind ? framedQuestion(asked, kind) : asked;
}

/**
 * The questions for `ask`, one per requested output, built from all the picks together: with a Show group, its pick's
 * output ("both" gives the definitions question and the figures question, each complete); without one, the question
 * as the picks complete it.
 */
export function questionsFromPicks(original, accepted) {
  const show = accepted.find((a) => a.kind === "show");
  const kinds = !show ? [undefined] : show.id === "both" ? ["definitions", "figures"] : [show.id];
  return kinds.map((kind) => questionFor(original, accepted, kind));
}
