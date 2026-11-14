import React, { useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import { Popover } from "@material-ui/core";
import PopupState, { bindPopover } from "material-ui-popup-state/index";
import StyledButtonWithIcon from "./StyledButtonWithIcon";
import MultiSelectMenu from "./MultiSelectMenu";
import SingleSelectMenu from "./SingleSelectMenu";

const styles = (theme) => ({
  popOver: {
    width: "auto",
  },
});

const PopOverMenuButton = (props) => {
  const { classes } = props;
  const [openMenu, setOpenMenu] = useState(false);

  return (
    <>
      <PopupState variant="popover" popupId="demo-popup-popover">
        {(popupState) => (
          <div>
            <StyledButtonWithIcon
              name={props.name}
              icon={props.icon}
              popupState={popupState}
            />
            <Popover
              className={classes.popOver}
              {...bindPopover(popupState)}
              anchorOrigin={{
                vertical: "bottom",
                horizontal: "left",
              }}
              transformOrigin={{
                vertical: "top",
                horizontal: "center",
              }}
              marginThreshold={"-1rem"}
              PaperProps={{
                style: {
                  maxHeight: "40rem",
                },
              }}
              onScroll={setOpenMenu(true)}
            >
              {props.singleSelectMenu ? (
                <SingleSelectMenu data={props.data} action={props.name} popupState={popupState} />
              ) : (
                <MultiSelectMenu data={props.data} from={props.from} popupState={popupState} open={openMenu} />
              )}
            </Popover>
          </div>
        )}
      </PopupState>
    </>
  );
};

PopOverMenuButton.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(PopOverMenuButton);
