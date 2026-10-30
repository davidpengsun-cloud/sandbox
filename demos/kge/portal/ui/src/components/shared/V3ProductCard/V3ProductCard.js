import React from "react";
import { useNavigate } from "react-router-dom";
import { useDispatch } from "react-redux";
import { selectedProduct, selectedProductName } from "../../../features/dataProduct/dataProductSlice";
import GoldBadge from "../../../assets/icons/gold.png";
import SilverBadge from "../../../assets/icons/silver.png";
import BronzeBadge from "../../../assets/icons/bronze.png";
import "./V3ProductCard.css";

/* Reusable "settlement tape" product card, bound to the real search-result
   shape. Shared by the v3 Home and Explore pages. Self-contained class names
   (mp-*) so it renders identically regardless of the page it sits in. */

const BADGE = {
  GOLD: { icon: GoldBadge, label: "Gold quality tier", rail: "mp-card--g" },
  SILVER: { icon: SilverBadge, label: "Silver quality tier", rail: "mp-card--s" },
  BRONZE: { icon: BronzeBadge, label: "Bronze quality tier", rail: "mp-card--b" },
};

const badgeInfo = (name) =>
  BADGE[(name || "").toUpperCase()] || { icon: null, label: "No quality tier", rail: "mp-card--n" };

/* Backend names arrive in mixed casing ("SECURITY MASTER" next to
   "Fund Master"). Normalise fully-uppercase names to title case for display
   without touching the underlying data. */
export const titleCaseName = (name) => {
  const t = String(name || "").trim();
  if (!t) return "";
  if (t === t.toUpperCase() && /[A-Z]/.test(t)) {
    return t
      .toLowerCase()
      .replace(/(^|[\s\-_/(.])([a-z])/g, (m, pre, ch) => pre + ch.toUpperCase());
  }
  return t;
};

const HeartIcon = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 20.3 4.6 13a4.6 4.6 0 0 1 6.5-6.5l.9.9.9-.9A4.6 4.6 0 0 1 19.4 13z" />
  </svg>
);

export default function V3ProductCard({ product, onToggleFavourite }) {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  if (!product) return null;

  const badge = badgeInfo(product.BADGE_NAME);
  const title = titleCaseName(product.PRODUCTDISPLAYNAME || product.PRODUCTNAME) || "Untitled";
  const eyebrowText =
    [product.PRODUCTTYPE, product.DOMAIN].filter(Boolean).join(" · ") ||
    product.COMMUNITY ||
    "Data asset";
  const likes =
    product.SUBSCRIBE_COUNT === null || product.SUBSCRIBE_COUNT === undefined ? 0 : product.SUBSCRIBE_COUNT;
  const isFav = product.FAVOURITED || product.ISFAVOURITE === "Y";

  const open = () => {
    if (product.TYPE === "column") return;
    dispatch(selectedProduct(product.PRODUCTID));
    dispatch(selectedProductName(product.PRODUCTNAME));
    navigate(`/productDetails/${product.PRODUCTID}`);
  };

  const key = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  const toggleFav = (e) => {
    e.stopPropagation();
    if (onToggleFavourite) onToggleFavourite(product);
  };

  return (
    <article
      className={`mp-card ${badge.rail}`}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={key}
      aria-label={`Open ${title}`}
    >
      <div className="mp-card__body">
        <div className="mp-card__eyebrow">
          {badge.icon ? (
            <img className="mp-tier-img" src={badge.icon} alt={badge.label} title={badge.label} />
          ) : null}
          <span className="mp-card__kind">{eyebrowText}</span>
        </div>
        <h3 className="mp-card__title">{title}</h3>
        {product.DESCRIPTION ? <p className="mp-card__desc">{product.DESCRIPTION}</p> : null}
        <div className="mp-card__facts">
          {product.PRODUCTSOURCE && (
            <span className="mp-chip mp-chip-aqua">
              <span className="mp-chip-dot" aria-hidden="true" />
              {product.PRODUCTSOURCE}
            </span>
          )}
          {product.COMMUNITY && <span className="mp-chip mp-chip-plum">{product.COMMUNITY}</span>}
        </div>
      </div>
      <div className="mp-card__foot">
        {product.LAST_REFRESH ? (
          <span className="mp-card__meta" title="Last refresh">◷ {product.LAST_REFRESH}</span>
        ) : badge.icon ? (
          <span className="mp-card__meta">{badge.label.toUpperCase()}</span>
        ) : null}
        {onToggleFavourite && product.TYPE !== "column" ? (
          <button
            className="mp-heart"
            aria-pressed={isFav}
            aria-label={isFav ? `Unfavourite ${title}` : `Favourite ${title}`}
            onClick={toggleFav}
          >
            <HeartIcon />
            {likes}
          </button>
        ) : (
          <span className="mp-card__meta mp-card__meta--right">♥ {likes}</span>
        )}
      </div>
    </article>
  );
}
