import { randomBytes, scryptSync } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

try {
  if (process.argv.length !== 3) throw new Error("usage: node src/passwords.js <identity-directory>");
  const directory = resolve(process.argv[2]);
  const principals = JSON.parse(readFileSync(resolve(directory, "principals.json"), "utf8"));
  const names = ["passwords.json", "passwords.txt", "session_secret"];
  if (names.some((name) => existsSync(resolve(directory, name)))) throw new Error("Credential files already exist; use a new directory to rotate them.");
  if (!Array.isArray(principals) || principals.some((p) => !p || typeof p.principal !== "string" || !/^[A-Za-z0-9_-]+$/.test(p.principal) || !Array.isArray(p.processes)) || new Set(principals.map((p) => p.principal)).size !== principals.length) throw new Error("principals.json must contain distinct KGE principal records.");
  const people = principals.filter((p) => p.processes.some((process) => ["querying", "certification"].includes(process)));
  if (!people.length) throw new Error("principals.json contains no people.");
  const plain = Object.fromEntries(people.map((p) => [p.principal, randomBytes(18).toString("base64url")]));
  const hashes = Object.fromEntries(Object.entries(plain).map(([name, password]) => {
    const salt = randomBytes(16);
    const digest = scryptSync(password, salt, 32, { N: 16384, r: 8, p: 1 });
    return [name, `scrypt$16384$8$1$${salt.toString("base64url")}$${digest.toString("base64url")}`];
  }));
  const outputs = [JSON.stringify(hashes, null, 2), Object.entries(plain).map(([name, password]) => `${name} ${password}`).join("\n"), randomBytes(36).toString("base64url")];
  names.forEach((name, index) => writeFileSync(resolve(directory, name), `${outputs[index]}\n`, { mode: 0o600, flag: "wx" }));
  console.log("Created passwords.json, passwords.txt and session_secret. Hand each person their password, then delete passwords.txt.");
} catch {
  console.error("Could not create credentials. Give an identity directory with valid principals.json and no existing password or session-secret files.");
  process.exitCode = 1;
}
