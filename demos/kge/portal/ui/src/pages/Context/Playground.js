import React, { useEffect, useRef, useState } from "react";
import { Button } from "../../components/shared/Controls";
import WaitingStatus from "../../components/shared/WaitingStatus";
import Ask from "./Ask";
import Answer, { SqlPart, sqlOf } from "./Answer";
import AgentCall from "./AgentCall";
import { GraphPath, Plan, Trace } from "./BehindAnswer";
import { TabButtons } from "./ContextTabs";
import { mayMap } from "./contextState";
import { callTool, graphSliceOf, loadTools, replyText, sourcesOf, timeWords } from "./playgroundState";

const ALL = "all";
const ASK_ONLY = [{ setting: "ask", label: "Ask", tool: "ask" }];

function SourcesList({ items }) {
  return items.length ? <ul className="pg-list">{items.map((s, i) => <li key={i}><span>{s.title}</span>{[s.kind, s.unit, s.why].filter(Boolean).length > 0 && <span className="muted small"> · {[s.kind, s.unit, s.why].filter(Boolean).join(" · ")}</span>}</li>)}</ul> : <p>This reply names no sources.</p>;
}
function SliceList({ result }) {
  if (result.tool === "ask" && (result.value?.graph_path?.nodes?.length || result.value?.path?.nodes?.length)) return <GraphPath answer={result.value} />;
  const nodes = graphSliceOf(result.tool, result.value);
  return nodes.length ? <ul className="pg-list">{nodes.map((n, i) => <li key={i}><span className="badge">{n.kind}</span> {n.name}</li>)}</ul> : <p>This reply touched no part of the graph it names.</p>;
}
function ReplyView({ result, me }) {
  if (result.error) return <p role="alert">{result.error}</p>;
  if (result.tool === "ask") return <Answer answer={result.value} me={me} sqlShown={false} verdictsShown={false} choicesShown={false} />;
  const text = replyText(result.tool, result.value);
  return <>{text ? <p className="pg-reply">{text}</p> : <p className="muted">The reply holds no text; its sources and raw reply are below.</p>}
    {result.tool === "search" && <SourcesList items={sourcesOf("search", result.value)} />}</>;
}

/** #623 requirement 4: the right panel for every tool: reply, sources, graph slice, time, Ask's SQL, and the agent call. */
export function ToolPanel({ result, question, me, base }) {
  const engineer = mayMap(me);
  const ask = result?.tool === "ask" && result.value && !result.error;
  const names = ["reply", "sources", ...(ask ? ["sql"] : []), "graph", ...(ask && engineer ? ["plan", "trace"] : []), "agent"];
  const [tab, setTab] = useState("reply");
  if (!result) return <section id="pg-panel" className="fold" aria-labelledby="pg-panel-title"><h2 id="pg-panel-title">Behind the reply</h2><p>Send a question and what lies behind each reply shows here.</p></section>;
  const shown = names.includes(tab) ? tab : "reply";
  return <section id="pg-panel" className="fold" aria-labelledby="pg-panel-title">
    <h2 id="pg-panel-title">Behind “{question}” · {result.label || result.setting}</h2>
    <dl className="answer-facts"><div><dt>Tool</dt><dd><code>{result.tool || result.setting}</code></dd></div>{result.ms > 0 && <div><dt>Time</dt><dd>{timeWords(result.ms)}</dd></div>}</dl>
    <TabButtons className="tabs" label="Behind the reply" names={names} value={shown} onChange={setTab} prefix="pgtab-" panelPrefix="pgpanel-" />
    {names.map(name => <div key={name} id={`pgpanel-${name}`} role="tabpanel" aria-labelledby={`pgtab-${name}`} tabIndex={0} hidden={shown !== name}>
      {name === "reply" ? <ReplyView result={result} me={me} />
        : name === "sources" ? (result.value ? <SourcesList items={sourcesOf(result.tool, result.value)} /> : <p>No reply, so no sources.</p>)
        : name === "sql" ? (sqlOf(result.value).length ? <SqlPart answer={result.value} /> : <p>No SQL ran for this answer.</p>)
        : name === "graph" ? (result.value ? <SliceList result={result} /> : <p>No reply, so no graph slice.</p>)
        : name === "plan" ? <Plan answer={result.value} />
        : name === "trace" ? <Trace answer={result.value} />
        : <AgentCall request={result.request} reply={result.reply} base={base} />}
    </div>)}
  </section>;
}

/** One tool's column in a run: its name, its time and its reply in short; picking it fills the right panel. */
function Column({ result, chosen, onChoose }) {
  const text = result.pending ? "" : result.error || replyText(result.tool, result.value) || "No text in the reply.";
  return <button type="button" className={`pg-column${chosen ? " chosen" : ""}${result.error ? " failed" : ""}`} aria-pressed={chosen} data-setting={result.setting} onClick={() => { if (!result.pending) onChoose(); }} aria-disabled={result.pending || undefined}>
    <span className="pg-column-head"><strong>{result.label}</strong>{result.pending ? <span className="muted small">running…</span> : <span className="pg-time small">{timeWords(result.ms) || "no time"}</span>}</span>
    {result.pending ? <WaitingStatus detail={`Calling ${result.tool}`} /> : <span className="pg-column-text">{text.length > 280 ? `${text.slice(0, 277)}…` : text}</span>}
  </button>;
}

/** The Playground's tools but Ask: one question to one tool, or to every tool the person may use at once. */
function ToolRuns({ mode, settings, me, base, fetcher, hidden }) {
  const [question, setQuestion] = useState("");
  const [runs, setRuns] = useState([]);
  const [chosen, setChosen] = useState(null);
  const [status, setStatus] = useState("");
  const sequence = useRef(0), aborts = useRef(new Set());
  useEffect(() => () => aborts.current.forEach(a => a.abort()), []);
  const busy = runs.some(run => run.results.some(r => r.pending));
  const label = mode === ALL ? "Try all" : `Ask ${settings.find(s => s.setting === mode)?.label || mode}`;
  function send(event) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    const id = ++sequence.current;
    const targets = mode === ALL ? settings : settings.filter(s => s.setting === mode);
    setRuns(old => [{ id, question: text, all: mode === ALL, results: targets.map(t => ({ ...t, pending: true })) }, ...old]);
    setChosen(mode === ALL ? null : { run: id, setting: targets[0]?.setting });
    for (const target of targets) {
      const abort = new AbortController();
      aborts.current.add(abort);
      callTool(target.setting, text, { fetcher, signal: abort.signal }).then(result => {
        aborts.current.delete(abort);
        if (result.cancelled) return;
        setRuns(old => old.map(run => run.id !== id ? run : { ...run, results: run.results.map(r => r.setting === target.setting ? { ...target, ...result, pending: false } : r) }));
        setStatus(`${target.label} ${result.error ? "failed" : "replied"}${result.ms > 0 ? ` in ${timeWords(result.ms)}` : ""}.`);
      });
    }
  }
  const selectedRun = runs.find(run => run.id === chosen?.run);
  const selected = selectedRun?.results.find(r => r.setting === chosen?.setting && !r.pending);
  return <div className="pg-workspace" hidden={hidden}>
    <div className="pg-main">
      <p className="vh" role="status">{status}</p>
      <div className="pg-runs">
        {runs.length === 0 && <p className="muted">{mode === ALL ? "One question goes to every tool you may use, side by side, each with its time." : `Send a question to ${settings.find(s => s.setting === mode)?.tool || mode} and see its reply.`}</p>}
        {runs.map(run => <div key={run.id} className="pg-run" data-run={run.id}>
          <div className="msg q"><p>{run.question}</p></div>
          <div className={`pg-columns${run.all ? " all" : ""}`}>{run.results.map(result => <Column key={result.setting} result={result} chosen={chosen?.run === run.id && chosen?.setting === result.setting} onChoose={() => setChosen({ run: run.id, setting: result.setting })} />)}</div>
        </div>)}
      </div>
      <form className="question-form pg-form" onSubmit={send}>
        <label htmlFor="pg-q" className="vh">Your question</label>
        <input id="pg-q" type="text" value={question} onChange={e => setQuestion(e.target.value)} placeholder={mode === ALL ? "One question for every tool" : "Your question"} autoComplete="off" />
        <Button id="pg-send" type="submit" className="primary" disabled={busy || !question.trim()}>{busy ? "Running…" : label}</Button>
      </form>
    </div>
    <div className="pg-behind"><ToolPanel key={`${chosen?.run}:${chosen?.setting}`} result={selected} question={selectedRun?.question} me={me} base={base} /></div>
  </div>;
}

/** #623: Context's Playground: the switch (Ask, Context, Map, Search, Graph; Retrieve and Assist for engineers) and Try all. */
export default function Playground({ api, me, presets, header, golden, tester, health, fetcher = fetch }) {
  const [tools, setTools] = useState({ settings: ASK_ONLY, base: "" });
  const [mode, setMode] = useState("ask");
  const [opened, setOpened] = useState(false), [lastTool, setLastTool] = useState(ALL);
  useEffect(() => { if (mode !== "ask") { setOpened(true); setLastTool(mode); } }, [mode]);
  useEffect(() => {
    const abort = new AbortController();
    loadTools(fetcher, abort.signal).then(found => { if (!abort.signal.aborted) setTools(found.settings.length ? found : { ...found, settings: ASK_ONLY }); }).catch(() => {});
    return () => abort.abort();
  }, [fetcher]);
  const choices = [...tools.settings.map(s => s.setting), ...(tools.settings.length > 1 ? [ALL] : [])];
  const labelOf = (name) => name === ALL ? "Try all" : tools.settings.find(s => s.setting === name)?.label || name;
  const keys = (event) => {
    const at = choices.indexOf(mode);
    const next = { ArrowRight: at + 1, ArrowDown: at + 1, ArrowLeft: at - 1, ArrowUp: at - 1, Home: 0, End: choices.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const name = choices[(next + choices.length) % choices.length];
    setMode(name);
    event.currentTarget.querySelector(`[data-mode="${name}"]`)?.focus();
  };
  return <div className="playground">
    <div className="pg-switch" role="radiogroup" aria-label="Tool" onKeyDown={keys}>
      {choices.map(name => <button key={name} type="button" role="radio" data-mode={name} aria-checked={mode === name} tabIndex={mode === name ? 0 : -1} className={`pg-choice${name === ALL ? " all" : ""}`} onClick={() => setMode(name)}>{labelOf(name)}</button>)}
      {tools.error && <span className="muted small" role="status">{tools.error}</span>}
    </div>
    <div className="pg-ask" hidden={mode !== "ask"}><Ask api={api} me={me} presets={presets} header={header} golden={golden} tester={tester} health={health} mcpBase={tools.base} /></div>
    {(mode !== "ask" || opened) && <ToolRuns mode={mode === "ask" ? lastTool : mode} settings={tools.settings} me={me} base={tools.base} fetcher={fetcher} hidden={mode === "ask"} />}
  </div>;
}
