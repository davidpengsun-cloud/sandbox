import { createSlice } from "@reduxjs/toolkit";

const initialState = {
  searchResults: [],
  searchParam: null,
  browseScope: null,
  domainFilter: [],
  communityFilter: [],
  sourceFilter: [],
  typeFilter: [],
  hierarchyList: [],
  filterOptions: [],
  groupOption: [],
  sortOption: [],
  domainSelected: false,
};

const formatArrayToOptions = (array, value) => {
  let optionsList = [];
  array.map((item) =>
    optionsList.push({ value: item, label: item, parent: value })
  );
  return optionsList;
};

export const searchSlice = createSlice({
  name: "search",
  initialState,
  reducers: {
    searchResults: (state, action) => {
      state.searchResults = action.payload;
    },
    searchParam: (state, action) => {
      state.searchParam = action.payload;
      state.browseScope = null;
    },
    browseScope: (state, action) => {
      state.browseScope = action.payload;
    },
    domainFilter: (state, action) => {
      state.domainFilter = formatArrayToOptions(action.payload, "DOMAIN");
    },
    communityFilter: (state, action) => {
      state.communityFilter = formatArrayToOptions(action.payload, "COMMUNITY");
    },
    sourceFilter: (state, action) => {
      state.sourceFilter = formatArrayToOptions(action.payload, "PRODUCTSOURCE");
    },
    typeFilter: (state, action) => {
      state.typeFilter = formatArrayToOptions(action.payload, "PRODUCTTYPE");
    },
    hierarchyList: (state, action) => {
      state.hierarchyList = action.payload;
    },
    filterOptions: (state, action) => {
      state.filterOptions = action.payload;
    },
    groupOption: (state, action) => {
      state.groupOption = action.payload;
    },
    sortOption: (state, action) => {
      state.sortOption = action.payload;
    },
    domainSelected: (state, action) => {
      state.domainSelected = action.payload;
    },
  },
});

export const { searchResults, searchParam, browseScope, domainFilter, communityFilter, sourceFilter, typeFilter, hierarchyList, filterOptions, groupOption, sortOption, domainSelected } = searchSlice.actions;

export default searchSlice.reducer;
