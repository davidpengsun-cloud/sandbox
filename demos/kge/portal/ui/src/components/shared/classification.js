// The four classification labels the portal shows. A source value of
// Restricted is the top level of the policy's scale and shows as Highly
// Confidential; an unset or unknown value shows blank, never a default.
const LABELS = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  "highly confidential": "Highly Confidential",
  restricted: "Highly Confidential",
};

export const classificationLabel = (value) =>
  typeof value === "string" ? LABELS[value.trim().replace(/[\s_-]+/g, " ").toLowerCase()] || "" : "";
