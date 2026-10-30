import { getApi, postApi } from "../api/api";

export const getFields = async (productId) => {
  let request = {
    api: `/fields/${productId}`,
  };
  return getApi(request);
};

export const getDataQuality = async (productId, dqType) => {
  let request = {
    api: `/dataQuality/${productId}/${dqType}`,
  };
  return getApi(request);
};

export const getLineage = async (productId) => {
  let request = {
    api: `/lineage/${productId}`,
  };
  return getApi(request);
};

export const getProduct = async (productId) => {
  let request = {
    api: `/product/${productId}`,
  };
  return getApi(request);
};

export const submitFeedback = async (productId, rating, comment) => postApi({
  api: "/submit-feedback",
  params: { productId, rating, comment },
});

export const getFilteredFields = async (filterParams) => {
  let request = {
    api: "/filteredFields",
    params: filterParams,
  };
  return postApi(request);
};

export const getQueryColumns = (productId) => getApi({ api: `/api/preview/${encodeURIComponent(productId)}?limit=1` });

export const getDataPreview = async (previewParams) => {
  let request = {
    api: "/get-data-preview",
    params: previewParams,
  };
  return postApi(request);
};

export const getDataExport = async (requestId) => {
  let request = {
    api: `/get-data-export/${requestId}`,
  };
  return getApi(request);
};

export const executeQuery = async (sqlText) => {
  let request = {
    api: "/execute-query",
    params: sqlText,
  };
  return postApi(request);
};
