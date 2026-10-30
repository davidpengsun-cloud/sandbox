import { createSlice } from "@reduxjs/toolkit";

const initialState = {
  recentDataProducts: [],
  recommendedDataProducts: [],
  selectedProduct: 0,
  selectedProductName: "",
  domainList: [],
  platformList: [],
  applicationNameList: [],
  dataProductTypeList: [],
  fieldsDataList: [],
  recentProductsError: "",
  recommendedProductsError: "",
};

const formatArrayToOptions = (array, value) => {
  let optionsList = [];
  array.map((item) =>
    optionsList.push({ value: item, label: item, parent: value })
  );
  return optionsList;
};

export const dataProductSlice = createSlice({
  name: "data product",
  initialState,
  reducers: {
    recentProductsArray: (state, action) => {
      state.recentDataProducts = action.payload;
    },
    recommendedProductsArray: (state, action) => {
      state.recommendedDataProducts = action.payload;
    },
    selectedProduct: (state, action) => {
      state.selectedProduct = action.payload;
    },
    selectedProductName: (state, action) => {
      state.selectedProductName = action.payload;
    },
    domainList: (state, action) => {
      state.domainList = formatArrayToOptions(action.payload, "Domain");
    },
    platformList: (state, action) => {
      state.platformList = formatArrayToOptions(action.payload, "Platform");
    },
    applicationNameList: (state, action) => {
      state.applicationNameList = formatArrayToOptions(
        action.payload,
        "ApplicationName"
      );
    },
    dataProductTypeList: (state, action) => {
      state.dataProductTypeList = formatArrayToOptions(
        action.payload,
        "ProductType"
      );
    },
    fieldsDataList: (state, action) => {
      state.fieldsDataList = action.payload;
    },
    recentProductsError: (state, action) => {
      state.recentProductsError = action.payload;
    },
    recommendedProductsError: (state, action) => {
      state.recommendedProductsError = action.payload;
    },
  },
});

export const {
  recentProductsArray,
  recommendedProductsArray,
  selectedProduct,
  selectedProductName,
  domainList,
  platformList,
  applicationNameList,
  dataProductTypeList,
  fieldsDataList,
  recentProductsError,
  recommendedProductsError,
} = dataProductSlice.actions;

export default dataProductSlice.reducer;
