import { createHash } from "node:crypto";
import { unreachable } from "./knowledge.js";

export function knowledgeIdentity(kge, local, clock = Date.now) {
  const verified = new Map();
  const key = (req) => createHash("sha256").update(JSON.stringify([req.headers.cookie || "", req.headers.authorization || ""])).digest("hex");
  const prune = () => { for (const [id, held] of verified) if (held.expires <= clock()) verified.delete(id); };
  return {
    forget(req) { verified.delete(key(req)); },
    async identity(req) {
      if (!kge) return local.identity(req);
      prune();
      const id = key(req);
      try {
        const value = await kge.identity(req);
        if (req.headers.cookie || req.headers.authorization) {
          if (verified.size >= 10000) verified.delete(verified.keys().next().value);
          verified.set(id, { value, expires: clock() + 300000 });
        }
        return value;
      } catch (error) {
        if (!unreachable(error)) { verified.delete(id); throw error; }
        const held = verified.get(id);
        if (!held) throw error;
        return { ...held.value, fallback: true };
      }
    },
  };
}
