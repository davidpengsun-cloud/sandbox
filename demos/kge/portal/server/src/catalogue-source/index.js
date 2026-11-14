import { openFileCatalogueSource } from "./file.js";
import { openKgeCatalogueSource } from "./kge.js";

export function openCatalogueSource(env, kge) {
  const provider = env.PORTAL_CATALOGUE_PROVIDER || (env.KGE_URL || kge ? "kge" : env.PORTAL_CATALOGUE_FILE ? "file" : "");
  if (provider === "kge") {
    if (!env.KGE_URL && !kge) throw new Error("PORTAL_CATALOGUE_PROVIDER=kge requires KGE_URL");
    return openKgeCatalogueSource(kge);
  }
  if (provider === "file") {
    if (!env.PORTAL_CATALOGUE_FILE) throw new Error("PORTAL_CATALOGUE_PROVIDER=file requires PORTAL_CATALOGUE_FILE");
    return openFileCatalogueSource(env.PORTAL_CATALOGUE_FILE);
  }
  if (provider) throw new Error("PORTAL_CATALOGUE_PROVIDER must be kge or file");
  throw new Error("Set KGE_URL or PORTAL_CATALOGUE_FILE (or PORTAL_CATALOGUE_PROVIDER) before starting the portal");
}
