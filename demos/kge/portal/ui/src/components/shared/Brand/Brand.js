import React, { useEffect, useState } from "react";
import { getBrand, knownBrand } from "../../../services/brand";
import "../../../assets/styles/v3-brand.css";

export function useBrand() {
  const [brand, setBrand] = useState(knownBrand);
  useEffect(() => {
    let live = true;
    getBrand().then((value) => live && setBrand(value));
    return () => {
      live = false;
    };
  }, []);
  return brand;
}

export function BrandLogo({ brand }) {
  if (brand.logo) return <img src="/brand/logo" alt="" className="v3-brand-logo" data-brand="logo" />;
  return <span className="v3-brand-tile" aria-hidden="true" data-brand="tile">{brand.name.trim().charAt(0).toUpperCase()}</span>;
}
