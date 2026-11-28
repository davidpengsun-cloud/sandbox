import React, { useEffect, useState } from "react";
import { Button } from "../../../components/shared/Controls";
import WaitingStatus from "../../../components/shared/WaitingStatus";
import { MAX_LINES, TICK_MS, stillWaiting, blockLines, finalText, readoutText, secondsOf, spokenFinal, stepLines } from "./working";

/* #622: a turn's working block, under the person's question. While the turn runs: the moving spinner, the step under
   way in plain words and the seconds so far beside it, then at most MAX_LINES lines, the newest. Once it ends, one
   final line: answered and failed open to the lines; a failure keeps its readout in view and is followed by its error
   card. The block has one live region, the same element from its first step to its end, so each step is announced
   once and so is its final state ("Worked for 6 s, 4 steps"); the seconds are never read out. Once ended it is the
   final line itself, or, beside a final line that opens (a button), heard alone. #651 (B5): ChatView pins a running
   block above the input and leaves an answered one out of the thread (its steps are the answer's Steps tab). */

function useNow(running) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

const Lines = ({ lines }) => (lines.length ? (
  <ul className="cerebro-working-lines">
    {lines.map((line) => <li key={line.key} data-kind={line.kind}>{line.text}</li>)}
  </ul>
) : null);

/** The block. `onTryAgain`, `onRephrase`, `onAsk(text)` and `onReload` are the error card's actions (absent: not
 *  offered); `engineer` opens the run's detail. */
export function WorkingBlock({ block, engineer = false, disabled = false, onTryAgain, onRephrase, onAsk, onReload }) {
  const running = block.state === "running";
  const now = useNow(running);
  const [open, setOpen] = useState(false);
  if (running) {
    const lines = blockLines(block);
    const step = block.silent ? stillWaiting(block) : block.step.text;
    return (
      <>
        <section className="cerebro-working" data-state="running" aria-label="Working on your question">
          <p className="cerebro-working-live" role="status">{step}</p>
          <WaitingStatus className="cerebro-working-step" text={step} detail={`${secondsOf(block, now)} s`} live={false} aria-hidden="true" />
          <Lines lines={lines.slice(-MAX_LINES)} />
        </section>
      </>
    );
  }
  // #630: the pending card stands for a turn that went to the background.
  if (block.state === "background") return null;
  const lines = stepLines(block);
  const openable = (block.state === "answered" || block.state === "failed") && lines.length > 0;
  const readout = block.state === "failed" ? readoutText(block.readout) : "";
  const id = `cerebro-working-${block.started}`;
  return (
    <>
      <section className="cerebro-working is-final" data-state={block.state} aria-label="How this question went">
        <p className={openable ? "cerebro-working-live" : "cerebro-working-final"} role="status">
          {openable ? spokenFinal(block, now) : finalText(block, now)}
        </p>
        {openable ? (
          <Button type="button" className="cerebro-working-final" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}>
            {finalText(block, now)}
          </Button>
        ) : null}
        {readout && !open ? <p className="cerebro-working-readout">{readout}</p> : null}
        {open ? <div id={id}><Lines lines={lines} /></div> : null}
      </section>
      {block.state === "failed" && block.error ? (
        <ErrorCard error={block.error} engineer={engineer} disabled={disabled} onTryAgain={onTryAgain} onRephrase={onRephrase} onAsk={onAsk} onReload={onReload} />
      ) : null}
    </>
  );
}

/** #622 requirement 8: the failure's plain message and its next step. Try again resends the question; Rephrase (and
 *  Narrow it) put it in the box to edit; a suggestion is a button that asks it; Reload (#625: the conversation changed
 *  in another tab) reopens the conversation from the server. An engineer can open the run's detail. `disabled` holds
 *  the actions that ask while another turn runs. */
export function ErrorCard({ error, engineer = false, disabled = false, onTryAgain, onRephrase, onAsk, onReload }) {
  const [detail, setDetail] = useState(false);
  const retry = error.action === "try_again" || error.action === "narrow";
  return (
    <article className="cerebro-chat-card cerebro-error" aria-label="Not answered" data-case={error.case}>
      <p className="cerebro-error-message" role="alert">{error.message}</p>
      <div className="row cerebro-error-actions">
        {retry && onTryAgain ? <Button type="button" className="primary" disabled={disabled} onClick={onTryAgain}>Try again</Button> : null}
        {error.action === "narrow" && onRephrase ? <Button type="button" onClick={onRephrase}>Narrow it</Button> : null}
        {error.action === "rephrase" && onRephrase ? <Button type="button" className="primary" onClick={onRephrase}>Rephrase</Button> : null}
        {error.action === "reload" && onReload ? <Button type="button" className="primary" disabled={disabled} onClick={onReload}>Reload</Button> : null}
        {error.suggestion && onAsk ? (
          <Button type="button" className="cerebro-error-suggestion" aria-label={`Ask “${error.suggestion}”`} disabled={disabled} onClick={() => onAsk(error.suggestion)}>{error.suggestion}</Button>
        ) : null}
      </div>
      {engineer && error.detail ? (
        <div className="cerebro-error-detail">
          <Button type="button" aria-expanded={detail} onClick={() => setDetail((o) => !o)}>Run detail</Button>
          {detail ? <pre className="agentchat-code-block">{error.detail}</pre> : null}
        </div>
      ) : null}
    </article>
  );
}
