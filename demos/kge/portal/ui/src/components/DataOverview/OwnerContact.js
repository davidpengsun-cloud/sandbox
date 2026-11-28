import React, { useId, useState } from "react";

export const teamsChatUrl = (email) => `https://teams.microsoft.com/l/chat/0/0?users=${encodeURIComponent(email)}`;

// An owner's name on the Ownership card. Hovering or focusing it opens a
// contact card: the name, the role, the email as a mailto link and a Teams
// chat action, or a note that no email is on record. Escape closes it.
export default function OwnerContact({ name, role, email }) {
  const [open, setOpen] = useState(false);
  const cardId = useId();
  const shown = name || "Unassigned";
  const address = typeof email === "string" ? email.trim() : "";
  const leave = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  };
  return (
    <span
      className="owner-contact"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onBlur={leave}
      onKeyDown={(event) => {
        if (event.key === "Escape") setOpen(false);
      }}
    >
      <button
        type="button"
        className="owner-contact-trigger"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? cardId : undefined}
        onFocus={() => setOpen(true)}
        onClick={() => setOpen(true)}
      >
        {shown}
      </button>
      {open ? (
        <span className="owner-contact-card" role="dialog" id={cardId} aria-label={`Contact ${shown}`}>
          <b>{shown}</b>
          <span>{role}</span>
          {address ? (
            <>
              <a href={`mailto:${address}`}>{address}</a>
              <a href={teamsChatUrl(address)} target="_blank" rel="noopener noreferrer">Chat in Teams</a>
            </>
          ) : (
            <span className="owner-contact-none">No email on record</span>
          )}
        </span>
      ) : null}
    </span>
  );
}
