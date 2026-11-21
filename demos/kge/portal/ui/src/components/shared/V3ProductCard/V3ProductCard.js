import React from "react";
import { useNavigate } from "react-router-dom";
import { useDispatch } from "react-redux";
import { selectedProduct, selectedProductName } from "../../../features/dataProduct/dataProductSlice";
import GoldBadge from "../../../assets/icons/gold.png";
import SilverBadge from "../../../assets/icons/silver.png";
import BronzeBadge from "../../../assets/icons/bronze.png";
import V3Card, { NO_TIER_RAIL } from "./V3Card";

/* The product binding of the shared card (V3Card): maps the real
   search-result shape to the card's eyebrow, title, meta, description, chips
   and foot, and opens the product's page. Shared by the v3 Home and Explore
   pages. */

const BADGE = {
  GOLD: { icon: GoldBadge, label: "Gold quality tier", rail: "mp-card--g" },
  SILVER: { icon: SilverBadge, label: "Silver quality tier", rail: "mp-card--s" },
  BRONZE: { icon: BronzeBadge, label: "Bronze quality tier", rail: "mp-card--b" },
};

const badgeInfo = (name) =>
  BADGE[(name || "").toUpperCase()] || { icon: null, label: "No quality tier", rail: NO_TIER_RAIL };

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
    [product.PRODUCTTYPE === "API/MCP" ? "Connector" : product.PRODUCTTYPE, product.DOMAIN].filter(Boolean).join(" · ") ||
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

  const toggleFav = (e) => {
    e.stopPropagation();
    if (onToggleFavourite) onToggleFavourite(product);
  };

  const chips = [
    product.PRODUCTSOURCE && { key: "source", tone: "aqua", dot: true, label: product.PRODUCTSOURCE },
    product.COMMUNITY && { key: "community", tone: "plum", label: product.COMMUNITY },
  ].filter(Boolean);

  const foot = product.LAST_REFRESH ? (
    <span className="mp-card__meta" title="Last refresh">◷ {product.LAST_REFRESH}</span>
  ) : badge.icon ? (
    <span className="mp-card__meta">{badge.label.toUpperCase()}</span>
  ) : null;

  const footEnd =
    onToggleFavourite && product.TYPE !== "column" ? (
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
    );

  return (
    <V3Card
      rail={badge.rail}
      tier={badge.icon ? badge : null}
      eyebrow={eyebrowText}
      title={title}
      meta={product.CATALOGUE_STATE}
      description={product.DESCRIPTION}
      chips={chips}
      foot={foot}
      footEnd={footEnd}
      onOpen={open}
    />
  );
}
