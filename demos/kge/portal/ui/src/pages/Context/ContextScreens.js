import React, { useEffect, useRef, useState } from "react";
import { Button } from "../../components/shared/Controls";
import { APP_NAMES } from "../../components/shell/apps";
import { TabButtons } from "./ContextTabs";
import Playground from "./Playground";
import Docs from "./Docs";
import Onboard from "./Onboard";
import Quality from "./Quality";
import Telemetry from "./Telemetry";
import { AboutRelease, FailedEvaluation, PassedEvaluation, useReleaseEvaluation } from "./ReleaseNotice";
import { canEvaluate, homeTab, nextStep, useContextApi, unitPlan, proposalPlan, useResource, visibleTabs } from "./contextState";
import "./context.css";

function Session({ bootstrap, onViewChange }) {
  const me = bootstrap.person, api = useContextApi(), tabs = visibleTabs(me), tester = canEvaluate(me);
  const [view, setView] = useState(() => homeTab(me));
  useEffect(() => { onViewChange?.(view); }, [view, onViewChange]);
  const root = useRef(null);
  const [destination, setDestination] = useState(null);
  useEffect(() => {
    if (!destination) return;
    const card = destination.proposal ? root.current.querySelector("#ob-proposal-card") : [...root.current.querySelectorAll("#ob-status .unit-card")].find(card => card.dataset.unit === destination.unit);
    const target = card || root.current.querySelector(`#view-${destination.view}`);
    const bar = document.querySelector("header.gb-bar");
    target.style.scrollMarginTop = `${bar && getComputedStyle(bar).position === "sticky" ? bar.offsetHeight + 12 : 12}px`;
    target.scrollIntoView?.({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    (card?.querySelector("button.primary") || card?.querySelector("summary") || root.current.querySelector(`#nav-${destination.view}`))?.focus({ preventScroll: true });
  }, [destination]);
  const [profile, setProfile] = useState(bootstrap.profile);
  const golden = useResource(api, "/golden"), ab = useResource(api, "/ab");
  const versions = useResource(api, "/versions");
  const onboard = useResource(api, "/onboard", tabs.includes("onboard"));
  useEffect(() => { const timer = setInterval(versions.refresh, 30000); return () => clearInterval(timer); }, [versions.refresh]);
  useEffect(() => {
    if (onboard.data?.job?.state !== "running") return;
    const timer = setTimeout(onboard.refresh, 2000);
    return () => clearTimeout(timer);
  }, [onboard.data, onboard.refresh]);
  const job = onboard.data?.job;
  useEffect(() => {
    if (job?.state !== "done" || !["release", "rollback"].includes(job.step)) return;
    let active = true;
    api("/profile").then(p => { if (active && !p.error) setProfile(p); });
    golden.refresh(); versions.refresh(); ab.refresh();
    return () => { active = false; };
  }, [job?.id, job?.state, job?.step, api, golden.refresh, versions.refresh, ab.refresh]);
  const hint = nextStep(onboard.data);
  const notice = useReleaseEvaluation(versions.data?.eval_post, me?.id);
  return <div ref={root} className="context-app v3-controls" data-person={me?.id || ""}>
    <div id="release-banner" role="status" hidden={bootstrap.mode?.released !== false}>{bootstrap.mode?.message}</div>
    {profile?.preview?.banner && <div id="preview" className="context-preview">{profile.preview.banner}</div>}
    <FailedEvaluation notice={notice} onQuality={tabs.includes("quality") ? () => { setView("quality"); root.current.querySelector("#nav-quality")?.focus(); } : null} />
    <div id="views-bar" className="context-tabbar">
      <TabButtons id="views" label="Workspaces" names={tabs} value={view} onChange={setView} counts={{ onboard: [...(onboard.data?.units || []).map(unit => unitPlan(unit, onboard.data)), ...(onboard.data?.proposal ? [proposalPlan(onboard.data.proposal, onboard.data)] : [])].filter(plan => plan.mine).length }} />
      <AboutRelease profile={profile} version={bootstrap.build?.version} baseline={ab.data?.baseline} evaluation={versions.data?.eval_post} />
      <PassedEvaluation notice={notice} quality={tabs.includes("quality")} />
    </div>
    <div id="next-step" hidden={!hint}><div className={hint?.waiting ? "wait" : ""}><span className="lead">{hint?.waiting ? "Waiting on" : "Your next step ·"}</span>
      <span id="next-step-text">{hint?.text}</span><Button data-go={hint?.view} data-unit={hint?.unit} data-proposal={hint?.proposal} onClick={() => { setView(hint.view); setDestination({ ...hint }); }}>Open</Button></div></div>
    <main aria-label={APP_NAMES.context}>
      {tabs.map(name => <div key={name} className="view" id={`view-${name}`} role="tabpanel" aria-labelledby={`nav-${name}`} hidden={view !== name}>
        {name === "ask" && <Playground api={api} me={me} presets={bootstrap.presets} header={bootstrap.ask} golden={golden.data} tester={tester} health={bootstrap.build?.ask} />}
        {name === "docs" && <Docs />}
        {name === "onboard" && <Onboard api={api} resource={onboard} me={me} />}
        {name === "telemetry" && view === "telemetry" && <Telemetry api={api} />}
        {name === "quality" && <Quality api={api} me={me} units={profile?.units || []} golden={golden.data} ab={ab.data} versions={versions.data} onboard={onboard.data} />}
      </div>)}
    </main>
  </div>;
}
export default function ContextScreens({ bootstrap, onViewChange }) {
  return <Session key={JSON.stringify(bootstrap)} bootstrap={bootstrap} onViewChange={onViewChange} />;
}
