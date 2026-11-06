import React, { useCallback, useEffect, useState } from "react";
import { getPortalUsers, getPortalRoleAudit, grantPortalRole, removePortalRole } from "../../services/admin";

const failureReason = (error) => {
  const body = error.response?.data;
  return [body?.detail, body?.message, body?.error].find((value) => typeof value === "string") || "Portal roles could not be loaded or updated. Try again.";
};
const roleNames = (roles) => roles.length ? roles.join(", ") : "None";

export default function UserManagement() {
  const [users, setUsers] = useState([]);
  const [roles, setRoles] = useState([]);
  const [audit, setAudit] = useState([]);
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("publisher");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    const [people, history] = await Promise.all([getPortalUsers(), getPortalRoleAudit()]);
    setUsers(people.data.users);
    setRoles(people.data.roles);
    setAudit(history.data.audit);
  }, []);

  useEffect(() => {
    refresh().catch((failure) => setError(failureReason(failure))).finally(() => setBusy(false));
  }, [refresh]);

  const changeRole = async (person, membership, remove = false) => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await (remove ? removePortalRole(person, membership) : grantPortalRole(person, membership));
      setNotice(`${membership} ${remove ? "removed from" : "granted to"} ${person}. The change takes effect at their next request.`);
      await refresh();
    } catch (failure) {
      setError(failureReason(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="portal-users" aria-label="Portal user management" aria-busy={busy}>
      <h2>Portal roles</h2>
      <p>Viewer is the default. Publisher manages publishing; approver decides access requests; admin can do both and manage portal roles. Access stays limited to the data each person can read.</p>
      <p>Access admins always have portal admin access. Removing a portal grant leaves that access in place.</p>
      {error && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <form onSubmit={(event) => { event.preventDefault(); changeRole(username, role); }}>
        <label htmlFor="portal-role-username">Username</label>
        <input id="portal-role-username" name="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoComplete="off" />
        <label htmlFor="portal-role-name">Portal role</label>
        <select id="portal-role-name" value={role} onChange={(event) => setRole(event.target.value)}>
          {roles.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <button className="btn btn-ghost-light" disabled={busy || !username.trim() || !roles.includes(role)}>Grant role</button>
      </form>
      <button className="btn btn-ghost-light" disabled={busy} onClick={async () => {
        setBusy(true);
        setError("");
        try { await refresh(); } catch (failure) { setError(failureReason(failure)); } finally { setBusy(false); }
      }}>Refresh roles and audit</button>
      <div className="portal-users-table">
        <table>
          <caption>Portal role grants</caption>
          <thead><tr><th scope="col">Username</th><th scope="col">Granted roles</th></tr></thead>
          <tbody>
            {users.map((person) => <tr key={person.principal}>
              <th scope="row">{person.principal}</th>
              <td>{person.roles.map((membership) => <span className="portal-role-grant" key={membership}>
                {membership}
                <button className="btn btn-ghost-light" disabled={busy} aria-label={`Remove ${membership} from ${person.principal}`} onClick={() => changeRole(person.principal, membership, true)}>Remove</button>
              </span>)}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
      {!busy && !error && !users.length && <p>No portal roles have been granted. Viewer is the default; Access admins retain admin access.</p>}
      <div className="portal-users-table">
        <table>
          <caption>Role change audit</caption>
          <thead><tr><th scope="col">When</th><th scope="col">Changed by</th><th scope="col">Username</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
          <tbody>{audit.map((entry) => <tr key={entry.id}>
            <td><time dateTime={entry.at}>{entry.at}</time></td><td>{entry.by}</td><td>{entry.principal}</td><td>{roleNames(entry.before)}</td><td>{roleNames(entry.after)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      {!busy && !error && !audit.length && <p>No portal role changes recorded.</p>}
    </section>
  );
}
