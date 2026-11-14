import { vertexAskModel } from "./vertex.js";
import { mockAskModel } from "./mock.js";

// The model port exposes complete({ question, catalogue, direction? }) and returns JSON.
export function openAskModel(settings, env, options = {}) {
  if (!settings.model) return undefined;
  return settings.modelProvider === "mock"
    ? mockAskModel(options.reply)
    : vertexAskModel({ model: settings.model, project: env.VERTEX_PROJECT || env.BIGQUERY_PROJECT, region: env.VERTEX_REGION || "global", credentials: env.GOOGLE_APPLICATION_CREDENTIALS }, options);
}
