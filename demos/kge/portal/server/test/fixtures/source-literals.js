// Tests only: the literals of a JavaScript module's code, with its comments left out, for checks that read what a
// module matches on (#707 acceptance 8). literalsOf(source) gives { regexes: [{ name, body, flags }], strings: [text],
// lists: [[text, ...]] }: each regular expression literal with the constant it is assigned to (or undefined), each
// string or template literal's own text (a template's ${...} parts are read as code), and each list of words the code
// declares: an array of two or more string literals and nothing else (so a Set or a Map made from one), and a string
// literal split into words (.split). A source that ends inside a string, a template, a regular expression or a block
// comment is refused with a SyntaxError that says where it opened: the reader never runs past the end of the text.
const BEFORE_REGEX = new Set(["(", ",", "=", ":", "[", "!", "&", "|", "?", "{", "}", ";", "+", "-", "*", "%", "<", ">", "~", "^", "\n"]);

/** A regular expression may also follow one of these keywords, or the arrow of a function. */
const AFTER_KEYWORD = /(?:^|[^\w$.])(?:return|case|typeof|void|in|of|do|else|yield|await|throw)\s*$/;

/** A string literal's stand-in in the code the reader keeps: its place among the strings, between two marks. */
const MARK = "\u0001";
const HELD = `${MARK}(\\d+)${MARK}`;
const LIST = new RegExp(`\\[\\s*${HELD}(?:\\s*,\\s*${HELD})+\\s*,?\\s*\\]`, "g");
const SPLIT = new RegExp(`${HELD}\\s*\\.\\s*split\\s*\\(`, "g");

export function literalsOf(source) {
  const regexes = [];
  const strings = [];
  const lists = [];
  let last = "\n";
  let at = 0;
  const code = [];
  const unterminated = (what, from) => new SyntaxError(`source-literals: ${what} opened at character ${from} is not closed`);
  const hold = (text) => {
    strings.push(text);
    code.push(`${MARK}${strings.length - 1}${MARK}`);
    last = "s";
  };
  const read = () => {
    while (at < source.length) {
      const c = source[at];
      if (c === "/" && source[at + 1] === "/") { at = source.indexOf("\n", at); if (at < 0) at = source.length; continue; }
      if (c === "/" && source[at + 1] === "*") {
        const end = source.indexOf("*/", at + 2);
        if (end < 0) throw unterminated("a block comment", at);
        at = end + 2;
        continue;
      }
      if (c === "'" || c === '"') {
        const from = at;
        let text = "";
        for (at += 1; source[at] !== c; at += 1) {
          if (at >= source.length || source[at] === "\n") throw unterminated("a string", from);
          if (source[at] === "\\") { text += source[at]; at += 1; if (at >= source.length) throw unterminated("a string", from); }
          text += source[at];
        }
        at += 1;
        hold(text);
        continue;
      }
      if (c === "`") {
        const from = at;
        let text = "";
        for (at += 1; source[at] !== "`"; at += 1) {
          if (at >= source.length) throw unterminated("a template", from);
          if (source[at] === "\\") { if (at + 1 >= source.length) throw unterminated("a template", from); text += source[at] + source[at + 1]; at += 1; continue; }
          if (source[at] === "$" && source[at + 1] === "{") {
            at += 2;
            let depth = 1;
            const start = at;
            while (depth) {
              if (at >= source.length) throw unterminated("a template's ${", start - 2);
              if (source[at] === "{") depth += 1;
              if (source[at] === "}") depth -= 1;
              at += 1;
            }
            const inner = literalsOf(source.slice(start, at - 1));
            regexes.push(...inner.regexes);
            strings.push(...inner.strings);
            lists.push(...inner.lists);
            at -= 1;
            text += " ";
            continue;
          }
          text += source[at];
        }
        at += 1;
        strings.push(text);
        code.push(`${MARK}t${MARK}`);
        last = "s";
        continue;
      }
      if (c === "/" && (BEFORE_REGEX.has(last) || (last === "w" && AFTER_KEYWORD.test(code.join(""))))) {
        const from = at;
        let body = "";
        let inClass = false;
        for (at += 1; inClass || source[at] !== "/"; at += 1) {
          if (at >= source.length || source[at] === "\n") throw unterminated("a regular expression", from);
          if (source[at] === "\\") { body += source[at]; at += 1; if (at >= source.length) throw unterminated("a regular expression", from); } else if (source[at] === "[") inClass = true; else if (source[at] === "]") inClass = false;
          body += source[at];
        }
        at += 1;
        const flags = source.slice(at).match(/^[a-z]*/)[0];
        at += flags.length;
        regexes.push({ name: code.join("").match(/(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*$/)?.[1], body, flags });
        last = "r";
        continue;
      }
      code.push(c);
      if (!/\s/.test(c) || c === "\n") last = /[\w$)\]]/.test(c) ? "w" : c;
      at += 1;
    }
  };
  read();
  const kept = code.join("");
  for (const match of kept.matchAll(LIST)) lists.push([...match[0].matchAll(new RegExp(HELD, "g"))].map((each) => strings[Number(each[1])]));
  for (const match of kept.matchAll(SPLIT)) lists.push([strings[Number(match[1])]]);
  return { regexes, strings, lists };
}

/** The words a regular expression's body or a string holds: runs of two or more letters, its escapes (\b, \s, \p{L},
 *  \uD800) left out. */
export const wordsIn = (text) => text.replace(/\\p\{[^}]*\}|\\u[0-9A-Fa-f]{4}|\\+[A-Za-z]/g, " ").match(/[A-Za-z]{2,}/g) ?? [];
