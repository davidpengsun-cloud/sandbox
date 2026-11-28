import React from "react";
import { Button } from "./Controls";
import useDisclosure from "../shell/useDisclosure";
import "./AskSettingsPicker.css";

/* The ask bar's one compact settings chip. Given the model alone it is a static chip that names the model, with no
   caret and no drop-up: Cerebro's chip. Given a setting to choose (arms, purposes, rich answers or runtimes), the chip
   reads "model · settings" and opens a drop-up with one setting per line, each only when given: Model, then Answered
   by, then Purpose (its select is #purpose, with the hint on why it is asked), then Rich answers (its checkbox is
   #rich), then the Runtimes rows (checkbox · name · badge). Context's Playground uses that form, and gives Answered by,
   Purpose and Rich answers to testers alone; the walkthrough opens the chip to reach #purpose and #rich. The drop-up's
   styles live in AskSettingsPicker.css, scoped to the picker so the page's label and select rules cannot reach in.
   `model` names the model; the older `health` prop (the /health reply's `ask` object) still works, and `model` wins.
   The AI fallback is not a person's choice (owner, 2026-11-16): it is one deployment switch, so the picker offers no
   fallback control and sends no fallback setting. */

export function runtimeSummary(selected) {
  return selected && selected.length ? selected.join(" → ") : "certified default";
}

export default function AskSettingsPicker({
  id = "ask-settings",
  model: modelProp,
  health,
  arms,
  arm,
  onArmChange,
  purposes,
  purpose,
  onPurposeChange,
  rich = false,
  onRichChange,
  runtimes,
  selectedRuntimes,
  onRuntimeToggle,
  onOpen,
  disabled = false,
}) {
  const disclosure = useDisclosure();
  const model = modelProp || health?.model || "none";
  const hasArms = Array.isArray(arms) && typeof onArmChange === "function";
  const hasPurpose = Array.isArray(purposes) && typeof onPurposeChange === "function";
  const hasRich = typeof onRichChange === "function";
  const hasRuntimes = typeof onRuntimeToggle === "function";

  if (!hasArms && !hasPurpose && !hasRich && !hasRuntimes) {
    return (
      <span id={id} className="ask-settings ask-settings-static" title={`Model: ${model}`}>
        <span className="ask-settings-name">Model:</span>
        <span className="ask-settings-model">{model}</span>
      </span>
    );
  }

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
          <p className="ask-settings-line"><span className="ask-settings-key">Model</span><span className="ask-settings-value">{model}</span></p>
          {hasArms && (
            <label className="ask-settings-line" htmlFor={`${id}-arm`}>
              <span className="ask-settings-key">Answered by</span>
              <select id={`${id}-arm`} value={arm || ""} onChange={(event) => onArmChange(event.target.value)}>
                {arms.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {group.options.map((option) => <option key={option.value} value={option.value}>{option.label || option.value}</option>)}
                  </optgroup>
                ))}
              </select>
            </label>
          )}
          {hasPurpose && (
            <>
              <label className="ask-settings-line" htmlFor="purpose">
                <span className="ask-settings-key">Purpose</span>
                <select id="purpose" value={purpose} onChange={(event) => onPurposeChange(event.target.value)}>
                  {purposes.map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <p className="ask-settings-note ask-settings-hint">Why you ask: the access rules are checked for this purpose. Analysis suits most questions.</p>
            </>
          )}
          {hasRich && (
            <label className="ask-settings-line" htmlFor="rich">
              <span className="ask-settings-key">Rich answers</span>
              <input id="rich" type="checkbox" checked={rich} onChange={(event) => onRichChange(event.target.checked)} />
            </label>
          )}
          {hasRuntimes && (
            <div className="ask-settings-runtimes" role="group" aria-label="Runtimes">
              <span className="ask-settings-key">Runtimes <span className="ask-settings-note">· {runtimeSummary(selectedRuntimes)}</span></span>
              {runtimes == null ? (
                <p className="ask-settings-note">Loading runtimes…</p>
              ) : runtimes.length === 0 ? (
                <p className="ask-settings-note">Default certified runtime active.</p>
              ) : (
                runtimes.map((runtime) => {
                  const order = (selectedRuntimes || []).indexOf(runtime.id);
                  return (
                    <label key={runtime.id} className="ask-settings-option runtime-option" data-runtime={runtime.id}>
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
