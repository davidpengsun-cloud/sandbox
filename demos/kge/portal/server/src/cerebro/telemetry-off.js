// Third-party telemetry is off, with no exception: LangChain's and LangSmith's own tracing, their OpenTelemetry
// bridge and usage pings never run in the portal, whatever the environment says. Imported first by agent.js, so the
// switches are set before any LangChain module reads them; a value already set otherwise is overwritten. The kit
// (compose.yaml) and Terraform (modules/portal) set the same values, so a process that never loads the agent has them
// too. The portal's own tracing (tracing.js, off unless TRACING turns it on) is the only tracing.

export const SWITCHED_OFF = Object.freeze({
  LANGSMITH_TRACING: "false",
  LANGSMITH_TRACING_V2: "false",
  LANGCHAIN_TRACING: "false",
  LANGCHAIN_TRACING_V2: "false",
  LANGSMITH_OTEL_ENABLED: "false",
  LANGCHAIN_OTEL_ENABLED: "false",
  LANGSMITH_GATEWAY: "false",
  DO_NOT_TRACK: "1",
});

// Settings that would hand LangSmith a destination or a key: unset, so nothing has anywhere to send to.
export const CLEARED = Object.freeze(["LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_ENDPOINT", "LANGCHAIN_ENDPOINT", "LANGSMITH_GATEWAY_API_KEY"]);

export function telemetryOff(env = process.env) {
  Object.assign(env, SWITCHED_OFF);
  for (const name of CLEARED) delete env[name];
  return env;
}

telemetryOff();
