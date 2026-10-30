import { readFileSync, statSync } from "node:fs";
import { extname, resolve } from "node:path";

export const LOGO_LIMIT = 262144;
const REFUSAL = "PORTAL_LOGO must name an svg, png, jpg, jpeg or ico image of 256 KB or less; an SVG must be a plain drawing, with no doctype, script, event handler, animation, style sheet, embedded image, character reference or external link.";
const TYPES = { png: "image/png", jpeg: "image/jpeg", ico: "image/x-icon", svg: "image/svg+xml" };
const EXTENSIONS = { ".svg": "svg", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".ico": "ico" };

function startsWith(bytes, hex) {
  const signature = Buffer.from(hex, "hex");
  return bytes.length >= signature.length && bytes.subarray(0, signature.length).equals(signature);
}

const SVG_ELEMENTS = new Set(("svg g defs title desc metadata path rect circle ellipse line polyline polygon text tspan use symbol linearGradient " +
  "radialGradient stop clipPath mask pattern marker filter feBlend feColorMatrix feComponentTransfer feComposite " +
  "feDropShadow feFlood feFuncA feFuncB feFuncG feFuncR feGaussianBlur feMerge feMergeNode feMorphology feOffset").split(" "));
const SVG_NAMESPACES = { xmlns: "http://www.w3.org/2000/svg", "xmlns:xlink": "http://www.w3.org/1999/xlink" };
const STYLE_FUNCTIONS = new Set(["rgb", "rgba", "hsl", "hsla", "url"]);
const XML_DECLARATION = /^<\?xml[ \t\n\r][^<>?]*\?>/;
const SVG_TOKEN = /<!--([^<>-]*(?:-[^<>-]+)*)-->|<\/([A-Za-z][A-Za-z0-9-]*)[ \t\n\r]*>|<([A-Za-z][A-Za-z0-9-]*)((?:[ \t\n\r]+[A-Za-z][A-Za-z0-9:._-]*[ \t\n\r]*=[ \t\n\r]*(?:"[^"<]*"|'[^'<]*'))*)[ \t\n\r]*(\/?)>|([^<]+)/y;
const SVG_ATTRIBUTE = /[ \t\n\r]+([A-Za-z][A-Za-z0-9:._-]*)[ \t\n\r]*=[ \t\n\r]*(?:"([^"<]*)"|'([^'<]*)')/g;
const SVG_REFUSED = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff\\]|&#|&(?!(?:amp|lt|gt|quot|apos);)|<!(?!--)|<\?|@import|data:|javascript:|url[ \t\n\r]*\((?![ \t\n\r'"]*#)/i;
const LOCAL_REFERENCE = /^#[A-Za-z_][A-Za-z0-9_.-]*$/;
export const LOGO_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox";

function safeAttributes(attributes) {
  const found = [...attributes.matchAll(SVG_ATTRIBUTE)].map((m) => [m[1], m[2] ?? m[3] ?? ""]);
  if (new Set(found.map(([name]) => name)).size !== found.length) return false;
  for (const [name, value] of found) {
    if (name.includes(":") && name !== "xmlns:xlink" && name !== "xlink:href") return false;
    if (Object.hasOwn(SVG_NAMESPACES, name) && value !== SVG_NAMESPACES[name]) return false;
    if (name.toLowerCase().startsWith("on") || name.toLowerCase() === "attributename") return false;
    if ((name === "href" || name === "xlink:href") && !LOCAL_REFERENCE.test(value)) return false;
    if (name === "style" && [...value.matchAll(/([A-Za-z-]*)[ \t\n\r]*\(/g)].some((m) => !STYLE_FUNCTIONS.has(m[1].toLowerCase()))) return false;
  }
  return true;
}

/* A simple logo, the same rule as KGE's (kge/adapters/brand.py), both run on test/fixtures/logo-probes.json. */
export function safeSvg(source) {
  let text = source.startsWith("\ufeff") ? source.slice(1) : source;
  const declaration = XML_DECLARATION.exec(text);
  if (declaration) text = text.slice(declaration[0].length);
  if (SVG_REFUSED.test(text)) return false;
  const open = [];
  let roots = 0;
  let position = 0;
  while (position < text.length) {
    SVG_TOKEN.lastIndex = position;
    const token = SVG_TOKEN.exec(text);
    if (!token) return false;
    position = SVG_TOKEN.lastIndex;
    const [, , closing, opening, attributes, empty, words] = token;
    if (words !== undefined && !open.length && words.replace(/[ \t\n\r]/g, "")) return false;
    if (closing !== undefined && (!open.length || open.pop() !== closing)) return false;
    if (opening !== undefined) {
      if (!open.length) roots += 1;
      if (!SVG_ELEMENTS.has(opening) || (!open.length && (opening !== "svg" || roots > 1))) return false;
      if (!safeAttributes(attributes)) return false;
      if (!empty) open.push(opening);
    }
  }
  return roots === 1 && !open.length;
}

export function logoType(bytes) {
  if (startsWith(bytes, "89504e470d0a1a0a")) return "png";
  if (startsWith(bytes, "ffd8ff")) return "jpeg";
  if (startsWith(bytes, "00000100")) return "ico";
  const text = bytes.toString("utf8");
  if (Buffer.from(text, "utf8").equals(bytes) && safeSvg(text)) return "svg";
  return null;
}

/* PORTAL_LOGO is read and checked once, as the portal starts, and served from memory: a new file takes effect on restart. */
export function checkedLogo(setting) {
  if (!setting) return null;
  const path = resolve(setting);
  let size;
  try {
    const stat = statSync(path);
    size = stat.isFile() ? stat.size : -1;
  } catch {
    size = -1;
  }
  const expected = EXTENSIONS[extname(path).toLowerCase()];
  if (size < 0 || size > LOGO_LIMIT || !expected) throw new Error(REFUSAL);
  const bytes = readFileSync(path);
  const type = logoType(bytes);
  if (!type || (type === "svg") !== (expected === "svg")) throw new Error(REFUSAL);
  return { bytes, contentType: TYPES[type] };
}

export const BRAND_NAME = "Data Discovery Portal";

export function checkedAddress(setting, value) {
  if (!value) return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    url = null;
  }
  if (!url || !["http:", "https:"].includes(url.protocol) || !url.host || url.search || url.hash || /[?#]/.test(value)) {
    throw new Error(`${setting} must be KGE's http or https address, such as https://kge.example.com.`);
  }
  return value;
}

export function brandRoutes(app, { env, ui, configured, ask = true }) {
  const logo = configured || { path: resolve(ui, "brand/logo.png"), contentType: TYPES.png };
  app.get("/brand", (_req, res) => res.json({
    name: BRAND_NAME,
    logo: true,
    kge_url: ask && env.KGE_URL ? env.KGE_PUBLIC_URL || env.KGE_URL : null,
  }));
  app.get(["/brand/logo.png", "/brand/logo.svg", "/brand/logo"], (_req, res) => {
    res.set({
      "Content-Type": logo.contentType,
      "Cache-Control": "public, max-age=300",
      "X-Content-Type-Options": "nosniff",
      "X-Brand-Logo": configured ? "configured" : "built-in",
      "Content-Security-Policy": LOGO_POLICY,
    });
    if (configured) res.send(configured.bytes);
    else res.sendFile(logo.path, { cacheControl: false });
  });
}
