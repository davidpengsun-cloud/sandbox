# Cerebro: the graph and its agent, as an app in the portal

| Document control | Value |
|---|---|
| Status | Draft for the owner's approval. Six of the questions this design raised were decided by the owner on 2026-11-15 (§3.2); the points §4 to §7 left open are decided by the engineering manager and marked "Decided (EM)"; one is proposed for the owner to confirm or change, separation of duties (§3.3); the app's default name is `Cerebro` (owner, 2026-11-18, §9.2) |
| Deliverable | The Cerebro app built in the portal and the Context app's cleanup, in one pull request into `develop`, with this document as that pull request's design section. The reference agent's move onto MCP that requirement 1 carries (§4.2) moved to A7 (#497). The build order is §11 |
| Requirement | Issue #466 (A3), as pivoted by the owner on 2026-11-15. It replaces A3's earlier scope in [answer-modes.md](answer-modes.md), §9.3 and §9.4, and carries that scope's approved requirements forward |
| Basis | Code on `develop` at `103de05`. A2 (#465) is still open and changes `portal/ui/src/pages/Context/*`; the owner decided the draft may start before it merges (§3.2), so the Context citations in §1 are re-checked once A2 lands |
| Related | [answer-modes.md](answer-modes.md) (#462): the outcomes, `confirm_first` and feedback the agent uses (A1, #464), and the MCP front door (§9); [graph-model-spec.md](graph-model-spec.md): the graph the app reads |
| Scope | Two things: the new Cerebro app, and the Context app's cleanup, in which review moves to Cerebro's Curate and Context keeps building, checks and release. Within them: the agent in the portal's server; the graph read; where proposals come from; the app's screens; the role matrix as KGE API checks; the move of review out of the Context app; the app's settings; keeping client material out of the repository; the build order inside the issue's one pull request |
| Out of scope | Changes to the owner's prototype, which is kept outside the repository; lineage and catalogue connectors beyond what the portal already syncs; any change to Discover or Certify; any change to Context's building, checks and release beyond the matrix's role checks (§8.4) |

Cerebro is the company's graph and its agent as one product, on the context layer: a new app in the portal's Knowledge home beside Certify, Context and Discover. Everyone who may use it can ask the agent and read the part of the graph they may read; stewards also curate the graph and review candidates there, which they did in the Context app until this pull request removed its review (§9.1). The product name is a setting, `Cerebro` by default (owner, 2026-11-18). This document calls the product Cerebro throughout.

The design also covers the Context app's cleanup. Review moves out of Context to Cerebro's Curate. Context keeps building, checks and release. Curate covers every review action, so Context's review is removed (§9.1, done). Discover is unchanged.

All paths below are relative to `demos/kge/` unless they start with `docs/`. File and line citations describe `develop` at `103de05`. Every threshold, count and time in this document is a planning estimate until measured.

## 1. Today

### 1.1 What the portal shows of the graph

| What | Where | What it does |
|---|---|---|
| The Context app's tabs | `portal/ui/src/pages/Context/contextState.js:4-9` | Ask, Docs, Review, Onboard and Quality, each shown by the person's KGE processes. There is no tab for the whole graph |
| Graph, inside Behind the answer | `portal/ui/src/pages/Context/BehindAnswer.js:541`, `:319` | One of five tabs (sources, map, plan, graph, trace) under one answer. `GraphPath` draws that answer's path only, from its `graph_path` (`kge/services/ask/models.py:184`) |
| Its layout | `BehindAnswer.js:101` | `layoutGraph` places the path's nodes in fixed columns (words, vocabulary, meaning, rules, tables); it does not use xyflow |
| xyflow in the portal | `portal/ui/src/components/DataLineage.js:4`; `portal/ui/package.json:19` | Discover's lineage view draws with `@xyflow/react` 12 |

No app in the portal shows the whole graph: communities, repositories, documents, tables, columns and terms together, with proposed and inferred links drawn apart from certified ones.

### 1.2 Review in the Context app

| What | Where | What it does |
|---|---|---|
| Who sees Review | `contextState.js:6`, `:10` | The Review tab appears for a person with the `certification` process, and is their home tab |
| The queue | `portal/ui/src/pages/Context/ContextScreens.js:33-34`, `:63` | Reads `/steward/queue` (pending and decided) and renders `Review` |
| The families | `portal/ui/src/pages/Context/Review.js:5` | Four: suggested passage-to-term links, candidate terms, personal data in documents, client cross-reference matches |
| A decision | `Review.js:23` | Posts `/steward/decide` with the tag, the decision and, for an accepted candidate term, its definition |
| The queue's role check | `kge/services/steward/routes.py:23-24`, `:36-37` | Both routes need `certification` |
| Acceptance and certification | `kge/services/onboard/routes.py:319-328`, with the role helpers at `:188-193` | `/accept` needs the business role, `/certify` the steward role |

The Context app is the knowledge engineers' tool: onboarding, checks and release. Stewards review there today because it is where the queue is.

### 1.3 The catalogue agent

| What | Where | What it does |
|---|---|---|
| The agent | `portal/server/src/catalogue-agent.js:71` | `catalogueAgent` answers a catalogue question from the readable assets, within a budget of 8 assets and 60,000 characters (`:13`) |
| Its call | `portal/server/src/knowledge.js:207-226` | The portal's own catalogue Ask, used when KGE is absent or unreachable; the rules answer is its fallback |
| The Ask chain with KGE | `knowledge.js:235-246` | Discover's search and Ask call KGE's `/search` and `/assist` as the signed-in person; this is the call [answer-modes.md, §9.4](answer-modes.md#94-the-portals-catalogue-agent-onto-mcp) moves onto the `assist` MCP tool |

It is a single model call over the catalogue, not an agent loop, and it does not call KGE's MCP tools today. [answer-modes.md, §9.4](answer-modes.md#94-the-portals-catalogue-agent-onto-mcp) moved its call into KGE onto MCP; that requirement is carried into §4.

### 1.4 The app registries and their settings

| What | Where | What it does |
|---|---|---|
| Server flags and names | `portal/server/src/apps.js:9-10` | `PORTAL_APP_CERTIFY`, `PORTAL_APP_CONTEXT`, `PORTAL_APP_DISCOVER`, each on by default (`:14-18`, `:37`) |
| The single-app rule | `apps.js:39-47` | With the Knowledge home off, exactly one app must be on, or the server does not start |
| Product name | `apps.js:6-7`, `:48-49` | `PORTAL_CLIENT_NAME` with a neutral default, `Client` |
| The shell's registry | `portal/ui/src/components/shell/apps.js:13-17` | Certify, Context and Discover, in the doors' order |
| Path to app | `shell/apps.js:28-34` | `appForPath` maps a path to its app; anything unknown is Discover |
| Who may use an app | `shell/apps.js:58-61` | Context needs a KGE role; Discover and Certify are open to anyone signed in |

### 1.5 The owner's prototype

The owner's working prototype is kept outside the repository because it carries client data and branding. Only its structure is recorded here, as Cerebro builds it (#539): no data, names or figures. Its answers are synchronous mocks, so it shows no streaming.

| Mode | Who | Layout |
|---|---|---|
| Graph (the default) | Everyone | The graph fills the free width. One filter row runs over its top: the community, All / Structured / Unstructured with their counts, the term-links switch and a name search. Fit, Center, the zoom with its percentage and a legend that folds away sit over the canvas's left edge. The legend names the node and link kinds and turns each on and off; proposed and inferred links are drawn apart from certified ones. An overview panel on the right shows the graph's counts and its documents list; a selected node's or link's details take its place. The assistant floats over the foot of the graph and hides to a button. The view fits to what is shown after each filter change, and when the assistant is shown or hidden |
| Chat | Everyone | A foldable panel on the left with the sessions and, under them, a small graph of the last answer's path with "Open graph"; the conversation in a centred column |
| Curate | Stewards only | A heading with the steward's note; four stat tiles. On the left, the proposals filtered by kind (term tags, new words, filter values, documents, business terms), each kind with its count, each proposal with its source, View in graph, Accept and Reject, and the decisions already made. On the right, the candidate to review with its checks, business acceptance, Certify and sign-off; under it, the gaps people hit |

The header is the portal's own, as in the other apps (owner, 2026-11-18): the global bar with the logo, the app's name, the app switcher, the theme button and the avatar; under it, the mode tabs and the hint for the key that switches mode. The prototype's "View as" and its own mode switch are not built. The signed-in person's role decides the tabs: a business user sees Chat and Graph, and a steward also sees Curate.

The modes share one state: the selection, the highlighted path, the role and the mode. A key cycles the modes, and an address that names a node and a question reopens the graph on that node. The graph draws node kinds community, domain, repository, document, asset, column and term. Each link carries the reason it exists, and the prototype draws proposed and inferred links apart from verified ones. Name-match links are flagged as not a verified join. An answer shows its status (certified, proposed, model-generated, not sure), the release and the responsible steward, and what it rests on. It shows a figure only after the person confirms. Its path is highlighted in the graph. Each answer offers Confirm, Rephrase, feedback, Ask about this, Suggest a change and View in graph.

There is no separate Overview mode. Its content sits in three places: the overview panel beside the graph, the Curate stat tiles, and the details of a selected node. The prototype is dark only and hard-codes about 34 colours beside its tokens, so moving it onto the portal's shared design tokens (§3.2) means redefining its colour roles for the portal's light and dark themes.

## 2. Who meets it

### 2.1 The role matrix

Copied from issue #466 as written.

| Action | Business | Steward | Engineer | Admin |
|---|---|---|---|---|
| Ask the agent | ✓ | ✓ | ✓ |  |
| Read the graph, filtered to what each person may read | ✓ | ✓ | ✓ |  |
| Suggest a change; ✓/✗ feedback on answers | ✓ | ✓ | ✓ |  |
| Curate: accept or reject proposals (terms, tags, domains, document tiers) |  | ✓ |  |  |
| Review candidates: business acceptance, steward certification |  | ✓ |  |  |
| Build and onboard candidates: pack edits, mappings, filter values, joins |  |  | ✓ |  |
| Read the retrieval-side APIs; Ask with plan, SQL and trace |  |  | ✓ |  |
| Release / roll back a certified candidate that passed the checks |  |  | ✓ |  |
| Access, identities, settings |  |  |  | ✓ |

Separation of duties: engineers build and release but do not certify; stewards certify but do not release.

### 2.2 Today's roles

Today's deployment roles map onto these four as the issue states. The Meridian pack defines them at `clients/meridian/pack.yaml:1633-1649`:

| Today's role | Processes today | Cerebro role |
|---|---|---|
| `analyst_retail`, `analyst_enterprise` | querying | Business |
| `advisor_wealth` | querying | Business |
| `finance_reporting` | querying | Business |
| `steward`, `steward_glossary` | certification, querying; writes mappings and terms | Steward |
| `business_team` | querying; writes mappings and terms | Steward |
| `knowledge_engineer` | drafting, building, improving, querying | Engineer |
| `admin` | tagging, certification, querying; writes mappings and terms | Admin |
| `pipeline_tagger` | tagging; reads nothing | Not mapped: the pipeline's own service role, not a person |

The Cerebro role comes from the processes each pack declares for a role, never from the role's name, so every client's pack maps the same way (`cerebro_role_of`, `kge/pipeline/access.py`). The access administrator (the reserved `access_admin`) and the pack's content `admin` role are Admin alone. Any other role holding `certification`, or business acceptance (`accepting`, or writes to terms and mappings without certification, the existing `accepts_drafts` rule), is Steward; one holding `drafting`, `building` or `improving` is Engineer; one holding `querying` and none of those is Business. `tagging` alone, or a role the release does not define, maps to none. A person holding several roles holds every Cerebro role they give. Meridian's roles map as the table above; Thornbury's `actuary` and Fellmoor's `network_analyst` are Business, their `claims_steward` and `fares_steward` Steward; a `kge client` pack's `analyst` is Business and its `business_team` and `steward` Steward.

Three facts bear on separation of duties and are recorded here, not changed: `business_team` maps to Steward but has no `certification` process today (`pack.yaml:1638`); `steward` and `steward_glossary` write mappings, which the matrix gives to engineers (`:1636`, `:1645`); and `admin` holds `certification` (`:1644`). §3.3 proposes how Cerebro treats them; the role matrix and its checks are settled in §8.

## 3. Decisions

### 3.1 Architecture decisions (owner, 2026-11-15)

| # | Decision | Not taken, and why |
|---|---|---|
| D1 | Full-stack JavaScript in the portal's server (BFF), and nothing else to run. The BFF runs the agent in process on LangGraph.js, with LangChain.js for the model (Gemini on Vertex AI) and for KGE's MCP tools, and streams it to the page from one endpoint of its own. No LangGraph server and no extra database: conversations stay in the page and are sent with each question, so the BFF stays stateless like KGE. KGE stays Python | A separate agent service; LangGraph's own server, whose self-hosting terms tie production use to a licence key; the Python agent as the business agent |
| D2 | The chat screen reuses LangChain's open-source Agent Chat UI components (MIT) inside the portal, on LangChain's React chat hook (`useStream`) and the BFF's endpoint. Its chat view, conversation list and agent inbox (the pause that waits for a person: the confirmation before the figure) are copied into the portal and restyled to its design tokens. The BFF either answers the few LangGraph API calls the hook makes (conversations, runs, `/info`) or uses the hook's custom-backend adapter; a half-day check settles which, and that the copied components run on it, before the chat is built. The conversation list shows the conversations the page holds. The hook's version is pinned, since its adapter interface is changing between releases | Agent Chat UI run as it is, a separate Next.js app on LangGraph's JavaScript server: a second app with its own look, conversations held in that server's memory, and a proxy that does not authenticate callers, which breaks the portal's sign-in and the role matrix. assistant-ui: ready components too, but no inbox and not built on the hook |
| D3 | The graph is drawn with xyflow (`@xyflow/react`, already used for Discover's lineage, §1.1), with a budget of about 1,000 visible nodes: columns collapsed into their table by default, only on-screen elements drawn, less detail when zoomed out, memoised nodes, and the layout computed once off the main thread (d3-force or ELK). A benchmark of 1,000 nodes and 2,500 links on a mid-range laptop, panning and zooming, turns the budget into a measured figure; until then both are planning estimates | A canvas or WebGL renderer now. Kept as the fallback: the renderer sits behind the graph data, so a WebGL library (for example Sigma.js) can replace it if the visible graph grows past a few thousand nodes |
| D4 | New KGE endpoints: the graph read (an API route and an MCP tool, filtered per person, paged, each link marked certified, proposed or inferred) and `explore` (a short summary and a deep link). Suggestions use a role-checked endpoint into the steward queue. Curation and review reuse `/steward/queue`, `/steward/decide`, `/accept` and `/certify`, with the role matrix enforced | The portal reading KGE's graph store directly |
| D5 | The Python LangGraph reference agent stays, for evaluation (the A/B and the margin) and as the test of the MCP contract | Retiring it |

### 3.2 Decided by the owner (2026-11-15)

| Question | Decision |
|---|---|
| The name and the file | This document calls the product "Cerebro" and is `docs/use-cases/kge/platform/cerebro.md`. The product name is a setting, `PORTAL_CEREBRO_NAME`, whose default is `Cerebro` (owner, 2026-11-18; §9) |
| The business agent | The portal's own LangGraph.js agent (D1), not a CX Agent Studio agent embedded in the portal |
| Where the graph read lives | A new `kge/services/graph/` service, not routes under `kge/services/ask/` |
| Overview mode | As the issue says: the owner's prototype (§1.5), refined onto the portal's shared design tokens |
| The logo | Cerebro uses the portal's own logo. The product name is a title set by a setting, not a brand element |
| The `useStream` check (D2) | The first job of this pull request (B1, §11), not a separate trial |
| When the draft starts | Now, before A2 (#465) merges. The build still follows A2 |

### 3.3 Proposed by the engineering manager, for the owner to confirm or change

| Question | Proposal |
|---|---|
| Separation of duties | Proposed (EM): enforced by the matrix's checks only (§8). Today's pack roles (§2) are unchanged; the checks refuse a release to a steward and a certification to an engineer, whatever the pack grants |

## 4. The agent in the portal's server (requirement 1)

This section settles the BFF endpoint, the MCP tools the agent uses and with whose bearer, and the reference agent's carry-over from [answer-modes.md, §9.3](answer-modes.md#93-the-reference-agent-onto-mcp).

### 4.1 Two agents, one contract

| Agent | Where it runs | What it is for | As whom |
|---|---|---|---|
| Cerebro's agent (the business agent) | In process in the portal's server, on LangGraph.js, with LangChain.js for the model (Gemini on Vertex AI) and KGE's MCP tools (D1) | The agent people meet: in its own chat, floating over the graph, or as the business agent. The owner decided it is the portal's own agent, not a CX Agent Studio agent embedded in the portal (§3.2) | The signed-in person |
| The reference agent | KGE, in Python, on LangGraph (`kge/services/ask/engine/agent.py:22-23`) | Evaluation (the A/B and the margin) and the test of the MCP contract (D5) | The principal the evaluation runs as |

Both reach KGE through the same MCP tools, so a change to a tool's contract is caught by the reference agent's tests before Cerebro's agent meets it.

### 4.2 Requirements carried from A3

Requirement 1 carries A3's approved requirements from [answer-modes.md, §9.3](answer-modes.md#93-the-reference-agent-onto-mcp) and [§9.4](answer-modes.md#94-the-portals-catalogue-agent-onto-mcp). They are listed as the issue lists them; their detail and which agent each binds stay as those sections state them.

| # | Requirement | Where it is settled |
|---:|---|---|
| 1 | The context step on `get_context` | answer-modes.md §9.3–9.4; the tool exists at `kge/services/ask/mcp.py:28` |
| 2 | The treatment arms on `ask` | answer-modes.md §9.3–9.4; the tool exists at `ask/mcp.py:59` (mounted at `/mcp/answer`, `kge/app.py:428`) |
| 3 | The control arm in process | answer-modes.md §9.3–9.4 |
| 4 | The layer exception removed | answer-modes.md §9.3–9.4 |
| 5 | The agent seal manifest | answer-modes.md §9.3–9.4 |
| 6 | The person's bearer | answer-modes.md §9.3–9.4, and §4.4 below |
| 7 | `confirm_first` followed | answer-modes.md §9.3–9.4 and A1 (#464); in Cerebro, the Confirm pause in §4.3 |

### 4.3 The BFF endpoint

One endpoint of the portal's server streams the agent to the page (D1). What D1 and D2 settle:

| Property | Settled as |
|---|---|
| State | None in the portal's server. The page holds the conversations and sends the conversation with each question; Ask stays stateless |
| Request | The question, the conversation so far, and the node the question is about when it comes from "Ask about this" (§7) |
| Response | A stream: the answer's parts as they arrive, the tool calls, and a pause that waits for the person (the agent inbox, D2) where `confirm_first` asks to confirm before a figure |
| Resume | After Confirm, the page sends the conversation with the person's confirmation, and the portal's server runs the turn from that alone, holding nothing between requests. How the hook carries it is settled by the `useStream` check below |
| Sign-in | The portal's own sign-in. The endpoint refuses a caller who is not signed in |
| Version | The `useStream` hook's version is pinned (D2) |

The wire format depends on the half-day `useStream` check (D2), which the owner placed as the first job of this pull request (§3.2, §11). Its two options, as D2 states them:

| Option | What the portal's server does | Pass condition of the check |
|---|---|---|
| A | Answers the few LangGraph API calls the hook makes: conversations, runs, `/info` | The copied chat view, conversation list and agent inbox stream an answer, show a tool call, and pause and resume on Confirm, against the portal's server, with no LangGraph server running |
| B | Serves the hook's custom-backend adapter | The same, through the adapter |

### 4.4 The MCP tools, and with whose bearer

Every MCP tool call runs as the principal whose bearer the request carries; a browser cookie is never taken (`kge/services/http.py:303-310`). KGE reads the bearer from `x-kge-authorization`, else `authorization` (`http.py:306`). Today the portal's server forwards the person's cookie and `authorization` header to KGE (`portal/server/src/kge.js:9-15`), and authenticates itself to the platform with `x-serverless-authorization` (`portal/server/src/kgeauth.js:2`, `:43`).

| Tool | Today | Matrix row (§2.1) | Who may call it through Cerebro's agent |
|---|---|---|---|
| `get_context` | `ask/mcp.py:28`, mounted at `/mcp` (`app.py:426`); needs `querying` (`ask/mcp.py:79-80`) | Ask the agent | Business, steward, engineer |
| `ask` | `ask/mcp.py:59`, at `/mcp/answer`; needs `querying` | Ask the agent | Business, steward, engineer |
| The graph read | New (§5) | Read the graph, filtered to what each person may read | Business, steward, engineer |
| `explore`, `explore_question` | New (§5.3) | Read the graph, filtered to what each person may read | Business, steward, engineer |
| Suggest a change | A new role-checked endpoint into the steward queue (D4) | Suggest a change | Business, steward, engineer |
| ✓/✗ feedback on an answer | A1 (#464) | ✓/✗ feedback on answers | Business, steward, engineer |

No other row of the matrix is reached through the agent. Admin has no ✓ in the Ask row; today's `admin` role holds `querying` (§2.2), so Cerebro's checks refuse an admin the Ask row (§8). The plan, the SQL and the trace of an answer are the engineer's row ("Ask with plan, SQL and trace"); the agent shows them to an engineer only.

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| How the portal's server obtains a bearer for the signed-in person | Decided (EM): the portal's server sends the signed-in person's own KGE token in `x-kge-authorization`, as [answer-modes.md, §9.4](answer-modes.md#94-the-portals-catalogue-agent-onto-mcp) and its decision D4 state. There is no shared service identity: every tool call runs as the person |
| Whether the agent also uses `retrieve`, `map_term` (`ask/mcp.py:39`, `:46`), `search` (`kge/services/search/mcp.py:13`) or `assist` (`kge/services/assist/mcp.py:16`) | Decided (EM): the business agent gets only what the matrix grants every persona, the tools in the table above. `retrieve`, `map_term`, `search` and `assist` are retrieval-side, so the agent offers them to engineers only. KGE tightens `retrieve` and `map_term` to engineers (§8.4); `search` and `assist` keep their `querying` check, because Discover's search and Ask call them for everyone (`portal/server/src/knowledge.js:235-246`) and the issue keeps Discover as it is |
| Whether Cerebro's agent replaces the catalogue agent (`portal/server/src/catalogue-agent.js:71`, called at `portal/server/src/knowledge.js:220`) | Decided (EM): Cerebro's agent replaces it as the portal's agent on MCP, as requirement 1 moves "the portal's catalogue agent" onto MCP; the old one stays until the Cerebro app is on. `catalogue-agent.js` is the portal's own Ask when KGE is absent or unreachable (§1.3); with KGE, Discover's Ask calls `/assist` (`knowledge.js:235-246`), which answer-modes §9.4 moves onto the `assist` MCP tool. The issue keeps Discover as it is, so what Discover's Ask shows does not change; B5's gate runs Discover's existing tests unchanged (§11) |

## 5. The graph read (requirement 2)

This section settles the route and MCP tool contract, the `explore` tool, and the new service's place in the demo's layout.

### 5.1 The contract

One read, served twice: an API route and an MCP tool, the same contract, the same check (D4). It returns the graph's nodes and links, filtered to what the person may read, paged, each link marked certified, proposed or inferred. It returns no figures. The portal never reads KGE's graph store directly (D4).

| Field | On | Values |
|---|---|---|
| `kind` | Node | One of the issue's kinds: term, table, column, document, repository, community, person; and domain (decided in §5.5) |
| `id`, `name` | Node | The node's identifier and readable name |
| `source`, `target` | Link | Node identifiers |
| `kind` | Link | What the link joins, for example a column to its table, or a document to a term |
| `status` | Link | Exactly one of `certified`, `proposed`, `inferred` |
| `why` | Link | The reason the link exists (decided in §5.5) |
| Paging | Request and response | A page of nodes and links, and a cursor for the next (decided in §5.5) |

No figures: no record counts, sizes, scores or values from the data, on any node or link.

A worked example on Meridian sample data: a business user who may read the retail unit asks for the neighbourhood of a term. The page holds the term, the tables and columns it is mapped to in the retail unit, and the documents that mention it, with the term's mappings marked `certified` and a passage-to-term suggestion still in the queue marked `proposed`. A table in a unit the person may not read is absent, and so are its links; nothing in the page says it exists.

### 5.2 The per-person filter

The read is filtered by the person's readable units, as the steward queue is today (`kge/services/ask/engine/graphview.py:849`, used at `:1046`). KGE's catalogue carries a community per asset (`kge/services/catalogue/__init__.py:78`, `:156`). Repositories and communities come from the portal catalogue's sync (decided below).

### 5.3 `explore`

A second MCP tool and route (D4) for the agent: a short summary of a node or a question's neighbourhood, and a deep link that opens the graph on that node in the address form §7.2 states. It is filtered as the graph read is. A node's goes by `GET /graph/explore?node=` and the `explore` tool; a question's by `POST /graph/explore`, the question in the body, and the `explore_question` tool, so no URL or access log carries question text (EM, 2026-11-15).

### 5.4 Where the service lives

The owner decided a new `kge/services/graph/` service (§3.2), not routes under `kge/services/ask/`. It follows the existing services' pattern: its routes, its logic, its models, and its own `mcp.py`, mounted beside the others (`kge/app.py:426-434`).

### 5.5 Roles

| Action | Matrix row (§2.1) | Business | Steward | Engineer | Admin |
|---|---|---|---|---|---|
| Graph read, route and MCP tool | Read the graph, filtered to what each person may read | ✓ | ✓ | ✓ |  |
| `explore` | The same row | ✓ | ✓ | ✓ |  |

The KGE check that refuses the blank cell, and its audit entry, are settled in §8.

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| Domain nodes | Decided (EM): yes. `domain` is a node kind, as the owner's prototype draws it (§1.5) and the matrix names domains as a curation kind |
| Paging | Decided (EM): a cursor. A page returns its nodes and links and a cursor for the next page, absent on the last |
| The reason for a link | Decided (EM): each link carries `why`, the reason it exists, beside its `status`, as the prototype shows (§1.5). A name-match link's `why` says it is not a verified join |
| Where repositories come from | Decided (EM): repositories and communities come from the portal catalogue's sync, as requirement 3 names it |

With these, §5.1's node kinds are term, table, column, document, repository, community, person and domain, and each link carries `source`, `target`, `kind`, `status` and `why`.

## 6. Where proposals come from (requirement 3)

This section maps each source of proposals to its queue family and its producer.

### 6.1 Sources and families

The queue today has four families (`portal/ui/src/pages/Context/Review.js:5`). The issue names five sources of proposals.

| Source (requirement 3) | Family today | Producer today |
|---|---|---|
| Onboarding drafts | — | `/draft` (`kge/services/onboard/routes.py:307-308`) produces a candidate, which goes to `/accept` and `/certify` (§1.2) |
| Term tagging | `candidate_term`, `privacy` | The tagger (`kge/pipeline/tagger.py:36`, `:81`), run in the pipeline (`kge/pipeline/run.py:232`) |
| Similar-name inference | — | No producer today. The nearest is `suggested_mention`, which links a passage to a term by embedding similarity (`kge/pipeline/suggestions.py:19`), not a column to a term by its name |
| The agent's feedback (A1) | — | No producer today; A1 (#464) adds feedback |
| The portal catalogue's communities and repositories | — | No producer today. The catalogue carries a community per asset (`catalogue/__init__.py:156`) |

One family has no source in requirement 3: `xref_match`, the client cross-reference matches, loaded with the graph (`kge/graph/load.py:452`) and queued for a steward (`graphview.py:1019`, `:1045-1051`). It stays in the queue as it is today.

### 6.2 Proposals and Curate's kinds

Curate lists proposals by kind: term tags, new words, filter values, documents, business terms (§7).

Decided (EM): each Curate kind maps to one queue family. Three of the families are new; the build job that adds their producers (§11) adds them to the queue and to `/steward/decide`.

| Curate kind | Queue family | Today | Its sources (§6.1) |
|---|---|---|---|
| Term tags | `suggested_mention` | Exists | Term tagging; similar-name inference, whose items link a column to a term and carry the status `inferred` (§5.1) |
| New words | `candidate_term` | Exists | Term tagging; Suggest a change and the agent's feedback when they name a new word |
| Filter values | `filter_value` | New | Onboarding drafts |
| Documents | `document_tier` | New: document tiers, the curation kind the matrix names (row 4) | Term tagging of documents; Suggest a change on a document |
| Business terms | `business_term` | New | Onboarding drafts; Suggest a change and the agent's feedback when they name a business term |

Two sources and two of today's families sit outside the five kinds, and Curate lists them too, because Curate must cover every review action before Context's review goes (§9):

| What | Where it goes in Curate |
|---|---|
| The portal catalogue's communities and repositories | Their links to the graph arrive marked `proposed` and are curated as domains, the matrix's fourth curation kind (row 4) |
| `privacy` (personal data in documents) | Listed under Documents beside `document_tier`, decided as today |
| `xref_match` (client cross-reference matches) | Its own list, decided as today |

Open in #503: Suggest a change, the agent's feedback as a source of proposals, and the portal catalogue's communities and repositories as domains are not built in this pull request.

### 6.3 Roles

| Action | Matrix row (§2.1) | Who |
|---|---|---|
| A proposal arrives from a person (Suggest a change, feedback) | Suggest a change; ✓/✗ feedback on answers | Business, steward, engineer |
| A proposal arrives from the pipeline (tagging, inference, onboarding) | No person acts; the producer runs as the pipeline's own service role (§2.2) | — |
| Accept or reject it | Curate: accept or reject proposals | Steward only |

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| Onboarding drafts as proposals | Decided (EM): a draft's items are proposals. They enter the steward queue in the families above, and the draft as a whole is still reviewed as Curate's candidate to review (§7) |
| Documents | Decided (EM): Curate's "documents" kind is document tiers, the new `document_tier` family (§6.2), not `privacy` |

## 7. The screens (requirement 4)

The issue names the modes in two places, in its own words. The owner's direction: Chat and Graph for everyone, Curate for stewards, sharing one state, cycled by a switch and a key. Requirement 4: Explore (everyone), Curate (stewards), Overview (later).

This section settles the modes, their shared state and address, the deep links both ways, the `graph_path` highlight, and the graph's drawing budget.

### 7.1 The modes

The layout is the one the owner approved on 2026-11-15, from the prototype (§1.5). The look uses the portal's existing design tokens and components, as the other apps do, not the prototype's colours or fonts.

| Mode | Who | Layout | Matrix rows (§2.1) |
|---|---|---|---|
| Chat | Business, steward, engineer | The conversation in the middle; a foldable panel on the left with the sessions and a small graph of the last answer's path, with "Open graph" | Ask the agent; Suggest a change; ✓/✗ feedback |
| Graph | Business, steward, engineer | The graph across the free width, under one filter row; Fit, Center, the zoom and a foldable legend over its left edge; the overview panel on the right, replaced by the selection's details; the assistant floating over the graph's foot, hideable, with the answer's path highlighted in the graph | Read the graph; Ask the agent |
| Curate | Steward only | Four stat tiles. On the left, proposals filtered by kind (term tags, new words, filter values, documents, business terms), each kind with its count, with View in graph, Accept and Reject. On the right, the candidate to review with its check results, business acceptance, Certify and sign-off, then the gaps people hit | Curate: accept or reject proposals; Review candidates: business acceptance, steward certification |
| Overview | Later | Not a mode. Its content sits in three places (§1.5): the overview panel beside the graph, the Curate tiles, and a selected node's details | — |

The mode tabs and a key cycle the modes. A person without the steward role does not see Curate, and the key skips it. The sessions panel lists the conversations the page holds (D2). An engineer sees an answer's plan, SQL and trace; no one else does ("Ask with plan, SQL and trace"). An admin with no other role has no Cerebro door: the matrix gives the admin none of Cerebro's rows, and the shell decides who sees an app (`portal/ui/src/components/shell/apps.js:58-61`).

Chat mode's answer is Context's (#607): the card is Context's Ask answer, built from its components, so it shows a person what Context's Ask page shows a person of the same role, with Context's live steps while it is on its way and its suggestions in the question box. A clarification's choices are buttons; a question the agent rephrased shows "Asked as"; a term question shows `get_context`'s definitions and citable sources as a card. Every `ask` sends back the `history` KGE returned on the previous answer, unchanged, as Context's page does: it is KGE's SQL-free continuation contract, so the page holds it for every role, but the page never shows it and the model never reads it. A past question can be edited and asked again from that point; the page sends the conversation up to it.

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| The names of the modes | Decided (EM): the modes are the owner's direction's, Chat, Graph and Curate. Requirement 4's "Explore" is Graph with the assistant over it; "Overview (later)" stays later |
| Business acceptance in Curate | Decided (EM): recorded in Curate by stewards, as the matrix's row 5 gives "Review candidates: business acceptance" to stewards. `/accept` takes stewards as well as the business role (`kge/services/onboard/routes.py:319-320`); the change to its check is listed in §8 |

### 7.2 Shared state and the address

Switching modes keeps the state below. The address carries the mode and the node, so a link reopens the same state.

| State | Kept across modes | In the address | Held where |
|---|---|---|---|
| The mode | — | Yes | The address |
| The selected node | Yes | Yes | The address |
| The conversation | Yes | No | The page (D1); sent with each question |
| The last answer's path | Yes | No | The page, from the answer's `graph_path` |
| The graph's position | Yes | No | The page |

A link therefore reopens the mode and the node; it does not carry a conversation. Nothing is stored in the portal's server.

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| The address's form | Decided (EM): `/cerebro?mode=<m>&node=<id>`, with `m` one of `chat`, `graph`, `curate`. `appForPath` sends an unknown path to Discover today (`shell/apps.js:28-34`), so the build adds `/cerebro` to it (§11) |
| A question in the address | Decided (EM): no question text in the address. The address carries the mode and the node, as the issue states |

### 7.3 Deep links both ways

| From | To | What happens | Matrix row |
|---|---|---|---|
| The agent's answer: "View in graph", or `explore`'s link (§5.3) | The graph | Graph mode opens on the node; the node is selected | Read the graph |
| A node: "Ask about this" | The agent | Chat mode opens with the node filled in to the question | Ask the agent |
| A Curate proposal: "View in graph" | The graph | Graph mode opens on the proposal's node, its proposed link drawn as proposed | Read the graph (the steward's) |

### 7.4 The answer's path

Each answer's path is highlighted in the graph from its `graph_path`, the nodes and edges the answer used (`kge/services/ask/models.py:184`). The small graph panel in Chat draws the same path. Today the Context app draws one answer's path in fixed columns (`portal/ui/src/pages/Context/BehindAnswer.js:101`); Cerebro draws it on the whole graph instead.

The highlight is filtered as the graph read is: a node of the path that the person may not read is not drawn.

Decided (EM): `graph_path` is "omitted in compact mode" today (`models.py:184`); compact mode will include it. A non-engineer's answer is compact (no plan, SQL or trace, §8), so this is what lets every role's answer carry its path. It is a KGE behaviour change, listed in §8.

### 7.5 The graph's drawing budget

D3 settles the renderer and the budget. As build acceptance items, each a planning estimate until measured:

| Item | Threshold |
|---|---|
| Visible nodes | About 1,000, columns collapsed into their table by default |
| Benchmark | 1,000 nodes and 2,500 links on a mid-range laptop, panning and zooming, turns the budget into a measured figure |
| Drawing | Only on-screen elements drawn; less detail when zoomed out; memoised nodes |
| Layout | Computed once, off the main thread (d3-force or ELK) |
| Fallback | The renderer sits behind the graph data, so a WebGL library can replace it if the visible graph grows past a few thousand nodes |

Proposed and inferred links are drawn apart from certified ones, as the link's `status` (§5.1) says.

## 8. Roles, checks and audit (requirement 5)

This section maps each action in the role matrix (§2.1) to its endpoint, its KGE check and its audit entry. KGE enforces the matrix; the portal only hides what a person may not do. A blank cell is a 403 from KGE, whichever app or tool made the call, since KGE cannot tell a call from Cerebro from one from Context.

### 8.1 How KGE knows a person's Cerebro role

Each person's deployment roles map onto the four Cerebro roles as the issue states, derived from the processes each role declares (§2.2). The build adds that mapping beside the pack's roles and one check beside today's `may` (`kge/services/http.py:320-323`): the call passes when any of the person's Cerebro roles has a ✓ in the action's row. A person who holds two deployment roles holds both Cerebro roles; an admin who is also a steward may curate as a steward. Pack roles and their processes are unchanged (§3.3). Where the matrix only narrows today's check (rows 1, 2, 3, 6, 7 and 9), the matrix check is added in front of it. Where the matrix gives an action to a role today's process check refuses (rows 4, 5 and 8: `business_team` curating and reviewing, stewards accepting, engineers releasing), the matrix check replaces the process check and the matching entries of `permissions()` (`kge/pipeline/access.py:47-57`) for those endpoints, with one limit: `business_team` reads the steward queue and curates on the curate row, but deciding a queue item (`steward_decide` needs `certification` for every decision), certifying and signing off still need `certification`, so it decides nothing (open in #503); §8.4 lists each.

### 8.2 Endpoint by endpoint

| # | Matrix row | Endpoint (HTTP; MCP tool) | Check today | Cerebro's check | Audit entry |
|---:|---|---|---|---|---|
| 1 | Ask the agent | `POST /ask`, `POST /context`, `GET /ask/runs/{id}`, `GET /runtimes` (`kge/services/ask/routes.py:63`, `:206`, `:134`, `:159`), `POST /answer`, `POST /agent`, `POST /search`, `POST /assist`; MCP `ask`, `get_context`, `list_runtimes` (`ask/mcp.py:28`, `:59`, `:71`), `search`, `assist` | `querying` | Business, steward or engineer | New role-audit entry (§8.3) |
| 2 | Read the graph, filtered to what each person may read | The graph read and `explore`, route and MCP tool (§5) | New | Business, steward or engineer; then the per-person filter (§5.2) | New role-audit entry |
| 3 | Suggest a change; ✓/✗ feedback on answers | The new suggestion endpoint into the steward queue (D4), open in #503; `POST /ask/feedback` and MCP `feedback` from A1 ([answer-modes.md](answer-modes.md), its feedback call) | New; A1's needs `querying` | Business, steward or engineer | New role-audit entry; the queue item records who suggested it |
| 4 | Curate: accept or reject proposals | `GET /steward/queue`, `POST /steward/decide` (`kge/services/steward/routes.py:23`, `:36`) | `certification` | Steward | The overlay journal, as today (`kge/pipeline/overlay.py`), and a role-audit entry |
| 5 | Review candidates: business acceptance, steward certification | `/accept`, `/certify` (`kge/services/onboard/routes.py:319`, `:325`); `/run` with step `sign-off` (`:241-254`) | `/accept`: the business role; `/certify` and sign-off: `certification` (`kge/pipeline/access.py:47-57`) | Steward | The unit's review history, as today (`kge/pipeline/unit_history.py:44`), and a role-audit entry |
| 6 | Build and onboard candidates: pack edits, mappings, filter values, joins | `/tables`, `/source`, `/draft`, `/rows` (`onboard/routes.py:295-313`); `/run` with steps `checks` and `improve`; `GET /margin` | `drafting`, `building`, `improving` | Engineer | Role-audit entry; the onboarding job's own commits, as today |
| 7 | Read the retrieval-side APIs; Ask with plan, SQL and trace | `POST /retrieve`, `GET /map/{term}` (`ask/routes.py:218`, `:228`); MCP `retrieve`, `map_term` (`ask/mcp.py:39`, `:46`); `POST /ask` without compact | `querying` | Engineer. A non-engineer's Ask is answered compact: no plan, SQL or trace | Role-audit entry |
| 8 | Release / roll back a certified candidate that passed the checks | `/run` with steps `release` and `rollback`; `/rollback` (`onboard/routes.py:238`, `:343`) | `certification`, the steward's (`access.py:56-57`; `RUN_CHECKS`, `routes.py:238`) | Engineer | Role-audit entry; the release's own record, as today |
| 9 | Access, identities, settings | `/access` routes and its MCP tools (`kge/services/access/routes.py:31-49`) | The administrator check (`http.py:332-340`) | Admin, unchanged | `access-audit.jsonl`, as today (`kge/pipeline/objstore.py:17`) |

### 8.3 The role-audit entry

Grants, steward decisions and reviews are audited today (rows 4, 5 and 9). A checked call in the other rows leaves no record of who asked for what, so the build adds one entry per call the matrix checks, allowed or refused: the time, the person, their Cerebro roles, the matrix row, the endpoint or tool, and the outcome. A refusal by today's process check behind the matrix is a refused entry too; a failure of that check that is not a refusal (a 503) is none. An access administrator's entry carries the Cerebro role admin, as `/whoami` does. It carries no question text, no answer and no figures. It is written beside the access audit in the client's object store, one object per entry under `role-audit/<YYYYMMDD>/…`, and an admin reads the newest entries of the last 31 days, as the access audit is read today (`access/routes.py:37`).

### 8.4 Behaviour changes

The matrix changes what some of today's roles may do. Each change is a KGE check; no pack role is edited.

| # | Change | Today | Under the matrix | Who it affects on Meridian (§2.2) |
|---:|---|---|---|---|
| 1 | Admin has no Ask (decided, EM) | `admin` holds `querying` | An admin-only person is refused rows 1–3 | `admin` |
| 2 | `retrieve` and `map_term` are engineers only (decided, EM) | They need `querying` | Engineer only (row 7) | Every business and steward role |
| 3 | Plan, SQL and trace are engineers only | Any `querying` caller gets them unless it asks compact (`ask/routes.py:73`) | A non-engineer's answer is compact (row 7) on `/ask`, `/answer` and `/agent`, as JSON and streamed | Every business and steward role, in Context's Ask too |
| 4 | Compact mode carries `graph_path` (decided, EM) | Omitted in compact mode (`kge/services/ask/models.py:184`) | Included, so every role's answer carries its path (§7.4) | None refused; compact answers gain a field |
| 5 | Business acceptance is the steward's (decided, EM) | `/accept` needs the business role (`accepts_drafts`, `kge/pipeline/access.py:31-34`), which no role holding `certification` has | Steward (row 5); the matrix check replaces the business-role check (§8.1) | `steward` and `steward_glossary` gain it; `business_team`, which maps to Steward, keeps it; the other business roles have no `/accept` today, unchanged |
| 6 | Release and roll back are the engineer's | Steward (`certification`) | Engineer (row 8); a steward is refused (separation of duties). The matrix check replaces `RUN_CHECKS`' steward check and `permissions()['release']` for these steps (§8.1) | `steward`, `steward_glossary`, `admin` lose release; `knowledge_engineer` gains it |
| 7 | Admin has no curation or review | `admin` holds `certification`, so it reaches rows 4 and 5 and release | Refused rows 4, 5 and 8 | `admin` |
| 8 | `business_team` curates and reviews | `business_team` has no `certification` (`pack.yaml:1638`) | It maps to Steward: it reads the steward queue and curates on the curate row (row 4) and accepts (row 5); deciding a queue item, certifying and signing off still need `certification`, which it lacks (§8.1; open in #503) | `business_team` |
| 9 | Engineers do not certify | Already refused: `knowledge_engineer` has no `certification` | Unchanged; the matrix check states it | None |

Changes 3, 6, 7 and 8 follow from the matrix as the issue writes it, not from a decision of this design. Change 3 reaches Context's Ask, whose Behind the answer shows the plan, the SQL and the trace; change 6 reaches Context's release, which then needs an engineer. Both are recorded in the pull request.

## 9. The review move and the settings (requirements 6 and 7)

This section designs the Context app's cleanup and the app's settings. Review moves to Curate. Context keeps building, checks and release. Curate covers every review action, and Context's review is removed (§9.1, done). The section lists the Context review screens that move (§1.2), the trigger for removing each from Context, and the app's on/off and name settings.

### 9.1 The review screens that move

| Context screen or action | Where it is today | Endpoint | Its place in Curate | Removed from Context when |
|---|---|---|---|---|
| The Review tab: the pending queue | `portal/ui/src/pages/Context/ContextScreens.js:33`, `Review.js`; shown by `contextState.js:6`, `:10` | `GET /steward/queue` | Proposals by kind (§6.2), with `privacy` and `xref_match` in their own lists | Curate shows every family the queue returns, each with View in graph |
| A decision on a queue item | `Review.js:23` | `POST /steward/decide` | Accept and Reject, with a candidate term's definition | Curate decides every family, including `privacy` and `xref_match` |
| The decided list | `ContextScreens.js:34` | `GET /steward/queue?status=decided` | The proposals' history in Curate | Curate shows it with who and when |
| Business acceptance of a candidate's items | `Onboard.js:93-94`, `:121` (action `accept`) | `/accept` | The candidate to review | Curate records business acceptance (§7.1) |
| Steward certification of a candidate's items | `Onboard.js:93-94`, `:121` (action `certify`) | `/certify` | The candidate to review, with its check results, Certify and Return with a note | Curate certifies and returns |
| Sign-off of a candidate | `Onboard.js:53`, `:58` (step `sign-off`) | `/run`, step `sign-off` | The candidate to review | Curate signs off |

Context's review goes in one step, in this pull request, once every row's Curate counterpart passes its tests on Meridian sample data (job B9, §11). Until then both apps keep all of it. The step removes the screens above, the Review tab and its home-tab rule for `certification` holders (`contextState.js:6`, `:10`); a steward's way into review is then Cerebro.

**Done (job B9).** Every row above has its Curate counterpart under test, but for Return with a note in the certification row, which is open in #503; Context's review is removed: the Review tab, `Review.js`, the decided list, Onboard's accept, certify and sign-off, and an answer's "Send to review" and its link to the queue. A steward and the business team open Context on *Onboard*, which names who does the next step and where, and review in Curate.

What Context keeps: Ask, Docs, Onboard's drafting and building, Quality, the checks, and release and rollback (`Onboard.js:74`). Release and rollback become the engineer's under the matrix (§8.4, change 6).

### 9.2 The app's settings

| Setting | Values | Default in the repository | Where it is read and set |
|---|---|---|---|
| `PORTAL_APP_CEREBRO` | `on`, `off` | `on`, as the other three apps (`portal/server/src/apps.js:14-18`, `:37`) | `portal/server/src/apps.js` (`FLAGS`); `kit/compose/.env.example`; `kit/INSTALL.md`'s settings table; `infra/modules/portal/main.tf`; `infra/pilot/tests/pilot.tftest.hcl`; `.env.example`; `README.md` |
| `PORTAL_CEREBRO_NAME` | The app's display name | `Cerebro` (owner, 2026-11-18; it was `Graph` before #539) | The same files, beside `PORTAL_CLIENT_NAME` |

The single-app rule (`apps.js:39-47`) counts the new flag with the others: with the Knowledge home off, exactly one app is on. Discover, Context and Certify keep their flags and defaults.

### 9.3 The Knowledge home and the shell

The Knowledge home gets a Cerebro box beside Certify, Context and Discover (owner, 2026-11-15). Each box's text, who it is for and what you can do there, follows the role matrix (§2.1):

| App | Who it is for | What you can do there |
|---|---|---|
| Cerebro (by its name setting) | Business users, stewards, knowledge engineers | Chat with the agent; read the graph you may read; suggest and give feedback; stewards curate and review in Curate |
| Context | Knowledge engineers | Building, checks and release; no review, which is Curate's (§9.1, done) |
| Discover | Unchanged | Unchanged |
| Certify | Unchanged | Unchanged: external document certification. Curate's documents items link to it (`PORTAL_CERTIFY_URL`, `apps.js:20-31`) |

| File | Change |
|---|---|
| `portal/ui/src/components/shell/apps.js` | Cerebro in `APPS`, in the doors' order; `appForPath` maps `/cerebro` to it (§7.2); the rule for who may use an app (`:58-61`) lets in a person with a Cerebro role (§8.1), so an admin-only person has no door |
| `portal/ui/src/pages/knowledge/content.js` | A Cerebro entry in `STATIONS`, and Context's `line`, `roles` and `actions` per the table above |
| `portal/ui/src/pages/knowledge/KnowledgeHome.js` | Draws the fourth box |
| Each app's landing text | Cerebro's from `content.js`; Context's per the table above |

## 10. Client material (requirement 8)

This section states how the repository builds on Meridian sample data alone, and which settings carry the client's name, logo and documents on the pilot.

| What | In the repository | On the pilot |
|---|---|---|
| Data, graph and documents | Meridian's invented pack and fixtures only (`kit/INSTALL.md:59`, `:68`); every test and the acceptance run (§11, B10) use them | The client's pack and sources, loaded after installation by the job command the kit's guide gives (`kit/INSTALL.md:59`) |
| The client's name | The neutral default `Client` (`PORTAL_CLIENT_NAME`, `apps.js:6`, `:48`) | `PORTAL_CLIENT_NAME` |
| The product name | `Cerebro`, the default of `PORTAL_CEREBRO_NAME` (§9.2, owner, 2026-11-18) | `Cerebro` |
| The logo | The portal's built-in logo (`PORTAL_LOGO` unset, `kit/INSTALL.md:1113`) | `PORTAL_LOGO`. Cerebro uses the portal's logo, as the other apps do (§3.2) |
| The owner's prototype | Not in the repository: its layout is recorded in §1.5 as structure only | Unchanged; it stays outside the repository |

The look comes from the portal's design tokens, not the prototype's colours or fonts (§7.1). The disclosure check runs on every commit and fails on a client's name in a committed file. A test, a fixture or a screenshot in the build uses Meridian alone.

## 11. The build order (requirement 9)

Issue #466 delivers the Cerebro app and the Context app's cleanup in one pull request; this document is that pull request's design section. This section lists the jobs inside it and their dependencies. It starts from A2's merge (#465), after A1 (#464), whose labels, `confirm_first` and feedback the agent uses.

### 11.1 The jobs

Four lanes: KGE (the graph service, the role checks, the queue's producers, the MCP tools), the portal server (the LangGraph.js agent and its BFF endpoint), the portal UI (the Chat, Graph and Curate port from the owner's prototype onto the design tokens, and the app's settings and Knowledge home), and the Context cleanup. Each job ends at its gate, the checks the change needs ([kge-checks](../../../../.claude/skills/kge-checks/SKILL.md)), before the next job that depends on it starts.

| Job | Lane | What it builds | Design | Depends on | Files it changes (planning estimate) | Gate |
|---|---|---|---|---|---|---|
| B1 | Portal server | The `useStream` check: which of the two options in §4.3 the portal's server takes, and that the copied chat components run on it; the hook's version pinned. The owner placed it first in this pull request (§3.2) | D2, §4.3 | — | The portal's server and page; kept as B5 and B7 use it | §4.3's pass condition, shown on Meridian; that the portal's sign-in hands the portal's server the person's KGE token to send in `x-kge-authorization` (answer-modes §9.4), or a stop to ask the owner; `make portal-tests` |
| B2 | KGE | The graph read and `explore`: route, MCP tool, per-person filter, cursor paging, domain nodes, each link's `status` and `why`, no figures. Repositories and communities from the portal catalogue's sync are open in #503 | §5 | — | New `kge/services/graph/`; `kge/app.py` (the mount) | Demo code's checks (`make lint`, `make test-unit`, `make test-integration`, `make parity`); a test that a unit the person may not read is absent (§5.1) |
| B3 | KGE | The role matrix as KGE checks and the role-audit entry (§8.1–§8.3), with every behaviour change in §8.4: admin has no Ask; retrieval-side tools engineers only; plan, SQL and trace engineers only; compact mode carries `graph_path`; acceptance by stewards; release and rollback by engineers. The role-checked suggestion endpoint into the steward queue is open in #503 | §8, D4 | B2 | `kge/services/http.py`, `kge/services/ask/routes.py`, `ask/mcp.py`, `ask/models.py`, `kge/services/search/`, `kge/services/assist/`, `kge/services/steward/routes.py`, `kge/services/onboard/routes.py`, `kge/pipeline/access.py`, and B2's routes | Demo code's checks, plus the full legs and the golden 20 at 20 of 20, since it changes the answer's fields; one test per blank cell of the matrix that it is a 403 and audited |
| B3b | KGE | The queue's new families and producers (§6.2): `filter_value`, `document_tier` and `business_term`; onboarding drafts' items into the queue; similar-name inference into `suggested_mention`; `/steward/decide` taking each. Suggestions and feedback into their families are open in #503 | §6 | B3 | `kge/services/steward/`, `kge/pipeline/`, `kge/services/onboard/` | Demo code's checks; a test per family that an item queues, decides and is journaled |
| B3c | KGE | Moved to A7 (#497), not delivered in this pull request: the reference agent onto MCP, as requirement 1 carries answer-modes §9.3 (§4.2 items 1–5): the context step on `get_context`, the treatment arms on `ask`, the control arm in process, the layer exception removed, the agent seal manifest | §4.1, §4.2, D5 | B3 | `kge/services/ask/engine/agent.py` and the files answer-modes §9.3 names | Demo code's checks, the full legs, the golden 20 at 20 of 20, and answer-modes §13's checks for A3; it is the MCP contract test B5's gate runs |
| B4 | Portal UI | The app, `PORTAL_APP_CEREBRO` and `PORTAL_CEREBRO_NAME` with its default (`Cerebro` since #539), in the server's and the shell's registries; `appForPath` for `/cerebro`; the Knowledge home's Cerebro box and every box's text (§9.3) | §9.2, §9.3 | — | `portal/server/src/apps.js`, `portal/ui/src/components/shell/apps.js`, `portal/ui/src/pages/knowledge/content.js`, `KnowledgeHome.js`, and the kit and infrastructure files §9.2 names | `make portal-tests`; Infra's and the kit's checks (Terraform `fmt`, `validate`, the mocked `terraform test`, `tests/unit/test_infra.py`, `tests/unit/test_kit.py`) |
| B5 | Portal server | Cerebro's agent: one streaming endpoint, the MCP tools as the signed-in person with the person's token in `x-kge-authorization` (`portal/server/src/kge.js`), only the tools §4.4 grants, `confirm_first` followed; the catalogue agent replaced once the app is on, with Discover's Ask unchanged | §4 | B1, B2, B3, B3c | The portal's server: a new agent module and its route; `kge.js`; `knowledge.js` | `make portal-tests`, with Discover's existing tests unchanged; the reference agent's MCP contract tests (D5) |
| B6 | Portal UI | Graph mode: the whole graph on xyflow, the drawing budget, proposed and inferred links drawn apart, the answer's path highlighted | §7.1, §7.4, §7.5, D3 | B2, B4 | New `portal/ui/src/pages/Cerebro/` | `make portal-tests`; `make test-e2e`; the benchmark of §7.5 run and its figure recorded |
| B7 | Portal UI | Chat mode, the shared state, the address `/cerebro?mode=<m>&node=<id>`, the mode switch and key, and the deep links both ways | §7.1–§7.3 | B1, B4, B5, B6 | `pages/Cerebro/` | `make portal-tests`; `make test-e2e` |
| B8 | Portal UI | Curate: proposals by kind, the candidate to review, the gaps people hit, business acceptance by stewards, documents items linking to Certify | §6, §7.1, §9.1 | B3, B3b, B4, B6 | `pages/Cerebro/` | `make portal-tests`; `make test-e2e`, as a steward and as a business user, who has no Curate |
| B9 | Context cleanup | The review move: each Context review screen removed once Curate covers its action, row by row (§9.1); Context's landing text (§9.3) | §9.1 | B8 | `portal/ui/src/pages/Context/` (§1.2) | `make portal-tests`; `make test-e2e`; each §9.1 row's Curate test passing before its Context screen goes |
| B10 | All | Acceptance: the app on Meridian sample data, Chat, Graph and Curate per the approved layout on the portal's tokens and logo; the role matrix enforced and audited; the golden 20 at 20 of 20; the drawing benchmark measured; independent review and UAT | Issue #466, Acceptance | B1–B9, B3b, B3c | Tests only | The full local gate, the golden 20, the independent review and UAT |

### 11.2 Order

B1, B2 and B4 have no dependency on each other and run in parallel. B3 follows B2, since it puts the matrix check on B2's routes. B3b follows B3, since both change the steward and onboarding routes; B3c follows B3, since both change `ask/mcp.py`. B5 follows B1, B2, B3 and B3c; B6 follows B2 and B4; B7 follows B5 and B6; B8 follows B3, B3b, B4 and B6; B9 follows B8; B10 is last. B6, B7 and B8 change the same new folder, so they are treated as dependent and run in that order.

| Lane | Jobs, in order |
|---|---|
| KGE | B2, then B3, then B3b and B3c |
| Portal server | B1, then B5 |
| Portal UI | B4, then B6, B7, B8 |
| Context cleanup | B9 |
| All | B10 |

Every point §4 to §7 left open is decided (§3.2, and the points marked "Decided (EM)"), so no job waits on one.
