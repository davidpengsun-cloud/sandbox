import React, { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import './PublishedStatus.css';
import { getPublishedStatus, updatePublishedStatus, publishStatusLiveSync, updateApplicationLiveSync,
  getDomainList, getFilterResult, getPublishSearchData, getPublishedSearchResult } from "../../services/admin";

/* Admin › Published Status on the v3 tokens: two labelled filters, one table,
   a checkbox per product. It sits inside the admin page's .v3-detail .panel,
   so the shared btn / chip classes apply; the .pub-* rules are its own. */

const toRow = (item) => ({
  COMMUNITY_NAME: item.COMMUNITY_NAME,
  DOMAIN_NAME: item.DOMAIN_NAME,
  TECHNICAL_NAME: item.TECHNICAL_NAME,
  PRODUCTNAME: item.PRODUCTNAME,
  PUBLISHED_STATUS: Boolean(item.PUBLISHED_STATUS),
});

const PublishedStatus = () => {
  const [rows, setRows] = useState([]);
  const [emptyText, setEmptyText] = useState("Loading…");
  const [notice, setNotice] = useState("");
  const [products, setProducts] = useState([]);
  const [domains, setDomains] = useState([]);
  const [product, setProduct] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const userData = useSelector((state) => state.user.data);

  const show = (res, empty = "No data assets to publish.") => {
    const list = Array.isArray(res?.data) ? res.data : [];
    setRows(list.map(toRow));
    setEmptyText(list.length ? "" : empty);
  };
  const failed = (text) => () => {
    setRows([]);
    setEmptyText(text);
  };

  useEffect(() => {
    getPublishedStatus().then((res) => show(res)).catch(failed("Published status could not be loaded."));
    getDomainList().then((res) => setDomains(Array.isArray(res?.data) ? res.data : [])).catch(() => setDomains([]));
    getPublishSearchData('0').then((res) => setProducts(Array.isArray(res?.data) ? res.data : [])).catch(() => setProducts([]));
  }, []);

  const onProduct = (event) => {
    const value = event.target.value;
    setProduct(value);
    setDomain("");
    (value ? getPublishedSearchResult(value) : getPublishedStatus())
      .then((res) => show(res, "No data asset matches."))
      .catch(failed("The search could not be run."));
  };

  const onDomain = (event) => {
    const value = event.target.value;
    setDomain(value);
    setProduct("");
    (value ? getFilterResult(value) : getPublishedStatus())
      .then((res) => show(res, "No data assets in this domain."))
      .catch(failed("The filter could not be applied."));
  };

  const toggle = (row) => {
    const next = !row.PUBLISHED_STATUS;
    const params = {
      technicalName: row.TECHNICAL_NAME,
      publishedStatus: next ? 'Y' : 'N',
      approver: userData?.userId,
    };
    updatePublishedStatus(params).then((res) => {
      const confirmed = Array.isArray(res?.data) && res.data[0] ? Boolean(res.data[0].PUBLISHED_STATUS) : next;
      setRows((current) => current.map((item) => item.TECHNICAL_NAME === row.TECHNICAL_NAME ? { ...item, PUBLISHED_STATUS: confirmed } : item));
      setNotice(`${row.PRODUCTNAME} is now ${confirmed ? "published" : "unpublished"}.`);
    }).catch(() => {
      setNotice(`${row.PRODUCTNAME} could not be updated.`);
    });
  };

  const liveSync = () => {
    setBusy(true);
    setNotice("");
    publishStatusLiveSync()
      .then((res) => {
        if (!res || !res.data) throw new Error("live sync failed");
        return updateApplicationLiveSync();
      })
      .then((res) => {
        if (!res || !res.data) throw new Error("live sync failed");
        setNotice("Live sync completed.");
        return getPublishedStatus().then((latest) => show(latest));
      })
      .catch(() => setNotice("Live sync failed."))
      .finally(() => setBusy(false));
  };

  const published = rows.filter((row) => row.PUBLISHED_STATUS).length;

  return (
    <div className="pub-status">
      <div className="pub-toolbar">
        <div className="pub-field">
          <label htmlFor="pub-product">Data asset</label>
          <select id="pub-product" value={product} onChange={onProduct}>
            <option value="">All products</option>
            {products.map((option) => <option key={option.key} value={option.value}>{option.value}</option>)}
          </select>
        </div>
        <div className="pub-field">
          <label htmlFor="pub-domain">Domain</label>
          <select id="pub-domain" value={domain} onChange={onDomain}>
            <option value="">All domains</option>
            {domains.map((option) => <option key={option.key} value={option.key}>{option.value}</option>)}
          </select>
        </div>
        <div className="pub-spacer" />
        <span className="pub-count">{published} OF {rows.length} PUBLISHED</span>
        <button type="button" className="btn" onClick={liveSync} disabled={busy}>{busy ? "Syncing…" : "Live sync"}</button>
      </div>

      <p className="pub-notice" role="status">{notice}</p>

      {rows.length > 0 ? (
        <div className="pub-table-wrap">
          <table className="pub-table" aria-label="Published status">
            <thead>
              <tr>
                <th>Community</th>
                <th>Domain</th>
                <th>Technical name</th>
                <th>Product</th>
                <th>Published</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.TECHNICAL_NAME}>
                  <td>{row.COMMUNITY_NAME}</td>
                  <td>{row.DOMAIN_NAME}</td>
                  <td className="mono">{row.TECHNICAL_NAME}</td>
                  <td>{row.PRODUCTNAME}</td>
                  <td>
                    <label className="pub-switch">
                      {/* the domain tells apart products of the same name (Account in Retail and in Wealth) */}
                      <input
                        type="checkbox"
                        checked={row.PUBLISHED_STATUS}
                        onChange={() => toggle(row)}
                        aria-label={row.DOMAIN_NAME ? `Publish ${row.PRODUCTNAME} (${row.DOMAIN_NAME})` : `Publish ${row.PRODUCTNAME}`}
                      />
                      <span className={`chip ${row.PUBLISHED_STATUS ? "chip-ok" : "chip-plain"}`}>
                        {row.PUBLISHED_STATUS ? "PUBLISHED" : "UNPUBLISHED"}
                      </span>
                    </label>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="pub-empty">{emptyText}</p>
      )}
    </div>
  );
};

export default PublishedStatus;
