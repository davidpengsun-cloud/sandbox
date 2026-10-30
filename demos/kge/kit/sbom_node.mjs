import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

// The portal image's bill of materials, CycloneDX 1.5 JSON, as kit/sbom.py makes KGE's:
//   node kit/sbom_node.mjs kge-portal:<version> portal/Dockerfile [--base <image>]
// The npm packages from npm ls in the image; the OS packages from dpkg-query or rpm, whichever the image has, else the
// rpm database itself (with none of them, the property kge:os-packages and standard error say so); the base from the
// Dockerfile's ARG BASE_IMAGE default, held to its digest, or the base given as --base (or, without it,
// PORTAL_BASE_IMAGE), recorded as given and marked unpinned when it names no digest. Commands run as uid 0 with no
// network and the image's entrypoint set aside.
const argv = process.argv.slice(2);
let given = process.env.PORTAL_BASE_IMAGE || "";
if (argv.length === 4 && argv[2] === "--base") given = argv.splice(2, 2)[1];
const [image, dockerfile] = argv;
if (argv.length !== 2) throw new Error("usage: sbom_node.mjs <image> <Dockerfile> [--base <image>]");
const run = (...command) => execFileSync("docker", ["run", "--rm", "--platform", "linux/amd64", "--network", "none", "--user", "0", "--entrypoint", command[0], image, ...command.slice(1)], { encoding: "utf8", maxBuffer: 64 << 20 });
const inspect = JSON.parse(execFileSync("docker", ["image", "inspect", image], { encoding: "utf8" }))[0];
const npmOutput = run("npm", "ls", "--all", "--json", "--long").trim();
const npm = npmOutput ? JSON.parse(npmOutput) : { dependencies: {} };
const components = new Map();
function visit(node) {
  for (const [name, value] of Object.entries(node.dependencies || {})) {
    if (value.version) components.set(`pkg:npm/${encodeURIComponent(name)}@${encodeURIComponent(value.version)}`, { type: "library", name, version: value.version, purl: `pkg:npm/${encodeURIComponent(name)}@${encodeURIComponent(value.version)}` });
    visit(value);
  }
}
visit(npm);

const OS_PACKAGES = "if [ -r /etc/os-release ]; then . /etc/os-release; fi; echo \"${ID:-unknown}-${VERSION_ID:-}\"; "
  + "if command -v dpkg-query >/dev/null 2>&1; then echo dpkg; dpkg-query -W -f '${Package}\\t${Version}\\t${Architecture}\\n'; "
  + "elif command -v rpm >/dev/null 2>&1; then echo rpm; rpm -qa --qf '%{NAME}\\t%{EPOCH}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\n'; "
  + "elif [ -r /var/lib/rpm/rpmdb.sqlite ]; then echo rpmdb; node --experimental-sqlite --no-warnings -e \"$1\" /var/lib/rpm/rpmdb.sqlite; "
  + "else echo none; fi";
// A base with an rpm database but no rpm (a package manager stripped out): the image's own node reads each package's
// header from the database (name, epoch, version, release and arch) and prints it as rpm -qa would.
const RPMDB = `const db = new (require("node:sqlite").DatabaseSync)(process.argv[1]);
for (const { blob } of db.prepare("select blob from Packages").all()) {
  const b = Buffer.from(blob), count = b.readInt32BE(0), data = b.subarray(8 + 16 * count), tags = {};
  for (let i = 0; i < count; i++) {
    const at = 8 + 16 * i, tag = b.readInt32BE(at), kind = b.readInt32BE(at + 4), offset = b.readInt32BE(at + 8);
    if (kind === 6) tags[tag] = data.subarray(offset, data.indexOf(0, offset)).toString();
    else if (kind === 4) tags[tag] = String(data.readInt32BE(offset));
  }
  console.log([tags[1000] || "", tags[1003] || "(none)", (tags[1001] || "") + "-" + (tags[1002] || ""), tags[1022] || "(none)"].join("\\t"));
}`;
const NO_PACKAGE_TOOL = "neither dpkg-query nor rpm nor an rpm database is in the image: its OS packages are not listed";
const [distro = "", listed = "", ...packages] = run("sh", "-c", OS_PACKAGES, "sh", RPMDB).split("\n");
const tool = ["dpkg", "rpm", "rpmdb"].includes(listed) ? listed : "none";
const vendor = distro.split("-")[0];
for (const line of packages.filter(Boolean)) {
  if (tool === "dpkg") {
    const [name, version, arch] = line.split("\t");
    const purl = `pkg:deb/${vendor}/${name}@${encodeURIComponent(version)}?arch=${arch}&distro=${distro}`;
    components.set(purl, { type: "library", name, version, purl });
  } else if (tool === "rpm" || tool === "rpmdb") {
    const [name, epoch, release, arch] = line.split("\t");
    if (name === "gpg-pubkey") continue;
    const purl = `pkg:rpm/${vendor}/${name}@${encodeURIComponent(release)}?arch=${arch}${epoch === "(none)" ? "" : `&epoch=${epoch}`}&distro=${distro}`;
    components.set(purl, { type: "library", name, version: epoch === "(none)" ? release : `${epoch}:${release}`, purl });
  }
}

const text = readFileSync(dockerfile, "utf8").replace(/\\\r?\n/g, " ");
const first = text.search(/^\s*FROM\s/im);
const args = {};
for (const [, name, value = ""] of text.slice(0, Math.max(first, 0)).matchAll(/^\s*ARG\s+(\w+)(?:=(\S*))?/gim)) args[name] = value.replace(/^["']|["']$/g, "");
if (given) args.BASE_IMAGE = given;
const copied = new Set([...text.matchAll(/^\s*COPY\s+(?:--\S+\s+)*?--from=(\S+)/gim)].map((m) => m[1].toLowerCase()));
const stages = [...text.matchAll(/^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?/gim)];
stages.forEach(([, raw, stage], index) => {
  const final = index === stages.length - 1;
  if (!final && !copied.has((stage || "").toLowerCase())) return;
  const ref = raw.replace(/\$\{(\w+)\}|\$(\w+)/g, (_, a, b) => {
    if (!args[a || b]) throw new Error(`${dockerfile}: FROM ${raw} names ARG ${a || b}, which has no value`);
    return args[a || b];
  });
  const isGiven = Boolean(given) && /\$\{?BASE_IMAGE\b/.test(raw);
  const found = ref.match(/^(?<repo>[^@]+?)(?::(?<tag>[^:/@]+))?(?:@sha256:(?<digest>[0-9a-f]{64}))?$/);
  if (!found || (!found.groups.digest && !isGiven)) throw new Error(`${dockerfile}: FROM ${ref} is not held to a digest`);
  const { repo, tag = "", digest } = found.groups;
  const properties = [{ name: "kge:dockerfile:stage", value: final ? "final" : stage }];
  if (isGiven) properties.push({ name: "kge:base:given", value: ref });
  const component = { type: "container", name: repo, version: tag, properties };
  if (digest) component.hashes = [{ alg: "SHA-256", content: digest }];
  else properties.push({ name: "kge:base:pinned", value: "false: given without a digest" });
  components.set(ref, component);
});

if (given && ![...components.values()].some((c) => c.properties?.some((p) => p.name === "kge:base:given"))) throw new Error(`${dockerfile}: a base was given (${given}), but no stage that reaches the image is FROM its ARG BASE_IMAGE`);
const id = inspect.Id.split(":").pop();
const labels = inspect.Config.Labels || {};
const properties = [{ name: "kge:image:id", value: inspect.Id }];
if (labels["kge.local-only"]) properties.push({ name: "kge:local-only", value: labels["kge.local-only"] });
if (tool === "none") {
  process.stderr.write(`${image}: ${NO_PACKAGE_TOOL}\n`);
  properties.push({ name: "kge:os-packages", value: NO_PACKAGE_TOOL });
}
const readWith = `npm ls and ${{ dpkg: "dpkg-query", rpm: "rpm", rpmdb: "the rpm database" }[tool] || "no OS package tool"}, read from the image`;
const bom = { bomFormat: "CycloneDX", specVersion: "1.5", serialNumber: `urn:uuid:${createHash("sha256").update(inspect.Id).digest("hex").slice(0, 32)}`, version: 1, metadata: { tools: { components: [{ type: "application", name: "kit/sbom_node.mjs", description: readWith }] }, component: { type: "container", "bom-ref": image, name: image.split(":")[0], version: labels["org.opencontainers.image.version"] || "", hashes: [{ alg: "SHA-256", content: id }], properties } }, components: [...components.values()].sort((a, b) => (a.purl || a.name).localeCompare(b.purl || b.name)) };
process.stdout.write(`${JSON.stringify(bom, null, 1)}\n`);
