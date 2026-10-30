import React from "react";
import LoginForm from "../components/LoginForm";
import { BrandLogo, useBrand } from "../components/shared/Brand/Brand";
import "../assets/styles/v3-signin.css";

/* Sign-in page on the v3 tokens: one card, the brand (the client logo when
   configured, else the initial tile) and the form. The same page serves both
   modes (KGE behind the portal, or the portal alone). */
const Login = () => {
  const brand = useBrand();
  return (
    <main className="v3-signin">
      <section className="signin-card" aria-labelledby="signin-title">
        <div className="signin-brand">
          <BrandLogo brand={brand} />
          <span className="signin-eyebrow">{brand.name}</span>
        </div>
        <h1 id="signin-title">Sign in</h1>
        <p className="signin-sub">Your account decides which data assets you can see.</p>
        <LoginForm />
      </section>
    </main>
  );
};

export default Login;
