import React, { useRef, useState } from "react";
import { Button, Panel } from "../../components/shared/Controls";
import { ACTION_LABELS, STAGES, actionPlan, plain, proposalPlan, referenceFigure, reportUrl, unitDone, unitPlan } from "./contextState";

const obPlain = (text) => String(text ?? "").replace(/\s*\(ROWS=\w+\)/g, "");
export function changeLines(record, descriptions) {
  if (!record) return [];
  const value = v => v == null ? "none" : String(v), columns = names => names?.length ? `(${names.join(", ")})` : "none";
  const foreign = key => `${columns(key.columns)} referencing ${key.ref_table} ${columns(key.ref_columns)}`;
  const lines = descriptions ? [...descriptions] : (record.lock?.changes || []).flatMap(c => {
    const table = `${c.source}.${c.table}`, column = `${table}.${c.column}`, type = v => value(v && typeof v === "object" ? v.type : v);
    if (c.change === "key-changed") {
      const before = c.was || {}, after = c.now || {}, result = [];
      if (columns(before.primary_key) !== columns(after.primary_key)) result.push(`Primary key of ${table} changed from ${columns(before.primary_key)} to ${columns(after.primary_key)}`);
      const old = (before.foreign_keys || []).map(foreign), next = (after.foreign_keys || []).map(foreign);
      old.filter(key => !next.includes(key)).forEach(key => result.push(`Foreign key of ${table} ${key} removed`));
      next.filter(key => !old.includes(key)).forEach(key => result.push(`Foreign key of ${table} ${key} added`));
      if (old.join("; ") !== next.join("; ") && old.every(key => next.includes(key)) && next.every(key => old.includes(key))) result.push(`Foreign keys of ${table} reordered from ${old.join("; ")} to ${next.join("; ")}`);
      return result;
    }
    return ({ "table-added": `Table ${table} added`, "table-removed": `Table ${table} removed`, "column-added": `Column ${column} added (${type(c.now)})`,
      "column-removed": `Column ${column} removed (was ${type(c.was)})`, "column-retyped": `Column ${column} retyped from ${value(c.was)} to ${value(c.now)}`,
      "column-described": `Description of column ${column} changed from ${value(c.was)} to ${value(c.now)}`, "column-moved": `Column ${column} moved from position ${value(c.was)} to ${value(c.now)}` })[c.change] || `Change to ${table}: ${c.change}`;
  });
  if (!lines.length) lines.push("No schema changes");
  if (descriptions == null) {
    for (const doc of record.estate?.unread_documents || []) lines.push(`${doc.path}: ${doc.evidence}`);
    for (const path of record.inputs?.ignored || []) lines.push(`Ignored: ${path}`);
    if (record.inputs?.suggestion) lines.push(`Declarations to add\n${record.inputs.suggestion}`);
    if (record.inputs?.upload) lines.push(`Upload ${record.inputs.upload.sha256.slice(0, 12)}`);
    if (record.rechecked) lines.push("Checked again: the last check did not pass");
  }
  if (record.estate) lines.push(`Estate ${record.estate.rebuilt ? "rebuilt" : "not rebuilt"}: ${{ "reader-changed": "the document reader changed", "no-estate": "no estate had been built", "fixture-changed": "the fixture files changed", unchanged: "the fixture files are unchanged", "no-fixture": "this pack has no fixture" }[record.estate.why] || record.estate.why}`);
  if (record.reloaded) lines.push("Reloaded the same model");
  return lines;
}
export function Items({ items }) {
  return (items || []).map(item => <div className="item" key={item.id}><b>{item.id}</b> · {item.title || item.detail?.question || item.kind} {item.change && <span className="change-kind">{item.change.kind} </span>}<span className="badge">{item.state}</span>
    <p className="muted">{item.from}{item.made_by && ` · drafted by ${item.made_by === "model" ? `the model ${item.model || ""}`.trim() : "rules"}`}</p>
    {item.accepted_by && <p>accepted by {item.accepted_name || item.accepted_by}</p>}{item.certified_by && <p>certified by {item.certified_name || item.certified_by}</p>}
    {item.kind === "question" && <p><b>{referenceFigure(item.detail?.figure)}</b> · <code>{item.detail?.sql}</code></p>}
    {item.kind === "mapping" && <p>{item.detail?.relation} <code>{item.detail?.iri}</code> · {item.detail?.standard} {item.detail?.standard_version}</p>}
    {item.kind === "policy" && <p>restrict <code>{item.detail?.column}</code> · {item.detail?.family} · {item.detail?.relation} <code>{item.detail?.iri}</code></p>}
    {item.detail?.phrase && <p>add the alias <code>{item.detail.phrase}</code> for {item.detail.term_name} <code>{item.detail.term}</code> in <code>{item.detail.section || "terminology.aliases"}</code> · why: {item.detail.why || "missing alias"} · evidence: {item.detail.evidence_column} ({item.detail.evidence}){item.detail.also_answers?.length ? ` · would also answer: ${item.detail.also_answers.join("; ")}` : ""}</p>}
  </div>);
}
function Action({ plan, ob, decide, busy, primary = true }) {
  if (!plan.mine || !ACTION_LABELS[plan.action]) return null;
  return <Button className={primary ? "primary" : ""} data-act={plan.action} data-unit={plan.unit} data-proposal={plan.proposal} disabled={busy || ob.job?.state === "running"} onClick={() => decide(plan.action, plan)}>{ACTION_LABELS[plan.action]}</Button>;
}
function Job({ job, ob }) {
  if (!job) return null;
  const last = job.progress?.at(-1), name = { checks: "Checks", "sign-off": "Sign-off", release: "Release", rollback: "Rollback", improve: "Improvement" }[job.step] || job.step;
  const live = (ob.units || []).filter(u => u.release?.model_hash === job.hash).map(u => u.label || u.unit).join(", ") || "the candidate";
  const steps = last ? ` · ${last.n} of ${last.of} steps` : "";
  const seconds = Math.max(0, Math.floor(((Date.parse(job.finished_at) || Date.now()) - (Date.parse(job.started_at) || Date.now())) / 1000));
  const duration = seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  const text = job.state === "running" ? `${name} running · ${duration} elapsed · started by ${job.by}` : job.state === "done" ? ({ checks: `Checks passed${steps}`, "sign-off": `Signed off by ${job.by}`, release: `Released: ${live} is live`, improve: `Proposal drafted${steps}` }[job.step] || `${name} done`) : `${name} failed: ${job.message || job.state}`;
  return <div className="job" id="ob-job" data-job-id={job.id} data-state={job.state} role="status" aria-live="polite">
    {job.state === "running" && <progress aria-label={`${name} running`} value={last?.n} max={last?.of} />}{text}{job.state !== "running" && seconds >= 1 && ` · took ${duration}`}
    <p>{job.step} · {job.by || "not recorded"} · {job.started_at}{job.finished_at && ` → ${job.finished_at}`}</p>
    {job.progress?.length > 0 && <ol className="job-steps" aria-label="Job steps">{job.progress.map((p, i) => <li key={i}>{p.n} of {p.of} · {p.name} · {p.state}</li>)}</ol>}
    {job.message && <p>{job.message}</p>}{job.gate?.lines?.length > 0 && <div id="ob-gate" className="lines">{job.gate.lines.map((line, i) => <div key={i}>{line}</div>)}</div>}
    {(job.hash || job.report) && <p>{job.hash && <>candidate <code title={job.hash}>{job.hash.slice(0, 12)}</code></>}{reportUrl(job.report) && <> · <a href={reportUrl(job.report)} target="_blank" rel="noopener">report</a></>}</p>}
  </div>;
}
function Next({ plan, ob, decide, busy, job }) {
  return <div className={`next ${plan.action && !plan.mine ? "wait" : ""}`}><div className="cta"><span>{plan.at < 0 && !plan.action ? `${plan.label} is live.${ob.can?.improve ? " Improve: Run, below, proposes changes from the questions it misses." : ""}` : plan.mine ? `Next: ${plan.phrase}.` : `Waiting on ${plan.role} to ${plan.phrase}.`}</span><Action plan={plan} ob={ob} decide={decide} busy={busy} /></div>{job && <Job job={job} ob={ob} />}</div>;
}
const releaseName = v => v.release || v.version || v.model_hash || "Unnamed release";
export function ReleaseHistory({ ob, me, decide, busy }) {
  const versions = ob.versions || [], current = versions.find(v => v.current)?.model_hash;
  const score = s => s && (s.total ?? s.of) != null ? `${s.passed}/${s.total ?? s.of}` : "not scored";
  return <section id="ob-versions"><h2>History · releases, sign-offs and rollbacks</h2>{ob.can?.rollback && <Button id="ob-rollback" disabled={busy || ob.job?.state === "running"} onClick={() => decide("rollback")}>Roll back to the previous release</Button>}
    {!versions.length ? <p>No releases yet.</p> : <><div className="scroll"><table><thead><tr>{["Version", "Released", "Signed off", "Learnable", "Held-out", "Note", "Decision"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{versions.map((v, i) => <tr key={i}><td><code>{v.version || releaseName(v)}</code>{v.release && v.version && ` ${v.release}`} <span title={v.model_hash}>{v.model_hash?.slice(0, 12)}</span>{v.current && <span className="badge ok">current</span>}{v.rollback_of && <span className="badge warn">rollback</span>}</td><td>{v.released_at || "not recorded"}</td><td>{v.signed_off_by || "none"}</td><td>{score(v.scores?.learnable)}</td><td>{score(v.scores?.heldout)}</td><td>{v.note}{v.rollback_of && <> Rolled back from <code>{v.rollback_of}</code></>}</td><td>{!v.current && v.model_hash !== current && ob.can?.release && (v.kept_pack !== false ? <Button data-release-hash={v.model_hash} disabled={busy || ob.job?.state === "running"} onClick={() => decide("release", { hash: v.model_hash })}>Release again</Button> : "no kept pack")}</td></tr>)}</tbody></table></div><h3>Recorded item decisions</h3>
      {versions.map((v, i) => {
        const rows = Object.entries(v.audit || {}).filter(([unit]) => me.reads?.includes(unit)).flatMap(([unit, items]) => Object.entries(items).flatMap(([item, decisions]) => ["accepted", "certified"].filter(kind => decisions[kind]).map(kind => ({ unit, item, kind, ...decisions[kind] }))));
        return <details className="release-decisions" key={i}><summary>{releaseName(v)} · {v.released_at || "date not recorded"}</summary>{rows.length ? <div className="scroll"><table><thead><tr>{["Unit", "Item", "Decision", "Who · role", "When"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((row, j) => <tr key={j}><td>{row.unit}</td><td>{row.item}</td><td>{row.kind}</td><td>{row.by}{row.as && ` · ${row.as}`}</td><td>{row.on}</td></tr>)}</tbody></table></div> : <p>No readable item decisions recorded for this release.</p>}</details>;
      })}</>}
  </section>;
}
export default function Onboard({ api, resource, me }) {
  const ob = resource.data || {}, [message, setMessage] = useState(null), [busy, setBusy] = useState(false), pending = useRef(false);
  const [started, setStarted] = useState(null);
  const candidate = ob.candidate, proposal = ob.proposal;
  const lines = changeLines(ob.changes, ob.change_lines);
  const unitHome = (ob.units || []).find(u => ["release", "rollback"].includes(ob.job?.step) && ob.job?.state === "done" ? u.release?.model_hash === ob.job.hash : [4, 5].includes(unitPlan(u, ob).at));
  const jobHome = ob.job?.step === "improve" ? proposal || ob.can?.improve ? "proposal" : "bar" : unitHome ? unitHome.unit : proposal ? "proposal" : "bar";
  const displayMessage = started && started === ob.job?.id && ["done", "failed"].includes(ob.job.state) ? "" : message;
  async function decide(action, plan = {}) {
    if (pending.current || !(action === "rollback" ? ob.can?.rollback : ob.can?.[action])) return;
    pending.current = true; setBusy(true); setStarted(null);
    const direct = ["accept", "certify", "rollback"].includes(action);
    const body = action === "rollback" ? {} : direct ? { ...(plan.proposal ? { proposal: plan.proposal } : { unit: plan.unit }), items: "all" } : { step: { run: "checks", sign_off: "sign-off", release: "release", improve: "improve" }[action], ...(plan.hash ? { hash: plan.hash } : {}) };
    const result = await api(direct ? `/onboard/${action}` : "/onboard/run", body);
    const text = result.error || (direct ? result.said || (action === "rollback" ? "Rollback started." : "Done.") : { run: "Checks started.", sign_off: "Sign-off started.", release: "Release started.", improve: "Improvement job started." }[action]);
    setMessage({ text, tone: result.error ? result.status === 409 ? "warn" : "bad" : /has no rows|needs? (its )?rows/i.test(text) ? "warn" : "ok" });
    if (!result.error && result.job?.id) setStarted(result.job.id);
    await resource.refresh(); pending.current = false; setBusy(false);
  }
  const planProps = { ob, decide, busy };
  const pplan = proposal && proposalPlan(proposal, ob);
  const pRows = proposal ? [
    ...(proposal.learnable_table?.length ? proposal.learnable_table.map(row => ({ ...row, kind: "learnable" })) : Object.entries(proposal.ab?.learnable?.sets || {}).map(([set, row]) => ({ set, before_passed: row.before, after_passed: row.after, of: row.of, kind: "learnable" }))),
    ...(proposal.heldout_table?.length ? proposal.heldout_table.map(row => ({ ...row, kind: "held-out" })) : Object.entries(proposal.ab?.heldout?.sets || {}).map(([set, row]) => ({ set, before_passed: row.before, after_passed: row.after, of: row.of, fell: row.fell, kind: "held-out" }))),
  ] : [];
  return <section id="onboard" data-drawn={resource.data && !resource.loading ? me.id : undefined} aria-busy={resource.loading || undefined}><div className="screen-heading"><div><h1>Onboard</h1><p className="intro">Units, candidate checks and release decisions.</p></div><a id="ob-guide" href="/api/context/onboarding-guide" target="_blank" rel="noopener">Role guides</a></div>
    <div id="ob-msg" role="status" aria-live="polite">{displayMessage ? <><span className={`badge ${displayMessage.tone}`}>{{ ok: "done", warn: "needs attention", bad: "refused" }[displayMessage.tone]}</span>{obPlain(displayMessage.text)}</> : ob.error || (!ob.writable ? ob.why : "")}</div>
    <p className="small muted">Start work with <code>kge onboard</code> and the agent skills: land-and-draft, build-and-gate and improve.</p>
    <Panel id="ob-candidate"><h2>Candidate</h2>{candidate ? <dl className="candidate-state"><dt>Candidate</dt><dd><code>{candidate.hash}</code> · {candidate.loaded ? "Loaded" : "Not loaded"}{candidate.released && " · Released"}</dd><dt>Live release</dt><dd><code>{ob.serving || "none"}</code></dd><dt>Checks and release gate</dt><dd>{candidate.gate ? candidate.gate.passed ? "Passed" : "Failed" : "Not checked"}{candidate.gate?.run_id && ` · ${candidate.gate.run_id}`}</dd><dt>Sign-off</dt><dd>{candidate.signed_off ? `${candidate.signed_off.principal} · ${candidate.signed_off.at}` : "Not signed off"}</dd></dl> : <p>No candidate yet.</p>}</Panel>
    <div id="ob-changes" aria-live="polite">{lines.length > 0 && <Panel><h2>Candidate changes — review before sign-off</h2><ul>{lines.map((line, i) => <li key={i}>{line.startsWith("Declarations to add\n") ? <pre>{line}</pre> : line}</li>)}</ul></Panel>}</div>
    <section id="ob-status"><h2>Units and status</h2>{!(ob.units || []).length && <p>{resource.loading ? "Loading units…" : "No units yet."}</p>}{(ob.units || []).map(u => {
      const plan = unitPlan(u, ob), done = unitDone(u);
      return <Panel key={u.unit} className="unit-card" data-unit={u.unit}><div className="unit-head"><h3>{u.label || u.unit}</h3><span className="badge">{plan.at < 0 ? "Live" : STAGES[Math.max(0, plan.at - 1)]}</span></div>
        <ol className="stagebar" aria-label={`Onboarding stages for ${u.label || u.unit}`}>{STAGES.map((stage, i) => <li key={stage} className={done[i] ? "done" : i === plan.at ? "next" : ""} aria-label={`${stage}: ${done[i] ? "complete" : i === plan.at ? "next" : "not complete"}`}>{stage}</li>)}</ol>
        <p className="status">{obPlain(u.status)}</p><Next plan={plan} {...planProps} job={jobHome === u.unit ? ob.job : null} />
        <p className="small muted">Golden questions · {u.golden?.client || 0} from client · {u.golden?.drafted || 0} drafted</p>
        <details><summary>Progress by sub-graph</summary>{u.progress && <div className="scroll"><table className="progress" aria-label="Progress by sub-graph"><thead><tr><th>Sub-graph</th><th>Items</th><th>Accepted</th><th>Certified</th></tr></thead><tbody>{[["physical", "Physical"], ["glossary", "Glossary"], ["metrics", "Metrics"], ["ontology", "Industry ontology"], ["documents", "Documents"], ["governance", "Governance"]].map(([key, label]) => <tr key={key}><td>{label}</td><td>{u.progress[key]?.items || 0}</td><td>{u.progress[key]?.accepted || 0}</td><td>{u.progress[key]?.certified || 0}</td></tr>)}</tbody></table></div>}</details>
        {u.update?.changes && <div className="what-changed"><b>What changed in v{u.update.version}</b>{["changed", "added", "removed"].some(kind => u.update.changes[kind]?.length) ? <ul>{["changed", "added", "removed"].flatMap(kind => (u.update.changes[kind] || []).map(id => <li key={`${kind}-${id}`}>{kind} · {id}: {(u.update.changes.details?.[id] || []).join("; ")}</li>))}</ul> : <p>Nothing to review: only rows changed.</p>}</div>}
        <div><b>Open requests</b>{u.requests?.length ? <ul>{u.requests.map((r, i) => <li key={i}>{obPlain(r.ask)} {r.source}</li>)}</ul> : <p>None.</p>}</div>
        <details className="items" open={plan.mine && ["accept", "certify"].includes(plan.action) || undefined}><summary>Items to review ({u.items?.length || 0})</summary><Items items={u.items} /></details>
        {ob.can?.run && plan.action !== "run" && u.items?.length > 0 && <details className="more"><summary>More actions</summary><Action {...planProps} plan={actionPlan("run", u, ob, plan.at)} primary={false} /></details>}
        <details><summary aria-label={`Show history for ${u.label || u.unit}`}>History</summary><ol>{u.history?.length ? u.history.map((h, i) => <li key={i}><b>{h.step}</b> · {h.by} · {h.at} · v{h.version}<p>{JSON.stringify(h.detail ?? {})}</p></li>) : <li>No history yet.</li>}</ol></details>
        <details><summary aria-label={`Show documents for ${u.label || u.unit}`}>Documents</summary><ul>{u.documents?.length ? u.documents.map((d, i) => <li key={i}><b>{d.title}</b> · {d.path === "m365" ? "certified by M365" : d.path === "graded" ? "graded here" : "not used"}{d.tier && ` · ${d.tier} tier`}</li>) : <li>No reference documents supplied.</li>}</ul></details>
      </Panel>;
    })}</section>
    <Panel id="ob-proposal-card" hidden={!proposal && (!ob.can?.improve || !ob.units?.length)}><div id="ob-proposal">{proposal ? <><h2>Improvement proposal{(proposal.version?.from || proposal.base_version) && (proposal.version?.to || proposal.next_version) && ` · pack ${proposal.version?.from || proposal.base_version} → ${proposal.version?.to || proposal.next_version}`}</h2>{(proposal.heldout_fell || pRows.some(r => r.kind === "held-out" && (r.fell || r.after_passed < r.before_passed))) && <span className="badge warn">held-out fell</span>}
      <ol className="stagebar five">{["Proposed", "Accepted", "Certified", "Checks", "Live"].map((name, i) => <li key={name} className={pplan.done[i] ? "done" : i === pplan.at ? "next" : ""}>{name}</li>)}</ol><Next plan={pplan} {...planProps} job={jobHome === "proposal" ? ob.job : null} />
      <div className="items"><h3>Items to review ({proposal.items?.length || 0})</h3><Items items={proposal.items} /></div>{pRows.length > 0 && <div className="scroll"><table><thead><tr><th>Set</th><th>Kind</th><th>Before</th><th>After</th></tr></thead><tbody>{pRows.map((r, i) => <tr key={i}><td>{r.set}</td><td>{r.kind}{r.kind === "held-out" && ` (${proposal.ab?.heldout?.note || "judge only, never read by the job"})`}</td><td>{r.before_passed}/{r.of}</td><td>{r.after_passed}/{r.of}</td></tr>)}</tbody></table></div>}{reportUrl(proposal.report) && <a href={reportUrl(proposal.report)} target="_blank" rel="noopener">candidate A/B report</a>}</> : <><p>No proposal yet. Improve: Run proposes changes from the questions the live units miss.</p><Action {...planProps} plan={actionPlan("improve", {}, ob, 0)} primary={(ob.units || []).every(u => u.live)} />{jobHome === "proposal" && <Job job={ob.job} ob={ob} />}</>}</div></Panel>
    <div id="ob-work"><div id="ob-jobbar">{jobHome === "bar" && <Job job={ob.job} ob={ob} />}</div><div id="ob-notes">{[...((ob.requests || []).map(r => ({ ...r, audience: "request to the platform team" }))), ...((ob.notes || []).map(r => ({ ...r, audience: "note to the steward" })))].map((r, i) => <p key={i}><span className="badge">{r.audience}</span> <b>{r.question_id || r.id}</b> · {r.category || r.diagnosis}: {r.summary || r.why || r.question} ({r.proposer || "rules"})</p>)}</div>
      <div id="ob-eval-post" role="status" hidden={!ob.eval_post}>{ob.eval_post?.message}{ob.eval_post && <details><summary>Automatic release evaluation</summary><pre>{JSON.stringify(ob.eval_post, null, 2)}</pre></details>}</div>
      <div id="ob-eval-pre">{ob.eval_pre && <details><summary>Pre-release margin report · {ob.eval_pre.passed ? "passed" : "failed"}</summary><pre>{ob.eval_pre.report}</pre></details>}</div><ReleaseHistory {...planProps} me={me} />
    </div>
  </section>;
}
