import React, { memo, useEffect, useRef } from "react";
import { Button, Panel, Tabs } from "../../components/shared/Controls";
import { mountContext } from "./controller";
import "./context.css";

// React owns the workspace shell; the controller retains the existing task flows
// and their escaped result renderers. The global bar owns identity and theme.
export default memo(function Workspace({
  bootstrap
}) {
  const root = useRef(null);
  useEffect(() => mountContext(root.current, bootstrap), [bootstrap]);
  return (
    <div className="context-app v3-controls" ref={root}>
      <div id={"release-banner"} className={"notice"} role={"status"} aria-live={"polite"} hidden></div>
      <div id={"release-eval-banner"} className={"notice"} role={"status"} aria-live={"polite"} hidden></div>
      <div className={"context-toolbar"}>
        <details id={"about-build"} className={"popover"}>
          <summary>
            <span>
              {"About"}
              <span className={"long"}>
                {" this build"}
              </span>
            </span>
          </summary>
          <div className={"pop"}>
            <p id={"profile"} title={"The release: what certification landed, the graph's only input from the estate. The artifact: the compiled pack."}></p>
            <p id={"version"} className={"muted"}></p>
            <p className={"muted"}>
              {"Local demo. Synthetic estate, embedded graph, rule-based parser (a model is optional). The graph advises; the warehouse answers; the documents back it."}
            </p>
            <div id={"about-ab"} className={"muted"}></div>
          </div>
        </details>
        <span id={"preview"} role={"status"} aria-live={"polite"}></span>
      </div>
      <Tabs as="nav" id={"views"} role={"tablist"} aria-label={"Workspaces"}>
        <Button role={"tab"} id={"nav-docs"} data-view={"docs"} aria-controls={"view-docs"} aria-selected={"false"} tabIndex={"-1"}>
          {"Docs"}
        </Button>
        <Button role={"tab"} id={"nav-ask"} data-view={"ask"} aria-controls={"view-ask"} aria-selected={"true"}>
          {"Ask"}
        </Button>
        <Button role={"tab"} id={"nav-review"} data-view={"review"} aria-controls={"view-review"} aria-selected={"false"} tabIndex={"-1"} hidden>
          {"Review queue"}
          <span className={"count"} id={"queue-count"}></span>
        </Button>
        <Button role={"tab"} id={"nav-onboard"} data-view={"onboard"} aria-controls={"view-onboard"} aria-selected={"false"} tabIndex={"-1"} hidden>
          {"Onboard"}
          <span className={"count"} id={"onboard-count"}></span>
        </Button>
        <Button role={"tab"} id={"nav-quality"} data-view={"quality"} aria-controls={"view-quality"} aria-selected={"false"} tabIndex={"-1"} hidden>
          {"Quality"}
        </Button>
      </Tabs>
      <div id={"next-step"} hidden>
        <div>
          <span className={"lead"}>
            {"Your next step"}
          </span>
          <span id={"next-step-text"}></span>
          <Button data-go={"onboard"}>
            {"Open"}
          </Button>
        </div>
      </div>
      <main aria-label="Context">
        <Panel id={"no-view"} role={"status"} hidden>
          <p className={"muted"}></p>
        </Panel>
        <div className={"view"} id={"view-docs"} role={"tabpanel"} aria-labelledby={"nav-docs"} hidden>
          <iframe id={"role-docs"} title={"Your role guides"} style={{
  "width": "100%",
  "height": "75vh",
  "border": "0"
}}></iframe>
        </div>
        <div className={"view"} id={"view-ask"} role={"tabpanel"} aria-labelledby={"nav-ask"}>
          <Panel id={"assistant"}>
            <h2>
              {"Ask a question"}
            </h2>
            <div className={"row"}>
              <label htmlFor={"q"} className={"vh"}>
                {"Question"}
              </label>
              <input type={"text"} id={"q"} placeholder={"Ask for a figure or a definition, or pick an example below"} />
              <Button className={"primary"} id={"ask"}>
                {"Ask"}
              </Button>
            </div>
            <details className={"fold"} id={"settings"}>
              <summary>
                {"Answer settings "}
                <span className={"muted"} id={"settings-now"}></span>
              </summary>
              <div className={"row"}>
                <span className={"field"}>
                  <label htmlFor={"purpose"}>
                    {"Purpose"}
                  </label>
                  <select id={"purpose"} title={"Purpose of the request"}>
                    <option>
                      {"analysis"}
                    </option>
                    <option>
                      {"assistant"}
                    </option>
                    <option>
                      {"supervision"}
                    </option>
                  </select>
                </span>
                <span className={"hint"}>
                  {"Why you ask: the access rules are checked for this purpose. Analysis suits most questions."}
                </span>
              </div>
              <div className={"row"}>
                <span className={"field"}>
                  <label htmlFor={"arm"}>
                    {"Arm"}
                  </label>
                  <select id={"arm"} title={"Which arm answers: a preset of the control, or the reference agent's graph off or graph on (GET /presets)"}></select>
                </span>
                <span className={"hint"}>
                  {"Which setup answers. graph uses the whole governance graph; the others leave parts of it out, for comparison."}
                </span>
              </div>
              <div className={"row"}>
                <label className={"switch"} htmlFor={"rich"}>
                  <input type={"checkbox"} id={"rich"} autoComplete={"off"} />
                  {" Rich answers"}
                </label>
                <span className={"hint"}>
                  {"Adds a sentence, a figures table and a chart where the answer has rows."}
                </span>
              </div>
            </details>
            <details className={"fold"} id={"examples"} open>
              <summary>
                {"Examples"}
              </summary>
              <div className={"row chips"} id={"chips"}></div>
            </details>
            <div id={"thread"} aria-live={"polite"} aria-relevant={"additions"}></div>
          </Panel>
          <div id={"chat-splitter"} role={"separator"} aria-label={"Resize chat column"} aria-orientation={"vertical"} aria-valuemin={"320"} aria-valuemax={"960"} aria-valuenow={"480"} tabIndex={"0"}></div>
          <aside>
            <Panel id={"detail"}>
              <h2>
                {"Behind the answer"}
              </h2>
              <div id={"about"} className={"small muted"}></div>
              <Tabs as="div" className={"tabs"} role={"tablist"} aria-label={"Behind the answer"}>
                <Button role={"tab"} id={"tabbtn-sources"} data-tab={"sources"} aria-controls={"tab-sources"} aria-selected={"false"} tabIndex={"-1"}>
                  {"Sources"}
                </Button>
                <Button role={"tab"} id={"tabbtn-map"} className={"on"} data-tab={"map"} aria-controls={"tab-map"} aria-selected={"true"}>
                  {"Map"}
                </Button>
                <Button role={"tab"} id={"tabbtn-plan"} data-tab={"plan"} aria-controls={"tab-plan"} aria-selected={"false"} tabIndex={"-1"}>
                  {"Plan"}
                </Button>
                <Button role={"tab"} id={"tabbtn-trace"} data-tab={"trace"} aria-controls={"tab-trace"} aria-selected={"false"} tabIndex={"-1"}>
                  {"Trace"}
                </Button>
              </Tabs>
              <div id={"tab-sources"} role={"tabpanel"} aria-labelledby={"tabbtn-sources"} tabIndex={"0"} hidden>
                <p className={"muted small"}>
                  {"The documents behind a combined answer appear here: what it cites, what it was shown and did not use, and what the privacy gate held back."}
                </p>
              </div>
              <div id={"tab-map"} role={"tabpanel"} aria-labelledby={"tabbtn-map"} tabIndex={"0"}>
                <p className={"muted small"}>
                  {"Ask something and the resolved map appears here: the term, its certified implementations, what they are measured in, and which joins do not exist."}
                </p>
              </div>
              <div id={"tab-plan"} role={"tabpanel"} aria-labelledby={"tabbtn-plan"} tabIndex={"0"} hidden></div>
              <div id={"tab-trace"} role={"tabpanel"} aria-labelledby={"tabbtn-trace"} tabIndex={"0"} hidden></div>
            </Panel>
          </aside>
        </div>
        <div className={"view"} id={"view-review"} role={"tabpanel"} aria-labelledby={"nav-review"} hidden>
          <Panel id={"steward"}>
            <h2>
              {"Review queue"}
            </h2>
            <p className={"small muted"}>
              {"Tags the pipeline could not settle on its own. Accept or reject each; a decision is recorded at full confidence and supersedes the machine's tag."}
            </p>
            <div id={"queuemsg"} className={"small"} role={"status"} aria-live={"polite"}></div>
            <div id={"queue-tools"}>
              <label htmlFor={"queue-filter"} className={"lbl"} style={{
  "margin": "0"
}}>
                {"Show"}
              </label>
              <select id={"queue-filter"} style={{
  "flex": "0 1 280px"
}}>
                <option value={""}>
                  {"Every kind"}
                </option>
              </select>
            </div>
            <div id={"queue"} className={"small muted"}>
              {"Loading."}
            </div>
          </Panel>
        </div>
        <div className={"view"} id={"view-onboard"} role={"tabpanel"} aria-labelledby={"nav-onboard"} hidden>
          <Panel id={"onboard"} hidden>
            <h2>
              {"Onboard a business unit"}
            </h2>
            <a id={"ob-guide"} href={"/api/context/onboarding-guide"} target={"_blank"} rel={"noopener"}>
              {"Role guides"}
            </a>
            <div id={"ob-msg"} className={"small"} role={"status"} aria-live={"polite"}></div>
            <div id={"ob-changes"} aria-live={"polite"}></div>
            <div id={"ob-status"}></div>
            <article className={"unit-card"} id={"ob-proposal-card"} hidden>
              <div id={"ob-proposal"}></div>
            </article>
            <div id={"ob-work"}>
              <details id={"ob-add"} className={"fold"}>
                <summary id={"ob-add-title"}>
                  {"Add a unit"}
                </summary>
                <div className={"row"}>
                  <span className={"field"}>
                    <label htmlFor={"ob-unit"}>
                      {"Unit"}
                    </label>
                    <input id={"ob-unit"} type={"text"} placeholder={"a short id, such as leasing"} />
                  </span>
                  <span className={"field"} id={"ob-source-label"}>
                    <label htmlFor={"ob-label"}>
                      {"Label"}
                    </label>
                    <input id={"ob-label"} type={"text"} placeholder={"its name, such as Asset Finance"} />
                  </span>
                </div>
                <div className={"row"} id={"ob-upload"}>
                  <span className={"drop"}>
                    <label htmlFor={"ob-file"} className={"lbl"}>
                      {"1 \u00b7 Describe the data: a DDL file (.sql). Its tables preview as soon as it is chosen."}
                    </label>
                    <input id={"ob-file"} type={"file"} accept={".sql,text/plain"} />
                  </span>
                  <span className={"drop"} id={"ob-csv-zone"}>
                    <label htmlFor={"ob-csv"} className={"lbl"}>
                      {"2 \u00b7 Rows and questions: CSV files, one per table, and questions.json or heldout.json if you have them"}
                    </label>
                    <input id={"ob-csv"} type={"file"} multiple accept={".csv,.json"} />
                    <span className={"field"} id={"ob-row-source"}>
                      <label htmlFor={"ob-rows"}>
                        {"Rows"}
                      </label>
                      <select id={"ob-rows"}>
                        <option value={"csv"}>
                          {"from these CSV files"}
                        </option>
                        <option value={"invented"}>
                          {"invented from the DDL, labelled invented"}
                        </option>
                        <option value={"none"}>
                          {"no rows yet: draft only"}
                        </option>
                      </select>
                    </span>
                    <div id={"ob-files"} className={"files"}></div>
                  </span>
                </div>
                <div id={"ob-tables"} className={"small"}></div>
                <div className={"row ob-actions"}>
                  <Button id={"ob-create"} className={"primary"}>
                    {"Create source"}
                  </Button>
                  <Button id={"ob-cancel"} className={"quiet"} hidden>
                    {"Cancel"}
                  </Button>
                </div>
              </details>
              <div id={"ob-jobbar"}></div>
              <div id={"ob-notes"}></div>
              <div id={"ob-eval-post"} role={"status"} hidden></div>
              <div id={"ob-eval-pre"}></div>
              <div id={"ob-versions"}></div>
            </div>
          </Panel>
        </div>
        <div className={"view"} id={"view-quality"} role={"tabpanel"} aria-labelledby={"nav-quality"} hidden>
          <Panel id={"control"}>
            <h2>
              {"Quality: the golden set and the A/B"}
            </h2>
            <div className={"row"}>
              <Button id={"runeval"} hidden>
                {"Run the golden set"}
              </Button>
              <span className={"muted small"} id={"evalnote"}>
                {"The profile's golden set on the three presets, scored apart by kind."}
              </span>
            </div>
            <progress id={"evalprogress"} hidden aria-label={"Running the golden set"}></progress>
            <div id={"evalout"}></div>
            <div id={"ab"} className={"small"}></div>
          </Panel>
        </div>
      </main>
    </div>
  );
});
