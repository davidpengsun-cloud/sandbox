import { useEffect } from "react";
import { useLocation } from "react-router-dom";
import { DEFAULT_PRODUCT_NAME, shortNameOf, useConfig } from "../../services/config";

const pages = {
  "/knowledge/docs/at-a-glance": "At a glance",
  "/knowledge/docs/start-here": "Start here",
  "/certify": "Certify",
  "/context": "Context",
  "/home": "Discover",
  "/search": "Explore",
  "/adminaccess": "Admin access",
};

export default function usePageTitle(title) {
  const { pathname } = useLocation();
  const config = useConfig();
  const product = config?.productName || DEFAULT_PRODUCT_NAME;
  const path = pathname.toLowerCase();
  const page = title || (path === "/" ? `${shortNameOf(config)} home` : pages[path]) || "Data product details";
  useEffect(() => {
    document.title = `${page} · ${product}`;
  }, [page, product]);
}
