import React from "react";

/* v3 empty state. Kept prop-compatible (message, optional noDataStyle) with
   the previous Material-UI version so existing call sites keep working. */
const NoDataFound = (props) => {
  return (
    <div className="v3-empty" role="status">
      <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" style={{ marginBottom: 10, color: "var(--v3-line-2)" }}>
        <path d="M3 3v18h18" />
        <path d="m7 14 3-4 3 3 5-7" />
      </svg>
      <div className="v3-empty-msg">{props.message}</div>
    </div>
  );
};

export default NoDataFound;
