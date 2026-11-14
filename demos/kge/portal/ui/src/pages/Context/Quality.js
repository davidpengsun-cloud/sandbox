import React, { useRef, useState } from "react";
import { Button, Panel } from "../../components/shared/Controls";
import { reportUrl } from "./contextState";

export default function Quality({ api, me, units, golden, ab, versions, onboard }) {
  const [result, setResult] = useState(null), [busy, setBusy] = useState(false), pending = useRef(false);
  const kinds = { within: "Within a unit", cross: "Across units", shelf: "Shelf", abstain: "Abstain" };
  const last = ab?.runs?.[0], gate = onboard?.candidate?.gate;
  async function evaluate() {
    if (pending.current) return;
    pending.current = true; setBusy(true);
    let r = await api("/eval", null);
    while (r && !r.error && (r.job || r.state === "running")) {
      await new Promise(resolve => setTimeout(resolve, 100));
      r = await api("/eval");
    }
    setResult(r);
    pending.current = false; setBusy(false);
  }
  return <section id="control"><h1>Quality</h1><p className="intro">The golden set, the A/B and the release gate.</p>
    <Panel><h2>Golden set results</h2><div className="row"><Button id="runeval" disabled={busy} onClick={evaluate}>{busy ? "Running…" : "Run the golden set"}</Button><span id="evalnote">{busy ? "Running the golden set on the three presets: this takes a minute." : result?.error || (result ? "Done." : `${golden?.sets?.find(s => s.name === "golden")?.questions.length || 0} questions of the golden set on the three presets, scored apart by kind.`)}</span></div>
      <progress id="evalprogress" aria-label="Running the golden set" hidden={!busy} /><div id="evalout" role="status">{result && !result.error && <><p>Scored as {me.id} ({me.reads?.length && me.reads.length === units.length ? "reads every unit" : `reads ${me.reads?.join(", ") || "no unit"}`}).{me.reads?.length < units.length && ` The golden answers assume a principal who reads every unit: a question about a unit ${me.id} cannot read is refused, and the refusal counts as a miss.`}</p><div className="scroll"><table><thead><tr><th>Arm</th>{Object.values(kinds).map(kind => <th key={kind}>{kind}</th>)}</tr></thead><tbody>{Object.entries(result).map(([arm, value]) => <tr key={arm}><td>{arm}</td>{Object.keys(kinds).map(kind => <td key={kind}>{value.by_kind?.[kind]?.passed ?? 0}/{value.by_kind?.[kind]?.of ?? 0}</td>)}</tr>)}</tbody></table></div><p>Schema: names, types and declared keys only. Catalog: plus glossary, descriptions and certified mappings. Graph: plus join paths, measure metadata, the no-join facts and the shelves. Within a unit the catalog arm is close; across units only the graph arm refuses the joins that do not exist.</p></>}</div>
    </Panel><Panel id="ab"><h2>The A/B harness</h2>{ab?.error ? <p role="alert">{ab.error}</p> : last ? <p>Latest run <code>{last.run_id}</code>, asked as {last.principal}, {last.repeats} repeat{last.repeats === 1 ? "" : "s"}: {(last.arms || []).map(x => `${x.arm} (${x.mode})`).join(", ")}. {reportUrl(last.report) && <a href={reportUrl(last.report)} target="_blank" rel="noopener">Open its report</a>}{last.arms?.some(x => x.mode === "mock") && " Mock arms prove the seam, not a model's quality."}</p> : <p>No A/B results yet.</p>}</Panel>
    <Panel><h2>Release gate</h2><p>{gate ? gate.passed ? "Passed" : "Failed" : "Not checked"}{gate?.run_id && ` · ${gate.run_id}`}</p>{gate && <details><summary>Checks</summary><pre>{JSON.stringify(gate, null, 2)}</pre></details>}{onboard?.eval_pre && <details><summary>Pre-release margin report</summary><pre>{onboard.eval_pre.report}</pre></details>}{versions?.eval_post && <p>{versions.eval_post.message}</p>}</Panel>
    <Panel><h2>Baselines</h2>{ab?.baseline ? <details><summary>Release baseline</summary><pre>{JSON.stringify(ab.baseline, null, 2)}</pre></details> : <p>No release baseline yet.</p>}</Panel>
  </section>;
}
