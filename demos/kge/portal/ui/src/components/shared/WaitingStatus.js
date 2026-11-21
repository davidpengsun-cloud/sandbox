import React from "react";
import { usePhone as useMediaQuery } from "../shell/useDisclosure";
import "./Controls.css";

/* The shared waiting state of an ask: a spinning glyph in front of the status text, and the
   text pulses. Both move in CSS alone, so the text itself never changes and the one live
   region announces it once. `detail` (the pipeline's current step, say) shows beside the
   text but outside the live region, so step changes are not read out again. With reduced
   motion the animation classes are left off, and the CSS rule stops them as well.
   Context's Ask bar and Cerebro's chat share it. */
export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export default function WaitingStatus({ text = "Working out the answer…", detail, className = "", ...props }) {
  const still = useMediaQuery(REDUCED_MOTION);
  const motion = still ? "" : " is-animated";
  return (
    <span className={`waiting-status${motion}${className ? ` ${className}` : ""}`} {...props}>
      <span className="waiting-status-spinner" aria-hidden="true" />
      <span className="waiting-status-text" role="status">{text}</span>
      {detail ? <span className="waiting-status-detail">{detail}</span> : null}
    </span>
  );
}
