import React, { useEffect, useState } from "react";
import ShellPage from "../../components/shell/ShellPage";
import { Button } from "../../components/shared/Controls";
import Workspace from "./Workspace";

export default function ContextApp() {
  const [bootstrap, setBootstrap] = useState(null);
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
        throw Object.assign(new Error(body.detail || "Context is unavailable right now. Try again shortly."), { http: true });
      }
      if (!abort.signal.aborted) setBootstrap(body);
    }).catch((failure) => {
      if (!abort.signal.aborted) setError(failure.http ? failure.message : "Context is unavailable right now. Try again shortly.");
    });
    return () => abort.abort();
  }, [attempt]);
  return <ShellPage app="context" contextPerson={bootstrap?.person}>
    {bootstrap ? <Workspace bootstrap={bootstrap} /> : <main className="gb-status v3-controls" aria-label="Context">
      {error ? <><p role="alert">{error}</p><Button onClick={() => setAttempt((value) => value + 1)}>Retry</Button></> : <p role="status">Opening Context…</p>}
    </main>}
  </ShellPage>;
}
