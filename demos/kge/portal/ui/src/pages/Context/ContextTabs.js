import React from "react";
import { Button, Tabs } from "../../components/shared/Controls";
import { plain } from "./contextState";

export function TabButtons({ names, value, onChange, prefix = "nav-", label, counts = {}, ...props }) {
  const choose = (event) => {
    const buttons = [...event.currentTarget.querySelectorAll('[role="tab"]')].filter(b => !b.hidden);
    const at = buttons.indexOf(document.activeElement);
    if (at < 0) return;
    const next = { ArrowRight: (at + 1) % buttons.length, ArrowDown: (at + 1) % buttons.length, ArrowLeft: (at - 1 + buttons.length) % buttons.length,
      ArrowUp: (at - 1 + buttons.length) % buttons.length, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault(); onChange(buttons[next].dataset.view || buttons[next].dataset.tab); buttons[next].focus();
  };
  return <Tabs as="nav" aria-label={label} onKeyDown={choose} {...props}>{names.map(name => <Button key={name} role="tab" id={prefix + name}
    data-view={prefix === "nav-" ? name : undefined} data-tab={prefix !== "nav-" ? name : undefined} aria-controls={(prefix === "nav-" ? "view-" : "tab-") + name}
    aria-selected={name === value} tabIndex={name === value ? 0 : -1} onClick={() => onChange(name)}>{plain(name)}{["review", "onboard"].includes(name) && <span aria-hidden="true" className="count" id={name === "review" ? "queue-count" : "onboard-count"}>{counts[name] || ""}</span>}</Button>)}</Tabs>;
}
