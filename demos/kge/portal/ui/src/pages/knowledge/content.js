// What the Knowledge home says. The wording starts from the landing-page drawing and follows the issue's decisions
// where they differ: Certify is document-level certification and sign-off only, Context starts with drafting and
// onboarding, Discover's people request access, and the technologies carry no caption (their order is the signal).

export const DISCOVER_PATH = "/home";
export const CONTEXT_PATH = "/context";
export const CERTIFY_PLACEHOLDER = "[Power App link]";
export const DOCS_AT_A_GLANCE = "/knowledge/docs/at-a-glance";
export const DOCS_START_HERE = "/knowledge/docs/start-here";

export const BROUGHT_IN = [
  { group: "Structured data", shape: "square", items: [
    { icon: "table", label: "Tables (DDL, CSV)" },
    { icon: "cube", label: "dbt, LookML, OSI" },
    { icon: "book", label: "Data dictionaries" },
  ] },
  { group: "Documents", shape: "circle", items: [
    { icon: "chart", label: "Metrics and KPIs" },
    { icon: "policy", label: "Policies, standards" },
    { icon: "tag", label: "Glossary" },
    { icon: "mail", label: "Notes and emails" },
  ] },
];

export const USED_BY = [
  { icon: "store", label: "Retail" },
  { icon: "building", label: "SME and corporate" },
  { icon: "trend", label: "Wealth" },
  { icon: "calc", label: "Finance" },
  { icon: "risk", label: "Risk" },
  { icon: "scale", label: "Compliance" },
];

export const STATIONS = [
  {
    key: "certify",
    name: "Certify",
    icon: "certify",
    line: "Certify each whole document, at document level, before it reaches Context.",
    roles: ["Content creators", "Data owners", "Stewards"],
    actions: ["Certify a whole document", "Sign off its certification"],
  },
  {
    key: "context",
    name: "Context",
    icon: "graph",
    line: "Draft from what you provide, onboard documents and data into the graph, and serve that context to people, apps and agents, each within their own access.",
    roles: ["Knowledge engineers", "Developers", "Agent builders", "Stewards"],
    actions: ["Draft business terms from your sources", "Onboard documents and data", "Ask, with cited answers", "Connect apps and agents through MCP"],
  },
  {
    key: "discover",
    name: "Discover",
    icon: "search",
    line: "Find the data you need, understand it, and request access.",
    roles: ["Everyone at {client}", "Stewards"],
    actions: ["Search data products", "See owners, quality, lineage", "Preview, export, query", "Request access"],
  },
];

// In use first, then the rest; the order is the only signal. A logo of null means the drawing has no logo for it.
export const TECHNOLOGIES = [
  { name: "Anthropic", logo: "anthropic" },
  { name: "BigQuery", logo: "googlebigquery" },
  { name: "BigQuery Graph", logo: "bqgraph" },
  { name: "Collibra", logo: null },
  { name: "Confluence", logo: "confluence" },
  { name: "dbt", logo: "dbt" },
  { name: "Gemini", logo: "googlegemini" },
  { name: "GitHub Actions", logo: "githubactions" },
  { name: "GitHub Copilot", logo: "githubcopilot" },
  { name: "Google Cloud", logo: "googlecloud" },
  { name: "Informatica", logo: null },
  { name: "MCP", logo: "modelcontextprotocol" },
  { name: "Microsoft Azure", logo: "azure" },
  { name: "Microsoft Entra ID", logo: "microsoft" },
  { name: "Microsoft Fabric", logo: "microsoft" },
  { name: "Microsoft Purview", logo: "microsoft" },
  { name: "OpenAI", logo: "openai" },
  { name: "Power Platform", logo: "microsoft" },
  { name: "SharePoint", logo: "sharepoint" },
  { name: "Terraform", logo: "terraform" },
  { name: "Databricks", logo: "databricks" },
  { name: "Power BI", logo: "powerbi" },
  { name: "Snowflake", logo: "snowflake" },
  { name: "Tableau", logo: "tableau" },
];
