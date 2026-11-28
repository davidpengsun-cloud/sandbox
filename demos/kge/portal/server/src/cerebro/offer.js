// #624 requirement 7: the next step offered after a difference between two terms (#617): "Compare the figures?", with
// choices built from the months both terms' data covers (each term's period_range), never a figure and never a period
// outside both ranges. Pure: the agent posts the offer, re-derives it from the verified answer when the person picks, and
// asks Ask the question a pick names.

export const OFFER_QUESTION = "Compare the figures?";
export const NO_THANKS = "No thanks";
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** A date's month as a count of months (year * 12 + month - 1), from an ISO date ("YYYY-MM" or "YYYY-MM-DD"); else undefined. */
function monthIndex(value) {
  const found = typeof value === "string" ? /^(\d{4})-(\d{2})(?:-\d{2})?/.exec(value) : null;
  const month = found ? Number(found[2]) : 0;
  return month >= 1 && month <= 12 ? Number(found[1]) * 12 + month - 1 : undefined;
}
const dayOf = (value) => (typeof value === "string" ? Number(/^\d{4}-\d{2}-(\d{2})/.exec(value)?.[1]) || undefined : undefined);
const daysIn = (index) => new Date(Date.UTC(yearOf(index), (index % 12) + 1, 0)).getUTCDate();

/**
 * The whole months a period range covers, as the owner's "the months both ranges cover" reads: a range of released
 * months (released_months) is whole months; a daily range is rounded in, so its first month counts only from its first
 * day and its latest only through its last day. { first, latest } as month counts; first > latest where no month is whole.
 */
function wholeMonths(range) {
  const first = monthIndex(range?.first);
  const latest = monthIndex(range?.latest);
  if (first === undefined || latest === undefined || range.released_months === true) return { first, latest };
  const from = dayOf(range.first);
  const to = dayOf(range.latest);
  return { first: from !== undefined && from > 1 ? first + 1 : first, latest: to !== undefined && to < daysIn(latest) ? latest - 1 : latest };
}
const yearOf = (index) => Math.floor(index / 12);
const monthWords = (index) => `${MONTH_NAMES[index % 12]} ${yearOf(index)}`;
const monthId = (index) => `${yearOf(index)}-${String((index % 12) + 1).padStart(2, "0")}`;

/** The two terms a difference answer compares, each with the whole months its data covers; undefined unless there are two,
 *  each covering at least one whole month. */
export function termsOf(reply) {
  const terms = reply && typeof reply === "object" ? reply.difference?.terms : undefined;
  if (!Array.isArray(terms) || terms.length !== 2) return undefined;
  const out = terms.map((t) => ({ term: typeof t?.term === "string" ? t.term.trim() : "", ...wholeMonths(t?.period_range) }));
  return out.every((t) => t.term && t.first !== undefined && t.latest !== undefined && t.first <= t.latest) ? out : undefined;
}

/**
 * The offer after a difference answer: its question, its choices (the latest month both terms cover, the last calendar
 * quarter whose three months both cover, and No thanks) and every month both cover ("YYYY-MM", for Pick a month). None
 * where the reply compares no two terms with period ranges, or the ranges do not overlap.
 */
export function offerOf(reply) {
  const terms = termsOf(reply);
  if (!terms) return undefined;
  const first = Math.max(...terms.map((t) => t.first));
  const latest = Math.min(...terms.map((t) => t.latest));
  if (first > latest) return undefined;
  const options = [{ id: "latest_month", label: monthWords(latest) }];
  const quarterEnd = latest - (((latest % 12) + 1) % 3);
  if (quarterEnd - 2 >= first) options.push({ id: "last_quarter", label: `Q${Math.floor((quarterEnd % 12) / 3) + 1} ${yearOf(quarterEnd)}` });
  options.push({ id: "no", label: NO_THANKS });
  const months = [];
  for (let month = first; month <= latest; month += 1) months.push(monthId(month));
  return { question: OFFER_QUESTION, options, months };
}

/**
 * The person's pick of an offer: { pick: option id } or { month: "YYYY-MM" }. Gives { id, label, no } for No thanks,
 * { id, label, words, byMonth } for a period, or undefined for a pick the offer does not hold.
 */
export function offerPick(offer, value) {
  if (!offer || !value || typeof value !== "object") return undefined;
  if (typeof value.month === "string") {
    if (!offer.months.includes(value.month)) return undefined;
    const label = monthWords(monthIndex(value.month));
    return { id: value.month, label, words: label, byMonth: false };
  }
  const option = offer.options.find((o) => o.id === value.pick);
  if (!option) return undefined;
  if (option.id === "no") return { id: option.id, label: option.label, no: true };
  return { id: option.id, label: option.label, words: option.label, byMonth: option.id === "last_quarter" };
}

const period = (pick) => `for ${pick.words}${pick.byMonth ? " by month" : ""}`;

/** The one question that asks for both terms side by side for the picked period, phrased as sage S08 is. */
export const comparisonQuestion = (terms, pick) => `Compare ${terms[0].term} vs. ${terms[1].term} ${period(pick)}, with the dollar and percent difference`;

/** The fallback's question for one term for the picked period. */
export const termQuestion = (term, pick) => `${term.term} ${period(pick)}`;
