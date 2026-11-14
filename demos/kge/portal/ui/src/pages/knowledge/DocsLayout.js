import React from "react";
import { Link } from "react-router-dom";
import { DOCS_AT_A_GLANCE, DOCS_START_HERE } from "./content";
import "./docs.css";

const PAGES = [
  { to: DOCS_AT_A_GLANCE, label: "At a glance", key: "at-a-glance" },
  { to: DOCS_START_HERE, label: "Start here", key: "start-here" },
];

// An "On this page" link moves to its section and focuses the heading, without touching the route.
function jump(event, id) {
  const target = document.getElementById(id);
  if (!target) return;
  event.preventDefault();
  target.scrollIntoView?.({ block: "start" });
  target.focus({ preventScroll: true });
}

// The Knowledge docs' frame: the docs' own navigation, the page, and its table of contents.
export default function DocsLayout({ current, sections, children }) {
  return (
    <div className="kd-wrap">
      <nav className="kd-side" aria-label="Docs">
        <p className="kd-grp">Docs</p>
        <ul>
          {PAGES.map((page) => (
            <li key={page.key}><Link to={page.to} aria-current={page.key === current ? "page" : undefined}>{page.label}</Link></li>
          ))}
        </ul>
        <p className="kd-grp kd-later">Each area's own docs</p>
        <ul className="kd-later">
          <li><span className="kd-soon">Certify<em>in the Certify app</em></span></li>
          <li><span className="kd-soon">Context<em>in the Context app</em></span></li>
          <li><span className="kd-soon">Discover<em>in the portal</em></span></li>
        </ul>
        <p className="kd-grp kd-later">To come</p>
        <ul className="kd-later"><li><span className="kd-soon">What's new<em>to come</em></span></li></ul>
      </nav>
      <article className="kd-doc">
        <p className="kd-crumb">Docs</p>
        {children}
      </article>
      <aside className="kd-toc" aria-label="On this page">
        <p>On this page</p>
        <ol>
          {sections.map((section) => (
            <li key={section.id}><a href={`#${section.id}`} onClick={(event) => jump(event, section.id)}>{section.title}</a></li>
          ))}
        </ol>
      </aside>
    </div>
  );
}

// A section heading the table of contents can move focus to.
export function Section({ section }) {
  return <h2 id={section.id} tabIndex={-1}>{section.title}</h2>;
}

// A table that scrolls inside its own box on a narrow screen, never the page.
export function Table({ head, rows }) {
  return (
    <div className="kd-tbl" tabIndex={0} role="group" aria-label={head.join(", ")}>
      <table>
        <thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}
