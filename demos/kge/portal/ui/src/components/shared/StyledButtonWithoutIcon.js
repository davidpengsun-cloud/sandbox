import React, { useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import { Button } from "@material-ui/core";

const styles = (theme) => ({
	button: {
		textTransform: "none",
		fontWeight: 400,
		borderRadius: "3rem",
		height: "4rem",
    fontSize: "2rem",
    fontFamily: "Inter",
		padding: "0rem 3rem 0rem 3rem",
    backgroundColor: "#55585A",
    color: "#FFFFFF",
    "&:hover": {
      backgroundColor: "#55585A",
      color: "#FFFFFF",
    },
    [theme.breakpoints.down("sm")]: {
      fontSize: "2rem",
      fontFamily: "Inter",
    },
    [theme.breakpoints.between("sm", "md")]: {
      fontSize: "2rem",
      fontFamily: "Inter",
    },
    [theme.breakpoints.up("md")]: {
      fontSize: "2rem",
      fontFamily: "Inter",
    },
	},
});

const StyledButtonWithoutIcon = (props) => {
	const { classes } = props;
	const [isHover, setIsHover] = useState(false);
	return (
    <>
      <Button
        variant="contained"
        color={isHover ? "secondary" : "secondary"}
        className={classes.button}
        onClick={props.handleClick}
        onMouseEnter={() => setIsHover(true)}
        onMouseLeave={() => setIsHover(false)}
        disabled = {props.disabled}
      >
        {props.name}
      </Button>
    </>
  );
};

StyledButtonWithoutIcon.propTypes = {
	classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(StyledButtonWithoutIcon);
