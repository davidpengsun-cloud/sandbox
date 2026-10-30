import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDispatch } from "react-redux";
import { userLogin, userDetails } from "../features/user/userSlice";
import { login } from "../services/login";

/* Why a sign-in failed, in the server's own words. KGE and the portal both
   answer POST /session with a plain `detail`: "wrong username or password",
   "too many sign-in attempts: try again in N s", "KGE is unavailable.". Only
   when there is no such reason does the form write one of its own. */
export const signInReason = (error) => {
  const body = error?.response?.data;
  const detail = typeof body?.detail === "string" ? body.detail : typeof body?.message === "string" ? body.message : "";
  if (detail) return detail;
  if (!error?.response) return "The portal could not be reached. Check your connection and try again.";
  return `Sign-in failed (HTTP ${error.response.status}). Try again in a moment.`;
};

const LoginForm = () => {
  const navigate = useNavigate();
  const dispatch = useDispatch();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    login({ email, password })
      .then((response) => {
        dispatch(
          userDetails({
            userId: response.data.id,
            userName: response.data.id,
          })
        );
        dispatch(userLogin(true));
        navigate("/home");
      })
      .catch((failure) => {
        setError(signInReason(failure));
        setBusy(false);
      });
  };

  return (
    <form className="signin-form" noValidate onSubmit={handleSubmit}>
      <div className="signin-field">
        <label htmlFor="signin-username">Username</label>
        <input
          id="signin-username"
          name="username"
          type="text"
          autoComplete="username"
          autoFocus
          required
          value={email}
          aria-invalid={error ? true : undefined}
          onChange={(e) => setEmail(e.target.value)}
        />
      </div>
      <div className="signin-field">
        <label htmlFor="signin-password">Password</label>
        <input
          id="signin-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          aria-invalid={error ? true : undefined}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      {error ? <p className="signin-error" role="alert">{error}</p> : null}
      <button type="submit" className="signin-btn" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
};

export default LoginForm;
