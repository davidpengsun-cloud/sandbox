import { useEffect, useState } from "react";

export const DEFAULT_PRODUCT_SHORT_NAME = "ALTA";
export const DEFAULT_PRODUCT_NAME = `Client ${DEFAULT_PRODUCT_SHORT_NAME}`;

export const DEFAULT_CONFIG = Object.freeze({
  knowledgeHome: true,
  apps: Object.freeze({ certify: true, context: false, discover: true }),
  certifyUrl: null,
  productName: DEFAULT_PRODUCT_NAME,
  productShortName: DEFAULT_PRODUCT_SHORT_NAME,
  logoTitle: false,
  contextMcpUrl: null,
});

const text = (value) => (typeof value === "string" && value.trim() ? value.trim() : null);

export function normaliseConfig(body) {
  const raw = body && typeof body === "object" ? body : {};
  const apps = raw.apps && typeof raw.apps === "object" ? raw.apps : {};
  return {
    knowledgeHome: raw.knowledgeHome !== false,
    configuredApps: { certify: apps.certify !== false, context: apps.context !== false, discover: apps.discover !== false },
    apps: { certify: apps.certify !== false, context: apps.context !== false && Boolean(text(raw.contextMcpUrl)), discover: apps.discover !== false },
    certifyUrl: text(raw.certifyUrl),
    productName: text(raw.productName) || DEFAULT_PRODUCT_NAME,
    productShortName: text(raw.productShortName) || DEFAULT_PRODUCT_SHORT_NAME,
    logoTitle: raw.logoTitle === true,
    contextMcpUrl: text(raw.contextMcpUrl),
  };
}

/* The product's short name from the served settings, or ALTA before they are known. */
export const shortNameOf = (config) => text(config?.productShortName) || DEFAULT_PRODUCT_SHORT_NAME;

let known = null;
let pending = null;

export const knownConfig = () => known;

export function loadConfig() {
  if (known) return Promise.resolve(known);
  if (!pending) {
    pending = (async () => {
      try {
        const response = await fetch("/api/config", { credentials: "same-origin" });
        if (!response.ok) throw new Error("Settings unavailable");
        const body = await response.json();
        if (!body || typeof body.knowledgeHome !== "boolean" ||
            !["certify", "context", "discover"].every((app) => typeof body.apps?.[app] === "boolean")) {
          throw new Error("Settings unavailable");
        }
        known = normaliseConfig(body);
        return known;
      } finally {
        pending = null;
      }
    })();
  }
  return pending;
}

export function forgetConfig() {
  known = null;
  pending = null;
}

/* The settings, or null until the first answer is in. */
export function useConfig() {
  const [config, setConfig] = useState(knownConfig);
  useEffect(() => {
    let live = true;
    if (!config) loadConfig().then(
      (value) => live && setConfig(value),
      () => live && setConfig({ knowledgeHome: false, apps: {}, unavailable: true, retry: () => setConfig(null) }),
    );
    return () => {
      live = false;
    };
  }, [config]);
  return config;
}

export function useShortName() {
  return shortNameOf(useConfig());
}
