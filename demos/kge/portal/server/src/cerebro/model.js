import "./telemetry-off.js";
import { FakeStreamingChatModel } from "@langchain/core/utils/testing";
import { AIMessage, AIMessageChunk, ToolMessage } from "@langchain/core/messages";
import { ChatGenerationChunk } from "@langchain/core/outputs";
import { GatewayChat } from "./gatewayModel.js";
import { nodeAskedAbout } from "./agent.js";

// Thought signatures (#577). Gemini 3 and every later Gemini model return an opaque thoughtSignature on function call
// parts and refuse the next request (HTTP 400) unless each comes back on the same call, unchanged. The library
// (@langchain/google-common 2.3.6, the newest release) keeps signatures as a list beside the parts and re-attaches them
// only when the list's length matches the rebuilt parts, which a thought part, a text part or a second call in one
// streamed chunk breaks; it also reads only the first part of a streamed chunk and names every parallel call's result
// after the first call. So the wrapper reads every part of every chunk, keeps each call's signature under its tool call
// id (SIGNATURES), drops thought parts (their text never reaches the page or the next request), and on the way out puts
// each signature back on its own function call part and names each result after its own call.
export const SIGNATURES = "function_call_signatures";

const candidateOf = (data) => data?.candidates?.[0];

/** One response chunk as one response per part, in order; the finish reason and usage ride on the last alone. */
export function partResponses(data) {
  const candidate = candidateOf(data);
  const all = candidate?.content?.parts ?? [];
  const parts = all.filter((part) => part && !part.thought);
  if (!candidate || parts.length <= 1) {
    if (!candidate || parts.length === all.length) return [data];
    return [{ ...data, candidates: [{ ...candidate, content: { ...candidate.content, parts } }] }];
  }
  const { usageMetadata: _usage, ...rest } = data;
  const { finishReason: _finish, ...open } = candidate;
  return parts.map((part, at) => {
    const last = at === parts.length - 1;
    const one = { ...(last ? candidate : open), content: { ...candidate.content, parts: [part] } };
    return { ...(last ? data : rest), candidates: [one] };
  });
}

// A turn the model did not make has no signature to return: the portal's own steps (a clarification's card, the
// offer, a pick sent to ask, the safety net) and the earlier turns the model reads rebuilt (agent.js earlier). #625: a
// paused turn of the model's own comes back from the saved conversation with its signatures. Google's documented
// placeholder tells Gemini 3 and later to skip the check for a turn with none; it goes on the turn's first call, where
// Gemini 3 puts a turn's signature.
export const UNSIGNED_TURN = "skip_thought_signature_validator";
const SIGNING = /^gemini-(?:[3-9]|\d{2,})/;

/** A model turn's parts, rebuilt from its tool calls, with each call's own signature back and no thought part. */
function signCalls(content, message, model) {
  const known = message.additional_kwargs?.[SIGNATURES];
  const signatures = known ?? (SIGNING.test(model ?? "") && message.tool_calls?.[0] ? { [message.tool_calls[0].id]: UNSIGNED_TURN } : {});
  const calls = message.tool_calls ?? [];
  let at = 0;
  const parts = [];
  for (const part of content.parts ?? []) {
    if (part.thought) continue;
    const { thoughtSignature: _dropped, ...bare } = part;
    if (bare.functionCall) {
      const call = calls[at];
      at += 1;
      const signature = call && call.name === bare.functionCall.name ? signatures[call.id] : undefined;
      parts.push(typeof signature === "string" && signature ? { ...bare, thoughtSignature: signature } : bare);
    } else parts.push(bare);
  }
  return { ...content, parts: parts.length ? parts : [{ text: "" }] };
}

/**
 * The request's contents with each model turn's function calls signed and each tool result named after its own call.
 * The library builds one model content per assistant message and one function content per run of tool messages (their
 * results in order), so the k-th model content with function calls is the k-th assistant message with tool calls.
 */
export function signContents(contents, input, model) {
  const turns = input.filter((m) => AIMessage.isInstance(m) && m.tool_calls?.length);
  const runs = [];
  input.forEach((message, at) => {
    if (!ToolMessage.isInstance(message)) return;
    if (at > 0 && ToolMessage.isInstance(input[at - 1])) runs.at(-1).push(message);
    else runs.push([message]);
  });
  let turn = 0;
  let run = 0;
  return contents.map((content) => {
    if (content.role === "model" && content.parts?.some((part) => part.functionCall)) return signCalls(content, turns[turn++] ?? {}, model);
    if (content.role === "function") {
      const results = runs[run++] ?? [];
      return { ...content, parts: content.parts.map((part, at) => (part.functionResponse && results[at]?.name ? { ...part, functionResponse: { ...part.functionResponse, name: results[at].name } } : part)) };
    }
    return content;
  });
}

/** A chunk's message with the library's signature list replaced by its call's own signature, keyed by the call's id. */
/**
 * The mock model (PORTAL_ASK_MODEL_PROVIDER=mock): it calls nothing outside the portal and answers with its fixed
 * reply. An "Ask about this" question, with explore among its tools, first calls explore on the node the question is
 * about, so an offline run shows a whole tool-calling turn on the exact node; every other question is answered at once.
 */
export class MockCerebroChat extends FakeStreamingChatModel {
  bindTools(tools) {
    const next = new MockCerebroChat({ sleep: this.sleep, responses: this.responses });
    next.tools = [...this.tools, ...tools];
    return next;
  }

  exploreCall(messages) {
    const node = messages.at(-1)?._getType?.() === "human" && this.tools.some((tool) => (tool.function?.name ?? tool.name) === "explore")
      ? nodeAskedAbout(messages.find((m) => m._getType?.() === "system")?.content) : undefined;
    return node ? { id: `mock-${globalThis.crypto.randomUUID()}`, name: "explore", args: { node } } : undefined;
  }

  async _generate(messages, options, runManager) {
    const call = this.exploreCall(messages);
    if (!call) return super._generate(messages, options, runManager);
    return { generations: [{ text: "", message: new AIMessage({ content: "", tool_calls: [call] }) }] };
  }

  async *_streamResponseChunks(messages, options, runManager) {
    const call = this.exploreCall(messages);
    if (!call) {
      yield* super._streamResponseChunks(messages, options, runManager);
      return;
    }
    const chunk = new ChatGenerationChunk({ text: "", message: new AIMessageChunk({ content: "", tool_call_chunks: [{ ...call, args: JSON.stringify(call.args), index: 0, type: "tool_call_chunk" }] }) });
    yield chunk;
    await runManager?.handleLLMNewToken("", undefined, undefined, undefined, undefined, { chunk });
  }
}

// #622 requirement 4: Cerebro's model calls think at level low where the model takes a thinking level, as KGE sends its
// planner since #589 (kge/adapters/model/providers.py GEMINI_3, thinking_support): Gemini 3 and every later Gemini.
// Gemini 2.5 takes a token budget and no level, so it is sent none and thinks as it did. The library sends the level as
// generationConfig.thinkingConfig.thinkingLevel.
export const THINKING_LEVEL = "low";
const TAKES_LEVEL = /^gemini-(?:[3-9]|\d{2,})/;
export const thinkingLevelFor = (model) => (TAKES_LEVEL.test(String(model ?? "")) ? THINKING_LEVEL : undefined);

export function openCerebroModel(settings, env, options = {}) {
  if (options.model) return options.model;
  if (settings.modelProvider === "mock") {
    if (!settings.model) return undefined;
    return new MockCerebroChat({ responses: [new AIMessage(options.reply ?? "This is the mock model's answer.")] });
  }
  if (!options.kge || !options.req) return undefined;
  return new GatewayChat({ kge: options.kge, req: options.req, expectedModel: settings.model || undefined });
}
