import React, { useEffect, useRef, useState } from "react";
import { withStyles } from "@material-ui/core/styles";
import Header from "../../components/shared/Header/Header";
import BreadcrumbsView from "../../components/shared/BreadcrumbsView";
import {
  CssBaseline,
  Grid,
  Paper,
  Tab,
  Tabs,
} from "@material-ui/core";
import DataOverview from "../../components/DataOverview/DataOverview";
import { qualityOf } from "../../components/DataOverview/quality";
import DataOverviewV3 from "../../components/DataOverview/DataOverviewV3";
import DataFields from "../../components/DataFields";
import DataLineage from "../../components/DataLineage";
import DataControls from "../../components/DataControls/DataControls";
import DataPreview from "../../components/DataPreview";
import DataQuery from "../../components/DataQuery";
import UseCases from "../../components/UseCases/UseCases";
import { getProduct, submitFeedback } from "../../services/metadata";
import { removeFavouriteProduct, saveFavouriteProduct, getConfig, getMyRequests, requestAccess } from "../../services/user";
import { useParams } from "react-router-dom";
import NavigateNextIcon from "@material-ui/icons/NavigateNext";
import './DataProductDetails.css'
import '../../assets/styles/v3-detail.css'
import { showToast } from "../../components/shared/V3Toast/V3Toast";
import Chatbot from "../../components/askportal/Chatbot";
import { Favorite, FavoriteBorderOutlined } from "@material-ui/icons";
import { useDispatch, useSelector } from "react-redux";
// import { searchResults } from "../../features/search/searchSlice";
import { DatabaseOutlined } from "@ant-design/icons";
import { Tooltip } from "antd";
import { NavLink } from "react-router-dom";
import parse from 'html-react-parser';
import { getSearchResults, getCommunityProducts } from "../../services/search";
import { getFavouriteDataProducts } from "../../services/user";
import { communityFilter, domainFilter, hierarchyList, searchResults, sourceFilter, typeFilter, searchParam, domainSelected } from "../../features/search/searchSlice";
import { selectedProduct, selectedProductName } from "../../features/dataProduct/dataProductSlice";
import GoldBadge from "../../assets/icons/gold.png";
import SilverBadge from "../../assets/icons/silver.png";
import BronzeBadge from "../../assets/icons/bronze.png";
import EmptyBadge from "../../assets/icons/empty.png";

const CustomTabs = withStyles({
  indicator: {
    backgroundColor: '#00345F',
    height: "0rem",
    borderRadius: "2px",
    left: 0,
    right: 0,
  },
})(Tabs);

const CustomTab = withStyles((theme) => ({
  root: {
    textTransform: "initial",
    maxWidth: "100%",
    margin: "0 2rem 0 2rem",
    overflow: "hidden",
    fontSize: "2.5rem",
    fontFamily: "Inter",
    "&.MuiTab-root": {
      backgroundColor: "#FFFFFF"
    },
    '&:hover': {
      color: "#00345F",
      fontWeight: "bold",
      opacity: 1,
    },
    '&$selected': {
      color: "#00345F",
      fontWeight: "bold"
    },
    '&:focus': {
      color: "#00345F",
    },
  },
  selected: {},
}))((props) => <Tab {...props} />);

export default function DataProductDetails(props) {
  const productId = useParams().id;
  const dispatch = useDispatch();
  const [selectedDataProduct, setSelectedDataProduct] = useState();
  const [value, setValue] = useState("overview");
  const [showChatbot, setShowChatbot] = useState(false);
  const searchResultsData = useSelector((state) => state.search.searchResults);
  const [status, setStatus] = useState("loading");
  const [access, setAccess] = useState({ status: null, busy: false });
  // After a request the button is replaced by its chip: focus follows it there,
  // so a keyboard user is not dropped to the page body.
  const accessChipRef = useRef(null);
  const focusAccessChip = useRef(false);
  const [hierarchyNav, setHierarchyNav] = useState([]);
  const [configValues, setConfigValues] = useState();
  const [isFav, setIsFav] = useState("");
  const [feedbackRating, setFeedbackRating] = useState(5);
  const [feedbackComment, setFeedbackComment] = useState("");

  const handleChange = (event, value) => {
    setValue(value);
  };
  useEffect(() => {
    getProduct(productId).then((res) => {
      if (!res || !res.data) {
        console.log('Error', res);
        setStatus("missing")
      } else {
        dispatch(selectedProduct(productId));
        dispatch(selectedProductName(res.data.TECHNICALNAME));
        setSelectedDataProduct(res.data);
        setStatus("ready");
        setHierarchyNav(res.data.HIERARCHY.split("->"));
      }
    }).catch((error) => {
      console.log('Error', error);
      setStatus("missing")
    });
    getConfig().then((res) => {
      if (!res || !res.data) {
          console.log('Error', res);
      } else {
          setConfigValues(res.data);
      }
    }).catch((error) => {
        console.log(error);
    });
  }, []);

  useEffect(() => {
    getMyRequests().then(rows => {
      const mine = (rows.data || []).filter(r => r.product_id === productId && r.kind === "access");
      const pick = mine[0];
      setAccess(current => current.status === "OPEN" ? current : { status: pick?.status || null, busy: current.busy });
    }).catch(() => {});
  }, [productId]);

  useEffect(() => {
    if (focusAccessChip.current && accessChipRef.current) {
      focusAccessChip.current = false;
      accessChipRef.current.focus();
    }
  }, [access.status]);

  const askForAccess = () => {
    setAccess(a => ({ ...a, busy: true }));
    requestAccess(productId).then(() => {
      focusAccessChip.current = true;
      setAccess({ status: "OPEN", busy: false });
      showToast("Request recorded — the data steward will review it");
    }).catch(e => {
      setAccess(a => ({ ...a, busy: false }));
      showToast(e?.response?.data?.detail || e?.response?.data?.message || "The request could not be sent");
    });
  };

  const updateSearchResults = (id, value) => {
    return searchResultsData.map(item => {
      if (item.PRODUCTID === id) {
        return { ...item, FAVOURITED: value, SUBSCRIBE_COUNT: value ? item.SUBSCRIBE_COUNT + 1 : item.SUBSCRIBE_COUNT - 1 }
      }
      return item;
    });
  };

  const favoriteAction = (product) => {
    let favouritedProduct;
    const params = {
      productId: product.PRODUCTID,
      applicationId: product.APPLICATION_IDENTIFIER,
      technicalName: product.TECHNICALNAME
    }
    if (product.ISFAVOURITE === 'Y') {
      removeFavouriteProduct(params).then((res) => {
        if (!res || !res.status || res.status === 'Fail') {
          console.log('Error', res.message);
        } else {
          setIsFav(false);
          favouritedProduct = updateSearchResults(product.PRODUCTID, false);
          setSelectedDataProduct({ ...selectedDataProduct, ISFAVOURITE: null });
          dispatch(searchResults(favouritedProduct));
          showToast("Removed from favourites");
        }
      }).catch((error) => {
        console.log(error);
      });
    } else {
      saveFavouriteProduct(params).then((res) => {
        if (!res || !res.status || res.status === 'Fail') {
          console.log('Error', res.message);
        } else {
          setIsFav(true);
          favouritedProduct = updateSearchResults(product.PRODUCTID, true);
          setSelectedDataProduct({ ...selectedDataProduct, ISFAVOURITE: 'Y' });
          dispatch(searchResults(favouritedProduct));
          showToast("Saved to favourites");
        }
      }).catch((error) => {
        console.log(error);
      });
    }    
  };

  const setResultSet = (data) => {
    if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
      console.log('Error', data);
    } else {
      const result = data[0].data.searchList.map(item => ({
        ...item,
        FAVOURITED: data[1].data.find(d => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false
      }));
      dispatch(searchResults(result));
      dispatch(domainFilter(data[0].data.domainFilter));
      dispatch(communityFilter(data[0].data.communityFilter));
      dispatch(sourceFilter(data[0].data.sourceFilter));
      dispatch(typeFilter(data[0].data.typeFilter));
      dispatch(hierarchyList(data[0].data.hierarchy));
    }
  }

  const triggerSearch = (param, isCommunitySearch) => {
    dispatch(domainSelected(false));
    if (!isCommunitySearch) {
      dispatch(searchParam(param));
      Promise.all([
        getCommunityProducts({ searchParam: param }),
        getFavouriteDataProducts()
      ]).then((data) => {
        setResultSet(data);
      }).catch((error) => {
        console.log(error);
      });
    } else {
      Promise.all([
        getCommunityProducts({ searchParam: param, isDomain: true }),
        getFavouriteDataProducts()
      ]).then((data) => {
        setResultSet(data);
      }).catch((error) => {
        console.log(error);
      });
    }
  }

  const toggleChatbot = () => {
    setShowChatbot(prevState => !prevState);
  };

  const getBadge = (val) => {
    if (val === 'EMPTY') {
      return EmptyBadge;
    } else if (val === 'BRONZE') {
      return BronzeBadge;
    } else if (val === 'SILVER') {
      return SilverBadge;
    }
    return GoldBadge;
  }

  const getBadgeLegend = (dqScore) => {
    if (dqScore >= 0 && dqScore < 10) {
      return 'Low Trust with Data Quality between 0-9% or no ownership/stewardship indicated.';
    } else if (dqScore >= 10 && dqScore < 59) {
      return 'Moderate Trust with Data Quality between 10-59%.';
    } else if (dqScore >= 60 && dqScore < 89) {
      return 'High Trust with Data Quality between 60-89%.';
    }
    return 'Highest Trust with Data Quality above 90% and ownership in place.';
  }

  const copyText = (text, message) => {
    try {
      navigator.clipboard.writeText(text);
      showToast(message || "Copied to clipboard");
    } catch (e) { /* clipboard unavailable */ }
  };

  const TABS = [
    { key: "overview", label: "Overview" },
    { key: "fields", label: "Fields" },
    { key: "controls", label: "Field Data Quality" },
    { key: "lineage", label: "Lineage" },
    ...(configValues?.preview ? [{ key: "preview", label: "Data preview" }] : []),
    ...(configValues?.executeQuery ? [{ key: "query", label: "Run a query" }] : []),
  ];

  const renderDetail = (p) => {
    const badges = (p.BADGES || [p.BADGE_NAME]).filter(Boolean).map((item) => item.toUpperCase());
    const badge = badges.find((item) => ["GOLD", "SILVER", "BRONZE"].includes(item)) || "";
    const badgeSrc = getBadge(["GOLD", "SILVER", "BRONZE"].includes(badge) ? badge : "EMPTY");
    const badgeTitle = { GOLD: "Gold — owned, documented and monitored", SILVER: "Silver — owned and documented", BRONZE: "Bronze — catalogued only" }[badge] || "No quality tier recorded";
    const fqn = [p.SCHEMANAME, p.TECHNICALNAME].filter(Boolean).join(".");
    const quality = qualityOf(p);
    const columns =
      p.PARENT_DATASET_COUNT_OF_COLUMNS !== null && p.PARENT_DATASET_COUNT_OF_COLUMNS !== undefined
        ? `${p.COUNTOFCOLUMNS} / ${p.PARENT_DATASET_COUNT_OF_COLUMNS}`
        : p.COUNTOFCOLUMNS;
    const crumbTrail = hierarchyNav.slice(0, Math.max(hierarchyNav.length - 1, 0));

    return (
      <div className="v3-detail">
        <nav className="crumbs" aria-label="Breadcrumb">
          <NavLink to="/home">HOME</NavLink><span className="sep">/</span>
          {crumbTrail.map((item, i) => (
            <React.Fragment key={i}>
              {item === "Portal"
                ? <NavLink to="/home">{item}</NavLink>
                : <NavLink to="/search" onClick={() => triggerSearch(item, false)}>{item}</NavLink>}
              <span className="sep">/</span>
            </React.Fragment>
          ))}
          <span className="here">{p.PRODUCTNAME}</span>
        </nav>

        <div className="asset-head">
          <div className="asset-top">
            <div className="asset-title">
              <div className="eyebrow">
                {[p.PRODUCTTYPE, p.DATA_PRODUCT_SOURCE, p.DATA_DOMAIN_NAME].filter(Boolean).join(" · ")}
              </div>
              <h1>
                {p.PRODUCTNAME}
                <img className="tier-medal" src={badgeSrc} alt={`${badge || "No quality"} tier`} title={badgeTitle} />
                {badges.includes("CERTIFIED") && <span className="chip chip-aqua chip-ok">CERTIFIED</span>}
                {badges.filter((item) => !["GOLD", "SILVER", "BRONZE", "CERTIFIED"].includes(item)).map((item) => <span className="chip chip-rasp" key={item}>{item}</span>)}
              </h1>
              {fqn && (
                <div className="tech-name">
                  <span>{fqn}</span>
                  <button className="copy-btn" onClick={() => copyText(fqn)} aria-label="Copy fully qualified name" title="Copy full name">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="9" y="9" width="12" height="12" rx="1"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>
                  </button>
                </div>
              )}
            </div>
            <div className="asset-actions">
              {access.status === "OPEN" ? (
                <span className="chip chip-aqua" role="status" tabIndex={-1} ref={accessChipRef}><span className="dot dot-aqua" />ACCESS REQUESTED</span>
              ) : access.status === "APPROVED" ? (
                <span className="chip chip-aqua chip-ok" role="status">ACCESS APPROVED</span>
              ) : (
                <>
                  {access.status === "REJECTED" && <span className="chip chip-rasp" role="status">REQUEST DECLINED</span>}
                  <button className="btn btn-ghost-light" onClick={askForAccess} disabled={access.busy} title="Record a request for this product's data with its data steward">{access.busy ? "Requesting…" : access.status === "REJECTED" ? "Request again" : "Request access"}</button>
                </>
              )}
              <button
                className="btn btn-ghost-light btn-icon fav-btn"
                aria-pressed={p.ISFAVOURITE === "Y"}
                onClick={() => favoriteAction(p)}
                title="Save"
                aria-label="Save to my products"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill={p.ISFAVOURITE === "Y" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2"><path d="M6 3h12v18l-6-4.5L6 21z"/></svg>
              </button>
            </div>
          </div>

          <div className="tape">
            <div className="tape-cell">
              <div className="lbl">Last refresh</div>
              <div className="tape-val live"><span className="pulse" aria-hidden="true"></span>{p.LAST_REFRESH || "—"}</div>
              <div className="tape-sub">{quality.recorded && p.DQRUNDATE ? `LAST DQ · ${p.DQRUNDATE}` : "PORTAL"}</div>
            </div>
            <div className="tape-cell">
              <div className="lbl">Quality</div>
              <div className={`tape-val ${quality.recorded ? (quality.score >= 90 ? "live" : "warn") : ""}`}>{quality.text}</div>
              <div className="tape-sub">{quality.recorded ? "DATA SET QUALITY SCORE" : "NO DQ RUN RECORDED"}</div>
            </div>
            <div className="tape-cell">
              <div className="lbl">Source records (before read filters)</div>
              <div className="tape-val">{p.COUNTOFRECORDS || "—"}</div>
              <div className="tape-sub">{[p.SIZE, p.DATA_FORMAT].filter(Boolean).join(" · ").toUpperCase() || " "}</div>
            </div>
            <div className="tape-cell">
              <div className="lbl">Columns</div>
              <div className="tape-val">{columns}</div>
              <div className="tape-sub">SCHEMA · {p.SCHEMANAME || "—"}</div>
            </div>
            <div className="tape-cell">
              <div className="lbl">Views</div>
              <div className="tape-val">{p.VIEWTIMES === null || p.VIEWTIMES === undefined ? "0" : p.VIEWTIMES}</div>
              <div className="tape-sub">RATING · {p.AVGRATING ?? "—"} / 5</div>
            </div>
          </div>
        </div>

        <div className="tabs" role="tablist" aria-label="Data asset sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              className="tab"
              role="tab"
              aria-selected={value === t.key}
              onClick={() => setValue(t.key)}
            >
              {t.label}
              {t.key === "fields" && p.COUNTOFCOLUMNS ? <span className="count">{p.COUNTOFCOLUMNS}</span> : null}
            </button>
          ))}
        </div>

        <div className="panel">
          {value === "overview" && <DataOverviewV3 data={p} config={configValues} />}
          {value === "fields" && <DataFields data={p} />}
          {value === "lineage" && <DataLineage data={p} />}
          {value === "controls" && <DataControls data={p} />}
          {value === "preview" && <DataPreview data={p} config={configValues} />}
          {value === "query" && <DataQuery data={p} />}
          <div className="card" aria-label="Product feedback">
            <div className="card-head"><h3>Rate this data asset</h3></div>
            <div className="tag-row">
              <select aria-label="Rating" value={feedbackRating} onChange={(event) => setFeedbackRating(Number(event.target.value))}>
                {[5, 4, 3, 2, 1].map((rating) => <option value={rating} key={rating}>{rating} / 5</option>)}
              </select>
              <input aria-label="Feedback comment" value={feedbackComment} onChange={(event) => setFeedbackComment(event.target.value)} placeholder="What was useful?" maxLength={500} />
              <button className="btn btn-primary" onClick={() => submitFeedback(p.PRODUCTID, feedbackRating, feedbackComment).then(() => { setFeedbackComment(""); showToast("Feedback saved"); }).catch(() => showToast("Feedback could not be saved"))}>Send feedback</button>
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <React.Fragment>
      <CssBaseline />
      <Header type="DPD" toggleChatbot={toggleChatbot} askScope={selectedDataProduct?.PRODUCTNAME} askOpen={showChatbot} />
      <main>
        <div id="DPDRoot">
          <Chatbot showChatbot={showChatbot} setShowChatbot={setShowChatbot} productId={productId} />
          {status === "ready" ? (
            renderDetail(selectedDataProduct)
          ) : status === "loading" ? <DetailSkeleton /> : (
            <div className="v3-detail"><nav className="crumbs" aria-label="Breadcrumb"><NavLink to="/home">HOME</NavLink><span className="sep">/</span><span className="here">Not found</span></nav>
              <div className="v3-empty" role="status"><div className="v3-empty-msg">This data asset could not be found, or your units do not include it. <NavLink to="/home">Back to home</NavLink></div></div>
            </div>
          )}
        </div>
      </main>
    </React.Fragment>
  );
};

function SkeletonLines({ lines }) {
  return lines.map(([height, width], i) => <div key={i} className="v3-skel" style={{ height, width }} />);
}

function DetailSkeleton() {
  const lines = [[15, "40%"], [12, "95%"], [12, "88%"], [12, "60%"]];
  return <div className="v3-detail" aria-busy="true" aria-label="Loading data asset">
    <div className="crumbs"><SkeletonLines lines={[[11, 220]]} /></div>
    <div className="asset-head">
      <div className="asset-top skel-lines"><SkeletonLines lines={[[11, 180], [30, "55%"], [24, 260]]} /></div>
      <div className="tape">{Array.from({ length: 5 }, (_, i) => <div className="tape-cell skel-lines" key={i}><SkeletonLines lines={[[11, 70], [18, 90]]} /></div>)}</div>
    </div>
    <div className="tabs" style={{ padding: "12px 16px" }}><SkeletonLines lines={Array.from({ length: 5 }, () => [12, 80])} /></div>
    <div className="panel"><div className="two-col">
      <div className="skel-lines">{[0, 1].map(i => <div className="card skel-lines" key={i}><SkeletonLines lines={lines} /></div>)}</div>
      <div className="card skel-lines"><SkeletonLines lines={lines.slice(0, 3)} /></div>
    </div></div>
  </div>;
}
