import "./V3Toast.css";

/* Tiny imperative toast for the v3 design system. Renders into its own
   fixed wrapper on document.body, so it works from any page or component
   (including the Ask panel) without a React provider. */

let wrap = null;

const CHECK_SVG =
  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

export function showToast(message, opts = {}) {
  if (typeof document === "undefined" || !message) return;
  if (!wrap || !document.body.contains(wrap)) {
    wrap = document.createElement("div");
    wrap.className = "v3-toast-wrap";
    wrap.setAttribute("aria-live", "polite");
    document.body.appendChild(wrap);
  }
  const toast = document.createElement("div");
  toast.className = "v3-toast";
  toast.innerHTML = CHECK_SVG;
  const text = document.createElement("span");
  text.textContent = message;
  toast.appendChild(text);
  wrap.appendChild(toast);

  const duration = opts.duration || 2600;
  setTimeout(() => {
    toast.classList.add("v3-toast--out");
    setTimeout(() => toast.remove(), 320);
  }, duration);
}

export default showToast;
