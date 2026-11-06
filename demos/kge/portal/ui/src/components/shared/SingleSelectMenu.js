import React, { useState } from "react";
import PropTypes from "prop-types";
import { withStyles } from "@material-ui/core/styles";
import {
  FormControl,
  FormControlLabel,
  Radio,
  RadioGroup,
} from "@material-ui/core";
import { useDispatch, useSelector } from "react-redux";
import { groupSearchResults, sortSearchResults } from "../../services/search";
import { searchResults, groupOption, sortOption } from "../../features/search/searchSlice";
import clsx from 'clsx';
import { sortArray, sortArrayBasedOnDates } from "../../services/util";

const styles = (theme) => ({
  root: {
    display: "flex",
  },
  formControl: {
    margin: theme.spacing(2),
  },
  radioBtn: {
    color: "#2E2E38",
    fontSize: "2rem",
  },
  radioBtnLabel: {
    fontSize: "2rem",
    fontWeight: 400,
    margin: "1rem",
  },
  icon: {
    borderRadius: '50%',
    width: 16,
    height: 16,
    boxShadow: 'inset 0 0 0 1px #000000, inset 0 -1px 0 #000000',
    backgroundColor: '#f5f8fa',
    backgroundImage: 'linear-gradient(180deg,hsla(0,0%,100%,.8),hsla(0,0%,100%,0))',
    '$root.Mui-focusVisible &': {
      outline: '2px auto rgba(19,124,189,.6)',
      outlineOffset: 2,
    },
    'input:hover ~ &': {
      backgroundColor: '#ebf1f5',
    },
    'input:disabled ~ &': {
      boxShadow: 'none',
      background: 'rgba(206,217,224,.5)',
    },
  },
  checkedIcon: {
    backgroundColor: '#00345F',
    backgroundImage: 'linear-gradient(180deg,hsla(0,0%,100%,.1),hsla(0,0%,100%,0))',
    '&:before': {
      display: 'block',
      width: 16,
      height: 16,
      backgroundImage: 'radial-gradient(#fff,#fff 28%,transparent 32%)',
      content: '""',
    },
    'input:hover ~ &': {
      backgroundColor: '#00345F',
    },
  },
});

const SingleSelectMenu = (props) => {
  const { classes } = props;
  const dispatch = useDispatch();
  const searchValue = useSelector((state) => state.search.searchParam);
  const searchResultsArray = useSelector((state) => state.search.searchResults);
  let [state, setState] = useState();
  const groupParamOption = useSelector((state) => state.search.groupOption);
  const sortParamOption = useSelector((state) => state.search.sortOption);

  let sortSearch = (event) => {
    setState(event.target.value);
    let arrayForSort = [...searchResultsArray];
    if (props.action === "Sort by") {
      if(event.target.value === 'prdtNameA-Z') {
        // dispatch(searchResults(arrayForSort.sort(sortArray('PRODUCTDISPLAYNAME'))));
        dispatch(sortOption('prdtNameA-Z'));
        props.popupState.close();
      }
      if(event.target.value === 'prdtNameZ-A') {
        // dispatch(searchResults(arrayForSort.sort(sortArray('PRODUCTDISPLAYNAME', 'desc'))));
        dispatch(sortOption('prdtNameZ-A'));
        props.popupState.close();
      }
      if(event.target.value === 'publishDateNew-Old') {
        // dispatch(searchResults(arrayForSort.sort(sortArrayBasedOnDates('PUBLISHDATE', 'asc'))));
        dispatch(sortOption('publishDateNew-Old'));
        props.popupState.close();
      }
      if(event.target.value === 'publishDateOld-New') {
        // dispatch(searchResults(arrayForSort.sort(sortArrayBasedOnDates('PUBLISHDATE', 'desc'))));
        dispatch(sortOption('publishDateOld-New'));
        props.popupState.close();
      }
      // let params = {
      //   searchParam: searchValue,
      //   actionParam: event.target.value,
      // };
      // sortSearchResults(params).then((res) => {
      //   props.popupState.close();
      //   dispatch(searchResults(res.data));
      // });
    } else if (props.action === "Group by") {
      if(event.target.value === 'COMMUNITY') {
        // dispatch(searchResults(arrayForSort.sort(sortArray('COMMUNITY'))));
        dispatch(groupOption('COMMUNITY'));
        props.popupState.close();
      }
      if(event.target.value === 'DOMAIN') {
        // dispatch(searchResults(arrayForSort.sort(sortArray('DOMAIN'))));
        dispatch(groupOption('DOMAIN'));
        props.popupState.close();
      }
      if(event.target.value === 'PRODUCTTYPE') {
        // dispatch(searchResults(arrayForSort.sort(sortArray('PRODUCTTYPE'))));
        dispatch(groupOption('PRODUCTTYPE'))
        props.popupState.close();
      }
      // let params = {
      //   searchParam: searchValue,
      //   actionParam: event.target.value,
      // };
      // groupSearchResults(params).then((res) => {
      //   props.popupState.close();
      //   dispatch(searchResults(res.data));
      // });
    }
  };

  return (
    <div className={classes.root}>
      <FormControl component="fieldset" className={classes.formControl}>
        <RadioGroup
          aria-label="Options"
          name="options"
          value={state}
          onChange={(event) => sortSearch(event)}
          defaultValue={props.action === 'Group by' ? groupParamOption: sortParamOption}
        >
          {props.data.map((menu, index) => (
            <FormControlLabel
              className={classes.radioBtnLabel}
              value={menu.key}
              control={<Radio className={classes.radioBtn} disableRipple
                color="default"
                checkedIcon={<span className={clsx(classes.icon, classes.checkedIcon)} />}
                icon={<span className={classes.icon} />} />}
              label={menu.value}
            />
          ))}
        </RadioGroup>
      </FormControl>
    </div>
  );
};

SingleSelectMenu.propTypes = {
  classes: PropTypes.object.isRequired,
};

export default withStyles(styles)(SingleSelectMenu);
