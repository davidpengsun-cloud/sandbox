"""The tools' logic: the onboarding guide's page, the presets, the summary, the profile served, the versions and the SQL
check."""
from __future__ import annotations

import json
import re
from html import escape

from kge.pipeline.profile import Profile
from kge.pipeline.access import permissions
from kge.pipeline.settings import DEFAULT_PRODUCT_NAME, ROOT, context_app_name, product_name, settings
from kge.pipeline.sqlguard import schema_allowlist
from kge.services.ask import AGENT_ARMS, PATHS, PRESETS, STAGES, Engine
from kge.services.tools.markdown import render_markdown
from kge.services.identity import SignedBearer


GUIDES = {"knowledge-engineer": "Knowledge engineer", "business": "Business team",
          "steward": "Steward", "developer": "Developers and agent builders"}


def developer_page(api_url: str, health: dict, logo: bool) -> str:
    """Fill the approved developer page with this install's current public values."""
    current = settings()
    client = (current.CLIENT_NAME or "").strip() or "Client"
    portal = (current.PORTAL_URL or "").strip().rstrip("/")
    values = {
        "APP_NAME": context_app_name(), "CLIENT_NAME": client,
        "DISCOVERY_NAME": (current.PRODUCT_NAME or "").strip() or DEFAULT_PRODUCT_NAME,
        "VERSION": health["version"], "HEALTH": "Service healthy" if health["status"] == "ok" else "Service unavailable",
        "CLIENT_CONFIG": json.dumps({"mcpServers": {"context": {"type": "http", "url": api_url.rstrip("/") + "/mcp",
                          "headers": {"Authorization": "Bearer <access_token>"}}}}, indent=2),
        "API_URL": api_url.rstrip("/"), "PORTAL_URL": portal + "/home",
        "CONTEXT_DOCS_URL": portal + "/api/context/onboarding-guide", "TOKEN_LIFETIME": f"{SignedBearer.SECONDS // 60} minutes",
    }
    values["CLIENT_LOGO"] = ('<img src="/brand/logo" alt="' + escape(client, quote=True)
                             + '" style="max-width:112px;max-height:32px;object-fit:contain">') if logo else escape(client)
    values["BUNDLED_FONTS"] = re.search(r'<style id="bundled-fonts">.*?</style>',
                                        (ROOT / "ui" / "fonts.html").read_text(), re.S)[0]
    page = (ROOT / "ui" / "developers.html").read_text()
    if not portal:
        page = re.sub(r'<a href="\[(?:PORTAL_URL|CONTEXT_DOCS_URL)\]"[^>]*>.*?</a>', "", page)
    return re.sub(r"\[([A-Z_]+)\]", lambda match: values[match[1]] if match[1] in {"CLIENT_LOGO", "BUNDLED_FONTS"}
                  else escape(values[match[1]], quote=True), page)


def onboarding_guide(who=None, people=None, roles=None, role=None, *, client: str = "", api_url: str = "") -> str:
    """Render only guides authorised by the live identity reader; HTML has no MCP capability."""
    allowed = []
    if who and people is not None and who in people.people():
        rights = permissions(people.role_of_person(who, roles or {}))
        if rights["draft"] or rights["run"] or rights["improve"]:
            allowed.append("knowledge-engineer")
        if rights["accept"]:
            allowed.append("business")
        if rights["certify"]:
            allowed.append("steward")
        allowed.append("developer")
    if role and role not in allowed:
        raise PermissionError("This guide is not assigned to your roles.")
    if not who:
        body = render_markdown((ROOT / "ui" / "onboarding.md").read_text())
    else:
        selected = role or next(iter(allowed), None)
        body = '<h1>Docs</h1><nav aria-label="Your role guides">' + " ".join(
            f'<a href="/onboarding-guide?role={key}"' + (' aria-current="page"' if key == selected else '')
            + f'>{GUIDES[key]}</a>' for key in allowed) + '</nav>'
        body += (render_markdown((ROOT / "ui" / "guides" / f"{selected}.md").read_text().replace("{{client}}", client)) if selected
                 else '<p>No role guide is assigned. Ask your access admin.</p>')
        if selected == "developer":
            api = api_url.rstrip("/")
            mcp = api + "/mcp"
            config = {"mcpServers": {"context": {"type": "http", "url": mcp,
                       "headers": {"Authorization": "Bearer <access_token>"}}}}
            body += ('<section aria-label="Connection addresses"><h2>This system</h2>'
                     '<p>API: <code id="connection-api">' + escape(api) + '</code></p>'
                     '<p>MCP: <code id="connection-mcp">' + escape(mcp) + '</code></p>'
                     '<p><a href="/docs" target="_top">Swagger API reference</a> · <a href="/redoc" target="_top">ReDoc API reference</a></p>'
                     '<pre id="connection-config">' + escape(json.dumps(config, indent=2)) + '</pre></section>')
    home = (settings().PORTAL_URL or "").rstrip("/") + "/context"
    home_link = '<a href="' + escape(home, quote=True) + '" target="_top">Context / Sign in</a>'
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>""" + escape(product_name()) + """ — Docs</title><style>
body{margin:auto;padding:24px;overflow-wrap:anywhere;font:16px/1.6 system-ui;color:#15181d;background:#fff}p{max-width:80ch}nav{display:flex;gap:16px;flex-wrap:wrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f1;padding:14px}code{font-size:14px}.command{position:relative}.command button{float:right}h2{margin-top:32px}a:focus-visible{outline:3px solid #235b69;outline-offset:3px}table{display:block;overflow-x:auto}nav a{padding:8px 0}a{color:#235b69}table{border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ddd}
</style></head><body>""" + home_link + ' <a href="' + escape((settings().PORTAL_URL or "").rstrip("/") + "/knowledge/docs/start-here", quote=True) + '" target="_top">Knowledge docs</a>' + """<main>""" + body + """</main><script>
document.addEventListener('click', async event => {
 const button = event.target.closest('button[data-copy]');
 if (!button) return;
 try { await navigator.clipboard.writeText(button.nextElementSibling.textContent); button.textContent='Copied'; }
 catch { button.textContent='Select and copy'; }
});
setInterval(async () => {
 try {
  const response = await fetch(location.href, {cache:'no-store'});
  if (response.status === 403) { location.replace('/onboarding-guide'); return; }
  if (!response.ok) return;
  const fresh = new DOMParser().parseFromString(await response.text(), 'text/html').querySelector('main');
  const current = document.querySelector('main');
  if (fresh && fresh.innerHTML !== current.innerHTML) current.replaceWith(fresh);
 } catch {}
}, 20000);
</script></body></html>"""


def presets(model: bool, ag, default_arm: str, sides: dict) -> dict:
    """The arms, as run configs, and the values each stage may take, with the endpoints each preset runs on as it is; and
    the reference agent's two arms, and what runs them."""
    return {"default": default_arm, "model_configured": model, "stages": {s: list(v) for s, v in STAGES.items()},
            "presets": {name: {"arm": name, **cfg.stages(), "label": cfg.label, "paths": [p for p in PATHS if not cfg.problems(p, model)]}
                        for name, cfg in PRESETS.items()},
            "agent_arms": {name: {"arm": name, "side": sides[name], **ag.config(name).stages(), "label": ag.config(name).label,
                                  "path": "agent", "mode": ag.mode} for name in AGENT_ARMS}}


def summary(profile: Profile) -> dict:
    """The estate's summary, and the pipeline's stats and the eval where the run state holds them."""
    out = {"estate": json.loads((profile.estate() / "summary.json").read_text())}
    for name in ("pipeline_stats", "eval"):
        p = profile.run_dir() / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text())
    return out


def served_profile(profile: Profile, e: Engine) -> dict:
    """Whom the process serves: the profile and its client, the pack's title, the units in the pack's order, the artifact
    whose graph it serves, and the landed release that graph was loaded from."""
    release = e.g.release()
    return {"profile": profile.id, "client": profile.client, "title": e.TITLE, "units": e.UNIT_ORDER, "artifact": e.profile.model_hash(),
            "release": {"id": release["id"], "as_of": release["as_of"]} if release else None}


def versions(profile: Profile) -> dict:
    """The released versions of the graph in order (kge/graph/history.py)."""
    from kge.graph.history import list_versions
    from kge.adapters import released
    post = released.post_evaluation(profile.serve()) if profile.model_hash() else None
    return {**list_versions(profile), "eval_post": {k: v for k, v in post.items() if k != "records"} if post else None}


def check_sql(e: Engine, query: str, who: str) -> dict:
    """The SQL gate alone, as who: parse, one statement, read-only, the allowlist of each warehouse who may read,
    restricted columns never, then the warehouse's plan. The query is never run. A table named <unit>.<table> is read
    in that unit's warehouse when who may read it and it holds the table; otherwise it is not available, as an unknown
    table is. The query is read in the dialect the warehouses run (WarehouseSet.sql_dialect), as a model writes it."""
    from sqlglot import exp, parse
    from sqlglot.errors import SqlglotError

    dialect = e.w.sql_dialect
    try:
        statements = [s for s in parse(query, read=dialect) if s is not None]
    except SqlglotError as exc:
        return {"ok": False, "reasons": [unparsed(exc)], "tables": [], "columns": []}
    if not statements:
        return {"ok": False, "reasons": ["no query given"], "tables": [], "columns": []}
    ctes = {cte.alias_or_name for statement in statements for cte in statement.find_all(exp.CTE)}
    tables = sorted({table.name for statement in statements for table in statement.find_all(exp.Table)
                     if table.name and table.name not in ctes})
    columns = sorted({column.sql(dialect) for statement in statements for column in statement.find_all(exp.Column)})
    readable = e.g.readable_units(who)
    allowlists = {unit: schema_allowlist(e.g, unit, e.RESTRICTED_COLUMNS) for unit in e.UNIT_ORDER}
    named = qualified(statements)
    if named:
        hidden = [f"not available: {unit}.{table} is in no warehouse {who} may read" for unit, table in sorted(named)
                  if unit not in readable or unit not in allowlists or table not in allowlists[unit].tables]
        if hidden:
            return {"ok": False, "reasons": hidden, "tables": tables, "columns": columns}
        if len({unit for unit, _ in named}) > 1:
            return {"ok": False, "reasons": [ONE_WAREHOUSE], "tables": tables, "columns": columns}
        query = ";\n".join(unqualified(statement).sql(dialect) for statement in statements)
    pinned = {unit for unit, _ in named}
    units = [unit for unit in e.UNIT_ORDER if not pinned or unit in pinned]
    candidates = [(unit, allowlists[unit]) for unit in units if unit in readable and set(tables) <= allowlists[unit].tables]
    if not candidates:
        return {"ok": False, "reasons": unreadable_tables(tables, allowlists, readable, who), "tables": tables, "columns": columns}
    results = [e.w.gate(unit, query, allow, dialect=dialect) for unit, allow in candidates]
    passed = any(result["passed"] for result in results)
    reasons = [] if passed else sorted({plain(reason) for result in results for reason in result["problems"]})
    return {"ok": passed, "reasons": reasons, "tables": tables, "columns": columns}


ONE_WAREHOUSE = "the query's tables are not in one warehouse this principal may read: a query reads one warehouse"


def qualified(statements) -> set[tuple[str, str]]:
    """The (unit, table) pairs a query names as <unit>.<table>, in a table's name or a column's (<unit>.<table>.<column>).
    A name with a catalogue as well is left to the gate, which refuses it."""
    from sqlglot import exp

    named = set()
    for statement in statements:
        named |= {(t.db, t.name) for t in statement.find_all(exp.Table) if t.db and not t.catalog}
        named |= {(c.db, c.table) for c in statement.find_all(exp.Column) if c.db and not c.catalog}
    return named


def unqualified(statement):
    """The statement with each <unit>. qualifier taken off, as the unit's own warehouse names its tables."""
    from sqlglot import exp

    statement = statement.copy()
    for node in [*statement.find_all(exp.Table), *statement.find_all(exp.Column)]:
        if node.args.get("db") is not None and not node.args.get("catalog"):
            node.set("db", None)
    return statement


UNRESOLVED = re.compile(r"does not resolve against the \w+ warehouse: (.*)")
UNRESOLVED_COLUMN = re.compile(r"Column '([^']+)' could not be resolved(?: for table: '([^']+)')?\. Line: (\d+), Col: (\d+)")
UNKNOWN_COLUMN = re.compile(r"Unknown column: (\S+)")


def plain(reason: str) -> str:
    """A gate's reason as the SQL check says it: a name the warehouse's schema does not resolve is an unknown column, with
    where it is when the resolver says, and any other resolver message is one plain sentence (the resolver's own text is
    never shown). The schema is the warehouse's the check ran on, so a column only a warehouse the principal may not read
    has is as unknown as one no warehouse has."""
    found = UNRESOLVED.fullmatch(reason)
    if not found:
        return reason
    column = UNRESOLVED_COLUMN.fullmatch(found.group(1))
    if column:
        name, table, line, col = column.groups()
        return f"unknown column {table + '.' if table else ''}{name} (line {line}, column {col})"
    unknown = UNKNOWN_COLUMN.fullmatch(found.group(1))
    if unknown:
        return f"unknown column {unknown.group(1)}"
    return "does not resolve: a name in the query is not a table or column the warehouse has"


def unparsed(exc) -> str:
    """Why a query does not parse, as one plain sentence, with where it stops when the parser says (sqlglot's own message,
    its token text and its context, is never shown)."""
    found = next(iter(getattr(exc, "errors", None) or []), None)
    if found and found.get("line") and found.get("col"):
        return f"does not parse: this is not a query the SQL check can read (line {found['line']}, column {found['col']})"
    return "does not parse: this is not a query the SQL check can read"


def unreadable_tables(tables: list[str], allowlists: dict, readable, who: str) -> list[str]:
    """Why no warehouse this principal may read holds every table the query names. A table in no warehouse of the estate
    and one only in warehouses the principal may not read get the same answer, not available, so the check never says
    which tables exist that the principal cannot read (as a term's map does not). Tables in readable warehouses that are
    not one warehouse cannot be read together, since a query reads one warehouse."""
    holders = {t: [unit for unit, allow in allowlists.items() if t in allow.tables] for t in tables}
    hidden = [f"not available: {t} is in no warehouse {who} may read" for t in tables
              if not any(unit in readable for unit in holders[t])]
    return hidden or [ONE_WAREHOUSE]
