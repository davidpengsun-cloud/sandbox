import React, { useEffect, useState } from "react";
import DataGridTable from "./shared/DataGridTable/DataGridTable";
import { getFields } from "../services/metadata";
import { useSelector } from "react-redux";
import NoDataFound from "./shared/NoDataFound";
import { saveViewedDataProduct } from "../services/user";

const DataFields = (props) => {
  const [message, setMessage] = useState("Loading…");
  const [fieldsData, setFieldsData] = useState([]);
  const [tableHeader, setTableHeader] = useState([]);
  const productId = useSelector((state) => state.dataProduct.selectedProduct);

  useEffect(() => {
    saveViewedDataProduct({ productId: props.data.PRODUCTID, productName: props.data.TECHNICALNAME, searchWord: null, category: "Details", tab: "DETAILS-FIELDS" });
    getFields(productId)
      .then((res) => {
        if (!res || !res.data) {
          setMessage("No records found");
        } else if (res.data.length > 0) {
          setFieldsData(res.data);
          setTableHeader(
            Object.keys(res.data[0]).map((key) => ({
              sortable: key === "Field Name" || key === "Data Quality Score",
              filter: key === "Field Name",
              minWidth: key === "Description" ? 450 : 150,
              field: key,
              headerName: key,
              flex: 1,
              unSortIcon: true,
            }))
          );
        } else {
          setMessage("No records found");
        }
      })
      .catch(() => setMessage("No records found"));
  }, []);

  if (fieldsData.length === 0) {
    return <NoDataFound message={message} />;
  }

  return (
    <div className="v3-tab">
      <div className="v3-tab-head">
        <h3>{fieldsData.length} {fieldsData.length === 1 ? "field" : "fields"}</h3>
      </div>
      <DataGridTable rows={fieldsData} columns={tableHeader} containerStyle={{ width: "100%", height: "600px" }} />
    </div>
  );
};

export default DataFields;
