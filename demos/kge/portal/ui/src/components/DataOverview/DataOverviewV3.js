import React, { useEffect } from "react";
import { saveViewedDataProduct } from "../../services/user";
import GoldBadge from "../../assets/icons/gold.png";
import SilverBadge from "../../assets/icons/silver.png";
import BronzeBadge from "../../assets/icons/bronze.png";
import EmptyBadge from "../../assets/icons/empty.png";
import { qualityOf } from "./quality";

/* Overview tab rendered in the v3 "settlement tape" design language, bound to
   the real data product fields. Kept as a sibling of DataOverview.js so the
   original remains available if we need to fall back. */

const initials = (name) =>
  (name || "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("") || "—";

const scoreClass = (score) => {
  const n = Number(score);
  if (Number.isNaN(n)) return "chip-plain";
  if (n >= 90) return "chip-ok";
  if (n >= 10) return "chip-must";
  return "chip-rasp";
};

const scoreTone = (score) => {
  const n = Number(score);
  if (Number.isNaN(n)) return "plain";
  if (n >= 90) return "good";
  if (n >= 10) return "warn";
  return "bad";
};

// Small stroke icons for the four technical-metadata groups.
const ICONS = {
  identity: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="2" /><circle cx="8.5" cy="12" r="2" /><path d="M13.5 10.5H18M13.5 13.5H18" /></svg>
  ),
  scale: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="5" y1="20" x2="5" y2="12" /><line x1="12" y1="20" x2="12" y2="5" /><line x1="19" y1="20" x2="19" y2="15" /></svg>
  ),
  freshness: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>
  ),
  access: (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4.5" y="11" width="15" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>
  ),
};

const Chip = ({ tone = "plain", dot, children }) => (
  <span className={`mt-chip mt-chip--${tone}`}>
    {dot ? <span className="mt-dot" style={{ background: dot }} /> : null}
    {children}
  </span>
);

export default function DataOverviewV3(props) {
  const d = props.data || {};

  useEffect(() => {
    saveViewedDataProduct({
      productId: d.PRODUCTID,
      productName: d.TECHNICALNAME,
      searchWord: null,
      category: "Details",
      tab: "DETAILS-OVERVIEW",
    });
  }, []);

  const path = d.PATH ? d.PATH.substring(d.PATH.indexOf(">") + 1) : "—";
  const columns =
    d.PARENT_DATASET_COUNT_OF_COLUMNS !== null && d.PARENT_DATASET_COUNT_OF_COLUMNS !== undefined
      ? `${d.COUNTOFCOLUMNS} of ${d.PARENT_DATASET_COUNT_OF_COLUMNS}`
      : d.COUNTOFCOLUMNS;
  const quality = qualityOf(d);
  const dq = quality.score;

  const Field = ({ label, children }) => {
    const empty = children === undefined || children === null || children === "";
    return (
      <div className="mt-tile">
        <div className="mt-lbl">{label}</div>
        <div className="mt-val">{empty ? "—" : children}</div>
      </div>
    );
  };

  return (
    <div className="two-col">
      <div>
        <div className="card">
          <div className="card-head">
            <h3>About this data asset</h3>
            {quality.recorded ? (
              <span className={`chip ${scoreClass(dq)}`}>
                <span className="dot dot-aqua" />
                {`DQ ${quality.text}`}
              </span>
            ) : (
              <span className="chip chip-plain" title="No data-quality run is recorded for this product">NO DQ RUN</span>
            )}
          </div>
          <p className="about-text">{d.DESCRIPTION || "No description provided."}</p>
          <div className="tag-row">
            {d.DATA_DOMAIN_NAME && <span className="chip chip-plum">{d.DATA_DOMAIN_NAME.replace(/,/g, " ")}</span>}
            {d.COMMUNITY && <span className="chip chip-plum">{d.COMMUNITY}</span>}
            {d.PRODUCTTYPE && <span className="chip chip-plain">{d.PRODUCTTYPE}</span>}
            {d.DATA_PRODUCT_SOURCE && <span className="chip chip-plain">{d.DATA_PRODUCT_SOURCE}</span>}
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Technical metadata</h3>
          </div>
          <div className="meta-groups">
            <div className="meta-group">
              <div className="meta-ghead"><span className="gi">{ICONS.identity}</span><h4>Identity</h4></div>
              <div className="meta-list">
                <Field label="Technical name">{d.TECHNICALNAME}</Field>
                <Field label="Schema">{d.SCHEMANAME}</Field>
                <Field label="Object type">{d.PRODUCTTYPE ? <Chip tone="plum">{d.PRODUCTTYPE}</Chip> : null}</Field>
                <Field label="Path">{path}</Field>
              </div>
            </div>
            <div className="meta-group">
              <div className="meta-ghead"><span className="gi">{ICONS.scale}</span><h4>Scale</h4></div>
              <div className="meta-list">
                <Field label="Source records (before read filters)">{d.COUNTOFRECORDS}</Field>
                <Field label="Storage">{d.SIZE || "N/A"}</Field>
                <Field label="Columns">{columns}</Field>
                <Field label="Data format">{d.DATA_FORMAT ? <Chip tone="plain">{d.DATA_FORMAT}</Chip> : null}</Field>
              </div>
            </div>
            <div className="meta-group">
              <div className="meta-ghead"><span className="gi">{ICONS.freshness}</span><h4>Freshness</h4></div>
              <div className="meta-list">
                <Field label="Last refresh">{d.LAST_REFRESH}</Field>
                <Field label="Data quality">{quality.recorded ? <Chip tone={scoreTone(dq)}>{quality.text}</Chip> : <span className="muted">Not assessed</span>}</Field>
                {quality.recorded ? <Field label="Last DQ run">{d.DQRUNDATE}</Field> : null}
                <Field label="Portal views">{d.VIEWTIMES === null || d.VIEWTIMES === undefined ? "0" : d.VIEWTIMES}</Field>
              </div>
            </div>
            <div className="meta-group">
              <div className="meta-ghead"><span className="gi">{ICONS.access}</span><h4>Access</h4></div>
              <div className="meta-list">
                <Field label="Source">{d.DATA_PRODUCT_SOURCE ? <Chip tone="aqua" dot="var(--v3-accent)">{d.DATA_PRODUCT_SOURCE}</Chip> : null}</Field>
                <Field label="Community">{d.COMMUNITY}</Field>
                <Field label="Data domain">{d.DATA_DOMAIN_NAME}</Field>
              </div>
            </div>
          </div>
        </div>
      </div>

      <aside className="rail">
        <div className="card">
          <div className="card-head">
            <h3>Ownership</h3>
          </div>
          <div className="person">
            <div className="avatar">{initials(d.BUSINESSOWNER)}</div>
            <div className="person-meta">
              <b>{d.BUSINESSOWNER || "Unassigned"}</b>
              <span>Data domain owner</span>
            </div>
          </div>
          <div className="person">
            <div className="avatar" style={{ background: "var(--v3-azure-700)" }}>{initials(d.PRODUCTOWNER)}</div>
            <div className="person-meta">
              <b>{d.PRODUCTOWNER || "Unassigned"}</b>
              <span>Data steward · approves access</span>
            </div>
          </div>
          <div style={{ marginTop: "12px", paddingTop: "12px", borderTop: "1px solid var(--v3-line)" }}>
            <div className="lbl">Community</div>
            <div className="val" style={{ fontSize: "13px" }}>{d.COMMUNITY || "—"}</div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Quality tiers</h3>
          </div>
          <div className="legend">
            <div><img className="tier-medal-sm" src={GoldBadge} alt="Gold" /><span><b>Gold</b> — owned, documented, monitored</span></div>
            <div><img className="tier-medal-sm" src={SilverBadge} alt="Silver" /><span><b>Silver</b> — owned and documented</span></div>
            <div><img className="tier-medal-sm" src={BronzeBadge} alt="Bronze" /><span><b>Bronze</b> — catalogued only</span></div>
            <div><img className="tier-medal-sm" src={EmptyBadge} alt="No quality tier" /><span>No quality tier recorded</span></div>
          </div>
        </div>
      </aside>
    </div>
  );
}
