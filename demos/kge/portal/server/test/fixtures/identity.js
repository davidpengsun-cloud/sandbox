import { randomBytes, scryptSync } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { join } from "node:path";

export const password = randomBytes(18).toString("base64url");

export async function localCredentials(directory) {
  const salt = randomBytes(16);
  const hash = `scrypt$16384$8$1$${salt.toString("base64url")}$${scryptSync(password, salt, 32, { N: 16384, r: 8, p: 1 }).toString("base64url")}`;
  const people = [
    { principal: "analyst_retail", role: "viewer", reads: ["retail"], processes: ["querying"] },
    { principal: "admin", role: "admin", reads: ["wealth"], processes: ["querying", "certification"] },
    { principal: "machine", role: "admin", reads: ["retail"], processes: ["tagging"] },
  ];
  const env = { PORTAL_IDENTITIES_FILE: join(directory, "principals.json"), PORTAL_PASSWORDS_FILE: join(directory, "passwords.json"), PORTAL_SESSION_SECRET: randomBytes(32).toString("base64url") };
  await writeFile(env.PORTAL_IDENTITIES_FILE, JSON.stringify(people));
  await writeFile(env.PORTAL_PASSWORDS_FILE, JSON.stringify(Object.fromEntries(people.map((p) => [p.principal, hash]))));
  return env;
}
