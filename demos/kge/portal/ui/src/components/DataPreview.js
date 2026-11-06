import React, { useEffect, useState } from "react";
import { getDataPreview, getDataExport } from "../services/metadata";
import { useSelector } from "react-redux";
import NoDataFound from "./shared/NoDataFound";
import parse from "html-react-parser";
import { saveViewedDataProduct } from "../services/user";
import DataGridTable from "./shared/DataGridTable/DataGridTable";

const DataPreview = (props) => {
  const [previewData, setPreviewData] = useState([]);
  const productName = useSelector((state) => state.dataProduct.selectedProductName);
  const [tableHeader, setTableHeader] = useState({});
  const [message, setMessage] = useState("Loading…");
  const [exportError, setExportError] = useState("");

  const download = async () => {
    setExportError("");
    try {
      const response = await getDataExport(props.data.PRODUCTID);
      const url = URL.createObjectURL(new Blob([response.data], { type: "text/csv" }));
      const link = document.createElement("a");
      link.href = url;
      const disposition = response.headers?.["content-disposition"] || "";
      link.download = /filename="([^"]+)"/i.exec(disposition)?.[1] || `${props.data.UNIT_ID}-${props.data.TECHNICALNAME}.csv`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(error.response?.data?.detail || error.response?.data?.message || "The export was refused.");
    }
  };

  useEffect(() => {
    saveViewedDataProduct({ productId: props.data.PRODUCTID, productName: props.data.TECHNICALNAME, searchWord: null, category: "Details", tab: "DETAILS-DATA_PREVIEW" });
    const previewParams = {
      productId: props.data.PRODUCTID,
      dbName: props.data.DATABASE_NAME,
      schemaName: props.data.SCHEMANAME,
      technicalName: props.data.TECHNICALNAME,
      maxLimit: props.config?.Data_Preview_Max_Limit,
      minLimit: props.config?.Data_Preview_Min_Limit,
      columnRestriction: props.config?.Column_Limit_Restriction,
      totalColumns: props.data.COUNTOFCOLUMNS,
      restrictPreview: props.data.ISPREVIEW,
    };

    if (previewParams.restrictPreview === "No") {
      setMessage(
        parse(
          "This data requires approval from the Data Owner to be seen. \n " +
            '<div>Please send inquiries to <a style="text-decoration: underline; color:var(--v3-link);" href="mailto:"></a></div>'
        )
      );
    } else {
      getDataPreview(previewParams)
        .then((res) => {
          if (!res || !res.data) {
            setMessage("No records found");
          } else if (res.data.length > 0) {
            setPreviewData(res.data);
            setTableHeader(
              Object.keys(res.data[0]).map((key) => ({
                sortable: false,
                filter: false,
                minWidth: 300,
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
        .catch((error) => setMessage(error.response?.data?.detail || error.response?.data?.message || "The preview was refused."));
    }
  }, []);

  if (previewData.length === 0) {
    return <NoDataFound message={message} />;
  }

  return (
    <div className="v3-tab">
      <div className="v3-tab-head">
        <h3>Sample rows</h3>
        {productName && <span className="v3-tab-sub">{productName}</span>}
        <button className="btn btn-primary" onClick={download}>Export CSV</button>
      </div>
      {exportError && <p role="alert">{exportError}</p>}
      <DataGridTable rows={previewData} columns={tableHeader} containerStyle={{ width: "100%", height: "560px" }} />
    </div>
  );
};

export default DataPreview;
