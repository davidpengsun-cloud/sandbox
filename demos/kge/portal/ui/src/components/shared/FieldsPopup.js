import React from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import { Button, Grid, Paper, Typography } from "@material-ui/core";

const styles = (theme) => ({
  fieldsPopup: {
    borderRadius: "0.5rem",
    borderStyle: "solid",
    borderColor: "#2E2E38",
    maxWidth: "70rem",
    height: "auto",
    borderWidth: "thick",
  },
  title: {
    textDecorationLine: "underline",
  },
  subTitle: {
    fontSize: "1.6rem",
    fontWeight: 600,
    marginTop: "1rem",
  },
  content: {
    fontSize: "1.6rem",
    fontWeight: 400,
  },
  button: {
    textTransform: "none",
    fontSize: "1.6rem",
    fontWeight: 700,
    borderRadius: "1rem",
    height: "4rem",
    width: "12rem",
    backgroundColor: "#55585A",
    color: "#000000",
  },
  buttonMain: {
    display: "flex",
    flexWrap: "wrap",
    justifyContent: "center",
    marginTop: "3rem",
  },
});

const FieldsPopup = (props) => {
  const { classes } = props;

  return (
    <Paper className={classes.fieldsPopup}>
      <Grid container style={{ margin: "2rem" }}>
        <Grid container item xs={12} spacing={24} style={{ margin: "2rem" }}>
          <Grid item xs={12}>
            <Typography variant="subtitle2" className={classes.title}>
              Field Overview
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Business Name</Typography>
            <Typography className={classes.content}>
              {props.rowData['Business Name']}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Technical Name
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.TechnicalName}
            </Typography>
          </Grid>
          <Grid item xs={12}>
            <Typography className={classes.subTitle}> Description</Typography>
            <Typography className={classes.content}>
              {props.rowData['Description']}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Data Type</Typography>
            <Typography className={classes.content}>
              {props.rowData['Data Type']}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Data Classification
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.DataClassification}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> APP Element</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.APPElement}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Sensitive Data
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.SensitiveData}
            </Typography>
          </Grid>
          <Grid item xs={12}>
            <Typography
              variant="subtitle2"
              className={classes.title}
              style={{ paddingTop: "1rem" }}
            >
              Data Profile
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Null Count</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.NullCount}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Null Percentage
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.NullPercentage}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Distinct Count
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.DistinctCount}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}>
              {" "}
              Distinct Percentage
            </Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.DiscountPercentage}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Min. Value</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.MinValue}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Max. Value</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.MaxValue}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Min. Length</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.MinLength}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography className={classes.subTitle}> Max. Length</Typography>
            <Typography className={classes.content}>
              {props.fieldPopupData.MaxLength}
            </Typography>
          </Grid>
          <Grid item xs={12} className={classes.buttonMain}>
            <Button
              variant="contained"
              color="textSecondary"
              className={classes.button}
              onClick={props.popupState.close}
            >
              Close
            </Button>
          </Grid>
        </Grid>
      </Grid>
    </Paper>
  );
};

FieldsPopup.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(FieldsPopup);
