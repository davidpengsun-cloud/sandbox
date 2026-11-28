import React, { useEffect, useState } from "react";
import { getAskSuggestions, saveAskSuggestions } from "../../services/admin";

/* Discover's Ask suggested questions, as an admin sets them: one list for a data asset's Ask and one for a domain's,
   each of up to MAX_QUESTIONS questions of up to MAX_LENGTH characters. A list left empty keeps the page's own
   questions. Saved the way Prompt direction is, with its last change; it applies from the next page load. */
export const MAX_QUESTIONS = 6;
export const MAX_LENGTH = 200;

const LISTS = [
  { key: "asset", legend: "On a data asset", label: (n) => `Data asset question ${n}` },
  { key: "domain", legend: "On a domain", label: (n) => `Domain question ${n}` },
];

const slots = (list) => Array.from({ length: MAX_QUESTIONS }, (_, i) => (Array.isArray(list) ? list[i] : undefined) || "");
const asList = (held) => held.map((question) => question.trim()).filter(Boolean);

const failureReason = (error) => {
  const body = error.response?.data;
  return [body?.detail, body?.message, body?.error].find((value) => typeof value === "string") || "Suggested questions could not be loaded or saved. Try again.";
};

export default function AskSuggestions() {
  const [held, setHeld] = useState({ asset: slots([]), domain: slots([]) });
  const [lastChange, setLastChange] = useState(null);
  const [busy, setBusy] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const take = (data) => {
    setHeld({ asset: slots(data.asset), domain: slots(data.domain) });
    setLastChange(data);
  };

  useEffect(() => {
    getAskSuggestions().then(({ data }) => {
      take(data);
      setLoaded(true);
    }).catch((failure) => setError(failureReason(failure))).finally(() => setBusy(false));
  }, []);

  const edit = (key, index, value) => {
    setHeld((old) => ({ ...old, [key]: old[key].map((question, i) => (i === index ? value : question)) }));
    setNotice("");
  };

  const save = async (event) => {
    event.preventDefault();
    if (busy || !loaded) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { data } = await saveAskSuggestions(asList(held.asset), asList(held.domain));
      take(data);
      setNotice("Suggested questions saved. They apply from the next page load.");
    } catch (failure) {
      setError(failureReason(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="portal-ask-suggestions" aria-label="Suggested questions configuration" aria-busy={busy}>
      <h2>Suggested questions</h2>
      <p>The questions Ask offers before the first one is asked: up to {MAX_QUESTIONS} for a data asset and up to {MAX_QUESTIONS} for a domain, each of up to {MAX_LENGTH} characters. Leave a list empty to keep the standard questions.</p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={save}>
        {LISTS.map(({ key, legend, label }) => (
          <fieldset key={key}>
            <legend>{legend}</legend>
            {held[key].map((question, i) => {
              const id = `portal-ask-suggestion-${key}-${i + 1}`;
              return (
                <React.Fragment key={id}>
                  <label htmlFor={id}>{label(i + 1)}</label>
                  <input id={id} type="text" value={question} maxLength={MAX_LENGTH} disabled={busy || !loaded} onChange={(event) => edit(key, i, event.target.value)} />
                </React.Fragment>
              );
            })}
          </fieldset>
        ))}
        <button className="btn btn-ghost-light" disabled={busy || !loaded}>Save suggested questions</button>
      </form>
      {lastChange?.changed_at ? <p>Last changed by {lastChange.changed_by} at <time dateTime={lastChange.changed_at}>{lastChange.changed_at}</time>.</p> : loaded && <p>No suggested question changes recorded.</p>}
    </section>
  );
}
