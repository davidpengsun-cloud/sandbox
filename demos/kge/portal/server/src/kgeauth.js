const METADATA = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity";
const HEADER = "x-serverless-authorization";
const CALLER_HEADER = "x-caller-token";
const MARGIN_MS = 5 * 60 * 1000;

export const noServiceAuth = { headers: async () => ({}) };

function expiry(token) {
  try {
    const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString("utf8"));
    return Number(claims.exp) * 1000;
  } catch {
    return 0;
  }
}

function metadataToken(audience, options = {}) {
  const get = options.fetch || fetch;
  const now = options.now || Date.now;
  let cached = { token: "", until: 0 };
  let minting = null;
  const mint = async () => {
    const url = `${options.metadataUrl || METADATA}?audience=${encodeURIComponent(audience)}&format=full`;
    const response = await get(url, { headers: { "Metadata-Flavor": "Google" } });
    if (!response.ok) throw Object.assign(new Error(`metadata server returned ${response.status}`), { status: 502 });
    const token = (await response.text()).trim();
    cached = { token, until: expiry(token) - MARGIN_MS };
  };
  return async () => {
    if (!cached.token || now() >= cached.until) {
      minting = minting || mint().finally(() => { minting = null; });
      await minting;
    }
    return cached.token;
  };
}

export function metadataServiceAuth(audience, options = {}, callerAudience = "") {
  const service = metadataToken(audience, options);
  const caller = !callerAudience ? null : callerAudience === audience ? service : metadataToken(callerAudience, options);
  return {
    headers: async () => {
      const headers = { [HEADER]: `Bearer ${await service()}` };
      if (caller) headers[CALLER_HEADER] = await caller();
      return headers;
    },
  };
}

export function openServiceAuth(env = process.env, options = {}) {
  const onCloudRun = Boolean(env.K_SERVICE);
  const audience = env.KGE_AUDIENCE || (env.KGE_URL && new URL(env.KGE_URL).origin);
  if (!onCloudRun || !audience) return noServiceAuth;
  return metadataServiceAuth(audience, options, env.KGE_CALLER_AUDIENCE || "");
}
