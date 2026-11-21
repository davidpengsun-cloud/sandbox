import { readFileSync, writeFileSync } from "node:fs";

const roots = ["portal/server/package-lock.json", "portal/ui/package-lock.json"];
const packages = new Map();
for (const root of roots) {
  const lock = JSON.parse(readFileSync(root, "utf8"));
  for (const [path, value] of Object.entries(lock.packages || {})) {
    if (!path.startsWith("node_modules/") || !value.version) continue;
    const name = path.slice(path.lastIndexOf("node_modules/") + "node_modules/".length);
    packages.set(`${name}@${value.version}`, value.license || "Not declared in package metadata");
  }
}
const lines = ["# Portal third-party notices", "", "Generated from the dependency closures in the portal npm lockfiles. Do not edit by hand.", "", "## npm packages", ""];
for (const [item, license] of [...packages.entries()].sort(([a], [b]) => a.localeCompare(b))) lines.push(`- ${item}: ${license}`);
// Source copied into the portal, not installed from npm: each keeps its licence beside the copy.
const copied = [
  "- Agent Chat UI (github.com/langchain-ai/agent-chat-ui, commit 0d21f7ff4c45287797e876dc6d8b150b6f277b1e): MIT, Copyright (c) 2025 Brace Sproul. Adapted in portal/ui/src/components/agentchat/; the licence text is in its LICENSE.md file.",
];
lines.push("", "## Copied source", "", ...copied);
lines.push("");
const output = `${lines.join("\n")}`;
if (process.argv.includes("--check")) {
  const committed = readFileSync("portal/THIRD_PARTY_NOTICES.md", "utf8");
  if (committed !== output) {
    console.error("portal/THIRD_PARTY_NOTICES.md: stale; run kit/generate_npm_notices.mjs");
    process.exit(1);
  }
} else writeFileSync("portal/THIRD_PARTY_NOTICES.md", output);
