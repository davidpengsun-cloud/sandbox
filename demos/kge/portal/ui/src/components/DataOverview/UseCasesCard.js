import React, { useEffect, useState } from "react";
import V3Card from "../shared/V3ProductCard/V3Card";
import { normaliseUseCase, useCaseCard } from "../UseCases/useCase";
import UseCaseDetail from "../UseCases/UseCaseDetail";
import { getUseCases } from "../../services/user";

/* What an asset is used for, from the pack's catalogue: the asset's Use cases
   tab shows each one's card, which opens the use case's detail modal. Every
   value is shown as text. */

export const useCasesOf = (value) => (Array.isArray(value) ? value : []);

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
  const [listed, setListed] = useState(null);
  const [detail, setDetail] = useState(null);
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
            <V3Card key={index} headingLevel={4} {...useCaseCard(entry, setDetail)} />
          ))}
        </div>
      ) : (
        <p className="about-text" aria-busy="true">Loading use cases…</p>
      )}
      {detail ? <UseCaseDetail useCase={detail} onClose={() => setDetail(null)} /> : null}
    </div>
  );
}
