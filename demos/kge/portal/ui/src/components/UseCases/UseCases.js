import React, { useEffect } from "react";
import { Grid, Paper, Typography, withStyles, Tooltip } from "@material-ui/core";
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import parse from 'html-react-parser';
// import { Tooltip } from "antd";
import ExcelIcon from '../../assets/icons/icons8-excel-480.png';
import PowerBiIcon from '../../assets/icons/icons8-power-bi-480.png';
import GraphQLIcon from '../../assets/icons/icons8-graphql-480.png';
import './UseCases.css';
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
                  EOD Positions 
                </Typography>
              </Grid>
              <Grid item xs={12} className="content">
                <Typography variant="h5" className="content">
                  The End-of-Day (EOD) Positions Report provides a comprehensive snapshot of all fixed income holdings at the close of each trading day. It is used daily by trading and risk teams to validate trades, track exposure, and update dashboards and compliance workflows. 
                </Typography>
                {/* add some vertical space */}
                <div style={{ marginBottom: '5rem' }} />
              </Grid>
              {/* For Excel */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">Excel</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in an Excel Spreadsheet</Typography>
                    }
                    placement='top' arrow
                    key='Excel Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://www.microsoft.com/en-us/microsoft-365/excel" download target="_blank">
                    <img src={ExcelIcon} alt="Link to Excel Spreadsheet" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
              { /* For PowerBi */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">PowerBi</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in PowerBi</Typography>
                    }
                    placement='top' arrow
                    key='PowerBI Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://www.microsoft.com/en-us/power-platform/products/power-bi" target="_blank">
                    <img src={PowerBiIcon} alt="Link to  PowerBI" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
              {/* {For GraphQL} */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">GraphQL</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in GraphQL</Typography>
                    }
                    placement='top' arrow
                    key='GraphQL Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://graphql.org/" target="_blank">
                    <img src={GraphQLIcon} alt="Link to GraphQL" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
            </Grid>
          </Paper>
        </Grid>
        <Grid item xs={12}>
          <Paper className="overviewPaper">
            <Grid container item xs={12} spacing={24}>
              <Grid item xs={12}>
                <Typography variant="h3" className="title">
                  EOM Positions 
                </Typography>
              </Grid>
              <Grid item xs={12} className="content">
                <Typography variant="h5" className="content">
                  The End-of-Month (EOM) Positions Report captures a validated and often audited snapshot of fixed income positions as of the last trading day of the month. It serves as the official record for performance attribution, regulatory filings, and accounting. Primarily used by finance, risk, performance, and client reporting teams to support month-end close processes, portfolio reviews, board reporting, and external disclosures.
                </Typography>
                {/* add some vertical space */}
                <div style={{ marginBottom: '5rem' }} />
              </Grid>
              {/* For Excel */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">Excel</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in an Excel Spreadsheet</Typography>
                    }
                    placement='top' arrow
                    key='Excel Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://www.microsoft.com/en-us/microsoft-365/excel" download target="_blank">
                    <img src={ExcelIcon} alt="Link to Excel Spreadsheet" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
              { /* For PowerBi */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">PowerBi</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in PowerBi</Typography>
                    }
                    placement='top' arrow
                    key='PowerBI Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://www.microsoft.com/en-us/power-platform/products/power-bi" target="_blank" >
                    <img src={PowerBiIcon} alt="Link to  PowerBI" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
              {/* {For GraphQL} */}
              <Grid item xs={3} className="overviewGrid">
                <Grid container rowSpacing={2} columnSpacing={2}>
                  <Typography variant="h4" className="subTitle">GraphQL</Typography>
                  <HtmlTooltip
                    title={
                      <Typography variant='h5'>Link to the Live Data in GraphQL</Typography>
                    }
                    placement='top' arrow
                    key='GraphQL Link'
                  ><div style={{cursor: "pointer"}}><InfoOutlinedIcon className="info-icon" /></div>
                  </HtmlTooltip>
                </Grid>
                <Typography variant="h6" className="content">
                  <a href="https://graphql.org/" target="_blank">
                    <img src={GraphQLIcon} alt="Link to GraphQL" className="link-icons-class" />
                  </a>
                </Typography>
              </Grid>
            </Grid>
          </Paper>
        </Grid>
      </Grid>
    </Paper>
  );
};
