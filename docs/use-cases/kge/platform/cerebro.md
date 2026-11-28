# Cerebro: the graph and its agent, as an app in the portal

| Document control | Value |
|---|---|
| Status | Draft for the owner's approval. Six of the questions this design raised were decided by the owner on 2026-11-15 (§3.2); the points §4 to §7 left open are decided by the engineering manager and marked "Decided (EM)"; one is proposed for the owner to confirm or change, separation of duties (§3.3); the app's default name is `Cerebro` (owner, 2026-11-18, §9.2) |
| Deliverable | The Cerebro app built in the portal and the Context app's cleanup, in one pull request into `develop`, with this document as that pull request's design section. The reference agent's move onto MCP that requirement 1 carries (§4.2) moved to A7 (#497). The build order is §11 |
| Requirement | Issue #466 (A3), as pivoted by the owner on 2026-11-15. It replaces A3's earlier scope in [answer-modes.md](answer-modes.md), §9.3 and §9.4, and carries that scope's approved requirements forward |
| Basis | Code on `develop` at `103de05`. A2 (#465) is still open and changes `portal/ui/src/pages/Context/*`; the owner decided the draft may start before it merges (§3.2), so the Context citations in §1 are re-checked once A2 lands |
| Related | [answer-modes.md](answer-modes.md) (#462): the outcomes and feedback the agent uses (A1, #464; `confirm_first` withdrawn by the owner on 2026-11-21, #624), and the MCP front door (§9); [graph-model-spec.md](graph-model-spec.md): the graph the app reads |
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
| Server flags and names | `portal/server/src/apps.js:32-33` | `PORTAL_APP_CERTIFY`, `PORTAL_APP_CONTEXT`, `PORTAL_APP_DISCOVER`, each on by default (`:37-41`, `:60`) |
| The single-app rule | `apps.js:62-70` | With the Knowledge home off, exactly one app must be on, or the server does not start |
| Product name | `apps.js:7-8`, `:71`, `:74` | `PORTAL_CLIENT_NAME` with a neutral default, `Client` |
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
| Read the retrieval-side APIs; Ask with plan and trace |  |  | ✓ |  |
| Release / roll back a certified candidate that passed the checks |  |  | ✓ |  |
| Access, identities, settings |  |  |  | ✓ |

Separation of duties: engineers build and release but do not certify; stewards certify but do not release.

The owner amended the row "Ask with plan, SQL and trace" on 2026-11-21 (#624): an answer's SQL goes to every role who may ask, on the answer's card, marked "SQL: not certified (AI-Generated)" for an AI-Generated answer; the plan and the trace stay the engineer's.

The term map (`map_term`, `GET /map/{term}`) was a retrieval-side API; the owner gave it to every role that may ask on 2026-11-21 (#622), compact for non-engineers, and its full form, with physical columns and joins, stays in the engineer's row (§8.2, §8.4).

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
| D2 | The chat screen reuses LangChain's open-source Agent Chat UI components (MIT) inside the portal, on LangChain's React chat hook (`useStream`) and the BFF's endpoint. Its chat view, conversation list and agent inbox (the pause that waits for a person: since #624, the one HITL card that asks the person back, never a confirmation before a figure) are copied into the portal and restyled to its design tokens. The BFF either answers the few LangGraph API calls the hook makes (conversations, runs, `/info`) or uses the hook's custom-backend adapter; a half-day check settles which, and that the copied components run on it, before the chat is built. The conversation list shows the conversations the page holds. The hook's version is pinned, since its adapter interface is changing between releases | Agent Chat UI run as it is, a separate Next.js app on LangGraph's JavaScript server: a second app with its own look, conversations held in that server's memory, and a proxy that does not authenticate callers, which breaks the portal's sign-in and the role matrix. assistant-ui: ready components too, but no inbox and not built on the hook |
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
| 7 | `confirm_first` followed | Withdrawn by the owner on 2026-11-21 (#624): answers are delivered with their label, never held for a confirm (answer-modes.md §6); in Cerebro, §4.3 |

### 4.3 The BFF endpoint

One endpoint of the portal's server streams the agent to the page (D1). What D1 and D2 settle:

| Property | Settled as |
|---|---|
| State | None in the portal's server. The page holds the conversations and sends the conversation with each question; Ask stays stateless |
| Request | The question, the conversation so far, and the node the question is about when it comes from "Ask about this" (§7) |
| Response | A stream: the answer's parts as they arrive, the tool calls, and a pause that waits for the person (the agent inbox, D2) only where the agent asks the person back: one HITL card (below). An answer is never held for a confirm (#624) |
| Resume | After the person answers the HITL card, the page sends the conversation with the person's pick or words, and the portal's server runs the turn from that alone, holding nothing between requests. How the hook carries it is settled by the `useStream` check below |
| Sign-in | The portal's own sign-in. The endpoint refuses a caller who is not signed in |
| Version | The `useStream` hook's version is pinned (D2) |

The wire format depends on the half-day `useStream` check (D2), which the owner placed as the first job of this pull request (§3.2, §11). Its two options, as D2 states them:

| Option | What the portal's server does | Pass condition of the check |
|---|---|---|
| A | Answers the few LangGraph API calls the hook makes: conversations, runs, `/info` | The copied chat view, conversation list and agent inbox stream an answer, show a tool call, and pause and resume on a HITL card, against the portal's server, with no LangGraph server running |
| B | Serves the hook's custom-backend adapter | The same, through the adapter |

How the agent answers each question (#622). The rules are the prompt's, with a few worked examples; the code adds the safety net and the turn's end, and holds no other business logic. Small talk is the prompt's rule too (#672), with no list of words in code: it names no client, pack or term, and a question about the data, terms or the catalogue, however short, still goes to the tools; only such a question, without a tool's answer, is told the agent cannot answer.

| Question | Tool the agent calls | Its reply on the page |
|---|---|---|
| A figure, a table or a trend | `ask` | Ask's compact card (#613) |
| The difference between two declared terms ("what is the difference between X and Y", and its rewordings) | `ask` | The difference card, then the "Compare the figures?" offer (below) |
| A meaning, a steward, an owner, a source, or what data there is about something | `get_context` | The terms with their stewards, and the citable sources |
| Which tables or columns hold something, how things connect, lineage | `explore_question` or `explore` | The node, its linked nodes by status, and a small graph |
| A catalogue lookup | `search` | The hits, in business words |
| A term's data sets and documents | `map_term` | The term map, compact for anyone but an engineer |
| A greeting, a thanks or small talk (#672) | None | One short sentence from the agent, offering to answer a question about the data; nothing reaches Ask. A message short enough to be framed still gets the portal's framing `get_context` first (Framing, below) |

| Rule | Settled as |
|---|---|
| Safety net | Where Ask declines a question as outside figures and definitions (its reason is KGE's catalogue abstention), the agent calls `explore_question` in the same step when the question asks where something is held, how things connect or where they come from, and `get_context` otherwise, so the turn does not end on not sure |
| A card ends the turn | A tool reply that is a card ends the turn with no second model call, as an answering `ask` does (#619): `get_context` with a term it defines or a citable source, `explore` and `explore_question` with a node, `read_graph` with nodes, `search` with hits, `map_term` with an implementation, a data set, a document or a context. Any other reply is no card, and the agent says what it found in one short sentence |
| The `ask` tool's description | It names definitions and the difference between two declared terms: "Answer a business question with governed figures and definitions, including the difference between two declared terms. Never compute or change a figure." A single term's meaning, steward or sources still go to `get_context` (#624) |
| Thinking | The agent's model calls think at level low on Gemini 3 and later, as Ask's planner has since #589; an earlier model is sent no level |
| A busy model | One automatic retry when the model is only busy (429, 503, resource exhausted, unavailable); any other model failure ends the turn |
| Examples (#621) | At the start of each turn the portal fetches the showcase questions KGE serves the person (`GET /golden`, as the person) and gives the model up to 20 of them as examples of well-formed questions, quoted as data. A list the page sends is never read |
| Framing (#621) | A short, unclear or follow-up question (at most 2 words; or, after an earlier `ask`, at most 8 words or one opening with a follow-on word such as "and", "what about" or "only") gets one `get_context` call of the portal's own before the model writes the full question for `ask`, so it uses the client's declared terms, periods and filters. It is decided in code, with no model call; it shows no card and does not end the turn. A clear standalone question goes straight to the model |
| "What can I ask about X?" (#621) | Routed to `explore_question`. After its card, a choice card lists up to 4 showcase questions that name X or a node the reply holds; a pick asks it as the person's next question. The card never pauses the turn |
| Background runs (#630) | Every `ask` is sent with `background: true`. Where KGE's answer is not ready within `QUESTION_START_WAIT`, the turn ends at a pending card ("<question> · running in the background · 1:12 · Stop"), and the chat takes the next question. The run's id is kept in the saved conversation. Between turns the page asks the portal at KGE's `poll_after`; the portal reads the run id from the conversation and calls `question_run` as the person, until KGE's own run limit. When the run ends, the portal writes the ending in place of the pending card: the answer card; the timeout card, with KGE's message and "Try a shorter period or fewer groups."; or the warehouse error card. Stop calls `stop_question_run`. Reopening the conversation on any instance picks the run up again; the follower writes only once the conversation's last turn has ended (a turn that failed counts as ended 25 minutes after the conversation's last save, beyond the 1,200-second limit Cloud Run puts on the request that runs a turn), so on Google Cloud it never ends a turn under way. Under Compose a turn is bounded only by its connection; one that outlives the hold is ended at its next save as a changed conversation, never quietly, and Stop waits while a turn runs. A run that ends with an answer is posted as that answer's card alone: a clarification, a not-sure retry, the offer after a difference and the catalogue safety net, which follow a direct answer in its turn, do not follow one that ended in the background. Cerebro has no time setting of its own |

The one step before `ask` (#707, #714). Before the model reads a new question, the portal asks KGE how it reads it, and asks the person once for whatever is still open. KGE reports facts; the policy and the card are Cerebro's. No model call fills an item.

| Rule | Settled as |
|---|---|
| When | Once, at the start of every new question's turn, for anyone who has `ask` and `get_context`. Which tool a question is bound for is the model's choice, and the step must not depend on the model, so every new question is read |
| What is read | `get_context` with the question's bindings and open items, with the conversation's history, as `ask` will read it. Then `map_term` for the meanings of a name, and `search` for filter phrases, only where the bindings do not settle a meaning |
| The limit on calls | At most `PORTAL_CEREBRO_CALLS_BEFORE_ASK` KGE calls for one question (§9.2; default 6, a planning estimate until measured; at least 2, so the read and the first name's meanings always run). When it is spent, what is still open goes on the card: a name whose qualifier was not checked stays open with every meaning KGE lists, and a name whose meanings were not read is asked with the one meaning KGE bound it to, which the person confirms or answers past with "Something else". A call that fails within the limit is not a spent limit: the name stays as KGE bound it, and nothing is asked of it |
| Order | KGE's access refusal first: Cerebro says it, in KGE's words, with no card and no `ask`. Then a period KGE reports as not answerable: Cerebro says `ask`'s reason where KGE gives one, names the latest covered period, and makes no card and no `ask`. Only then a card |
| An open meaning | Whether a meaning is open comes from KGE's open items: a name bound by its default alias that names several readable meanings. It is not open where the person wrote a time mode directly before the name that is the bound meaning's own ("current NNA"). A name KGE bound explicitly is settled, with one exception: where its qualifier also names a filter value (`search` finds it as a filter phrase), the word names two things and the meaning stays open ("retail NNA"). A pick of the meaning KGE bound the phrase to takes the qualifier's place; any other pick leaves the person's qualifier in the question |
| The open output | Whether the person wants definitions, figures or both is Cerebro's own check, with its request words (below). KGE's bindings only tell a grouping or a comparison from other words: "Show me … by X" asks for figures where KGE read X as a grouping, and a comparison KGE bound counts whatever its wording |
| A period or grouping | A grouping word KGE reports as naming several declared dimensions goes on the card, with every dimension KGE reports and no cap. A period that `ask` would settle by a default goes on the card only when the card is already needed, with the latest three covered periods. It is not offered where the question names a period in the portal's own time words (below), whatever KGE read: a second period would contradict the person's |
| A question for definitions or another output | A question that asks, in Cerebro's request words (below), for definitions or another output (stewards, tables, documents) and for no figure is not one the step fills: it gets no Period group and no card, and goes on to the model, which chooses its tool as before the step. So a card's answer never ends at `ask`'s decline of such a question. A request word in a place KGE bound or reports as open ("by source system") is KGE's reading, not a request, and a question that also asks for a figure keeps the step. A "Something else" answer is not read this way |
| Items the card does not hold | An item that says `ask` cannot answer on another ground does not stop the card and opens none. A clarification that is `ask`'s own goes to `ask`, which clarifies on its own card as before |
| One card | Two or more open questions make one multiple-question card: a labelled group each ("Meaning", "Show", "Period", and the person's own grouping word), nothing preselected, one Send. One open question is the one-question card, as on kit 0.14.58: a pick sends at once. At most one card comes before the first `ask`, and it holds at most six groups, the first six |
| The suggested meaning | The card lists the suggested meaning first and marks it "(default)". Until #709 lets a pack declare a family's suggested default, the portal's one named, temporary rule marks the option whose label holds "certified" (`SUGGESTED_UNTIL_709`). It is never the default meaning, the one `ask` binds by default. Where no option, or more than one, carries the word, none is marked. Labels and definitions are KGE's |
| Shape, not meaning | The question sent to `ask` is the person's own, with each pick's words in the place the card saved for it. A Show pick reframes it as "What was …?" or "What is the definition of …?". No other word is added. "Both" sends the definitions question and the figures question. The prompt gives the model the same rule for the questions it sends itself |
| A pick | Accepted only against the card the server saved in the conversation: one offered pick per group shown. On the one-question meaning card the answer is held to the picked meaning: a figure for another meaning is not shown |
| Something else | One answer in the person's own words that covers everything. On a one-question card, words that name exactly one option are that pick. Any other answer is read back through the same KGE tools as a full question: where nothing is open it is sent to `ask` as written; where anything is still open (a period settled by a default counts here), Cerebro asks the person to restate the full question, with no second card and no `ask`. The read-back has the same limit on calls |
| Golden questions | A question that is exactly one of the pack's golden questions (case, punctuation and spacing folded) is asked as written: no read, no card. Decided once, at this step. A "Something else" answer that is a golden question is asked as written too, with no read-back |
| When KGE cannot be read | A failed or missing reading leaves the turn as it would be with no step: the model sends the question to `ask`, which applies its defaults and its own refusals. A "Something else" answer that cannot be read back is sent to `ask` as written. A fault in the step's own code is treated the same way and is written to the portal's log ("Cerebro step before ask not read", with the error's type and message), so it never turns the step off unseen |

**No word lists, with two named exceptions.** The meaning check and the intent check hold no meaning, comparison or "by" word list, and no word that locates a mention: KGE gives each place in the question. What stays:

- **Cerebro's request words** (#707 decision 10): the words that tell an open request ("show me", "tell me about"), a request for definitions, a request for figures and a request for another output (documents, stewards, tables). They decide whether the output is open, and whether a question asks for definitions or another output and no figure, which the step leaves to the model.
- **The "certified" marking rule**, named and temporary, until #709.

A test reads every literal of the three modules (the meaning check, the intent check and the step) and fails if any other list or word comes back. A second test reads every module of Cerebro's server for the ways a word list is declared (a regular expression that holds words, an array or a Set of words, a string split into words) and fails on any that its allow-list does not name with a reason; the two business exceptions above are in it, each in its one module. The portal's time words and follow-on words (Framing, above) are outside #707 and stay as they are; the step uses the time words for one thing, to offer no Period group where the question already names a period in them.

**The evaluation suite through Cerebro** (#707 requirement 16). Every wording of the suite's fixture is asked of Cerebro's agent against a stand-in KGE that answers with what KGE recorded for it. Its expected outcome (no card, which card and which groups, a period with no data, a refusal) comes from #707's requirements: the suite's oracle derives it from KGE's recorded reading with none of the meaning check's, the intent check's or the step's code, and the wording's outcome must equal it. Each wording also carries a verdict, pass or fail against that expected outcome, for the hotfix's recorded outcome and for the outcome now, and the suite fails if a wording that passed under the hotfix fails now, apart from the exceptions it names. A wording that negates or hedges its qualifier ("not certified") is guarded: with no card it passes only where `ask` gives no figure, which KGE's own tests hold with and without a model.

What the person sees of an answer, and the one question back (owner, 2026-11-21, #624):

| Rule | Settled as |
|---|---|
| Delivered | Every answer is delivered with its label, Certified or AI-Generated, at once: no Confirm, no figure held back, no figure-free first text |
| SQL on the card | The card shows the figure, table or chart with its label, and the SQL behind it, readable and copyable, for every role who may ask; where `sql_certified` is false it is headed "SQL: not certified (AI-Generated)". The plan and the trace stay the engineer's (§8.2, row 7) |
| ✓/✗ on the card | Confirmed and Not what I meant are on the card itself, sending the same `feedback` call as Context's (answer-modes.md §8.1) |
| One HITL card | Every question back to the person is one card, a LangGraph interrupt: a clarification's choices, or the agent's own question when it cannot decide which question to send to `ask`. A clarification's card holds Ask's question and the choices Ask returns; the agent's card holds its question (at most 200 characters) and two to four options (at most 80 characters each), with no figure, or it is refused. Both offer "Something else". A pick on a clarification sends the restated question to `ask` and posts its card; a pick on the agent's own card, or "Something else", goes back to the model. The cards the step before `ask` issues (above) are the same card: a pick, or "Something else", on one of them never goes back to the model. The portal signs each card it issues for the person, under a key each instance makes at start, as it marks the results it produces. A pick on a card whose signature does not verify (another instance or a restart signed it, or the page made it up) takes a path no more powerful than the person typing: a clarification's pick goes to `ask` as the person's own words, a pick on the agent's card or "Something else" becomes the person's next message, and an offer holds only where the answer it follows verifies; nothing that path makes is marked or signed, and it calls the model only as a typed message does. The transcript shows the pick, never a restated question |
| The offer | After a difference answer only (never after a one-term definition), the card "Compare the figures?" offers the latest month, the last quarter, a month picker and "No thanks", all within the months both terms' data cover, whole months only (a daily range counts a month only where it covers all of it); nothing is offered when they do not overlap. It holds no figure and never pauses the turn. A pick asks one question naming both terms and the period, in the form "Compare current vs. certified Q3 2024 NNA by month, with the dollar and percent difference", and posts one card; only where that reply has no error and gives no figure or table (a clarification or an abstention) does it ask one question per term and post both cards together, while an error, a refusal or a blocked access ends the turn failed; a clarification on either is asked on its HITL card. A typed question moves on |

The stream carries five kinds of the agent's own events beside the answer's parts. Each field is plain words, a count or a state: no id beyond the tool call's, no SQL, no physical name and never a figure.

| Event | What it carries | When |
|---|---|---|
| `step` | The step under way, in plain words ("Understanding your question", "Checking the client's terms", "Planning the query", "Running it on the warehouse", "Checking the figures") | At the turn's start, before each tool call, and as the warehouse starts and the answer arrives |
| `ask_progress` | Ask's graph path as it is traversed; Ask's stages for an engineer alone | As Ask streams them; never Ask's plan or raw context |
| `readout` | What is being computed, built from Ask's context and graph-path events with no model call (Ask's plan event carries SQL, so it is not read): the measure, certified or current, the period, the filters, the grouping and the source | Each time it grows |
| `warehouse` | Each warehouse statement's state (queued, running, done, failed) and its elapsed time | Each warehouse progress event |
| `outcome` | Answered, paused, background (#630: the turn ended at a pending card) or failed, the step count, and for a failure its case, plain message and next action; the raw reason for an engineer alone | Once, the turn's last event |

| Failure | Detected by | The message | Next action |
|---|---|---|---|
| Not planned | The turn ends with no card and its last Ask reply was not sure, a decline or a refusal, after the safety net | Ask's reason, cut by the agent's figure rule | Rephrase |
| Unsafe query | Ask's error reply: the SQL gate refused it, or it did not compile | "The query couldn't be built safely." | Rephrase |
| Warehouse failed | A warehouse event with state failed, or Ask's warehouse error | "The warehouse query failed." | Try again |
| Too long | Ask's time or size limit, or the request budget | "This took too long; try a narrower period or fewer groups." | Try again, or narrow it |
| KGE unreachable | The tool call's error | "Couldn't reach the knowledge graph." | Try again |
| Model failed | The model call's error, after the one retry | "The assistant couldn't respond." | Try again |

A failure ends the turn at once, with no further model call.

### 4.4 The MCP tools, and with whose bearer

Every MCP tool call runs as the principal whose bearer the request carries; a browser cookie is never taken (`kge/services/http.py:303-310`). KGE reads the bearer from `x-kge-authorization`, else `authorization` (`http.py:306`). Today the portal's server forwards the person's cookie and `authorization` header to KGE (`portal/server/src/kge.js:9-15`), and authenticates itself to the platform with `x-serverless-authorization` (`portal/server/src/kgeauth.js:2`, `:43`).

| Tool | Today | Matrix row (§2.1) | Who may call it through Cerebro's agent |
|---|---|---|---|
| `get_context` | `ask/mcp.py:28`, mounted at `/mcp` (`app.py:426`); needs `querying` (`ask/mcp.py:79-80`) | Ask the agent | Business, steward, engineer |
| `ask` | `ask/mcp.py:59`, at `/mcp/answer`; needs `querying` | Ask the agent | Business, steward, engineer |
| `question_run`, `stop_question_run` | `ask/mcp.py`, at `/mcp/answer`, over `GET` and `DELETE /ask/runs/{run_id}`; needs `querying`, the run's owner only (#630) | Ask the agent | Business, steward, engineer |
| The graph read | New (§5) | Read the graph, filtered to what each person may read | Business, steward, engineer |
| `explore`, `explore_question` | New (§5.3) | Read the graph, filtered to what each person may read | Business, steward, engineer |
| Suggest a change | A new role-checked endpoint into the steward queue (D4) | Suggest a change | Business, steward, engineer |
| ✓/✗ feedback on an answer | A1 (#464) | ✓/✗ feedback on answers | Business, steward, engineer |
| `search` | `kge/services/search/mcp.py`, at `/mcp/search`; needs `querying` | Ask the agent | Business, steward, engineer (owner, 2026-11-21, #622) |
| `map_term` | `ask/mcp.py`, at `/mcp`; needs `querying` | Ask the agent; the full map is the engineer's (row 7) | Business, steward, engineer; compact for business and steward (owner, 2026-11-21, #622) |

No other row of the matrix is reached through the agent. Admin has no ✓ in the Ask row; today's `admin` role holds `querying` (§2.2), so Cerebro's checks refuse an admin the Ask row (§8). The plan and the trace of an answer are the engineer's row ("Ask with plan and trace"); the agent shows them to an engineer only. Its SQL goes to every role who may ask, on the answer's card (owner, 2026-11-21, #624).

Decided (EM), 2026-11-15:

| Point | Decision |
|---|---|
| How the portal's server obtains a bearer for the signed-in person | Decided (EM): the portal's server sends the signed-in person's own KGE token in `x-kge-authorization`, as [answer-modes.md, §9.4](answer-modes.md#94-the-portals-catalogue-agent-onto-mcp) and its decision D4 state. There is no shared service identity: every tool call runs as the person |
| Whether the agent also uses `retrieve`, `map_term` (`ask/mcp.py:39`, `:46`), `search` (`kge/services/search/mcp.py:13`) or `assist` (`kge/services/assist/mcp.py:16`) | Decided (EM), then changed by the owner on 2026-11-21 (#622): the agent offers `search` and `map_term` to every role and `retrieve` and `assist` to engineers only. `map_term` is filtered to what each person may read and holds no figures; KGE answers it on the Ask row, full for an engineer and compact for anyone else: the term's data sets, documents, steward and contexts in business names, with physical columns, no-join facts and certified joins left to the engineer, as Ask's compact answer leaves out the plan (and, until #624, the SQL). `retrieve` remains engineer-only by default; its opt-in bounded passage mode is available to querying roles (§8.4); `search` and `assist` keep their `querying` check, because Discover's search and Ask call them for everyone (`portal/server/src/knowledge.js:235-246`) and the issue keeps Discover as it is. The EM's decision of 2026-11-15 offered all four to engineers only, as retrieval-side tools |
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

The owner widened both on 2026-11-21 (#628), from what the graph already holds and with no model call, warehouse query or new graph data: `explore_question` also explains the question's whole path in order (the term and the words that name it, the context, fact table and measure, each join hop in the context's order with its kind, cardinality, type and validity window from the pack's join options, or SQL generation's defaults where none is declared, a warning on any one-to-many hop, the calendar roll-ups, hierarchy levels and codes and cross-references reached, the filters, exclusions and stated conditions, and the time rules); `explore` lists a hierarchy term's members and codes and gives a join's (`join:<id>`) facts; and `map_term` (`GET /map/{term}`) carries the same per-term view, each of the term's contexts explained as `explore_question` explains it, on top of #622's compact form, whose own fields are unchanged; the compact form's join hops are in business words, with no join condition. Business words for every role, physical names for an engineer (row 7), and a unit the person may not read is absent.

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

The mode tabs and a key cycle the modes. A person without the steward role does not see Curate, and the key skips it. The sessions panel lists the conversations the page holds (D2). An engineer sees an answer's plan and trace; no one else does ("Ask with plan and trace"). Every role who may ask sees the answer's SQL on its card (#624). An admin with no other role has no Cerebro door: the matrix gives the admin none of Cerebro's rows, and the shell decides who sees an app (`portal/ui/src/components/shell/apps.js:58-61`).

Chat mode's answer is Context's (#607): the card is Context's Ask answer, built from its components, so it shows a person what Context's Ask page shows a person of the same role, with the turn's working block while it is on its way (below) and Context's suggestions in the question box. A clarification's choices are the one HITL card (§4.3, #624); a question the agent rephrased shows "Asked as"; a term question shows `get_context`'s definitions and citable sources as a card. Every `ask` sends back the `history` KGE returned on the previous answer, unchanged, as Context's page does: it is KGE's SQL-free continuation contract, so the page holds it for every role, but the page never shows it and the model never reads it. A past question can be edited and asked again from that point; the page sends the conversation up to it. Each turn reads in one order (#672): the question, its Edit right under it, then when the reply was sent and how long it took, in small grey text just above the reply and aligned with it, then the reply, then Copy, Like and Dislike as icon buttons in the page's grey ink. Copy's tooltip says Copy, then Copied for two seconds, as its live region does; Like and Dislike are outlines, the chosen one filled in the same grey, one verdict per reply.

Each question has its turns' working blocks under it (#622), drawn from the agent's events (§4.3). While a turn runs, its block shows a spinner, the step under way and the seconds so far, then at most five lines, the newest kept: the steps done, the readout, the graph path and each warehouse statement's state. The input bar shows Stop and no second status line. With reduce motion on, the spinner blinks slowly instead of spinning; it never stands still. Every block ends in a final state, and no spinner is left running.

| Outcome | The block's final line | Then |
|---|---|---|
| Answered | "✓ Worked for 6 s · 4 steps", which opens to the steps and the readout | The answer's card |
| Paused | "Paused · needs your choice" | The choice; a new block continues after it |
| Stopped | "Stopped after 5 s" | The question can be edited and asked again |
| Failed | "✗ Not answered · 7 s", which opens, with the readout kept in view | The error card |

The figures in the lines above are examples. Each block has one polite live region, the same element from its first step to its end, so a screen reader hears each step once and then the final state; the seconds are not read out. The error card is an alert with the failure's message and its actions: Try again sends the same question again, in its place where it is the conversation's last or the conversation is at its eight-question limit; Rephrase, and Narrow it, put the question back in the box; a suggested question, where the server gives one, is a button that asks it. An engineer can open the run's detail. A not-planned turn shows Ask's not-sure card and the error card, and not the agent's sentence after them, so the reason is not said three times.

The page adds three states of its own, since the server cannot send them:

| State | When | What the person sees |
|---|---|---|
| Connection lost | The stream ends, or fails, with no outcome | "Connection lost"; Try again |
| Still waiting | About 20 s with no event: the step line says "Still waiting for the warehouse…" while the warehouse runs, else "Still waiting for the answer…" | None; the turn goes on |
| Timed out | About 120 s with no event: the page stops the turn | "This took too long; try a narrower period or fewer groups."; Try again |

Any event starts both waits afresh. The page never freezes while a turn runs: the person can type, scroll, open cards and switch modes, and switching conversation stays blocked. A question sent while a turn runs waits above the box as "Next: …", which can be removed and is announced politely, and goes as soon as the turn ends, however it ends; Stop ends the turn, so the next question goes then, and a turn paused on the person's choice keeps it until they choose. A queued question is never sent past the eight-question limit: it stays, marked "Next, in a new chat", and goes when New chat starts one, unless it is removed. A queued question whose send fails does not hold up the ones behind it.

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

Decided (EM): `graph_path` is "omitted in compact mode" today (`models.py:184`); compact mode will include it. A non-engineer's answer is compact (no plan or trace, §8; its SQL kept since #624), so this is what lets every role's answer carry its path. It is a KGE behaviour change, listed in §8.

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
| 1 | Ask the agent | `POST /ask`, `POST /context`, `GET /ask/runs/{id}`, `GET /runtimes` (`kge/services/ask/routes.py:63`, `:206`, `:134`, `:159`), `DELETE /ask/runs/{id}` (#630), `POST /answer`, `POST /agent`, `POST /search`, `POST /assist`, `GET /map/{term}`; MCP `ask`, `get_context`, `list_runtimes` (`ask/mcp.py:28`, `:59`, `:71`), `question_run`, `stop_question_run` (#630), `search`, `assist`, `map_term` (owner, 2026-11-21, #622) | `querying` | Business, steward or engineer | New role-audit entry (§8.3) |
| 2 | Read the graph, filtered to what each person may read | The graph read and `explore`, route and MCP tool (§5) | New | Business, steward or engineer; then the per-person filter (§5.2) | New role-audit entry |
| 3 | Suggest a change; ✓/✗ feedback on answers | The new suggestion endpoint into the steward queue (D4), open in #503; `POST /ask/feedback` and MCP `feedback` from A1 ([answer-modes.md](answer-modes.md), its feedback call) | New; A1's needs `querying` | Business, steward or engineer | New role-audit entry; the queue item records who suggested it |
| 4 | Curate: accept or reject proposals | `GET /steward/queue`, `POST /steward/decide` (`kge/services/steward/routes.py:23`, `:36`) | `certification` | Steward | The overlay journal, as today (`kge/pipeline/overlay.py`), and a role-audit entry |
| 5 | Review candidates: business acceptance, steward certification | `/accept`, `/certify` (`kge/services/onboard/routes.py:319`, `:325`); `/run` with step `sign-off` (`:241-254`) | `/accept`: the business role; `/certify` and sign-off: `certification` (`kge/pipeline/access.py:47-57`) | Steward | The unit's review history, as today (`kge/pipeline/unit_history.py:44`), and a role-audit entry |
| 6 | Build and onboard candidates: pack edits, mappings, filter values, joins | `/tables`, `/source`, `/draft`, `/rows` (`onboard/routes.py:295-313`); `/run` with steps `checks` and `improve`; `GET /margin` | `drafting`, `building`, `improving` | Engineer | Role-audit entry; the onboarding job's own commits, as today |
| 7 | Read the retrieval-side APIs; Ask with plan and trace | `POST /retrieve` (`ask/routes.py:218`); MCP `retrieve` (`ask/mcp.py:39`); `POST /ask` without compact; the full term map | `querying` | Engineer. A non-engineer's Ask is answered compact: no plan or trace. Its SQL, the answered query's alone, with `sql_certified`, goes to every role who may ask (owner, 2026-11-21, #624); a decline's compact reply carries none. `GET /map/{term}` and MCP `map_term` are on row 1 (owner, 2026-11-21, #622): an engineer's map is full, anyone else's compact, in business names, with no physical column, no-join fact or certified join | Role-audit entry |
| 8 | Release / roll back a certified candidate that passed the checks | `/run` with steps `release` and `rollback`; `/rollback` (`onboard/routes.py:238`, `:343`) | `certification`, the steward's (`access.py:56-57`; `RUN_CHECKS`, `routes.py:238`) | Engineer | Role-audit entry; the release's own record, as today |
| 9 | Access, identities, settings | `/access` routes and its MCP tools (`kge/services/access/routes.py:31-49`) | The administrator check (`http.py:332-340`) | Admin, unchanged | `access-audit.jsonl`, as today (`kge/pipeline/objstore.py:17`) |

### 8.3 The role-audit entry

Grants, steward decisions and reviews are audited today (rows 4, 5 and 9). A checked call in the other rows leaves no record of who asked for what, so the build adds one entry per call the matrix checks, allowed or refused: the time, the person, their Cerebro roles, the matrix row, the endpoint or tool, and the outcome. A refusal by today's process check behind the matrix is a refused entry too; a failure of that check that is not a refusal (a 503) is none. An access administrator's entry carries the Cerebro role admin, as `/whoami` does. It carries no question text, no answer and no figures. It is written beside the access audit in the client's object store, one object per entry under `role-audit/<YYYYMMDD>/…`, and an admin reads the newest entries of the last 31 days, as the access audit is read today (`access/routes.py:37`).

### 8.4 Behaviour changes

The matrix changes what some of today's roles may do. Each change is a KGE check; no pack role is edited.

| # | Change | Today | Under the matrix | Who it affects on Meridian (§2.2) |
|---:|---|---|---|---|
| 1 | Admin has no Ask (decided, EM) | `admin` holds `querying` | An admin-only person is refused rows 1–3 | `admin` |
| 2 | Legacy `retrieve` is engineers only; bounded citable-document passage mode is available to querying roles. `map_term` is every role's, compact for non-engineers (owner, 2026-11-21, #622) | They need `querying` | `retrieve` without `include_passages`: engineer only (row 7); `include_passages: true`: business, steward or engineer (row 1), scoped to the caller and purpose, with eligible gold or silver documents. `map_term` and `GET /map/{term}`: business, steward or engineer (row 1); a non-engineer's map is compact, with physical columns, no-join facts and certified joins left to the engineer (row 7) | Business and steward roles can use passage mode but not legacy retrieval; an admin-only caller is refused. Business and steward roles get the compact map |
| 3 | Plan and trace are engineers only; SQL is every asker's (owner, 2026-11-21, #624) | Any `querying` caller gets them unless it asks compact (`ask/routes.py:73`) | A non-engineer's answer is compact (row 7) on `/ask`, `/answer` and `/agent`, as JSON and streamed: no plan or trace, and the answered query's SQL with `sql_certified` | Every business and steward role, in Context's Ask too |
| 4 | Compact mode carries `graph_path` (decided, EM) | Omitted in compact mode (`kge/services/ask/models.py:184`) | Included, so every role's answer carries its path (§7.4) | None refused; compact answers gain a field |
| 5 | Business acceptance is the steward's (decided, EM) | `/accept` needs the business role (`accepts_drafts`, `kge/pipeline/access.py:31-34`), which no role holding `certification` has | Steward (row 5); the matrix check replaces the business-role check (§8.1) | `steward` and `steward_glossary` gain it; `business_team`, which maps to Steward, keeps it; the other business roles have no `/accept` today, unchanged |
| 6 | Release and roll back are the engineer's | Steward (`certification`) | Engineer (row 8); a steward is refused (separation of duties). The matrix check replaces `RUN_CHECKS`' steward check and `permissions()['release']` for these steps (§8.1) | `steward`, `steward_glossary`, `admin` lose release; `knowledge_engineer` gains it |
| 7 | Admin has no curation or review | `admin` holds `certification`, so it reaches rows 4 and 5 and release | Refused rows 4, 5 and 8 | `admin` |
| 8 | `business_team` curates and reviews | `business_team` has no `certification` (`pack.yaml:1638`) | It maps to Steward: it reads the steward queue and curates on the curate row (row 4) and accepts (row 5); deciding a queue item, certifying and signing off still need `certification`, which it lacks (§8.1; open in #503) | `business_team` |
| 9 | Engineers do not certify | Already refused: `knowledge_engineer` has no `certification` | Unchanged; the matrix check states it | None |

Changes 3, 6, 7 and 8 follow from the matrix as the issue writes it, not from a decision of this design. Change 3 reaches Context's Ask, whose Behind the answer shows the plan and the trace, and whose answer card shows every asker the SQL (#624); change 6 reaches Context's release, which then needs an engineer. Both are recorded in the pull request.

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
| `PORTAL_APP_CEREBRO` | `on`, `off` | `on`, as the other three apps (`portal/server/src/apps.js:37-41`, `:60`) | `portal/server/src/apps.js` (`FLAGS`); `kit/compose/.env.example`; `kit/INSTALL.md`'s settings table; `infra/modules/portal/main.tf`; `infra/pilot/tests/pilot.tftest.hcl`; `.env.example`; `README.md` |
| `PORTAL_CEREBRO_NAME` | The app's display name | `Cerebro` (owner, 2026-11-18; it was `Graph` before #539) | The same files, beside `PORTAL_CLIENT_NAME` |
| `PORTAL_CEREBRO_CALLS_BEFORE_ASK` | A whole number from 2 to 20: the limit on Cerebro's KGE calls before `ask`, for one question (§4.3, the one step before `ask`). The lowest value, 2, is the read and the call for a name's meanings, so an open meaning such as "retail NNA" always has options to put on the card. Any other value, 1 included, stops startup and names the setting | 6, a planning estimate until measured (#707 decision 9) | `portal/server/src/apps.js`; `kit/compose/.env.example` and `compose.yaml`; `kit/INSTALL.md`'s settings table; `.env.example`; `README.md`. No Terraform variable: on Google Cloud the default applies in this stage |

The single-app rule (`apps.js:62-70`) counts the new flag with the others: with the Knowledge home off, exactly one app is on. Discover, Context and Certify keep their flags and defaults.

### 9.3 The Knowledge home and the shell

The Knowledge home gets a Cerebro box beside Certify, Context and Discover (owner, 2026-11-15). Each box's text, who it is for and what you can do there, follows the role matrix (§2.1):

| App | Who it is for | What you can do there |
|---|---|---|
| Cerebro (by its name setting) | Business users, stewards, knowledge engineers | Chat with the agent; read the graph you may read; suggest and give feedback; stewards curate and review in Curate |
| Context | Knowledge engineers | Building, checks and release; no review, which is Curate's (§9.1, done) |
| Discover | Unchanged | Unchanged |
| Certify | Unchanged | Unchanged: external document certification. Curate's documents items link to it (`PORTAL_CERTIFY_URL`, `apps.js:43-55`) |

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
| The client's name | The neutral default `Client` (`PORTAL_CLIENT_NAME`, `apps.js:7`, `:71`) | `PORTAL_CLIENT_NAME` |
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
| B3 | KGE | The role matrix as KGE checks and the role-audit entry (§8.1–§8.3), with every behaviour change in §8.4: admin has no Ask; `retrieve` engineers only by default, with bounded passage mode available to querying roles, and `map_term` every role's, compact for non-engineers (owner, 2026-11-21, #622); plan and trace engineers only, SQL every asker's (#624); compact mode carries `graph_path`; acceptance by stewards; release and rollback by engineers. The role-checked suggestion endpoint into the steward queue is open in #503 | §8, D4 | B2 | `kge/services/http.py`, `kge/services/ask/routes.py`, `ask/mcp.py`, `ask/models.py`, `kge/services/search/`, `kge/services/assist/`, `kge/services/steward/routes.py`, `kge/services/onboard/routes.py`, `kge/pipeline/access.py`, and B2's routes | Demo code's checks, plus the full legs and the golden 20 at 20 of 20, since it changes the answer's fields; one test per blank cell of the matrix that it is a 403 and audited |
| B3b | KGE | The queue's new families and producers (§6.2): `filter_value`, `document_tier` and `business_term`; onboarding drafts' items into the queue; similar-name inference into `suggested_mention`; `/steward/decide` taking each. Suggestions and feedback into their families are open in #503 | §6 | B3 | `kge/services/steward/`, `kge/pipeline/`, `kge/services/onboard/` | Demo code's checks; a test per family that an item queues, decides and is journaled |
| B3c | KGE | Moved to A7 (#497), not delivered in this pull request: the reference agent onto MCP, as requirement 1 carries answer-modes §9.3 (§4.2 items 1–5): the context step on `get_context`, the treatment arms on `ask`, the control arm in process, the layer exception removed, the agent seal manifest | §4.1, §4.2, D5 | B3 | `kge/services/ask/engine/agent.py` and the files answer-modes §9.3 names | Demo code's checks, the full legs, the golden 20 at 20 of 20, and answer-modes §13's checks for A3; it is the MCP contract test B5's gate runs |
| B4 | Portal UI | The app, `PORTAL_APP_CEREBRO` and `PORTAL_CEREBRO_NAME` with its default (`Cerebro` since #539), in the server's and the shell's registries; `appForPath` for `/cerebro`; the Knowledge home's Cerebro box and every box's text (§9.3) | §9.2, §9.3 | — | `portal/server/src/apps.js`, `portal/ui/src/components/shell/apps.js`, `portal/ui/src/pages/knowledge/content.js`, `KnowledgeHome.js`, and the kit and infrastructure files §9.2 names | `make portal-tests`; Infra's and the kit's checks (Terraform `fmt`, `validate`, the mocked `terraform test`, `tests/unit/test_infra.py`, `tests/unit/test_kit.py`) |
| B5 | Portal server | Cerebro's agent: one streaming endpoint, the MCP tools as the signed-in person with the person's token in `x-kge-authorization` (`portal/server/src/kge.js`), only the tools §4.4 grants, answers delivered with their label and the one HITL card (#624); the catalogue agent replaced once the app is on, with Discover's Ask unchanged | §4 | B1, B2, B3, B3c | The portal's server: a new agent module and its route; `kge.js`; `knowledge.js` | `make portal-tests`, with Discover's existing tests unchanged; the reference agent's MCP contract tests (D5) |
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
