import './../../assets/styles/chatbot.css';
import { useState, useRef, useEffect } from 'react';
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import sql from "react-syntax-highlighter/dist/cjs/languages/hljs/sql";
import { atelierHeathLight } from "react-syntax-highlighter/dist/cjs/styles/hljs";
import { Typography, Collapse, Button, Spin, ConfigProvider, Select, Tooltip, notification } from "antd";
import { useSelector } from "react-redux";
import enUS from 'antd/lib/locale/en_US'
import { DatabaseOutlined } from "@ant-design/icons";
import { ProTable } from "@ant-design/pro-table";
import { DislikeFilled, DislikeOutlined, LikeFilled, LikeOutlined, DownloadOutlined } from "@ant-design/icons";
import FeedbackForm from './FeedbackForm';
import SaveChatFeedback from './SaveChatFeedback';
import Markdown from './Markdown';
import { CopyToClipboard } from 'react-copy-to-clipboard';
import { format } from 'sql-formatter';
import { postApi } from '../../api/api';
// import FetchAnswer from './FetchAnswer';

SyntaxHighlighter.registerLanguage("sql_more", sql);

// Narrow enough to still read a result table, wide enough not to collapse.
const MIN_PANEL_WIDTH = 360;
// Painted pixels of page kept visible beside the panel, so the data product
// layout has room to reflow instead of being crushed.
const MIN_PAGE_WIDTH = 420;
const PANEL_WIDTH_KEY = 'askPortalPanelWidth';

// Reference-style suggested prompts, shown until the first question is asked.
const SUGGESTED_PROMPTS = [
  "What does this data asset contain?",
  "Which columns does this table have?",
  "Show the top 10 rows by the most recent date",
  "How many records were loaded in the last month?",
];

// Shown when the chat is scoped to a whole domain (Explore), where questions can
// span and join multiple products.
const DOMAIN_PROMPTS = [
  "What tables are in this domain and how do they relate?",
  "Which category has the most records?",
  "Show the top 10 by the most recent date",
  "Summarise activity across this domain over the last month",
];

// Chat history is kept per data product in sessionStorage so it survives leaving
// and returning to the page. Only serialisable turns are stored — the greeting is
// rebuilt on load, and transient loading/story messages are dropped.
const HISTORY_KEY = (pid) => `askHistory:${pid}`;
const loadHistory = (pid) => {
  if (!pid) return null;
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(HISTORY_KEY(pid)));
    return Array.isArray(parsed) ? parsed : null;
  } catch (e) {
    return null;
  }
};
const saveHistory = (pid, turns) => {
  if (!pid) return;
  try {
    window.sessionStorage.setItem(HISTORY_KEY(pid), JSON.stringify(turns));
  } catch (e) {
    /* storage full or unavailable — history simply won't persist */
  }
};

function Chatbot(props) {
  const GuidelineMessageContent = () => {
    return (
      <Typography.Paragraph style={{ marginBottom: "0" }}>
        <p style={{ fontSize: "13px", color: "var(--v3-ink)" }}>Ask about the products you can read. Open Guidelines for help with discovery and governed answers.</p>
        <Collapse
          className="cot-collapse"
          items={[
            {
              key: "1",
              label: (
                <>
                  <DatabaseOutlined /> Guidelines
                </>
              ),
              children: (
                <ol className="guideline-list">
                  <li>Browse products or search by name, field or business term. Your signed-in identity determines which units you can see; a portal admin role does not widen that access.</li>
                  <li>With KGE, Search can match certified terms, synonyms and related products. Ask for a measure, unit and period using the catalogue's terms. Read the returned answer and citations together; ask a new question for another figure.</li>
                  <li>Data preview shows allowed rows and columns. Export CSV downloads a capped product export. Run a query accepts read-only SQL for the product's unit; start with its suggested query.</li>
                  <li>Data reads require a table in the served release and apply its access rules and row selectors. A catalogue badge does not grant access. If the page asks for a new release after an upgrade, contact your knowledge engineer.</li>
                  <li>The portal alone can browse, search and answer questions about catalogue owners and fields. It cannot return financial figures, preview rows, export data or run SQL. A notice identifies a fallback to the portal catalogue when KGE is unavailable.</li>
                  <li>Product descriptions and feedback belong to the portal. KGE answers use the released definitions, so an edited description may differ until it has been accepted, certified, checked and released.</li>
                  <li>Use the thumbs up or thumbs down button to give feedback. A refusal or an abstention explains what is unavailable; ask the named business team or steward to resolve it.</li>
                </ol>
              ),
            },
          ]}
        />
      </Typography.Paragraph>
    );
  };
  const buildGreeting = () => ({
    text: GuidelineMessageContent(),
    isBot: true,
    type: null,
    chatFeedback: 'na',
    userQuerySeqId: null,
  });
  // What this chat is scoped to: a single product (detail page) or a whole domain
  // (Explore). Chat history and the API endpoint both key off this.
  const scopeId = props.domainMode ? `domain:${props.scopeLabel || "current"}` : props.productId;
  const [messages, setMessages] = useState(() => {
    const saved = loadHistory(scopeId);
    return saved && saved.length ? [buildGreeting(), ...saved] : [buildGreeting()];
  });
  const [input, setInput] = useState("")
  const [currentChatFeedback, setCurrentChatFeedback] = useState(null);
  const [currentUserQuerySeqId, setCurrentUserQuerySeqId] = useState(null);
  const [open, setOpen] = useState(false);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [chatIndex, setChatIndex] = useState(null)
  const [copied, setCopied] = useState(false);
  // const [isInterrupted, setIsInterrupted] = useState(null);
  const [api, contextHolder] = notification.useNotification();
  const [time, setTime] = useState(0);
  const [wbskt, setWbskt] = useState(null);
  const [sessionId, setSessionId] = useState(Date.now)
  // null means "fall back to the responsive width in chatbot.css".
  const [panelWidth, setPanelWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(PANEL_WIDTH_KEY));
    return stored >= MIN_PANEL_WIDTH ? stored : null;
  });

  const userData = useSelector((state) => state.user.data);
  const userId = userData.userId?.split('@')[0];
  const scopedProductName = useSelector((state) => state.dataProduct.selectedProductName);

  // Swap to another scope's saved history when it changes without the panel
  // unmounting (e.g. product → product, or a different domain in Explore).
  const prevScopeRef = useRef(scopeId);
  useEffect(() => {
    if (prevScopeRef.current === scopeId) return;
    prevScopeRef.current = scopeId;
    setSessionId(Date.now());
    const saved = loadHistory(scopeId);
    setMessages(saved && saved.length ? [buildGreeting(), ...saved] : [buildGreeting()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeId]);

  // Persist the conversation (minus the greeting and transient messages) so it
  // survives leaving and returning to this product or domain.
  useEffect(() => {
    const turns = messages.slice(1).filter((m) => m.type !== 'loading' && m.type !== 'story');
    saveHistory(scopeId, turns);
  }, [messages, scopeId]);

  const clearChat = () => {
    setSessionId(Date.now());
    setMessages([buildGreeting()]);
    saveHistory(scopeId, []);
  };

  const handleCopy = () => {
    setCopied(true);
    setTimeout(() => {
      setCopied(false);
    }, 2000);
  };

  const onCreate = async (values, chatRating, userQuerySeqId) => {
    const isLiked = chatRating === 'like' ? 1 : 0
    const userFeedback = {
      "session_id": messages[chatIndex]?.sessionId ?? sessionId,
      "user_query_seq_id": userQuerySeqId,
      "feedback_rating": isLiked,
      "feedback_comment": values.feedback,
      "productId": props.productId,
      "productIds": props.productId ? undefined : props.productIds,
      "domain": props.productId ? undefined : props.scopeLabel
    }
    setConfirmLoading(true)
    // await SaveChatFeedback(userFeedback).then((response) => 
    //     console.log(response)
    // ).catch(error => {
    //     console.log(error)
    // })
    let request = {
      api: "/feedback-add",
      params: userFeedback,
    };
    try {
      await SaveChatFeedback(request);
      setOpen(false);
    } catch (error) {
      api.error({ message: "Feedback was not saved", description: "Please try again." });
    } finally {
      setConfirmLoading(false)
    }
  };

  const msgEnd = useRef(null);
  const chatInputRef = useRef(null);
  const hideChatRef = useRef(null);
  const intervalRef = useRef(null);
  const resizingRef = useRef(false);
  const panelWidthRef = useRef(null);
  const panelRef = useRef(null);
  // One question in flight at a time; the input itself is never gated on feedback.
  const pendingRef = useRef(false);
  // The control that opened the panel, so closing can hand focus back to it.
  const openerRef = useRef(null);
  const showRef = useRef(Boolean(props.showChatbot));
  showRef.current = Boolean(props.showChatbot);

  /**
   * Keyboard users: focus moves into the panel when it opens and returns to
   * the control that opened it when it closes. While hidden the panel is
   * inert, so nothing inside it can take focus or be read out.
   */
  useEffect(() => {
    if (props.showChatbot) {
      const active = document.activeElement;
      if (active && active !== document.body && !panelRef.current?.contains(active)) openerRef.current = active;
      chatInputRef.current?.focus({ preventScroll: true });
      return;
    }
    const opener = openerRef.current;
    if (opener && opener.isConnected && panelRef.current?.contains(document.activeElement)) opener.focus();
  }, [props.showChatbot]);

  /**
   * Reserve space for the panel instead of covering the page. The panel is
   * fixed to the viewport, so padding the body shifts the page content and
   * header without moving the panel itself. Measuring the rendered element
   * keeps this correct whether the width comes from the CSS breakpoints or
   * from a drag.
   */
  useEffect(() => {
    if (!props.showChatbot) {
      document.body.style.transition = 'padding-right 0.2s ease';
      document.body.style.paddingRight = '';
      return;
    }

    const panel = panelRef.current;
    if (!panel) return;

    const applyOffset = () => {
      // Skip the easing mid-drag, otherwise the page lags behind the handle.
      document.body.style.transition = resizingRef.current ? 'none' : 'padding-right 0.2s ease';
      document.body.style.paddingRight = `${panel.offsetWidth}px`;
    };

    applyOffset();
    const observer = new ResizeObserver(applyOffset);
    observer.observe(panel);
    window.addEventListener('resize', applyOffset);

    return () => {
      observer.disconnect();
      window.removeEventListener('resize', applyOffset);
      document.body.style.paddingRight = '';
    };
  }, [props.showChatbot]);

  const startResize = (event) => {
    event.preventDefault();
    resizingRef.current = true;
    // Stop the drag from selecting the conversation text it passes over.
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  };

  // Double-clicking the handle drops back to the default responsive width.
  const resetWidth = () => {
    panelWidthRef.current = null;
    setPanelWidth(null);
    window.localStorage.removeItem(PANEL_WIDTH_KEY);
  };

  useEffect(() => {
    const handleResizeMove = (event) => {
      if (!resizingRef.current) return;
      const panel = panelRef.current;
      if (!panel) return;

      // body carries zoom: 67%, so painted pixels and layout pixels differ.
      // Mouse coordinates are painted; the width we set is layout. Derive the
      // factor from the element itself so this holds if the zoom ever changes.
      const painted = panel.getBoundingClientRect().width;
      const scale = painted && panel.offsetWidth ? painted / panel.offsetWidth : 1;

      // The panel is docked right, so its width is the gap from the cursor
      // to the right edge of the window. Leave enough page visible for the
      // product layout to stay readable rather than collapsing to a sliver.
      const desired = (window.innerWidth - event.clientX) / scale;
      const maxWidth = (window.innerWidth - MIN_PAGE_WIDTH) / scale;
      const next = Math.min(Math.max(desired, MIN_PANEL_WIDTH), maxWidth);
      panelWidthRef.current = next;
      setPanelWidth(next);
    };

    const handleResizeEnd = () => {
      if (!resizingRef.current) return;
      resizingRef.current = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      if (panelWidthRef.current) {
        window.localStorage.setItem(PANEL_WIDTH_KEY, String(Math.round(panelWidthRef.current)));
      }
    };

    document.addEventListener('mousemove', handleResizeMove);
    document.addEventListener('mouseup', handleResizeEnd);
    return () => {
      document.removeEventListener('mousemove', handleResizeMove);
      document.removeEventListener('mouseup', handleResizeEnd);
    };
  }, []);

  useEffect(() => {
    // Close on Escape. The panel is docked alongside the page rather than
    // overlaying it, so clicking the page no longer dismisses it - use the
    // header close button or Escape.
    const handleEscape = (event) => {
      if (event.key === 'Escape' && showRef.current) {
        props.setShowChatbot(false)
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('keydown', handleEscape);
    };
  }, []);

  useEffect(() => {
    if (msgEnd.current) {
      msgEnd.current.scrollTop = msgEnd.current.scrollHeight;
    }
  }, [messages.length, ...messages.map(message => message.type)])

  useEffect(() => {
    chatInputRef.current.style.height = "0px";
    const scrollHeight = chatInputRef.current.scrollHeight;
    chatInputRef.current.style.height = scrollHeight + "px";
  }, [input]);

  useEffect(() => {
    const story = {
      25: "Processing your request to generate the results.",
      40: "Currently generating your results, stay tuned.",
      55: "Collecting your results, almost there.",
      70: "Results are coming your way.",
      90: "Almost there...",
    }[time];

    if (story) {
      setMessages(prevMessages => [...prevMessages, {text: story, isBot: true, type: 'story', chatFeedback: 'na', userQuerySeqId: null}])
    }
    else if (time === 30 || time === 45 || time === 60 || time === 75 || time === 95) {
      setMessages(prevMessages => prevMessages.filter((message) => message.type !== 'story'))
    }
  }, [time])

  const closeChatbot = () => {
    props.setShowChatbot(false);
  };

  const toggleChatFeedback = (i, chatFeedback, userQuerySeqId) => {
    let newStateMessages = [...messages];
    newStateMessages[i].chatFeedback = chatFeedback;
    setMessages(newStateMessages)
    setCurrentChatFeedback(chatFeedback)
    setCurrentUserQuerySeqId(userQuerySeqId)
  }

  // KGE enforces access and refuses or abstains with a reason; the portal adds
  // no keyword filter of its own in front of it.
  const handleSend = async (forcedText) => {
    const text = (typeof forcedText === "string" ? forcedText : input).trim();
    if (!text || pendingRef.current) return
    pendingRef.current = true;
    // Send and the suggested prompts hide once a question is on its way; keep a
    // keyboard user's focus in the input instead of letting it fall to the page.
    chatInputRef.current?.focus({ preventScroll: true });

    setInput('')
    setMessages(prevMessages => [...prevMessages,
      { text: text, isBot: false, type: null, chatFeedback: null, userQuerySeqId: null },
      { text: "Looking at the data to answer that...", isBot: true, type: 'loading', chatFeedback: 'na', userQuerySeqId: null }
    ])

    intervalRef.current = setInterval(() => {
      setTime(prevTime => prevTime + 1);
    }, 1000)

    let res = null;
    let failure = "";
    try {
      res = props.domainMode
        ? await postApi({
            api: "/ask-domain",
            params: { productIds: props.productIds, question: text },
          })
        : await postApi({
            api: "/ask-data-product",
            params: { productId: props.productId, question: text },
          });
    } catch (error) {
      const detail = error?.response?.data?.detail;
      failure = typeof detail === "string" ? detail : "";
    }

    clearInterval(intervalRef.current)
    setTime(0)
    pendingRef.current = false;

    setMessages(prevMessages => {
      // Drop the placeholders this request put up before appending the answer.
      const settled = prevMessages.filter(message => message.type !== 'loading' && message.type !== 'story');

      if (!res || !res.data) {
        return [...settled, { text: failure || "Sorry, that question could not be answered right now. Please try again.", isBot: true, type: 'error', chatFeedback: 'na', userQuerySeqId: null }]
      }

      const citations = (res.data.citations || []).map((citation) => typeof citation === "string" ? citation : `[${citation.n}] ${citation.title || citation.doc}`);
      const figures = (res.data.figures || []).map((figure) => typeof figure === "string" ? figure : `${figure.label}: ${figure.value ?? "—"}${figure.currency ? ` ${figure.currency}` : ""}`);
      const behaviour = String(res.data.behaviour || "");
      const refused = ["refuse", "blocked_access"].includes(behaviour);
      const abstained = behaviour.startsWith("abstain");
      if (refused || abstained) {
        // Not an error: KGE declined on purpose. Show its reason as a neutral card.
        // KGE's answer_text often restates its reason ("No answer: <reason> …"): a part that
        // another part already contains is shown once, in the fuller part.
        const parts = [res.data.notice, res.data.reason, res.data.answer_text].map((part) => String(part ?? "").trim()).filter(Boolean);
        const core = (part) => part.replace(/[.\s]+$/, "");
        const reason = parts.filter((part, index) => parts.indexOf(part) === index && !parts.some((other) => other !== part && other.includes(core(part)))).join("\n\n");
        return [...settled, { text: reason || (refused ? "KGE refused this question and gave no reason." : "KGE abstained from this question and gave no reason."), kind: refused ? 'refused' : 'abstained', isBot: true, type: 'notice', chatFeedback: 'na', userQuerySeqId: null }];
      }
      const answer = [res.data.notice, res.data.answer_text || res.data.reason || "No answer text was returned."].filter(Boolean).join("\n\n");
      const details = [figures.length ? `Figures: ${figures.join(", ")}` : "", citations.length ? `Citations: ${citations.join(", ")}` : ""].filter(Boolean).join("\n\n");
      return [...settled, { text: details ? `${answer}\n\n${details}` : answer, isBot: true, type: 'output', chatFeedback: null, sessionId, userQuerySeqId: settled.filter(message => !message.isBot).length }];
    })
  }

  const NOTICE_LABEL = { refused: "Refused", abstained: "Abstained" };
  const NOTICE_HINT = {
    refused: "You may not read the data this needs. Ask the named business team or steward for access.",
    abstained: "No governed answer covers this. Try the catalogue's terms: a measure, a unit and a period.",
  };

  const handleStopServer = () => {
    clearInterval(intervalRef.current)
    setTime(0)
    pendingRef.current = false;
    setMessages(prevMessages => prevMessages.map(message => {
      if (message.type === 'loading') {
        return { text: "Processing is stopped, Please Try Again", isBot: true, type: 'output', chatFeedback: 'na', userQuerySeqId: null };
      }
      return message;
    }));
  };

  const showModal = (i, chatFeedback, userQuerySeqId) => {
    setOpen(true)
    setChatIndex(i)
    toggleChatFeedback(i, chatFeedback, userQuerySeqId)
  }

  function handleStopClosingChatClick(event) {
    // Prevent the event from propagating to the document level
    event.stopPropagation();
  }

  const closeModal = () => {
    setOpen(false)
    toggleChatFeedback(chatIndex, null, null)
    setChatIndex(null)
  }

  const handleEnter = (e) => {
      if (e.key === 'Enter' && !e.shiftKey && window.innerWidth > 800) {
        e.preventDefault();
        handleSend();
      }
  }

  const SQLMessageContent = (content, outputContent) => {
    // const formattedQuery = content
    const formattedQuery = format(content, { language: 'sql' });
    return (
      <Typography.Paragraph style={{ marginBottom: "0" }}>
        <div style={{ fontSize: "13px", color: "var(--v3-ink)" }}><Markdown>{ outputContent }</Markdown></div>
        <Collapse
          className="cot-collapse"
          items={[
            {
              key: "1",
              label: (
                <>
                  <DatabaseOutlined /> Database Query
                </>
              ),
              children: (
                <SyntaxHighlighter
                  language="sql_more"
                  style={atelierHeathLight}
                  showLineNumbers={true}
                >
                  {formattedQuery}
                </SyntaxHighlighter>
              ),
            },
          ]}
        />
      </Typography.Paragraph>
    );
  };

  function generateCSV(data) {
    // Generate CSV data
    // Convert data to CSV format
    const headers = Object.keys(data[0]);
    const rows = data.map(obj => headers.map(header => obj[header]));
    const csvData = [headers.join(','), ...rows.map(row => row.join(','))].join('\n');
  
    return csvData;
  }

  const handleDownloadClick = (data) => {  
    const csvData = generateCSV(data);
    const blob = new Blob([csvData], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
  
    const link = document.createElement('a');
    link.href = url;
    // use today's date and time to label the extract
    link.download = 'genai_response_extract_' + new Date().toISOString().slice(0, 19).replace(/:/g, "-") + '.csv';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  
  };

  const TableMessageContent = (content) => {
    const jsonVal = JSON.parse(content || "[]")['table'];
    const tableColumns = jsonVal?.columns.map((column, index) => {
      return {
        key: index,
        title: column.column_name,
        dataIndex: column.column_name,
        valueType: column.data_type
      };
    });
    const tableData = jsonVal?.rows.map((row, index) => {
      return {
        key: index,
        ...row,
      };
    });
    const pageSize = 10;
    const onClick = () => {
      handleDownloadClick(tableData);
    };
    return (
      <ConfigProvider locale={enUS}>
        <ProTable
          scroll={{ x: "max-content" }}
          size="small"
          columns={tableColumns}
          dataSource={tableData}
          search={false}
          options={false}
          toolBarRender={false}
          cardProps={false}
          pagination={tableData.length > pageSize && { pageSize }}
        />
        <div style={{textAlign: "right", marginTop: "6px"}}>
          <Button onClick={onClick} style={{ marginTop: "10px" }}>
              <DownloadOutlined />
          </Button>
        </div>
      </ConfigProvider>
    );
  };

  return (
    <div className={props.showChatbot ? 'show-chatbot' : ''} ref={hideChatRef}>
      <FeedbackForm open={open} onCreate={onCreate} onCancel={closeModal} chatFeedback={currentChatFeedback} userQuerySeqId={currentUserQuerySeqId} handleStopClosingChatClick={handleStopClosingChatClick} confirmLoading={confirmLoading} />
      {/* inert="" (React 18 passes unknown attributes through as strings) keeps the
          closed panel out of the tab order; chatbot.css also hides it visually. */}
      <aside
        className="chatbot"
        ref={panelRef}
        style={panelWidth ? { width: `${panelWidth}px` } : undefined}
        aria-label="Ask"
        aria-hidden={!props.showChatbot}
        inert={props.showChatbot ? undefined : ""}
      >
        <div
          className="chatbot-resizer"
          onMouseDown={startResize}
          onDoubleClick={resetWidth}
          title="Drag to resize, double-click to reset"
          aria-hidden="true"
        />
        <header>
          <h2>Ask</h2>
          <button
            type="button"
            className="icon-btn new-chat-btn"
            onClick={clearChat}
            title="Start a new chat (clears this scope's history)"
            aria-label="Start a new chat"
          >
            <span className="material-symbols-outlined" aria-hidden="true">restart_alt</span>
          </button>
          <button type="button" className="icon-btn close-btn" onClick={closeChatbot} aria-label="Close Ask">
            <span className="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        </header>
        {props.domainMode ? (
          <div className="ask-ctx">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
            SCOPED TO {props.scopeLabel || "THIS DOMAIN"} · ALL PRODUCTS
          </div>
        ) : scopedProductName ? (
          <div className="ask-ctx">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
            SCOPED TO {scopedProductName}
          </div>
        ) : null}
        <ul ref={msgEnd} className="chatbox" aria-live="polite">
          {contextHolder}
          {messages.map((message, i) =>
            <li key={i} className={ message.isBot && message.type !== 'table' ? 'chat incoming' : message.isBot && message.type === 'table' ? 'chat incoming with-table' :'chat outgoing'}>
              { message.isBot && message.type !== 'table' ? ( <span className='botfeedback'><span className="material-symbols-rounded botclass" aria-hidden="true">person</span>{ message.chatFeedback !== 'na' ? (<span className='feedback' role="group" aria-label="Was this answer helpful?"><button type="button" className='thumb likefeedback' aria-label="Helpful" aria-pressed={message.chatFeedback === 'like'} onClick={() => message.chatFeedback === 'like' ? toggleChatFeedback(i, null, null) : showModal(i, 'like', message.userQuerySeqId)}>{ message.chatFeedback === 'like' ? <LikeFilled style={{ color: 'var(--v3-accent)' }} /> : <LikeOutlined /> }</button><button type="button" className='thumb dislikefeedback' aria-label="Not helpful" aria-pressed={message.chatFeedback === 'dislike'} onClick={() => message.chatFeedback === 'dislike' ? toggleChatFeedback(i, null, null) : showModal(i, 'dislike', message.userQuerySeqId)}>{ message.chatFeedback === 'dislike' ? <DislikeFilled style={{ color: 'var(--v3-accent)' }} /> : <DislikeOutlined /> }</button></span>) : '' }</span> ) : ''}
              { message.type === 'sql' ? (<div className='chatmsg'>{ SQLMessageContent(message.text.sql, message.text.output) }</div>) : message.type === 'table' ? (<div className='chatmsg'>{ TableMessageContent(message.text) }</div>) : message.type === 'output' ? (<div className='chatmsg'><Markdown>{ message.text }</Markdown></div>) : message.type === 'loading' ? (<div className='chatmsg'><div style={{ display: "flex" }}><Spin style={{marginTop: "12px"}}></Spin><div style={{ marginTop: "10px", marginLeft: "7px" }}>{ message.text }</div></div></div>) : message.type === 'notice' ? (<div className={`chatmsg notice notice-${message.kind}`} role="note"><span className="notice-kind">{ NOTICE_LABEL[message.kind] || "Not answered" }</span><Markdown>{ message.text }</Markdown><span className="notice-hint">{ NOTICE_HINT[message.kind] }</span></div>) : message.type === 'error' ? (<div className='chatmsg error' role="alert"><Markdown>{ message.text }</Markdown></div>) : message.type === 'story' ? (<div className='chatmsg' style={{ marginTop: '10px' }}><Markdown>{ message.text }</Markdown></div>) : (<div className='chatmsg'><Markdown>{ message.text }</Markdown></div>) }
              { message.isBot && message.type === 'sql' ? (<CopyToClipboard text={messages[i-1].text.concat(" Answer: ", message.text.output.concat(" Database Query: ", message.text.sql))} onCopy={handleCopy}><Tooltip title={copied ? "Copied" : "Copy"} overlayStyle={{ zIndex: 1500 }}><button type="button" className="icon-btn copy-answer" aria-label={copied ? "Copied" : "Copy answer"}><span className="material-symbols-outlined" aria-hidden="true">{ copied ? "check_circle" : "content_copy"}</span></button></Tooltip></CopyToClipboard>) : message.isBot && message.type === 'output' ? (<CopyToClipboard text={messages[i-1].text.concat(" Answer: ",message.text)} onCopy={handleCopy}><Tooltip title={copied ? "Copied" : "Copy"} overlayStyle={{ zIndex: 1500 }}><button type="button" className="icon-btn copy-answer" aria-label={copied ? "Copied" : "Copy answer"}><span className="material-symbols-outlined" aria-hidden="true">{ copied ? "check_circle" : "content_copy"}</span></button></Tooltip></CopyToClipboard>) : message.isBot && message.type === 'loading' && time >= 120 ? (<Tooltip title="Stop generation" overlayStyle={{ zIndex: 1500 }}><button type="button" onClick={handleStopServer} className="icon-btn stop-answer" aria-label="Stop generation"><span className="material-symbols-outlined" aria-hidden="true">stop_circle</span></button></Tooltip>) : '' }
            </li>
          )}
          {messages.length === 1 && (
            <li className="ask-prompts" aria-label="Suggested questions">
              <div className="ask-prompts-lbl">Try one of these</div>
              {(props.domainMode ? DOMAIN_PROMPTS : SUGGESTED_PROMPTS).map((q, i) => (
                <button key={i} className="ask-prompt" onClick={() => handleSend(q)}>{q}</button>
              ))}
            </li>
          )}
        </ul>
        <div className="chat-input">
          {/* Feedback is optional: the next question never waits for a thumb. */}
          <textarea maxLength={300} ref={chatInputRef} aria-label="Your question" placeholder="Ask a question…" required value={input} onKeyDown={handleEnter} onChange={(e) => {setInput(e.target.value)}}></textarea>
          <button type="button" id="send-btn" aria-label="Send" onClick={() => handleSend()}>
            <span className="material-symbols-rounded" aria-hidden="true">arrow_upward</span>
          </button>
        </div>
      </aside>
    </div>
  );
}

export default Chatbot;
