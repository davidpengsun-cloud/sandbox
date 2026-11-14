/* One reading of a product's data-quality score, shared by the detail page's
   tape and the Overview tab. KGE's derived catalogue carries 0 when nothing has
   been assessed (its field rules default to Not_Assessed), so a missing, "N/A"
   or zero score means no data-quality run is recorded, not a product that
   scored 0%. Only a positive score is shown as a percentage. */
export const qualityOf = (product) => {
  const raw = product?.PRODUCT_SCORE;
  const score = raw === "N/A" || raw === undefined || raw === null || raw === "" ? NaN : Number(raw);
  const recorded = Number.isFinite(score) && score > 0;
  return { recorded, score, text: recorded ? `${raw}%` : "—" };
};
