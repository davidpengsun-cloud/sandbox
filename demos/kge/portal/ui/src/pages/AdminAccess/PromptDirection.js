import React, { useEffect, useState } from "react";
import { getPromptDirection, savePromptDirection } from "../../services/admin";

const failureReason = (error) => {
  const body = error.response?.data;
  return [body?.detail, body?.message, body?.error].find((value) => typeof value === "string") || "Prompt direction could not be loaded or saved. Try again.";
};

export default function PromptDirection() {
  const [text, setText] = useState("");
  const [lastChange, setLastChange] = useState(null);
  const [busy, setBusy] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    getPromptDirection().then(({ data }) => {
      setText(data.text);
      setLastChange(data);
      setLoaded(true);
    }).catch((failure) => setError(failureReason(failure))).finally(() => setBusy(false));
  }, []);

  const save = async (event) => {
    event.preventDefault();
    if (busy || !loaded || text.length > 2000) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const { data } = await savePromptDirection(text);
      setText(data.text);
      setLastChange(data);
      setNotice("Prompt direction saved. It applies from the next question.");
    } catch (failure) {
      setError(failureReason(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="portal-prompt-direction" aria-label="Prompt direction configuration" aria-busy={busy}>
      <h2>Prompt direction</h2>
      <p>Steer the portal catalogue agent from the next question. Its fixed rules, readable catalogue and figure limits always apply. Leave empty for no direction.</p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={save}>
        <label htmlFor="portal-prompt-direction">Prompt direction</label>
        <textarea id="portal-prompt-direction" value={text} onChange={(event) => { setText(event.target.value); setNotice(""); }} maxLength={2000} rows={6} disabled={busy || !loaded} aria-describedby="portal-prompt-direction-limit" />
        <p id="portal-prompt-direction-limit">{text.length} / 2000 characters</p>
        <button className="btn btn-ghost-light" disabled={busy || !loaded || text.length > 2000}>Save prompt direction</button>
      </form>
      {lastChange?.changed_at ? <p>Last changed by {lastChange.changed_by} at <time dateTime={lastChange.changed_at}>{lastChange.changed_at}</time>.</p> : loaded && <p>No prompt direction changes recorded.</p>}
    </section>
  );
}
