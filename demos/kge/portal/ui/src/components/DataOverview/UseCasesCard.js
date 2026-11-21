import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import V3Card from "../shared/V3ProductCard/V3Card";
import { httpsAddress, normaliseUseCase, useCaseCard } from "../UseCases/useCase";
import { getUseCases } from "../../services/user";

/* What an asset is used for, from the pack's catalogue. The Overview card shows
   the first three in the order the pack declares them (title, objective and an
   optional https url), with a link to the Use cases tab when there are more;
   the tab shows each one's card, which opens Discover's search filtered to it. Every value is shown as text. */

export const USE_CASES_SHOWN = 3;

const text = (value) => (typeof value === "string" ? value : "");

export const useCasesOf = (value) => (Array.isArray(value) ? value : []);

export function UseCaseList({ useCases }) {
  return (
    <ul className="use-cases" aria-label="Use cases">
      {useCases.map((entry, index) => (
        <li className="use-case" key={index}>
          <h4>{text(entry?.title)}</h4>
          <p>{text(entry?.objective) || text(entry?.description)}</p>
          {httpsAddress(entry?.url) ? (
            <a href={entry.url} target="_blank" rel="noopener noreferrer">{entry.url}</a>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export default function UseCasesCard({ useCases, onShowAll }) {
  const all = useCasesOf(useCases);
  const shown = all.slice(0, USE_CASES_SHOWN);
  return (
    <div className="card">
      <div className="card-head">
        <h3>Use cases</h3>
      </div>
      {shown.length ? (
        <UseCaseList useCases={shown} />
      ) : (
        <p className="about-text">No use cases recorded yet</p>
      )}
      {all.length > USE_CASES_SHOWN && onShowAll ? (
        <button type="button" className="use-cases-all" onClick={onShowAll}>
          All {all.length} use cases <span aria-hidden="true">→</span>
        </button>
      ) : null}
    </div>
  );
}

/* The asset's use cases in its own order, each as the portal-wide card of the
   same id and title (its linked data products are those the person may read);
   one the portal does not list falls back to the asset's own entry, linked to
   this asset alone. */
const cardKey = ({ id, title }) => JSON.stringify([id, title]);

export function assetUseCases(useCases, asset, listed) {
  const byKey = new Map(useCasesOf(listed).map((entry) => [cardKey(normaliseUseCase(entry)), entry]));
  const self = { id: asset?.PRODUCTID, name: asset?.PRODUCTDISPLAYNAME || asset?.PRODUCTNAME || asset?.PRODUCTID };
  return useCasesOf(useCases)
    .map((entry) => ({ entry, ...normaliseUseCase(entry) }))
    .filter(({ id }) => id)
    .map(({ entry, id, title }) => byKey.get(cardKey({ id, title })) || { ...entry, id, products: self.id ? [self] : [] });
}

export function UseCasesTab({ useCases, asset }) {
  const navigate = useNavigate();
  const [listed, setListed] = useState(null);
  useEffect(() => {
    let live = true;
    getUseCases()
      .then((res) => { if (live) setListed(useCasesOf(res?.data?.useCaseList)); })
      .catch(() => { if (live) setListed([]); });
    return () => { live = false; };
  }, []);
  const entries = listed ? assetUseCases(useCases, asset, listed) : [];
  return (
    <div className="card">
      <div className="card-head">
        <h3>Use cases</h3>
      </div>
      {listed ? (
        <div className="mp-grid">
          {entries.map((entry, index) => (
            <V3Card key={index} headingLevel={4} {...useCaseCard(entry, navigate)} />
          ))}
        </div>
      ) : (
        <p className="about-text" aria-busy="true">Loading use cases…</p>
      )}
    </div>
  );
}
