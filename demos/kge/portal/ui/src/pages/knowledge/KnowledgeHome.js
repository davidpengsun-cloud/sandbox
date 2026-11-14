import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import useKnowledgeConfig from "./useKnowledgeConfig";
import { DEFAULT_CLIENT, DEFAULT_PRODUCT } from "./knowledgeConfig";
import { Icon, Logo, Symbols } from "./Symbols";
import { BROUGHT_IN, CERTIFY_PLACEHOLDER, CONTEXT_PATH, DISCOVER_PATH, STATIONS, TECHNOLOGIES, USED_BY } from "./content";
import "./knowledge.css";

const REDUCED = "(prefers-reduced-motion: reduce)";

// Whether the person asks for reduced motion, kept current as the setting changes.
function usePrefersReducedMotion() {
  const query = () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(REDUCED) : null);
  const [reduced, setReduced] = useState(() => Boolean(query()?.matches));
  useEffect(() => {
    const list = query();
    if (!list) return undefined;
    const changed = (event) => setReduced(Boolean(event.matches));
    list.addEventListener?.("change", changed);
    return () => list.removeEventListener?.("change", changed);
  }, []);
  return reduced;
}

const Arrow = () => (
  <svg aria-hidden="true" focusable="false" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
);

const External = () => (
  <svg aria-hidden="true" focusable="false" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
);

const Check = () => (
  <svg aria-hidden="true" focusable="false" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l5 5 9-10" /></svg>
);

// A door's way in: a link where the app is set up, else a plain statement that it is not.
function DoorAction({ station, config }) {
  if (!config) return null;
  const { key, name } = station;
  if (!config.apps[key]) return <p className="kh-off">{name} is not set up here</p>;
  if (key === "certify") {
    if (!config.certifyUrl) {
      return <p className="kh-placeholder"><span className="kh-sr">Certify opens at </span>{CERTIFY_PLACEHOLDER}</p>;
    }
    return (
      <a className="kh-open" href={config.certifyUrl} target="_blank" rel="noopener noreferrer">
        <span>Open Certify</span><External /><span className="kh-sr"> (opens outside the platform, in a new tab)</span>
      </a>
    );
  }
  return <Link className="kh-open" to={key === "context" ? CONTEXT_PATH : DISCOVER_PATH}><span>Open {name}</span><Arrow /></Link>;
}

function Station({ station, index, config, clientName }) {
  const off = config && !config.apps[station.key];
  const headingId = `kh-door-${station.key}`;
  return (
    <article className={`kh-station${off ? " kh-station--off" : ""}`} style={{ "--kh-c": index + 2 }} aria-labelledby={headingId} data-door={station.key}>
      <div className="kh-card">
        <div className="kh-card-head">
          <span className="kh-well"><Icon name={station.icon} size={19} /></span>
          <h2 id={headingId} className="kh-ttl">{station.name}</h2>
        </div>
        <p className="kh-desc">{station.line}</p>
        <DoorAction station={station} config={config} />
      </div>
      <span className="kh-stop" aria-hidden="true">
        <span className="kh-stem" />
        <span className={`kh-node kh-n${index + 1}`}><Check /></span>
      </span>
      <div className="kh-details">
        <h3 className="kh-lab">Roles</h3>
        <ul className="kh-chips">{station.roles.map((role) => <li key={role}>{role.replace("{client}", clientName)}</li>)}</ul>
        <h3 className="kh-lab">Actions</h3>
        <ul className="kh-acts">{station.actions.map((action) => <li key={action}>{action}</li>)}</ul>
      </div>
    </article>
  );
}

export default function KnowledgeHome() {
  const config = useKnowledgeConfig();
  const reduced = usePrefersReducedMotion();
  const [paused, setPaused] = useState(false);
  const productName = config?.productName || DEFAULT_PRODUCT;
  const clientName = config?.clientName || DEFAULT_CLIENT;
  const motion = reduced ? "kh-row--still" : paused ? "kh-row--paused" : "kh-row--moving";

  return (
    <main className="kh-home">
      <Symbols />
      <h1 className="kh-sr">{productName}</h1>
      <div className="kh-motion-bar">
        {!reduced && (
          <button type="button" className="kh-pause" onClick={() => setPaused((p) => !p)}>
            {paused ? "Play the animation" : "Pause the animation"}
          </button>
        )}
      </div>
      <div className={`kh-row ${motion}`} data-testid="kh-story">
        <section className="kh-side kh-in" aria-labelledby="kh-in-h">
          <h2 id="kh-in-h" className="kh-ph">Brought in</h2>
          {BROUGHT_IN.map((group) => (
            <React.Fragment key={group.group}>
              <p className="kh-grp"><i className={`kh-dot kh-dot--${group.shape}`} />{group.group}</p>
              <ul className="kh-list">
                {group.items.map((item) => <li key={item.label} className="kh-item"><span className="kh-tile"><Icon name={item.icon} /></span>{item.label}</li>)}
              </ul>
            </React.Fragment>
          ))}
        </section>

        <div className="kh-rail" aria-hidden="true">
          <span className="kh-pt kh-sq" style={{ animationDelay: "0s" }} />
          <span className="kh-pt kh-ci" style={{ animationDelay: "0.22s" }} />
          <span className="kh-pt kh-sq" style={{ animationDelay: "0.44s" }} />
          <span className="kh-pt kh-ci" style={{ animationDelay: "0.66s" }} />
        </div>

        {STATIONS.map((station, index) => <Station key={station.key} station={station} index={index} config={config} clientName={clientName} />)}

        <section className="kh-side kh-out" aria-labelledby="kh-out-h">
          <h2 id="kh-out-h" className="kh-ph">Used by</h2>
          <p className="kh-grp kh-grp--accent"><i className="kh-dot kh-dot--circle" />Business teams</p>
          <ul className="kh-list">
            {USED_BY.map((item) => <li key={item.label} className="kh-item"><span className="kh-tile kh-tile--accent"><Icon name={item.icon} /></span>{item.label}</li>)}
          </ul>
        </section>
      </div>

      <section className="kh-tech" aria-labelledby="kh-tech-h">
        <div className="kh-tech-head">
          <h2 id="kh-tech-h">Technologies</h2>
          <span className="kh-all" role="link" aria-disabled="true">All technologies <span aria-hidden="true">→</span></span>
        </div>
        <ul className="kh-ints">
          {TECHNOLOGIES.map((tech) => (
            <li key={tech.name} className="kh-logo" data-tech={tech.name}>
              {tech.logo ? <Logo name={tech.logo} /> : <span className="kh-logo-mark kh-logo-none" aria-hidden="true" />}
              <span className="kh-lname">{tech.name}</span>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
