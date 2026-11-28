import React from "react";

/* #638: an explained path (chatTools.pathsOf), as KGE returns it on explore_question and map_term. The compact card
   shows each path's term, its measure and its hops in KGE's order (source → target), a one-to-many hop marked with its
   cardinality and KGE's warning; the side panel's Graph path tab shows the whole path: every hop with its kind,
   cardinality, type and window, and the dimensions, conditions and time rules. Every word is KGE's or a plain label:
   the page names nothing of its own, computes nothing and orders nothing. What is the engineer's alone (a measure's
   expression, a condition's predicate, a hop's declared options) is already left out of a business user's path by
   pathsOf. Every field is React text. */

const spaced = (value) => String(value ?? "").replace(/_/g, " ");

/** The measure in a line: its name, then its aggregation, grain and currency as KGE gives them. */
export function measureWords(measure) {
  if (!measure) return "";
  const how = [measure.aggregation, measure.grain, measure.currency].filter(Boolean).map(spaced).join(", ");
  return [measure.name, how ? `(${how})` : ""].filter(Boolean).join(" ");
}

/** One hop's ends, as KGE names them. */
const ends = (hop) => `${hop.source || "?"} → ${hop.target || "?"}`;

/** The compact card's part: each path's term, measure and ordered hops, a one-to-many hop marked. Nothing where the
 *  reply carries no path. */
export function PathSummary({ paths }) {
  if (!Array.isArray(paths) || !paths.length) return null;
  return (
    <section className="cerebro-path" aria-label="Explained path">
      {paths.map((p, i) => (
        <div key={i} className="cerebro-path-one">
          <p className="cerebro-path-head">
            <span className="cerebro-path-term">{p.term}</span>
            {p.measure ? <span className="cerebro-path-measure"> · {measureWords(p.measure)}</span> : null}
          </p>
          {p.hops?.length ? (
            <ol className="cerebro-path-hops" aria-label={`Hops of ${p.term || "the path"}`}>
              {p.hops.map((h, j) => (
                <li key={j}>
                  {ends(h)}
                  {h.warning ? <span className="badge warn cerebro-path-warn" title={h.warning}>{h.cardinality || "warning"}</span> : null}
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ))}
    </section>
  );
}

/** A definition list of the facts given, each its label and value; a fact KGE left out is not shown. */
function Facts({ rows, label }) {
  const shown = rows.filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!shown.length) return null;
  return (
    <dl className="cerebro-path-facts" aria-label={label}>
      {shown.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}
    </dl>
  );
}

const yesNo = (value) => (value === true ? "yes" : value === false ? "no" : undefined);

/** One path in full: its term and context, its measure, every hop, the dimensions, conditions, time rules and warnings. */
function ExplainedPath({ path: p, n }) {
  const m = p.measure;
  return (
    <section className="cerebro-path-full" aria-label={`Path ${n}: ${p.term || ""}`}>
      <h3 className="cerebro-path-title">{p.term}</h3>
      <Facts label="The term" rows={[["Basis", p.basis], ["Definition", p.definition], ["Context", p.context], ["Context id", p.context_id], ["Part of", p.part_of], ["Unit", p.unit], ["Fact", p.fact]]} />
      {m ? (
        <section aria-label="Measure">
          <h4>Measure</h4>
          <Facts label="The measure" rows={[["Name", m.name], ["Aggregation", spaced(m.aggregation)], ["Grain", spaced(m.grain)], ["Currency", m.currency], ["Snapshot", yesNo(m.snapshot)]]} />
          {m.expression ? <pre className="cerebro-path-code" aria-label="Expression">{m.expression}</pre> : null}
        </section>
      ) : null}
      {p.hops?.length ? (
        <section aria-label="Hops">
          <h4>Hops</h4>
          <ol className="cerebro-path-list">
            {p.hops.map((h, i) => (
              <li key={i} className="cerebro-path-hop" data-warning={h.warning ? "true" : undefined}>
                <p className="cerebro-path-ends">{ends(h)}</p>
                <Facts label={`Hop ${i + 1}`} rows={[["Kind", spaced(h.kind)], ["Cardinality", h.cardinality], ["Type", h.type], ["Window", h.window], ["Order", h.order], ["Use", h.use], ["Id", h.id], ["Declared", h.declared?.join(", ")]]} />
                {h.said ? <p className="cerebro-path-said">{h.said}</p> : null}
                {h.condition ? <pre className="cerebro-path-code" aria-label="Join condition">{h.condition}</pre> : null}
                {h.warning ? <p className="cerebro-path-warning" role="note"><span className="badge warn">{h.cardinality || "warning"}</span> {h.warning}</p> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
      {p.dimensions?.length ? (
        <section aria-label="Dimensions">
          <h4>Dimensions</h4>
          <ul className="cerebro-path-list">
            {p.dimensions.map((d, i) => (
              <li key={i}>
                <p><b>{d.name}</b> <span className="cerebro-kind">{spaced(d.kind)}</span>{d.via ? <span className="cerebro-kind">via {d.via}</span> : null}</p>
                {d.said ? <p className="cerebro-path-said">{d.said}</p> : null}
                {d.levels?.length ? <Levels levels={d.levels} name={d.name} /> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {p.conditions?.length ? (
        <section aria-label="Conditions">
          <h4>Conditions</h4>
          <ul className="cerebro-path-list">
            {p.conditions.map((c, i) => (
              <li key={i}>
                <p><b>{c.label}</b> <span className="cerebro-kind">{spaced(c.kind)}</span>{c.on ? <span className="cerebro-kind">on {c.on}</span> : null}{c.id ? <span className="cerebro-kind">{c.id}</span> : null}</p>
                {c.predicate ? <pre className="cerebro-path-code" aria-label="Predicate">{c.predicate}</pre> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {p.time_rules?.length ? (
        <section aria-label="Time rules">
          <h4>Time rules</h4>
          <ul className="cerebro-path-list">
            {p.time_rules.map((t, i) => (
              <li key={i}><span className="cerebro-kind cerebro-kind-lead">{spaced(t.kind)}</span>{t.on ? ` on ${t.on}` : ""}{t.said ? `: ${t.said}` : ""}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {p.warnings?.length ? (
        <section aria-label="Warnings">
          <h4>Warnings</h4>
          <ul className="cerebro-path-list">{p.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
        </section>
      ) : null}
    </section>
  );
}

const LEVEL_COLUMNS = [["name", "Level"], ["code", "Code"], ["column", "Column"], ["broader", "Broader"], ["of", "Of"]];
/** A dimension's levels: a column for each field any level has. */
function Levels({ levels, name }) {
  const columns = LEVEL_COLUMNS.filter(([key]) => levels.some((l) => l[key]));
  return (
    <div className="scroll">
      <table aria-label={`Levels of ${name || "the dimension"}`}>
        <thead><tr>{columns.map(([key, label]) => <th key={key}>{label}</th>)}</tr></thead>
        <tbody>{levels.map((l, i) => <tr key={i}>{columns.map(([key]) => <td key={key}>{l[key] || ""}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

/** The side panel's Graph path tab for explore_question and map_term: every path in full, in KGE's order. */
export function ExplainedPaths({ paths }) {
  if (!Array.isArray(paths) || !paths.length) return null;
  return <div className="cerebro-paths">{paths.map((p, i) => <ExplainedPath key={i} path={p} n={i + 1} />)}</div>;
}
