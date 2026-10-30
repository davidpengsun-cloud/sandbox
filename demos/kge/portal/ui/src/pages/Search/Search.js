import React, { useEffect, useMemo, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import Header from "../../components/shared/Header/Header";
import Chatbot from "../../components/askportal/Chatbot";
import { KnowledgeMatches } from "../../components/KnowledgeStatus";
import V3ProductCard, { titleCaseName } from "../../components/shared/V3ProductCard/V3ProductCard";
import { showToast } from "../../components/shared/V3Toast/V3Toast";
import { filterSearchResults, getCommunityProducts, getSearchResults } from "../../services/search";
import { getFavouriteDataProducts, saveFavouriteProduct, removeFavouriteProduct, saveViewedDataProduct } from "../../services/user";

import { buildSearchUrl, parseSearchParams } from "../../features/search/searchUrl";

import { freshness, relTime } from "../../utils/time";
import { getFields, getProduct } from "../../services/metadata";
import {
  searchResults,
  filterOptions,
  sortOption,
  groupOption,
  searchParam,
  browseScope,
  domainFilter,
  communityFilter,
  sourceFilter,
  typeFilter,
  hierarchyList,
  domainSelected,
} from "../../features/search/searchSlice";
import "../../assets/styles/cx-explore.css";

const PAGE_SIZE = 24;

const SORTS = [
  { key: "", value: "Most relevant" },
  { key: "prdtNameA-Z", value: "Name: A → Z" },
  { key: "prdtNameZ-A", value: "Name: Z → A" },
  { key: "publishDateNew-Old", value: "Newest first" },
  { key: "publishDateOld-New", value: "Oldest first" },
];

const FACETS = [
  { key: "COMMUNITY", title: "Community", stateKey: "communityFilter" },
  { key: "DOMAIN", title: "Domain", stateKey: "domainFilter" },
  { key: "PRODUCTSOURCE", title: "Source", stateKey: "sourceFilter" },
  { key: "PRODUCTTYPE", title: "Object type", stateKey: "typeFilter" },
];

const COLUMNS = [
  { key: "NAME", label: "Data asset", get: (p) => p.PRODUCTDISPLAYNAME || p.PRODUCTNAME || "" },
  { key: "TIER", label: "Tier", get: (p) => ({ GOLD: 3, SILVER: 2, BRONZE: 1 }[String(p.BADGE_NAME || "").toUpperCase()] || 0), numeric: true },
  { key: "COMMUNITY", label: "Community", get: (p) => p.COMMUNITY || "" },
  { key: "LAST_REFRESH", label: "Loaded", get: (p) => p.LAST_REFRESH || "" },
  { key: "SUBSCRIBE_COUNT", label: "Saved", get: (p) => p.SUBSCRIBE_COUNT || 0, numeric: true },
];

const TIER_KEY = { GOLD: "g", SILVER: "s", BRONZE: "b" };
const TierChip = ({ b }) => {
  const t = String(b || "").toUpperCase();
  const k = TIER_KEY[t] || "n";
  return (
    <span className={`cx-tier cx-tier--${k}`}>
      <i aria-hidden="true" />
      {TIER_KEY[t] ? t : "NONE"}
    </span>
  );
};

export default function Search() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const paramKey = params.toString();
  const spec = useMemo(() => parseSearchParams(params), [paramKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const facetState = {
    communityFilter: useSelector((state) => state.search.communityFilter) || [],
    domainFilter: useSelector((state) => state.search.domainFilter) || [],
    sourceFilter: useSelector((state) => state.search.sourceFilter) || [],
    typeFilter: useSelector((state) => state.search.typeFilter) || [],
  };
  const selectedFilters = spec.filters;
  const sortParamOption = spec.sort;
  const searchValue = spec.community ? spec.community : spec.q;

  const [results, setResults] = useState([]);
  const [baseList, setBaseList] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [reloadKey, setReloadKey] = useState(0);
  const [view, setView] = useState("table");
  const [selectedId, setSelectedId] = useState(null);
  const [inspect, setInspect] = useState({ id: null, product: null, fields: [], status: "idle" });
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [tableSort, setTableSort] = useState({ key: null, dir: 1 });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [collapsedFacets, setCollapsedFacets] = useState({});
  const [showAsk, setShowAsk] = useState(false);
  const baseCache = useRef({ key: null, data: null });
  const requestId = useRef(0);

  /* lock body scroll while the mobile filter drawer is open */
  useEffect(() => {
    document.body.style.overflow = filtersOpen ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [filtersOpen]);

  /* Load whenever the URL changes (or Try again is pressed). The base result set
     (which also carries the facet options) is cached per query, so changing sort
     or filters only calls the filter endpoint. */
  useEffect(() => {
    const id = ++requestId.current;
    const stale = () => id !== requestId.current;
    const track = !!(location.state && location.state.track);
    setStatus("loading");

    const loadBase = () => {
      if (baseCache.current.key === spec.baseKey && baseCache.current.data) {
        return Promise.resolve(baseCache.current.data);
      }
      const call = spec.community
        ? getCommunityProducts({ searchParam: spec.community, domain: spec.domain, isDomain: false })
        : getSearchResults({ searchParam: spec.q || null });
      return call.then((res) => {
        if (!res || !res.data || !Array.isArray(res.data.searchList)) throw new Error("search failed");
        baseCache.current = { key: spec.baseKey, data: res.data };
        return res.data;
      });
    };

    const needsFilter = spec.filters.length > 0;
    Promise.all([loadBase(), getFavouriteDataProducts()])
      .then(([base, favRes]) => {
        const favs = Array.isArray(favRes?.data) ? favRes.data : [];
        const narrowed = needsFilter
          ? filterSearchResults({
              searchParam: spec.community || spec.q || null,
              groupParam: "",
              sortParam: spec.sort,
              filterParams: spec.filters,
              isDomain: false,
              ...(spec.community ? { browseScope: { community: spec.community, domain: spec.domain } } : {}),
            }).then((res) => {
              if (!res || !Array.isArray(res.data)) throw new Error("filter failed");
              return res.data;
            })
          : Promise.resolve(base.searchList);
        return narrowed.then((list) => ({ base, favs, list }));
      })
      .then(({ base, favs, list }) => {
        if (stale()) return;
        const mapped = list.map((item) => ({
          ...item,
          FAVOURITED: favs.some((d) => d.DATA_PRODUCT_ID === item.PRODUCTID),
        }));
        setResults(mapped);
        setBaseList(base.searchList);
        setVisible(PAGE_SIZE);
        setStatus("ready");
        /* Keep the detail page and legacy components in sync. */
        dispatch(searchResults(mapped));
        dispatch(searchParam(spec.community || spec.q || null));
        dispatch(browseScope(spec.community ? { community: spec.community, domain: spec.domain } : null));
        dispatch(domainSelected(false));
        dispatch(filterOptions(spec.filters));
        dispatch(sortOption(spec.sort));
        dispatch(groupOption(""));
        dispatch(domainFilter(base.domainFilter || []));
        dispatch(communityFilter(base.communityFilter || []));
        dispatch(sourceFilter(base.sourceFilter || []));
        dispatch(typeFilter(base.typeFilter || []));
        dispatch(hierarchyList(base.hierarchy || []));
        if (track) {
          const word = spec.q || spec.community;
          if (word) {
            saveViewedDataProduct({
              productId: 0,
              productName: null,
              searchWord: word,
              category: base.searchList.length === 0 ? "Invalid" : "Valid",
              tab: "SEARCH",
            });
          }
          /* drop the one-shot flag so a refresh does not log the search again */
          navigate(`${location.pathname}${location.search}`, { replace: true, state: null });
        }
      })
      .catch(() => {
        if (!stale()) setStatus("error");
      });
  }, [paramKey, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Filter changes create history entries so Back restores the previous view. */
  const updateUrl = (changes) => setParams(new URLSearchParams(buildSearchUrl({ ...spec, ...changes }).split("?")[1] || ""));

  const isSelected = (opt) => selectedFilters.some((f) => f.parent === opt.parent && f.value === opt.value);

  const toggleFilter = (opt) => {
    const next = isSelected(opt)
      ? selectedFilters.filter((f) => !(f.parent === opt.parent && f.value === opt.value))
      : [...selectedFilters, { parent: opt.parent, value: opt.value, label: opt.label }];
    updateUrl({ filters: next });
  };

  const onSortChange = (e) => updateUrl({ sort: e.target.value });

  const clearAll = () => {
    updateUrl({ filters: [], sort: "", tier: "", fresh: false });
    showToast("Filters cleared");
  };

  const retry = () => {
    baseCache.current = { key: null, data: null };
    setReloadKey((k) => k + 1);
  };

  const toggleFavourite = (product) => {
    const params = {
      productId: product.PRODUCTID,
      applicationId: product.APPLICATION_IDENTIFIER,
      technicalName: product.PRODUCTNAME,
    };
    const name = product.PRODUCTDISPLAYNAME || product.PRODUCTNAME || "Product";
    const update = (value) => {
      const next = results.map((item) =>
        item.PRODUCTID === product.PRODUCTID
          ? {
              ...item,
              FAVOURITED: value,
              SUBSCRIBE_COUNT: value ? (item.SUBSCRIBE_COUNT || 0) + 1 : Math.max((item.SUBSCRIBE_COUNT || 0) - 1, 0),
            }
          : item
      );
      setResults(next);
      dispatch(searchResults(next));
      showToast(value ? `${name} saved to favourites` : `${name} removed from favourites`);
    };
    const failed = () => showToast(`Couldn't update favourites for ${name}. Try again.`);
    const call = product.FAVOURITED ? removeFavouriteProduct(params) : saveFavouriteProduct(params);
    call
      .then((res) => (res && res.status !== "Fail" ? update(!product.FAVOURITED) : failed()))
      .catch(failed);
  };

  const openProduct = (id) => id && navigate(`/productDetails/${id}`);

  /* client-side table sorting */
  const sortForTable = (list) => {
    if (!tableSort.key) return list;
    const col = COLUMNS.find((c) => c.key === tableSort.key);
    if (!col) return list;
    return [...list].sort((a, b) => {
      const av = col.get(a);
      const bv = col.get(b);
      if (col.numeric) return (Number(av) - Number(bv)) * tableSort.dir;
      return String(av).localeCompare(String(bv), undefined, { sensitivity: "base" }) * tableSort.dir;
    });
  };

  const onHeaderSort = (key) => {
    setTableSort((s) => (s.key === key ? { key, dir: -s.dir } : { key, dir: 1 }));
  };

  /* quick filters (tier / fresh) narrow the server results in the browser */
  const tierOf = (p) => String(p.BADGE_NAME || "").toUpperCase();
  const tierCounts = ["GOLD", "SILVER", "BRONZE"].reduce((acc, t) => ({ ...acc, [t]: results.filter((p) => tierOf(p) === t).length }), {});
  const freshCount = results.filter((p) => freshness(p.LAST_REFRESH) === "fresh").length;
  const display = results.filter(
    (p) => (!spec.tier || tierOf(p) === spec.tier) && (!spec.fresh || freshness(p.LAST_REFRESH) === "fresh")
  );
  /* facet option counts from the unfiltered result set for this query */
  const facetCount = (opt) => baseList.filter((p) => p[opt.parent] === opt.value).length;

  const activeFacetCount = selectedFilters.length + (spec.tier ? 1 : 0) + (spec.fresh ? 1 : 0);
  const shown = Math.min(visible, display.length);
  const sorted = (() => {
    if (spec.sort === "prdtNameA-Z" || spec.sort === "prdtNameZ-A")
      return [...display].sort((a, b) => String(a.PRODUCTDISPLAYNAME || a.PRODUCTNAME || "").localeCompare(String(b.PRODUCTDISPLAYNAME || b.PRODUCTNAME || ""), undefined, { sensitivity: "base" }) * (spec.sort === "prdtNameA-Z" ? 1 : -1));
    if (spec.sort === "publishDateNew-Old" || spec.sort === "publishDateOld-New")
      return [...display].sort((a, b) => ((Date.parse(a.PUBLISHDATE || a.LAST_REFRESH) || 0) - (Date.parse(b.PUBLISHDATE || b.LAST_REFRESH) || 0)) * (spec.sort === "publishDateOld-New" ? 1 : -1));
    return display;
  })();
  const tableRows = sortForTable(sorted);


  /* ---- selection + inspector (right pane) ---- */
  const selected = display.find((p) => String(p.PRODUCTID) === String(selectedId)) || display[0] || null;
  useEffect(() => {
    const id = selected && selected.PRODUCTID;
    if (!id || selected.TYPE === "column" || id === inspect.id) return;
    let live = true;
    setInspect({ id, product: null, fields: [], status: "loading" });
    Promise.all([getProduct(id), getFields(id)])
      .then(([pr, fr]) => {
        if (!live) return;
        setInspect({
          id,
          product: pr && pr.data && !Array.isArray(pr.data) ? pr.data : null,
          fields: Array.isArray(fr?.data) ? fr.data : [],
          status: pr ? "ready" : "error",
        });
      })
      .catch(() => live && setInspect({ id, product: null, fields: [], status: "error" }));
    return () => {
      live = false;
    };
  }, [selected && selected.PRODUCTID]); // eslint-disable-line react-hooks/exhaustive-deps

  /* keyboard: ↑/↓ move, Enter opens, inside the results table */
  const onResultsKey = (e) => {
    if (e.target.closest("button, input, select")) return;
    if (!display.length || (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Enter")) return;
    const rows = view === "table" ? tableRows : sorted;
    const i = Math.max(0, rows.findIndex((p) => selected && p.PRODUCTID === selected.PRODUCTID));
    if (e.key === "Enter") {
      if (selected) openProduct(selected.PRODUCTID);
      return;
    }
    e.preventDefault();
    const next = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      setSelectedId(next.PRODUCTID);
      const el = document.getElementById(`cx-row-${next.PRODUCTID}`);
      if (el) el.focus();
    }
  };

  const toggleFacet = (key) => {
    setCollapsedFacets((previous) => ({ ...previous, [key]: !previous[key] }));
  };
  const facetGroup = (key, title, count, content) => {
    const collapsed = Boolean(collapsedFacets[key]);
    const choices = count === 1 ? "choice" : "choices";
    return (
      <div className="cx-facet" key={key}>
        <h4 className="cx-facet-heading">
          <button
            type="button"
            className="cx-facet-toggle"
            aria-label={`${title}, ${count} available ${choices}, ${collapsed ? "expand" : "collapse"} filters`}
            aria-expanded={!collapsed}
            onClick={() => toggleFacet(key)}
          >
            <span>{title}</span>
            <span className="cx-facet-toggle-meta">
              <span className="cx-facet-choice-count">{count}</span>
              <span className="cx-facet-toggle-icon" aria-hidden="true">{collapsed ? "+" : "−"}</span>
            </span>
          </button>
        </h4>
        <div className="cx-facet-options" hidden={collapsed}>{content}</div>
      </div>
    );
  };

  const facetPanel = (
    <>
      {facetGroup("tier", "Tier", 3, ["GOLD", "SILVER", "BRONZE"].map((t) => (
        <label key={t} className={tierCounts[t] === 0 ? "is-empty" : ""}>
          <input type="checkbox" checked={spec.tier === t} disabled={tierCounts[t] === 0} onChange={() => updateUrl({ tier: spec.tier === t ? "" : t })} />
          <TierChip b={t} />
          <span className="cx-facet-count cx-mono">{tierCounts[t]}</span>
        </label>
      )))}
      {facetGroup("freshness", "Freshness", 2, <>
        <label>
          <input type="radio" name="cx-fresh" checked={!spec.fresh} onChange={() => updateUrl({ fresh: false })} />
          <span className="cx-facet-name">Any</span>
          <span className="cx-facet-count cx-mono">{results.length}</span>
        </label>
        <label className={freshCount === 0 ? "is-empty" : ""}>
          <input type="radio" name="cx-fresh" checked={spec.fresh} disabled={freshCount === 0} onChange={() => updateUrl({ fresh: true })} />
          <span className="cx-facet-name">Loaded in 7 days</span>
          <span className="cx-facet-count cx-mono">{freshCount}</span>
        </label>
      </>)}
      {FACETS.map((facet) => {
        const options = facetState[facet.stateKey];
        const content = options.length === 0 ? (
          <div className="cx-facet-empty">No options</div>
        ) : (
          options.map((opt, i) => (
            <label key={i}>
              <input type="checkbox" checked={isSelected(opt)} onChange={() => toggleFilter(opt)} />
              <span className="cx-facet-name">{opt.label}</span>
              {baseList.length > 0 && <span className="cx-facet-count cx-mono">{facetCount(opt)}</span>}
            </label>
          ))
        );
        return facetGroup(facet.key, facet.title, options.length, content);
      })}
    </>
  );

  const skeletonRows = Array.from({ length: 8 }).map((_, i) => (
    <tr key={i} aria-hidden="true">
      {COLUMNS.map((c, j) => (
        <td key={j}><span className="v3-skel" style={{ height: 10, width: j === 0 ? "70%" : "55%" }} /></td>
      ))}
    </tr>
  ));

  const chips = [
    ...(spec.tier ? [{ label: `Tier: ${spec.tier}`, clear: () => updateUrl({ tier: "" }) }] : []),
    ...(spec.fresh ? [{ label: "Loaded in 7 days", clear: () => updateUrl({ fresh: false }) }] : []),
    ...selectedFilters.map((f) => ({ label: f.label, clear: () => toggleFilter(f) })),
  ];

  /* Ask is scoped to the data assets in view: the panel is labelled by their
     community or domain when there is one, else by the search or the whole view. */
  const uniqueOf = (getter) => [...new Set(display.map(getter).filter(Boolean))];
  const askCommunities = uniqueOf((p) => p.COMMUNITY);
  const askDomains = uniqueOf((p) => p.DOMAIN);
  const askLabel =
    askCommunities.length === 1
      ? askCommunities[0]
      : askDomains.length === 1
      ? askDomains[0]
      : searchValue || "Current results";
  const askProductIds = display.map((p) => p.PRODUCTID).filter(Boolean);

  const ip = inspect.product;
  const sel = selected;
  const selFresh = sel ? freshness(sel.LAST_REFRESH) : "unknown";

  return (
    <>
      <Header type="other" toggleChatbot={() => setShowAsk((openNow) => !openNow)} askScope={askLabel} askOpen={showAsk} />
      <main className="cx-explore">
        <div className="cx-ex-head">
          <div className="cx-ex-title">
            <nav className="cx-crumbs" aria-label="Breadcrumb">
              <button type="button" onClick={() => navigate("/home")}>Workspace</button>
              <span aria-hidden="true">/</span>
              <button type="button" onClick={() => navigate(buildSearchUrl({}))}>Explore</button>
              {searchValue && (<><span aria-hidden="true">/</span><span className="here">{searchValue}</span></>)}
            </nav>
            <div className="cx-ex-title-line">
              <h1>
                {spec.q ? <>Results for <q>{spec.q}</q></> : spec.community ? spec.community : "All data assets"}
              </h1>
              <KnowledgeMatches />
            </div>
            <span className="cx-ex-count cx-mono" aria-live="polite">
              {status === "ready" ? (display.length !== results.length ? `${display.length} of ${results.length}` : `${results.length}`) : "—"} data assets
            </span>
          </div>
          <div className="cx-ex-actions">
            <button type="button" className="cx-btn cx-ex-filters" onClick={() => setFiltersOpen(true)} aria-haspopup="dialog" aria-expanded={filtersOpen}>
              Filters{activeFacetCount > 0 ? ` · ${activeFacetCount}` : ""}
            </button>
            <div className="cx-seg" role="group" aria-label="Layout">
              <button type="button" aria-pressed={view === "table"} onClick={() => setView("table")}>Table</button>
              <button type="button" aria-pressed={view === "cards"} onClick={() => setView("cards")}>Cards</button>
            </div>
            <label className="cx-sort">
              <span>Sort</span>
              <select aria-label="Sort results" value={sortParamOption} onChange={onSortChange}>
                {SORTS.map((o) => (
                  <option key={o.key} value={o.key}>{o.value}</option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <div className="cx-ex-grid">
          <aside className="cx-panel cx-facets" aria-label="Filters">{facetPanel}</aside>

          <section className="cx-panel cx-results" aria-label="Results">
            <div className="cx-results-bar">
              <div className="cx-chips">
                {chips.length === 0 ? (
                  <span className="cx-muted">No filters</span>
                ) : (
                  <>
                    {chips.map((c, i) => (
                      <span className="cx-chip" key={i}>
                        {c.label}
                        <button type="button" aria-label={`Remove ${c.label}`} onClick={c.clear}>×</button>
                      </span>
                    ))}
                    <button type="button" className="cx-link" onClick={clearAll}>Clear all</button>
                  </>
                )}
              </div>
              <span className="cx-kbd-hint cx-mono">↑↓ move · ↵ open</span>
            </div>

            {status === "error" ? (
              <div className="cx-pad">
                <div role="alert"><b>Results didn't load</b><p>The search service didn't respond.</p><button type="button" className="cx-btn" onClick={retry}>Try again</button></div>
              </div>
            ) : status === "ready" && display.length === 0 ? (
              <div className="cx-noresults">
                <h4>No data assets match</h4>
                <p>Try a different search term, or clear the filters to see more.</p>
                {(chips.length > 0 || sortParamOption) ? (
                  <button type="button" className="cx-btn" onClick={clearAll}>Clear filters</button>
                ) : (
                  <button type="button" className="cx-btn" onClick={() => navigate(buildSearchUrl({}))}>Browse the full catalog</button>
                )}
              </div>
            ) : view === "table" || status === "loading" ? (
              <div className="cx-table-wrap" onKeyDown={onResultsKey}>
                <table className="cx-table">
                  <thead>
                    <tr>
                      {COLUMNS.map((col) => (
                        <th key={col.key} aria-sort={tableSort.key === col.key ? (tableSort.dir === 1 ? "ascending" : "descending") : "none"} className={col.numeric && col.key !== "TIER" ? "cx-num" : ""}>
                          <button type="button" className="cx-th-sort" onClick={() => onHeaderSort(col.key)}>
                            {col.label}
                            <span aria-hidden="true">{tableSort.key === col.key ? (tableSort.dir === 1 ? "▲" : "▼") : ""}</span>
                          </button>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {status === "loading"
                      ? skeletonRows
                      : tableRows.slice(0, visible).map((p, i) => {
                          const isSel = sel && p.PRODUCTID === sel.PRODUCTID;
                          const f = freshness(p.LAST_REFRESH);
                          return (
                            <tr
                              key={p.PRODUCTID ?? i}
                              id={`cx-row-${p.PRODUCTID}`}
                              tabIndex={0}
                              className={isSel ? "is-selected" : ""}
                              aria-selected={isSel}
                              onClick={() => setSelectedId(p.PRODUCTID)}
                              onDoubleClick={() => p.TYPE !== "column" && openProduct(p.PRODUCTID)}
                              onFocus={() => setSelectedId(p.PRODUCTID)}
                            >
                              <td className="cx-td-name">
                                <b>{titleCaseName(p.PRODUCTDISPLAYNAME || p.PRODUCTNAME)}</b>
                                <span className="cx-mono">{[p.PRODUCTNAME, p.PRODUCTTYPE].filter(Boolean).join(" · ")}</span>
                              </td>
                              <td><TierChip b={p.BADGE_NAME} /></td>
                              <td className="cx-muted">{p.COMMUNITY}</td>
                              <td><span className={`cx-status cx-${f === "fresh" ? "ok" : f === "aging" ? "warn" : f === "stale" ? "bad" : "muted"}`}>{relTime(p.LAST_REFRESH) || "—"}</span></td>
                              <td className="cx-mono cx-num">{p.SUBSCRIBE_COUNT || 0}</td>
                            </tr>
                          );
                        })}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="cx-cards">
                {sorted.slice(0, visible).map((p, i) => (
                  <V3ProductCard key={p.PRODUCTID ?? i} product={p} onToggleFavourite={toggleFavourite} />
                ))}
              </div>
            )}
            {status === "ready" && shown < display.length && (
              <div className="cx-pf cx-loadmore">
                <button type="button" className="cx-btn" onClick={() => setVisible((v) => v + PAGE_SIZE)}>
                  Load {Math.min(PAGE_SIZE, display.length - shown)} more
                </button>
                <span className="cx-mono cx-muted">Showing {shown} of {display.length}</span>
              </div>
            )}
          </section>

          <aside className="cx-panel cx-inspector" aria-label="Preview" aria-live="polite">
            {status === "error" || (status === "ready" && !sel) ? (
              <div className="cx-pad cx-muted">Select a data asset to see its preview.</div>
            ) : status !== "ready" ? (
              <div className="cx-pad">
                <span className="v3-skel" style={{ height: 12, width: "40%", marginBottom: 12 }} />
                <span className="v3-skel" style={{ height: 20, width: "70%", marginBottom: 10 }} />
                <span className="v3-skel" style={{ height: 12, width: "90%" }} />
              </div>
            ) : (
              <>
                <div className="cx-ins-head">
                  <div className="cx-ins-meta"><TierChip b={sel.BADGE_NAME} /><span>{sel.COMMUNITY}</span></div>
                  <h2>{titleCaseName(sel.PRODUCTDISPLAYNAME || sel.PRODUCTNAME)}</h2>
                  <span className="cx-mono cx-muted cx-ins-tech">{ip && ip.SCHEMANAME ? `${ip.SCHEMANAME}.${sel.PRODUCTNAME}` : sel.PRODUCTNAME}</span>
                  {sel.DESCRIPTION && <p>{sel.DESCRIPTION}</p>}
                  <div className="cx-ins-actions">
                    <button type="button" className="cx-btn cx-btn--primary" onClick={() => openProduct(sel.PRODUCTID)}>Open</button>
                    <button type="button" className="cx-btn" aria-pressed={!!sel.FAVOURITED} onClick={() => toggleFavourite(sel)}>
                      {sel.FAVOURITED ? "Saved" : "Save"}
                    </button>
                  </div>
                </div>
                <div className="cx-ins-stats">
                  <div><span>Rows</span><b className="cx-mono">{ip && ip.COUNTOFRECORDS != null ? Number(ip.COUNTOFRECORDS).toLocaleString() : "—"}</b></div>
                  <div><span>Fields</span><b className="cx-mono">{ip && ip.COUNTOFCOLUMNS != null ? ip.COUNTOFCOLUMNS : inspect.fields.length || "—"}</b></div>
                  <div><span>Loaded</span><b className={`cx-mono cx-${selFresh === "fresh" ? "ok" : selFresh === "aging" ? "warn" : selFresh === "stale" ? "bad" : "muted"}`}>{relTime(sel.LAST_REFRESH) || "—"}</b></div>
                </div>
                <div className="cx-ins-fields">
                  <h4>Fields</h4>
                  {inspect.status === "loading" && <span className="v3-skel" style={{ height: 10, width: "80%" }} />}
                  {inspect.status === "error" && <span className="cx-muted">Field list didn't load.</span>}
                  {inspect.fields.slice(0, 8).map((f, i) => (
                    <div key={i} className="cx-ins-field">
                      <span className="cx-mono">{f["Field Name"] || f.FIELD_NAME || Object.values(f)[0]}</span>
                      <span className="cx-mono cx-muted">{f["Data Type"] || f.DATA_TYPE || ""}</span>
                    </div>
                  ))}
                  {inspect.fields.length > 8 && (
                    <button type="button" className="cx-link" onClick={() => openProduct(sel.PRODUCTID)}>All {inspect.fields.length} fields →</button>
                  )}
                </div>
              </>
            )}
          </aside>
        </div>

        {/* mobile filter drawer */}
        <div className={`cx-scrim ${filtersOpen ? "open" : ""}`} onClick={() => setFiltersOpen(false)} aria-hidden="true" />
        <div className={`cx-drawer ${filtersOpen ? "open" : ""}`} role="dialog" aria-modal="true" aria-label="Filters" aria-hidden={!filtersOpen} inert={filtersOpen ? undefined : ""}>
          <div className="cx-drawer-head">
            <h2>Filters</h2>
            <button type="button" onClick={() => setFiltersOpen(false)} aria-label="Close filters">×</button>
          </div>
          <div className="cx-drawer-body">{facetPanel}</div>
          <div className="cx-drawer-foot">
            <button type="button" className="cx-btn" onClick={clearAll}>Clear all</button>
            <button type="button" className="cx-btn cx-btn--primary" onClick={() => setFiltersOpen(false)}>Show {display.length} results</button>
          </div>
        </div>

        <Chatbot domainMode productIds={askProductIds} scopeLabel={askLabel} showChatbot={showAsk} setShowChatbot={setShowAsk} />
      </main>
    </>
  );
}
