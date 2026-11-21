import { useCaseEntries } from "../catalogue-record.js";

// KGE's use cases read as the portal resolves them (#599), so a KGE serving an
// asset's use cases in the inline shape before #599 still satisfies the contract.
const resolved = (catalogue) => catalogue && Array.isArray(catalogue.products)
  ? { ...catalogue, products: catalogue.products.map((product) => product && Array.isArray(product.use_cases) ? { ...product, use_cases: useCaseEntries(product.use_cases) } : product) }
  : catalogue;

export function openKgeCatalogueSource(kge) {
  return {
    identity: () => kge.catalogueIdentity(),
    load: async () => resolved(await kge.catalogue()),
  };
}
