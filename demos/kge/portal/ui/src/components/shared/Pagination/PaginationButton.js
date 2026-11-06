import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import { Grid } from "@material-ui/core";
import { Pagination, PaginationItem } from "@mui/material";
import './PaginationButton.css'

const styles = (theme) => ({
  paginationContainer: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-end",
    

    "& .MuiSvgIcon-root": {
      fontSize: "x-large",
    },
  },
});

const PaginationButton = (props) => {
  const { classes } = props;
  const [productsPerPage] = useState(props.itemsPerPage);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageCount, setPageCount] = useState(
    Math.ceil(props.data.length / productsPerPage)
  );

  const handleChange = (event, value) => {
    setCurrentPage(value);
    props.onClickChange(value);
  };

  useEffect(() => {
    setPageCount(Math.ceil(props.data.length / productsPerPage));
  }, [productsPerPage, currentPage, pageCount, props]);

  const stylePageButton = (item) => {
    if (item.type === "next") {
      return (
        <>
          <span className="pageCount">
            {currentPage + "/" + pageCount}
          </span>
          <PaginationItem size="xl" {...item}></PaginationItem>
        </>
      );
    } else if (item.type === "page") {
      return null;
    } else if (item.type === "end-ellipsis" || item.type === "start-ellipsis") {
      item.children = '';
    }
      else {
      return <PaginationItem {...item} />;
    }
  };

  return (
    <Grid className="paginationContainer">
      <Pagination
        count={pageCount}
        showFirstButton
        showLastButton
        size="large"
        defaultPage={1}
        onChange={handleChange}
        renderItem={stylePageButton}
      />
    </Grid>
  );
};

PaginationButton.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(PaginationButton);
