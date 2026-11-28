import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useSelector } from "react-redux";
import { useLocation, useNavigate } from "react-router-dom";
import ShellPage from "../../components/shell/ShellPage";
import { appById } from "../../components/shell/apps";
import { useConfig } from "../../services/config";
import { Button, Tabs } from "../../components/shared/Controls";
import { INITIAL_PAGE_STATE, MODE_KEY, MODE_LABELS, isTyping, modesFor, nextMode, pageReducer, readAddress, writeAddress } from "./cerebroState";
import GraphView from "./graph/GraphView";
import CurateView from "./curate/CurateView";
import ChatView, { ChatDock } from "./chat/ChatView";
import { ChatProvider } from "./chat/chatSession";
import "./cerebro.css";

/* The regions of each mode, from the approved layout (cerebro.md §7.1). Each mode's own work
   replaces its view here: Graph (pages/Cerebro/graph/), Chat (pages/Cerebro/chat/) and Curate
   (pages/Cerebro/curate/). Every view is given the same props:
   - `state`: { mode, node, conversation, lastPath, graphPosition, graph, fittedPath, roles }
   - `actions`: selectNode(id|null), openNode(id) (Graph on that node), askAbout(id) (Chat on that
     node), setMode(mode), setChat(id|null) (the open conversation in the address, issue 625), addTurn(turn), setConversation(turns), setLastPath(path),
     setGraphPosition(position), setGraph(graph) (the read and laid-out graph, kept across modes),
     setFittedPath(key) (the answer's path the graph's view was last fitted to). */
export const VIEWS = Object.freeze({ chat: ChatView, graph: GraphView, curate: CurateView });

/* The chat is held here, above every mode (chat/chatSession.js), so a mode switch keeps the conversation (§7.2) and
   Graph mode docks it. `chat` is for tests ({ fetch, health, questions, glossary, reads, sendFeedback }); `health` is KGE's /health `ask` object for
   the answer-settings picker, as Context passes bootstrap.build.ask. */
export default function CerebroApp({ views = VIEWS, chat = {} }) {
  const location = useLocation();
  const navigate = useNavigate();
  const config = useConfig();
  const roles = useSelector((store) => store.user?.data?.cerebroRoles);
  const userId = useSelector((store) => store.user?.data?.userId);
  const modes = modesFor(roles);
  const { mode, node, chat: chatId } = readAddress(location.search, roles);
  const [page, dispatch] = useReducer(pageReducer, INITIAL_PAGE_STATE);
  const tabs = useRef(null);
  // KGE's /health `ask` object for the picker, read once through the portal unless the page was handed one; null
  // until KGE states it (the picker handles null).
  const [health, setHealth] = useState(null);
  useEffect(() => {
    if (chat.health !== undefined) return undefined;
    let live = true;
    fetch("/api/cerebro/health", { credentials: "same-origin" })
      // Issue 651 B7: a failed KGE read still names the chat's model ({ detail, model }), which the chip shows.
      .then((r) => (r.ok ? r.json() : r.json().then((body) => (typeof body?.model === "string" ? { model: body.model } : null), () => null)))
      .then((body) => { if (live) setHealth(body); })
      .catch(() => {});
    return () => { live = false; };
  }, [chat.health]);

  // The golden questions this person may ask (KGE's /golden through the portal, already filtered to what they may
  // read), read once unless the page was handed them; none until they are in, and none if they cannot be read.
  // Where the reply gives the glossary's terms, the input's suggestions offer them too; where it gives the units the
  // person reads, Context's answer card names them in a refusal's way forward, as Context's Ask does.
  const [questions, setQuestions] = useState([]);
  const [glossary, setGlossary] = useState([]);
  const [reads, setReads] = useState(undefined);
  useEffect(() => {
    if (chat.questions !== undefined) return undefined;
    let live = true;
    fetch("/api/cerebro/questions", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!live) return;
        if (Array.isArray(body?.questions)) setQuestions(body.questions.filter((q) => typeof q === "string" && q.trim()));
        if (Array.isArray(body?.reads)) setReads(body.reads.filter((u) => typeof u === "string" && u));
        if (Array.isArray(body?.glossary)) setGlossary(body.glossary.filter((g) => g && typeof g === "object" && typeof g.term === "string" && g.term.trim()));
      })
      .catch(() => {});
    return () => { live = false; };
  }, [chat.questions]);

  // An address naming no mode, an unknown one, or one this person may not use is rewritten in place.
  const wanted = writeAddress({ mode, node, chat: chatId });
  useEffect(() => {
    if (Array.isArray(roles) && location.search !== wanted) navigate({ pathname: location.pathname, search: wanted }, { replace: true });
  }, [location.pathname, location.search, navigate, roles, wanted]);

  // A step that changes nothing adds no history entry. The open conversation stays in the address across steps; a
  // conversation opened or started replaces the entry (issue 625), so Back still steps between modes.
  const go = useCallback((next) => {
    if (next.mode && !modesFor(roles).includes(next.mode)) return;
    const search = writeAddress({ mode: next.mode ?? mode, node: next.node === undefined ? node : next.node, chat: next.chat === undefined ? chatId : next.chat });
    if (search !== location.search) navigate({ pathname: location.pathname, search }, next.replace ? { replace: true } : undefined);
  }, [location.pathname, location.search, mode, navigate, node, chatId, roles]);
  const actions = useMemo(() => ({
    setMode: (next) => go({ mode: next }),
    setChat: (id) => go({ chat: id ?? null, replace: true }),
    selectNode: (id) => go({ node: id ?? null }),
    openNode: (id) => go({ mode: "graph", node: id }),
    askAbout: (id) => {
      dispatch({ type: "asking", node: id ?? null });
      go({ mode: "chat", node: id });
    },
    asked: () => dispatch({ type: "asking", node: null }),
    addTurn: (turn) => dispatch({ type: "turn", turn }),
    setConversation: (conversation) => dispatch({ type: "conversation", conversation }),
    setLastPath: (path) => dispatch({ type: "path", path }),
    setGraphPosition: (position) => dispatch({ type: "position", position }),
    setGraph: (graph) => dispatch({ type: "graph", graph }),
    setFittedPath: (path) => dispatch({ type: "fitted", path }),
  }), [go]);
  // "Ask about this" holds for the next question in Chat mode alone.
  useEffect(() => {
    if (mode !== "chat") dispatch({ type: "asking", node: null });
  }, [mode]);

  // The switch key cycles the modes anywhere on the page but in a field.
  useEffect(() => {
    const onKey = (event) => {
      if (modes.length < 2 || event.key !== MODE_KEY || event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented || isTyping(event.target)) return;
      event.preventDefault();
      go({ mode: nextMode(mode, roles) });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, mode, roles, modes.length]);

  const onTabKey = (event) => {
    const at = modes.indexOf(mode);
    const to = { ArrowRight: at + 1, ArrowLeft: at - 1 + modes.length, Home: 0, End: modes.length - 1 }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    const next = modes[to % modes.length];
    go({ mode: next });
    tabs.current?.querySelector(`#cerebro-tab-${next}`)?.focus();
  };

  const View = views[mode];
  const state = { mode, node, roles: Array.isArray(roles) ? roles : [], userId: typeof userId === "string" ? userId : "", reads: chat.reads ?? reads, ...page };
  /* Under the portal's global bar: one row with the mode tabs on the left and the switch hint on the right, then the
     view, which takes the rest of the window's height (the shell's `fill`), so Graph mode's canvas can use all of it
     without the page scrolling. The view names its mode in its class for the mode's own layout. */
  return <ShellPage app="cerebro" title={appById("cerebro", config)?.label} fill>
    <main className="cerebro-app v3-controls" aria-label={MODE_LABELS[mode]}>
      <div className="cerebro-bar">
        <Tabs ref={tabs} aria-label="Modes" className="cerebro-modes" onKeyDown={onTabKey}>
          {modes.map((name) => <Button key={name} role="tab" id={`cerebro-tab-${name}`} aria-controls="cerebro-view" aria-selected={name === mode} tabIndex={name === mode ? 0 : -1} onClick={() => go({ mode: name })}>{MODE_LABELS[name]}</Button>)}
        </Tabs>
        {modes.length > 1 ? <p className="cerebro-hint">Press <kbd>{MODE_KEY}</kbd> to switch mode</p> : null}
      </div>
      <ChatProvider actions={actions} openId={chatId} fetch={chat.fetch} health={chat.health ?? health} questions={chat.questions ?? questions} glossary={chat.glossary ?? glossary} sendFeedback={chat.sendFeedback}>
        <div id="cerebro-view" role="tabpanel" tabIndex={0} aria-labelledby={`cerebro-tab-${mode}`} className={`cerebro-view cerebro-view-${mode}`}>
          <View state={state} actions={actions} />
          {mode === "graph" ? <ChatDock state={state} /> : null}
        </div>
      </ChatProvider>
    </main>
  </ShellPage>;
}
