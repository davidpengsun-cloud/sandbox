import { readFile } from "node:fs/promises";
import { openRepository } from "./repository.js";
import { normalizeUsername } from "./portal-access.js";

const [filename, actor] = process.argv.slice(2);
if (!filename || !actor || process.argv.length !== 4) {
  console.error("Usage: node src/migrate-identities.js <principal-to-username.json> <operator-username>");
  process.exitCode = 2;
} else {
  let repository;
  try {
    const by = normalizeUsername(actor);
    const mapping = JSON.parse(await readFile(filename, "utf8"));
    repository = openRepository();
    console.log(JSON.stringify(await repository.migrateIdentities(mapping, by)));
  } catch (error) {
    console.error(`Portal identity migration failed: ${error.message}`);
    process.exitCode = 1;
  } finally {
    await repository?.close();
  }
}
