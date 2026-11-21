import React from "react";
import { useSelector } from "react-redux";
import { Navigate } from "react-router-dom";
import Loading from "../Loading";
import ShellPage from "./ShellPage";
import { NoPermission, NotSetUp, SettingsUnavailable } from "./StatusViews";
import { canUse, isSetUp, singleAppPath } from "./apps";
import { useConfig } from "../../services/config";
import { KnowledgeHome } from "../knowledgePages";

/* An app's routes open only where the install has the app switched on and the person may use
   it; otherwise the shared not-set-up or no-permission view shows under the global bar. */
export default function AppGate({ app, children }) {
  const config = useConfig();
  const user = useSelector((state) => state.user.data);
  if (!config) return <Loading />;
  if (config.unavailable) return <SettingsUnavailable retry={config.retry} />;
  if (!isSetUp(config, app)) return <ShellPage app={app} title="Not set up"><NotSetUp app={app} /></ShellPage>;
  if (!canUse(user, app)) return <ShellPage app={app} title="No permission"><NoPermission app={app} /></ShellPage>;
  return children;
}

/* The Knowledge home's Docs pages: served only while its flag is on, in the page's <main> (the
   home brings its own).
   With it off, the portal's root sends the person straight to the single app. */
export function KnowledgeGate({ children }) {
  const config = useConfig();
  if (!config) return <Loading />;
  if (config.unavailable) return <SettingsUnavailable retry={config.retry} />;
  if (!config.knowledgeHome) return <Navigate to="/" replace />;
  return <ShellPage app="knowledge"><main>{children}</main></ShellPage>;
}

export function KnowledgeRoot() {
  const config = useConfig();
  if (!config) return <Loading />;
  if (config.unavailable) return <SettingsUnavailable retry={config.retry} />;
  if (config.knowledgeHome) return <ShellPage app="knowledge"><KnowledgeHome /></ShellPage>;
  const path = singleAppPath(config);
  if (path) return <Navigate to={path} replace />;
  return <ShellPage app="knowledge" title="Not set up"><NotSetUp app="knowledge" /></ShellPage>;
}
