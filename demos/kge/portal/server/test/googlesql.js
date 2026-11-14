// What live BigQuery refuses that a stand-in answering every query would not
// (#208): a GoogleSQL reserved keyword used as a column name without backticks,
// and an ARRAY(SELECT ...) subquery over another table that refers to a row of
// the query around it, which BigQuery cannot de-correlate. checked(client) puts
// both checks in front of a stand-in client, refusing as BigQuery refuses.

export const RESERVED = new Set(`ALL AND ANY ARRAY AS ASC ASSERT_ROWS_MODIFIED AT BETWEEN BY CASE CAST COLLATE CONTAINS CREATE CROSS
CUBE CURRENT DEFAULT DEFINE DESC DISTINCT ELSE END ENUM ESCAPE EXCEPT EXCLUDE EXISTS EXTRACT FALSE FETCH FOLLOWING FOR FROM
FULL GROUP GROUPING GROUPS HASH HAVING IF IGNORE IN INNER INTERSECT INTERVAL INTO IS JOIN LATERAL LEFT LIKE LIMIT LOOKUP
MERGE NATURAL NEW NO NOT NULL NULLS OF ON OR ORDER OUTER OVER PARTITION PRECEDING PROTO QUALIFY RANGE RECURSIVE RESPECT
RIGHT ROLLUP ROWS SELECT SET SOME STRUCT TABLESAMPLE THEN TO TREAT TRUE UNBOUNDED UNION UNNEST USING WHEN WHERE WINDOW WITH
WITHIN`.split(/\s+/));

const word = /^[A-Za-z_]\w*$/;
const reserved = (token) => word.test(token) && RESERVED.has(token.toUpperCase());
const unquoted = (token) => token.replace(/^`|`$/g, "").toLowerCase();

export function tokens(sql) {
  return [...sql.matchAll(/`[^`]*`|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|@\w+|[A-Za-z_]\w*|\S/g)].map((match) => match[0]);
}

function closing(list, open) {
  let depth = 0;
  for (let index = open; index < list.length; index++) {
    if (list[index] === "(") depth++;
    if (list[index] === ")" && --depth === 0) return index;
  }
  return list.length;
}

// The column names a statement defines, as written: each column of a
// CREATE TABLE's list and each ADD COLUMN of an ALTER TABLE.
export function definedColumns(sql) {
  const list = tokens(sql), names = [];
  for (let index = 0; index < list.length; index++) {
    const upper = list[index].toUpperCase();
    if (upper === "CREATE" && list.slice(index, index + 6).some((token) => token.toUpperCase() === "TABLE")) {
      let open = index;
      while (open < list.length && list[open] !== "(" && list[open] !== ";" && list[open].toUpperCase() !== "AS") open++;
      if (list[open] !== "(") continue;
      const end = closing(list, open);
      let depth = 0, start = true;
      for (let at = open + 1; at < end; at++) {
        if (start) { names.push(list[at]); start = false; }
        if (list[at] === "(") depth++;
        if (list[at] === ")") depth--;
        if (list[at] === "," && depth === 0) start = true;
      }
      index = end;
    }
    if (upper === "ADD" && list[index + 1]?.toUpperCase() === "COLUMN") {
      let at = index + 2;
      if (list[at]?.toUpperCase() === "IF") at += 3;
      names.push(list[at]);
    }
  }
  return names;
}

// Refuses a reserved keyword written without backticks where it names a
// column: in a column definition, at the start of a path, or as a bare word
// that is also a column a table defines. Later path segments may be reserved.
export function refuseReserved(sql, columns = new Set()) {
  for (const name of definedColumns(sql)) if (reserved(name)) throw new Error(`Syntax error: Unexpected keyword ${name.toUpperCase()}`);
  const list = tokens(sql);
  list.forEach((token, index) => {
    if (!reserved(token) || list[index - 1] === ".") return;
    if (list[index + 1] === "." || columns.has(token.toLowerCase())) throw new Error(`Syntax error: Unexpected keyword ${token.toUpperCase()}`);
  });
}

// Refuses ARRAY(SELECT ... FROM <table> ...) that refers to an alias it does
// not define itself. One whose sources are all UNNEST(...) is allowed, as
// BigQuery allows it.
export function refuseCorrelatedArray(sql) {
  const list = tokens(sql);
  for (let index = 0; index < list.length; index++) {
    if (list[index].toUpperCase() !== "ARRAY" || list[index + 1] !== "(" || list[index + 2]?.toUpperCase() !== "SELECT") continue;
    const inner = list.slice(index + 2, closing(list, index + 1));
    const aliases = new Set();
    let table = false;
    inner.forEach((token, at) => {
      if (!["FROM", "JOIN"].includes(token.toUpperCase())) return;
      let next = at + 1;
      if (inner[next]?.toUpperCase() === "UNNEST") next = closing(inner, next + 1);
      else { table = true; aliases.add(unquoted(inner[next]).split(".").pop()); }
      if (inner[next + 1]?.toUpperCase() === "AS") next++;
      if (word.test(inner[next + 1] || "") && !RESERVED.has(inner[next + 1].toUpperCase())) aliases.add(inner[next + 1].toLowerCase());
    });
    if (!table) continue;
    inner.forEach((token, at) => {
      if (inner[at + 1] === "." && word.test(token) && !aliases.has(token.toLowerCase())) {
        throw new Error("Query error: Correlated subqueries that reference other tables are not supported unless they can be de-correlated, such as by transforming them into an efficient JOIN.");
      }
    });
  }
}

// A client whose every query is checked first, the columns it has seen
// defined remembered across queries, then answered by the stand-in given.
export function checked(client) {
  const columns = new Set();
  return { ...client, query: async (job) => {
    refuseReserved(job.query, columns);
    refuseCorrelatedArray(job.query);
    for (const name of definedColumns(job.query)) columns.add(unquoted(name));
    return client.query(job);
  } };
}
