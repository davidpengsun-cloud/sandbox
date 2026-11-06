import { createTheme, responsiveFontSizes } from "@material-ui/core";

const theme = responsiveFontSizes(
	createTheme({
		palette: {
			background: {
				default: "#E7E3DF", //ecru light
			},
			primary: {
				main: "#FFFFFF", //white
			},
			secondary: {
				main: "#55585A", //grey
			},
			error: {
				main: "#A13030", //matchstick ADA
			},
			warning: {
				main: "#FC7B09", //orange
			},
			info: {
				main: "#6B7D6A", //gray
			},
			success: {
				main: "#09FE00", //green
			},
			green: {
				main: "#09533B", //green ADA
			},
			blue: {
				main: "#00345F", //saxe blue ADA
			},
			text: {
				primary: "#000000", //black
				secondary: "#FFFFFF", //white
				
			},
		},
		typography: {
			fontFamily: ["EB Garamond", "Inter"].join(","),
			h1: {
				fontSize: "90px/80px",
				fontFamily: "EB Garamond",
			},
			h2: {
				fontSize: "60px/60px",
				fontFamily: "EB Garamond",
			},
			h3: {
				fontSize: "3rem",
				fontFamily: "Inter",
				fontWeight: 500,
			},
			h4: {
				fontSize: "2.5rem",
				fontFamily: "Inter",
				fontWeight: 500,
			},
			h5: {
				fontSize: "2rem",
				fontFamily: "Inter",
			},
			h6: {
				fontSize: "2rem",
				fontFamily: "Inter",
			},
			subtitle1: {
				fontSize: "2rem",
				fontFamily: "Inter",
				fontWeight: 600,
			},
			subtitle2: {
				fontSize: "2rem",
				fontFamily: "Inter",
				fontWeight: 700, //bold
			},
			body1: {
				fontSize: "2.5rem",
				fontFamily: "Inter",
			},
			body2: {
				fontSize: "2.5rem",
				fontFamily: "Inter",
			},
			body3: {
				fontSize: "20px",
				fontFamily: "Inter",
			},
			body3_category: {
				fontSize: "12px",
				fontFamily: "Inter",
				fontWeight: 600,
			},
			caption: {},
			button: {
				fontSize: "16px",
				fontFamily: "Inter",
				fontWeight: 600,
				backgroundColor: "#55585A",
				color: "#FFFFFF"
			},
			overline: {},
		},
		breakpoints: {
			values: {
			  xs: 0,
			  sm: 480,
			  md: 768,
			  lg: 1280,
			  xl: 1920,
			},
		  },
	})
);

export default theme;
