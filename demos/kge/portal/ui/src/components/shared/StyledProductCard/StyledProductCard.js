import React, { useEffect, useState } from "react";
import Card from "@mui/material/Card";
import CardActions from "@mui/material/CardActions";
import CardContent from "@mui/material/CardContent";
import { CircularProgress } from "@mui/material";
import Typography from "@mui/material/Typography";
import { Grid } from "@mui/material";
import { Box, Popover, withStyles } from "@material-ui/core";
import { useNavigate } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import { selectedProduct, selectedProductName } from "../../../features/dataProduct/dataProductSlice";
import { Favorite, FavoriteBorderOutlined } from "@material-ui/icons";
import './StyledProductCard.css'
import HoverPopup from "../HoverPopup/HoverPopup";
import { saveFavouriteProduct, removeFavouriteProduct } from "../../../services/user";
import { searchResults } from "../../../features/search/searchSlice";
import { Tooltip } from "antd";
import { DatabaseOutlined } from "@ant-design/icons";
import GoldBadge from "../../../assets/icons/gold.png";
import SilverBadge from "../../../assets/icons/silver.png";
import BronzeBadge from "../../../assets/icons/bronze.png";
import EmptyBadge from "../../../assets/icons/empty.png";

// const HtmlTooltip = withStyles((theme) => ({
//   tooltip: {
//     width: 'max-content',
//     padding: '1rem',
//     position: 'absolute',
//     backgroundColor: "#FFFFFF",
//     opacity: 1,
//     backgroundColor: "#55585A",
//     boxShadow: '0px 2px 1px -1px rgba(0,0,0,0.2),0px 1px 1px 0px rgba(0,0,0,0.14),0px 1px 3px 0px rgba(0,0,0,0.12)',
//   }
// }))(Tooltip);

export default function StyledProductCard(props) {
  const [card, setCard] = useState(props.cardData);
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const searchResultsData = useSelector((state) => state.search.searchResults);

  useEffect(() => {
    setCard(props.cardData);
  }, [props]);

  const gotoProductDetails = (event, id) => {
    dispatch(selectedProduct(id));
    if (event.target.value && event.target.value === "product-card-checkbox") {
      event.stopPropagation();
    } else {
      navigate(`/productDetails/${id}`);
    }
  };

  const updateFavouriteValue = (id, value) => {
    return searchResultsData.map(item => {
      if (item.PRODUCTID === id) {
        return { ...item, FAVOURITED: value, SUBSCRIBE_COUNT: value ? item.SUBSCRIBE_COUNT + 1 : item.SUBSCRIBE_COUNT - 1 }
      }
      return item;
    });
  };

  const handleChange = (product) => {
    let favouritedProduct;
    const params = {
      productId: product.PRODUCTID,
      applicationId: product.APPLICATION_IDENTIFIER,
      technicalName: product.PRODUCTNAME
    }
    if (product.FAVOURITED) {
      removeFavouriteProduct(params).then((res) => {
        if (!res || !res.status || res.status === 'Fail') {
          console.log('Error', res.message);
        } else {
          favouritedProduct = updateFavouriteValue(product.PRODUCTID, false);
          dispatch(searchResults(favouritedProduct));
        }
      }).catch((error) => {
        console.log(error);
      });
    } else {
      saveFavouriteProduct(params).then((res) => {
        if (!res || !res.status || res.status === 'Fail') {
          console.log('Error', res.message);
        } else {
          favouritedProduct = updateFavouriteValue(product.PRODUCTID, true);
          dispatch(searchResults(favouritedProduct));
        }
      }).catch((error) => {
        console.log(error);
      });
    }
  };

  const getBadge = (val) => {
    if (val === 'EMPTY') {
      return EmptyBadge;
    } else if (val === 'BRONZE') {
      return BronzeBadge;
    } else if (val === 'SILVER') {
      return SilverBadge;
    }
    return GoldBadge;
  }

  const getBadgeLegend = (dqScore) => {
    if (dqScore >= 0 && dqScore < 10) {
      return 'Low Trust with Data Quality between 0-9% or no ownership/stewardship indicated.';
    } else if (dqScore >= 10 && dqScore < 59) {
      return 'Moderate Trust with Data Quality between 10-59%.';
    } else if (dqScore >= 60 && dqScore < 89) {
      return 'High Trust with Data Quality between 60-89%.';
    }
    return 'Highest Trust with Data Quality above 90% and ownership in place.';
  }

  return (
    <>
      {card ? (
        <Card
          sx={{ boxShadow: 3 }}
          id="card"
        // onClick={(e) => {
        //   gotoProductDetails(e, card.PRODUCTID);
        //   dispatch(selectedProductName(card.PRODUCTNAME));
        // }}
        >
          {/* <CardMedia
            component="span"
            sx={{
              // 16:9
              pt: "18%",
            }}
          />
         
          <Box>
            <Grid>
              <Box className="box">
                {card.favorited ? <Favorite /> : <FavoriteBorderOutlined /> }
              </Box>
              <Typography
                gutterBottom
                variant="h4"
                className="overlay"
              >
                {card.PRODUCTNAME?.length < 15
                  ? `${card.PRODUCTNAME}`
                  : `${card.PRODUCTNAME?.substring(0, 15)}...`}
              </Typography>
            </Grid>
          </Box> */}
          {/* Card content */}
          <Grid container spacing={4} className="header-style"  >
            <Grid item xs={1} className="productTypeIcon" onClick={(e) => {
              if (card.TYPE !== "column") {
                gotoProductDetails(e, card.PRODUCTID);
                dispatch(selectedProductName(card.PRODUCTNAME));
              }
            }}>
              {card.PRODUCTTYPE === 'Table' && <DatabaseOutlined />}
              {card.PRODUCTTYPE === 'Database View' && <DatabaseOutlined />}
            </Grid>
            <Tooltip title={card.PRODUCTDISPLAYNAME} color="#55585A" overlayInnerStyle={{fontSize: '1.8rem', width: "max-content"}} key={card}>
              <Grid item xs={9} className="product-name" onClick={(e) => {
                if (card.TYPE !== "column") {
                  gotoProductDetails(e, card.PRODUCTID);
                  dispatch(selectedProductName(card.PRODUCTNAME));
                }
              }}>
                {/* <Typography variant="h4">
                  {card.PRODUCTDISPLAYNAME?.length < 20
                    ? `${card.PRODUCTDISPLAYNAME}`
                    : `${card.PRODUCTDISPLAYNAME?.substring(0, 20)}...`}
                </Typography> */}
                 <Typography variant="h4" className="title">
                  {card.PRODUCTDISPLAYNAME}
                </Typography>
              </Grid>
            </Tooltip>
            <Grid item xs={1} className="badge">
              <Box className="score-badge"><img className="score-img" src={getBadge(card.BADGE_NAME)} alt="Trust Score" />
               {/* <Tooltip title={getBadgeLegend(card.DATA_PRODUCT_DQ_SCORE)} color="#55585A" overlayInnerStyle={{fontSize: '1.8rem', width: "max-content"}} key={card}>
                <img className="score-img" src={getBadge(card.DATA_PRODUCT_DQ_SCORE)} alt="Trust Score" /></Tooltip>} */}
              </Box>
            </Grid>
            <Grid item xs={1}>              
              <Box className="box" onClick={() => {
                handleChange(card)
              }}>
                {card.TYPE !== "column" ?
                  card.FAVOURITED ? <Favorite /> : <FavoriteBorderOutlined /> : <></>}
              </Box>
            </Grid>
          </Grid>
          {/* <HtmlTooltip
            title={
              <HoverPopup data={card} />
            }
          > */}
          <CardContent sx={{ flexGrow: 1 }} className="content"
            onClick={(e) => {
              if (card.TYPE !== "column") {
                gotoProductDetails(e, card.PRODUCTID);
                dispatch(selectedProductName(card.PRODUCTNAME));
              }
            }}>
            <Typography variant="h5" className="appname" limit={5} color="textPrimary">
              <span>{card.PRODUCTTYPE}</span>
            </Typography>
            <Typography variant="h5" className="appname" limit={5} color="textPrimary">
              Data Asset Source: <span>{card.PRODUCTSOURCE}</span>
            </Typography>
            <Typography variant="h6" className="appname" color="textPrimary">
              Community: <span>{`${card.COMMUNITY}`}</span>
            </Typography>
            <Typography variant="h6" className="appname" color="textPrimary">
              Domain: <span>{`${card.DOMAIN}`}</span>
            </Typography>
            <Typography variant="h5" className="description" limit={5} color="textPrimary">
              {/* {card.DESCRIPTION} */}
              {card.DESCRIPTION?.length < 199
                ? `${card.DESCRIPTION}`
                : `${card.DESCRIPTION?.substring(0, 200)}...`}
            </Typography>
          </CardContent>
          {/* </HtmlTooltip> */}
          {/* Card Footer */}
          {card.TYPE !== "column" ?
            <CardActions>
              <Grid container sx={{ flexGrow: 1 }} spacing={2} style={{ backgroundColor: "#FFFFFF" }}>
                <Grid item xs={8} md={8}>
                  <Typography variant="h6" className="footer-left" color="textPrimary">Last Refresh:{" "}{card.LAST_REFRESH}</Typography>
                </Grid>
                <Grid item xs={4} md={4}>
                  <Typography variant="h6" className="footer" color="textPrimary">
                    {" "}
                    {card.SUBSCRIBE_COUNT === null ? `0 likes` : card.SUBSCRIBE_COUNT === 1 ? `${card.SUBSCRIBE_COUNT} like` : `${card.SUBSCRIBE_COUNT} likes`}
                  </Typography>
                </Grid>
              </Grid>
            </CardActions> : <></>}
        </Card>
      ) : (
        <CircularProgress disableShrink />
      )}
    </>
  );
};
