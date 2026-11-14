import usePageTitle from "../../shell/usePageTitle";
import { KnowledgeNotice } from "../../KnowledgeStatus";
import React, { useState } from "react";
import { useSelector } from "react-redux";
import { Link, useLocation } from "react-router-dom";
import PortalSearch from "../PortalSearch";
import Chatbot from "../../askportal/Chatbot";
import GlobalBar from "../../shell/GlobalBar";
import { useShortName } from "../../../services/config";
import "../../../assets/styles/v3-topbar.css";
import "../../shell/GlobalBar.css";

/* Discover contributes its navigation, search and Ask action to the shared bar. A scoped
   page owns its chatbot; other pages open the panel over all readable data assets. */

export default function Header(props) {
  usePageTitle();
  const { pathname } = useLocation();
  const user = useSelector((state) => state.user.data) || {};
  const roles = user.userRoles || [user.userRole || "member"];
  const canAdmin = roles.some((role) => ["admin", "publisher", "approver"].includes(role));
  const isHome = props.type === "home";
  const [ownAsk, setOwnAsk] = useState({ mounted: false, open: false });
  const toggleAsk = props.toggleChatbot || (() => setOwnAsk((ask) => ({ mounted: true, open: !ask.open })));
  const askOpen = props.toggleChatbot ? props.askOpen : ownAsk.open;
  const ask = `Ask ${useShortName()}`;

  return (
    <>
    <GlobalBar
      app="discover"
      navigation={canAdmin && <Link className="gb-applink gb-admin" to="/adminaccess" {...(pathname.toLowerCase() === "/adminaccess" ? { "aria-current": "page" } : {})}>Admin</Link>}
      search={!isHome && <PortalSearch />}
      actions={
        <button
          className="v3-btn-ai"
          onClick={toggleAsk}
          aria-label={props.askScope ? `${ask} about ${props.askScope}` : ask}
          title={props.askScope ? `${ask} about ${props.askScope}` : ask}
          aria-expanded={askOpen === undefined ? undefined : Boolean(askOpen)}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 2l1.9 5.6L19.5 9l-5.6 1.9L12 16.5l-1.9-5.6L4.5 9l5.6-1.4L12 2z" />
          </svg>
          {ask}
        </button>
      }
    />
    <KnowledgeNotice />
    {!props.toggleChatbot && ownAsk.mounted && (
      <Chatbot
        domainMode
        scopeLabel="All data assets"
        showChatbot={ownAsk.open}
        setShowChatbot={(open) => setOwnAsk((ask) => ({ ...ask, open: Boolean(open) }))}
      />
    )}
    </>
  );
}
