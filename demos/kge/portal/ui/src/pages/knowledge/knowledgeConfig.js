import { DEFAULT_PRODUCT_NAME, normaliseConfig as normalisePortalConfig, shortNameOf } from "../../services/config";

// The settings the Knowledge pages read from GET /api/config, normalised: null until the answer is known, else the
// product's name, the client's name taken from it, the Certify link, and which apps are set up here.
export const DEFAULT_PRODUCT = DEFAULT_PRODUCT_NAME;
export const DEFAULT_CLIENT = "Client";

export function normaliseConfig(raw) {
  const config = raw && typeof raw === "object" && "config" in raw ? raw.config : raw;
  if (!config || typeof config !== "object") return null;
  const { apps } = normalisePortalConfig(config);
  const productName = typeof config.productName === "string" && config.productName.trim() ? config.productName.trim() : DEFAULT_PRODUCT;
  const suffix = ` ${shortNameOf(config)}`;
  return {
    productName,
    clientName: productName.endsWith(suffix) && productName.length > suffix.length ? productName.slice(0, -suffix.length) : DEFAULT_CLIENT,
    certifyUrl: typeof config.certifyUrl === "string" && config.certifyUrl ? config.certifyUrl : null,
    apps: { certify: apps.certify !== false, context: apps.context !== false, discover: apps.discover !== false },
  };
}
