import { createHmac, randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { promisify } from "node:util";

const derive = promisify(scrypt);
const COOKIE = "portal_session";
const LIFETIME = 12 * 3600 * 1000;
const HASH = /^scrypt\$16384\$8\$1\$[A-Za-z0-9_-]{22}\$[A-Za-z0-9_-]{43}$/;
const denied = () => Object.assign(new Error("Sign in required."), { status: 401 });

function readJson(path, setting) {
  if (!path) throw new Error(`${setting} is required without KGE`);
  try { return JSON.parse(readFileSync(path, "utf8")); }
  catch { throw new Error(`${setting} must name a readable JSON file`); }
}

export function openIdentity(env, clock = Date.now) {
  if (typeof env.PORTAL_SESSION_SECRET !== "string" || env.PORTAL_SESSION_SECRET.trim().length < 32) throw new Error("PORTAL_SESSION_SECRET must be at least 32 characters without KGE");
  if (env.PORTAL_COOKIE_SECURE && !["true", "false"].includes(env.PORTAL_COOKIE_SECURE)) throw new Error("PORTAL_COOKIE_SECURE must be true or false");
  const credentials = () => {
    const principals = readJson(env.PORTAL_IDENTITIES_FILE, "PORTAL_IDENTITIES_FILE");
    const hashes = readJson(env.PORTAL_PASSWORDS_FILE, "PORTAL_PASSWORDS_FILE");
    if (!Array.isArray(principals) || !principals.length || principals.some((p) => !p || typeof p.principal !== "string" || !p.principal || typeof p.role !== "string" || !Array.isArray(p.reads) || p.reads.some((u) => typeof u !== "string" || !u) || !Array.isArray(p.processes) || p.processes.some((p) => typeof p !== "string")) || new Set(principals.map((p) => p.principal)).size !== principals.length) throw new Error("PORTAL_IDENTITIES_FILE must contain distinct KGE principals with role, reads and processes");
    if (!hashes || Array.isArray(hashes) || typeof hashes !== "object" || !Object.values(hashes).every((h) => typeof h === "string" && HASH.test(h))) throw new Error("PORTAL_PASSWORDS_FILE must contain KGE scrypt password hashes");
    return { principals, hashes };
  };
  credentials();
  const sessions = new Map();
  const sign = (value) => createHmac("sha256", env.PORTAL_SESSION_SECRET).update(value).digest("base64url");
  const token = (req) => /(?:^|;\s*)portal_session=([A-Za-z0-9_.-]+)(?:;|$)/.exec(req.headers.cookie || "")?.[1];
  const prune = () => { for (const [key, session] of sessions) if (session.expires <= clock()) sessions.delete(key); };
  const cookieOptions = { httpOnly: true, sameSite: "lax", secure: env.PORTAL_COOKIE_SECURE !== "false", path: "/" };
  const person = (principals, name) => principals.find((p) => p.principal === name && p.processes.some((process) => ["querying", "certification"].includes(process)));
  return {
    async signIn(req, res, throttle) {
      const { username, password } = req.body;
      const { principals, hashes } = credentials();
      const user = person(principals, username);
      const stored = Object.hasOwn(hashes, username) && user ? hashes[username] : undefined;
      const parts = stored?.split("$");
      const salt = parts ? Buffer.from(parts[4], "base64url") : Buffer.alloc(16);
      const digest = await derive(password, salt, 32, { N: 16384, r: 8, p: 1 });
      if (!stored || !timingSafeEqual(digest, Buffer.from(parts[5], "base64url"))) return throttle ? throttle.failed() : res.status(401).json({ detail: "wrong username or password" });
      throttle?.passed();
      prune();
      if (sessions.size >= 10000) return res.status(503).json({ detail: "Sign-in is temporarily unavailable." });
      const key = randomBytes(32).toString("base64url");
      const value = `${key}.${sign(key)}`;
      sessions.delete(token(req));
      sessions.set(value, { principal: username, passwordHash: stored, expires: clock() + LIFETIME });
      res.cookie(COOKIE, value, { ...cookieOptions, maxAge: LIFETIME });
      return res.json({ principal: username });
    },
    identity(req) {
      prune();
      const session = sessions.get(token(req));
      if (!session) throw denied();
      const { principals, hashes } = credentials();
      const user = person(principals, session.principal);
      if (!user || hashes[user.principal] !== session.passwordHash) throw denied();
      return { principal: user.principal, units: [...user.reads] };
    },
    signOut(req, res) {
      sessions.delete(token(req));
      res.clearCookie(COOKIE, cookieOptions);
      return res.json({ signed_out: true });
    },
  };
}
