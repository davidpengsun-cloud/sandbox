import { BigQuery } from "@google-cloud/bigquery";
import { gaxios, GoogleAuth } from "google-auth-library";

// What leaves the portal on a call it makes. Two kinds of header never do:
// trace and correlation headers (but the traceparent tracing.js
// traceHeaders() adds itself to a call bound for TRACE_PROPAGATE_TO), and
// Google's client-info, the x-goog-api-client (and any x-goog-*-client)
// header naming Node and each library's version, which Google's Node
// libraries add to every call. What authentication needs passes:
// authorization, x-goog-user-project, x-goog-api-key and Metadata-Flavor.
// Calls to KGE pass through outboundHeaders() (kge.js request()); every
// Google client the portal makes comes from googleAuth() or bigQuery() below,
// which send the same headers less client-info, with the portal's own
// User-Agent in place of the libraries' names and versions.

export const TRACE_HEADERS = new Set([
  "traceparent", "tracestate", "baggage", "x-cloud-trace-context", "grpc-trace-bin", "b3", "x-b3-traceid",
  "x-b3-spanid", "x-b3-parentspanid", "x-b3-sampled", "x-b3-flags", "uber-trace-id", "sentry-trace",
  "x-datadog-trace-id", "x-datadog-parent-id", "x-datadog-sampling-priority", "x-amzn-trace-id", "request-id",
  "request-context", "elastic-apm-traceparent", "newrelic", "x-request-id", "x-correlation-id",
]);
const GOOGLE_CLIENT = /^x-goog-[a-z0-9-]*client$/;
export const USER_AGENT = "kge-portal";

export function sentHeader(name) {
  const lower = String(name).toLowerCase();
  return !TRACE_HEADERS.has(lower) && !GOOGLE_CLIENT.test(lower);
}

export function outboundHeaders(headers = {}) {
  return Object.fromEntries(Object.entries(headers).filter(([name]) => sentHeader(name)));
}

// A Google call's headers, in place: no trace or client-info header, and the
// portal's User-Agent alone.
function googleHeaders(headers) {
  for (const name of [...headers.keys()]) if (!sentHeader(name) || name === "user-agent") headers.delete(name);
  headers.set("user-agent", USER_AGENT);
  return headers;
}

// google-auth-library's GoogleAuth (application default credentials, a
// credentials file or an API key, as options say) whose token and API calls
// carry no client-info. Its clients go through one gaxios transporter that
// rewrites each call's headers last, and without the library's own request
// interceptor (useAuthRequestParameters: false), which adds the client-info
// and User-Agent and logs. authorizeRequest(), which BigQuery calls with its
// own client-info already set, returns the headers rewritten the same way.
export function googleAuth(options = {}) {
  const transporter = new gaxios.Gaxios();
  transporter.interceptors.request.add({ resolved: async (config) => Object.assign(config, { headers: googleHeaders(new Headers(config.headers)) }) });
  const auth = new GoogleAuth({ ...options, clientOptions: { ...options.clientOptions, transporter, useAuthRequestParameters: false } });
  const authorize = auth.authorizeRequest.bind(auth);
  auth.authorizeRequest = async (opts) => {
    const authorized = await authorize(opts);
    return Object.assign(authorized, { headers: googleHeaders(new Headers(authorized.headers)) });
  };
  return auth;
}

// A BigQuery client on googleAuth(projectId, scopes and auth's options):
// @google-cloud/common sets its client-info on each request after its
// interceptors, then authorizes it, so the authorized request is where the
// headers are rewritten.
export function bigQuery({ projectId, scopes = ["https://www.googleapis.com/auth/bigquery"], ...options } = {}, auth = {}) {
  return new BigQuery({ ...options, projectId, authClient: googleAuth({ projectId, scopes, ...auth }) });
}
