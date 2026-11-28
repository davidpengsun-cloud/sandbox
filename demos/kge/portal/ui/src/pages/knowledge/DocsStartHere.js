import React from "react";
import DocsLayout, { Section, Table } from "./DocsLayout";
import useKnowledgeConfig from "./useKnowledgeConfig";
import { DEFAULT_CLIENT, DEFAULT_PRODUCT } from "./knowledgeConfig";

// Docs, Start here: the owner's checked text from the docs-start-here drawing, with the client's name filled in.
const SECTIONS = [
  { id: "which-door-to-open", title: "Which door to open" },
  { id: "what-you-will-and-will-not-see", title: "What you will and will not see, and why" },
  { id: "asking-for-access-or-help", title: "Asking for access or help" },
  { id: "terms-you-will-meet", title: "Terms you will meet" },
  { id: "what-0-9-x-does-not-do", title: "What 0.9.x does not do" },
];

export default function DocsStartHere() {
  const config = useKnowledgeConfig();
  const product = config?.productName || DEFAULT_PRODUCT;
  const client = config?.clientName || DEFAULT_CLIENT;
  const [door, see, help, terms, notDo] = SECTIONS;
  return (
    <DocsLayout current="start-here" sections={SECTIONS}>
      <h1>Start here</h1>
      <p className="kd-lead">Which part of {product} to open for what you want to do, what you will and will not see and why, how to ask for access or help, the terms you will meet, and what version 0.9.x does not do.</p>

      <Section section={door} />
      <Table head={["If you want to", "Open", "Usually"]} rows={[
        ["Find a data product, see who owns it and where it comes from", "Discover", "Everyone"],
        ["Understand what a business term means and which data holds it", "Discover", "Everyone"],
        ["Ask a question and see the passage and data the answer rests on", "Discover", "Everyone"],
        ["Request access to a data product", "Discover", "Everyone"],
        ["Have a document certified, or learn how it was certified", "Certify: in the pilot, its Power App outside the platform", "Content creators, data owners, stewards"],
        ["Bring a business area's documents and data into the knowledge graph, or keep its business terms in order", "Context", "Knowledge engineers, stewards"],
        ["Connect an app or AI agent to governed context", "Context, through its API or MCP", "Developers, agent builders"],
      ]} />
      <p>Not sure? Start with Discover. Each part has its own docs that explain how it works.</p>

      <Section section={see} />
      <p>Read rights are set per <em>business area</em>, such as Retail or Wealth. A <em>role</em> names the areas it may read, and an access admin assigns roles.</p>
      <Table head={["You will see", "You will not see"]} rows={[
        ["Data products in the areas your roles read, with owners, lineage, preview and export", "Products in other areas, or lineage that leads to them"],
        ["The first rows of a table", "Restricted columns, or rows outside the area's row rules"],
        ["The live version of the graph", "Work in progress"],
        ["Answers that cite published documents", "Emails, notes and drafts as cited sources; they may appear as context only"],
        ["A plain reason when a question cannot be answered", "Partial answers: a question that needs several areas runs only if you can read all of them"],
      ]} />
      <p>An app or agent acting for you carries your identity and sees what you may read, no more. Restricted personal data is never given to the assistant. Portal roles decide what you can manage in the catalogue, never what data you can read.</p>

      <Section section={help} />
      <Table head={["You need", "Do this"]} rows={[
        ["Access to data", "Request it from the product's page in Discover. Your request and its decision are recorded. What you can read changes only when an access admin changes your roles"],
        ["To know more about a data product", "Its page names the data domain owner and the data steward"],
        ["Help with a document's certification", "Ask its content owner or the Certify app team"],
        ["To know why a question was not answered", "The reply says why, and whom to ask"],
        ["If the engine is unavailable", "Discover says so and shows its own copy of the catalogue; answers, figures and previews wait"],
      ]} />

      <Section section={terms} />
      <Table head={["Term", "Meaning"]} rows={[
        ["Knowledge graph", "The connected record of business terms, the document passages that discuss them, and the columns that hold their data"],
        ["Business term", <>A defined term {client} uses, such as <em>arrears balance</em></>],
        ["Passage", "A piece of a document, a paragraph or two long"],
        ["Certification", "Certify's result for a whole document, set before it reaches Context"],
        ["Business area", "A part of the business, such as Retail; read rights are set per area"],
        ["Data product", "A catalogued table in Discover"],
        ["Release", "The one live version of the graph, which Discover and agents work from"],
        ["Steward", "Takes part in Certify's certification loop, looks after business terms in Context, and is named on product pages in Discover"],
        ["MCP", "A standard way for software agents to call tools"],
        ["Medals", "Gold, Silver and Bronze in Discover are catalogue labels set by publishers, not Certify's results"],
      ]} />

      <Section section={notDo} />
      <ul>
        <li><strong>The demo and the pilot do not build Certify.</strong> In the pilot it runs in a Power App outside the platform; the demo uses a stand-in on invented documents.</li>
        <li><strong>Metadata does not arrive live.</strong> Metadata from Collibra, Informatica and Confluence comes in as uploaded files, not through a live connection.</li>
        <li><strong>Discover does not grant access.</strong> It records requests and decisions; read rights change only through roles.</li>
        <li><strong>Discover's answers do not read Certify's result.</strong> They cite by kind of document: published data dictionaries, metric definitions, policies and standards may be cited; emails, notes and drafts may not.</li>
        <li><strong>The quality figure is coverage, not a data-quality test.</strong> Unless the catalogue supplies a score, it is the share of a table's columns mapped to a business term in the live version of the graph.</li>
      </ul>
    </DocsLayout>
  );
}
