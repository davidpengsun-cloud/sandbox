import usePageTitle from "./usePageTitle";
import React from "react";
import GlobalBar from "./GlobalBar";
import { KnowledgeNotice } from "../KnowledgeStatus";
import "./GlobalBar.css";

/* A page of the portal that is not one of Discover's: the global bar, then the page filling the
   rest of the window. The page brings its own <main>. `fill` keeps the page to the window's height, for an app in a frame. */
export default function ShellPage({ app, title, fill = false, contextPerson, children }) {
  usePageTitle(title);
  return (
    <div className={fill ? "gb-page gb-fill" : "gb-page"}>
      <GlobalBar app={app} contextPerson={contextPerson} />
      <KnowledgeNotice />
      <div className="gb-body">{children}</div>
    </div>
  );
}
