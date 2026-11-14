import React, { useState } from "react";
import { Button, Panel } from "../../components/shared/Controls";
import Result, { general, ownSpans, resultNumber } from "./Result";
import { amount, businessName, declineText, figureMetadata, glossaryNotices, isAmount, periodText, plain, quarterWords, readablePeriods, realCurrency, withoutNoCurrency } from "./contextState";

const LABELS = { figure: "Certified figure", combined: "Answer with sources", sum_with_verdict: "Total", sum_without_verdict: "Total, not checked", parts_with_reason: "Parts, no total", abstain: "Cannot answer", abstain_with_reason: "Cannot answer", abstain_route_steward: "Sent to a steward", refuse: "Refused", blocked_access: "No access", cite_certified: "Definition", glossary_definition: "Glossary definition", cite: "Search results" };
export const armOf = (a, arms = {}) => a.agent ? `${a.agent.arm}, ${arms[a.agent.arm]?.side || "the reference agent"}` : a.arm;
export const citedAs = (c) => c.cited_as === "attributed" ? `silver, cited with attribution: ${c.attribution}` : c.cited_as === "authority" ? `${c.tier}, the authority` : `found by search, with no tier rule${c.tier ? `; landed ${c.tier}` : ""}`;
export function CopyButton({ value, label = "Copy the figure" }) {
  const [copied, setCopied] = useState(false);
  return <Button className="copy" aria-label={label} onClick={async e => { e.stopPropagation(); try { await navigator.clipboard.writeText(String(value)); setCopied(true); } catch { setCopied(false); } }}>{copied ? "Copied" : "Copy"}</Button>;
}
export function Cite({ n, children, onCite }) {
  return <a className="cite" href="#tab-sources" data-n={n} onClick={e => { e.preventDefault(); e.stopPropagation(); onCite(n || ""); }}>{children || `[${n}]`}</a>;
}
export function CitedText({ text, onCite }) {
  return withoutNoCurrency(text).split(/(\[\d+\])/g).map((part, i) => /^\[\d+\]$/.test(part) ? <Cite key={i} n={part.slice(1, -1)} onCite={onCite} /> : part);
}
export function periodWords(a) {
  const period = a.trace?.[0]?.period || a.period;
  const iso = a.months?.length === 1 ? a.months[0] : /^month (\d{4}-\d{2})/.exec(period || "")?.[1];
  return iso && /^\d{4}-\d{2}/.test(iso) ? new Date(`${iso.slice(0, 7)}-01T12:00:00Z`).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" }) : periodText(period);
}
function Parts({ a, onCite, metadata }) {
  const plan = Array.isArray(a.plan) ? a.plan : (a.figures || []).map(f => ({ ...f, status: f.value == null ? "error" : "ok", reason: f.error || "no value" }));
  return <div className="scroll"><table className="parts"><thead><tr><th>Unit</th><th className="num">Figure</th><th>Grain</th><th>Calendar</th>{a.agent && <th>Source</th>}</tr></thead><tbody>{plan.map((p, i) => <tr key={i}><td>{p.unit}</td><td className={p.status === "ok" ? "num" : undefined}>{p.status === "ok" ? `${isAmount(metadata[i]) ? `${metadata[i].currency} ` : ""}${amount(p.value, metadata[i])}` : p.reason}</td><td>{p.grain}</td><td>{p.calendar}{p.conversion && p.conversion !== "none" ? ` · ${p.conversion}` : ""}</td>{a.agent && <td>{(a.figures || []).find(f => f.unit === p.unit)?.cite && <Cite n={a.figures.find(f => f.unit === p.unit).cite} onCite={onCite} />}</td>}</tr>)}</tbody></table></div>;
}
function Rich({ display, onCite }) {
  const table = display.table, chart = display.chart;
  const keys = table.columns.map(c => c.key), xi = keys.indexOf(chart?.x), yi = keys.indexOf(chart?.y);
  const ci = keys.indexOf("currency");
  const shown = (value, c, row) => resultNumber(value, c.key === "__rank__" ? { ...c, kind: "count" } : { ...c, amount: Boolean(realCurrency(c.currency) || (ci >= 0 && realCurrency(row[ci]))) });
  const bars = chart ? table.rows.map(row => ({ label: quarterWords(String(row[xi] ?? "")), value: row[yi], row })).filter(row => Number.isFinite(row.value)).slice(0, chart.max_bars ?? table.rows.length) : [];
  const lo = Math.min(0, ...bars.map(r => r.value)), hi = Math.max(0, ...bars.map(r => r.value)), span = hi - lo || 1;
  const content = <div className="scroll"><table className="rich-table"><caption>{withoutNoCurrency(readablePeriods(table.caption)) || "Figures"}</caption><thead><tr>{table.columns.map((c, i) => <th key={i} className={c.type === "number" ? "num" : undefined}>{businessName(c.label)}</th>)}</tr></thead><tbody>{table.rows.map((row, i) => <tr key={i}>{table.columns.map((c, j) => <td key={j} className={c.type === "number" ? "num" : undefined}>{table.total_rows?.includes(i) && row[j] == null ? "" : c.type === "number" ? shown(row[j], c, row) : c.type === "cite" && row[j] ? <Cite n={row[j]} onCite={onCite} /> : quarterWords(String(row[j] ?? ""))}</td>)}</tr>)}{table.total && <tr><th>{table.total.label}</th><td className="num">{shown(table.total.value, table.columns.find(c => c.type === "number") || {}, table.rows[0] || [])}</td>{table.columns.slice(2).map((_, i) => <td key={i} />)}</tr>}</tbody></table></div>;
  return <><p className="ans"><CitedText text={readablePeriods(display.headline?.text)} onCite={onCite} /></p>{chart ? <><div className="chart" role="figure" aria-label="Bar chart drawn from the figures table">{bars.map((r, i) => <div className="bar-row" key={i}><span className="bl">{r.label}</span><svg aria-hidden="true"><line className="zero" x1={`${-lo / span * 100}%`} x2={`${-lo / span * 100}%`} y1="0" y2="18" /><rect className="bar" x={`${(Math.min(0, r.value) - lo) / span * 100}%`} y="2" width={`${Math.max(.5, Math.abs(r.value) / span * 100)}%`} height="14" /></svg><span className="bv num">{shown(r.value, table.columns[yi], r.row)}</span></div>)}</div>{table.rows.length > bars.length && <p>{bars.length} of {table.rows.length} bars shown; the table keeps every row.</p>}<details><summary>The figures as a table</summary>{content}</details></> : <>{display.chart_note && <p>No chart: {readablePeriods(display.chart_note)}</p>}{content}</>}{display.checked && <p title={display.checked.about}>{readablePeriods(display.checked.line)}</p>}{display.notes?.length > 0 && <p>{readablePeriods(display.notes.join("; "))}</p>}</>;
}
export function WayForward({ answer: a, me }) {
  const denied = a.denied_warehouses || [];
  let text = "";
  if (denied.length) {
    const said = new Set((a.reason || "").toLowerCase().match(/[\w-]+/g) || []), named = denied.filter(u => said.has(u.toLowerCase()));
    const other = (me?.reads || []).filter(u => !denied.includes(u));
    text = `${named.length ? `You may not read ${named.join(", ")}` : "This question needs a unit you may not read"}. Ask about ${other.join(", ") || "the units you read"} instead, or ask the business team that owns that data if you need access.`;
  } else if (a.joins_refused?.length || /no certified join path/i.test(a.reason || "")) text = "Ask for each unit's figure on its own; a join between them is a steward's to certify.";
  else if (/restricted personal data/i.test(a.reason || "")) text = "Ask for the figure without the restricted columns.";
  else if (/will not sum/i.test(a.reason || "")) text = "Ask for each measure on its own.";
  return text ? <p className="way"><b>A way forward.</b> {text}</p> : null;
}
export default function Answer({ answer: a, onCite, onReview, onOpenGraph, agentArms, mapping, me, onChoice }) {
  const [copiedAnswer, setCopiedAnswer] = useState(false);
  const [citedAnswer, setCitedAnswer] = useState(false);
  const [sentReview, setSentReview] = useState(false);
  const [helpful, setHelpful] = useState(false);
  if (a.error) return <Panel className="msg" role="alert"><span className="badge bad">Not answered</span> {a.error}</Panel>;
  const figure = ["figure", "sum_with_verdict", "sum_without_verdict"].includes(a.behaviour);
  const parts = ["sum_with_verdict", "sum_without_verdict", "parts_with_reason"].includes(a.behaviour);
  const by = Object.fromEntries((a.citations || []).map(c => [c.n, c]));
  const bound = [...new Set((a.figures || []).map(f => f.cite))].filter(n => by[n]);
  const about = figureMetadata(a, mapping), { currency } = about;
  const amounted = isAmount(about);
  const figureText = value => amount(value, about);
  const glossary = a.behaviour === "glossary_definition";
  const label = [a.context?.term || a.trace?.[0]?.term, periodWords(a), a.context?.time_mode === "as_reported" ? "as reported" : a.context?.time_mode === "current" ? "current" : ""].filter(Boolean).join(" · ");
  const liveMs = Number(a._live_ms);
  const stageMsSum = Array.isArray(a.run_trace?.stages)
    ? a.run_trace.stages.reduce((acc, s) => acc + (Number.isFinite(Number(s?.ms)) && Number(s.ms) > 0 ? Number(s.ms) : 0), 0)
    : 0;
  const totalMs = Number.isFinite(liveMs) && liveMs > 0 ? liveMs : stageMsSum > 0 ? stageMsSum : null;
  const pathNodes = Array.isArray(a.path?.nodes) ? a.path.nodes.filter(n => n && n.state !== "skipped") : [];
  const stepItems = pathNodes.length
    ? pathNodes.map(n => ({ id: n.id, label: `${n.kind}: ${n.label}`, state: n.state || "done", time: n.time }))
    : Array.isArray(a.run_trace?.stages) && a.run_trace.stages.length
      ? a.run_trace.stages.map((s, i) => ({ id: `${s.stage}-${i}`, label: s.stage, state: "done", time: null }))
      : Array.isArray(a.trace) && a.trace.length
        ? a.trace.map((t, i) => ({ id: `${t.step || i}`, label: t.step || `Step ${i + 1}`, state: "done", time: null }))
        : ["question", "terms", "measure", "tables", "access", "runtime", "query", "figure"].map(id => ({ id, label: id, state: "done", time: null }));
  const durationSec = totalMs != null ? Math.max(0.1, totalMs / 1000).toFixed(1) : null;

  const handleCopyAnswer = async e => {
    e.stopPropagation();
    const text = withoutNoCurrency(a.answer_text || a.answer || a.definition || (a.figure != null ? figureText(a.figure) : a.reason || a.question || ""));
    try {
      await navigator.clipboard.writeText(String(text));
      setCopiedAnswer(true);
    } catch {
      setCopiedAnswer(false);
    }
  };
  const handleCiteAnswer = async e => {
    e.stopPropagation();
    const firstN = a.citations?.[0]?.n || "";
    if (onCite) onCite(firstN);
    const citeStr = (a.cite || []).join(", ") || a.unit || a.question || "";
    try {
      await navigator.clipboard?.writeText(String(citeStr));
    } catch {
      /* Clipboard may be unavailable. */
    }
    setCitedAnswer(true);
  };
  const handleSendReview = e => {
    e.stopPropagation();
    if (onReview && a.candidate_sources?.[0]) {
      onReview(a.candidate_sources[0]);
      setSentReview(true);
    }
  };

  return <Panel className="msg answer-card" aria-label="Answer" tabIndex={-1} data-behaviour={a.behaviour}>
    <div className="row"><span className={`badge ${["refuse", "blocked_access", "sum_without_verdict"].includes(a.behaviour) ? "bad" : ["figure", "combined", "cite_certified", "glossary_definition", "sum_with_verdict"].includes(a.behaviour) ? "ok" : "warn"}`} title={a.behaviour}>{LABELS[a.behaviour] || plain(a.behaviour)}</span><span className="badge" title={`run config ${a.arm}`}>{armOf(a, agentArms)}</span>{a.agent && <span className="badge">agent · {a.agent.mode}</span>}</div>
    <details className="activity-line" onClick={e => e.stopPropagation()}>
      <summary className="activity-summary">{durationSec ? `Worked ${durationSec} s` : "Worked"} · {stepItems.length} steps ›</summary>
      <div className="activity-body">
        <ol className="activity-steps">
          {stepItems.map(s => (
            <li key={s.id} className={`activity-step activity-step-${s.state}`}>
              <span className="activity-step-label">{s.label}</span>
              {s.time && <span className="activity-step-time">{s.time}</span>}
            </li>
          ))}
        </ol>
        <a
          href="#tab-graph"
          className="activity-graph-link"
          onClick={e => {
            e.preventDefault();
            e.stopPropagation();
            if (onOpenGraph) onOpenGraph(a);
          }}
        >
          Graph tab ›
        </a>
      </div>
    </details>
    {figure && label && <div className="figlabel">{label}</div>}
    {a.agent && a.answer_text && !glossary && <p className="ans"><CitedText text={a.answer_text} onCite={onCite} /></p>}
    {a.behaviour === "clarify" ? <><p>{a.reason}</p>{ownSpans(a).length > 0 && <p>Which meaning of {[...new Set(ownSpans(a).reverse().map(s => Array.from(a.question).slice(s.start, s.end).join("")))].map(text => `“${text}”`).join(", ")}?</p>}<div className="choices row" role="group" aria-label="Choose a meaning">{(a.clarification?.choices || []).map((choice, i) => <Button key={i} data-choice={i} title={choice.id} disabled={!onChoice || !ownSpans(a).length} onClick={e => { e.stopPropagation(); onChoice(choice.label); }}>{choice.label}</Button>)}</div></> : a.display?.table && !a.display.error ? <Rich display={a.display} onCite={onCite} /> : general(a) ? <Result result={a.result} onCite={onCite} /> : a.behaviour === "combined" ? <><p className="ans"><CitedText text={a.answer_text} onCite={onCite} /></p><div className="scroll"><table className="figs"><thead><tr><th>Figure · certified column</th><th className="num">Value</th><th>Source</th></tr></thead><tbody>{(a.figures || []).map((f, i) => <tr key={i}><td>{f.label} ({f.kind}) <code>{f.column}</code></td><td className="num">{`${isAmount({ currency: f.currency, kind: f.kind }) ? `${f.currency} ` : ""}${amount(f.value, { currency: f.currency, kind: f.kind })}`}</td><td>{f.cite && <Cite n={f.cite} onCite={onCite} />}</td></tr>)}</tbody></table></div><p><span className={`badge ${a.guard?.passed ? "ok" : "bad"}`}>gate {a.guard?.passed ? "passed" : "failed"}</span>SQL by {a.sql_source} · text by {a.phrased_by} · <Cite onCite={onCite}>{a.citations?.length || 0} cited · {a.not_cited?.length || 0} not used · {a.withheld?.length || 0} held back</Cite></p></> : <>
      {figure && (a.rows ? <div className="scroll"><table className="parts"><thead><tr><th>{businessName(a.group_by) || "Group"}</th><th className="num">Figure{amounted ? ` (${currency})` : ""}</th></tr></thead><tbody>{Object.entries(a.rows).map(([name, value]) => <tr key={name}><td>{quarterWords(name)}</td><td className="num">{amounted ? `${currency} ` : ""}{figureText(value)}</td></tr>)}</tbody></table></div> : <div className="figrow"><span className="cur" data-currency>{amounted ? currency : ""}</span><div className="fig">{figureText(a.figure)}</div></div>)}
      {a.behaviour === "cite_certified" && <>{(a.answer || !a.agent) && <p className="ans">{a.answer || a.answer_text}</p>}{!/\bCited: /.test(a.answer || (a.agent ? "" : a.answer_text) || "") && (a.cite || []).length > 0 && <p>Cites: {(a.cite || []).map(doc => <code key={doc}>{doc} </code>)}</p>}</>}
      {glossary && <><p className="ans">{a.definition}</p><p className="definition-source">Source: the pack's glossary{a.steward ? ` (steward: ${a.steward})` : ""}{a.contexts?.length ? `, certified through its context ${a.contexts.join(", ")}` : ""}. No document defines it, so none is cited.</p></>}
      {a.behaviour === "cite" && <p>Cites: {(a.cited_docs || (a.cite || []).map(doc => ({ doc }))).map(d => <span key={d.doc}><code>{d.doc}</code> <span className="badge">{d.shelf}</span> </span>)}</p>}
      {parts && <><p><span className="badge warn">{a.behaviour === "sum_with_verdict" ? "commensurable" : a.behaviour === "sum_without_verdict" ? "no verdict" : a.missing_parts?.length ? "incomplete: no total" : "not commensurable"}</span></p><Parts a={a} onCite={onCite} metadata={about.parts} /></>}
    </>}
    {mapping?.definition && a.behaviour === "figure" && <p>{mapping.definition}</p>}
    {a.unit && <div className="answer-source">From <code>{mapping?.source || `${a.unit} · ${a.column || ""}`}</code></div>}
    {a.display?.table && a.behaviour === "sum_with_verdict" && <span className="badge ok">commensurable</span>}
    {a.reason && !glossary && <p className="small">{declineText(a.reason, a.question)}</p>}{glossary && glossaryNotices(a.reason) && <p className="small definition-notice">{glossaryNotices(a.reason)}</p>}{["refuse", "blocked_access"].includes(a.behaviour) && <WayForward answer={a} me={me} />}{a.notes?.length > 0 && <p>{a.notes.join("; ")}</p>}{a.joins_refused?.length > 0 && <p>Joins refused: {a.joins_refused.join(", ")}</p>}
    {a.unit && <details className="prov"><summary>Source: {a.unit}</summary><p>{a.unit} · {a.column}{a.context && ` · context ${a.context.context}${a.context.time_mode === "as_reported" ? ", as reported" : ""}`}{a.join_path?.length ? ` · via ${a.join_path.join(", ")}` : ""}</p></details>}
    {a.candidate_sources?.length > 0 && <p>Routed to the steward: {a.candidate_sources.map(doc => <React.Fragment key={doc}>{onReview ? <a className="queue-link" href="#view-review" data-doc={doc} onClick={e => { e.preventDefault(); e.stopPropagation(); onReview(doc); }}>{doc}</a> : doc} </React.Fragment>)}</p>}
    {a.fact?.asserted_by && <p>Asserted by {a.fact.asserted_by} on {a.fact.asserted_on}.</p>}
    {bound.length > 0 && <div className="bound">{bound.map(n => <div key={n}><Cite n={n} onCite={onCite} /> <code>{by[n].doc}{by[n].version ? ` v${by[n].version}` : ""}</code> {by[n].title} · {citedAs(by[n])}</div>)}</div>}
    <div className="answer-actions" role="group" aria-label="Answer actions" onClick={e => e.stopPropagation()}>
      <Button type="button" className="answer-action copy" aria-label="Copy answer" onClick={handleCopyAnswer}>{copiedAnswer ? "Copied" : "Copy"}</Button>
      <span className="answer-action-sep" aria-hidden="true">·</span>
      <Button type="button" className="answer-action" aria-label="Cite" onClick={handleCiteAnswer}>{citedAnswer ? "Cited" : "Cite"}</Button>
      <span className="answer-action-sep" aria-hidden="true">·</span>
      <Button type="button" className="answer-action" aria-label="Send to review" disabled={!onReview || !a.candidate_sources?.[0]} onClick={handleSendReview}>{sentReview ? "Sent to review" : "Send to review"}</Button>
      <span className="answer-action-sep" aria-hidden="true">·</span>
      <Button type="button" className="answer-action" aria-label="Helpful?" aria-pressed={helpful} onClick={e => { e.stopPropagation(); setHelpful(h => !h); }}>{helpful ? "Helpful ✓" : "Helpful?"}</Button>
    </div>
  </Panel>;
}
