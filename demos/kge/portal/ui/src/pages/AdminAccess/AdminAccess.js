import React, { useState } from "react";
import { CssBaseline } from "@material-ui/core";
import Header from "../../components/shared/Header/Header";
import PublishedStatus from "../../components/PublishedStatus/PublilshedStatus";
import RequestQueue from "../../components/RequestQueue/RequestQueue";
import UserManagement from "./UserManagement";
import { useSelector } from "react-redux";
import { useNavigate } from "react-router-dom";
import { useBrand } from "../../components/shared/Brand/Brand";
import "./AdminAccess.css";
import "../../assets/styles/v3-detail.css";

/* Admin console in the v3 "settlement tape" design. Reuses the scoped
   .v3-detail component classes (asset-head / tabs / panel / empty) so it
   matches the data product detail page without any new CSS. */

const TABS = [
  { key: "userMgmt", label: "User Management" },
  { key: "publishedStatus", label: "Published Status" },
  { key: "requests", label: "Access Requests" },
  { key: "config", label: "Configuration" },
];

export default function AdminAccess() {
  const navigate = useNavigate();
  const [value, setValue] = useState("publishedStatus");
  const person = useSelector((state) => state.user.data);
  const canManageUsers = (person?.userRoles || [person?.userRole]).includes("admin");
  const brand = useBrand();

  return (
    <React.Fragment>
      <CssBaseline />
      <Header type="home" />
      <main>
        <div className="v3-detail">
          <nav className="crumbs" aria-label="Breadcrumb">
            <button className="link" style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: "var(--v3-ink-2)", font: "inherit" }} onClick={() => navigate("/home")}>HOME</button>
            <span className="sep">/</span>
            <span className="here">ADMIN</span>
          </nav>

          <div className="asset-head">
            <div className="asset-top">
              <div className="asset-title">
                <div className="eyebrow">Administration · Governed catalog</div>
                <h1>{brand.name} configuration</h1>
              </div>
              <div className="asset-actions">
                <button className="btn btn-ghost-light" onClick={() => navigate("/home")}>Back to home</button>
              </div>
            </div>
          </div>

          <div className="tabs" role="tablist" aria-label="Admin sections">
            {TABS.filter((t) => t.key !== "userMgmt" || canManageUsers).map((t) => (
              <button
                key={t.key}
                className="tab"
                role="tab"
                aria-selected={value === t.key}
                onClick={() => setValue(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="panel">
            {value === "publishedStatus" && <PublishedStatus />}
            {value === "requests" && <RequestQueue />}
            {value === "userMgmt" && canManageUsers && <UserManagement />}
            {value === "config" && (
              <div className="empty">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="var(--v3-line-2)" strokeWidth="1.5" style={{ marginBottom: "12px" }}>
                  <path d="M12 2 2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5M2 12l10 5 10-5" />
                </svg>
                <h4>Development in progress</h4>
                <p>
                  Configuration editing isn't available yet. {brand.name} settings are managed through the config table.
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </React.Fragment>
  );
}
