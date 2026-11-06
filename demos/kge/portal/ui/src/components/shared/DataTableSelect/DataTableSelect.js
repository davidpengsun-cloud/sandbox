import React, { useEffect, useState } from "react";
import { withStyles } from "@material-ui/core/styles";
import {
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography, 
  Checkbox,
} from "@material-ui/core";
import Radio from '@mui/material/Radio';
import RadioGroup from '@mui/material/RadioGroup';
import FormControlLabel from '@mui/material/FormControlLabel';
import FormControl from '@mui/material/FormControl';
import FormLabel from '@mui/material/FormLabel';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import './DataTableSelect.css';

const CustomTableCell = withStyles((theme) => ({
  head: {
    backgroundColor: "#55585A",
    color: "#FFFFFF",
    minWidth: "fit-content",
    borderStyle: "solid",
    borderColor: "#E7E3DF",
    borderWidth: "medium",
    width: 'max-content',
    overflowWrap: "break-word",
  },
  body: {
    maxWidth: "62rem",
    borderStyle: "solid",
    borderColor: "#E7E3DF",
    borderWidth: "medium",
    width: 'max-content',
    overflowWrap: "break-word",
  },
}))(TableCell);

const CustomCheckbox = withStyles((theme) => ({
  root: {
    color: 'black',
    borderColor: 'black',
    
    "& .MuiSvgIcon-root": {
        fontSize: "2em",
      },
  },
}))(Checkbox);

const CustomRadioGroup = withStyles((theme) => ({
  root: {
    color: 'black',
    borderColor: 'black',
    
    "& .MuiSvgIcon-root": {
        fontSize: "1em",
      },
  },
}))(RadioGroup);

const CustomFormControlLabel = withStyles((theme) => ({
  root: {
    "& .MuiFormControlLabel-label": {
        fontSize: "2rem",
      },
  },
}))(FormControlLabel);

export default function DataTableSelect(props) {
  // const [tableBodyData, setTableBodyData] = useState(props.data);
  // let [currentItems, setCurrentItems] = useState(props.data);
  // let [tableHeader, setTableHeader] = useState(props.tableHeader);
  const [tableData, setTableData] = useState([]);
  const [message, setMessage] = useState('Loading...');
  const [value, setValue] = React.useState('');
  // const [sortAsc, setSortAsc] = useState(true);

  let tableHeader = props.tableHeader;
  // let dataProducts = props.data;
  // useEffect(() => {
  //   if (!sortAsc) {
  //     tableData.sort((a, b) => a.TECHNICAL_NAME?.localeCompare(b.TECHNICAL_NAME));
  //   } else {
  //     tableData.sort((a, b) => b.TECHNICAL_NAME?.localeCompare(a.TECHNICAL_NAME));
  //   }
  // },[sortAsc]);

  // const showSortIcon = () => {
  //   if(sortAsc) {
  //     return <KeyboardArrowUpIcon className="header-icon" onClick={handleSortClick} />
  //   } else {
  //     return <KeyboardArrowDownIcon className="header-icon" onClick={handleSortClick} />
  //   }
  // }

  // const handleSortClick = () => {
  //   console.log('sort a')   
  //   setSortAsc(!sortAsc);
  // }

  useEffect(() => {
    setTableData(props.data);
    setMessage(props.message);
  },[props.data, props.message]);

  const handleSelection = (technicalName, status) => {
    props.handleSelection(tableData, technicalName, !status ? 'Y' : 'N'); 
     
    // const updatedData = tableData.map(item => 
    //   item.TECHNICAL_NAME === val ? 
    //   { ...item, PUBLISHED_STATUS: !item.PUBLISHED_STATUS } 
    //   : item);
    // setTableData(updatedData);
  };

  const handlePreviewSelection = (technicalName, status) => {
    props.handlePreviewSelection(tableData, technicalName, !status ? 'Y' : 'N');
  };

  const handleRoleChange = (event, val) => {
    props.handleRoleChange(tableData, event.target.parentElement.getAttribute('data-name'), val);
  }

  const getControl = (key, item) => {
    switch (props.page) {
      case 'PublishedStatus':
        return (
          key === 'PUBLISHED_STATUS' || key === 'DATA_PREVIEW' ? 
          (<><CustomTableCell style={{maxWidth: "50rem"}}>
            <CustomCheckbox 
              checked={key === 'PUBLISHED_STATUS' ? item.PUBLISHED_STATUS : item.DATA_PREVIEW}
              onChange={() => key === 'PUBLISHED_STATUS' ? handleSelection(item.TECHNICAL_NAME, item.PUBLISHED_STATUS) :
                handlePreviewSelection(item.TECHNICAL_NAME, item.DATA_PREVIEW)
              }>
              </CustomCheckbox>
            </CustomTableCell></>)
            : 
            <><CustomTableCell><Typography variant="h5">{item[key]}</Typography></CustomTableCell></>);        
      case 'UserDetails':
        return (
          key === 'Role' ?
          (<><CustomTableCell style={{maxWidth: "50rem"}}>
            <FormControl>
              <CustomRadioGroup
                column
                aria-labelledby="row-radio-buttons-group-label"
                name="row-radio-buttons-group"                
                value={key === 'Role' ? item.Role : ''}       
                onChange={handleRoleChange}
              >
                <CustomFormControlLabel value="SuperAdmin" control={<Radio data-name={item.User_Id} />} label="Super Admin" />
                <CustomFormControlLabel value="Admin" control={<Radio data-name={item.User_Id} />} label="Admin" />
                <CustomFormControlLabel value="User" control={<Radio data-name={item.User_Id} />} label="User" />
                {/* <CustomFormControlLabel value="NoAccess" control={<Radio data-name={item.User_Id} />} label="No Access" /> */}
              </CustomRadioGroup>
            </FormControl>
            </CustomTableCell></>)
            : 
            <><CustomTableCell><Typography variant="h5">{item[key]}</Typography></CustomTableCell></>);       
      default:
        break; 
      }
  }
 
  return (
    <>
      <Paper className="select-tableRoot">
        <Table stickyHeader>
          <TableHead>
            <TableRow>
              {tableHeader.map((key, index) => (
                <CustomTableCell key={index}>
                  <div className="headerName"><Typography variant="subtitle1">{key}</Typography>{key === 'TECHNICAL_NAME' ? '': ''}</div>
                </CustomTableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {tableData && tableData.length > 0 ? (
              tableData.map((item, itemIndex) => (
                <TableRow className="row" key={itemIndex}>
                  {Object.keys(item).map((key) =>
                    getControl(key, item)                    
                  )}
                </TableRow>
              ))
            ) : (
              <Typography variant="h4">{message}</Typography>
            )}
          </TableBody>
        </Table>
      </Paper>     
    </>
  );
};
