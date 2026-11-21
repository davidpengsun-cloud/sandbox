import React from "react";
import { Link } from "react-router-dom";
import DocsLayout, { Section, Table } from "./DocsLayout";
import useKnowledgeConfig from "./useKnowledgeConfig";
import { DEFAULT_CLIENT, DEFAULT_PRODUCT } from "./knowledgeConfig";
import { DOCS_START_HERE } from "./content";

// Docs, At a glance: the owner's checked text from the docs-at-a-glance drawing, with the client's name filled in.
const sections = (product) => [
  { id: "what-it-is", title: `What ${product} is` },
  { id: "why-it-matters", title: "Why it matters" },
  { id: "three-parts-one-flow", title: "Three parts, one flow" },
  { id: "how-work-flows", title: "How work flows" },
  { id: "what-makes-it-different", title: "What makes it different" },
  { id: "who-it-serves", title: "Who it serves" },
  { id: "where-it-runs", title: "Where it runs" },
];

const FLOW_LABEL = "Business process: documents are written by content creators and certified whole in Certify by content owners and the Certify app team; data is provided by data owners, and knowledge engineers draft business terms that business teams confirm; knowledge engineers bring both into one graph in Context; stewards publish a version; everyone uses it in Discover and developers connect apps and agents; new or changed content comes back in.";

function Lines({ x, y, lines, className }) {
  return lines.map((line, i) => <text key={line} x={x} y={y + i * 14} className={className}>{line}</text>);
}

function Box({ x, y, w, h, tone = "plain", title, body, people, bodyY, peopleY }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx="9" className={`kd-f-box kd-f-${tone}`} />
      <text x={x + 13.5} y={y + 22.5} className="kd-f-title">{title}</text>
      <Lines x={x + 13.5} y={bodyY ?? y + 41.5} lines={body} />
      <Lines x={x + 13.5} y={peopleY ?? y + 84.5} lines={people} className="kd-f-who" />
    </g>
  );
}

// The drawing's flow figure, its colours moved onto the portal's tokens so it reads in light and dark.
function FlowFigure() {
  return (
    <figure className="kd-figure">
      <div className="kd-figure-scroll" tabIndex={0} role="group" aria-label="How work flows, figure">
        <svg viewBox="0 0 1080 370" role="img" aria-label={FLOW_LABEL}>
          <defs><marker id="kd-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" className="kd-f-arrowhead" /></marker></defs>
          <rect x="0" y="20" width="1080" height="130" rx="10" className="kd-f-lane" />
          <rect x="0" y="175" width="1080" height="130" rx="10" className="kd-f-lane" />
          <text x="14" y="52" className="kd-f-title">Documents</text>
          <text x="14" y="68" className="kd-f-small">unstructured</text>
          <text x="14" y="207" className="kd-f-title">Data</text>
          <text x="14" y="223" className="kd-f-small">structured</text>
          <g className="kd-f-text">
            <Box x={110.5} y={35.5} w={165} h={100} title="1. Bring in" body={["Policies, standards,", "definitions, glossaries"]} people={["Content creators"]} />
            <Box x={300.5} y={35.5} w={190} h={100} tone="accent" title="2. Certify" body={["Each whole document,", "in Certify (Power App)"]} people={["Content owners,", "Certify app team"]} peopleY={113} />
            <Box x={110.5} y={190.5} w={165} h={100} title="1. Bring in" body={["Tables, dictionaries,", "models, metadata"]} people={["Data owners"]} />
            <Box x={300.5} y={190.5} w={190} h={100} tone="accent" title="2. Draft and confirm" body={["Business terms and the", "columns that hold them"]} people={["Knowledge engineers,", "business teams"]} peopleY={268} />
            <rect x="520.5" y="35.5" width="190" height="255" rx="10" className="kd-f-box kd-f-accent" />
            <text x="536" y="60" className="kd-f-title kd-f-big">3. Into the graph</text>
            <text x="536" y="77" className="kd-f-small">Context</text>
            <Lines x={536} y={106} lines={["Each passage linked to the", "business terms it mentions"]} />
            <Lines x={536} y={148} lines={["Each term linked to the", "columns that hold it"]} />
            <text x="536" y="190">One graph for both</text>
            <text x="536" y="275" className="kd-f-who">Knowledge engineers</text>
            <rect x="740.5" y="125.5" width="120" height="74" rx="9" className="kd-f-box kd-f-strong" />
            <text x="754" y="149" className="kd-f-title kd-f-big">4. Publish</text>
            <text x="754" y="166" className="kd-f-small">one live version</text>
            <text x="754" y="186" className="kd-f-who">Stewards</text>
            <Box x={890.5} y={35.5} w={189} h={100} tone="accent" title="5. Use in Discover" body={["Find, understand,", "request access"]} people={["Everyone"]} />
            <Box x={890.5} y={190.5} w={189} h={100} tone="quiet" title="5. Use in apps and agents" body={["Governed context through", "the API and MCP"]} people={["Developers,", "agent builders"]} peopleY={268} />
          </g>
          <g className="kd-f-arrow" markerEnd="url(#kd-ah)">
            <path d="M276 85H298" /><path d="M491 85H518" /><path d="M276 240H298" /><path d="M491 240H518" />
            <path d="M711 162H738" /><path d="M861 152H875V85H888" /><path d="M861 172H875V240H888" />
          </g>
          <path d="M985 291V332H193V293" className="kd-f-loop" markerEnd="url(#kd-ah)" />
          <text x="590" y="352" textAnchor="middle" className="kd-f-small">6. Keep current: new versions of documents go back through Certify; new or changed data is onboarded again</text>
        </svg>
      </div>
      <figcaption>Documents and data take different routes, with different people, and meet in one graph. Each person, and each agent acting for them, sees only the business areas their roles may read.</figcaption>
    </figure>
  );
}

export default function DocsAtAGlance() {
  const config = useKnowledgeConfig();
  const product = config?.productName || DEFAULT_PRODUCT;
  const client = config?.clientName || DEFAULT_CLIENT;
  const s = sections(product);
  return (
    <DocsLayout current="at-a-glance" sections={s}>
      <h1>{product} at a glance</h1>
      <p className="kd-lead">{product} is one governed place to find data, understand what it means, and get answers that name the passage and the data they rest on.</p>

      <Section section={s[0]} />
      <p>{product} is built on a knowledge graph, in three parts: Certify, Context and Discover. A <em>knowledge graph</em> is a connected record that ties each business term, such as <em>arrears balance</em>, to the document passages that discuss it and to the columns that hold its data.</p>
      <p>People use it through a portal. Apps and AI agents use it through an API and MCP, a standard way for software agents to call tools. What a person sees is set by their roles, and an app or agent acting for them sees no more.</p>

      <Section section={s[1]} />
      <p>Business teams at {client} ask questions like these:</p>
      <ul>
        <li>Where is the data I need, and who owns it?</li>
        <li>Which definition of this measure is the right one?</li>
        <li>Can I rely on this answer, and what does it rest on?</li>
        <li>How do I request access?</li>
      </ul>
      <p>The answers are kept in different places: policies and data dictionaries, warehouse tables, catalogue metadata, and working knowledge in emails and notes. An AI assistant that answers without naming its source does not close that gap, and in a regulated business such an answer is hard to use.</p>

      <Section section={s[2]} />
      <Table head={["Part", "What it does", "Who works in it"]} rows={[
        [<strong>Certify</strong>, "Certifies whole documents in a document-level certification loop, before they reach Context. Owned by the Certify app team, the platform and the content owners", "Content creators, data owners, stewards"],
        [<strong>Context</strong>, "Brings documents and data into the knowledge graph, links each passage to the business terms it mentions, and serves that governed context to apps and AI agents through an API and MCP", "Knowledge engineers, developers, agent builders, stewards"],
        [<strong>Discover</strong>, "The portal: find data, understand it, request access", `Everyone at ${client}, stewards`],
      ]} />
      <p>Certification belongs to Certify alone. Context handles graph ingestion for documents, not certification, and owns no part of it. Certify is part of the solution, but the demo and the pilot do not build it: in the pilot it runs in a Power App outside the platform.</p>

      <Section section={s[3]} />
      <p>Two kinds of content come in: documents (unstructured) and data (structured). They take different routes, with different people, and meet in the graph.</p>
      <FlowFigure />
      <Table head={["Step", "Documents", "Data"]} rows={[
        [<strong>1. Bring in</strong>, <><strong>Content creators</strong> write policies, standards, metric definitions and glossaries in their usual tools</>, <><strong>Data owners</strong> provide tables, schemas, data dictionaries and semantic models (dbt, LookML, OSI), with metadata from Collibra, Informatica and Confluence</>],
        [<strong>2. Prepare</strong>, <><strong>Content owners and the Certify app team</strong> certify each whole document in Certify; in the pilot, the Power App</>, <><strong>Knowledge engineers</strong> draft the business terms, their definitions and the columns that hold each one, in Context. <strong>Business teams</strong> confirm what each term means</>],
        [<strong>3. Into the graph</strong>, <><strong>Knowledge engineers</strong> bring the certified documents into the graph in Context, linking each passage to the business terms it mentions</>, "The confirmed terms and their links to columns join the same graph"],
        [<strong>4. Publish</strong>, <><strong>Stewards</strong> publish the new version of the graph. One version is live at a time</>, "Same version"],
        [<strong>5. Use</strong>, <><strong>Everyone</strong> finds data, sees what it means and requests access in Discover. <strong>Developers and agent builders</strong> connect apps and agents through the API and MCP</>, "Same, each within the access of the person using it"],
        [<strong>6. Keep current</strong>, "A new version of a document goes back through Certify", "New or changed data is onboarded again"],
      ]} />
      <p>Each part's own docs explain the steps inside it.</p>

      <Section section={s[4]} />
      <p>Documents are certified as a whole. Context then connects them, passage by passage, to business terms and to data. So an answer can name the passage and the data it rests on, not just the document it came from.</p>
      <ul>
        <li><strong>Two levels, two owners.</strong> Certify settles whether a whole document can be trusted as a source. Context settles what each passage is about and which data it relates to.</li>
        <li><strong>Meaning and data stay together.</strong> A passage reaches a column through the business term it mentions, so a definition and the data it governs are linked, not filed apart.</li>
        <li><strong>Gaps are named, not guessed.</strong> A refusal says why, and which role to ask. When something an answer needs is missing, the reply says what is missing and whom to ask.</li>
        <li><strong>People and agents follow the same rules.</strong> An agent acting for you carries your identity and sees what you may read, no more.</li>
        <li><strong>One live version.</strong> Discover and agents work from the same live version of the graph.</li>
      </ul>

      <Section section={s[5]} />
      <p>The business teams that use the result are Retail, SME and corporate, Wealth, Finance, Risk and Compliance.</p>
      <Table head={["If you are", "It gives you", "Part"]} rows={[
        ["A business user or analyst", "Data products in your areas, with owners, lineage and previews, and answers that name their sources", "Discover"],
        ["A content creator or data owner", "One loop in which each document is certified as a whole", "Certify"],
        ["A knowledge engineer", "The place to bring a business area's documents and data into the graph", "Context"],
        ["A developer or agent builder", "Governed context for apps and agents, through an API and MCP", "Context"],
        ["A steward", "A role in each part, named on Discover's product pages", "All three"],
      ]} />
      <p><Link to={DOCS_START_HERE}>Start here</Link> shows which door to open for what you want to do.</p>

      <Section section={s[6]} />
      <p>In the pilot, Context and Discover run on Google Cloud, using Gemini on Vertex AI and BigQuery. Certify runs in its Power App outside the platform. The demo runs on a laptop, on invented data. These product names say what the solution uses; they do not imply a partnership.</p>
    </DocsLayout>
  );
}
