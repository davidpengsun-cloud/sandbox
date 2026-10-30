import React, { useEffect, useState } from 'react';
import { Navigate, Outlet } from 'react-router-dom';
import Loading from './Loading';
import { useDispatch } from "react-redux";
import { userDetails, userLogin } from "../features/user/userSlice";

export const RequiredAuth = () => {
  const dispatch = useDispatch();
  const [state, setState] = useState("loading");

  useEffect(() => {
    Promise.all([fetch("/whoami", { credentials: "include" }), fetch("/api/role", { credentials: "include" })])
      .then(async ([who, role]) => {
        if (!who.ok) throw new Error("signed out");
        const identity = await who.json();
        const portalRole = role.ok ? (await role.json()).role : "viewer";
        dispatch(userDetails({ userId: identity.id, userName: identity.id, userRole: portalRole }));
        dispatch(userLogin(true));
        setState("ready");
      })
      .catch(() => setState("signed-out"));
  }, [dispatch]);
  if (state === "loading") return <Loading />;
  if (state === "signed-out") return <Navigate to="/" replace />;
  return <Outlet />;
}
