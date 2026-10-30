export const BRAND_NAME = "Data Discovery Portal";
const fallback = { name: BRAND_NAME, logo: false, kge_url: null };
let known = null;

export const knownBrand = () => known || fallback;

export async function getBrand() {
  if (known) return known;
  try {
    const response = await fetch("/brand", { credentials: "same-origin" });
    if (!response.ok) return fallback;
    const body = await response.json();
    known = {
      name: typeof body?.name === "string" && body.name.trim() ? body.name : BRAND_NAME,
      logo: body?.logo === true,
      kge_url: typeof body?.kge_url === "string" && body.kge_url ? body.kge_url : null,
    };
    return known;
  } catch {
    return fallback;
  }
}

export function forgetBrand() {
  known = null;
}
