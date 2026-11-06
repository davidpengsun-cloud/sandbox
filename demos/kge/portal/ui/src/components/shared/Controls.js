import React, { forwardRef } from "react";
import "./Controls.css";

export const Button = forwardRef(function Button({ type = "button", ...props }, ref) {
  return <button type={type} ref={ref} {...props} />;
});
export const Panel = forwardRef(function Panel({ className = "", ...props }, ref) {
  return <section ref={ref} className={`panel ${className}`} {...props} />;
});
export const Tabs = forwardRef(function Tabs({ as: Tag = "div", ...props }, ref) {
  return <Tag ref={ref} role="tablist" {...props} />;
});
