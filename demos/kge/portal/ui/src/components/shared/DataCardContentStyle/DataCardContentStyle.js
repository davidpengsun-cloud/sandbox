import { Grid, Typography } from "@material-ui/core";
import { useNavigate } from "react-router-dom";
import { useDispatch } from "react-redux";
import { selectedProduct, selectedProductName } from "../../../features/dataProduct/dataProductSlice";
import './DataCardContentStyle.css';

export default function DataCardContentStyle(props) {
    const navigate = useNavigate();
    const dispatch = useDispatch();
    let dataList = props.content.data;
    let type = props.content.type;
    return (
        <>
            {dataList.length > 0 ? (
                <Grid container spacing={3} className="main">
                    {dataList.map((data) => (
                        <>
                            <Grid item xs={10} className="contentLeft" onClick={() => { navigate(`/productDetails/${data.DATA_PRODUCT_ID}`); dispatch(selectedProductName(data.TECHNICAL_NAME)); dispatch(selectedProduct(data.DATA_PRODUCT_ID)); }}>
                                <Typography variant="body2" color="textPrimary" className="card-subTitle">{data.PRODUCT_NAME}</Typography>
                                {/* <Typography variant="h5" color="textPrimary" className="newLine">{data.DATA_DOMAIN_NAME}</Typography> */}
                                {/* <Typography variant="h5" color="textPrimary" className="newLine">{data.BUSINESS_APPLICATION_NAME}</Typography> */}
                                {/* <Typography variant="h5" color="textPrimary" className="subContent">{data.DATA_PRODUCT_TYPE}</Typography> */}
                            </Grid>
                            <Grid item xs={2}><Typography variant="h6" color="textPrimary">{data.SUBSCRIBERS === null ? `0 likes` : data.SUBSCRIBERS === 1 ? `${data.SUBSCRIBERS} like` : `${data.SUBSCRIBERS} likes`}</Typography></Grid>
                        </>
                    ))}
                </Grid>
            ) : (
                <></>
            )}
        </>
    )
}