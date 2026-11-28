import React, { useEffect, useMemo, useRef, useState } from "react";
import { Button, Panel } from "../../components/shared/Controls";

const dateOnly = (date) => date.toISOString().slice(0, 10);
const initialRange = () => {
  const end = new Date();
  return { start: dateOnly(new Date(end.getTime() - 6 * 86400000)), end: dateOnly(end) };
};
const query = (fields) =>
  new URLSearchParams(Object.entries(fields).filter(([, value]) => value != null && value !== "")).toString();
const shown = (value) =>
  value == null
    ? "Not reported"
    : typeof value === "number"
      ? value.toLocaleString(undefined, { maximumSignificantDigits: 12 })
      : String(value);
const when = (value) => {
  const d = new Date(value);
  return value && !Number.isNaN(d.getTime())
    ? d
        .toISOString()
        .replace("T", " ")
        .replace(/\.\d{3}Z$/, " UTC")
    : "Not reported";
};
const percent = (value) => (value == null ? "Not reported" : `${shown(value * 100)}%`);
const fields = [
  ["Sessions", "sessions"],
  ["Turns", "turns"],
  ["OK", "ok"],
  ["Failed", "failed"],
  ["Latency p50 (ms)", "p50_ms"],
  ["Latency p95 (ms)", "p95_ms"],
  ["Maximum latency (ms)", "max_ms"],
  ["Model time (ms)", "model_ms"],
  ["Warehouse time (ms)", "warehouse_ms"],
  ["Model calls", "model_calls"],
  ["Input tokens", "input_tokens"],
  ["Output tokens", "output_tokens"],
  ["Thinking tokens", "thinking_tokens"],
  ["Known cost (USD)", "cost_usd"],
  ["Warehouse reads", "warehouse_reads"],
];
const tokenFields = [
  ["input_tokens", "input tokens"],
  ["output_tokens", "output tokens"],
  ["thinking_tokens", "thinking tokens"],
  ["cost_usd", "cost"],
];
function Reporting({ row, calls = row?.calls }) {
  if (calls == null) return null;
  const counts = tokenFields.map(([key]) => row?.[`${key}_reported`]);
  if (counts.every((count) => count != null && count === counts[0]))
    return counts[0] < calls ? (
      <small>
        {counts[0]} of {calls} calls reported tokens
      </small>
    ) : null;
  return (
    <>
      {tokenFields.map(
        ([key, label], i) =>
          counts[i] != null &&
          counts[i] < calls && (
            <small key={key}>
              {counts[i]} of {calls} calls reported {label}
            </small>
          ),
      )}
    </>
  );
}
function LoadState({ result, noun }) {
  if (result === null) return <p role="status">Loading {noun}…</p>;
  return result?.error ? <p role="alert">{result.error}</p> : null;
}
function modelName(span) {
  const a = span.attributes || {};
  return a["gen_ai.response.model"] || a["gen_ai.request.model"] || "Model";
}
function nodeText(span, label) {
  const a = span.attributes || {};
  if (span.name === "kge.model" || span.name === "kge.model.attempt")
    return `${label || (span.name === "kge.model.attempt" ? "Model attempt" : "Model call")} · ${modelName(span)} · ${shown(a["kge.stage"])} · ${shown(a["gen_ai.usage.input_tokens"])} / ${shown(a["gen_ai.usage.output_tokens"])} / ${shown(a["gen_ai.usage.reasoning_tokens"])} tokens`;
  return span.name || "Step";
}
function SpanTree({ trace }) {
  const spans = Array.isArray(trace?.spans) ? trace.spans : [],
    ids = new Set(spans.map((s) => s.id));
  const logical = spans.filter((s) => s.name === "kge.model"),
    attempts = spans.filter((s) => s.name === "kge.model.attempt");
  const equivalent = (a, b) => {
    const left = modelName(a),
      right = modelName(b),
      begin = Date.parse(a.started_at),
      end = Date.parse(a.ended_at),
      first = Date.parse(b.started_at),
      last = Date.parse(b.ended_at);
    const call = a.attributes?.["kge.call"] || a.attributes?.["kge.stage"],
      stage = b.attributes?.["kge.stage"];
    return (
      call &&
      stage &&
      call === stage &&
      left !== "Model" &&
      right !== "Model" &&
      left.replace(/^(?:vertex-gemini|vertex-anthropic|gemini|openai|anthropic|ollama):/, "") ===
        right.replace(/^(?:vertex-gemini|vertex-anthropic|gemini|openai|anthropic|ollama):/, "") &&
      [begin, end, first, last].every(Number.isFinite) &&
      first >= begin - 2 &&
      last <= end + 2
    );
  };
  const grouped = new Map();
  const byId = new Map(spans.map((span) => [span.id, span]));
  for (const attempt of attempts) {
    let parent = byId.get(attempt.parent_id),
      explicit = null;
    const seen = new Set();
    while (parent && !seen.has(parent.id)) {
      if (parent.name === "kge.model") {
        explicit = parent;
        break;
      }
      seen.add(parent.id);
      parent = byId.get(parent.parent_id);
    }
    if (explicit) continue;
    const candidates = logical.filter((call) => equivalent(call, attempt));
    if (candidates.length === 1) grouped.set(attempt.id, candidates[0].id);
  }
  const children = (parent) =>
    spans
      .filter(
        (s) =>
          (grouped.get(s.id) || s.parent_id || null) === parent ||
          (parent === null && s.parent_id && !ids.has(s.parent_id) && !grouped.has(s.id)),
      )
      .sort((a, b) => String(a.started_at || "").localeCompare(String(b.started_at || "")));
  const start = Date.parse(trace.started_at),
    total = trace.duration_ms;
  const bar = (span) => {
    const offset = Date.parse(span.started_at) - start,
      duration = span.duration_ms;
    if (!Number.isFinite(offset) || !Number.isFinite(duration) || !(total > 0)) return null;
    return (
      <span
        className="telemetry-timing"
        aria-label={`Starts ${Math.max(0, offset)} ms into turn, lasts ${duration} ms`}
      >
        <span
          style={{
            marginLeft: `${Math.min(100, Math.max(0, (offset / total) * 100))}%`,
            width: `${Math.min(100, Math.max(0, (duration / total) * 100))}%`,
          }}
        />
      </span>
    );
  };
  const render = (parent, seen = new Set()) => {
    if (parent && seen.has(parent)) return null;
    const nextSeen = new Set(seen);
    if (parent) nextSeen.add(parent);
    const list = children(parent),
      groups = [];
    for (const span of list) {
      const last = groups[groups.length - 1];
      if (span.name === "kge.warehouse" && last?.warehouse) last.spans.push(span);
      else groups.push({ warehouse: span.name === "kge.warehouse", spans: [span] });
    }
    return groups.length ? (
      <ul>
        {groups.map((group) => {
          const span = group.spans[0];
          if (group.warehouse && group.spans.length > 1)
            return (
              <li key={span.id}>
                <details>
                  <summary>
                    {group.spans.length} warehouse reads ·{" "}
                    {shown(
                      group.spans.every((s) => s.duration_ms != null)
                        ? group.spans.reduce((n, s) => n + s.duration_ms, 0)
                        : null,
                    )}{" "}
                    ms
                  </summary>
                  <ul>
                    {group.spans.map((s) => (
                      <li key={s.id}>
                        {nodeText(s)} · {shown(s.duration_ms)} ms {bar(s)}
                        {render(s.id, nextSeen)}
                      </li>
                    ))}
                  </ul>
                </details>
              </li>
            );
          const nested = children(span.id),
            attempts = nested.filter((s) => s.name === "kge.model.attempt"),
            isModel = span.name === "kge.model";
          if (isModel && attempts.length === 1)
            return (
              <li key={span.id}>
                {nodeText(attempts[0], "Model call")} · {shown(span.duration_ms)} ms {bar(span)}
                {render(attempts[0].id, nextSeen)}
                {nested
                  .filter((s) => s.name !== "kge.model.attempt")
                  .map((s) => (
                    <div key={s.id}>
                      {nodeText(s)} · {shown(s.duration_ms)} ms {bar(s)}
                      {render(s.id, nextSeen)}
                    </div>
                  ))}
              </li>
            );
          if (isModel && attempts.length > 1)
            return (
              <li key={span.id}>
                <details>
                  <summary>
                    {nodeText(span)} · {shown(span.duration_ms)} ms · {attempts.length} attempts {bar(span)}
                  </summary>
                  {render(span.id, nextSeen)}
                </details>
              </li>
            );
          return (
            <li key={span.id}>
              {nested.length ? (
                <details open>
                  <summary>
                    {nodeText(span)} · {shown(span.duration_ms)} ms {bar(span)}
                  </summary>
                  {render(span.id, nextSeen)}
                </details>
              ) : (
                <>
                  {nodeText(span)} · {shown(span.duration_ms)} ms {bar(span)}
                </>
              )}
            </li>
          );
        })}
      </ul>
    ) : null;
  };
  return render(null) || <p>No recorded steps.</p>;
}
function Drawer({ result, traceId, onClose, onNavigate, opener }) {
  const panel = useRef(null),
    trace = result?.trace;
  useEffect(() => {
    panel.current?.focus();
    return () => opener.current?.focus();
  }, [opener]);
  return (
    <aside
      className="telemetry-drawer"
      role="dialog"
      aria-modal="false"
      aria-label="Turn details"
      tabIndex={-1}
      ref={panel}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <div className="row">
        <h2>Turn details</h2>
        <Button onClick={onClose}>Close</Button>
      </div>
      <p>Trace {traceId}</p>
      <LoadState result={result} noun="trace" />
      {trace && (
        <>
          <p>
            {shown(trace.agent)} · Session {shown(trace.session_id)} · Turn {shown(trace.turn_index)} of{" "}
            {shown(trace.session_trace_count)} within selected dates
          </p>
          <p>
            {shown(trace.duration_ms)} ms ·{" "}
            <span className={`badge ${trace.outcome === "failed" ? "warn" : ""}`}>
              {shown(trace.outcome)}
            </span>{" "}
            · {shown(trace.model_calls)} model calls · {shown(trace.input_tokens)} /{" "}
            {shown(trace.output_tokens)} / {shown(trace.thinking_tokens)} tokens ·{" "}
            {shown(trace.warehouse_reads)} warehouse reads
          </p>
          <div className="row">
            <Button disabled={!trace.previous_trace_id} onClick={() => onNavigate(trace.previous_trace_id)}>
              Previous turn
            </Button>
            <Button disabled={!trace.next_trace_id} onClick={() => onNavigate(trace.next_trace_id)}>
              Next turn
            </Button>
          </div>
          <h3>Recorded steps</h3>
          <SpanTree trace={trace} />
        </>
      )}
    </aside>
  );
}
export default function Telemetry({ api }) {
  const [range, setRange] = useState(initialRange),
    [applied, setApplied] = useState(initialRange);
  const [agent, setAgent] = useState(""),
    [session, setSession] = useState(""),
    [outcome, setOutcome] = useState("");
  const [agents, setAgents] = useState(null),
    [sessions, setSessions] = useState(null),
    [overview, setOverview] = useState(null),
    [traces, setTraces] = useState(null);
  const [day, setDay] = useState(null),
    [cursor, setCursor] = useState(""),
    [traceId, setTraceId] = useState(""),
    [trace, setTrace] = useState(null);
  const opener = useRef(null),
    valid = range.start && range.end && range.start <= range.end;
  const period = useMemo(
    () => ({
      start: `${applied.start}T00:00:00Z`,
      end: new Date(Date.parse(`${applied.end}T00:00:00Z`) + 86400000).toISOString(),
    }),
    [applied],
  );
  useEffect(() => {
    let live = true;
    setAgents(null);
    api(`/telemetry/agents?${query(period)}`).then((x) => {
      if (live) setAgents(x);
    });
    return () => {
      live = false;
    };
  }, [api, period]);
  useEffect(() => {
    let live = true;
    setSessions(null);
    api(`/telemetry/sessions?${query({ ...period, agent })}`).then((x) => {
      if (live) setSessions(x);
    });
    return () => {
      live = false;
    };
  }, [api, period, agent]);
  useEffect(() => {
    let live = true;
    setOverview(null);
    api(`/telemetry/overview?${query({ ...period, agent, session, outcome })}`).then((x) => {
      if (live) setOverview(x);
    });
    return () => {
      live = false;
    };
  }, [api, period, agent, session, outcome]);
  useEffect(() => {
    let live = true;
    setTraces(null);
    api(
      `/telemetry/traces?${query({ ...period, agent, session, outcome, day: day?.date, day_agent: day?.agent, cursor })}`,
    ).then((x) => {
      if (live) setTraces(x);
    });
    return () => {
      live = false;
    };
  }, [api, period, agent, session, outcome, day, cursor]);
  useEffect(() => {
    let live = true;
    setTrace(null);
    if (traceId)
      api(`/telemetry/traces/${encodeURIComponent(traceId)}?${query(period)}`).then((x) => {
        if (live) setTrace(x);
      });
    return () => {
      live = false;
    };
  }, [api, traceId, period]);
  const reset = (fn) => (value) => {
    fn(value);
    setCursor("");
    setDay(null);
    setTraceId("");
  };
  const summary = overview?.summary,
    daily = overview?.daily || [],
    list = traces?.traces || [];
  return (
    <section className="telemetry-workspace">
      <h1>Telemetry</h1>
      <p className="intro">
        Usage and recorded turns for the selected UTC dates. These figures are not billing records.
      </p>
      <Panel>
        <div className="row">
          <label>
            From{" "}
            <input
              type="date"
              value={range.start}
              onChange={(e) => setRange({ ...range, start: e.target.value })}
            />
          </label>
          <label>
            Through{" "}
            <input
              type="date"
              value={range.end}
              onChange={(e) => setRange({ ...range, end: e.target.value })}
            />
          </label>
          <Button
            disabled={!valid}
            onClick={() => {
              setApplied({ ...range });
              setAgent("");
              setSession("");
              setCursor("");
              setDay(null);
              setTraceId("");
            }}
          >
            Apply dates
          </Button>
          <label>
            Agent{" "}
            <select
              value={agent}
              onChange={(e) => {
                reset(setAgent)(e.target.value);
                setSession("");
              }}
            >
              <option value="">All agents</option>
              {(agents?.agents || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Session{" "}
            <select value={session} onChange={(e) => reset(setSession)(e.target.value)}>
              <option value="">All sessions</option>
              {(sessions?.sessions || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            Outcome{" "}
            <select value={outcome} onChange={(e) => reset(setOutcome)(e.target.value)}>
              <option value="">All outcomes</option>
              <option value="ok">OK</option>
              <option value="failed">Failed</option>
            </select>
          </label>
        </div>
        {!valid && <p role="alert">Choose a start date on or before the end date.</p>}
        {agents?.error && <p role="alert">{agents.error}</p>}
        {sessions?.error && <p role="alert">{sessions.error}</p>}
      </Panel>
      <Panel>
        <h2>Usage overview</h2>
        <LoadState result={overview} noun="overview" />
        {summary && (
          <>
            <dl className="telemetry-summary">
              {fields.map(([label, key]) => (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>{shown(summary[key])}</dd>
                </div>
              ))}
              <div>
                <dt>Thinking share</dt>
                <dd>{percent(summary.thinking_share)}</dd>
              </div>
              <div>
                <dt>Successful turns with no model call</dt>
                <dd>{percent(summary.no_model_share)}</dd>
              </div>
            </dl>
            <Reporting row={summary} calls={summary.model_calls} />
          </>
        )}
        <h3>Daily breakdown</h3>
        {overview &&
          !overview.error &&
          (!daily.length ? (
            <p>No usage in this period.</p>
          ) : (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>UTC day</th>
                    <th>Agent</th>
                    <th>Sessions</th>
                    <th>Turns</th>
                    <th>Latency p50 / p95 / max (ms)</th>
                    <th>Errors</th>
                    <th>Models</th>
                  </tr>
                </thead>
                <tbody>
                  {daily.map((row) => (
                    <tr key={`${row.date}-${row.agent}`}>
                      <td>
                        <Button
                          onClick={() => {
                            setDay({ date: row.date, agent: row.agent });
                            setCursor("");
                          }}
                        >
                          {row.date}
                        </Button>
                      </td>
                      <td>
                        <Button
                          onClick={() => {
                            setDay({ date: row.date, agent: row.agent });
                            setCursor("");
                          }}
                        >
                          {row.agent}
                        </Button>
                      </td>
                      <td>{shown(row.sessions)}</td>
                      <td>
                        {shown(row.turns)}{" "}
                        {row.turns != null && (
                          <progress
                            aria-label={`${row.date} ${row.agent} turns`}
                            value={row.turns}
                            max={Math.max(1, ...daily.map((d) => d.turns || 0))}
                          />
                        )}
                      </td>
                      <td>
                        {shown(row.p50_ms)} / {shown(row.p95_ms)} / {shown(row.max_ms)}
                      </td>
                      <td>{shown(row.failed)}</td>
                      <td>
                        {(row.models || []).map((m, i) => (
                          <div key={`${m.model}-${i}`}>
                            <b>{shown(m.model)}</b>: {shown(m.calls)} calls · {shown(m.input_tokens)} /{" "}
                            {shown(m.output_tokens)} / {shown(m.thinking_tokens)} tokens · {shown(m.cost_usd)}{" "}
                            USD <Reporting row={m} />
                          </div>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
      </Panel>
      <div className="telemetry-columns">
        <Panel>
          <h2>Turns</h2>
          {day && (
            <p>
              Showing {day.date} · {day.agent}{" "}
              <Button
                onClick={() => {
                  setDay(null);
                  setCursor("");
                }}
              >
                Clear daily filter
              </Button>
            </p>
          )}
          <p>Turn numbers count the session within the selected dates.</p>
          <LoadState result={traces} noun="turns" />
          {traces &&
            !traces.error &&
            (!list.length ? (
              <p>No turns in this period.</p>
            ) : (
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Started (UTC)</th>
                      <th>Agent</th>
                      <th>Session and turn</th>
                      <th>Model calls</th>
                      <th>Tokens in / out / thinking</th>
                      <th>Warehouse reads</th>
                      <th>Duration</th>
                      <th>Outcome</th>
                      <th>Details</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((item) => (
                      <tr
                        key={item.id}
                        onClick={(e) => {
                          opener.current = e.currentTarget.querySelector("button[data-open-turn]");
                          setTraceId(item.id);
                        }}
                      >
                        <td>{when(item.started_at)}</td>
                        <td>
                          <Button
                            onClick={(e) => {
                              e.stopPropagation();
                              reset(setAgent)(item.agent);
                              setSession("");
                            }}
                          >
                            {shown(item.agent)}
                          </Button>
                        </td>
                        <td>
                          <Button
                            onClick={(e) => {
                              e.stopPropagation();
                              reset(setSession)(item.session_id);
                            }}
                          >
                            {shown(item.session_id)} · Turn {shown(item.turn_index)}/
                            {shown(item.session_trace_count)}
                          </Button>
                        </td>
                        <td>{shown(item.model_calls)}</td>
                        <td>
                          {shown(item.input_tokens)} / {shown(item.output_tokens)} /{" "}
                          {shown(item.thinking_tokens)}
                        </td>
                        <td>{shown(item.warehouse_reads)}</td>
                        <td>{shown(item.duration_ms)} ms</td>
                        <td>{shown(item.outcome)}</td>
                        <td>
                          <Button
                            data-open-turn
                            onClick={(e) => {
                              e.stopPropagation();
                              opener.current = e.currentTarget;
                              setTraceId(item.id);
                            }}
                          >
                            Open turn
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          {traces?.next_cursor && <Button onClick={() => setCursor(traces.next_cursor)}>Next page</Button>}
          {cursor && <Button onClick={() => setCursor("")}>First page</Button>}
        </Panel>
        {traceId && (
          <Drawer
            result={trace}
            traceId={traceId}
            onClose={() => setTraceId("")}
            onNavigate={setTraceId}
            opener={opener}
          />
        )}
      </div>
    </section>
  );
}
