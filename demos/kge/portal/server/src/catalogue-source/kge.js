export function openKgeCatalogueSource(kge) {
  return {
    identity: () => kge.catalogueIdentity(),
    load: () => kge.catalogue(),
  };
}
