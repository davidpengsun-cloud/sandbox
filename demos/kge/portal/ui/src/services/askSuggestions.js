/* Discover's Ask suggested questions as an admin set them (Admin › Configuration), read once per page load, so a
   saved change applies from the next one. An empty list keeps the page's own questions. A failed read is not kept:
   the page shows its own questions this time and the next Ask opened reads again. */
let pending = null;
let known = null;

export const knownSuggestions = () => known;

export function loadSuggestions() {
  pending ??= Promise.resolve()
    .then(() => fetch("/api/ask-suggestions", { credentials: "same-origin" }))
    .then((response) => {
      if (!response.ok) throw new Error("Suggested questions unavailable");
      return response.json();
    })
    .then((body) => {
      known = { asset: body?.asset, domain: body?.domain };
      return known;
    })
    .catch(() => {
      pending = null;
      return {};
    });
  return pending;
}

export function forgetSuggestions() {
  pending = null;
  known = null;
}

/* The admin's list where it holds a question, else the page's own. */
export const chosenSuggestions = (list, standard) => (Array.isArray(list) && list.length ? list : standard);
