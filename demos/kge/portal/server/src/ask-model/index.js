import { gatewayAskModel } from "./gateway.js";
import { mockAskModel } from "./mock.js";

// The model port exposes complete({ question, catalogue, direction? }) and returns JSON.
export function openAskModel(settings, _env, options = {}) {
  if (settings.modelProvider === "mock") return settings.model ? mockAskModel(options.reply) : undefined;
  return options.kge ? gatewayAskModel(options.kge, settings.model || undefined) : undefined;
}
