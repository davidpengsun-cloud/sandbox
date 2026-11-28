import React from "react";
import { Link } from "react-router-dom";
import { useConfig } from "../../services/config";
import { appById } from "./apps";
import "./GlobalBar.css";

/* The two views every door, the switcher and every app route share: one for a person without
   permission for an app, one for an app this install has switched off. */

function HomeLink() {
  const config = useConfig();
  if (!config?.knowledgeHome) return null;
  return (
    <p>
      <Link to="/">Back to {config.productName}</Link>
    </p>
  );
}

export function NoPermission({ app }) {
  const { label } = appById(app, useConfig());
  return (
    <main className="gb-status" aria-labelledby="gb-status-title">
      <h1 id="gb-status-title">You don't have access to {label}</h1>
      <p>Your account has no role in {label} yet. What you may see and do here is set by your access in the platform.</p>
      <p>
        To request access, ask your Access admin for a role in {label}. Once it is granted, open {label} again.
      </p>
      <HomeLink />
    </main>
  );
}

export function NotSetUp({ app }) {
  const { label } = appById(app, useConfig());
  return (
    <main className="gb-status" aria-labelledby="gb-status-title">
      <h1 id="gb-status-title">{label} is not set up here</h1>
      <p>This install does not include {label}. If you need it, ask the team that runs the platform for you.</p>
      <HomeLink />
    </main>
  );
}

export function SettingsUnavailable({ retry }) {
  return <main className="gb-status"><h1>Settings unavailable</h1><p>Try loading the settings again.</p><button type="button" onClick={retry}>Retry</button></main>;
}
