import React from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import { Button } from "@material-ui/core";
import { bindTrigger } from "material-ui-popup-state";

const styles = (theme) => ({
  button: {
    textTransform: "none",
    fontSize: "2rem",
    fontFamily: "Inter",
    borderRadius: "3rem",
    height: "4rem",
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
  icon: {
    marginRight: theme.spacing(),
    width: "3rem",
    height: "3rem",
  },
});

const StyledButtonWithIcon = (props) => {
  const { classes } = props;

  return (
    <>
      <Button
        variant="contained"
        className={classes.button}
        {...bindTrigger(props.popupState)}
      >
        <props.icon className={classes.icon} />
        {props.name}
      </Button>
    </>
  );
};

StyledButtonWithIcon.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(StyledButtonWithIcon);
