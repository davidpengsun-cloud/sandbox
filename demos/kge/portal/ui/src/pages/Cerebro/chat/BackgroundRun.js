import React, { useEffect, useState } from "react";
import { Button } from "../../../components/shared/Controls";
import { ANSWER_TOOL, toolObject } from "./chatAnswer";
import { TICK_MS } from "./working";

/* #630: a question that runs in the background. The server keeps the run's id in the saved conversation as the ask
   call's reply ({ run: { id, state: "running", poll_after, started_at }, question }), and the page shows it as the
   pending card: what is being answered, "running in the background", the time since it was sent to Ask, and Stop.
   The chat is free meanwhile. The page follows the run through the portal (chatSession.js useRunFollower); once it has
   ended, the server's reply takes the card's place: the answer card, or an ending card (a time limit, the warehouse's
   failure, Stop). Every word here is the server's or fixed: no figure, no id. */

/** The run an ask tool message carries: { pending } while it runs in the background, { ended } once it ended other
 *  than with an answer, or null for any other message (an answer included). */
export function runOf(message) {
  const value = toolObject(message, ANSWER_TOOL);
  const run = value?.run;
  if (!run || typeof run !== "object" || typeof run.id !== "string" || !run.id) return null;
  const question = typeof value.question === "string" ? value.question : "";
  if (run.state === "running" && !("error" in value)) {
    const startedAt = Number(run.started_at);
    const pollAfter = Number(run.poll_after);
    return { pending: { question, startedAt: Number.isFinite(startedAt) ? startedAt : null, pollAfter: Number.isFinite(pollAfter) && pollAfter > 0 ? pollAfter : 2 } };
  }
  if (typeof value.error !== "string") return null;
  return {
    ended: {
      question,
      case: typeof value.failure === "string" ? value.failure : "failed",
      message: value.error,
      suggestion: typeof value.suggestion === "string" ? value.suggestion : "",
    },
  };
}

/** m:ss, from whole seconds. */
export function clockOf(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function useClock(startedAt) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return startedAt ? clockOf((now - startedAt) / 1000) : "";
}

/** The pending card: "<question> · running in the background · 1:12 · Stop". `onStop` stops the run; `stopping` while
 *  it is being stopped; `disabled` while a turn of the conversation runs, whose answer a stop written now would end. */
export function PendingRunCard({ pending, onStop, stopping = false, disabled = false }) {
  const clock = useClock(pending.startedAt);
  return (
    <article className="cerebro-chat-card cerebro-run" aria-label="Running in the background" data-state="running">
      <p className="cerebro-run-line">
        {pending.question ? <span className="cerebro-run-question">{pending.question}</span> : null}
        {pending.question ? " · " : null}
        <span role="status">running in the background</span>
        {clock ? <span className="cerebro-run-clock" aria-hidden="true">{` · ${clock}`}</span> : null}
        {onStop ? " · " : null}
        {onStop ? <Button type="button" className="cerebro-run-stop" disabled={stopping || disabled} onClick={onStop}>{stopping ? "Stopping…" : "Stop"}</Button> : null}
      </p>
    </article>
  );
}

/** How a background run ended other than with an answer: the timeout card (KGE's message and the narrowing
 *  suggestion), the warehouse error card, Stop, or a run no longer kept. */
export function RunEndingCard({ ended }) {
  return (
    <article className="cerebro-chat-card cerebro-error cerebro-run-ended" aria-label="Not answered" data-case={ended.case}>
      {ended.question ? <p className="cerebro-run-question">{ended.question}</p> : null}
      <p className="cerebro-error-message" role={ended.case === "stopped" ? "status" : "alert"}>{ended.message}</p>
      {ended.suggestion ? <p className="cerebro-run-suggestion">{ended.suggestion}</p> : null}
    </article>
  );
}

/** #621: the questions the person can ask about a subject, after explore_question's card: each a button that asks it
 *  as the person's next question. It never holds the chat. */
export function SuggestCard({ choose, onAsk, disabled = false }) {
  return (
    <section className="cerebro-chat-card cerebro-suggest" aria-label={choose.question}>
      <p className="cerebro-try-lbl">{choose.question}</p>
      <ul>
        {choose.options.map((o) => (
          <li key={o.id}>
            <Button type="button" className="cerebro-try-q" disabled={disabled || !onAsk} onClick={() => onAsk?.(o.label)}>{o.label}</Button>
          </li>
        ))}
      </ul>
    </section>
  );
}
