import { createSlice } from "@reduxjs/toolkit";

const initialState = {
	isLoggedIn: false,
	data: {},
};

export const userSlice = createSlice({
	name: "user",
	initialState,
	reducers: {
		userLogin: (state, action) => {
			state.isLoggedIn = action.payload ?? true;
		},
		userDetails: (state, action) => {
			state.data = action.payload;
		},
	},
});

export const { userLogin, userDetails } = userSlice.actions;

export default userSlice.reducer;
