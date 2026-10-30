import { KnowledgeNotice, clearKnowledge } from "../../KnowledgeStatus";
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSelector } from "react-redux";
import { BrandLogo, useBrand } from "../Brand/Brand";
import PortalSearch from "../PortalSearch";
import Chatbot from "../../askportal/Chatbot";
import "../../../assets/styles/v3-topbar.css";

/* v3 "settlement tape" global top bar with typeahead search and /search + redux flow.
   The "Ask" button is on every page that has this bar. A page with a scope to ask
   about passes toggleChatbot (and askScope for the label) and shows its own panel;
   on any other page the bar opens a panel over all the data assets the user can read.
   The product switcher's "Ask" is a link to KGE, shown when the portal names one. */

const THEMES = ["system", "light", "dark"];
const THEME_LABEL = { system: "System", light: "Light", dark: "Dark" };

function storedTheme() {
  try {
    const value = window.localStorage.getItem("v3-theme");
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function applyTheme(theme) {
  try {
    if (theme === "system") window.localStorage.removeItem("v3-theme");
    else window.localStorage.setItem("v3-theme", theme);
  } catch {
    /* the choice holds for this page only */
  }
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}

const initials = (name) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((word) => word.charAt(0).toUpperCase()).join("") || "?";

function ThemeIcon({ theme }) {
  if (theme === "light")
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  if (theme === "dark")
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    );
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" />
    </svg>
  );
}

export default function Header(props) {
  const navigate = useNavigate();
  const userData = useSelector((state) => state.user.data) || {};
  const isHome = props.type === "home";
  const brand = useBrand();
  const [theme, setTheme] = useState(storedTheme);
  const [menuOpen, setMenuOpen] = useState(false);
  const [ownAsk, setOwnAsk] = useState({ mounted: false, open: false });
  const toggleAsk = props.toggleChatbot || (() => setOwnAsk((ask) => ({ mounted: true, open: !ask.open })));
  const askOpen = props.toggleChatbot ? props.askOpen : ownAsk.open;
  const menuRef = useRef(null);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);

  const displayName = userData.userName || "Guest user";
  const signedIn = Boolean(userData.userName);
  const role = (userData.userRole || "Member").toString().toUpperCase();
  const canAdmin = ["ADMIN", "PUBLISHER", "APPROVER"].includes(role);
  const signOut = async () => {
    clearKnowledge();
    await fetch("/session", { method: "DELETE", credentials: "include" });
    navigate("/");
  };

  /* identity menu: Escape closes and returns focus to the chip; a click outside closes */
  useEffect(() => {
    if (!menuOpen) return undefined;
    panelRef.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [menuOpen]);

  const cycleTheme = () => {
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    applyTheme(next);
    setTheme(next);
  };
  const themeLabel = `Theme: ${THEME_LABEL[theme]}`;

  return (
    <>
    <div className="v3-shell">
    <header className="v3-topbar">
      <button className="v3-brand" onClick={() => navigate("/home")} aria-label="Go to portal home">
        <BrandLogo brand={brand} />
        {!isHome && <span className="v3-brand-name">{brand.name}</span>}
      </button>

      <nav className="v3-switcher" aria-label="Products">
        {brand.kge_url && <a href={brand.kge_url}>Ask</a>}
        <a
          href="/home"
          aria-current="page"
          onClick={(e) => {
            e.preventDefault();
            navigate("/home");
          }}
        >
          Discover
        </a>
      </nav>
      {!isHome && <PortalSearch />}

      <div className="v3-top-actions">
        <button
          className="v3-btn-ai"
          onClick={toggleAsk}
          aria-label={props.askScope ? `Ask about ${props.askScope}` : "Ask"}
          title={props.askScope ? `Ask about ${props.askScope}` : "Ask"}
          aria-expanded={askOpen === undefined ? undefined : Boolean(askOpen)}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M12 2l1.9 5.6L19.5 9l-5.6 1.9L12 16.5l-1.9-5.6L4.5 9l5.6-1.4L12 2z" />
          </svg>
          Ask
        </button>
        <button className="v3-theme" onClick={cycleTheme} aria-label={themeLabel} title={themeLabel}>
          <ThemeIcon theme={theme} />
        </button>
        <div className="v3-identity" ref={menuRef}>
          <button
            ref={triggerRef}
            className="v3-id-trigger"
            onClick={() => setMenuOpen((value) => !value)}
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            aria-label={`Account: ${displayName}`}
          >
            <span className="v3-id-chip" aria-hidden="true">
              {initials(displayName)}
              {signedIn && <span className="v3-id-dot" />}
            </span>
            <span className="v3-id-name" aria-hidden="true">{displayName}</span>
          </button>
          {menuOpen && (
            <div className="v3-id-panel" role="dialog" aria-label="Account" ref={panelRef} tabIndex={-1}>
              <div className="v3-who">
                <b>{displayName}</b>
                <span>{role}</span>
              </div>
              {canAdmin && <button className="v3-id-item" onClick={() => navigate("/adminaccess")}>Admin</button>}
              <button className="v3-id-item" onClick={signOut}>Sign out</button>
            </div>
          )}
        </div>
      </div>
    </header>
    <KnowledgeNotice />
    </div>
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
