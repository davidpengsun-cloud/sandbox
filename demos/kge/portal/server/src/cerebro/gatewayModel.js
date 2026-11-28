import { BaseChatModel } from '@langchain/core/language_models/chat_models';
import { AIMessage, AIMessageChunk } from '@langchain/core/messages';
import { ChatGenerationChunk } from '@langchain/core/outputs';

export const SIGNATURES = 'function_call_signatures';
export const REPLAY = 'gateway_tool_metadata';
const FRAME_LIMIT = 8 * 1024 * 1024;
const CONTINUATION_LIMIT = 400 * 1024;
const plain = (content) => typeof content === 'string' ? content : Array.isArray(content) ? content.map((x) => x?.text ?? '').join('') : '';

/** Convert LangChain's server-owned conversation to the provider-neutral KGE port contract. */
export function gatewayMessages(messages) {
  const instructions = [];
  const result = [];
  const names = new Map();
  for (const message of messages) {
    const type = message._getType?.() ?? message.type;
    if (type === 'system') { instructions.push(plain(message.content)); continue; }
    if (type === 'human') { result.push({ role: 'user', parts: [{ kind: 'text', text: plain(message.content) }] }); continue; }
    if (type === 'ai') {
      const parts = [];
      if (plain(message.content)) parts.push({ kind: 'text', text: plain(message.content) });
      for (const call of message.tool_calls ?? []) {
        if (!call.id || !call.name || !call.args || typeof call.args !== 'object') throw new Error('Invalid assistant tool call.');
        names.set(call.id, call.name);
        const signature = message.additional_kwargs?.[SIGNATURES]?.[call.id];
        const metadata = message.additional_kwargs?.[REPLAY]?.[call.id];
        parts.push({ kind: 'call', call_id: call.id, name: call.name, value: call.args,
          ...(signature ? { signature } : {}), ...(metadata ? { metadata } : {}) });
      }
      if (parts.length) result.push({ role: 'assistant', parts });
      continue;
    }
    if (type === 'tool') {
      const name = message.name || names.get(message.tool_call_id);
      if (!name || !message.tool_call_id) throw new Error('Tool result has no matching call.');
      let value = message.content;
      if (typeof value === 'string') { try { value = JSON.parse(value); } catch { /* retain text */ } }
      result.push({ role: 'tool', parts: [{ kind: 'result', call_id: message.tool_call_id, name, value }] });
      continue;
    }
    throw new Error('Unsupported message in model conversation.');
  }
  return { instructions: instructions.join('\n\n'), messages: result };
}

function responseError(status, code, message) {
  const error = new Error(message || 'The model gateway could not answer.');
  error.status = status;
  error.code = code;
  if (code === 'quota_exceeded' || code === 'model_quota_exceeded') error.failure = 'model_quota';
  return error;
}

/** Decode arbitrarily split UTF-8 SSE frames. A missing final frame is a failed model call. */
export async function* gatewayEvents(response) {
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { data = {}; }
    throw responseError(response.status, data?.code, typeof data?.detail === 'string' ? data.detail : undefined);
  }
  if (!response.body || !/^text\/event-stream\b/i.test(response.headers.get('content-type') ?? '')) throw new Error('Invalid model gateway stream.');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let complete = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      if (buffer.length > FRAME_LIMIT) throw new Error('Model gateway event is too large.');
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const lines = frame.split(/\r?\n/);
        const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message';
        const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n');
        if (!data) continue;
        let parsed; try { parsed = JSON.parse(data); } catch { throw new Error('Invalid model gateway event.'); }
        if (event === 'error') throw responseError(undefined, parsed?.code, parsed?.message);
        if (event === 'final') { if (complete || parsed?.finish !== 'STOP') throw new Error('The model response was incomplete.'); complete = true; }
        else if (complete) throw new Error('Unexpected event after model completion.');
        if (event === 'part' || event === 'final') yield { event, data: parsed };
      }
      if (done) break;
    }
    if (buffer.trim() || !complete) throw new Error('The model response was incomplete.');
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

/** A model instance belongs to one signed-in request; it never stores a bearer across requests. */
export class GatewayChat extends BaseChatModel {
  constructor({ kge, req, purpose = 'agent', tools = [], expectedModel }) { super({}); this.kge = kge; this.req = req; this.purpose = purpose; this.tools = tools; this.expectedModel = expectedModel; }
  _llmType() { return 'kge-gateway'; }
  bindTools(tools) {
    return new GatewayChat({ kge: this.kge, req: this.req, purpose: this.purpose, expectedModel: this.expectedModel,
      tools: tools.map((tool) => tool.function ?? tool) });
  }
  async *_streamResponseChunks(messages, options = {}, runManager) {
    if (!this.req?.modelQuestionId || !this.req?.modelConversationId) throw new Error('The model question context is unavailable.');
    const payload = { purpose: this.purpose, question_id: this.req.modelQuestionId, conversation_id: this.req.modelConversationId,
      ...(this.expectedModel ? { expected_model: this.expectedModel } : {}),
      ...gatewayMessages(messages), tools: this.tools };
    const response = await this.kge.modelChat(this.req, payload, { signal: options.signal });
    const calls = [];
    let final;
    for await (const { event, data } of gatewayEvents(response)) {
      if (event === 'final') { final = data; continue; }
      if (data?.kind === 'text' && typeof data.text === 'string') {
        yield new ChatGenerationChunk({ text: data.text, message: new AIMessageChunk({ content: data.text }) });
        await runManager?.handleLLMNewToken(data.text);
      } else if (data?.kind === 'call' && data.call_id && data.name && data.value && typeof data.value === 'object') {
        if (data.metadata !== undefined && (!data.metadata || typeof data.metadata !== 'object' || Array.isArray(data.metadata)
          || Buffer.byteLength(JSON.stringify(data.metadata)) > CONTINUATION_LIMIT
          || (data.metadata.claude_continuation !== undefined && (typeof data.metadata.claude_continuation !== 'string' || data.metadata.claude_continuation.length > 400_000)))) throw new Error('The model continuation exceeds its safe size.');
        calls.push(data);
      }
      else throw new Error('Invalid model gateway part.');
    }
    if (!final) throw new Error('The model response was incomplete.');
    for (const [index, call] of calls.entries()) {
      yield new ChatGenerationChunk({ text: '', message: new AIMessageChunk({ content: '',
        tool_call_chunks: [{ type: 'tool_call_chunk', id: call.call_id, name: call.name, args: JSON.stringify(call.value), index }],
        additional_kwargs: { ...(call.signature ? { [SIGNATURES]: { [call.call_id]: call.signature } } : {}),
          ...(call.metadata ? { [REPLAY]: { [call.call_id]: call.metadata } } : {}) },
      }) });
    }
    const input = final.tokens_in; const output = final.tokens_out;
    yield new ChatGenerationChunk({ text: '', message: new AIMessageChunk({ content: '',
      ...(Number.isInteger(input) && Number.isInteger(output) ? { usage_metadata: { input_tokens: input, output_tokens: output, total_tokens: input + output } } : {}),
      response_metadata: { model: final.model, finish: final.finish, reduced: final.reduced },
    }) });
  }
  async _generate(messages, options, runManager) {
    let text = ''; const calls = []; const signatures = {}; const metadata = {}; let usage; let response;
    for await (const chunk of this._streamResponseChunks(messages, options, runManager)) {
      text += chunk.text;
      for (const item of chunk.message.tool_call_chunks ?? []) calls.push({ id: item.id, name: item.name, args: JSON.parse(item.args), type: 'tool_call' });
      Object.assign(signatures, chunk.message.additional_kwargs?.[SIGNATURES]);
      Object.assign(metadata, chunk.message.additional_kwargs?.[REPLAY]);
      usage = chunk.message.usage_metadata ?? usage;
      response = chunk.message.response_metadata ?? response;
    }
    const message = new AIMessage({ content: text, tool_calls: calls, additional_kwargs: {
      ...(Object.keys(signatures).length ? { [SIGNATURES]: signatures } : {}), ...(Object.keys(metadata).length ? { [REPLAY]: metadata } : {}),
    }, ...(usage ? { usage_metadata: usage } : {}), ...(response ? { response_metadata: response } : {}) });
    return { generations: [{ text, message }] };
  }
}
