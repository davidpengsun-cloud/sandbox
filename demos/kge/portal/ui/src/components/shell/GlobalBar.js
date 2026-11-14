import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button } from "../shared/Controls";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useSelector } from "react-redux";
import { BrandLogo, useBrand } from "../shared/Brand/Brand";
import { clearKnowledge } from "../KnowledgeStatus";
import { shortNameOf, useConfig } from "../../services/config";
import { clearAskHistory } from "../../services/askHistory";
import useDisclosure, { usePhone } from "./useDisclosure";
import { APPS, CERTIFY_PLACEHOLDER, CONTEXT_GUIDE, KNOWLEDGE, appById, appForPath, isSetUp, knowledgeFor } from "./apps";
import "../../assets/styles/v3-topbar.css";
import "./GlobalBar.css";

/* Every app shares one bar. Discover supplies its Admin link, search and Ask action;
   the switcher and account keep their shared disclosure and keyboard behaviour. */

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

const Caret = () => (
  <svg className="gb-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
    <path d="M6 9l6 6 6-6" />
  </svg>
);

const OutIcon = () => (
  <svg className="gb-out" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
    <path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" />
  </svg>
);

const Check = () => (
  <svg className="gb-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
    <path d="M5 12l5 5 9-10" />
  </svg>
);

/* One row of the switcher: a link, the Certify link out (or its placeholder), or a disabled row
   for an app this install has switched off. */
function AppItem({ app, config, current, onPick }) {
  const here = current === app.id;
  const mark = here ? { "aria-current": "page" } : {};
  const tick = <span className="gb-tick">{here ? <Check /> : null}</span>;
  if (app.id !== KNOWLEDGE.id && !isSetUp(config, app.id)) {
    return (
      <li>
        <span className="gb-item gb-off" aria-disabled="true" {...mark}>
          {tick}
          <span className="gb-label">{app.label}</span>
          <span className="gb-note">not set up here</span>
        </span>
      </li>
    );
  }
  if (app.external) {
    if (!config.certifyUrl) {
      return (
        <li>
          <span className="gb-item gb-placeholder" {...mark}>
            {tick}
            <span className="gb-label">{app.label}</span>
            <span className="gb-note">{CERTIFY_PLACEHOLDER}</span>
          </span>
        </li>
      );
    }
    return (
      <li>
        <a className="gb-item" href={config.certifyUrl} target="_blank" rel="noopener noreferrer" onClick={onPick} {...mark}>
          {tick}
          <span className="gb-label">{app.label}</span>
          <OutIcon />
          <span className="gb-sr">(opens outside the platform)</span>
        </a>
      </li>
    );
  }
  return (
    <li>
      <Link className="gb-item" to={app.path} onClick={onPick} {...mark}>
        {tick}
        <span className="gb-label">{app.label}</span>
      </Link>
    </li>
  );
}

function AppList({ config, current, onPick }) {
  return (
    <ul className="gb-list">
      {[knowledgeFor(config), ...APPS].map((app) => (
        <AppItem key={app.id} app={app} config={config} current={current} onPick={onPick} />
      ))}
    </ul>
  );
}

function DocsList({ groups, onPick }) {
  return groups.map((group) => (
    <div className="gb-group" key={group.title}>
      <p className="gb-group-title">{group.title}</p>
      <ul className="gb-list">
        {group.items.map((item) => (
          <li key={item.label}>
            {item.to ? (
              <Link className="gb-item" to={item.to} onClick={onPick}>
                <span className="gb-label">{item.label}</span>
              </Link>
            ) : (
              <a className="gb-item" href={item.href} target="_blank" rel="noopener" onClick={onPick}>
                <span className="gb-label">{item.label}</span>
                <OutIcon />
                <span className="gb-sr">(opens in a new tab)</span>
              </a>
            )}
          </li>
        ))}
      </ul>
    </div>
  ));
}

function Popover({ id, label, buttonLabel, buttonText, disclosure, children, className = "" }) {
  return (
    <div className={`gb-pop ${className}`} ref={disclosure.wrapRef}>
      <Button
        ref={disclosure.triggerRef}
        type="button"
        className="gb-trigger"
        aria-expanded={disclosure.open}
        aria-controls={id}
        aria-label={buttonLabel}
        onClick={disclosure.toggle}
      >
        {buttonText}
        <Caret />
      </Button>
      {disclosure.open && (
        <nav id={id} className="gb-panel" aria-label={label} ref={disclosure.panelRef} tabIndex={-1}>
          {children}
        </nav>
      )}
    </div>
  );
}

function Account({ contextPerson }) {
  const navigate = useNavigate();
  const userData = useSelector((state) => state.user.data) || {};
  const menu = useDisclosure();
  const displayName = userData.userName || "Guest user";
  const signedIn = Boolean(userData.userName);
  const role = (userData.userRoleLabel || userData.userRole || "Member").toString().toUpperCase();
  const signOut = async () => {
    clearKnowledge();
    await fetch("/session", { method: "DELETE", credentials: "include" });
    clearAskHistory();
    navigate("/");
  };
  return (
    <div className="v3-identity" ref={menu.wrapRef}>
      <Button
        ref={menu.triggerRef}
        className="v3-id-trigger"
        onClick={menu.toggle}
        aria-haspopup="dialog"
        aria-expanded={menu.open}
        aria-label={`Account: ${displayName}`}
      >
        <span className="v3-id-chip" aria-hidden="true">
          {initials(displayName)}
          {signedIn && <span className="v3-id-dot" />}
        </span>
        <span className="v3-id-name" aria-hidden="true">{displayName}</span>
      </Button>
      {menu.open && (
        <div className="v3-id-panel" role="dialog" aria-label="Account" ref={menu.panelRef} tabIndex={-1}>
          <div className="v3-who">
            {signedIn && <span className="gb-who-label">Signed in as</span>}
            <b>{displayName}</b>
            <span>{role}</span>
          </div>
          {contextPerson && <dl className="gb-context-rights">
            <dt>Context role</dt><dd>{contextPerson.role || "none"}</dd>
            <dt>Reads</dt><dd>{(contextPerson.reads || []).join(", ") || "none"}</dd>
            <dt>May</dt><dd>{[
              ...(contextPerson.processes || []).map((process) => ({ querying: "ask questions", certification: "certify and review", tagging: "tag documents" }[process] || process)),
              ...(contextPerson.writes_terms ? ["write terms and mappings"] : []),
            ].join(", ") || "nothing"}</dd>
          </dl>}
          <Button className="v3-id-item" onClick={signOut}>Sign out</Button>
        </div>
      )}
    </div>
  );
}

export default function GlobalBar({ app, contextPerson, navigation, search, actions }) {
  const { pathname } = useLocation();
  const config = useConfig();
  const brand = useBrand();
  const viewportPhone = usePhone();
  const shellRef = useRef(null);
  const [narrow, setNarrow] = useState(null);
  const phone = narrow ?? viewportPhone;
  const switcher = useDisclosure();
  const phoneMenu = useDisclosure();
  const phoneRef = useRef(phone);
  const restoreMenuFocus = useRef(false);
  phoneRef.current = phone;
  useEffect(() => {
    if (typeof ResizeObserver === "undefined" || !shellRef.current) return undefined;
    const observer = new ResizeObserver(([entry]) => {
      const nextPhone = entry.contentRect.width <= 640;
      if (nextPhone !== phoneRef.current) {
        const outgoing = phoneRef.current ? phoneMenu.wrapRef.current : switcher.wrapRef.current;
        restoreMenuFocus.current = Boolean(outgoing?.contains(document.activeElement));
      }
      setNarrow(nextPhone);
    });
    observer.observe(shellRef.current);
    return () => observer.disconnect();
  }, [phoneMenu.wrapRef, switcher.wrapRef]);
  useLayoutEffect(() => {
    if (restoreMenuFocus.current) {
      (phone ? phoneMenu.triggerRef : switcher.triggerRef).current?.focus();
      restoreMenuFocus.current = false;
    }
  }, [phone, phoneMenu.triggerRef, switcher.triggerRef]);
  useLayoutEffect(() => {
    switcher.close();
    phoneMenu.close();
  }, [phone, switcher.close, phoneMenu.close]);
  const [theme, setTheme] = useState(storedTheme);
  const current = app || appForPath(pathname);
  const currentApp = appById(current, config) || knowledgeFor(config);
  const currentLabel = current === "discover" ? shortNameOf(config) : currentApp.label;
  const knowledge = Boolean(config?.knowledgeHome);

  const cycleTheme = () => {
    const next = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    applyTheme(next);
    setTheme(next);
  };
  const themeLabel = `Theme: ${THEME_LABEL[theme]}`;

  return (
    <div className="v3-shell" ref={shellRef}>
      <header className="v3-topbar gb-bar">
        {config && (
          <Link className="v3-brand gb-home" to="/" aria-label={config.productName}>
            <BrandLogo brand={brand} />
            {(!brand.logo || config.logoTitle) && <span className="v3-brand-name gb-name">{config.productName}</span>}
          </Link>
        )}
        {!config && (
          <span className="v3-brand gb-home">
            <BrandLogo brand={brand} />
          </span>
        )}
        {(config || current === "discover" || navigation) && (
          <nav className="gb-appnav" aria-label={currentApp.label}>
            {config && (
              <Link className="gb-applink" to={currentApp.path} {...(pathname.toLowerCase() === currentApp.path ? { "aria-current": "page" } : {})}>
                {currentLabel}
              </Link>
            )}
            {current === "context" && isSetUp(config, "context") && (
              <a className="gb-applink" href={CONTEXT_GUIDE} target="_blank" rel="noopener" aria-label="Context docs (opens in a new tab)">
                Docs<span className="gb-sr"> (opens in a new tab)</span>
              </a>
            )}
            {navigation}
          </nav>
        )}
        {search}

        <div className="v3-top-actions">
          {actions}
          {config && !phone && knowledge && (
            <Popover
              id="gb-apps"
              label="Apps"
              buttonLabel={`Switch app, current: ${currentLabel}`}
              buttonText={currentLabel}
              disclosure={switcher}
              className="gb-switcher"
            >
              <AppList config={config} current={current} onPick={switcher.close} />
            </Popover>
          )}
          {config && phone && knowledge && (
            <Popover id="gb-menu" label="Menu" buttonText="Menu" disclosure={phoneMenu} className="gb-phone">
              {knowledge && (
                <div className="gb-group">
                  <p className="gb-group-title">Apps</p>
                  <AppList config={config} current={current} onPick={phoneMenu.close} />
                </div>
              )}
            </Popover>
          )}

          <Button className="v3-theme" onClick={cycleTheme} aria-label={themeLabel} title={themeLabel}>
            <ThemeIcon theme={theme} />
          </Button>
          <Account contextPerson={contextPerson} />
        </div>
      </header>
    </div>
  );
}
