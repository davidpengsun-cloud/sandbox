import React, { useEffect } from "react";
import { Grid, Paper, Typography, withStyles, Tooltip } from "@material-ui/core";
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import parse from 'html-react-parser';
// import { Tooltip } from "antd";
import './DataOverview.css';
import { saveViewedDataProduct } from '../../services/user';

const HtmlTooltip = withStyles((theme) => ({
  arrow: {
    color: "#55585A",
    fontSize: "2rem",
  },
  tooltip: {
    maxWidth: 800,
    width: 'max-content',
    padding: '1rem',
    // position: 'absolute',
    backgroundColor: "#55585A",
    borderRadius: "0.8rem"
  }
}))(Tooltip);

export default function DataOverview(props) {
  useEffect(() => {
    saveViewedDataProduct({ productId: props.data.PRODUCTID, productName: props.data.TECHNICALNAME, searchWord: null, category: 'Details', tab: 'DETAILS-OVERVIEW' });
  },[]);

  return (
    <Paper id="overviewRoot">
      <Grid container spacing={3} className="overviewMain">
        <Grid item xs={12}>
          <Paper className="overviewPaper">
            <Grid container item xs={12} spacing={24}>
              <Grid item xs={12}>
                <Typography variant="h3" className="title">
                  Ownership
                </Typography>
              </Grid>
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h5" className="subTitle">Data Domain Owner</Typography>
                  {/* <Tooltip title='Busniess Owner Description' color="#55585A" overlayInnerStyle={{fontSize: '1.5rem'}} key='Busniess Owner'>
                    <InfoOutlinedIcon className="info-icon" />
                  </Tooltip> */}
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>{props.config?.Owner}</Typography>
                    }
                    placement='top' arrow
                    key='Busniess Owner'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h4" className="content">
                  {props.data.BUSINESSOWNER}
                </Typography>
              </Grid>
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h5" className="subTitle">
                    Data Steward
                  </Typography>
                  {/* <Tooltip title='Data Product Owner Description' color="#55585A" overlayInnerStyle={{ fontSize: '1.5rem' }} key='Data Owner'>
                    <InfoOutlinedIcon className="info-icon" />
                  </Tooltip> */}
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>{props.config?.Steward}</Typography>
                    }
                    placement='top' arrow
                    key='Data Owner'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h4" className="content">
                  {props.data.PRODUCTOWNER}
                </Typography>
              </Grid>

            </Grid>
          </Paper>
        </Grid>
        <Grid item xs={12}>
          <Paper className="overviewPaper">
            <Grid container item xs={12} spacing={24}>
              <Grid item xs={12}>
                <Typography
                  variant="h3" className="title"
                >
                  Technical Metadata
                </Typography>
              </Grid>
              <Grid item xs={5} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Technical Name</Typography>
                <Typography variant="h4" className="content">
                  {props.data.TECHNICALNAME}
                </Typography>
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Schema</Typography>
                <Typography variant="h4" className="content">
                  {props.data.SCHEMANAME}
                </Typography>
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Source records (before read filters)</Typography>
                <Typography variant="h4" className="content">
                  {props.data.COUNTOFRECORDS}
                </Typography>
              </Grid>
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h5" className="subTitle">Data Set Quality Score</Typography>
                    <HtmlTooltip 
                        title={
                          <Typography style={{fontSize: "1.9rem"}} variant='h5'>{props.config?.DQ && parse(props.config.DQ)}</Typography>
                        }
                        placement='top' arrow
                        key='dq'
                      ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                    </HtmlTooltip>
                </Grid>
                {(props.data.PRODUCT_SCORE >= 90 && props.data.PRODUCT_SCORE <= 100) &&
                  <Typography variant="h4" className="content" style={{ color: '#09533B' }}>
                    {props.data.PRODUCT_SCORE} %
                  </Typography>
                }
                {(props.data.PRODUCT_SCORE >= 10 && props.data.PRODUCT_SCORE < 90) &&
                  <Typography variant="h4" className="content" style={{ color: '#FFBF00' }}>
                    {props.data.PRODUCT_SCORE} %
                  </Typography>
                }
                {(props.data.PRODUCT_SCORE < 10) &&
                  <Typography variant="h4" className="content" style={{ color: '#A13030' }}>
                    {props.data.PRODUCT_SCORE} %
                  </Typography>
                }
                {(props.data.PRODUCT_SCORE === 'N/A') &&
                  <Typography variant="h4" className="content" color="textPrimary">
                    {props.data.PRODUCT_SCORE}
                  </Typography>
                }
              </Grid>
              <Grid item xs={5} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Data Format</Typography>
                <Typography variant="h4" className="content">
                  {props.data.DATA_FORMAT}
                </Typography>
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Data Asset Type</Typography>
                <Typography variant="h4" className="content">
                  {props.data.PRODUCTTYPE}
                </Typography>
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Size</Typography>
                <Typography variant="h4" className="content">
                  {props.data.SIZE ? props.data.SIZE : 'N/A'}
                </Typography>
              </Grid>   
              <Grid item xs={3} className="overviewGrid">
                <Typography variant="h5" className="subTitle"># of Columns</Typography>
                <Typography variant="h4" className="content">
                  {props.data.COUNTOFCOLUMNS} {props.data.PARENT_DATASET_COUNT_OF_COLUMNS !== null ? 
                    `out of ${props.data.PARENT_DATASET_COUNT_OF_COLUMNS} (original dataset)` : ''}
                </Typography>
              </Grid>
              <Grid item xs={5} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Last Refresh</Typography>
                <Typography variant="h4" className="content">
                  {props.data.LAST_REFRESH}
                </Typography>
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Last DQ Refresh</Typography>
                <Typography variant="h4" className="content">
                  {!props.data.DQRUNDATE ? "N/A" : props.data.DQRUNDATE}
                </Typography>                           
              </Grid>
              <Grid item xs={2} className="overviewGrid">
                <Typography variant="h5" className="subTitle"># of Portal views</Typography>
                <Typography variant="h4" className="content">
                  {props.data.VIEWTIMES === null ? "0" : props.data.VIEWTIMES}
                </Typography>
              </Grid>
              <Grid item xs={3} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Data Preview Visible</Typography>
                <Typography variant="h4" className="content">
                  {!props.data.ISPREVIEW ? "N/A" : props.data.ISPREVIEW}
                </Typography>   
              </Grid>
              {/* <Grid item xs={3} className="overviewGrid">
                <Typography variant="h5" className="subTitle"># of Likes</Typography>
                <Typography variant="h4" className="content">
                  {!props.data.LIKESCOUNT ? "0" : props.data.LIKESCOUNT}
                </Typography>   
              </Grid> */}
              <Grid item xs={12} className="overviewGrid">
                <Typography variant="h5" className="subTitle">Path</Typography>
                <Typography variant="h4" className="contentPath">
                  {props.data.PATH.substring(props.data.PATH.indexOf('>') + 1)}
                </Typography>
              </Grid>
            </Grid>
          </Paper>
        </Grid>
      </Grid>
    </Paper>
  );
};
