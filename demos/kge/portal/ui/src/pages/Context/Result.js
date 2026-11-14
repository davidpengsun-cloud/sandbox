import React from "react";
import { Cite } from "./Answer";
import { amount, businessName, isAmount, quarterWords, realCurrency } from "./contextState";

export const resultNumber = (value, column = {}) => value == null ? "no value" : column.format === "percent" ? `${(Number(value) * 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%` : column.format === "ratio" ? `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} : 1` : (about => `${isAmount(about) && realCurrency(column.currency) ? `${column.currency} ` : ""}${amount(value, about)}`)({ currency: column.currency, kind: column.type === "integer" ? "count" : column.kind, amount: column.amount });
export const general = a => !!a.result && Array.isArray(a.result.columns) && (a.result.rows?.some(r => r.total || r.end) || a.figure === undefined && !a.rows && !a.figures?.length && !a.parts);
export function ownSpans(a) {
  const chars = Array.from(String(a.question || "")), turn = a.history?.at(-1)?.turn_id;
  const spans = (a.clarification?.spans || []).filter(s => s.turn === turn && Number.isInteger(s.start) && Number.isInteger(s.end) && s.start >= 0 && s.start < s.end && s.end <= chars.length).sort((a, b) => b.start - a.start);
  return spans.some((s, i) => i && s.end > spans[i - 1].start) ? [] : spans;
}
export function resolvedQuestion(a, label) {
  const chars = Array.from(String(a.question || "")), spans = ownSpans(a);
  if (!spans.length) return null;
  for (const s of spans) chars.splice(s.start, s.end - s.start, ...Array.from(String(label)));
  return chars.join("");
}
const scalar = (value, column) => !value || value.kind === "null" || value.value == null ? null : column && ["decimal", "integer"].includes(value.kind) ? resultNumber(value.value, column) : value.kind === "boolean" ? value.value ? "yes" : "no" : quarterWords(value.value);
export default function Result({ result, onCite }) {
  const dims = result.columns.filter(c => c.kind === "dimension"), outs = result.columns.filter(c => c.kind !== "dimension").map((c, index) => ({ ...c, index })).sort((a, b) => (a.display_order ?? Infinity) - (b.display_order ?? Infinity));
  const ranked = result.rows?.some(row => row.end);
  if (!result.rows?.length) return <p>No observations matched the checked request.</p>;
  return [...new Set(result.rows.map(row => row.panel ?? null))].map((panel, i) => <div className="scroll" key={i}><table className="parts result" data-panel={panel}>{panel != null && <caption>{panel}</caption>}<thead><tr>{ranked && <><th>End</th><th>Rank</th></>}{[...dims, ...outs].map(c => <th key={c.id} className={c.kind === "dimension" ? undefined : "num"} title={[c.kind, ...(c.lineage || [])].join(" · ")}>{businessName(c.label || c.id)}{realCurrency(c.currency) && ` (${c.currency})`}</th>)}</tr></thead><tbody>{result.rows.filter(row => (row.panel ?? null) === panel).map((row, j) => <tr key={j} data-total={row.total || undefined}>{ranked && <><td>{row.end || ""}</td><td>{row.rank ?? ""}</td></>}{dims.map((c, k) => <td key={c.id}>{row.total && row.dimensions?.[k]?.kind === "null" ? k === row.dimensions.findIndex(d => d.kind === "null") ? "Total" : "" : scalar(row.dimensions?.[k]) ?? "none"}</td>)}{outs.map(c => {
    const cell = row.cells?.[c.index];
    if (!cell) return <td key={c.id} className="num" />;
    const shown = scalar(cell.value, c), why = cell.null_reasons || [];
    if (row.total && shown == null && !why.length) return <td key={c.id} />;
    return <td key={c.id} className="num" title={(cell.citations || []).filter(n => !/^\d+$/.test(String(n))).join(", ") || undefined}>{shown}{why.length ? `${shown == null ? "no value: " : " · "}${why.join("; ")}` : shown == null ? "no value" : ""}{(cell.citations || []).filter(n => /^\d+$/.test(String(n))).map((n, q) => <Cite key={q} n={n} onCite={onCite} />)}</td>;
  })}</tr>)}</tbody></table></div>);
}
