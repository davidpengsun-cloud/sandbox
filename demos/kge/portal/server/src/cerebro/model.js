import "./telemetry-off.js";
import { ChatVertexAI } from "@langchain/google-vertexai";
import { FakeStreamingChatModel } from "@langchain/core/utils/testing";
import { AIMessage, AIMessageChunk, ToolMessage } from "@langchain/core/messages";
import { ChatGenerationChunk } from "@langchain/core/outputs";
import { concat } from "@langchain/core/utils/stream";
import { googleAuth } from "../outbound.js";
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

// A turn the server did not receive from the model in this request (a resume rebuilds the paused turn from the page's
// conversation, which never holds signatures) has no signature to return. Google's documented placeholder tells Gemini 3
// and later to skip the check for such a turn; it goes on the turn's first call, where Gemini 3 puts a turn's signature.
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
function signed(chunk, part) {
  const { signatures: _list, ...kwargs } = chunk.message.additional_kwargs ?? {};
  const id = chunk.message.tool_call_chunks?.[0]?.id ?? chunk.message.tool_calls?.[0]?.id;
  if (part?.functionCall && part.thoughtSignature && id) kwargs[SIGNATURES] = { [id]: part.thoughtSignature };
  return new ChatGenerationChunk({ text: chunk.text, generationInfo: chunk.generationInfo, message: new AIMessageChunk({ ...chunk.message, additional_kwargs: kwargs }) });
}

// Cerebro's model port: a LangChain chat model, on the portal's model settings (PORTAL_ASK_MODEL,
// PORTAL_ASK_MODEL_PROVIDER, VERTEX_PROJECT, VERTEX_REGION, GOOGLE_APPLICATION_CREDENTIALS), as ask-model/index.js
// reads them. "mock" is the tests' and the local stack's: it streams a fixed reply and calls nothing.

// Vertex AI through LangChain, sending what ask-model/vertex.js sends: the person's question to the model and the
// credentials it needs, no library client-info. The library builds its own GoogleAuth and adds a User-Agent and a
// Client-Info header naming LangChain; here the client is outbound.js's googleAuth() (no client-info, the portal's
// User-Agent) and the library's info headers are empty.
export class PortalVertexChat extends ChatVertexAI {
  static lc_name() {
    return "PortalVertexChat";
  }

  // Application Default Credentials only, as ask-model/vertex.js: never an API key from GOOGLE_API_KEY.
  buildApiKey() {
    return undefined;
  }

  // Called from the base constructor, so the portal's client rides in on fields (portalAuth), not on this.
  buildAbstractedClient(fields) {
    const client = super.buildAbstractedClient(fields);
    // GAuthClient's fetch reads this.gauth on each call, so the portal's client replaces the library's own.
    client.gauth = fields?.portalAuth ?? googleAuth({ ...fields?.authOptions, scopes: ["https://www.googleapis.com/auth/cloud-platform"] });
    return client;
  }

  buildConnection(fields, client) {
    super.buildConnection(fields, client);
    for (const connection of [this.connection, this.streamedConnection]) {
      if (!connection) continue;
      connection._clientInfoHeaders = async () => ({});
      const format = connection.formatData.bind(connection);
      connection.formatData = async (input, parameters) => {
        const data = await format(input, parameters);
        return Array.isArray(input) && Array.isArray(data?.contents) ? { ...data, contents: signContents(data.contents, input, this.model) } : data;
      };
    }
  }

  /** One response (a streamed chunk, or a whole reply) as generation chunks, one per part that is not a thought. */
  responseChunks(data, options) {
    const usage = this.streamUsage !== false && options?.streamUsage !== false;
    return partResponses(data).map((one) => {
      const chunk = signed(this.connection.api.responseToChatGeneration({ data: one }), candidateOf(one)?.content?.parts?.[0]);
      if (usage) chunk.message = new AIMessageChunk({ ...chunk.message, usage_metadata: chunk.generationInfo?.usage_metadata });
      return chunk;
    });
  }

  async *_streamResponseChunks(messages, options, runManager) {
    const parameters = this.invocationParams(options);
    const stream = (await this.streamedConnection.request(messages, parameters, options, runManager)).data;
    while (!stream.streamDone) {
      if (options.signal?.aborted) return;
      const output = await stream.nextChunk();
      const chunks = output !== null ? this.responseChunks(output, options)
        : [new ChatGenerationChunk({ text: "", generationInfo: { finishReason: "stop" }, message: new AIMessageChunk({ content: "" }) })];
      for (const chunk of chunks) {
        yield chunk;
        await runManager?.handleLLMNewToken(chunk.text ?? "", undefined, undefined, undefined, undefined, { chunk });
      }
    }
  }

  // Unstreamed, the whole reply is read part by part as a stream's chunks are, so both paths keep the same signatures.
  // Unlike the library's, these two paths emit no google-chunk custom event, and the unstreamed one returns the reply as
  // a chunk with no llmOutput; nothing in the portal reads either.
  async _generate(messages, options, runManager) {
    if (this.streaming) return super._generate(messages, options, runManager);
    options.signal?.throwIfAborted();
    const response = await this.connection.request(messages, this.invocationParams(options), options, runManager);
    const generation = this.responseChunks(response.data, options).reduce((all, chunk) => concat(all, chunk));
    await runManager?.handleLLMNewToken(generation.text || "");
    return { generations: [generation] };
  }
}

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

export function openCerebroModel(settings, env, options = {}) {
  if (options.model) return options.model;
  if (!settings.model) return undefined;
  if (settings.modelProvider === "mock") {
    return new MockCerebroChat({ responses: [new AIMessage(options.reply ?? "This is the mock model's answer.")] });
  }
  const location = env.VERTEX_REGION || "global";
  return new PortalVertexChat({
    model: settings.model,
    location,
    // Named, so LangChain's LangSmith gateway (LANGSMITH_GATEWAY) can never take the call elsewhere.
    endpoint: location === "global" ? "aiplatform.googleapis.com" : `${location}-aiplatform.googleapis.com`,
    temperature: 0,
    authOptions: {
      ...(env.VERTEX_PROJECT || env.BIGQUERY_PROJECT ? { projectId: env.VERTEX_PROJECT || env.BIGQUERY_PROJECT } : {}),
      ...(env.GOOGLE_APPLICATION_CREDENTIALS ? { keyFilename: env.GOOGLE_APPLICATION_CREDENTIALS } : {}),
    },
    ...(options.auth ? { portalAuth: options.auth } : {}),
  });
}
