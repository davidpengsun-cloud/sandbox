import React, { useEffect, useRef, useState } from "react";

let current = { notice: "", hits: [] };
let epoch = 0;
export const knowledgeEpoch = () => epoch;
export function clearKnowledge() {
  epoch += 1;
  current = { notice: "", hits: [] };
  window.dispatchEvent(new CustomEvent("portal-knowledge", { detail: current }));
}
export function publishKnowledge(response, requestEpoch = epoch) {
  if (requestEpoch !== epoch) return;
  const notice = response.headers?.["x-portal-notice"] ?? response.data?.notice;
  const hits = response.data?.hits;
  if (notice === undefined && hits === undefined) return;
  current = { notice: notice ?? current.notice, hits: hits ?? current.hits };
  window.dispatchEvent(new CustomEvent("portal-knowledge", { detail: current }));
}
function useKnowledge() {
  const [value, setValue] = useState(current);
  useEffect(() => {
    const receive = (event) => setValue(event.detail);
    window.addEventListener("portal-knowledge", receive);
    return () => window.removeEventListener("portal-knowledge", receive);
  }, []);
  return value;
}
export function KnowledgeNotice() {
  const { notice } = useKnowledge();
  return notice ? (
    <div className="v3-notice v3-notice-warn" role="status" aria-live="polite">
      <b className="v3-notice-word">Notice</b> <span>{notice}</span>
    </div>
  ) : null;
}
export function KnowledgeMatches() {
  const { hits } = useKnowledge();
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);
  if (!hits.length) return null;

  const closeIfUnfocused = () => {
    if (!wrapperRef.current?.contains(document.activeElement)) setOpen(false);
  };

  return (
    <span
      className="cx-match-help"
      ref={wrapperRef}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={closeIfUnfocused}
    >
      <button
        type="button"
        aria-label="Why these results matched"
        aria-expanded={open}
        aria-controls="cx-match-tooltip"
        aria-describedby="cx-match-tooltip"
        onFocus={() => setOpen(true)}
        onBlur={(event) => {
          if (!event.currentTarget.parentElement?.contains(event.relatedTarget)) setOpen(false);
        }}
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      <span id="cx-match-tooltip" className="cx-match-tooltip" role="tooltip" hidden={!open}>
        <strong>Why these results matched</strong>
        <ul>
          {hits.map((hit) => (
            <li key={`${hit.unit}:${hit.kind}:${hit.id}`}>
              <strong>{hit.title}</strong>
              <span className="cx-match-tooltip-meta">{hit.kind} · {hit.unit} · {Math.round(hit.score * 100)}%</span>
              <span className="cx-match-tooltip-why">
                {hit.why?.kind ? `${hit.why.kind}: ` : ""}{hit.why?.text ?? "No additional reason provided."}
                {hit.restricted && <b> · Restricted column</b>}
              </span>
            </li>
          ))}
        </ul>
      </span>
    </span>
  );
}
