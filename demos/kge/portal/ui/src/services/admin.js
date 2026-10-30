import { getApi, postApi } from "../api/api";

export const getPublishSearchData = async (searchVal) => {
    let request = {
      api: `/getPublishSearchData/${searchVal}`,
    };
    return getApi(request);
};

export const getPublishedSearchResult = async (searchVal) => {
    let request = {
        api: `/getPublishedSearchResult/${searchVal}`,
      };
      return getApi(request);
};

export const getPublishedStatus = async () => {
  let request = {
    api: "/getPublishedStatus",
  };
  return getApi(request);
};

export const updatePublishedStatus = async (params) => {
  let request = {
    api: "/updatePublishedStatus",
    params: params,
  };
  return postApi(request);
}

export const updatePreviewStatus = async (params) => {
  let request = {
    api: "/updatePreviewStatus",
    params: params,
  };
  return postApi(request);
}

export const publishStatusLiveSync = async () => {
    let request = {
      api: "/publishStatusLiveSync",
    };
    return getApi(request);
};

export const updateApplicationLiveSync = async () => {
  let request = {
    api: "/updateApplicationLiveSync",
  };
  return getApi(request);
};

export const getDomainList = async () => {
  let request = {
    api: "/getDomainList",
  };
  return getApi(request);
}

export const getFilterResult = async (filterVal) => {
  let request = {
      api: `/getFilterResult/${filterVal}`,
    };
    return getApi(request);
};

export const getUserData = async () => {
  let request = {
      api: `/getUserData`,
    };
    return getApi(request);
};