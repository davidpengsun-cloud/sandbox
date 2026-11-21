import React from "react";
import { buildSearchUrl } from "../../features/search/searchUrl";
import { exportUseCaseOdcs } from "../../services/exports";
import { showToast } from "../shared/V3Toast/V3Toast";

/* One use case as the portal shows it: the catalogue's resolved entry (or a
   legacy {title, description, url}) with the data products it links. Absent
   values are empty, so the views hide them; every value is plain text. */

const text = (value) => (typeof value === "string" && value.trim() ? value : "");

export const httpsAddress = (value) => typeof value === "string" && /^https:\/\/\S+$/.test(value);

export const useCaseSlug = (title) => title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || [...title.trim()].map((character) => character.codePointAt(0).toString(16)).join("-");

export function normaliseUseCase(entry) {
  const value = entry && typeof entry === "object" ? entry : {};
  const title = text(value.title);
  const kpi = value.kpi && typeof value.kpi === "object" && text(value.kpi.name)
    ? { name: value.kpi.name, definition: text(value.kpi.definition), grain: text(value.kpi.grain), unit: text(value.kpi.unit) }
    : null;
  return {
    id: text(value.id) || (title ? `uc-${useCaseSlug(title)}` : ""),
    title,
    objective: text(value.objective) || text(value.description),
    domain: text(value.domain),
    owner: text(value.owner),
    url: httpsAddress(value.url) ? value.url : "",
    kpi,
    exampleQuestions: (Array.isArray(value.example_questions) ? value.example_questions : []).filter((question) => text(question)),
    products: (Array.isArray(value.products) ? value.products : [])
      .filter((product) => product && text(product.id))
      .map((product) => ({ id: product.id, name: text(product.name) || product.id })),
  };
}

export const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;

/* A use case drawn by the shared card (V3Card): eyebrow "Use case" and its
   domain, the title, the owner as meta, the objective as the description, the
   KPI's name as a chip, and in the foot how many linked data assets the
   person may read. It has no quality tier, so the card keeps the no-tier
   rail. A click opens Discover's search filtered to the use case. The card's
   action, drawn at the end of its foot, is the "Export as ODCS" button, which
   downloads the use case as a data contract. V3Card puts it beside the card
   rather than inside it, so screen readers expose it and its click and keys
   never open the search. */
export function useCaseCard(useCase, navigate) {
  const entry = normaliseUseCase(useCase);
  return {
    eyebrow: ["Use case", entry.domain].filter(Boolean).join(" · "),
    title: entry.title,
    label: `Show assets for use case ${entry.title}`,
    meta: entry.owner,
    description: entry.objective,
    chips: entry.kpi ? [{ key: "kpi", tone: "plum", label: entry.kpi.name }] : [],
    foot: <span className="mp-card__meta">{plural(entry.products.length, "asset", "assets")}</span>,
    action: <OdcsExport entry={entry} />,
    onOpen: () => navigate(useCaseSearchUrl(entry)),
  };
}

function OdcsExport({ entry }) {
  const run = () => {
    exportUseCaseOdcs(entry).catch(() => showToast("The ODCS export could not be downloaded"));
  };
  return (
    <button type="button" className="mp-card__action" aria-label={`Export as ODCS: ${entry.title}`} title="Download this use case as an ODCS data contract" onClick={run}>
      Export as ODCS
    </button>
  );
}

export const useCaseSearchUrl = (entry) => buildSearchUrl({ useCase: entry.title || entry.id });
