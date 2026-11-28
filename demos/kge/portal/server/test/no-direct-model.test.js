import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { test } from 'node:test';

// Both the provider clients and their endpoints are forbidden; Google auth for storage, tracing and KGE is allowed.
const sdk = /(?:\b(?:import|export)\s+(?:[^;\n]*?\s+from\s+)?|\b(?:import|require)\s*\()\s*["'](?:@langchain\/(?:google-vertexai|openai|anthropic)|@google-cloud\/vertexai|@google\/genai|@anthropic-ai\/sdk|openai)(?:\/[^"']*)?["']/i;
const endpoint = /(?:aiplatform\.googleapis\.com|generativelanguage\.googleapis\.com|api\.openai\.com|api\.anthropic\.com|:(?:stream)?generateContent\b|:streamRawPredict\b)/i;
function joinedLiterals(source) {
  let previous;
  do {
    previous = source;
    source = source.replace(/(['"])([^'"\\]*)\1\s*\+\s*(['"])([^'"\\]*)\3/g, (_m, _a, a, _b, b) => JSON.stringify(a + b));
  } while (source !== previous);
  return source;
}
export function directModelAccess(source) { return sdk.test(source) || endpoint.test(joinedLiterals(source)); }
async function sourceFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  return (await Promise.all(entries.map((entry) => entry.isDirectory() ? sourceFiles(join(dir, entry.name))
    : entry.isFile() && entry.name.endsWith('.js') ? [join(dir, entry.name)] : []))).flat();
}

test('portal production modules contain no direct model provider SDK or endpoint', async () => {
  const root = fileURLToPath(new URL('../src/', import.meta.url));
  for (const path of await sourceFiles(root)) assert.equal(directModelAccess(await readFile(path, 'utf8')), false, path);
});

test('portal production dependencies do not include model-provider SDKs', async () => {
  const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
  for (const name of ['@langchain/google-vertexai', '@langchain/openai', '@langchain/anthropic', '@google-cloud/vertexai', '@google/genai', '@anthropic-ai/sdk', 'openai']) {
    assert.equal(Object.hasOwn(manifest.dependencies ?? {}, name), false, name);
  }
});

test('guard detects imports, provider URLs, RPC methods, and concatenated string literals', () => {
  for (const input of ["import {ChatVertexAI} from '@langchain/google-vertexai'", "const c = require('openai')",
    "fetch('https://aiplatform.googleapis.com/v1')", "fetch('https://' + 'api.openai.com')", "const method = ':streamGenerateContent'"])
    assert.equal(directModelAccess(input), true, input);
  assert.equal(directModelAccess("import {GoogleAuth} from 'google-auth-library'"), false);
});
