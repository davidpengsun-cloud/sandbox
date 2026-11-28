import { ToolMessage } from "@langchain/core/messages";
import { END } from "@langchain/langgraph";
import { RESULT_MAX_BYTES, RUN_GONE, answerCard, boundedResult, buildGraph, hideSavedPassages, pendingRun, plainReply, runEnding, toPage, withFigureEvidence } from "./agent.js";
import { allowedTools, isEngineer, kgeTools } from "./tools.js";
import { CONVERSATION_ID, isConflict } from "../conversations/index.js";
import { citationOnly, contextExcerpt, documentAnswer, figureDocumentEvidence, figureSourceIds, groundedDocumentAnswer } from "./documentAnswer.js";
import { questionUsage } from "./usage.js";
import { resumeQuestionTrace } from "../tracing.js";

// #630 requirements 2 to 4: following a background run of the person's ask, and stopping it. The page asks at KGE's
// poll interval (the pending card's poll_after); the portal reads the run's id from the person's own saved conversation
// by the pending card's message id (a tool call's id need not be unique across turns), never from the page, and asks KGE's question_run as the person (tools.js), so KGE checks the
// run's owner as it checks every call. While the run goes on, the reply says so; once it has ended (done, a time
// limit, a failure, or Stop), the portal writes its ending into the saved conversation in place of the pending card,
// so the model and the next ask's history read the answer, and gives the page the message as the conversation now
// holds it. Nothing about the run is kept anywhere else: any instance can follow it, after a reload too.
//
// The write is LangGraph's own update of the conversation's state, made only where the conversation's last turn has ended
// (an update would drop a question back to the person, or end a turn under way; a turn that failed has ended too, once
// TURN_HOLD_MS has passed); the page follows only between turns. Where it
// cannot be kept now (a pause waits, or the conversation changed since it was read), the reply says saved: false and
// the page asks again later: KGE keeps the run's ending for it.

const MESSAGE_ID = /^[\w.:-]{1,128}$/;
const none = (detail = "No such question in this conversation.") => Object.assign(new Error(detail), { status: 404 });

/** The person's saved conversation's graph state and the ask's tool message by its own id. */
async function opened(conversations, principal, id, messageId) {
  if (!CONVERSATION_ID.test(String(id)) || !MESSAGE_ID.test(String(messageId))) throw none();
  const saver = conversations.saver(principal);
  const info = await saver.info(id);
  if (!info) throw none("No such conversation.");
  const graph = buildGraph({ invoke: async () => { throw new Error("No model follows a run."); } }, { checkpointer: saver });
  const config = { configurable: { thread_id: id } };
  const state = await graph.getState(config);
  const message = (state.values?.messages ?? []).find((m) => m._getType() === "tool" && m.name === "ask" && m.id === messageId);
  if (!message) throw none();
  let reply;
  try { reply = JSON.parse(typeof message.content === "string" ? message.content : ""); } catch { reply = undefined; }
  const aside = await saver.aside(id);
  return { saver, graph, config, state, info, message, reply, aside, questionId: Object.hasOwn(aside.questionRuns ?? {}, messageId) ? aside.questionRuns[messageId] : undefined };
}

/** Update the associated question's safe totals after a completed background run or evidence pass. */
export async function backgroundUsage({ conversations, kge, req, id, messageId, result }) {
  if (!req?.modelQuestionToken || result?.state !== "ended" || result.saved === false) return undefined;
  const held = await opened(conversations, req.portalIdentity.principal, id, messageId);
  const questionId = req.modelQuestionId;
  const runs = held.aside.questionRuns ?? {};
  const pending = (held.state.values?.messages ?? []).some((message) => {
    if (message._getType() !== "tool" || message.name !== "ask" || !Object.hasOwn(runs, message.id) || runs[message.id] !== questionId) return false;
    let reply; try { reply = JSON.parse(message.content); } catch { return true; }
    return pendingRun(reply) || reply?.document_evidence_pending === true;
  });
  const usage = await questionUsage(kge, req, { finish: !pending && !result.evidence_pending });
  if (!usage) return undefined;
  try {
    await held.saver.updateAside(id, (now) => {
      const human = Object.entries(now.questionIds ?? {}).find(([, value]) => value === questionId)?.[0];
      if (!human || !Object.hasOwn(now.replies ?? {}, human)) return now;
      return { ...now, replies: { ...now.replies, [human]: { ...now.replies[human], usage } } };
    });
    await held.saver.flush();
  } catch { /* A completed authoritative answer remains usable if accounting could not be saved. */ }
  return usage;
}

// A checkpoint with a node due next is a turn under way while it is fresh; one left so by a turn that failed (its model
// or KGE threw, or its stream was cut) is not, once TURN_HOLD_MS has passed since the conversation was last saved. A turn
// is one request to the portal (route.js aborts it when its response closes), and several tool calls can run in one
// step with no checkpoint between them, so the hold is above the request's bound, not any one call's: on Google Cloud,
// the portal's Cloud Run request timeout, 1200 seconds. Under Compose nothing bounds the request but its connection; a
// turn that outlives the hold there and is written over ends at its next save as a changed conversation, never quietly.
// A question back to the person is told apart by its interrupt.
export const TURN_HOLD_MS = 25 * 60 * 1000;
function underWay({ state, info }, now = Date.now()) {
  if (!state.next.length) return false;
  const updated = Date.parse(info?.updated ?? "");
  return !Number.isFinite(updated) || now - updated < TURN_HOLD_MS;
}

/**
 * The ending written in place of the pending card: the same message id, so the conversation keeps its place. The
 * conversation is read afresh just before the write (the KGE call before it can take a while), and the write is made
 * only where the turn has ended there (no node is due next: none under way, no question back to the person) and the card
 * is still pending; else saved is false and the page asks again later. A turn that starts after the fresh read meets
 * the write as a changed conversation, so the page never follows while a turn of its own runs.
 */
async function settle({ conversations, principal, id, messageId }, value, { evidence = false } = {}) {
  const fresh = await opened(conversations, principal, id, messageId);
  const ended = new ToolMessage({ id: fresh.message.id, content: boundedResult(value), tool_call_id: fresh.message.tool_call_id, name: "ask" });
  if (underWay(fresh) || fresh.state.tasks.some((task) => task.interrupts?.length)
    || (evidence ? fresh.reply?.document_evidence_pending !== true : !pendingRun(fresh.reply))) return { saved: false, message: ended };
  try {
    await fresh.graph.updateState(fresh.config, { messages: [ended], then: END }, "choose");
    await fresh.saver.flush();
    return { saved: true, message: ended };
  } catch (error) {
    if (!isConflict(error)) console.error(`Cerebro run not saved: ${error?.name || "Error"}: ${error?.message || ""}`);
    return { saved: false, message: ended };
  }
}

/**
 * One look at a background run: { state: "running", poll_after, expires_at } while it goes on; { state: "ended",
 * saved, message } once it has ended (message as the page reads it); { state: "unknown", error, poll_after } where
 * KGE could not be asked now, to ask again. stop: true asks KGE to stop the run first (Stop).
 */
export async function followRun({ conversations, kge, req, roles = [], id, messageId, after = 0, stop = false, model, issueQuestion }) {
  const principal = req?.portalIdentity?.principal;
  const held = await opened(conversations, principal, id, messageId);
  if (held.questionId && held.aside.questionTraces && Object.hasOwn(held.aside.questionTraces, held.questionId)) {
    resumeQuestionTrace(req, held.aside.questionTraces[held.questionId], id);
  }
  if (req) {
    req.modelQuestionId = held.questionId ?? globalThis.crypto.randomUUID();
    req.modelConversationId = id;
    // Conversations from before accounting have no mapping; keep one stable for their future polling.
    if (!held.questionId) {
      await held.saver.updateAside(id, (now) => ({ ...now, questionRuns: { ...now.questionRuns, [messageId]: req.modelQuestionId } }));
      await held.saver.flush();
    }
    if (issueQuestion) req.modelQuestionToken = await issueQuestion(req, req.modelQuestionId);
  }
  const where = { conversations, principal, id, messageId };
  const engineer = isEngineer(roles);
  if (held.reply?.document_evidence_pending === true) {
    const { document_evidence_pending: _pending, ...figure } = held.reply;
    const call = kgeTools({ kge, req, allowed: allowedTools(roles) });
    const documentAnswer = stop ? undefined : await figureDocumentEvidence({ question: figure.question, reply: figure, call, model });
    const value = withFigureEvidence(figure, documentAnswer);
    const kept = await settle(where, value, { evidence: true });
    return { state: "ended", saved: kept.saved, evidence_pending: !kept.saved,
      message: kept.saved ? toPage(kept.message, engineer) : hideSavedPassages(toPage(held.message, engineer)) };
  }
  if (!pendingRun(held.reply)) return { state: "ended", saved: true, message: hideSavedPassages(toPage(held.message, engineer)) };
  const run = held.reply.run;
  const call = kgeTools({ kge, req, allowed: allowedTools(roles) });
  const asked = stop ? await call.stopRun(run.id) : await call.questionRun(run.id, after);
  const question = typeof held.reply.question === "string" ? held.reply.question : "";
  if (asked.error !== undefined) {
    if (/^(?:Error executing tool \w+: )?404: /.test(asked.error)) {
      const kept = await settle(where, { run: { id: run.id, state: "failed" }, question, error: RUN_GONE, failure: "run_gone" });
      return { state: "ended", saved: kept.saved, message: toPage(kept.message, engineer) };
    }
    return { state: "unknown", error: engineer ? asked.error : "The knowledge graph could not be asked about this question right now.", poll_after: run.poll_after };
  }
  const now = asked.value;
  if (!now || typeof now !== "object" || now.state === "running") {
    return { state: "running", poll_after: Number(now?.poll_after) > 0 ? Number(now.poll_after) : run.poll_after, ...(Number.isFinite(Number(now?.expires_at)) ? { expires_at: Number(now.expires_at) } : {}), cursor: Number.isInteger(now?.cursor) ? now.cursor : after };
  }
  let { value } = runEnding(now, { question, engineer });
  const figureSources = answerCard(value) && figureSourceIds(value).length > 0;
  if (!stop && now.state === "done" && figureSources) {
    const plain = plainReply(value, { engineer }).value;
    const staged = { ...plain, question: value.question || question, document_evidence_pending: true };
    const fits = Buffer.byteLength(JSON.stringify(staged)) <= RESULT_MAX_BYTES;
    const kept = await settle(where, fits ? staged : plain);
    return { state: "ended", saved: kept.saved, evidence_pending: fits, message: toPage(kept.message, engineer) };
  }
  if (!stop && now.state === "done" && citationOnly(value)) {
    const omitted = new Set(Array.isArray(value.superseded_not_cited) ? value.superseded_not_cited : []);
    let cited = value.cite.filter((id) => !omitted.has(id));
    let context;
    try { context = (await call("get_context", { question }))?.value; } catch { /* Leave the completed answer usable. */ }
    const window = new Set(Array.isArray(context?.in_window) ? context.in_window : []);
    const eligible = new Set((Array.isArray(context?.sources) ? context.sources : []).filter((s) => s?.citable === true && window.has(s.n)).map((s) => s.doc));
    for (const item of Array.isArray(context?.withheld) ? context.withheld : []) omitted.add(typeof item === "string" ? item : item?.doc);
    cited = cited.filter((id) => eligible.has(id) && !omitted.has(id));
    let evidence = await groundedDocumentAnswer({ question, cite: cited, call, model });
    evidence ??= contextExcerpt(question, context, cited, [...omitted]);
    if (!evidence) {
      let search;
      try { search = (await call("search", { query: question }))?.value; } catch { /* Leave the completed answer usable. */ }
      evidence = documentAnswer(search, cited);
    }
    value = { ...value, document_answer: evidence };
  }
  const kept = await settle(where, now.state === "done" ? plainReply(value, { engineer }).value : value);
  const message = toPage(kept.message, engineer);
  return { state: "ended", saved: kept.saved, message: kept.saved ? message : hideSavedPassages(message) };
}
