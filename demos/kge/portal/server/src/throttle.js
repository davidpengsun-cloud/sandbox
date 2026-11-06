export const RATE = 10;
export const FAILURES = 5;
export const WINDOW = 900;
export const LOCKOUT = 300;
export const KEEP = 10000;

export function createThrottle(clock = () => performance.now() / 1000) {
  const tries = new Map();
  const fails = new Map();
  const wait = (key, now) => {
    const t = (tries.get(key) || []).filter((at) => now - at < 60);
    const f = (fails.get(key) || []).filter((at) => now - at < WINDOW);
    if (t.length) tries.set(key, t); else tries.delete(key);
    if (f.length) fails.set(key, f); else fails.delete(key);
    const rate = t.length >= RATE ? t[t.length - RATE] + 60 - now : 0;
    const locked = f.length >= FAILURES ? f[f.length - 1] + LOCKOUT - now : 0;
    return Math.max(rate, locked, 0);
  };
  return {
    attempt(key) {
      const now = clock();
      if (tries.size + fails.size > KEEP) for (const k of new Set([...tries.keys(), ...fails.keys()])) wait(k, now);
      const seconds = wait(key, now);
      if (seconds === 0) tries.set(key, [...(tries.get(key) || []), now]);
      return Math.ceil(seconds);
    },
    failed(key) {
      const now = clock();
      fails.set(key, [...(fails.get(key) || []), now]);
      return Math.ceil(wait(key, now));
    },
    passed(key) { fails.delete(key); tries.delete(key); },
  };
}

export function clientAddress(req, env = process.env) {
  if (env.K_SERVICE) {
    const forwarded = String(req.headers["x-forwarded-for"] || "").split(",").map((part) => part.trim()).filter(Boolean);
    if (forwarded.length) return forwarded[forwarded.length - 1];
  }
  return req.socket.remoteAddress || "";
}
