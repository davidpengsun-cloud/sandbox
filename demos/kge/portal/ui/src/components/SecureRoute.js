import React, { useEffect, useRef, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import Loading from './Loading';
import { useDispatch } from "react-redux";
import { userDetails, userLogin } from "../features/user/userSlice";
import { contextRolesOf } from "./shell/apps";

/* Signed-out people go to sign-in, or see `signedOut` where it is given (the portal's root shows
   the sign-in form in place). Every navigation re-reads who is signed in and their roles. */
export const RequiredAuth = ({ signedOut = null }) => {
  const dispatch = useDispatch();
  const { key, pathname } = useLocation();
  const [state, setState] = useState("loading");
  const lastPath = useRef(pathname);

  useEffect(() => {
    let active = true;
    const samePath = lastPath.current === pathname;
    lastPath.current = pathname;
    setState((current) => samePath && current === "ready" ? "ready" : "loading");
    Promise.all([fetch("/whoami", { credentials: "include" }), fetch("/api/role", { credentials: "include" })])
      .then(async ([who, role]) => {
        if (!who.ok) throw new Error("signed out");
        const identity = await who.json();
        const portal = role.ok ? await role.json() : { role: "viewer", roles: ["viewer"] };
        if (!active) return;
        dispatch(userDetails({
          userId: identity.id,
          userName: identity.name || identity.id,
          ...(identity.admin && identity.role === "Access admin" ? { userRoleLabel: identity.role } : {}),
          userRole: portal.role || "viewer",
          userRoles: portal.roles || [portal.role || "viewer"],
          contextRoles: contextRolesOf(identity),
        }));
        dispatch(userLogin(true));
        setState("ready");
      })
      .catch(() => { if (active) setState("signed-out"); });
    return () => { active = false; };
  }, [dispatch, key, pathname]);
  if (state === "loading") return <Loading />;
  if (state === "signed-out") return signedOut || <Navigate to="/" replace />;
  return <Outlet />;
}
