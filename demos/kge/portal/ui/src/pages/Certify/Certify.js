import React from "react";
import ShellPage from "../../components/shell/ShellPage";
import { CERTIFY_PLACEHOLDER } from "../../components/shell/apps";
import { useConfig } from "../../services/config";

/* Certify's place among the apps. Certification runs outside the platform (in the pilot, a
   Power App); this page says what it is for and links out to it, from PORTAL_CERTIFY_URL. */
export default function Certify() {
  const config = useConfig();
  return (
    <ShellPage app="certify">
      <main className="gb-status" aria-labelledby="certify-title">
        <p className="gb-eyebrow">Certify</p>
        <h1 id="certify-title">Certify: document-level certification</h1>
        <p>Certify is where documents are certified, at document level, before their content reaches Context. Trust starts here.</p>
        <p><b>Who it is for:</b> content creators, data owners and stewards.</p>
        <p><b>What you can do there:</b></p>
        <ul>
          <li>Certify documents</li>
          <li>Sign off certifications</li>
        </ul>
        <p>Certify runs in its own app, outside the platform.</p>
        <p>
          {config?.certifyUrl ? (
            <a href={config.certifyUrl} target="_blank" rel="noopener noreferrer">
              Open Certify<span className="gb-sr"> (opens outside the platform)</span>
              <span aria-hidden="true"> ↗</span>
            </a>
          ) : (
            <span className="gb-placeholder-text">{CERTIFY_PLACEHOLDER}</span>
          )}
        </p>
      </main>
    </ShellPage>
  );
}
