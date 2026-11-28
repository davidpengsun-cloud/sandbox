import React from "react";
import "./V3ProductCard.css";

/* The "settlement tape" card's presentation, shared by data products and use
   cases: a whole-card button with a rail, an eyebrow, a title, meta, a
   description, chips and a foot. It knows nothing of what it shows; a binding
   (V3ProductCard for a product) maps its record to these props. Every value
   is rendered as text. Enter and Space open the card as a click does; its
   accessible name is label, else "Open <title>".

   An optional action (a button of its own, such as a use case's export) is
   drawn at the end of the foot but sits beside the card, not in it: what a
   role="button" holds is presentational to assistive technology, so a button
   inside it is hidden from some screen readers. An invisible copy keeps its
   room in the foot, so the card looks as it would with the action inside it,
   and the card's own click and keys never reach the action. Without an action
   (a product card) the card is drawn as before. */

export const NO_TIER_RAIL = "mp-card--n";

export default function V3Card({
  rail = NO_TIER_RAIL,
  tier = null,
  eyebrow,
  title,
  headingLevel = 3,
  meta,
  description,
  chips = [],
  foot = null,
  footEnd = null,
  onOpen,
  label,
  action = null,
}) {
  const Heading = `h${headingLevel}`;
  const open = () => onOpen?.();
  const key = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };

  const card = (
    <article
      className={`mp-card ${rail}`}
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={key}
      aria-label={label || `Open ${title}`}
    >
      <div className="mp-card__body">
        <div className="mp-card__eyebrow">
          {tier ? <img className="mp-tier-img" src={tier.icon} alt={tier.label} title={tier.label} /> : null}
          <span className="mp-card__kind">{eyebrow}</span>
        </div>
        <Heading className="mp-card__title">{title}</Heading>
        {meta ? <p className="mp-card__meta">{meta}</p> : null}
        {description ? <p className="mp-card__desc">{description}</p> : null}
        <div className="mp-card__facts">
          {chips.map((chip) => (
            <span key={chip.key} className={`mp-chip mp-chip-${chip.tone}`}>
              {chip.dot ? <span className="mp-chip-dot" aria-hidden="true" /> : null}
              {chip.label}
            </span>
          ))}
        </div>
      </div>
      <div className="mp-card__foot">
        {foot}
        {footEnd}
        {action ? <span className="mp-card__action-room" aria-hidden="true" style={{ visibility: "hidden" }}>{action}</span> : null}
      </div>
    </article>
  );
  if (!action) return card;
  return (
    <div className="mp-card-wrap">
      {card}
      <div className="mp-card__float">{action}</div>
    </div>
  );
}
