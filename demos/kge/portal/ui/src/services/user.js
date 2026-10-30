import { getApi, postApi } from "../api/api";

export const getFavouriteDataProducts = async () => {
  let request = {
    api: "/getFavourites",
  };
  return getApi(request);
};

export const saveFavouriteProduct = async (params) => {
  let request = {
    api: "/saveFavouriteProduct",
    params: params,
  };
  return postApi(request);
}

export const removeFavouriteProduct = async (params) => {
  let request = {
    api: "/removeFavouriteProduct",
    params: params,
  };
  return postApi(request);
}

export const saveViewedDataProduct = async (params) => {
  let request = {
    api: "/saveViewedDataProduct",
    params: params,
  };
  return postApi(request);
}

export const getRelatedDataProducts = async () => {
  let request = {
    api: "/getRelatedProducts",
  };
  return getApi(request);
};

export const getViewedDataProducts = async () => {
  let request = {
    api: "/getMostViewedProducts",
  };
  return getApi(request);
};

export const getDataCommunities = async() => {
  let request = {
    api: "/getDataCommunities"
  };
  return getApi(request);
}

export const getConfig = async() => {
  let request = {
    api: "/getConfig"
  };
  return getApi(request);
}

// Access requests go to the portal's own queue (Admin › Access Requests), where
// an approver or admin decides them. Both calls are scoped to the signed-in
// person's readable products by the server.
export const requestAccess = async (productId) => postApi({
  api: "/api/requests",
  params: { productId, kind: "access" },
});

export const getMyRequests = async () => getApi({ api: "/api/requests" });

export const trackMarkeplaceUser = async (params) => {
  let request = {
    api: "/trackMarkeplaceUser",
    params: params,
  };
  return postApi(request);
}