import { getApi, postApi } from "../api/api";

export const getSearchResults = async (params) => {
  let request = {
    api: "/search",
    params: params,
  };
  return postApi(request);
};

export const getCommunityProducts = async (params) => {
  let request = {
    api: "/getCommunityProducts",
    params: params,
  };
  return postApi(request);
}

export const sortSearchResults = async (params) => {
  let request = {
    api: "/sortSearchResults",
    params: params,
  };
  return postApi(request);
};

export const groupSearchResults = async (params) => {
  let request = {
    api: "/groupSearchResults",
    params: params,
  };
  return postApi(request);
};

export const filterSearchResults = async (params) => {
  let request = {
    api: "/filterSearchResults",
    params: params,
  };
  return postApi(request);
};

export const getSearchData = async (searchVal) => {
  let request = {
    api: `/getSearchData/${searchVal}`,
  };
  return getApi(request);
};
