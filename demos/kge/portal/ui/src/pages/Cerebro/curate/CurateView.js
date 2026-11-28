import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "../../../components/shared/Controls";
import { mergePages, readGraphPages } from "../graph/graphData";
import { CERTIFY_PLACEHOLDER } from "../../../components/shell/apps";
import { useConfig } from "../../../services/config";
import { ALL, FAMILY_LABELS, plainPassage, byKind, candidateChecks, certifiedShare, checkTone, decideBody, filterChips, kindOf, nodeOf, outcome, reviewGroups, shownRows, stewardCall, waiting } from "./curateData";
import "./curate.css";

/* Curate (cerebro.md §1.5, §7.1, steward only), laid out as the approved prototype: the page heading with the role
   note; the four stat tiles; on the left the proposals (§6.2), filtered by kind (All, then each kind present), one row
   each with its kind, the proposal, its source and View in graph, Accept and Reject, and the decided history with who
   and when; on the right the candidate to review with its check results, business acceptance, Certify and sign-off,
   and the gaps people hit. Reads and
   decisions go through the portal's Cerebro steward routes, as the signed-in person; KGE checks each role
   and records each decision with their name. `fetcher` is the browser's unless a test gives its own. */
function useQueue(fetcher, path) {
  const [result, setResult] = useState({ status: "loading" });
  const turn = useRef(0);
  const refresh = useCallback(async () => {
    const mine = ++turn.current;
    const reply = await stewardCall(fetcher, path).catch(() => null);
    if (mine !== turn.current || reply === null) return;
    setResult(reply.error ? { status: "error", error: reply.error } : { status: "ready", value: reply, rows: Array.isArray(reply) ? reply : [] });
  }, [fetcher, path]);
  useEffect(() => { refresh(); return () => { ++turn.current; }; }, [refresh]);
  return { ...result, refresh };
}

/* The share of readable columns with a certified term, from the graph read itself (columns unfolded). */
function useCertifiedShare(fetcher) {
  const [share, setShare] = useState({ status: "loading" });
  useEffect(() => {
    const abort = new AbortController();
    readGraphPages({ fetcher, signal: abort.signal })
      .then(({ pages }) => { if (!abort.signal.aborted) setShare({ status: "ready", value: certifiedShare(mergePages(pages)) }); })
      .catch(() => { if (!abort.signal.aborted) setShare({ status: "error" }); });
    return () => abort.abort();
  }, [fetcher]);
  return share;
}

const tileValue = (read, value) => (read.status === "ready" ? value : read.status === "error" ? "—" : "…");

function Candidate({ read, fetcher, onDone }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState({ text: "", error: false });
  const ob = read.value || {};
  const candidate = ob.candidate;
  const can = ob.can || {};
  async function act(path, body, done) {
    if (busy) return;
    setBusy(true);
    const result = await stewardCall(fetcher, path, body).catch(() => ({ error: "" }));
    setMessage({ text: result.error || result.said || done, error: Boolean(result.error) });
    await onDone();
    setBusy(false);
  }
  if (read.status === "loading") return <p role="status">Loading the candidate.</p>;
  if (read.status === "error") return <p role="alert">{read.error}</p>;
  const groups = reviewGroups(ob);
  const gatePassed = candidate?.gate?.passed === true;
  const changes = groups.reduce((sum, group) => sum + group.items, 0);
  return <>
    <div role={message.error ? "alert" : "status"} aria-live={message.error ? "assertive" : "polite"} className="curate-message">{message.text}</div>
    {candidate ? <p className="curate-candidate-head"><code className="curate-candidate-name" title={candidate.hash}>{String(candidate.hash).slice(0, 12)}</code>
      <span className="curate-muted">{changes === 1 ? "1 change" : `${changes} changes`}</span></p>
      : <p className="curate-muted">No candidate yet.</p>}
    {candidate && <ul className="curate-checks" aria-label="Check results">
      {candidateChecks(ob).map((check) => <li key={check.name}><span>{check.name}</span> <span className={`curate-status ${checkTone(check)}`}>{check.result}</span></li>)}
    </ul>}
    {groups.length > 0 && <ul className="curate-groups" aria-label="Items to review">
      {groups.map((group) => <li key={group.id} className="curate-group">
        <strong>{group.label}</strong>
        <p className="curate-muted">{group.items === 1 ? "1 change" : `${group.items} changes`} · {group.accepted} accepted · {group.certified} certified</p>
        <div className="curate-actions">
          {can.certify && group.accepted === group.items && group.certified < group.items && <Button className="primary" disabled={busy} onClick={() => act("certify", group.body, "Certified.")}>Certify</Button>}
          {can.accept && group.accepted < group.items && <Button disabled={busy} onClick={() => act("accept", group.body, "Business acceptance recorded.")}>Record business acceptance</Button>}
        </div>
      </li>)}
    </ul>}
    {candidate && can.sign_off && gatePassed && !candidate.signed_off && <div className="curate-actions">
      <Button className="primary" disabled={busy} onClick={() => act("sign-off", { hash: candidate.hash }, "Sign-off started.")}>Sign off</Button></div>}
    {candidate && <p className="curate-muted curate-foot">Stewards accept, certify and sign off; engineers release.</p>}
  </>;
}

function Tile({ label, value }) {
  return <div className="curate-tile"><span className="curate-tile-value">{value}</span><span className="curate-tile-label">{label}</span></div>;
}

/* A documents item's way to Certify, the external document certification app (cerebro.md §9.3, its Certify row): its
   address (PORTAL_CERTIFY_URL) as the global bar opens it, the placeholder while none is set, and
   nothing where Certify is not set up. */
function CertifyLink({ config }) {
  if (!config?.apps?.certify) return null;
  if (!config.certifyUrl) return <span className="curate-muted"><span className="gb-sr">Certify opens at </span>{CERTIFY_PLACEHOLDER}</span>;
  return <a href={config.certifyUrl} target="_blank" rel="noopener noreferrer">Open in Certify<span className="gb-sr"> (opens outside the platform)</span><span aria-hidden="true"> ↗</span></a>;
}

/* One row per proposal, two lines as the prototype draws it: what it proposes with its score, then its family and
   source. The definition, the passage, the evidence and the assessment fold under "Why" until the steward opens it. */
function Proposal({ row, kind, busy, definition, config, onDefinition, onDecide, onOpen }) {
  const node = nodeOf(row);
  const settled = !waiting(row);
  const confidence = Number(row.similarity ?? row.confidence);
  const passage = plainPassage(row.passage);
  const why = row.definition || passage || row.evidence || row.assessment;
  return <li className={`curate-proposal${settled ? " settled" : ""}`} data-tag={row.tag} data-family={row.family}>
    <span className={`curate-kind kind-${kind.id}`}>{kind.one}</span>
    <div className="curate-proposal-main">
      <div className="curate-proposal-head">
        <strong className="curate-proposal-what">{row.value}</strong>
        {Number.isFinite(confidence) && <span className="curate-chip proposed">Proposed · {row.family === "suggested_mention" ? "similarity" : "confidence"} {Math.round(confidence * 100)}%</span>}
      </div>
      <p className="curate-source"><span className="curate-family">{FAMILY_LABELS[row.family] || row.family}</span> · From <code>{row.chunk || row.doc || "not recorded"}</code>{(row.unit || row.units?.length || row.shelf) ? ` · ${row.unit || row.units?.join(", ") || row.shelf}` : ""}</p>
      {why && <details className="curate-why">
        <summary>Why</summary>
        {row.definition && <p>{row.definition}</p>}
        {passage && <p className="curate-quote">“{passage}”</p>}
        {row.evidence && <p className="curate-muted">{row.evidence}</p>}
        {row.assessment && <p><span className="curate-chip warn">{String(row.assessment).replace(/_/g, " ")}</span></p>}
      </details>}
      {row.upstream && <p className="curate-muted">{row.upstream} upstream{settled ? " · a decision here is recorded in the graph only" : ""}</p>}
      {(row.reject_requested_by || row.second_check_required_to_reject) && <p className="curate-muted">Reject needs a second steward{row.reject_requested_by ? `; requested by ${row.reject_requested_by}` : ""}</p>}
      {row.family === "candidate_term" && !settled && <label className="curate-definition">Definition to record with {row.value}
        <input type="text" value={definition} onChange={(event) => onDefinition(event.target.value)} /></label>}
      {kindOf(row) === "documents" && <p className="curate-certify"><CertifyLink config={config} /></p>}
    </div>
    <div className="curate-actions curate-proposal-actions">
      {node && <Button onClick={() => onOpen(node)}>View in graph</Button>}
      <Button className={settled ? "" : "primary"} disabled={busy} onClick={() => onDecide("accept")}>Accept</Button>
      <Button disabled={busy} onClick={() => onDecide("reject")}>Reject</Button>
    </div>
  </li>;
}

export default function CurateView({ actions, fetcher = globalThis.fetch }) {
  const config = useConfig();
  const queue = useQueue(fetcher, "queue");
  const decided = useQueue(fetcher, "queue?status=decided");
  const kinds = byKind(queue.rows);
  const [shown, setShown] = useState(ALL);
  const chips = filterChips(kinds, shown);
  const current = kinds.find((kind) => kind.id === shown);
  const rows = shownRows(kinds, shown);
  const [definitions, setDefinitions] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState({ text: "", error: false });
  const [accepted, setAccepted] = useState(0);
  const gaps = useQueue(fetcher, "gaps");
  const candidate = useQueue(fetcher, "candidate");
  const share = useCertifiedShare(fetcher);

  async function decide(row, decision) {
    if (busy) return;
    setBusy(true);
    const result = await stewardCall(fetcher, "decide", decideBody(row, decision, definitions[row.tag] || "")).catch(() => ({ error: "" }));
    setMessage({ text: result.error || outcome(result, decision), error: Boolean(result.error) });
    if (!result.error && decision === "accept" && result.status !== "pending_second_check") setAccepted((before) => before + 1);
    await Promise.all([queue.refresh(), decided.refresh()]);
    setBusy(false);
  }

  const waitingCount = (queue.rows || []).filter(waiting).length;
  const history = decided.rows || [];
  const listName = current ? current.label : "All proposals";
  return <div className="cerebro-curate">
    <div className="curate-page">
      <header className="curate-head">
        <h2>Review and curation</h2>
        <p className="curate-note">You review as a steward. Each decision goes into the next release with your name.</p>
      </header>
      <div className="curate-tiles" aria-label="Curate at a glance" role="group">
        <Tile label="Proposals waiting" value={tileValue(queue, waitingCount)} />
        <Tile label="Accepted this session" value={accepted} />
        <Tile label="Columns with a certified term" value={tileValue(share, share.value === null ? "—" : `${Math.round(share.value * 100)}%`)} />
        <Tile label="Gaps people hit this week" value={tileValue(gaps, Number(gaps.value?.total) || 0)} />
      </div>
      <div className="curate-columns">
        <div className="curate-main">
          <section className="curate-card curate-proposals" aria-label="Proposals">
            <h3 className="curate-card-title">Proposals</h3>
            <div role={message.error ? "alert" : "status"} aria-live={message.error ? "assertive" : "polite"} className="curate-message">{message.text}</div>
            {queue.status === "loading" ? <p role="status">Loading the proposals.</p>
              : queue.status === "error" ? <p role="alert">{queue.error}</p>
              : <>
                <div className="curate-filter" role="group" aria-label="Filter by kind">
                  <Button id="curate-kind-all" aria-pressed={shown === ALL} onClick={() => setShown(ALL)}>All <span className="curate-count">{waitingCount}</span></Button>
                  {chips.map((kind) => <Button key={kind.id} id={`curate-kind-${kind.id}`} aria-pressed={kind.id === shown} onClick={() => setShown(kind.id)}>
                    {kind.label} <span className="curate-count">{kind.waiting}</span></Button>)}
                </div>
                {rows.length ? <ul className="curate-rows" aria-label={listName}>
                  {rows.map(({ row, kind }) => <Proposal key={row.tag} row={row} kind={kind} busy={busy} config={config} definition={definitions[row.tag] || ""}
                    onDefinition={(value) => setDefinitions((before) => ({ ...before, [row.tag]: value }))}
                    onDecide={(decision) => decide(row, decision)} onOpen={(node) => actions.openNode(node)} />)}
                </ul> : <p className="curate-muted">{current ? `Nothing waiting in ${current.label.toLowerCase()}.` : "Nothing waiting."}</p>}
              </>}
          </section>
          <section className="curate-card" aria-label="Decided">
            <h3 className="curate-card-title">Decided</h3>
            {decided.status === "loading" ? <p role="status">Loading past decisions.</p>
              : decided.status === "error" ? <p role="alert">{decided.error}</p>
              : !history.length ? <p className="curate-muted">No decisions you may read yet.</p>
              : <div className="curate-scroll"><table><thead><tr>{["What", "Decision", "Who", "When", "Release"].map((name) => <th key={name} scope="col">{name}</th>)}</tr></thead>
                <tbody>{history.map((row, i) => <tr key={`${row.tag}-${i}`}><td>{row.term || row.value || row.tag}{row.doc ? <span className="curate-muted"> · {row.doc}</span> : null}</td>
                  <td>{row.decision}</td><td>{row.who || "not recorded"}{row.role ? ` · ${row.role}` : ""}</td><td>{row.at || "not recorded"}</td><td>{row.release ? <code>{String(row.release).slice(0, 12)}</code> : "not recorded"}</td></tr>)}</tbody></table></div>}
          </section>
        </div>
        <div className="curate-side">
          <section className="curate-card curate-candidate" aria-label="Candidate to review">
            <h3 className="curate-card-title">Candidate to review</h3>
            <Candidate read={candidate} fetcher={fetcher} onDone={candidate.refresh} />
          </section>
          <section className="curate-card curate-gaps" aria-label="Gaps people hit">
            <h3 className="curate-card-title">Gaps people hit</h3>
            {gaps.status === "loading" ? <p role="status">Loading the gaps.</p>
              : gaps.status === "error" ? <p role="alert">{gaps.error}</p>
              : !gaps.value?.gaps?.length ? <p className="curate-muted">No question went unanswered this week.</p>
              : <ul className="curate-gap-list" aria-label="Questions not answered this week">
                {gaps.value.gaps.map((gap) => <li key={gap.text}><span className="curate-gap-text">{gap.text}</span> <span className="curate-muted">{gap.count === 1 ? "asked once" : `asked ${gap.count} times`}</span></li>)}
              </ul>}
          </section>
        </div>
      </div>
    </div>
  </div>;
}
