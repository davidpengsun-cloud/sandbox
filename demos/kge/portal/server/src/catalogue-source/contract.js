import Ajv2020 from "ajv/dist/2020.js";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const schemaPath = fileURLToPath(new URL("../../contract/catalogue.v1.schema.json", import.meta.url));
const validate = new Ajv2020({ allErrors: true }).compile(JSON.parse(readFileSync(schemaPath, "utf8")));

export function validateCatalogue(value) {
  if (validate(value)) return value;
  const detail = validate.errors.map((error) => `${error.instancePath || "/"} ${error.message}`).join("; ");
  throw new Error(`catalogue provider did not satisfy contract v1: ${detail}`);
}
