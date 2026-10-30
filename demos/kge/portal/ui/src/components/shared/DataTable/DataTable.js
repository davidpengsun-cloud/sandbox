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
} from "@material-ui/core";
import PaginationButton from "../Pagination/PaginationButton";
import './DataTable.css'

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

export default function DataTable(props) {
  // const [tableBodyData, setTableBodyData] = useState(props.data);
  let [currentItems, setCurrentItems] = useState(props.data);
  let [tableHeader, setTableHeader] = useState(props.tableHeader);
  const [tableData, setTableData] = useState([]);
  // const [currentPage, setCurrentPage] = useState(1);
  // const [itemsPerPage] = useState(props.numberOfRows);
  // let [currentItems, setCurrentItems] = useState(
  //   tableBodyData.slice(0, props.numberOfRows)
  // );
  // let switchPages = (value) => {
  //   setCurrentPage(value);
  // };

  // useEffect(() => {
  //   const indexOfLastProduct = currentPage * itemsPerPage;
  //   const indexOfFirstProduct = indexOfLastProduct - itemsPerPage;
  //   setTableBodyData(props.data);
  //   setTableHeader(props.tableHeader);
  //   if (props.type === 'fields') {
  //     setCurrentItems(props.data);
  //   } else {
  //     setCurrentItems(props.data.slice(indexOfFirstProduct, indexOfLastProduct));
  //   }
  // }, [currentPage, props, itemsPerPage]);

  useEffect(() => {
    setTableData(props.data);
  },[props.data]);

  return (
    <>
      <Paper className="tableRoot">
        <Table>
          <TableHead>
            <TableRow>
              {tableHeader.map((key, index) => (
                <CustomTableCell key={index}>
                  <div className="headerName"><Typography variant="subtitle1">{key}</Typography></div>
                </CustomTableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {tableData && tableData.length > 0 ? (
              tableData.map((item, itemIndex) => (
                <TableRow className="row" key={itemIndex}>
                  {Object.keys(item).map((key) =>
                  <><CustomTableCell><Typography variant="h5"> {item[key]}</Typography></CustomTableCell></>
                    // key === 'Description' ? (<><CustomTableCell style={{maxWidth: "50rem"}}><Typography variant="h5"> {item[key]}</Typography></CustomTableCell></>) : 
                    // key === 'Field Name' ? (<><CustomTableCell style={{maxWidth: "30rem"}}><Typography variant="h5"> {item[key]}</Typography></CustomTableCell></>) :
                    // (<><CustomTableCell><Typography variant="h5"> {item[key]}</Typography></CustomTableCell></>)
                  )}
                </TableRow>
              ))
            ) : (
              <Typography variant="h4">Loading...</Typography>
            )}
          </TableBody>
        </Table>
      </Paper>
      {/* {props.type !== 'fields' && <Grid className="paginationGrid">
        {tableBodyData &&
          tableBodyData.length &&
          tableBodyData.length > itemsPerPage && (
            <PaginationButton
              data={tableBodyData}
              itemsPerPage={itemsPerPage}
              onClickChange={switchPages}
            />
          )}
      </Grid>} */}
    </>
  );
};
