import React, { useEffect, useState } from "react";
import ShellPage from "../../components/shell/ShellPage";
import { Button } from "../../components/shared/Controls";
import ContextScreens from "./ContextScreens";
import { APP_NAMES } from "../../components/shell/apps";
import { homeTab } from "./contextState";

export default function ContextApp() {
  const [bootstrap, setBootstrap] = useState(null);
  const [askActive, setAskActive] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    fetch("/api/context/bootstrap", { signal: abort.signal }).then(async (response) => {
      const body = await response.json();
      if (!response.ok) {
        if (response.status === 401) {
          await fetch("/session", { method: "DELETE", signal: abort.signal });
          window.location.assign("/");
          return;
        }
        throw Object.assign(new Error(body.detail || `${APP_NAMES.context} is unavailable right now. Try again shortly.`), { http: true });
      }
      if (!abort.signal.aborted) {
        setAskActive(homeTab(body?.person) === "ask");
        setBootstrap(body);
      }
    }).catch((failure) => {
      if (!abort.signal.aborted) setError(failure.http ? failure.message : `${APP_NAMES.context} is unavailable right now. Try again shortly.`);
    });
    return () => abort.abort();
  }, [attempt]);
  return <ShellPage app="context" contextPerson={bootstrap?.person} fill={Boolean(bootstrap && askActive)}>
    {bootstrap ? <ContextScreens bootstrap={bootstrap} onViewChange={(view) => setAskActive(view === "ask")} /> : <main className="gb-status v3-controls" aria-label={APP_NAMES.context}>
      {error ? <><p role="alert">{error}</p><Button onClick={() => setAttempt((value) => value + 1)}>Retry</Button></> : <p role="status">Opening {APP_NAMES.context}…</p>}
    </main>}
  </ShellPage>;
}
