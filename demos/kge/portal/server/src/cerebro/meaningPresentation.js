// Display projection only: the authoritative Ask reply is retained separately in server state.
const normal = (value) => String(value ?? '').toLowerCase().match(/[\p{L}\p{N}]+/gu)?.join(' ') ?? '';
const contains = (value, phrase) => (` ${normal(value)} `).includes(` ${normal(phrase)} `);
const withoutSpans = (text, spans) => [...(spans ?? [])].reverse()
  .reduce((out, span) => out.slice(0, span.start) + ' ' + out.slice(span.end), text);

/** Remove only whole, structured unselected-definition lines; never change figure, SQL, history or source reply. */
export function presentMeaningReply(reply, card, selected) {
  if (!reply || typeof reply !== 'object') return reply;
  if (card.explicitScopes) return reply;
  const selectedName = normal(selected.term);
  const hasFigure = ['figure', 'sum_with_verdict', 'sum_without_verdict', 'combined'].includes(reply.behaviour)
    || typeof reply.figure === 'number' || (Array.isArray(reply.figures) && reply.figures.length > 0)
    || (Array.isArray(reply.result?.rows) && reply.result.rows.length > 0)
    || (Array.isArray(reply.display?.table?.rows) && reply.display.table.rows.length > 0)
    || (reply.rows && typeof reply.rows === 'object' && Object.keys(reply.rows).length > 0);
  if (hasFigure && normal(reply.context?.term) !== selectedName) return undefined;
  const definitionsOnly = ['glossary_definition', 'cite_certified', 'cite'].includes(reply.behaviour);
  const namedDefinition = definitionsOnly && (normal(reply.term) === selectedName
    || (!reply.term && Array.isArray(reply.certified_definitions) && reply.certified_definitions.length > 0
      && reply.certified_definitions.every((definition) => normal(definition?.term) === selectedName)));
  if (definitionsOnly && !namedDefinition) return undefined;
  if (!Array.isArray(reply.certified_definitions) || reply.certified_definitions.length === 0) {
    // Without structured definitions we cannot safely remove prose. Refuse an identifiable other-term
    // definition line; a plain figure with verified context remains unchanged.
    const titles = card.options.filter((option) => option.id !== selected.id).map((option) => option.term.trim().toLowerCase());
    const text = [reply.answer_text, reply.display?.headline?.text].filter((value) => typeof value === 'string').join('\n');
    if (text.split('\n').some((line) => titles.some((title) => line.trim().toLowerCase().startsWith(`${title}:`)))) return undefined;
    return reply;
  }
  if (normal(reply.context?.term) !== selectedName && !namedDefinition) return undefined;
  const remainder = withoutSpans(card.original, card.spans);
  if (card.options.some((option) => option.id !== selected.id && contains(remainder, option.term))) return reply;
  const otherTerms = new Set(card.options.filter((option) => option.id !== selected.id).map((option) => normal(option.term)));
  const removed = reply.certified_definitions.filter((definition) => otherTerms.has(normal(definition?.term))
    && typeof definition?.definition === 'string' && definition.definition.trim());
  const unmatched = card.options.filter((option) => option.id !== selected.id
    && !removed.some((definition) => normal(definition.term) === normal(option.term)));
  const prose = [reply.answer_text, reply.display?.headline?.text].filter((value) => typeof value === 'string').join('\n');
  if (prose.split('\n').some((line) => unmatched.some((option) => line.trim().toLowerCase().startsWith(`${option.term.trim().toLowerCase()}:`)))) return undefined;
  if (!removed.length) return reply;
  function project(text) {
    if (typeof text !== 'string') return text;
    const lines = text.split('\n');
    for (const definition of removed) {
      const exact = `${definition.term}: ${definition.definition}`;
      const matches = lines.filter((line) => {
        const trimmed = line.trim();
        if (!trimmed.startsWith(exact)) return false;
        return /^(?:\s*\[\d+\])*(?:\s*)$/.test(trimmed.slice(exact.length));
      });
      // If the definition appears in any nonmatching prose, it cannot be safely removed.
      if (lines.some((line) => (line.includes(definition.definition)
        || line.trim().toLowerCase().startsWith(`${definition.term.trim().toLowerCase()}:`)) && !matches.includes(line))) return undefined;
      for (const line of matches) lines.splice(lines.indexOf(line), 1);
    }
    return lines.join('\n');
  }
  const answer = project(reply.answer_text);
  const headline = project(reply.display?.headline?.text);
  if (answer === undefined || (reply.display?.headline?.text !== undefined && headline === undefined)) return undefined;
  return { ...reply, answer_text: answer,
    certified_definitions: reply.certified_definitions.filter((definition) => !removed.includes(definition)),
    ...(reply.display?.headline ? { display: { ...reply.display, headline: { ...reply.display.headline, text: headline } } } : {}) };
}
