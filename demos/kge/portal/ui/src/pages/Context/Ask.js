import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Button } from "../../components/shared/Controls";
import { DEFAULT_RETENTION_DAYS, loadPersonHistory, savePersonTurn } from "../../services/askHistory";
import Answer from "./Answer";
import BehindAnswer from "./BehindAnswer";
import { resolvedQuestion } from "./Result";
import { plain } from "./contextState";

const MAX_HISTORY_TURNS = 8;
export const RAIL_FOLDED_KEY = "kge.ask.railFolded";
export const CHAT_SHARE_KEY = "kge.ask.chatShare";
export const DEFAULT_CHAT_SHARE = 57;
export const layoutKey = (key, viewer) => `${key}:${encodeURIComponent(viewer || "anonymous")}`;
const CHAT_MIN_PX = 360, PANEL_MIN_PX = 340, SPLITTER_PX = 12, SPLIT_GAP_PX = 4, SPLIT_STEP = 2;

function readStored(key, storage = "localStorage") {
  try { return window[storage]?.getItem(key) ?? null; } catch { return null; }
}
function writeStored(key, value, storage = "localStorage") {
  try { window[storage]?.setItem(key, value); return true; } catch { return false; }
}

/** The chat's share of the workspace, in percent, that keeps the chat at 360px or more and the panel at 340px or more. */
export function chatShareLimits(width) {
  const room = width - SPLITTER_PX - 2 * SPLIT_GAP_PX;
  if (!(room > 0)) return { min: 30, max: 75 };
  const min = Math.ceil(CHAT_MIN_PX / room * 100), max = Math.floor(100 - PANEL_MIN_PX / room * 100);
  if (min > max) { const even = Math.round(CHAT_MIN_PX / (CHAT_MIN_PX + PANEL_MIN_PX) * 100); return { min: even, max: even }; }
  return { min, max };
}

function RailIcon({ d }) {
  return <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

export function spread(questions, count) { return questions.length <= count ? questions : Array.from({ length: count }, (_, i) => questions[Math.floor((i + .5) * questions.length / count)]); }

function applyPathEvent(prevPath, ev) {
  const nodes = [...(prevPath?.nodes || [])];
  const edges = [...(prevPath?.edges || [])];
  if (ev.event === "node_added" && ev.node) {
    const idx = nodes.findIndex(n => n.id === ev.node.id);
    if (idx >= 0) nodes[idx] = { ...nodes[idx], ...ev.node };
    else nodes.push(ev.node);
  } else if (ev.event === "state_changed" && ev.id) {
    const idx = nodes.findIndex(n => n.id === ev.id);
    if (idx >= 0) {
      nodes[idx] = {
        ...nodes[idx],
        ...(ev.node || {}),
        state: ev.state || ev.node?.state || nodes[idx].state,
        ...(ev.time !== undefined ? { time: ev.time } : {}),
      };
    } else if (ev.node) {
      nodes.push(ev.node);
    }
  } else if ((ev.event === "edge_added" || ev.event === "edge_changed") && ev.edge) {
    const idx = edges.findIndex(e => e.from === ev.edge.from && e.to === ev.edge.to && e.type === ev.edge.type && e.label === ev.edge.label);
    if (idx >= 0) edges[idx] = { ...edges[idx], ...ev.edge };
    else edges.push(ev.edge);
  }
  return { ...prevPath, nodes, edges };
}

// The pipeline's stages as the Trace tab lists them: each in the order it first started, running until it ends with
// its time so far.
export function applyStageEvent(stages, ev) {
  const list = [...(stages || [])];
  const { event, cursor, ...stage } = ev;
  const idx = list.findIndex(s => s.stage === stage.stage);
  if (idx >= 0) list[idx] = stage.state === "running" ? { ...list[idx], state: "running" } : stage;
  else list.push(stage);
  return list;
}
export const STEP_WORDS = { understand: "understanding the question", access: "checking access", resolve: "resolving the terms",
  plan: "planning the query", gate: "checking the query", execute: "running the query", retrieve: "reading the documents",
  bind: "binding the figures", judge: "checking the answer", render: "writing the answer" };

function applyGraphEvent(prevGraph, ev) {
  const nodes = [...(prevGraph?.nodes || [])];
  const edges = [...(prevGraph?.edges || [])];
  if (ev.event === "graph_node_added" && ev.node?.id) {
    const idx = nodes.findIndex(n => n.id === ev.node.id);
    if (idx >= 0) nodes[idx] = { ...nodes[idx], ...ev.node };
    else nodes.push(ev.node);
  } else if (ev.event === "graph_summary" && Number.isFinite(ev.unused)) {
    return { nodes, edges, unused: ev.unused };
  } else if (ev.event === "graph_edge_added" && ev.edge?.from && ev.edge?.to) {
    const same = e => e.from === ev.edge.from && e.to === ev.edge.to && e.type === ev.edge.type && e.label === ev.edge.label;
    const idx = edges.findIndex(same);
    if (idx >= 0) edges[idx] = { ...edges[idx], ...ev.edge };
    else edges.push(ev.edge);
  }
  return Number.isFinite(prevGraph?.unused) ? { nodes, edges, unused: prevGraph.unused } : { nodes, edges };
}


// One exchange's live events (a direct stream, a background run's start and polls, or a resumed run's replay) applied to
// its streamed answer: the graph, the plan, the context, warehouse progress and the stages, and the step under way.
function liveEvents(setExchanges, id, signal, onRun) {
  return (ev) => {
    if (signal.aborted) return;
    setExchanges(old => old.map(ex => ex.id === id && !ex.streamStarted ? { ...ex, streamStarted: true } : ex));
    if (ev.event === "run" && /^[a-f0-9]{32}$/.test(ev.id)) onRun(ev);
    if ((ev.event === "node_added" && ev.node?.type) || (ev.event === "state_changed" && ev.node?.type) || ((ev.event === "edge_added" || ev.event === "edge_changed") && ev.edge?.type)) {
      setExchanges(old => old.map(ex => {
        if (ex.id !== id) return ex;
        const graph = applyPathEvent(ex.streamAnswer.graph_path, ev);
        return { ...ex, ...(ev.node?.type === "Runtime" ? { streamRuntime: ev.node.id } : {}), streamAnswer: { ...ex.streamAnswer, graph_path: graph } };
      }));
    } else if (ev.event === "node_added" || ev.event === "state_changed" || ev.event === "edge_added") {
      setExchanges(old => old.map(ex => {
        if (ex.id !== id) return ex;
        const graphHasNode = ev.event === "state_changed" && ex.streamAnswer.graph_path?.nodes?.some(n => n.id === ev.id);
        const nextPath = applyPathEvent(ex.streamPath, ev);
        const runningNode = nextPath.nodes.find(n => n.state === "running");
        return {
          ...ex,
          streamPath: nextPath,
          ...(graphHasNode ? { streamAnswer: { ...ex.streamAnswer, graph_path: applyPathEvent(ex.streamAnswer.graph_path, ev) } } : {}),
          currentStep: runningNode?.label || ex.currentStep || "",
        };
      }));
    } else if (ev.event === "stage" && ev.stage) {
      setExchanges(old => old.map(ex => {
        if (ex.id !== id) return ex;
        const stages = applyStageEvent(ex.streamAnswer.run_trace?.stages, ev);
        const running = stages.filter(s => s.state === "running").at(-1);
        return { ...ex, currentStep: running ? STEP_WORDS[running.stage] || running.stage : ex.currentStep,
          streamAnswer: { ...ex.streamAnswer, run_trace: { ...ex.streamAnswer.run_trace, stages } } };
      }));
    } else if (ev.event === "graph_summary" || ev.event === "graph_node_added" || ev.event === "graph_edge_added") {
      setExchanges(old => old.map(ex => ex.id === id ? { ...ex, streamAnswer: { ...ex.streamAnswer, graph_path: applyGraphEvent(ex.streamAnswer.graph_path, ev) } } : ex));
    } else if (ev.event === "plan" || ev.event === "context" || ev.event === "progress") {
      setExchanges(old => old.map(ex => {
        if (ex.id !== id) return ex;
        if (ev.event === "progress") {
          const statementNodes = { ...ex.statementNodes };
          if (!(ev.statement in statementNodes) && ex.streamRuntime) statementNodes[ev.statement] = ex.streamRuntime;
          const node = statementNodes[ev.statement];
          const progressByNode = node ? { ...ex.streamAnswer.progressByNode, [node]: ev } : ex.streamAnswer.progressByNode;
          return { ...ex, statementNodes, streamAnswer: { ...ex.streamAnswer, progress: ev, progressByNode } };
        }
        const update = ev.event === "plan" ? ev.plan : { context: ev.context };
        return { ...ex, streamAnswer: { ...ex.streamAnswer, ...update } };
      }));
    }
  };
}

function isReadableByMe(units, meReads) {
  if (!Array.isArray(units) || !units.length) return true;
  if (!Array.isArray(meReads) || !meReads.length) return false;
  const readSet = new Set(meReads);
  return units.every(u => readSet.has(u));
}

function turnUnits(value) {
  if (!value || typeof value !== "object") return [];
  return [
    ...(typeof value.unit === "string" && value.unit ? [value.unit] : []),
    ...(Array.isArray(value.units) ? value.units.filter(unit => typeof unit === "string" && unit) : []),
    ...(value.parts && typeof value.parts === "object" && !Array.isArray(value.parts) ? Object.keys(value.parts) : []),
    ...Object.values(value).flatMap(turnUnits),
  ];
}

function renderHighlightedText(text, query) {
  const q = String(query || "").trim();
  if (!q) return text;
  const idx = text.toLowerCase().indexOf(q.toLowerCase());
  if (idx < 0) return text;
  const before = text.slice(0, idx);
  const match = text.slice(idx, idx + q.length);
  const after = text.slice(idx + q.length);
  return (
    <>
      {before}
      <strong className="suggestion-match">{match}</strong>
      {after}
    </>
  );
}

export const ASK_TITLE = "Ask a governed question";
export const ASK_SUBTITLE = "Certified definitions and figures, answered only from data you may read.";

export default function Ask({ api, me, presets = {}, golden, tester, onReview, header }) {
  const title = String(header?.title || "").trim() || ASK_TITLE, subtitle = String(header?.subtitle || "").trim() || ASK_SUBTITLE;
  const thread = useRef(null), titleRow = useRef(null), mainCol = useRef(null), examplesButton = useRef(null), examplesPanel = useRef(null);
  const [examplesTop, setExamplesTop] = useState(0);
  const userScrolledUp = useRef(false);
  const followedScrollTop = useRef(0);
  const [question, setQuestion] = useState(""), [arm, setArm] = useState(presets.default || ""), [purpose, setPurpose] = useState("analysis"), [rich, setRich] = useState(false);
  const [all, setAll] = useState(false), [examples, setExamples] = useState(false), [exchanges, setExchanges] = useState([]), [selected, setSelected] = useState(null), [busy, setBusy] = useState(false);
  const [source, setSource] = useState(null), [requestedTab, setRequestedTab] = useState(null);
  const [currencies, setCurrencies] = useState(new Map());
  const onCurrency = useCallback((answer, currency) => setCurrencies(old => new Map(old).set(answer, currency)), []);
  const mappingOf = (answer) => currencies.get(answer);
  const [conversation, setConversation] = useState(null);
  const [conversationId, setConversationId] = useState(() => `conv-${Date.now()}`);
  const [selectedRuntimes, setSelectedRuntimes] = useState([]);
  const [availableRuntimes, setAvailableRuntimes] = useState(null);
  const [runtimesOpen, setRuntimesOpen] = useState(false);
  const [personHistory, setPersonHistory] = useState({ conversations: [], questions: [] });
  const [suggestionsClosed, setSuggestionsClosed] = useState(false);
  const [activeSuggestionIdx, setActiveSuggestionIdx] = useState(0);
  const railKey = layoutKey(RAIL_FOLDED_KEY, me?.id), shareKey = layoutKey(CHAT_SHARE_KEY, me?.id);
  const [railFolded, setRailFolded] = useState(() => readStored(railKey) === "1");
  const workspace = useRef(null), dragging = useRef(false);
  const [workspaceWidth, setWorkspaceWidth] = useState(0), [splitDragging, setSplitDragging] = useState(false);
  const [chatShare, setChatShare] = useState(() => {
    const stored = parseFloat(readStored(shareKey));
    return Number.isFinite(stored) ? stored : DEFAULT_CHAT_SHARE;
  });

  useLayoutEffect(() => {
    dragging.current = false;
    setSplitDragging(false);
    setRailFolded(readStored(railKey) === "1");
    const stored = parseFloat(readStored(shareKey));
    setChatShare(Number.isFinite(stored) ? stored : DEFAULT_CHAT_SHARE);
  }, [railKey, shareKey]);

  const retentionDays = Number.isFinite(Number(golden?.retention_days)) ? Number(golden.retention_days) : DEFAULT_RETENTION_DAYS;
  const scope = JSON.stringify([me.id, arm, purpose]);
  const latestScope = useRef(scope); latestScope.current = scope;
  const history = conversation?.scope === scope ? conversation.history : [];
  const pending = useRef(false), sequence = useRef(0), activeAbort = useRef(null);
  useEffect(() => () => activeAbort.current?.abort(), []);
  const sets = golden?.sets || [], total = sets.reduce((sum, set) => sum + set.questions.length, 0);
  const readableConversations = personHistory.conversations.filter(conv => (conv.turns || []).every(turn => {
    const units = turn.units || [];
    return units.length > 0 && isReadableByMe([...turnUnits(turn), ...units], me?.reads);
  }));

  useEffect(() => {
    setPersonHistory(loadPersonHistory(me?.id, { retentionDays }));
  }, [me?.id, retentionDays]);

  const ensureRuntimesLoaded = useCallback(async () => {
    if (availableRuntimes !== null) return;
    try {
      const res = await api("/runtimes");
      setAvailableRuntimes(Array.isArray(res?.runtimes) ? res.runtimes : []);
    } catch {
      setAvailableRuntimes([]);
    }
  }, [api, availableRuntimes]);

  const suggestions = useMemo(() => {
    const q = question.trim();
    if (!q || suggestionsClosed || busy) return [];
    const lower = q.toLowerCase();
    const meReads = me?.reads || [];
    const qUnitsMap = golden?.question_units || {};
    const seen = new Set();
    const matches = [];

    for (const set of sets) {
      for (const sampleQ of set.questions || []) {
        const units = qUnitsMap[sampleQ] || [];
        if (!isReadableByMe(units, meReads)) continue;
        const norm = sampleQ.toLowerCase();
        if (norm.includes(lower) && norm !== lower && !seen.has(norm)) {
          seen.add(norm);
          matches.push({ text: sampleQ, tag: "Sample question", prefixMatch: norm.startsWith(lower) });
        }
      }
    }

    for (const item of personHistory.questions || []) {
      const pastQ = String(item.question || "").trim();
      if (!pastQ) continue;
      if (!isReadableByMe(item.units || qUnitsMap[pastQ] || [], meReads)) continue;
      const norm = pastQ.toLowerCase();
      if (norm.includes(lower) && norm !== lower && !seen.has(norm)) {
        seen.add(norm);
        matches.push({ text: pastQ, tag: "You asked before", prefixMatch: norm.startsWith(lower) });
      }
    }

    for (const gItem of golden?.glossary || []) {
      const term = String(gItem?.term || "").trim();
      if (!term) continue;
      if (gItem.units?.length && !gItem.units.some(unit => meReads?.includes(unit))) continue;
      const termQuestion = `What is ${term}?`;
      const norm = termQuestion.toLowerCase();
      if ((term.toLowerCase().includes(lower) || norm.includes(lower)) && norm !== lower && !seen.has(norm)) {
        seen.add(norm);
        matches.push({ text: termQuestion, tag: "Glossary term", prefixMatch: norm.startsWith(lower) || term.toLowerCase().startsWith(lower) });
      }
    }

    return matches.slice(0, 2);
  }, [question, suggestionsClosed, busy, me?.reads, golden?.question_units, golden?.glossary, sets, personHistory.questions]);

  const bestGhostSuffix = useMemo(() => {
    if (!suggestions.length || !question.trim()) return "";
    const top = suggestions[0].text;
    if (top.toLowerCase().startsWith(question.toLowerCase())) {
      return top.slice(question.length);
    }
    return "";
  }, [suggestions, question]);

  const choose = (id, cite) => {
    setSelected(id);
    if (cite !== undefined) setSource(cite || null);
  };

  const openGraphFor = useCallback((id) => {
    flushSync(() => {
      setSelected(id);
      setSource(null);
      setRequestedTab({ tab: "graph", seq: ++sequence.current });
    });
  }, []);

  const startNewConversation = useCallback(() => {
    setConversation(null);
    setExchanges([]);
    setSelected(null);
    setSelectedRuntimes([]);
    setConversationId(`conv-${Date.now()}`);
    userScrolledUp.current = false;
  }, []);

  const restorePastConversation = useCallback((conv) => {
    if (!conv) return;
    setConversation(null);
    setConversationId(conv.id);
    setSelectedRuntimes(Array.isArray(conv.runtimes) ? conv.runtimes : []);
    const restored = (conv.turns || []).map((t, idx) => ({
      id: t.id || idx + 1,
      question: t.q || t.question || "",
      units: t.units || [],
      purpose: t.purpose,
      pending: false,
      answer: t.a || t.answer || { question: t.q || "", behaviour: "abstain", reason: t.err || "" },
    }));
    sequence.current = Math.max(sequence.current, ...restored.map(t => t.id));
    setExchanges(restored);
    setSelected(restored[0]?.id || null);
  }, []);

  useEffect(() => {
    let saved;
    try { saved = JSON.parse(readStored(layoutKey("kge.ask.pendingRun", me?.id), "sessionStorage") || "null"); } catch { return; }
    if (!saved || !/^[a-f0-9]{32}$/.test(saved.id) || pending.current) return;
    const controller = new AbortController();
    activeAbort.current = controller;
    pending.current = true;
    const restoredArm = saved.arm || presets.default || "graph", restoredPurpose = saved.purpose || "analysis";
    setArm(restoredArm);
    setPurpose(restoredPurpose);
    setSelectedRuntimes(Array.isArray(saved.runtimes) ? saved.runtimes : []);
    setBusy(true);
    const id = ++sequence.current;
    setExchanges(old => [{ id, question: saved.question || "Resuming question", purpose: restoredPurpose, pending: true, streamAnswer: { question: saved.question || "", principal: me?.id || "", arm: restoredArm, streaming: true, _mapKey: {} }, currentStep: "" }, ...old]);
    setSelected(id);
    api(`/ask/runs/${saved.id}`, undefined, { signal: controller.signal, onEvent: liveEvents(setExchanges, id, controller.signal, () => {}) }).then(answer => {
      if (controller.signal.aborted) return;
      setExchanges(old => old.map(ex => ex.id === id ? { ...ex, pending: false, answer } : ex));
      if (!answer?.error) writeStored(layoutKey("kge.ask.pendingRun", me?.id), "", "sessionStorage");
      pending.current = false;
      activeAbort.current = null;
      setBusy(false);
      if (answer?.history) setConversation({ scope: JSON.stringify([me.id, restoredArm, restoredPurpose]), history: answer.history });
    });
    return () => { controller.abort(); pending.current = false; };
  }, [api, me?.id]);

  const toggleRuntimeSelection = (rtId) => {
    setSelectedRuntimes(prev => prev.includes(rtId) ? prev.filter(id => id !== rtId) : [...prev, rtId]);
  };

  const stopActiveQuery = useCallback(() => {
    if (!activeAbort.current) return;
    activeAbort.current.abort();
    writeStored(layoutKey("kge.ask.pendingRun", me?.id), "", "sessionStorage");
    activeAbort.current = null;
    pending.current = false;
    setBusy(false);
    setExchanges(old => old.map(ex => ex.pending ? {
      ...ex,
      pending: false,
      answer: { ...ex.streamAnswer, behaviour: "abstain", reason: "Stopped.", streaming: false, stopped: true },
    } : ex));
  }, [me?.id]);

  async function ask(text = question) {
    text = text.trim();
    if (!text || pending.current || history.length >= MAX_HISTORY_TURNS) return;
    pending.current = true;
    const id = ++sequence.current;
    const abortCtrl = new AbortController();
    activeAbort.current = abortCtrl;
    userScrolledUp.current = false;
    flushSync(() => {
      setBusy(true); setExamples(false); setAll(false); setQuestion(text); setSuggestionsClosed(true); setSource(null);
      setExchanges(old => [{ id, question: text, purpose, pending: true, streamPath: null, streamGraph: null, streamAnswer: { question: text, principal: me?.id || "", arm, streaming: true, _mapKey: {} }, currentStep: "" }, ...old]); setSelected(id);
    });
    const agent = presets.agent_arms?.[arm];
    const path = agent ? "/agent" : presets.presets?.[arm]?.paths?.includes("ask") ? "/ask" : "/answer";
    const askedScope = scope;
    const reqBody = {
      question: text,
      purpose,
      arm,
      ...(history.length ? { history } : {}),
      ...(selectedRuntimes.length ? { runtimes: selectedRuntimes } : {}),
    };
    const onEvent = liveEvents(setExchanges, id, abortCtrl.signal, ev => writeStored(layoutKey("kge.ask.pendingRun", me?.id), JSON.stringify({ id: ev.id, question: text, arm, purpose, runtimes: selectedRuntimes }), "sessionStorage"));
    const t0 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();
    const rawAnswer = api.length >= 3
      ? await api(path + (!agent && rich ? "?display=1" : ""), reqBody, { signal: abortCtrl.signal, onEvent })
      : await api(path + (!agent && rich ? "?display=1" : ""), reqBody);
    const t1 = typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();
    const elapsedMs = Math.max(1, Math.round(t1 - t0));
    const answer = rawAnswer && typeof rawAnswer === "object"
      ? { ...rawAnswer, _live_ms: rawAnswer._live_ms || elapsedMs }
      : rawAnswer;
    if (abortCtrl.signal.aborted || answer?.cancelled) {
      return;
    }
    activeAbort.current = null;
    if (!answer.error) writeStored(layoutKey("kge.ask.pendingRun", me?.id), "", "sessionStorage");
    if (latestScope.current === askedScope && !answer.error) setConversation({ scope: askedScope, history: Array.isArray(answer.history) ? answer.history : [] });
    if (answer.status === 422) setConversation(null);
    setExchanges(old => {
      const qUnits = golden?.question_units?.[text] || (answer?.unit ? [answer.unit] : []);
      const updated = old.map(ex => ex.id === id ? { ...ex, pending: false, answer: answer?.error && ex.streamStarted ? { ...ex.streamAnswer, behaviour: "abstain", reason: answer.error, streaming: false, stopped: true } : { ...answer, _mapKey: ex.streamAnswer?._mapKey }, units: qUnits } : ex);
      if (me?.id && !answer?.error && !["refuse", "blocked_access"].includes(answer?.behaviour)) {
        const nextHist = savePersonTurn(
          me.id,
          {
            conversationId,
            title: updated[updated.length - 1]?.question || text,
            turns: updated.map(ex => ({ id: ex.id, q: ex.question, purpose: ex.purpose || purpose, arm, a: ex.answer, units: ex.units })),
            runtimes: selectedRuntimes,
            question: text,
            units: qUnits,
          },
          { retentionDays },
        );
        setPersonHistory(nextHist);
      }
      return updated;
    });
    pending.current = false; setBusy(false);
  }

  useEffect(() => {
    const card = thread.current?.querySelector(".exchange");
    const answer = card?.querySelector(".answer-card");
    const target = answer || card;
    const pinned = titleRow.current?.offsetHeight || 0;
    if (target && !userScrolledUp.current) {
      if (window.innerWidth <= 1024) {
        target.style.scrollMarginTop = `${(document.querySelector("header.gb-bar")?.offsetHeight || 0) + pinned + 12}px`;
        target.scrollIntoView?.({ block: answer ? "start" : "nearest", behavior: "auto" });
        followedScrollTop.current = thread.current.scrollTop;
      } else {
        target.style.scrollMarginTop = "0px";
        requestAnimationFrame(() => {
          const t = thread.current;
          if (!t) return;
          t.scrollTop += target.getBoundingClientRect().top - t.getBoundingClientRect().top - pinned;
          followedScrollTop.current = t.scrollTop;
        });
      }
    }
    if (exchanges[0] && !exchanges[0].pending) card?.querySelector(".answer-card")?.focus({ preventScroll: true });
  }, [exchanges]);

  const handleInputKeyDown = e => {
    if (!suggestions.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveSuggestionIdx(idx => (idx + 1) % suggestions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveSuggestionIdx(idx => (idx - 1 + suggestions.length) % suggestions.length);
    } else if (e.key === "Tab") {
      const picked = suggestions[activeSuggestionIdx] || suggestions[0];
      if (picked) {
        e.preventDefault();
        setQuestion(picked.text);
        setSuggestionsClosed(true);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      setSuggestionsClosed(true);
    }
  };

  useLayoutEffect(() => {
    const el = workspace.current;
    if (!el) return undefined;
    const measure = () => setWorkspaceWidth(el.getBoundingClientRect().width);
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(el);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  const shareLimits = chatShareLimits(workspaceWidth);
  const clampShare = value => Math.round(Math.min(shareLimits.max, Math.max(shareLimits.min, value)));
  const shownShare = clampShare(chatShare);
  const setShare = (value, remember = true) => {
    const next = clampShare(value);
    setChatShare(next);
    if (remember) writeStored(shareKey, String(next));
  };
  const resetShare = () => { setChatShare(DEFAULT_CHAT_SHARE); writeStored(shareKey, String(DEFAULT_CHAT_SHARE)); };
  const shareAt = clientX => {
    const box = workspace.current?.getBoundingClientRect();
    const room = (box?.width || 0) - SPLITTER_PX - 2 * SPLIT_GAP_PX;
    return room > 0 ? (clientX - box.left - SPLIT_GAP_PX - SPLITTER_PX / 2) / room * 100 : null;
  };
  const splitterKeys = e => {
    const moves = { ArrowLeft: () => setShare(shownShare - SPLIT_STEP), ArrowRight: () => setShare(shownShare + SPLIT_STEP), Home: () => setShare(shareLimits.min), End: () => setShare(shareLimits.max), Enter: resetShare };
    if (!moves[e.key]) return;
    e.preventDefault();
    moves[e.key]();
  };
  const toggleRail = () => {
    const next = !railFolded;
    setRailFolded(next);
    writeStored(railKey, next ? "1" : "0");
  };

  const focusExamples = useRef(false);
  const toggleExamples = useCallback((next, moveFocus = false) => {
    focusExamples.current = next && moveFocus;
    setExamples(next);
    if (!next && moveFocus) examplesButton.current?.focus();
  }, []);
  useLayoutEffect(() => {
    if (!examples) return undefined;
    const place = () => {
      const button = examplesButton.current?.getBoundingClientRect(), box = mainCol.current?.getBoundingClientRect();
      if (button && box) setExamplesTop(Math.max(0, Math.round(button.bottom - box.top + 6)));
    };
    place();
    if (focusExamples.current) {
      focusExamples.current = false;
      examplesPanel.current?.querySelector("button, [href], [tabindex]:not([tabindex='-1'])")?.focus();
    }
    const outside = e => { if (!examplesPanel.current?.contains(e.target) && !examplesButton.current?.contains(e.target)) setExamples(false); };
    const escape = e => { if (e.key === "Escape") setExamples(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("resize", place);
    thread.current?.addEventListener("scroll", place);
    const scroller = thread.current;
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("resize", place);
      scroller?.removeEventListener("scroll", place);
    };
  }, [examples]);
  const shown = exchanges.find(ex => ex.id === selected);
  const shownAnswer = shown?.pending ? shown.streamAnswer : shown?.answer;
  return (
    <section id="assistant" className={`ask-shell${railFolded ? " rail-folded" : ""}`}>
      <aside className={`ask-rail${railFolded ? " folded" : ""}`} aria-label="Conversations">
        <div className="rail-top">
          <button type="button" className="rail-fold" data-testid="rail-fold" aria-expanded={!railFolded} aria-controls="ask-rail-body" aria-label={railFolded ? "Show conversations" : "Hide conversations"} title={railFolded ? "Show conversations" : "Hide conversations"} onClick={toggleRail}>
            <RailIcon d={railFolded ? "M8 5 L13 10 L8 15" : "M12 5 L7 10 L12 15"} />
          </button>
          {railFolded && (
            <button type="button" className="rail-new-icon" data-testid="rail-new-conversation-icon" aria-label="New conversation" title="New conversation" disabled={busy} onClick={startNewConversation}>
              <RailIcon d="M10 4 L10 16 M4 10 L16 10" />
            </button>
          )}
        </div>
        <div id="ask-rail-body" className="rail-body">
        <Button type="button" className="rail-new-btn" data-testid="rail-new-conversation" disabled={busy} onClick={startNewConversation}>
          New conversation
        </Button>
        <div className="rail-past">
          <span className="rail-heading muted small">Past conversations</span>
          {readableConversations.length === 0 ? (
            <p className="rail-empty muted small">None yet</p>
          ) : (
            <ul className="rail-list">
              {readableConversations.map(conv => (
                <li key={conv.id}>
                  <a
                    href={`#conv-${conv.id}`}
                    className={`rail-conv-btn${conv.id === conversationId ? " active" : ""}`}
                    onClick={e => { e.preventDefault(); restorePastConversation(conv); }}
                  >
                    {conv.title}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
        </div>
      </aside>
      <div className="ask-workspace" ref={workspace} style={{ "--ask-chat": `${shownShare}fr`, "--ask-behind": `${100 - shownShare}fr` }}>
        <div className="ask-main-col" ref={mainCol}>
          <div
            id="thread"
            ref={thread}
            onScroll={e => {
              const el = e.currentTarget;
              userScrolledUp.current = el.scrollTop < followedScrollTop.current - 40;
            }}
          >
            <div className="ask-exchanges" aria-live="polite" aria-relevant="additions">
            {exchanges.map(ex => (
              <div
                className="exchange"
                key={ex.id}
                data-exchange-id={ex.id}
                aria-current={ex.id === selected}
                aria-busy={ex.pending || undefined}
                tabIndex={0}
                onFocus={e => {
                  if (e.target === e.currentTarget || e.target?.classList?.contains("answer-card") || e.target?.classList?.contains("copy")) {
                    choose(ex.id);
                  }
                }}
                onClick={e => {
                  if (e.target?.closest?.(".activity-line, .answer-actions")) return;
                  choose(ex.id);
                }}
                onKeyDown={e => { if (e.key === "Enter" && e.target === e.currentTarget) { e.preventDefault(); choose(ex.id); } }}
              >
                <div className="msg q"><p>{ex.question}</p></div>
                {ex.pending
                  ? (
                    <div className="msg skeleton" aria-busy="true">
                      <span className="live-activity-line" data-testid="live-activity-line">
                        {ex.currentStep ? `Working out the answer… · ${ex.currentStep}` : "Working out the answer…"}
                      </span>
                    </div>
                  )
                  : <Answer
                      answer={ex.answer}
                      onCite={n => choose(ex.id, n)}
                      onReview={onReview}
                      onOpenGraph={() => openGraphFor(ex.id)}
                      agentArms={presets.agent_arms}
                      mapping={mappingOf(ex.answer)}
                      me={me}
                      onChoice={!busy && ex.answer?.history === history && history.length ? label => { const text = resolvedQuestion(ex.answer, label); if (text) ask(text); } : undefined}
                    />}
              </div>
            ))}
            </div>
            <p className="intro ask-subtitle">{subtitle}</p>
            <div className="ask-title-row" ref={titleRow}>
              <h1>{title}</h1>
              <Button type="button" id="examples-button" ref={examplesButton} aria-haspopup="dialog" aria-expanded={examples} aria-controls="examples" onClick={() => toggleExamples(!examples, true)}>Examples ▾</Button>
            </div>
          </div>
          <div id="examples" ref={examplesPanel} role="dialog" aria-label="Example questions" hidden={!examples} style={{ top: examplesTop, maxHeight: `max(12rem, calc(100% - ${examplesTop + 20}px))` }} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); toggleExamples(false, true); } }}>
            <div id="chips" className="chips">{golden?.error ? <p>{golden.error}</p> : sets.map((set, i) => <React.Fragment key={`${set.name}-${i}`}><span className="set" data-set={`${set.name} ${set.version}`}>{set.name === "golden" ? "Sample questions" : `${plain(set.name.replace(/^golden_/, ""))} questions`}</span>{(all ? set.questions : spread(set.samples || set.questions, 4)).map(q => <Button key={q} data-q={q} className={set.misses?.includes(q) ? "miss" : ""} title={`${q}${set.misses?.includes(q) ? ` · known miss: the ${presets.default} arm does not answer it as the set expects` : ""}`} onClick={() => ask(q)}>{q}{set.misses?.includes(q) && <span className="tag">known miss</span>}</Button>)}</React.Fragment>)}
            {sets.some(set => (all ? set.questions : spread(set.samples || set.questions, 4)).some(q => set.misses?.includes(q))) && <span className="note" title="These questions did not match the expected results in the release baseline.">Known miss: the {presets.default || "default"} arm does not answer it as the set expects, as this profile's A/B baseline records.</span>}
            {total > sets.reduce((sum, set) => sum + spread(set.samples || set.questions, 4).length, 0) && <Button className="more" onClick={() => setAll(!all)}>{all ? "Fewer sample questions" : `All ${total} sample questions`}</Button>}</div>
          </div>
          <div className="ask-composer-dock">
            <div id="conversation" className="row small muted" role="status" hidden={!history.length}><span id="conversation-text">{history.length >= MAX_HISTORY_TURNS ? "This conversation has reached its eight follow-ups. Start a new conversation to continue." : history.length ? `A follow-up builds on the last ${history.length === 1 ? "question" : `${history.length} questions`}.` : ""}</span></div>
            {suggestions.length > 0 && (
              <ul className="ask-suggestions" role="listbox" aria-label="Question suggestions">
                {suggestions.map((s, idx) => (
                  <li
                    key={s.text}
                    role="option"
                    aria-selected={idx === activeSuggestionIdx}
                    className={`ask-suggestion-item${idx === activeSuggestionIdx ? " active" : ""}`}
                    onMouseDown={e => {
                      e.preventDefault();
                      setQuestion(s.text);
                      setSuggestionsClosed(true);
                      ask(s.text);
                    }}
                  >
                    <span className="suggestion-text">{renderHighlightedText(s.text, question)}</span>
                    <span className="badge suggestion-tag">{s.tag}</span>
                  </li>
                ))}
              </ul>
            )}
            <form className="question-form" onSubmit={e => { e.preventDefault(); ask(); }}>
              <label htmlFor="q" className="vh">Question</label>
              <div className="composer-input-wrap">
                {bestGhostSuffix && (
                  <div className="composer-ghost" aria-hidden="true">
                    <span className="ghost-typed">{question}</span>
                    <span className="ghost-rest">{bestGhostSuffix}</span>
                  </div>
                )}
                <input
                  id="q"
                  type="text"
                  value={question}
                  onChange={e => {
                    setQuestion(e.target.value);
                    setSuggestionsClosed(false);
                    setActiveSuggestionIdx(0);
                  }}
                  onKeyDown={handleInputKeyDown}
                  placeholder="Ask for a figure or a definition, or pick one from Examples"
                />
              </div>
              <Button id="ask" type="submit" className="primary" disabled={busy} aria-busy={busy || undefined}>{busy ? "Asking…" : "Ask"}</Button>
              {busy && <Button id="stop-ask" type="button" onClick={stopActiveQuery}>Stop</Button>}
            </form>
            <div className="composer-chips-bar">
              <details
                id="runtimes-chip"
                className="fold chip-fold"
                open={runtimesOpen}
                onToggle={e => {
                  const nextOpen = e.currentTarget.open;
                  setRuntimesOpen(nextOpen);
                  if (nextOpen) ensureRuntimesLoaded();
                }}
              >
                <summary>
                  Runtimes
                  <span id="runtimes-now" className="muted">
                    {" "}· {selectedRuntimes.length ? selectedRuntimes.join(" → ") : "certified default"}
                  </span>
                </summary>
                <div className="runtimes-list" role="group" aria-label="Available runtimes">
                  {availableRuntimes === null ? (
                    <p className="small muted">Loading runtimes…</p>
                  ) : availableRuntimes.length === 0 ? (
                    <p className="small muted">Default certified runtime active.</p>
                  ) : (
                    availableRuntimes.map(rt => {
                      const orderIdx = selectedRuntimes.indexOf(rt.id);
                      const isSelected = orderIdx >= 0;
                      return (
                        <label key={rt.id} className="switch runtime-option">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleRuntimeSelection(rt.id)}
                          />
                          <span className="runtime-option-label">{rt.label || rt.id}</span>
                          {rt.certified && <span className="badge ok">Certified</span>}
                          {isSelected && <span className="badge">#{orderIdx + 1}</span>}
                        </label>
                      );
                    })
                  )}
                </div>
              </details>
              <details id="settings" className="fold chip-fold" hidden={!tester}><summary>Answer settings <span id="settings-now" className="muted">· {arm || "default"} arm, {purpose}{rich ? ", rich answers" : ""}</span></summary>
                <div className="row"><label className="field" htmlFor="purpose">Purpose<select id="purpose" value={purpose} onChange={e => { setPurpose(e.target.value); setConversation(null); }}>{["analysis", "assistant", "supervision"].map(value => <option key={value}>{value}</option>)}</select></label>
                  <label className="field" htmlFor="arm">Answered by<select id="arm" value={arm} onChange={e => { setArm(e.target.value); setConversation(null); }}><optgroup label="The control: presets">{Object.values(presets.presets || {}).map(p => <option key={p.arm} value={p.arm}>{p.arm}</option>)}</optgroup><optgroup label="The reference agent, the A/B">{Object.values(presets.agent_arms || {}).map(p => <option key={p.arm} value={p.arm}>agent {p.arm}, {p.side} ({p.mode})</option>)}</optgroup></select></label>
                  <label className="switch" htmlFor="rich"><input id="rich" type="checkbox" checked={rich} onChange={e => setRich(e.target.checked)} />Rich answers</label></div>
                <p className="hint">Why you ask: the access rules are checked for this purpose. Analysis suits most questions.</p>
              </details>
            </div>
          </div>
        </div>
        <div
          className={`ask-splitter${splitDragging ? " dragging" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the chat and Behind the answer"
          aria-valuenow={shownShare}
          aria-valuemin={shareLimits.min}
          aria-valuemax={shareLimits.max}
          aria-valuetext={`Chat ${shownShare}%, Behind the answer ${100 - shownShare}%`}
          tabIndex={0}
          title="Drag, or use the arrow keys, to resize. Enter or a double-click resets."
          onKeyDown={splitterKeys}
          onDoubleClick={resetShare}
          onPointerDown={e => {
            if (e.button !== undefined && e.button !== 0) return;
            dragging.current = true;
            setSplitDragging(true);
            e.currentTarget.setPointerCapture?.(e.pointerId);
            e.preventDefault();
          }}
          onPointerMove={e => {
            if (!dragging.current) return;
            const next = shareAt(e.clientX);
            if (next !== null) setShare(next, false);
          }}
          onPointerUp={e => {
            if (!dragging.current) return;
            dragging.current = false;
            setSplitDragging(false);
            e.currentTarget.releasePointerCapture?.(e.pointerId);
            const next = shareAt(e.clientX);
            if (next !== null) setShare(next);
          }}
          onPointerCancel={e => {
            if (!dragging.current) return;
            dragging.current = false;
            setSplitDragging(false);
            e.currentTarget.releasePointerCapture?.(e.pointerId);
            setShare(shownShare);
          }}
        />
        <div className="ask-behind-col">
          <BehindAnswer api={api} answer={shownAnswer} purpose={shown?.purpose} me={me} agentArms={presets.agent_arms} source={source} onCurrency={onCurrency} requestedTab={requestedTab} />
        </div>
      </div>
    </section>
  );
}
