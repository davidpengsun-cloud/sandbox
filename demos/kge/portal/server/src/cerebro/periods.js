// #697, #700: the portal's period words. #714: the intent check alone reads them, for one thing, whether a question
// names a period range; the meaning check reads KGE. A period word names a quarter, a half, a fiscal year, a year to date, a year or a month.
const MONTH = "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?";
const PERIOD_WORD = `(?:q[1-4]|h[12]|fy\\d{0,4}|ytd|qtd|mtd|(?:19|20)\\d{2}|${MONTH})`;
/** The kinds of period word: a range joins two of one kind ("Q1 through Q3", "Jan-Mar", "2024-2025", "FY24–FY25"); a
 *  period hyphenated to its year ("Q1-2025", "Mar-2025", "2025-Q1", "FY-2025") is one period, no range. */
const KINDS = [/^q[1-4]$/i, /^h[12]$/i, /^fy\d{0,4}$/i, /^(?:ytd|qtd|mtd)$/i, /^(?:19|20)\d{2}$/, new RegExp(`^(?:${MONTH})$`, "i")];
const kindOf = (word) => KINDS.findIndex((kind) => kind.test(word));
const RANGE = new RegExp(`\\b(${PERIOD_WORD})(?:\\s+(?:19|20)\\d{2})?\\s*(?:\\s(?:through|thru|to|until|till)\\s|[-–])\\s*(${PERIOD_WORD})\\b`, "gi");
/** Whether the text names a period range: two period words of one kind joined by "through", "thru", "to", "until",
 *  "till", a hyphen-minus or an en dash. A lower-case "may" begins none ("who may to 2025 ..."). */
export const namesPeriodRange = (text) => [...String(text ?? "").matchAll(RANGE)].some((m) => m[1] !== "may" && kindOf(m[1]) === kindOf(m[2]));
