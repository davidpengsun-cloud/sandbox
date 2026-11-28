import React, { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { exportOdcs, normaliseUseCase, useCaseSearchUrl } from "./useCase";
import "./UseCaseDetail.css";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function Facts({ rows }) {
  const shown = rows.filter(([, value]) => value);
  if (!shown.length) return null;
  return (
    <dl className="uc-facts">
      {shown.map(([name, value]) => (
        <div className="uc-fact" key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/* The whole use case in a modal dialog. Focus moves into it and returns to the
   control that opened it; Escape and Close close it. A linked data product
   opens its page; "Show assets in Discover" opens Discover's search filtered to
   the use case; "Export as ODCS" downloads the use case as a data contract. */
export default function UseCaseDetail({ useCase, onClose }) {
  const entry = normaliseUseCase(useCase);
  const titleId = useId();
  const dialogRef = useRef(null);

  useEffect(() => {
    const opener = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (opener && opener !== document.body && opener.isConnected) opener.focus();
    };
  }, []);

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !dialogRef.current) return;
    const controls = [...dialogRef.current.querySelectorAll(FOCUSABLE)];
    if (!controls.length) return;
    const index = controls.indexOf(document.activeElement);
    if (index < 0 || (event.shiftKey && index === 0) || (!event.shiftKey && index === controls.length - 1)) {
      event.preventDefault();
      controls[event.shiftKey ? controls.length - 1 : 0].focus();
    }
  };

  return createPortal(
    <div className="uc-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="uc-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} ref={dialogRef} onKeyDown={onKeyDown}>
        <header className="uc-dialog-head">
          <h2 id={titleId}>{entry.title}</h2>
          {entry.id ? <p className="uc-dialog-id">ID {entry.id}</p> : null}
          <Facts rows={[["Domain", entry.domain], ["Owner", entry.owner]]} />
        </header>
        {entry.objective || entry.url ? (
          <section className="uc-dialog-block">
            <h3>Objective</h3>
            {entry.objective ? <p className="uc-dialog-text">{entry.objective}</p> : null}
            {entry.url ? <a href={entry.url} target="_blank" rel="noopener noreferrer">{entry.url}</a> : null}
          </section>
        ) : null}
        {entry.kpi ? (
          <section className="uc-dialog-block">
            <h3>KPI</h3>
            <Facts rows={[["Name", entry.kpi.name], ["Definition", entry.kpi.definition], ["Grain", entry.kpi.grain], ["Unit", entry.kpi.unit]]} />
          </section>
        ) : null}
        {entry.exampleQuestions.length ? (
          <section className="uc-dialog-block">
            <h3>Example questions</h3>
            <ul className="uc-questions" aria-label="Example questions">
              {entry.exampleQuestions.map((question, index) => (
                <li key={index} className="uc-question">{question}</li>
              ))}
            </ul>
          </section>
        ) : null}
        {entry.products.length ? (
          <section className="uc-dialog-block">
            <h3>Linked data products</h3>
            <ul className="uc-products" aria-label="Linked data products">
              {entry.products.map((product, index) => (
                <li key={`${product.id}-${index}`}>
                  <span id={`${titleId}-p${index}`} className="uc-product-name">{product.name}</span>
                  <Link className="uc-product-link" to={`/productDetails/${encodeURIComponent(product.id)}`} aria-describedby={`${titleId}-p${index}`} onClick={onClose}>View product</Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        <footer className="uc-dialog-foot">
          <Link className="uc-btn" to={useCaseSearchUrl(entry)} onClick={onClose}>Show assets in Discover</Link>
          <button type="button" className="uc-btn" onClick={() => exportOdcs(entry)}>Export as ODCS</button>
          <button type="button" className="uc-btn uc-btn-primary" onClick={onClose}>Close</button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
