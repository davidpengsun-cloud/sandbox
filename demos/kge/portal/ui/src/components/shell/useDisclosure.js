import { useCallback, useEffect, useRef, useState } from "react";

/* One pop-over of the global bar: a button that opens a panel. Opening moves focus into the
   panel; Escape closes it and returns focus to the button; a click outside closes it. */
export default function useDisclosure() {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return undefined;
    panelRef.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) && panelRef.current?.contains(event.target)) {
        const items = [...panelRef.current.querySelectorAll('a[href], button:not(:disabled)')].filter((item) => item.getAttribute("aria-disabled") !== "true");
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : event.key === "ArrowDown" ? (index + 1) % items.length
          : (index < 0 ? items.length - 1 : (index - 1 + items.length) % items.length);
        items[next].focus();
      }
    };
    const onClick = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) setOpen(false);
    };
    const wrapper = wrapRef.current;
    const onFocusOut = (event) => {
      if (event.relatedTarget && !wrapper.contains(event.relatedTarget)) setOpen(false);
    };
    wrapper?.addEventListener("focusout", onFocusOut);
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      wrapper?.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  return { open, setOpen, toggle: () => setOpen((value) => !value), close, wrapRef, triggerRef, panelRef };
}

/* True while the viewport is phone-wide (the bar's 640px break), following resizes. */
export function usePhone(query = "(max-width: 640px)") {
  const read = () => (typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false);
  const [phone, setPhone] = useState(read);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const list = window.matchMedia(query);
    const onChange = () => setPhone(list.matches);
    onChange();
    list.addEventListener?.("change", onChange);
    return () => list.removeEventListener?.("change", onChange);
  }, [query]);
  return phone;
}
