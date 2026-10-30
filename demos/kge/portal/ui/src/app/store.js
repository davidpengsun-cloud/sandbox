import { configureStore } from "@reduxjs/toolkit";
import userReducer from "../features/user/userSlice";
import dataProductReducer from "../features/dataProduct/dataProductSlice"
import searchReducer from "../features/search/searchSlice"

const store = configureStore({
  reducer: {
    user: userReducer,
    dataProduct: dataProductReducer,
    search: searchReducer
  },
});

export default store