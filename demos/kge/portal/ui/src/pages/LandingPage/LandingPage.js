import React, { useEffect, useState } from "react";
import { useDispatch } from "react-redux";
import { CssBaseline } from "@material-ui/core";
import { useNavigate } from "react-router-dom";
import Header from "../../components/shared/Header/Header";
import PortalSearch from "../../components/shared/PortalSearch";
import { useBrand } from "../../components/shared/Brand/Brand";
import { buildSearchUrl } from "../../features/search/searchUrl";
import V3ProductCard, { titleCaseName } from "../../components/shared/V3ProductCard/V3ProductCard";
import GoldBadge from "../../assets/icons/gold.png";
import SilverBadge from "../../assets/icons/silver.png";
import BronzeBadge from "../../assets/icons/bronze.png";
import {
  getFavouriteDataProducts,
  getRelatedDataProducts,
  getViewedDataProducts,
  getConfig,
  getDataCommunities,
  saveViewedDataProduct,
} from "../../services/user";
import { getSearchResults, getCommunityProducts } from "../../services/search";
import {
  searchResults,
  searchParam,
  browseScope,
  domainFilter,
  communityFilter,
  sourceFilter,
  typeFilter,
  hierarchyList,
  groupOption,
  sortOption,
  filterOptions,
  domainSelected,
} from "../../features/search/searchSlice";
import "../../assets/styles/v3-home.css";

/* ---- helpers ---- */
/* Counts are displayed separately from community names. */
const cleanName = (n) => String(n || "").replace(/\s*\(\d+\)\s*$/, "");

const normalise = (item) => ({
  ...item,
  PRODUCTID: item.PRODUCTID ?? item.DATA_PRODUCT_ID ?? item.PRODUCT_ID,
  PRODUCTNAME: item.PRODUCTNAME ?? item.PRODUCT_NAME ?? item.TECHNICALNAME,
  PRODUCTDISPLAYNAME: item.PRODUCTDISPLAYNAME ?? item.PRODUCT_DISPLAY_NAME ?? item.PRODUCTNAME ?? item.PRODUCT_NAME,
  // the viewed/related feeds carry these under different keys — map them so the
  // card shows the object type and community chip instead of a bare grey card.
  PRODUCTTYPE: item.PRODUCTTYPE ?? item.DATA_PRODUCT_TYPE,
  COMMUNITY: item.COMMUNITY ?? item.BUSINESS_APPLICATION_NAME,
  SUBSCRIBE_COUNT: item.SUBSCRIBE_COUNT ?? item.SUBSCRIBERS,
});

const asList = (data) => (Array.isArray(data) ? data : Array.isArray(data?.searchList) ? data.searchList : []);

export default function LandingPage() {
  const dispatch = useDispatch();
  const navigate = useNavigate();
  const [favouriteProducts, setFavouriteProducts] = useState([]);
  const [relatedDataProducts, setRelatedDataProducts] = useState([]);
  const [viewedDataProducts, setViewedDataProducts] = useState([]);
  const [communities, setCommunities] = useState([]);
  const [totalCount, setTotalCount] = useState(null);
  const [configValues, setConfigValues] = useState({});
  const [ready, setReady] = useState(false);
  const [heroTerm, setHeroTerm] = useState("");
  const brand = useBrand();

  useEffect(() => {
    loadPageData();
  }, []);

  const loadPageData = () => {
    getFavouriteDataProducts().then((res) => res?.data && setFavouriteProducts(asList(res.data))).catch((e) => console.log(e));
    getRelatedDataProducts().then((res) => res?.data && setRelatedDataProducts(asList(res.data))).catch((e) => console.log(e));
    getViewedDataProducts().then((res) => res?.data && setViewedDataProducts(asList(res.data))).catch((e) => console.log(e));
    getConfig().then((res) => {
      if (res?.data) {
        setConfigValues(res.data);
      }
    }).catch((e) => console.log(e));
    getDataCommunities()
      .then((res) => {
        if (res?.data) {
          setCommunities(res.data.communityList || []);
          setTotalCount(res.data.totalCount ?? null);
        }
      })
      .catch((e) => console.log(e))
      .finally(() => setReady(true));
  };

  const runTextSearch = (value) => {
    const val = value && value.trim() ? value.trim() : null;
    dispatch(groupOption(""));
    dispatch(sortOption(""));
    dispatch(filterOptions([]));
    dispatch(domainSelected(false));
    dispatch(searchParam(val));
    Promise.all([getSearchResults({ searchParam: val }), getFavouriteDataProducts()])
      .then((data) => {
        if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
          console.log("Error", data);
        } else {
          const result = data[0].data.searchList.map((item) => ({
            ...item,
            FAVOURITED: data[1].data.find((d) => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false,
          }));
          dispatch(searchResults(result));
          dispatch(domainFilter(data[0].data.domainFilter));
          dispatch(communityFilter(data[0].data.communityFilter));
          dispatch(sourceFilter(data[0].data.sourceFilter));
          dispatch(typeFilter(data[0].data.typeFilter));
          dispatch(hierarchyList(data[0].data.hierarchy));
        }
        navigate(buildSearchUrl({ q: val }));
      })
      .catch((error) => console.log(error));
  };

  const openCommunity = (node) => {
    dispatch(searchParam(node.val));
    dispatch(browseScope({ community: node.val, domain: node.domain || "" }));
    dispatch(domainSelected(false));
    Promise.all([
      getCommunityProducts({ searchParam: node.val, isDomain: false, domain: node.domain }),
      getFavouriteDataProducts(),
    ])
      .then((data) => {
        if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
          console.log("Error", data);
        } else {
          saveViewedDataProduct({ productId: 0, productName: null, searchWord: node.val, category: "Valid", tab: "SEARCH" });
          const result = data[0].data.searchList.map((item) => ({
            ...item,
            FAVOURITED: data[1].data.find((d) => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false,
          }));
          dispatch(searchResults(result));
          dispatch(domainFilter(data[0].data.domainFilter));
          dispatch(communityFilter(data[0].data.communityFilter));
          dispatch(sourceFilter(data[0].data.sourceFilter));
          dispatch(typeFilter(data[0].data.typeFilter));
          dispatch(hierarchyList(data[0].data.hierarchy));
        }
        navigate(buildSearchUrl({ community: node.val, domain: node.domain }));
      })
      .catch((error) => console.log(error));
  };

  const domainCards = communities.map((node) => ({ node, count: node.count ?? 0 }));
  const maxDomain = domainCards.reduce((m, d) => Math.max(m, d.count), 0) || 1;
  const domainFilters = communities.reduce((domains, node) => {
    const domain = String(node.domain || "").trim();
    if (domain) domains.set(domain, (domains.get(domain) || 0) + (Number(node.count) || 0));
    return domains;
  }, new Map());

  const title = brand.name;
  const desc =
    configValues.Application_Desc ||
    "Search governed data assets, see how fresh and trustworthy each one is, and request access from the people who own it.";

  const badgeIcon = (b) =>
    ({ GOLD: GoldBadge, SILVER: SilverBadge, BRONZE: BronzeBadge }[(b || "").toUpperCase()] || null);

  const savedItems = favouriteProducts.map(normalise);

  return (
    <React.Fragment>
      <CssBaseline />
      <Header type="home" />
      <main>
        <div className="v3-home">
          {/* HERO */}
          <section className="hero">
            <div className="hero-in">
              <div className="eyebrow">{configValues.Community_Title || "Governed catalog"}</div>
              <h1>{title}</h1>
              <p>{desc}</p>
              <PortalSearch className="hero-search" showShortcut={false} onTermChange={setHeroTerm} />
            </div>
            {domainFilters.size > 0 && (
              <div className="hero-domain-filters" role="group" aria-label="Filter by domain">
                {[...domainFilters.entries()].map(([domain, count]) => (
                  <button
                    key={domain}
                    className="hero-domain"
                    aria-label={`Filter by domain ${domain}`}
                    onClick={() => navigate(buildSearchUrl({ q: heroTerm.trim(), filters: [{ parent: "DOMAIN", value: domain, label: domain }] }))}
                  >
                    <span>{domain}</span>
                    <span className="hero-domain-count">{count}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="tape">
              <div className="tape-cell">
                <div className="lbl">Data assets</div>
                <div className="tape-val">{totalCount ?? "—"}</div>
                <div className="tape-sub">ACROSS {communities.length || "—"} COMMUNITIES</div>
              </div>
              <div className="tape-cell">
                <div className="lbl">Most viewed</div>
                <div className="tape-val live">{viewedDataProducts.length || "—"}</div>
                <div className="tape-sub">POPULAR RIGHT NOW</div>
              </div>
              <div className="tape-cell">
                <div className="lbl">Related to you</div>
                <div className="tape-val live">{relatedDataProducts.length || "—"}</div>
                <div className="tape-sub">FROM YOUR ACTIVITY</div>
              </div>
              <div className="tape-cell">
                <div className="lbl">Saved</div>
                <div className="tape-val">{savedItems.length || "0"}</div>
                <div className="tape-sub">YOUR FAVOURITES</div>
              </div>
            </div>
          </section>

          {!ready ? (
            <div aria-busy="true" aria-label="Loading portal">
              <div className="sect-head"><span className="v3-skel" style={{ height: 18, width: 190 }} /></div>
              <div className="domains">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div className="domain" key={i} style={{ pointerEvents: "none" }}>
                    <span className="v3-skel" style={{ height: 15, width: "55%" }} />
                    <span className="v3-skel" style={{ height: 11, width: "40%" }} />
                    <span className="v3-skel" style={{ height: 3, width: "100%" }} />
                    <span className="v3-skel" style={{ height: 12, width: "85%" }} />
                  </div>
                ))}
              </div>
              <div className="sect-head"><span className="v3-skel" style={{ height: 18, width: 140 }} /></div>
              <div className="grid">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div className="card" key={i} style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 170 }}>
                    <span className="v3-skel" style={{ height: 20, width: 20 }} />
                    <span className="v3-skel" style={{ height: 15, width: "70%" }} />
                    <span className="v3-skel" style={{ height: 12, width: "95%" }} />
                    <span className="v3-skel" style={{ height: 12, width: "60%" }} />
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <>
              {/* BROWSE BY COMMUNITY */}
              {domainCards.length > 0 && (
                <>
                  <div className="sect-head">
                    <h2>Browse by community</h2>
                    <span className="more" onClick={() => runTextSearch("")}>See all →</span>
                  </div>
                  <div className="domains">
                    {domainCards.map((d, i) => (
                      <button key={i} className="domain" onClick={() => openCommunity(d.node)}>
                        <span className="domain-eyebrow">Community</span>
                        <b>{cleanName(d.node.name)}<span className="go" aria-hidden="true">→</span></b>
                        {d.node.desc && <span className="domain-desc">{d.node.desc}</span>}
                        <span className="domain-foot">
                          <span className="domain-num">{d.count}</span>
                          <span className="domain-unit">{d.count === 1 ? "ASSET" : "ASSETS"}</span>
                        </span>
                        <span className="meter"><i style={{ width: `${Math.round((d.count / maxDomain) * 100)}%` }} /></span>
                      </button>
                    ))}
                  </div>
                </>
              )}

              <div className="home-grid">
                <div>
                  {/* MOST VIEWED */}
                  {viewedDataProducts.length > 0 && (
                    <div className="stack">
                      <div className="sect-head">
                        <h2>Most viewed</h2>
                        <span className="more" onClick={() => runTextSearch("")}>Explore all →</span>
                      </div>
                      <div className="grid">
                        {viewedDataProducts.slice(0, 6).map((p, i) => (
                          <V3ProductCard key={i} product={normalise(p)} />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* RELATED */}
                  {relatedDataProducts.length > 0 && (
                    <div className="stack">
                      <div className="sect-head">
                        <h2>Related to you</h2>
                      </div>
                      <div className="grid">
                        {relatedDataProducts.slice(0, 6).map((p, i) => (
                          <V3ProductCard key={i} product={normalise(p)} />
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <aside className="rail">
                  {/* SAVED */}
                  <div className="card">
                    <div className="card-head"><h3>Saved</h3></div>
                    {savedItems.length > 0 ? (
                      <div className="mini">
                        {savedItems.slice(0, 6).map((p, i) => (
                          <button
                            key={i}
                            className="mini-row"
                            onClick={() => p.PRODUCTID && navigate(`/productDetails/${p.PRODUCTID}`)}
                          >
                            {badgeIcon(p.BADGE_NAME) ? (
                              <img className="tier-img" src={badgeIcon(p.BADGE_NAME)} alt={`${p.BADGE_NAME} tier`} />
                            ) : (
                              <span className="mini-dot" aria-hidden="true" />
                            )}
                            <span className="mini-name">{titleCaseName(p.PRODUCTDISPLAYNAME || p.PRODUCTNAME)}</span>
                            {p.DOMAIN && <span className="mini-meta">{p.DOMAIN}</span>}
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="mini-empty">
                        <b>Nothing saved yet</b>
                        Favourite a data asset and it will be pinned here for quick access.
                      </div>
                    )}
                  </div>

                  {/* CATALOG AT A GLANCE */}
                  <div className="card">
                    <div className="card-head"><h3>Catalog at a glance</h3></div>
                    <div className="stat-list">
                      <div className="stat-row"><span className="lbl">Data assets</span><span className="num">{totalCount ?? "—"}</span></div>
                      <div className="stat-row"><span className="lbl">Communities</span><span className="num">{communities.length || "—"}</span></div>
                      <div className="stat-row"><span className="lbl">Most viewed</span><span className="num">{viewedDataProducts.length || "—"}</span></div>
                      <div className="stat-row"><span className="lbl">Saved</span><span className="num">{savedItems.length || "0"}</span></div>
                    </div>
                  </div>
                </aside>
              </div>
            </>
          )}
        </div>
      </main>
    </React.Fragment>
  );
}
