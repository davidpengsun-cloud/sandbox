import { getApi } from "../api/api";

/* The ODPS and ODCS exports (#613). The server builds each document from what
   the person may read; the page only saves the file it sends. The request goes
   through the shared API helper, so an expired session (401) sends the person
   back to sign in as every other call does, and any failure rejects. */

async function save(api, filename, params) {
  const response = await getApi({ api, params });
  const type = response.headers?.["content-type"] || "application/yaml";
  const address = URL.createObjectURL(new Blob([response.data], { type }));
  const link = document.createElement("a");
  link.href = address;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(address);
}

export const exportAssetOdps = (assetId) => save(`/product/${encodeURIComponent(assetId)}/odps`, `${assetId}.odps.yaml`);

export const exportUseCaseOdcs = ({ id, title }) =>
  save(`/use-cases/${encodeURIComponent(id)}/odcs`, `${id}.odcs.yaml`, title ? { title } : undefined);
