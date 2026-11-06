import { Container, Grid } from "@material-ui/core";
import StyledProductCard from "./shared/StyledProductCard/StyledProductCard";
import React, { useEffect, useState } from "react";
import PaginationButton from "./shared/Pagination/PaginationButton";
import PropTypes from "prop-types";

const FeaturedDataProducts = (props) => {
  const [featuredDataProducts, setFeaturedDataProducts] = useState([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [productsPerPage] = useState(props.itemsPerPage);
  const [currentProducts, setCurrentProducts] = useState(
    featuredDataProducts.slice(0, props.itemsPerPage)
  );
  useEffect(() => {
    if(props.data.length <= 12) {
      setCurrentPage(1);
    }
    const indexOfLastProduct = currentPage * productsPerPage;
    const indexOfFirstProduct = indexOfLastProduct - productsPerPage;
    setFeaturedDataProducts(props.data)
    setCurrentProducts(
      featuredDataProducts.slice(indexOfFirstProduct, indexOfLastProduct)
    );
  }, [currentPage, featuredDataProducts, productsPerPage, props]);

  let switchPages = (value) => {
    setCurrentPage(value);
  };
  return (
    <>
      <main>
        <Container sx={{ py: 8, display: "flex" }} maxWidth={false}>
          <Grid container spacing={4}>
            {currentProducts.map((card, index) => {
              return (
                <Grid item key={index} xs={12} sm={6} md={3}>
                  <StyledProductCard cardData={card} selectItem = {props.selectItem} />
                </Grid>
              );
            })}
          </Grid>
          {featuredDataProducts &&
            featuredDataProducts.length &&
            featuredDataProducts.length > productsPerPage && (
              <PaginationButton
                data={featuredDataProducts}
                itemsPerPage={productsPerPage}
                onClickChange={switchPages}
              />
            )}
        </Container>
      </main>
    </>
  );
};

FeaturedDataProducts.propTypes = {
  selectItem: PropTypes.func.isRequired,
};

export default FeaturedDataProducts;
