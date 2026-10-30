import React, { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import { withStyles } from "@material-ui/core/styles";
import { Grid, Paper, Typography } from "@material-ui/core";
import { Select } from 'antd';
import './UserDetails.css';
import DataTableSelect from "../shared/DataTableSelect/DataTableSelect";
import NoDataFound from "../shared/NoDataFound";
import { getUserData } from "../../services/admin";
import StyledButtonWithoutIcon from "../../components/shared/StyledButtonWithoutIcon";

const styles = (theme) => ({
  root: {
    margin: "0 5rem 2rem 2rem",
    // width: "auto",
    height: "90rem",
    backgroundColor: "#E7E3DF",
    borderRadius: "1rem",
    overflowY: "auto",
    overflowX: "auto"
  },
  main: {
    margin: "0 1rem 0 1rem",
  },
  noData: {
    margin: "0 5rem 2rem 2rem",
    width: "auto",
    height: "90rem",
    backgroundColor: "#E7E3DF",
    borderRadius: "1rem",
    display: "flex",
    flexDirection: "row",
    flexWrap: "wrap",
    paddingTop: "30rem",
    justifyContent: "center",
    "& .MuiTypography-h3": {
      fontFamily: "Inter",
    },
  },
  searchBar: {
    marginTop: "0.5rem !important",
    [theme.breakpoints.up('lg')]: {
      width: "100rem",
      height: "5rem",
    },
    [theme.breakpoints.between('md', 'lg')]: {
      width: "60rem",
      height: "5rem",
    },
    [theme.breakpoints.between('sm', 'md')]: {
      width: "35rem",
      height: "5rem",
    },
  },
  searchBarInputStyle: {
    [theme.breakpoints.up('lg')]: {
      width: "100rem",
    },
    [theme.breakpoints.between('md', 'lg')]: {
      width: "60rem",
    },
    [theme.breakpoints.between('sm', 'md')]: {
      width: "35rem",
    },
  },
});

const UserDetails = (props) => {
  const { classes } = props;
  const [message, setMessage] = useState('Loading...');
  const [fieldsData, setFieldsData] = useState([]);
  const [tableHeader, settableHeader] = useState([]);
  const productId = useSelector((state) => state.dataProduct.selectedProduct);
  const [tableData, setTableData] = useState([]);
  const [domainList, setDomainList] = useState([]);
  const [options, setOptions] = useState([]);
  const [productSelectedValue, setProductSelectedValue] = useState("Click here");
  const [domainSelectedValue, setDomainSelectedValue]= useState("Click here");

  useEffect(() => {
    getUserData().then((res) => {
      if (!res || !res.data) {
        setMessage("No records found");
      } else if (res.data.length > 0) {        
        getTableData(res.data);
        settableHeader(Object.keys(res.data[0]));
      } else {
        setMessage("No records found");
      }
    }).catch((err)=>{
      setMessage("No records found");
    });
  }, []);

  const getTableData = (val) => {
    const data = val.map(obj => {return { "User_Name" : obj['userName'], "User_Id" : obj['userId'], "Role" : obj['role'] }});
    if (data.length === 0) {
      setTableData([]);
      setMessage('No Users available!');
    } else {
      setTableData(data);
    }
  } 

  const handleRoleChange = (data, user, role) => {   
    const params = {
        technicalName: user,
        publishedStatus: role,
      }
    //   updatePreviewStatus(params).then((res) => {
    //     if (!res || !res.data) {
    //       setMessage("No records found");
    //     } else if (res.data.length > 0) {
          const updatedData = data.map(item => 
            item.User_Id === user ? 
            { ...item, Role: role } 
            : item);
          setTableData(updatedData);

          console.log(updatedData)
    //     }
    //   }).catch((err)=>{
    //     setMessage("Update Failed!");
    //   });
    
  }

  const handleSearchResult = (searchValue) => {
    setDomainSelectedValue("Click here");
    console.log(searchValue)
    setProductSelectedValue(!searchValue ? 'Click here' : searchValue);
   
    // getTableData(val);
    // setTableData(val);
  }

  const onClick = () => {
   
  };
  
  const handleChange = (val, obj) => {
    setProductSelectedValue("Click here");
    setDomainSelectedValue(!obj ? 'Click here' : obj.key);
    
  };

  return (
    <>            
      <Paper className={classes.root}>
        <Grid container>
          <Grid item xs={5} style={{padding: '2rem'}}>
            {/* <Grid item xs={12}>
              <Grid container spacing={2}>
                <Grid item xs={4} style={{alignContent: 'center'}}>
                  <Typography variant="h4" style={{ marginLeft: '3rem'}}>Search Data Asset:</Typography>
                </Grid>
                <Grid item xs={8}>
                
                  <Select
                    className="admin-selector"
                    defaultValue="Click here"
                    size="large"
                    style={{ width: 525, border: '4px black solid', height: '48px' }}
                    onChange={handleSearchResult}
                    allowClear
                    showSearch
                    optionFilterProp="value"
                    options={options}
                    value={productSelectedValue}
                  />
                </Grid>
                <Grid item xs={4} style={{alignContent: 'center'}}>
                  <Typography variant="h4" style={{ marginLeft: '3rem'}}>Search by Domain:</Typography> 
                </Grid>
                <Grid item xs={8}>
                 
                  <Select
                    className="admin-selector"
                    defaultValue="Click here"
                    size="large"
                    style={{ width: 525, border: '4px black solid', height: '48px' }}
                    onChange={handleChange}
                    allowClear
                    showSearch
                    optionFilterProp="value"
                    options={domainList}
                    value={domainSelectedValue}
                  />
                </Grid>
              </Grid>
            </Grid> */}
            
          </Grid>
          <Grid item xs={7} style={{padding: '6rem'}}>
            {/* <StyledButtonWithoutIcon handleClick={onClick} name="Live Sync" /> */}
          </Grid>
          <DataTableSelect
              page="UserDetails"
              data={tableData}
              message={message}
              tableHeader={['User Name', 'User Id', 'User Role']}
              handleRoleChange = {handleRoleChange}
              // numberOfRows={10}
            />
        </Grid>
      </Paper>      
    </>
  );
};

export default withStyles(styles)(UserDetails);
