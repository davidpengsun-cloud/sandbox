import React, { useEffect, useId, useRef, useState } from "react";
import { Button } from "../../../components/shared/Controls";
import { monthWords } from "./chatAnswer";

/* #624: the one card for every question back to the person: a clarification's choices, the agent's own question when
   it cannot decide what to ask, and, after a difference answer, the offer to compare the figures. A question, its
   options as buttons, and either "Something else" (a text field and Send) or, for the offer, "Pick a month" (the months
   both terms cover, and Ask). Live, a button answers it; answered, the options stay shown but inactive, with the
   person's answer under them as theirs ("You: …"). Every field is React text. */

/**
 * `choose`: { source, question, options: [{ id, label }], months } (chatAnswer.chooseOf). `active`: the person may
 * answer it now. `other`: it takes an answer of the person's own words. `answer`: the person's answer, shown as theirs;
 * `picked`: the id of the option they picked; `error`: why their answer was not taken. `onPick(id, label)`,
 * `onOther(text)` and `onMonth(month)` send an answer.
 */
/** The longest answer in the person's own words the server takes (route.js OTHER_MAX). */
export const OTHER_MAX = 2000;
const optionWords = (label, source) => {
  const shown = source === "meaning" && /(?:…|\.\.\.)\s*$/.test(label) && label.includes(" — ") ? label.split(" — ")[0] : label;
  return source === "offer" || /[.!?]$/.test(shown.trim()) ? shown : `Choose ${shown}.`;
};

function OneQuestionCard({ choose, active = false, other = false, answer = "", picked, error = "", autoFocus = false, onPick, onOther, onMonth, onCancel }) {
  const id = useId();
  const { source, question, options, months } = choose;
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [picking, setPicking] = useState(false);
  const [month, setMonth] = useState(months.at(-1) || "");
  const card = useRef(null);
  const open = active && !answer;
  useEffect(() => {
    if (autoFocus && open) card.current?.querySelector("button:not(:disabled)")?.focus();
  }, [autoFocus, open]);
  const sendOther = (e) => {
    e.preventDefault();
    const words = text.trim();
    if (words && open) onOther?.(words);
  };
  return (
    <section ref={card} className="cerebro-hitl" aria-labelledby={`${id}-q`} data-source={source} data-active={open ? "true" : "false"}>
      <p id={`${id}-q`} className="cerebro-hitl-q">{question}</p>
      {options.length || months.length || other ? (
        <div className="cerebro-hitl-options" role="group" aria-labelledby={`${id}-q`}>
          {options.map((o) => (
            <Button key={o.id} type="button" disabled={!open} aria-describedby={`${id}-q`} aria-pressed={picked === o.id} onClick={() => onPick?.(o.id, o.label)}>{optionWords(o.label, source)}</Button>
          ))}
          {months.length ? (
            <Button type="button" disabled={!open} aria-expanded={open && picking} onClick={() => setPicking((shown) => !shown)}>Pick a month</Button>
          ) : null}
          {other ? (
            <Button type="button" disabled={!open} aria-expanded={open && typing} onClick={() => setTyping((shown) => !shown)}>Something else</Button>
          ) : null}
        </div>
      ) : null}
      {open && picking && months.length ? (
        <form className="cerebro-hitl-month v3-controls" onSubmit={(e) => { e.preventDefault(); if (months.includes(month)) onMonth?.(month); }}>
          <label>
            <span>Month</span>
            <select value={month} onChange={(e) => setMonth(e.target.value)} autoFocus>
              {months.map((m) => <option key={m} value={m}>{monthWords(m)}</option>)}
            </select>
          </label>
          <Button type="submit" className="primary">Ask</Button>
        </form>
      ) : null}
      {open && typing && other ? (
        <form className="cerebro-hitl-other v3-controls" onSubmit={sendOther}>
          <label>
            <span>Your answer</span>
            <input type="text" value={text} maxLength={OTHER_MAX} onChange={(e) => setText(e.target.value)} autoFocus />
          </label>
          <Button type="submit" className="primary" aria-label="Send your answer" disabled={!text.trim()}>Send</Button>
        </form>
      ) : null}
      {open && onCancel ? <Button type="button" onClick={onCancel}>Cancel clarification</Button> : null}
      {answer ? <p className="cerebro-hitl-you">You: {answer}</p> : null}
      {error ? <p className="cerebro-hitl-error" role="alert">Not taken: {error}</p> : null}
    </section>
  );
}

/* #713 (#707 requirement 12): the multiple-question card, the same card with one labelled group per open question
   ("Meaning", "Show", "Period", "Segment"), single choice, one line per option, nothing preselected (the suggested
   option carries the one-question card's marker in its label, never a selected state). A pick selects, highlighted and
   changeable; Send stays disabled until every group shown has a pick and sends them all at once (ChatView); the server
   checks them against the card it saved. The Period and grouping groups apply to figures: a Show pick of definitions hides them, and
   they need no pick. An option valid only for some earlier pick (its `when`) shows once that pick is made, inside the
   same card. "Something else" stays, for one answer in the person's words that covers everything. Answered, every
   group shows its pick, inactive, with every pick under them as the person's. */

/** The groups that apply to figures alone, hidden when the Show pick is definitions (server: questions.js). */
const FIGURES_ONLY = new Set(["period", "grouping"]);

/** The groups the card shows for the picks so far, each with the options it offers then, and the picks that still
 *  count, as the server reads them (questions.js visibleGroups): the Show pick first, wherever its group sits, then the
 *  others in order; picks held with no prototype, so any group id is a key like another. */
const ownPick = (picks, id) => (picks && Object.hasOwn(picks, id) ? picks[id] : undefined);
export function shownGroups(groups, picks = {}) {
  const counted = Object.create(null);
  const shown = [];
  const show = groups.find((g) => g.kind === "show");
  const output = show && show.options.some((o) => o.id === ownPick(picks, show.id)) ? ownPick(picks, show.id) : undefined;
  for (const group of groups) {
    if (FIGURES_ONLY.has(group.kind) && output === "definitions") continue;
    const options = group.options.filter((o) => !o.when || o.when.options.includes(counted[o.when.group]));
    if (!options.length) continue;
    shown.push({ ...group, options });
    const pick = ownPick(picks, group.id);
    if (options.some((o) => o.id === pick)) counted[group.id] = pick;
  }
  return { groups: shown, picks: { ...counted } };
}

/** An option's one line. A meaning's label is "Name — definition"; where the server cut the definition to fit ("…",
 *  questions.js fitted) the line is the name alone, so a cut meaning reads like its neighbours. The suggested option's
 *  marker follows the cut ("… (default)") and stays on the line: "Name (default)". A label that was not cut is whole. */
const CUT_LABEL = /(?:…|\.\.\.)\s*(\(default\))?\s*$/;
const optionLine = (label, kind) => {
  const cut = kind === "meaning" && label.includes(" — ") ? label.match(CUT_LABEL) : null;
  if (!cut) return label;
  const name = label.split(" — ")[0];
  return cut[1] ? `${name} ${cut[1]}` : name;
};

/**
 * `choose`: { source: "questions", question, groups } (chatAnswer.chooseOf). `picks`: the answered card's picks, { group
 * id: option id }. `onSend(picks, words)` sends every pick that counts, with the words the card shows as the person's;
 * the other props are the one-question card's.
 */
function QuestionsCard({ choose, active = false, other = false, answer = "", picks, error = "", autoFocus = false, onSend, onOther, onCancel }) {
  const id = useId();
  const [selected, setSelected] = useState({});
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const card = useRef(null);
  const open = active && !answer;
  useEffect(() => {
    if (autoFocus && open) card.current?.querySelector("button:not(:disabled)")?.focus();
  }, [autoFocus, open]);
  const { groups, picks: counted } = shownGroups(choose.groups, open ? selected : picks || {});
  const ready = groups.length > 0 && groups.every((g) => Object.hasOwn(counted, g.id));
  const send = () => {
    if (!open || !ready) return;
    onSend?.(counted, groups.map((g) => `${g.label}: ${g.options.find((o) => o.id === counted[g.id]).label}`).join("; "));
  };
  const sendOther = (e) => {
    e.preventDefault();
    const words = text.trim();
    if (words && open) onOther?.(words);
  };
  return (
    <section ref={card} className="cerebro-hitl cerebro-hitl-questions" aria-labelledby={`${id}-q`} data-source="questions" data-active={open ? "true" : "false"}>
      <p id={`${id}-q`} className="cerebro-hitl-q">{choose.question}</p>
      {groups.map((group) => (
        <div key={group.id} className="cerebro-hitl-group" role="group" aria-labelledby={`${id}-${group.id}`}>
          <p id={`${id}-${group.id}`} className="cerebro-hitl-group-label">{group.label}</p>
          <div className="cerebro-hitl-options">
            {group.options.map((o) => (
              <Button key={o.id} type="button" disabled={!open} aria-pressed={counted[group.id] === o.id}
                onClick={() => setSelected((now) => ({ ...now, [group.id]: o.id }))}>{optionLine(o.label, group.kind)}</Button>
            ))}
          </div>
        </div>
      ))}
      {open || other ? (
        <div className="cerebro-hitl-options">
          {open ? <Button type="button" className="primary" aria-label="Send your choices" disabled={!ready} onClick={send}>Send</Button> : null}
          {other ? <Button type="button" disabled={!open} aria-expanded={open && typing} onClick={() => setTyping((shown) => !shown)}>Something else</Button> : null}
        </div>
      ) : null}
      {open && typing && other ? (
        <form className="cerebro-hitl-other v3-controls" onSubmit={sendOther}>
          <label>
            <span>Your answer</span>
            <input type="text" value={text} maxLength={OTHER_MAX} onChange={(e) => setText(e.target.value)} autoFocus />
          </label>
          <Button type="submit" className="primary" aria-label="Send your answer" disabled={!text.trim()}>Send</Button>
        </form>
      ) : null}
      {open && onCancel ? <Button type="button" onClick={onCancel}>Cancel clarification</Button> : null}
      {answer ? <p className="cerebro-hitl-you">You: {answer}</p> : null}
      {error ? <p className="cerebro-hitl-error" role="alert">Not taken: {error}</p> : null}
    </section>
  );
}

/** The one HITL card: the multiple-question card for a card of source "questions" (#713), else the one-question card
 *  as it was. */
export function HitlCard(props) {
  return props.choose?.source === "questions" ? <QuestionsCard {...props} /> : <OneQuestionCard {...props} />;
}

export default HitlCard;
