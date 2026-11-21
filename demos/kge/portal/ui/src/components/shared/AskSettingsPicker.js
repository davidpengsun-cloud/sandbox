import React from "react";
import { Button } from "./Controls";
import useDisclosure from "../shell/useDisclosure";

/* The ask bar's one compact settings control. A button names the current settings (the model
   and, when the caller passes them, who answers); its panel opens upward and shows the model
   and, when the caller passes them, the "Answered by" choice and the ordered runtimes, so one
   control replaces the separate select and chip. Context's Ask bar and Cerebro's chat share it.
   `health` takes the /health reply's `ask` object; only its `model` is read here. The AI
   fallback is not a person's choice (owner, 2026-11-16): it is one deployment switch, so the
   picker offers no fallback control and sends no fallback setting. */

export function runtimeSummary(selected) {
  return selected && selected.length ? selected.join(" → ") : "certified default";
}

export default function AskSettingsPicker({
  id = "ask-settings",
  health,
  arms,
  arm,
  onArmChange,
  runtimes,
  selectedRuntimes,
  onRuntimeToggle,
  onOpen,
  disabled = false,
}) {
  const disclosure = useDisclosure();
  const model = health?.model || "none";
  const hasArms = Array.isArray(arms) && typeof onArmChange === "function";
  const hasRuntimes = typeof onRuntimeToggle === "function";
  const answeredBy = hasRuntimes ? runtimeSummary(selectedRuntimes) : hasArms ? arm || "default" : null;
  const panelId = `${id}-panel`;

  const toggle = () => {
    if (!disclosure.open) onOpen?.();
    disclosure.toggle();
  };

  return (
    <div className="ask-settings v3-controls" ref={disclosure.wrapRef}>
      <Button
        id={id}
        ref={disclosure.triggerRef}
        className="ask-settings-trigger"
        aria-haspopup="dialog"
        aria-expanded={disclosure.open}
        aria-controls={panelId}
        disabled={disabled}
        onClick={toggle}
      >
        <span className="ask-settings-name">Answer settings:</span>
        <span className="ask-settings-now">
          <span className="ask-settings-model">{model}</span>
          {answeredBy !== null && <span className="ask-settings-by">{answeredBy}</span>}
        </span>
        <span className="ask-settings-caret" aria-hidden="true">▴</span>
      </Button>
      {disclosure.open && (
        <div
          id={panelId}
          className="ask-settings-panel"
          data-placement="top"
          role="dialog"
          aria-label="Answer settings"
          ref={disclosure.panelRef}
          tabIndex={-1}
        >
          <p className="ask-settings-row"><span className="lbl">Model</span><span className="ask-settings-value">{model}</span></p>
          {hasArms && (
            <label className="field" htmlFor={`${id}-arm`}>
              Answered by
              <select id={`${id}-arm`} value={arm || ""} onChange={(event) => onArmChange(event.target.value)}>
                {arms.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.options.map((option) => <option key={option.value} value={option.value}>{option.label || option.value}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
          )}
          {hasRuntimes && (
            <div className="ask-settings-runtimes" role="group" aria-label="Runtimes">
              <span className="lbl">Runtimes <span className="ask-settings-note">· {runtimeSummary(selectedRuntimes)}</span></span>
              {runtimes == null ? (
                <p className="ask-settings-note">Loading runtimes…</p>
              ) : runtimes.length === 0 ? (
                <p className="ask-settings-note">Default certified runtime active.</p>
              ) : (
                runtimes.map((runtime) => {
                  const order = (selectedRuntimes || []).indexOf(runtime.id);
                  return (
                    <label key={runtime.id} className="ask-settings-option runtime-option">
                      <input type="checkbox" checked={order >= 0} onChange={() => onRuntimeToggle(runtime.id)} />
                      <span className="runtime-option-label">{runtime.label || runtime.id}</span>
                      {runtime.certified && <span className="ask-settings-badge ok">Certified</span>}
                      {order >= 0 && <span className="ask-settings-badge">#{order + 1}</span>}
                    </label>
                  );
                })
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
