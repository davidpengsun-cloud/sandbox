import React, { useCallback, useEffect, useState } from "react";
import { getApi, postApi } from "../../api/api";

export default function RequestQueue() {
  const [rows, setRows] = useState([]);
  const [message, setMessage] = useState("Loading…");

  const load = useCallback(() => {
    getApi({ api: "/api/admin/requests" })
      .then((res) => {
        const list = Array.isArray(res?.data) ? res.data : [];
        setRows(list);
        setMessage(list.length ? "" : "No open requests.");
      })
      .catch((error) => setMessage(error?.response?.status === 403 ? "Approving requests needs the approver or admin portal role." : "Requests could not be loaded."));
  }, []);

  useEffect(() => { load(); }, [load]);

  const decide = (id, decision) => {
    postApi({ api: `/api/admin/requests/${id}/decision`, params: { decision } })
      .then(load)
      .catch(() => setMessage("That request could not be updated."));
  };

  return (
    <div className="request-queue">
      {message && <p role="status">{message}</p>}
      {rows.length > 0 && (
        /* a narrow screen scrolls the table inside this box, never the page; focusable so the
           keyboard can scroll it too */
        <div className="request-queue-scroll" role="region" aria-label="Open requests, scrollable" tabIndex={0} style={{ overflowX: "auto", maxWidth: "100%" }}>
        <table aria-label="Open requests" style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th align="left">Product</th>
              <th align="left">Unit</th>
              <th align="left">Requested by</th>
              <th align="left">Kind</th>
              <th align="left">Requested</th>
              <th align="left">Decision</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                {/* the server's queue rows carry product_id (unit.table); a name only where one is given */}
                <td>{row.product_name || row.product_id}</td>
                <td>{row.unit_id}</td>
                <td>{row.principal_id}</td>
                <td>{row.kind}</td>
                <td>{String(row.created_at?.value ?? row.created_at)}</td>
                <td>
                  {/* both decisions on the panel's white surface: a primary and a visible danger
                      button (btn-ghost-light is white text for the dark asset header only) */}
                  <button className="btn btn-primary" onClick={() => decide(row.id, "approve")}>Approve</button>{" "}
                  <button className="btn btn-danger" onClick={() => decide(row.id, "reject")}>Reject</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
