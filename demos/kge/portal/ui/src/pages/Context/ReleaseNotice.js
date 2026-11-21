import React, { useEffect, useRef, useState } from "react";
import { Button } from "../../components/shared/Controls";
import useDisclosure from "../../components/shell/useDisclosure";

/* The release evaluation's notice (#427) and "About this release" behind an ⓘ at the end of the tab bar.
   A passed evaluation is a toast for about ten seconds, shown once per release for a person in a browser;
   a failed or unfinished one is a red row above the tabs until that person closes it, and comes back for
   the next release. Which release a person has closed is kept in this browser's storage; a storage error
   never breaks the page, the choice then holds for this page only. */

export const NOTICE_MS = 10000;
const storageKey = (person) => `context-release-eval:${person || ""}`;

export function closedRelease(person) {
  try {
    return window.localStorage.getItem(storageKey(person));
  } catch {
    return null;
  }
}

export function closeRelease(person, release) {
  try {
    window.localStorage.setItem(storageKey(person), release);
  } catch {
    /* the choice holds for this page only */
  }
}

export const releaseOf = (evaluation) =>
  evaluation ? JSON.stringify([evaluation.identity ?? null, evaluation.at ?? null, evaluation.message ?? null]) : "";

export function evaluationWords(evaluation) {
  if (evaluation?.passed === true)
    return evaluation.compared === false
      ? { passed: true, title: "Release evaluation passed", detail: "No earlier results to compare.", summary: "passed · no earlier results to compare" }
      : { passed: true, title: "Release evaluation passed", detail: "Results unchanged.", summary: "passed · results unchanged" };
  if (Array.isArray(evaluation?.differences) && evaluation.differences.length)
    return { passed: false, title: "Release evaluation failed:", detail: "answers changed after the release.", summary: "failed · answers changed after the release" };
  return { passed: false, title: "Release evaluation did not finish:", detail: "the release is served without its check.", summary: "did not finish" };
}

export function useReleaseEvaluation(evaluation, person) {
  const release = releaseOf(evaluation);
  const passed = evaluation?.passed === true;
  const [shown, setShown] = useState("");
  const [closed, setClosed] = useState("");
  const done = useRef(new Set());
  useEffect(() => {
    if (!release || !passed || done.current.has(release) || closedRelease(person) === release) return;
    done.current.add(release);
    closeRelease(person, release);
    setShown(release);
  }, [release, passed, person]);
  useEffect(() => {
    if (!shown) return undefined;
    const timer = setTimeout(() => setShown(""), NOTICE_MS);
    return () => clearTimeout(timer);
  }, [shown]);
  const failedOpen = Boolean(release) && !passed && closed !== release && !done.current.has(release) && closedRelease(person) !== release;
  return {
    words: evaluation ? evaluationWords(evaluation) : null,
    toast: Boolean(release) && passed && shown === release,
    failed: failedOpen,
    closeToast: () => setShown(""),
    closeFailed: () => { done.current.add(release); closeRelease(person, release); setClosed(release); },
  };
}

function Close({ label, onClick }) {
  return <Button className="notice-close" aria-label={label} onClick={onClick}><span aria-hidden="true">✕</span></Button>;
}

export function FailedEvaluation({ notice, onQuality }) {
  if (!notice.failed) return null;
  return <div id="release-eval-banner" className="bad" role="alert">
    <p><b>{notice.words.title}</b> {notice.words.detail}{onQuality && <> <a href="#view-quality" onClick={(event) => { event.preventDefault(); onQuality(); }}>Open in Quality</a></>}</p>
    <Close label="Close the release evaluation notice" onClick={notice.closeFailed} />
  </div>;
}

export function PassedEvaluation({ notice, quality }) {
  return <div className="release-toast-region" role="status">
    {notice.toast && <div id="release-eval-notice" className="release-toast">
      <span className="tick" aria-hidden="true">✓</span>
      <div className="release-toast-body"><b>{notice.words.title}</b><span>{notice.words.detail}{quality && " Details in Quality."}</span>
        <span className="release-toast-bar" aria-hidden="true" /></div>
      <Close label="Close the release evaluation notice" onClick={notice.closeToast} />
    </div>}
  </div>;
}

export function AboutRelease({ profile, version, baseline, evaluation }) {
  const about = useDisclosure();
  const words = evaluation ? evaluationWords(evaluation) : null;
  return <div className="about-release" ref={about.wrapRef}>
    <Button ref={about.triggerRef} id="about-release" className="about-trigger" aria-haspopup="dialog" aria-expanded={about.open}
      aria-controls="about-build" aria-label="About this release" onClick={about.toggle}><span aria-hidden="true">i</span></Button>
    <div id="about-build" className="about-panel" role="dialog" aria-labelledby="about-build-title" ref={about.panelRef} tabIndex={-1} hidden={!about.open}>
      <h2 id="about-build-title">About this release</h2>
      <dl className="about-facts">
        <dt>Release</dt><dd>{profile?.release ? <><span data-fact="release">{profile.release.id}</span> · as of <span data-fact="as_of">{profile.release.as_of}</span></> : <span data-fact="release">none</span>}</dd>
        <dt>Client</dt><dd><span data-fact="client">{profile?.client}</span> · profile <span data-fact="profile">{profile?.profile}</span></dd>
        <dt>Artifact</dt><dd data-fact="artifact">{String(profile?.artifact || "none").slice(0, 12)}</dd>
        <dt>Version</dt><dd id="version">{version}</dd>
        {words && <><dt>Evaluation</dt><dd id="about-eval" className={words.passed ? "ok" : "bad"}>{words.passed && <span aria-hidden="true">✓ </span>}{words.summary}</dd></>}
        <dt>Baseline</dt><dd id="about-ab">{baseline ? "Quality compares results with the release baseline." : "No release baseline yet."}</dd>
      </dl>
    </div>
  </div>;
}
