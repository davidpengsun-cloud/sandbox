import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { literalsOf, wordsIn } from "./fixtures/source-literals.js";

// #707 requirement 8 and acceptance 8, over every module of Cerebro's server (review of PR #728, finding 4): the portal
// holds no meaning, comparison or "by" word list, and a word list added anywhere under src/cerebro/ fails here, not in
// the three modules of the meaning check, the intent check and the step alone (cerebro-meaning.test.js reads those
// three more closely: every string they hold). Every module's code is read, comments left out, for the two ways a
// list of words is declared:
//   - a regular expression that holds words: each is named below with its text's SHA-256 (its first 16 characters, base64url) and
//     the reason it may stand, so a new one, or a word added to one, is a reviewed change;
//   - a list of string literals (an array, so a Set or a Map made from one, or a string split into words): each is
//     given below as it stands.
// The allow-list says what each module may hold. The business exceptions are two, as #707 states them: Cerebro's
// request words (decision 10: intent.js's four lists, and the one of them that asks to be shown), and the one named,
// temporary rule that marks the "certified" option (decisions 8 and 17: meaning.js SUGGESTED_UNTIL_709). Every other
// entry is a contract word of KGE's or of a protocol, a field name, the card's own text, or the portal's time and
// follow-on words, which #707 leaves as they are. No entry is a client's, a pack's, a term's or a segment's name.
const FOLDER = new URL("../src/cerebro/", import.meta.url);
const digest = (text) => createHash("sha256").update(text).digest("base64url").slice(0, 16);
/** module: { regexes: [[the constant's name or null, its text's digest, why it may stand]], lists: [[word, ...]] } */
const ALLOWED = {
  "agent.js": {
    regexes: [
      ["TIME_WORD", "Osmg8VNKhlGAE0Ma", "the portal's time words, which #707 leaves as they are"],
      ["FOLLOW_ON", "qYLIoL5kRbodeAff", "the portal's follow-on words, which #707 leaves as they are"],
      ["ASK_ABOUT", "o8vRe1CgmBv9m3d5", "#621: the \"What can I ask about X?\" request"],
      ["WHERE_HELD", "LPlPdpxhoDniUkyf", "#622: a request for where data is held, which picks the catalogue net's tool"],
      ["NOT_AN_END", "xBYQpacWSLSZ0aJ8", "abbreviations that end no sentence, for cutting a reply into sentences"],
      ["BUSY", "MfDgKwvbi0cgE4Px", "a model provider's own words for a busy service"],
      ["LABEL", "mEqSjwXfF6DYfwM1", "KGE's answer-mode labels at the start of an answer's text, its contract and no pack's"],
      [null, "twXfGJYxI_b6Bn3Z", "KGE's behaviour word for a decline"],
    ],
    lists: [
      ["the","our","my","your","their","this","that","these","those","and","for","with","data","about"],
      ["label","term","name","measure"],
      ["node","nodes","paths","neighbours","hops"],
      ["history","answer_text","answer_mode","behaviour","reason","question","error","message"],
      ["display","history","failure_plain","document_answer","citable_passages","document_evidence_pending","graph_path","receipt"],
      ["too_long","stopped","access_changed","run_gone"],
      ["combined","figure","sum_with_verdict","sum_without_verdict","parts_with_reason","cite","cite_certified","glossary_definition"],
      ["run_time_limit","warehouse_time_limit"],
      ["This question exceeded its time limit; ask a narrower question.","The warehouse exceeded its time limit; ask a narrower question."],
      ["cite","cite_certified","glossary_definition"],
      ["refuse","blocked_access"],
      ["implementations","data_sets","defined_by","contexts","paths"],
      ["offer","suggest"],
      ["question","options"],
      ["definitions","figures"],
      ["definitions","figures"],
      ["agent","choose"],
      ["values","messages","updates","custom"],
      ["__proto__","constructor","prototype"],
      ["like","dislike"],
    ],
  },
  "beforeAsk.js": { regexes: [], lists: [] },
  "chat.js": {
    regexes: [
    ],
    lists: [
      ["business","steward","engineer"],
    ],
  },
  "conversations.js": { regexes: [], lists: [] },
  "documentAnswer.js": {
    regexes: [
      [null, "g2GZop-31gffr3lJ", "KGE's own markers in a document answer"],
      [null, "rBcfklDWb53DcgDI", "KGE's own markers in a document answer"],
    ],
    lists: [
      ["cite","cite_certified"],
      ["gold","silver"],
      ["gold","silver"],
    ],
  },
  "gatewayModel.js": {
    regexes: [
      [null, "M13Fqzmh1j9SDrPa", "a content type"],
    ],
    lists: [
    ],
  },
  "graph.js": {
    regexes: [
      [null, "WJHYi4mqD-QlTNty", "the bearer scheme"],
    ],
    lists: [
      ["cursor","limit","node"],
    ],
  },
  "intent.js": {
    regexes: [
      ["OPEN_REQUEST", "ezvyn4tGf1ajMJpI", "#707 decision 10: Cerebro's request words, an open request"],
      ["SHOW_REQUEST", "Q2xXwS3uI2ubLIPZ", "#707 decision 10: the open request that asks to be shown something"],
      ["DEFINITION", "pjMiab7-euU0suBP", "#707 decision 10: Cerebro's request words, a request for definitions"],
      ["FIGURE", "qHHkbLlYFbLd4aCK", "#707 decision 10: Cerebro's request words, a request for figures"],
      ["OTHER_OUTPUT", "12tSrUGax_8DqWRb", "#707 decision 10: Cerebro's request words, a request for another output"],
    ],
    lists: [
    ],
  },
  "meaning.js": {
    regexes: [
      ["SUGGESTED_UNTIL_709", "1FYsE7F2JDhsOf3w", "#707 decisions 8 and 17: the one named, temporary rule, until #709"],
    ],
    lists: [
    ],
  },
  "meaningPresentation.js": {
    regexes: [
    ],
    lists: [
      ["figure","sum_with_verdict","sum_without_verdict","combined"],
      ["glossary_definition","cite_certified","cite"],
    ],
  },
  "model.js": {
    regexes: [
      ["SIGNING", "LSTl5q7UJT0aeulW", "a model family's name"],
      ["TAKES_LEVEL", "LSTl5q7UJT0aeulW", "a model family's name"],
    ],
    lists: [
    ],
  },
  "offer.js": {
    regexes: [
    ],
    lists: [
      ["January","February","March","April","May","June","July","August","September","October","November","December"],
    ],
  },
  "periods.js": {
    regexes: [
      [null, "CH2H8fcMEPT-CJP3", "the portal's time words, which #707 leaves as they are"],
      [null, "9xVp9kmxMhFwPLVo", "the portal's time words, which #707 leaves as they are"],
    ],
    lists: [
    ],
  },
  "questions.js": {
    regexes: [
      [null, "h2t-Qy5y94_Pzl9G", "the card's own marker"],
    ],
    lists: [
      ["meaning","show","period","grouping"],
      ["period","grouping"],
      ["definitions","figures","both"],
      ["definitions","figures"],
    ],
  },
  "route.js": { regexes: [], lists: [] },
  "runs.js": {
    regexes: [
      [null, "EBRcd7aGoM9I2ZAC", "a tool's own error text"],
    ],
    lists: [
    ],
  },
  "status.js": {
    regexes: [
      ["TOO_LONG", "eY5AjNb5YDwXzY7P", "KGE's and the warehouse's words for a failure, for the working block"],
      ["UNSAFE", "XU-hv8rKuE2pVMo8", "KGE's and the warehouse's words for a failure, for the working block"],
      ["WAREHOUSE", "_jk6BHnntbet9MUa", "KGE's and the warehouse's words for a failure, for the working block"],
      [null, "A-xR06U1v2wwBPSK", "KGE's and the warehouse's words for a failure, and an article's vowel, for the working block"],
      [null, "0-3NFV1-Lvdyc6HD", "KGE's and the warehouse's words for a failure, and an article's vowel, for the working block"],
      ["COMPARISON", "mH_INWzA-Bhq7o1g", "#693: the working block's readout tells a comparison by these words. It is no part of the meaning or the intent check, and it predates #714; whether #707 requirement 8 means it to go is reported on #714"],
      [null, "RyQ2aGABrXM8psep", "KGE's and the warehouse's words for a failure, and an article's vowel, for the working block"],
    ],
    lists: [
      ["Context","Measure","Term","Acronym"],
      ["Filter","RowSelector"],
      ["matches","plans"],
    ],
  },
  "steward.js": {
    regexes: [
      ["HASH", "00dfjf-GuE5mtSK_", "an id's shape"],
    ],
    lists: [
      ["pending","decided"],
    ],
  },
  "telemetry-off.js": {
    regexes: [
    ],
    lists: [
      ["LANGSMITH_API_KEY","LANGCHAIN_API_KEY","LANGSMITH_ENDPOINT","LANGCHAIN_ENDPOINT","LANGSMITH_GATEWAY_API_KEY"],
    ],
  },
  "tools.js": {
    regexes: [
      [null, "whyAdhj8fS9mveoD", "a content type"],
    ],
    lists: [
      ["ask","get_context","read_graph","explore","explore_question","search","map_term","suggest","feedback"],
      ["retrieve","assist"],
      ["reference_sql","plan","trace","run_trace","path","versions","agent","physical_tables","failure_plain"],
      ["queries","checked"],
      ["stage","node_added","state_changed","edge_added"],
      ["event","stage","state","id"],
      ["id","label","type","state","name"],
      ["from","to","type","label","state"],
      ["question","options"],
      ["ask","question_run","stop_question_run"],
    ],
  },
  "usage.js": {
    regexes: [
    ],
    lists: [
      ["calls","input_tokens","output_tokens","thinking_tokens"],
    ],
  },
};
const REQUEST_WORDS = ["OPEN_REQUEST", "SHOW_REQUEST", "DEFINITION", "FIGURE", "OTHER_OUTPUT"];

/** What a module declares beyond its allow-list, and what the allow-list names that it no longer declares. */
function unlisted(name, source) {
  const allowed = ALLOWED[name];
  if (!allowed) return [`${name}: a module this check does not know; list what it may hold`];
  const { regexes, lists } = literalsOf(source);
  const found = [];
  const left = allowed.regexes.map(([constant, hash]) => `${constant ?? ""}/${hash}`);
  for (const regex of regexes.filter((each) => wordsIn(each.body).length)) {
    const at = left.indexOf(`${regex.name ?? ""}/${digest(regex.body)}`);
    if (at < 0) found.push(`${name}: a regular expression that matches on words: ${regex.name ?? "(unnamed)"} /${regex.body}/`);
    else left.splice(at, 1);
  }
  const lines = allowed.lists.map((list) => JSON.stringify(list));
  for (const list of lists) {
    const at = lines.indexOf(JSON.stringify(list));
    if (at < 0) found.push(`${name}: a list of words: ${JSON.stringify(list)}`);
    else lines.splice(at, 1);
  }
  return [...found, ...left.map((entry) => `${name}: the allow-list names a regular expression the module no longer holds: ${entry}`),
    ...lines.map((entry) => `${name}: the allow-list names a list the module no longer holds: ${entry}`)];
}
const modules = () => readdirSync(FOLDER).filter((name) => name.endsWith(".js")).sort();
const sourceOf = (name) => readFileSync(new URL(name, FOLDER), "utf8");

test("#707 acceptance 8: no module of Cerebro's server declares a word list but the ones named, each with its reason", () => {
  assert.deepEqual(modules(), Object.keys(ALLOWED).sort(), "every module under src/cerebro/ is read, and none is listed that is gone");
  assert.ok(modules().length >= 21);
  for (const name of modules()) assert.deepEqual(unlisted(name, sourceOf(name)), [], name);
  for (const [name, { regexes }] of Object.entries(ALLOWED)) for (const [constant, hash, why] of regexes) assert.ok(/^[\w-]{16}$/.test(hash) && why.trim().length > 10, `${name} ${constant}: a digest and a reason`);
});

test("#707 acceptance 8: the business exceptions are Cerebro's request words, in the intent check alone, and \"certified\", in the one named rule alone", () => {
  const holders = (constant) => Object.entries(ALLOWED).filter(([, { regexes }]) => regexes.some(([each]) => each === constant)).map(([name]) => name);
  for (const constant of REQUEST_WORDS) assert.deepEqual(holders(constant), ["intent.js"], constant);
  assert.deepEqual(holders("SUGGESTED_UNTIL_709"), ["meaning.js"]);
  // The word of the temporary rule, as a word a module matches a question or an option on: the rule itself, and
  // nowhere else but KGE's own answer-mode label, which is KGE's contract and no pack's (agent.js LABEL).
  const matching = modules().flatMap((name) => literalsOf(sourceOf(name)).regexes.filter((regex) => /certif/i.test(regex.body)).map((regex) => `${name}: ${regex.name}`));
  assert.deepEqual(matching, ["agent.js: LABEL", "meaning.js: SUGGESTED_UNTIL_709"]);
  const listed = modules().flatMap((name) => literalsOf(sourceOf(name)).lists.flat().filter((word) => /certif/i.test(word)));
  assert.deepEqual([...new Set(listed)], ["cite_certified"], "and in no list of words, but for KGE's own behaviour name, a contract word");
});

test("#707 acceptance 8: the check fails when a word list is added to any module, the agent's among them, as a Set, an array, a split string or a regular expression", () => {
  for (const name of ["agent.js", "questions.js", "tools.js", "status.js", "beforeAsk.js"]) {
    const planted = (line) => unlisted(name, `${sourceOf(name)}\n${line}\n`);
    assert.deepEqual(planted("const NEGATION = new Set(['not', 'never', 'without']);"), [`${name}: a list of words: ["not","never","without"]`], `${name}: a Set of words`);
    assert.deepEqual(planted("const BY = [\"by\", \"per\"];"), [`${name}: a list of words: ["by","per"]`], `${name}: an array of words`);
    assert.deepEqual(planted("const STOP = new Set('a an and are'.split(' '));"), [`${name}: a list of words: ["a an and are"]`], `${name}: a string split into words`);
    assert.match(planted("const COMPARED = /\\b(?:versus|compared to|against)\\b/i;")[0], /a regular expression that matches on words: COMPARED/, `${name}: a regular expression`);
    assert.match(planted("const hedged = (text) => /\\bpartly\\b/.test(text);")[0], /a regular expression that matches on words: \(unnamed\)/, `${name}: a regular expression in line`);
  }
  assert.match(unlisted("intent.js", sourceOf("intent.js").replace("tell me about|", "tell me about|walk me through|"))[0], /OPEN_REQUEST/, "a word added to a request-word list is a change this check sees");
  assert.match(unlisted("meaning.js", sourceOf("meaning.js").replace("\\bcertified\\b", "\\b(?:certified|frozen)\\b"))[0], /SUGGESTED_UNTIL_709/, "the named rule holds one word");
  assert.deepEqual(unlisted("new.js", "const WORDS = ['retail', 'wholesale'];"), ["new.js: a module this check does not know; list what it may hold"]);
});

test("the literal reader refuses a source that ends inside a string, a template, a regular expression or a block comment, and never reads past its end", () => {
  for (const [source, what] of [["const a = 'open", "a string"], ["const a = \"open\nconst b = 1;", "a string"], ["const a = `open ${1}", "a template"], ["const a = `open ${ {", "a template's \\$\\{"],
    ["const a = /open", "a regular expression"], ["const a = /[open/;", "a regular expression"], ["const a = 1; /* open", "a block comment"], ["const a = 'ends in a slash\\", "a string"], ["const a = /ends\\", "a regular expression"]]) {
    assert.throws(() => literalsOf(source), (error) => error instanceof SyntaxError && new RegExp(`^source-literals: ${what} opened at character \\d+ is not closed$`).test(error.message), source);
  }
  assert.deepEqual(literalsOf("const a = ['x', \"y\"]; const b = /w+/g; // 'not read\n/* \"nor this */ const c = `t ${['p', 'q'].length}`;"),
    { regexes: [{ name: "b", body: "w+", flags: "g" }], strings: ["x", "y", "p", "q", "t  "], lists: [["p", "q"], ["x", "y"]] });
  assert.deepEqual(literalsOf("const one = ['alone']; const mixed = ['a', 1]; const words = 'a b'.split(' ');").lists, [["a b"]], "one string is no list, nor is an array that holds anything else");
});
