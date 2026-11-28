import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import {
  List,
  ListItem,
  ListItemText,
  Collapse,
  Grid,
} from "@material-ui/core";
import { ExpandMore, ExpandLess } from "@material-ui/icons";
import DropdownWithSearch from "./DropdownWithSearch";
import { filterSearchResults } from "../../services/search";
import { filterOptions, searchResults } from "../../features/search/searchSlice";
import { useDispatch, useSelector } from "react-redux";

const styles = (theme) => ({
  filterListData: {
    width: "100%",
    maxWidth: 360,
    display: "contents",
    overflowY: "auto",
  },
  listItemText: {
    paddingLeft: "0rem",
    font: "400 15px / 20px Inter",
  },
  expandIcon: {
    marginLeft: "6rem",
    fontSize: "2rem",
  },
});

const MultiSelectMenu = (props) => {
  const { classes } = props;
  const dispatch = useDispatch();
  const searchValue = useSelector((state) => state.search.searchParam);
  let filterParamsOptions = useSelector((state) => state.search.filterOptions);
  const userData = useSelector((state) => state.user.data);
  let [filterList, setFilterList] = useState(props.data);

  let setInitialFilterList = (data) => {
    let updatedFilterList = data.map((item) => {
      filterParamsOptions.map((option) => {
        if (item.key === option.parent && !item.selectedSubmenu.includes(option)) {
          item.selectedSubmenu.push(option);
        }
      })
      return item;
    })
    return updatedFilterList;
  }

  const handleClick = (filterKey) => () => {
    setFilterList(
      filterList.map((item) => {
        if (item.key !== filterKey && item.expanded) {
          item.expanded = !item.expanded;
        }
        if (item.key === filterKey) {
          item.expanded = !item.expanded;
        }
        return item;
      })
    );
  };

  let setSelectedOptions = (options, parent) => {
    let filterParams = [];
    setFilterList(
      filterList.map((item) => {
        if (item.key === parent) {
          item.selectedSubmenu = options;
        }
        filterParams = filterParams.concat(item.selectedSubmenu);
        return item;
      })
    );
    dispatch(filterOptions(filterParams));
    // let params = {};
    // if (props.from === "search") {
    //   params = {
    //     searchParam: searchValue,
    //     filterParams: filterParams,
    //   };
    //   filterSearchResults(params).then((res) => {
    //     props.popupState.close();
    //     if (!res || !res.data) {
    //       console.log('Error', res);
    //     } else {
    //       dispatch(searchResults(res.data));
    //     }
    //   }).catch((error) => {
    //     console.log(error);
    //   });
    // }
  };

  useEffect(() => {
    setFilterList(setInitialFilterList(filterList))
  }, []);

  return (
    <>
      {filterList.map((filter, index) => (
        <List key={index} component="nav" className={classes.filterListData}>
          <ListItem button onClick={handleClick(filter.key)}>
            <ListItemText
              inset
              primary={filter.menu}
              className={classes.listItemText}
            />
            {filter.expanded ? (
              <ExpandLess className={classes.expandIcon} />
            ) : (
              <ExpandMore className={classes.expandIcon} />
            )}
          </ListItem>
          <Collapse in={filter.expanded} timeout="auto" unmountOnExit>
            <Grid style={{ margin: "1rem 2rem 1rem 2rem" }}>
              <DropdownWithSearch
                options={filter.submenu}
                value={filter.selectedSubmenu}
                parent={filter.key}
                getSelectedOptions={setSelectedOptions}
                open={props.open}
              />
            </Grid>
          </Collapse>
        </List>
      ))}
    </>
  );
};

MultiSelectMenu.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(MultiSelectMenu);
