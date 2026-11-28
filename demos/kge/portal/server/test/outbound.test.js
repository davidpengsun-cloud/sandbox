import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { BigQuery } from "@google-cloud/bigquery";
import { GoogleAuth } from "google-auth-library";
import { bigQuery, googleAuth, outboundHeaders, sentHeader, TRACE_HEADERS, USER_AGENT } from "../src/outbound.js";

// A stand-in for a Google API: records each call's headers and answers an
// empty BigQuery dataset list.
async function google(context) {
  const seen = [];
  const server = createServer((req, res) => {
    seen.push({ url: req.url, headers: req.headers });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ kind: "bigquery#datasetList", datasets: [] }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  return { url: `http://127.0.0.1:${server.address().port}`, seen };
}

// An API key the stand-in never checks: the one credential that needs no token request.
const PLANTED_KEY = "planted-key";
const clientInfo = (headers) => Object.keys(headers).filter((name) => /^x-goog-[a-z0-9-]*client$/.test(name));
const LIBRARIES = /google-api-nodejs-client|gl-node|gccl|gcloud-node|node-fetch/;

test("a call through google-auth-library's gaxios carries no client-info, and what auth needs passes", async (context) => {
  const api = await google(context);
  // The control: google-auth-library's own client sends x-goog-api-client, so the check below can fail.
  await (await new GoogleAuth({ apiKey: PLANTED_KEY, projectId: "p" }).getClient()).request({ url: `${api.url}/control` });
  assert.deepEqual(clientInfo(api.seen[0].headers), ["x-goog-api-client"]);
  assert.match(api.seen[0].headers["user-agent"], LIBRARIES);

  const client = await googleAuth({ apiKey: PLANTED_KEY, projectId: "p" }).getClient();
  await client.request({ url: `${api.url}/quiet` });
  // A header a library or a caller sets itself (the external-account token exchange sets x-goog-api-client).
  const planted = { "x-goog-api-client": "gl-node/planted", "x-goog-gax-client": "planted", traceparent: "00-0af7651916cd43dd8448eb211c80319c-b7ad6b71a9203331-01", "x-goog-user-project": "billing-project", "Metadata-Flavor": "Google", "User-Agent": "google-api-nodejs-client/planted" };
  await client.transporter.request({ url: `${api.url}/planted`, headers: planted });
  for (const call of api.seen.slice(1)) {
    assert.deepEqual(clientInfo(call.headers), [], call.url);
    assert.equal(call.headers["user-agent"], USER_AGENT, call.url);
    assert.equal(call.headers.traceparent, undefined, call.url);
  }
  assert.equal(api.seen[1].headers["x-goog-api-key"], PLANTED_KEY);
  assert.equal(api.seen[2].headers["x-goog-user-project"], "billing-project");
  assert.equal(api.seen[2].headers["metadata-flavor"], "Google");
});

test("BigQuery's calls carry no client-info", async (context) => {
  const api = await google(context);
  // The control: @google-cloud/bigquery on its own sends x-goog-api-client.
  await new BigQuery({ projectId: "p", apiEndpoint: api.url, authClient: new GoogleAuth({ apiKey: PLANTED_KEY, projectId: "p" }) }).getDatasets();
  assert.deepEqual(clientInfo(api.seen[0].headers), ["x-goog-api-client"]);
  assert.match(api.seen[0].headers["user-agent"], LIBRARIES);

  const client = bigQuery({ projectId: "p", apiEndpoint: api.url }, { apiKey: PLANTED_KEY, projectId: "p" });
  const [datasets] = await client.getDatasets();
  assert.deepEqual(datasets, []);
  const call = api.seen[1];
  assert.match(call.url, /^\/bigquery\/v2\/projects\/p\/datasets/);
  assert.deepEqual(clientInfo(call.headers), []);
  assert.equal(call.headers["user-agent"], USER_AGENT);
  assert.equal(call.headers["x-goog-api-key"], PLANTED_KEY);
  assert.ok(client.authClient instanceof GoogleAuth);
});

test("outbound headers drop trace and client-info headers in any case and keep the rest", () => {
  for (const name of TRACE_HEADERS) assert.equal(sentHeader(name.toUpperCase()), false, name);
  for (const name of ["x-goog-api-client", "X-Goog-Api-Client", "x-goog-gax-client"]) assert.equal(sentHeader(name), false, name);
  for (const name of ["authorization", "x-goog-user-project", "x-goog-api-key", "metadata-flavor", "x-serverless-authorization", "content-type"]) assert.equal(sentHeader(name), true, name);
  assert.deepEqual(outboundHeaders({ accept: "application/json", Traceparent: "t", "x-goog-api-client": "gl-node/1", authorization: "Bearer t" }), { accept: "application/json", authorization: "Bearer t" });
  assert.deepEqual(outboundHeaders(), {});
});
