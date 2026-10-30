import React, { useEffect, useState } from "react";
import { executeQuery, getQueryColumns } from "../services/metadata";
import DataGridTable from "./shared/DataGridTable/DataGridTable";

export default function DataQuery({ data }) {
  const [sql, setSql] = useState("");
  const [result, setResult] = useState(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    setSql("");
    setResult(null);
    setMessage("Loading available columns…");
    getQueryColumns(data.PRODUCTID).then(({ data: preview }) => {
      if (!active) return;
      const columns = preview.columns || [];
      setSql(columns.length ? `SELECT ${columns.map((column) => `${data.TECHNICALNAME}.${column}`).join(", ")} FROM ${data.TECHNICALNAME} LIMIT 20` : "");
      setMessage(columns.length ? "" : "This product has no columns available to query.");
    }).catch((error) => { if (active) setMessage(error.response?.data?.detail || error.response?.data?.message || "Available columns could not be loaded."); });
    return () => { active = false; };
  }, [data.PRODUCTID, data.TECHNICALNAME]);

  const run = () => {
    setMessage("Running…");
    executeQuery({ unit: data.UNIT_ID || data.SCHEMANAME, sql })
      .then((response) => {
        setResult(response.data);
        setMessage("");
      })
      .catch((error) => {
        setResult(null);
        setMessage(error.response?.data?.detail || error.response?.data?.message || "The query was refused.");
      });
  };
  const columns = (result?.columns || []).map((name) => ({ field: name, headerName: name, flex: 1 }));

  return <div className="v3-tab">
    <div className="v3-tab-head"><h3>Run your own read-only query</h3></div>
    <textarea aria-label="SQL query" value={sql} onChange={(event) => setSql(event.target.value)} rows={7} style={{ width: "100%" }} />
    <button className="btn btn-primary" onClick={run} disabled={!sql.trim()}>Run query</button>
    {message && <p role="status">{message}</p>}
    {result && <DataGridTable rows={result.rows} columns={columns} containerStyle={{ width: "100%", height: "560px" }} />}
  </div>;
}
