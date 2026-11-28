// Model usage never carries provider payload, identity or correlation tokens to a conversation or the page.
const COUNTS = ['calls', 'input_tokens', 'output_tokens', 'thinking_tokens'];
export const unknownUsage = () => ({ calls: null, input_tokens: null, output_tokens: null, thinking_tokens: null, cost_usd: null, complete: false });
export function safeUsage(value) {
  if (!value || typeof value !== 'object' || typeof value.complete !== 'boolean') return unknownUsage();
  if (!value.complete) return unknownUsage();
  const out = { complete: value.complete };
  for (const field of COUNTS) {
    const number = value[field];
    if (number !== null && (!Number.isSafeInteger(number) || number < 0)) return unknownUsage();
    out[field] = number;
  }
  if (value.cost_usd !== null && (typeof value.cost_usd !== 'number' || !Number.isFinite(value.cost_usd) || value.cost_usd < 0)) return unknownUsage();
  out.cost_usd = value.cost_usd;
  return out;
}

/** Never fail an authoritative answer because accounting is unavailable. */
export async function questionUsage(kge, req, { finish = false, purpose = 'agent' } = {}) {
  if (!kge?.modelUsage || !req?.modelQuestionToken) return undefined;
  try { return safeUsage(await kge.modelUsage(req, { finish, purpose, signal: AbortSignal.timeout(2000) })); }
  catch { return unknownUsage(); }
}
