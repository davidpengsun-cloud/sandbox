import React from "react";
import { SPRITE } from "./sprite";

// The drawing's symbols, once per page, hidden from screen readers; Icon and Logo draw from them.
export function Symbols() {
  return <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: `<defs>${SPRITE}</defs>` }} />;
}

export function Icon({ name, size = 15, className }) {
  return (
    <svg aria-hidden="true" focusable="false" width={size} height={size} className={className} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <use href={`#kh-i-${name}`} />
    </svg>
  );
}

export function Logo({ name }) {
  return (
    <svg aria-hidden="true" focusable="false" width="24" height="24" className="kh-logo-mark" fill="currentColor" data-logo={name}>
      <use href={`#kh-b-${name}`} />
    </svg>
  );
}
