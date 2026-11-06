import React, { useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import PopupState, { bindPopover, bindTrigger } from "material-ui-popup-state";
import FilterAltIcon from "@mui/icons-material/FilterAlt";
import { Grid, Paper, Popover } from "@material-ui/core";
import { ArrowRight } from "@material-ui/icons";
import { getFilteredFields } from "../../services/metadata";
import { useDispatch } from "react-redux";
import { fieldsDataList } from "../../features/dataProduct/dataProductSlice";

const styles = (theme) => ({
  tableHeaderIcons: {
    color: "#FFFFFF",
    transform: "scale(2)",
    cursor: "pointer",
  },
  popOver: {
    width: "auto",
  },
  filter: {
    backgroundColor: "#FFFFFF",
    height: "3rem",
    display: "flex",
    flexDirection: "row",
    justifyContent: "center",
    overflow: "hidden",
  },
  searchBarInput: {
    height: "auto",
    width: "12rem",
    border: 0,
    fontWeight: 400,
    fontSize: "1.6rem",
    padding: "0 0 0 0.5rem",
    "&:focus": {
      outlineWidth: 0,
    },
  },
  filterAction: {
    height: "auto",
    width: "3rem",
    padding: 0,
    cursor: "pointer",
  },
  action: {
    backgroundColor: "#FFFFFF",
    height: "auto",
    overflow: "hidden",
    padding: "1rem",
  },
  actionItem: {
    fontSize: "1.6rem",
    fontWeight: 400,
    marginBottom: "0.5rem",
    borderBottom: "0.3rem solid #E7E3DF",
    cursor: "pointer",
    "&:hover": {
      backgroundColor: "#E7E3DF",
    },
    "&:focus": {
      backgroundColor: "#E7E3DF",
    },
    "&:active": {
      backgroundColor: "#E7E3DF",
    },
  },
});

const TextFilter = (props) => {
  const { classes } = props;
  const dispatch = useDispatch();
  const [searchValue, setSearchValue] = useState(null);

  const filterActions = [
    "Equals",
    "Does Not Equal",
    "Contains",
    "Does Not Contain",
  ];

  let triggerSearch = (event, searchState) => {
    setSearchValue(event.target.value)
    if (event.key === "Enter") {
      let filterParams = {
        field: props.field,
        value: searchValue,
        productId: props.productId,
      };
      getFilteredFields(filterParams).then((res) => {
        dispatch(fieldsDataList(res.data))
        searchState.close();
      });
    }
  };

  let triggerAction = (action, actionState, searchState) => {
    if (searchValue !== null) {
      let filterParams = {
        field: props.field,
        value: searchValue,
        action: action,
        productId: props.productId,
      };

      getFilteredFields(filterParams).then((res) => {
        dispatch(fieldsDataList(res.data))
        closeAllPopups(actionState, searchState);
      });
    }
  };

  let closeAllPopups = (actionState, searchState) => {
    actionState.close();
    searchState.close();
  };

  return (
    <>
      <PopupState variant="popover" popupId="text-filter-popover">
        {(popupState1) => (
          <div>
            <FilterAltIcon
              className={classes.tableHeaderIcons}
              {...bindTrigger(popupState1)}
            />
            <Popover
              className={classes.popOver}
              {...bindPopover(popupState1)}
              anchorOrigin={{
                vertical: "center",
                horizontal: "right",
              }}
              transformOrigin={{
                vertical: "top",
                horizontal: "right",
              }}
            >
              <Paper className={classes.filter}>
                <input
                  placeholder="Search"
                  className={classes.searchBarInput}
                  onKeyUp={(event) => triggerSearch(event, popupState1)}
                />
                <PopupState variant="popover" popupId="filter-actions-popover">
                  {(popupState2) => (
                    <div>
                      <ArrowRight
                        className={classes.filterAction}
                        {...bindTrigger(popupState2)}
                      />
                      <Popover
                        className={classes.popOver}
                        {...bindPopover(popupState2)}
                        onClose={() => closeAllPopups(popupState2, popupState1)}
                        anchorOrigin={{
                          vertical: "top",
                          horizontal: "right",
                        }}
                        transformOrigin={{
                          vertical: "top",
                          horizontal: "left",
                        }}
                      >
                        <Paper className={classes.action}>
                          {filterActions.map((action) => (
                            <Grid
                              className={classes.actionItem}
                              onClick={(event) =>
                                triggerAction(action, popupState2, popupState1)
                              }
                            >
                              {action}
                            </Grid>
                          ))}
                        </Paper>
                      </Popover>
                    </div>
                  )}
                </PopupState>
              </Paper>
            </Popover>
          </div>
        )}
      </PopupState>
    </>
  );
};

TextFilter.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(TextFilter);
