import React from "react";
import { Panel } from "../../components/shared/Controls";

export default function Docs() {
  return <><h1>Docs</h1><p className="intro">Your role guides and connection reference.</p>
    <Panel><a href="/api/context/" target="_blank" rel="noopener">Developer page</a>
      <iframe id="role-docs" title="Your role guides" src="/api/context/onboarding-guide" /></Panel></>;
}
