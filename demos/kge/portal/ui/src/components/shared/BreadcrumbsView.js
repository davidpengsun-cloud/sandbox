import React from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import Breadcrumbs from "@material-ui/core/Breadcrumbs";
import NavigateNextIcon from "@material-ui/icons/NavigateNext";
import { Typography } from "@material-ui/core";
import { NavLink } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import { getSearchResults } from "../../services/search";
import { getFavouriteDataProducts } from "../../services/user";
import { communityFilter, domainFilter, hierarchyList, searchResults, sourceFilter, typeFilter } from "../../features/search/searchSlice";

const styles = (theme) => ({
  main: {
    // marginBottom: "2rem",
  },
  link: {
    fontSize: "2rem",
    fontFamily: "Inter",
    fontWeight: 500,
    color: "#000000"
  },
  typography: {
    fontFamily: "Inter",
    fontWeight: 600,
    color: "#00345F"
  },
  nextIcon: {
    fontSize: "2rem",
    fontWeight: 500,
    color: "#000000"
  },
});

const BreadcrumbsView = (props) => {
  const { classes } = props;
  let breadcrumbs;
  const dispatch = useDispatch();
  const searchResultsArray = useSelector((state) => state.search.searchResults);

  const triggerSearch = () => {
    if (searchResultsArray.length === 0) {
      Promise.all([
        getSearchResults({ searchParam: null }),
        getFavouriteDataProducts()
      ]).then((data) => {
        if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
          console.log('Error', data);
        } else {
          const result = data[0].data.searchList.map(item => ({
            ...item,
            FAVOURITED: data[1].data.find(d => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false
          }));
          dispatch(searchResults(result));
          dispatch(domainFilter(data[0].data.domainFilter));
          dispatch(communityFilter(data[0].data.communityFilter));
          dispatch(sourceFilter(data[0].data.sourceFilter));
          dispatch(typeFilter(data[0].data.typeFilter));
          dispatch(hierarchyList(data[0].data.hierarchy));
        }
      }).catch((error) => {
        console.log(error);
      });
    }
  }

  const searchBreadcrumb = [
    <NavLink to="/home" className={classes.link}>
      HOME
    </NavLink>,
    <Typography variant="h5" className={classes.typography}>
      EXPLORE
    </Typography>,
  ];

  const productDetailsBreadcrumb = [
    <NavLink to="/home" className={classes.link}>
      HOME
    </NavLink>,
    <NavLink to="/search" className={classes.link} onClick={() => triggerSearch()}>
      EXPLORE
    </NavLink>,
    <Typography variant="h5" className={classes.typography}>
      {props.name}
    </Typography>,
  ];

  if (props.page === "search") {
    breadcrumbs = searchBreadcrumb;
  } else if (props.page === "productDetails") {
    breadcrumbs = productDetailsBreadcrumb;
  }

  return (
    <Breadcrumbs
      separator={<NavigateNextIcon className={classes.nextIcon} />}
      arial-label="Breadcrumb"
      className={classes.main}
    >
      {breadcrumbs}
    </Breadcrumbs>
  );
};

BreadcrumbsView.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(BreadcrumbsView);
