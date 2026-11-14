import { useCallback, useEffect, useRef, useState } from "react";

export const canEvaluate = (me) => !!me && !!(me.processes?.includes("building") || me.admin || me.roles?.includes("admin"));
export function visibleTabs(me) {
  const does = (process) => me?.processes?.includes(process);
  return [...(does("querying") ? ["ask"] : []), "docs", ...(does("certification") ? ["review"] : []),
    ...(me?.writes_terms || ["certification", "drafting", "building", "improving", "accepting"].some(does) ? ["onboard"] : []),
    ...(canEvaluate(me) ? ["quality"] : [])];
}
export const homeTab = (me) => me?.processes?.includes("certification") ? "review" : visibleTabs(me).includes("onboard") ? "onboard" : visibleTabs(me).includes("ask") ? "ask" : "docs";
export const plain = (value) => String(value ?? "").replace(/_/g, " ").replace(/^./, c => c.toUpperCase());
export const money = (value) => value == null ? "none" : Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const referenceFigure = (value) => value == null ? "none" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
export const wordsOf = (text) => String(text ?? "").toLocaleLowerCase().normalize("NFKC").match(/[\p{L}\p{N}]+/gu) || [];
export function saysWords(text, phrase) {
  const said = wordsOf(text), wanted = wordsOf(phrase);
  return wanted.length > 0 && said.some((_, i) => wanted.every((word, k) => said[i + k] === word));
}
export const realCurrency = (currency) => currency && String(currency).toUpperCase() !== "XXX" ? currency : "";
export const withoutNoCurrency = (text) => String(text ?? "").replace(/\s*·\s*XXX(?=\s*(?:·|$))/g, "").replace(/ ?\(XXX\)/g, "").replace(/(?<![\w-])XXX (?=\S)/g, "").replace(/ (?<![\w-])XXX(?![\w-])/g, "").replace(/(?<![\w-])XXX(?![\w-])/g, "");
export const isCountKind = (kind) => /^count/i.test(String(kind ?? ""));
export const isAmount = ({ currency, kind, agg, amount } = {}) => !isCountKind(kind) && !isCountKind(agg) && Boolean(amount || realCurrency(currency));
export const amount = (value, about = {}) => isAmount(about) ? money(value) : count(value);
export function figureMetadata(a, mapping) {
  const columns = (a.result?.columns || []).filter(c => c.kind !== "dimension");
  const sourceParts = [a.plan, a.parts, a.figures].find(items => Array.isArray(items) && items.length) || [];
  const parts = (sourceParts.length ? sourceParts : columns).map((p, i) => ({
    currency: realCurrency(p.currency) || realCurrency(columns[i]?.currency) || realCurrency(a.currency),
    kind: [p, columns[i]].some(c => isCountKind(c?.kind) || isCountKind(c?.agg)) ? "count" : undefined,
  }));
  const currencies = parts.map(p => p.currency);
  const agreedCurrency = currencies.length && currencies.every(c => c === currencies[0]) ? currencies[0] : "";
  const currency = sourceParts.length && ["sum_with_verdict", "sum_without_verdict"].includes(a.behaviour) ? agreedCurrency
    : realCurrency(mapping?.currency) || realCurrency(a.context?.currency) || realCurrency(a.currency) || agreedCurrency;
  const counted = a.trace?.some?.(step => isCountKind(step?.agg)) || isCountKind(mapping?.kind || a.context?.kind || a.kind)
    || [...parts, ...columns, ...(a.figures || [])].some(p => isCountKind(p.kind) || isCountKind(p.agg));
  return { currency, kind: counted ? "count" : undefined, parts };
}
export const count = (value) => value == null ? "none" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
export const quarterWords = (text) => String(text ?? "").replace(/\b(\d{4})-Q([1-4])\b/g, "Q$2 $1");
const asAt = (iso) => new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const asOfWords = (text) => text.replace(/\bas_of (\d{4}-\d{2}-\d{2})\b/g, (_, iso) => `as at ${asAt(iso)}`);
export const readablePeriods = (text) => quarterWords(asOfWords(String(text ?? "").replace(/\b(?:calendar|fiscal)_(?:month|quarter|year|day|week) (?=\S)/g, "")));
export const periodText = (period) => quarterWords(asOfWords(String(period ?? "").replace(/^(calendar_|fiscal_)?(month|quarter|year|day|week) /, "")));
const RAW_NAME = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*)+$|^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/;
export function businessName(name) {
  const text = String(name ?? "");
  if (!RAW_NAME.test(text)) return text;
  const last = text.split(".").pop().toLowerCase().replace(/^calendar_/, "");
  const core = last.replace(/_(code|cd|id|key|name|nm)$/, "") || last;
  return plain(core);
}
const PHRASE = "[\\w'’-]+(?: [\\w'’-]+){0,2}";
const DECLINED_WORDS = new RegExp(`^(${PHRASE}(?:; ${PHRASE})*)\\. No figure is given\\.((?: .*)?)$`, "s");
const GLOSSARY_PROVENANCE = /^(No document defines |What the question retrieved is listed as a reference)/;
export const glossaryNotices = (reason) => String(reason ?? "").split(/(?<=\.)\s+(?=[A-Z(])/).filter(sentence => sentence.trim() && !GLOSSARY_PROVENANCE.test(sentence.trim())).join(" ");
export function declineText(reason, question) {
  const text = String(reason ?? ""), found = DECLINED_WORDS.exec(text), words = found?.[1]?.split("; ");
  if (!words || !words.every(word => saysWords(question, word))) return text;
  const quoted = words.map(word => `“${word}”`), list = quoted.length === 1 ? quoted[0] : `${quoted.slice(0, -1).join(", ")} and ${quoted.at(-1)}`;
  return `No figure is given: the question's ${quoted.length === 1 ? "word" : "words"} ${list} could not be applied.${found[2]}`;
}
export const reportUrl = (path) => typeof path === "string" && /^\/ab\/[\w.-]+\/report\.html$/.test(path) ? "/api/context" + path : undefined;
export async function followQuestionRun(initial, load, { signal, onEvent = () => {}, wait } = {}) {
  const pause = wait || ((ms) => new Promise(resolve => {
    const done = () => { clearTimeout(timer); signal?.removeEventListener("abort", done); resolve(); };
    const timer = setTimeout(done, ms);
    signal?.addEventListener("abort", done, { once: true });
  }));
  let run = initial;
  // Each event moves the run's cursor as it is delivered, so a poll that fails midway resumes after it, never before.
  const deliver = (event) => {
    onEvent(event);
    if (Number.isInteger(event?.cursor) && event.cursor > (run.cursor || 0)) run = { ...run, cursor: event.cursor };
  };
  while (true) {
    if (signal?.aborted) return { error: "Stopped.", cancelled: true };
    if (run.error) return { error: run.error };
    if (run.answer) return run.answer;
    if (run.state !== "running") return { error: "This question could not finish. Please try again." };
    if (Date.now() / 1000 >= run.expires_at) {
      // Fetch once more so the server records expiry and cancels its warehouse jobs.
      try {
        const expired = await load(run.id, run.cursor || 0, deliver);
        return expired.answer || { error: expired.error || "This question exceeded its time limit; ask a narrower question." };
      } catch {
        return { error: "This question exceeded its time limit; ask a narrower question." };
      }
    }
    await pause(Math.max(50, Number(run.poll_after || 2) * 1000));
    if (signal?.aborted) return { error: "Stopped.", cancelled: true };
    try {
      const next = await load(run.id, run.cursor || 0, deliver);
      for (const event of next.events || []) onEvent(event);
      run = { ...next, cursor: Math.max(next.cursor || 0, run.cursor || 0) };
    } catch {
      if (signal?.aborted) return { error: "Stopped.", cancelled: true };
    }
  }
}

const streams = (response) => !!response.body && typeof response.body.getReader === "function"
  && (response.headers?.get?.("content-type") || "").toLowerCase().includes("application/x-ndjson");
// Each NDJSON line of body, parsed, to onLine as it arrives; onLine returning false stops reading.
export async function readLines(body, onLine) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const take = (line) => line.trim() ? onLine(JSON.parse(line.trim())) !== false : true;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) if (!take(line)) { reader.cancel?.().catch?.(() => {}); return; }
  }
  take(buffer);
}

export function useContextApi() {
  const lifecycle = useRef(null);
  if (!lifecycle.current) lifecycle.current = new AbortController();
  useEffect(() => {
    if (lifecycle.current.signal.aborted) lifecycle.current = new AbortController();
    return () => lifecycle.current.abort();
  }, []);
  return useCallback(async (path, body, options) => {
    options = options || {};
    const controller = lifecycle.current;
    const signal = options.signal || controller.signal;
    const onEvent = options.onEvent;
    let questionRun = null;
    const follow = (run) => followQuestionRun(run, async (id, after, deliver = onEvent) => {
      const response = await fetch(`/api/context/ask/runs/${encodeURIComponent(id)}?after=${after}`, { signal, ...(onEvent ? { headers: { accept: "application/x-ndjson, application/json" } } : {}) });
      if (response.ok && onEvent && streams(response)) {
        // A poll that advances the run streams its events live; the run line carries its state and cursor.
        const polled = { events: [], cursor: after };
        await readLines(response.body, (ev) => {
          if (ev.event === "run") { Object.assign(polled, ev, { events: [] }); return; }
          if (Number.isInteger(ev.cursor)) polled.cursor = Math.max(polled.cursor, ev.cursor);
          if (ev.event === "answer") polled.answer = ev.answer;
          else if (ev.event === "error") polled.error = ev.error || "This question could not finish. Please try again.";
          deliver(ev);
        });
        return polled;
      }
      const result = await response.json();
      return response.ok ? result : { error: result.detail || "This question is unavailable." };
    }, { signal, onEvent });
    if (onEvent && path.split("?")[0] === "/ask") path += (path.includes("?") ? "&" : "?") + "background=true";
    try {
      const response = await fetch("/api/context" + path, { signal,
        ...(body === undefined && onEvent ? { headers: { accept: "application/x-ndjson, application/json" } } : {}),
        ...(body !== undefined ? { method: "POST", ...(body === null ? {} : { headers: { "content-type": "application/json", ...(onEvent ? { accept: "application/x-ndjson, application/json" } : {}) }, body: JSON.stringify(body) }) } : {}) });
      if (response.status === 401) {
        await fetch("/session", { method: "DELETE", signal: controller.signal });
        if (!controller.signal.aborted) window.location.assign("/");
      }
      if (response.ok && onEvent && streams(response)) {
        let finalAnswer = null, failed = null;
        await readLines(response.body, (ev) => {
          onEvent(ev);
          if (ev.event === "run") questionRun = ev;
          else if (questionRun && Number.isInteger(ev.cursor)) questionRun.cursor = ev.cursor;
          if (ev.event === "answer") finalAnswer = ev.answer;
          else if (ev.event === "error") { failed = { error: ev.error || "This action is unavailable.", status: 500 }; return false; }
        });
        if (failed) return failed;
        if (signal.aborted) return { error: "Stopped.", cancelled: true };
        return finalAnswer || (questionRun ? follow(questionRun) : { error: "Stream ended without an answer." });
      }
      const result = await response.json();
      if (signal.aborted) return { error: "Request cancelled.", cancelled: true };
      if (response.ok && result.id && result.state && Array.isArray(result.events)) {
        for (const event of result.events) onEvent?.(event);
        onEvent?.({ event: "run", ...result });
        return follow(result);
      }
      return response.ok ? result : { error: `${response.status === 403 ? "Not permitted: " : ""}${typeof (result.detail || result.error) === "string" ? result.detail || result.error : result.detail || result.error ? JSON.stringify(result.detail || result.error) : "This action is unavailable."}`, status: response.status };
    } catch {
      if (questionRun && !signal.aborted) return follow(questionRun);
      return { error: signal.aborted ? "Request cancelled." : "The service is unavailable right now. Try again shortly.", cancelled: signal.aborted };
    }
  }, []);
}
export function useResource(api, path, enabled = true) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const turn = useRef(0);
  const refresh = useCallback(async () => {
    const current = ++turn.current;
    if (!enabled) { setData(null); return; }
    setLoading(true);
    const result = await api(path);
    if (current === turn.current) { setData(result); setLoading(false); }
    return result;
  }, [api, path, enabled]);
  useEffect(() => { refresh(); return () => { ++turn.current; }; }, [refresh]);
  return { data, loading, refresh };
}
export const STAGES = ["Uploaded", "Drafted", "Accepted", "Certified", "Checks", "Live"];
export function unitDone(unit) {
  const items = unit.items || [], version = unit.update?.version || unit.version || 1;
  return [true, items.length > 0, items.length > 0 && items.every(i => i.accepted_by), items.length > 0 && items.every(i => i.certified_by),
    !unit.update && (unit.history || []).some(h => h.step === "onboard" && (h.version || 1) === version && h.detail?.passed === true),
    !unit.update && !!unit.release?.model_hash && (unit.release.version || 1) === version && unit.live === true];
}
export const ACTION_LABELS = { accept: "Accept all", certify: "Certify all", run: "Run checks", sign_off: "Sign off", release: "Release", improve: "Improve: Run" };
const roles = { draft: "a knowledge engineer", rows: "a knowledge engineer", run: "a knowledge engineer", improve: "a knowledge engineer", accept: "the business team", certify: "a steward", sign_off: "a steward", release: "a steward" };
export function actionPlan(action, unit, ob, at, proposal) {
  const label = unit.label || unit.unit || "the proposal", count = (unit.items || proposal?.items || []).length;
  const phrase = { draft: `draft ${label}'s items`, rows: `prepare ${label}'s rows`, accept: `accept ${count} drafted items`, certify: `certify ${count} accepted items`,
    run: "run the checks on the candidate", sign_off: "sign off the checked candidate", release: `release the signed-off candidate: ${label} goes live`, improve: "draft an improvement proposal" }[action];
  return { action, unit: unit.unit, label, count, at, proposal: proposal?.id, mine: !!ACTION_LABELS[action] && !!ob?.can?.[action], role: roles[action] || "a knowledge engineer", phrase };
}
export function unitPlan(unit, ob) {
  const at = unitDone(unit).indexOf(false);
  if (at < 0) return { at, unit: unit.unit, label: unit.label || unit.unit, mine: false };
  let action = ["create", "draft", "accept", "certify", "run", "sign_off"][at];
  if (at > 1 && (unit.needs || (unit.requests || []).some(q => /\brows\b/i.test(q.ask || ""))) && (ob?.can?.create || at === 4)) action = "rows";
  if (at === 5 && ob?.candidate?.signed_off) action = "release";
  return actionPlan(action, unit, ob, at);
}
export function proposalPlan(proposal, ob) {
  const items = proposal.items || [];
  const done = [true, items.length > 0 && items.every(i => i.accepted_by), items.length > 0 && items.every(i => i.certified_by), !!ob.candidate?.gate?.passed, false];
  const at = done.indexOf(false);
  return { ...actionPlan(at === 1 ? "accept" : at === 2 ? "certify" : at === 3 ? "run" : ob.candidate?.signed_off ? "release" : "sign_off", {}, ob, at, proposal), done };
}
export function nextStep(ob, queue) {
  const steps = ob ? [...(ob.units || []).map(u => unitPlan(u, ob)), ...(ob.proposal ? [proposalPlan(ob.proposal, ob)] : [])].filter(p => p.action) : [];
  const mine = steps.filter(p => p.mine), chosen = mine.length ? mine : steps;
  const tags = (queue || []).filter(t => t.upstream !== "rejected").length;
  if (!chosen.length && !tags) return null;
  return { waiting: !mine.length && !!steps.length, view: chosen.length ? "onboard" : "review", unit: chosen[0]?.unit, proposal: chosen[0]?.proposal,
    text: chosen.length ? chosen.map(p => p.mine ? `${plain(["accept", "certify"].includes(p.action) ? `${p.phrase} for ${p.label}` : p.phrase)}.` : `${p.role} to ${p.action === "accept" ? `accept ${p.label}'s ${p.count} drafted items` : p.phrase}.`).join(" · ") : `${tags} tags wait for a decision in the Review queue.` };
}
