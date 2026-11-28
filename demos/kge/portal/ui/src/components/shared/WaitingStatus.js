import React from "react";
import { usePhone as useMediaQuery } from "../shell/useDisclosure";
import "./Controls.css";

/* The shared waiting state of an ask: a spinning glyph in front of the status text, and the
   text pulses. Both move in CSS alone, so the text itself never changes and the one live
   region announces it once. `detail` (the pipeline's current step, say) shows beside the
   text but outside the live region, so step changes are not read out again. With reduced
   motion the glyph never stands still: it blinks slowly (is-blinking, opacity only) in place
   of the spin, and the text stops pulsing. Context's Ask bar and Cerebro's chat share it.
   `live` false leaves the text out of a live region, for a caller that holds its own (Cerebro's
   working block, whose one region outlives the spinner to announce the final state). */
export const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

export default function WaitingStatus({ text = "Working out the answer…", detail, className = "", live = true, ...props }) {
  const still = useMediaQuery(REDUCED_MOTION);
  const motion = still ? " is-blinking" : " is-animated";
  return (
    <span className={`waiting-status${motion}${className ? ` ${className}` : ""}`} {...props}>
      <span className="waiting-status-spinner" aria-hidden="true" />
      <span className="waiting-status-text" role={live ? "status" : undefined}>{text}</span>
      {detail ? <span className="waiting-status-detail">{detail}</span> : null}
    </span>
  );
}
