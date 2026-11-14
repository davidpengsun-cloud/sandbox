import React, { useEffect, useRef, useState } from "react";
import { Button, Panel } from "../../components/shared/Controls";
import { plain } from "./contextState";

export const FAMILIES = { suggested_mention: "Suggested passage-to-term links", candidate_term: "Candidate terms", privacy: "Personal data in documents", xref_match: "Client cross-reference matches" };
export default function Review({ api, queue, decided, highlightDoc }) {
  const root = useRef(null);
  useEffect(() => {
    if (!highlightDoc) return;
    setFilter("");
    const row = [...(root.current?.querySelectorAll("[data-doc]") || [])].find(row => row.dataset.doc === highlightDoc);
    row?.scrollIntoView?.({ block: "center" }); row?.querySelector("input,button")?.focus({ preventScroll: true });
  }, [highlightDoc, queue.data]);
  const [filter, setFilter] = useState(""), [definitions, setDefinitions] = useState({});
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const rows = Array.isArray(queue.data) ? queue.data : [];
  const groups = [...new Set(rows.map(row => row.family))];
  const shown = groups.includes(filter) ? filter : "";
  const releases = (Array.isArray(decided.data) ? decided.data : []).reduce((all, row) => all.set(row.release || "Release not recorded", [...(all.get(row.release || "Release not recorded") || []), row]), new Map());
  async function decide(row, decision) {
    if (busy) return;
    setBusy(true);
    const result = await api("/steward/decide", { tag: row.tag, decision, ...(row.family === "candidate_term" && decision === "accept" ? { definition: (definitions[row.tag] || "").trim() } : {}) });
    setMessage(result.error || (result.status === "pending_second_check" ? `second check · ${result.reason}` : `recorded · ${result.decision} · human tag at 1.0 supersedes ${result.superseded}${result.term_created ? ` · term created: ${result.term_created}` : ""}${result.embedded_now ? ` · chunk now ${result.readiness} and embedded for the first time` : ""}${result.note ? ` · ${result.note}` : ""}`));
    if (!result.error) await Promise.all([queue.refresh(), decided.refresh()]);
    setBusy(false);
  }
  return <section id="steward"><div className="screen-heading"><div><h1>Review queue</h1><p className="intro">Suggested links between passages and certified terms, waiting for a steward's decision.</p></div>
    <label htmlFor="queue-filter">Show<select id="queue-filter" value={shown} onChange={e => setFilter(e.target.value)}>
      <option value="">All pending ({rows.filter(row => row.upstream !== "rejected").length})</option>{groups.map(kind => <option key={kind} value={kind}>{FAMILIES[kind] || plain(kind)} ({rows.filter(row => row.family === kind && row.upstream !== "rejected").length})</option>)}</select></label></div>
    <div id="queuemsg" role="status" aria-live="polite">{message}</div>
    <section id="queue" ref={root} aria-label="Pending suggestions">
      {queue.loading && !queue.data ? <p role="status">Loading.</p> : queue.data?.error ? <p role="alert">{queue.data.error}</p> : !rows.length ? <p>Nothing waiting.</p> : groups.filter(kind => !shown || kind === shown).map(kind => <section key={kind}>
        <h2 className="queue-kind">{FAMILIES[kind] || plain(kind)}<span className="group-count">{rows.filter(row => row.family === kind && row.upstream !== "rejected").length}</span></h2>{rows.filter(row => row.family === kind).sort((a, b) => (a.upstream === "rejected") - (b.upstream === "rejected")).map(row => <Panel key={row.tag} className={`suggestion ${highlightDoc === row.doc ? "hl" : ""} ${row.upstream === "rejected" ? "settled" : ""}`} data-family={row.family} data-doc={row.doc}>
          <div className="suggestion-copy"><div className="row"><strong>{row.value}</strong><span className="badge">Suggested · {kind === "suggested_mention" ? "similarity" : "confidence"} {row.similarity ?? row.confidence} ({Math.round(Number(row.confidence) * 100)}%)</span></div>
            {row.definition && <p>{row.definition}</p>}{row.passage && <p>“{row.passage}”</p>}<p>{String(row.evidence || "").replace(/PROBABILISTIC/g, "probabilistic")}</p>
            <p>From <code>{row.chunk || row.doc}</code> · {row.unit || row.units?.join(", ") || row.shelf}</p>
            {row.assessment && <span className="badge warn">{plain(row.assessment)}</span>}{row.upstream && <p>{row.upstream} upstream {row.upstream === "rejected" ? "· no action needed" : ""}</p>}
            {(row.reject_requested_by || row.second_check_required_to_reject) && <p>Reject needs a second steward{row.reject_requested_by ? `; requested by ${row.reject_requested_by}` : ""}</p>}
          </div><div className="actions">{kind === "candidate_term" && <label>Definition to record with {row.value}<input className="def" type="text" value={definitions[row.tag] || ""} onChange={e => setDefinitions({ ...definitions, [row.tag]: e.target.value })} /></label>}
            <div className="row">{["accept", "reject"].map(decision => <Button key={decision} className={decision === "accept" ? "primary" : ""} data-tag={row.tag} data-d={decision} disabled={busy} onClick={() => decide(row, decision)}>{plain(decision)}</Button>)}</div></div>
        </Panel>)}</section>)}
    </section><p>Decisions are recorded with your name and go into the next release. Onboard and Quality show only to the roles that run them.</p>
    <h2>Decided · by release</h2><div id="decided" aria-live="polite">{decided.loading && !decided.data ? "Loading past decisions…" : decided.data?.error ? <p role="alert">{decided.data.error}</p> : releases.size ? [...releases].map(([release, decisions]) => <details key={release} className="release-decisions"><summary>{release}</summary><div className="scroll"><table><thead><tr>{["Passage", "Term", "Decision", "Who", "When"].map(name => <th key={name}>{name}</th>)}</tr></thead><tbody>{decisions.map((row, i) => <tr key={i}><td>{row.doc} · {row.chunk}</td><td>{row.term}</td><td>{row.decision}</td><td>{row.who || "not recorded"}{row.role ? ` · ${row.role}` : ""}</td><td>{row.at || "not recorded"}</td></tr>)}</tbody></table></div></details>) : <p>No readable suggested-link decisions yet.</p>}</div>
  </section>;
}
