import React from "react";
import { Button } from "../../../components/shared/Controls";
import MiniGraph from "./MiniGraph";
import { PathSummary } from "./PathCards";
import { citableSources, definitionsOf } from "./chatAnswer";
import { kindWords } from "./chatTools";

/* #622 requirement 3: the compact cards of the non-figure tools, as an Ask answer has its card. Every field is React
   text; none shows an id, a score, SQL or raw JSON. #651 (B9): their small graphs are read-only, with no "Open graph". #626: every card has
   "Open", `onPanel`, which shows it in the side panel with the content it already has; in the panel itself it is drawn
   without. #638: a card whose reply carries explained paths shows them compactly (PathCards.js); the panel's Graph
   path tab shows them whole. */

/** "Open": the card in the side panel (#626). */
export function OpenCard({ onPanel, label = "Open" }) {
  if (!onPanel) return null;
  return <Button type="button" className="cerebro-chat-open" onClick={onPanel}>{label}</Button>;
}

/** explore and explore_question: the node the reply is about, its linked nodes by the status of the link, and the small
 *  graph of them; and its explained paths (#638), where it carries them. */
export function ExploreCard({ explore, graph, onPanel }) {
  const { node, named, links } = explore;
  return (
    <article className="cerebro-chat-card cerebro-tool-card" aria-label="In the knowledge graph" data-tool="explore">
      {node ? (
        <p className="cerebro-compact-label">{node.name} <span className="cerebro-kind">{kindWords(node.kind)}</span></p>
      ) : (
        <p className="cerebro-compact-text">{explore.summary}</p>
      )}
      {named.length > 1 ? <p className="cerebro-compact-text">Also named: {named.slice(1).map((n) => n.name).join(", ")}</p> : null}
      {links.map((l) => (
        <section key={l.status} aria-label={`${l.status} links`}>
          <h3 className="cerebro-tool-head">{l.status.charAt(0).toUpperCase() + l.status.slice(1)} links</h3>
          <ul className="cerebro-tool-list">
            {l.nodes.map((n, i) => <li key={`${i}-${n.name}`}>{n.name} <span className="cerebro-kind">{kindWords(n.kind)}</span></li>)}
          </ul>
        </section>
      ))}
      {graph ? (
        <MiniGraph path={graph} label={`${node?.name || "The question"}'s neighbourhood`} caption="Its links you may read." />
      ) : null}
      <PathSummary paths={explore.paths} />
      <OpenCard onPanel={onPanel} />
    </article>
  );
}

/** read_graph: the page's nodes and their small graph. */
export function GraphPageCard({ page, onPanel }) {
  const shown = page.nodes.slice(0, 8);
  return (
    <article className="cerebro-chat-card cerebro-tool-card" aria-label="In the knowledge graph" data-tool="read_graph">
      <ul className="cerebro-tool-list">
        {shown.map((n) => <li key={n.id}>{n.name} <span className="cerebro-kind">{kindWords(n.kind)}</span></li>)}
      </ul>
      {page.nodes.length > shown.length ? <p className="cerebro-compact-note">And {page.nodes.length - shown.length} more in the graph.</p> : null}
      <MiniGraph path={page.path} label="These nodes in the graph" caption="Nodes and links you may read." />
      <OpenCard onPanel={onPanel} />
    </article>
  );
}

/** search: each hit's title, what it is and its unit in business words, and why it matched. */
export function SearchCard({ hits, onPanel }) {
  return (
    <article className="cerebro-chat-card cerebro-tool-card" aria-label="Search results" data-tool="search">
      <ul className="cerebro-tool-list cerebro-hits">
        {hits.map((h, i) => (
          <li key={`${i}-${h.title}`}>
            <span className="cerebro-hit-title">{h.title}</span>
            <span className="cerebro-kind">{[h.kind, h.unit].filter(Boolean).join(" · ")}</span>
            {h.why ? <span className="cerebro-hit-why">{h.why}</span> : null}
            {h.restricted ? <span className="cerebro-hit-why">You may not read it; ask its steward for access.</span> : null}
          </li>
        ))}
      </ul>
      <OpenCard onPanel={onPanel} />
    </article>
  );
}

const Part = ({ label, items }) => (items.length ? (
  <div><dt>{label}</dt><dd>{items.join(", ")}</dd></div>
) : null);

/** The BFF's authorized, attributed document excerpts, shown as extracts rather than a complete answer. */
export const documentExcerptItems = (documentAnswer) => (Array.isArray(documentAnswer?.excerpts) ? documentAnswer.excerpts : [])
  .filter((entry) => entry && typeof entry.doc === "string" && entry.doc && typeof entry.text === "string" && entry.text.trim());
export const documentIntroText = (documentAnswer) => (typeof documentAnswer?.intro === "string" && documentAnswer.intro.trim()
  ? documentAnswer.intro : "The available sources do not provide a complete answer.");
export function DocumentExcerpts({ documentAnswer }) {
  const excerpts = documentExcerptItems(documentAnswer);
  if (!excerpts.length) return null;
  return (
    <section aria-label="Summary from source excerpts">
      <h3>Summary from source excerpts</h3>
      <ul>{excerpts.map((entry, i) => <li key={`${entry.doc}-${i}`}>
        <span>[{i + 1}] {entry.title ? `${entry.title} · ` : ""}{entry.doc}{entry.attribution ? ` · ${entry.attribution}` : ""}</span>
        <blockquote>{entry.text}</blockquote>
      </li>)}</ul>
    </section>
  );
}

export function DocumentIntro({ documentAnswer }) {
  if (!documentAnswer) return null;
  return <p>{documentIntroText(documentAnswer)}</p>;
}

/** map_term: the term, its data sets, documents, steward and contexts, and its explained paths (#638). */
export function TermMapCard({ map, onPanel }) {
  return (
    <article className="cerebro-chat-card cerebro-tool-card" aria-label="Term map" data-tool="map_term">
      {map.term ? <p className="cerebro-compact-label">{map.term}</p> : null}
      <dl className="cerebro-compact-definitions">
        <Part label="Data sets" items={map.dataSets} />
        <Part label="Documents" items={map.documents} />
        <Part label="Steward" items={map.steward ? [map.steward] : []} />
        <Part label="Contexts" items={map.contexts} />
      </dl>
      <PathSummary paths={map.paths} />
      <OpenCard onPanel={onPanel} />
    </article>
  );
}

/** A get_context reply as a person may read it: each term's meaning and steward, and the citable sources. The package's
 *  own decisions (what it decided, needs or held back, the plan, the certified columns) are not shown. With Open, the
 *  side panel's Definitions tab (#626). */
export function DefinitionCard({ pkg, onPanel }) {
  const terms = definitionsOf(pkg);
  const selected = new Set(documentExcerptItems(pkg?.document_answer).map((entry) => entry.doc));
  const sources = citableSources(pkg).filter((source) => !pkg?.document_answer || selected.has(source.doc));
  if (!terms.length && !sources.length && !pkg?.document_answer) return null;
  return (
    <article className="cerebro-chat-card cerebro-definitions" aria-label="Definitions">
      <DocumentIntro documentAnswer={pkg?.document_answer} />
      {terms.length ? (
        <dl>
          {terms.map((t, i) => (
            <div key={`${i}-${t.term}`} className="cerebro-definition">
              <dt>{t.term}</dt>
              <dd>
                <p>{t.meaning || "No definition is recorded."}</p>
                {t.steward ? <p className="cerebro-steward">Steward: {t.steward}</p> : null}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      {sources.length ? (
        <section aria-label="Sources">
          <h3>Sources</h3>
          <ul>
            {sources.map((s) => (
              <li key={`${s.n}-${s.doc}`}>
                {s.n != null ? `[${s.n}] ` : ""}{s.title ? `${s.title} · ` : ""}<code>{s.doc}{s.version ? ` v${s.version}` : ""}</code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <DocumentExcerpts documentAnswer={pkg?.document_answer} />
      <OpenCard onPanel={onPanel} />
    </article>
  );
}
