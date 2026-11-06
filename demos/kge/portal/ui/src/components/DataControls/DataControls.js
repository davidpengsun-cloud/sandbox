import React, { useEffect, useState } from "react";
import { useSelector } from "react-redux";
import { getDataQuality } from "../../services/metadata";
import NoDataFound from "../shared/NoDataFound";
import "./DataControls.css";
import { saveViewedDataProduct } from "../../services/user";
import DataGridTable from "../shared/DataGridTable/DataGridTable";

const FILTERS = [
  { value: "all", label: "All" },
  { value: "adaptive", label: "Anomaly checks" },
  { value: "custom", label: "Custom data checks" },
  { value: "na", label: "No rules" },
];

export default function DataControls(props) {
  const [controlData, setControls] = useState([]);
  const [message, setMessage] = useState("Loading…");
  const [tableHeader, setTableHeader] = useState({});
  const [value, setValue] = useState("all");
  const productId = useSelector((state) => state.dataProduct.selectedProduct);

  useEffect(() => {
    saveViewedDataProduct({ productId: props.data.PRODUCTID, productName: props.data.TECHNICALNAME, searchWord: null, category: "Details", tab: "DETAILS-DATA_QUALITY" });
  }, []);

  useEffect(() => {
    setMessage("Loading…");
    getDataQuality(productId, value)
      .then((res) => {
        if (!res || !res.data) {
          setControls([]);
          setMessage("No records found");
        } else if (res.data.length > 0) {
          setControls(res.data);
          setTableHeader(
            Object.keys(res.data[0]).map((key) => ({
              sortable: ["Data Element Name", "Data Quality Rule Type", "Data Quality Dimension", "Data Quality Score", "Failing %"].includes(key),
              filter: key === "Data Element Name",
              minWidth: key === "Data Element Name" || key === "Data Quality Rule Type" || key === "Data Quality Dimension" ? 300 : key === "Data Quality Rule Name" ? 450 : 200,
              field: key,
              headerName: key,
              flex: 1,
              unSortIcon: true,
            }))
          );
        } else {
          setControls([]);
          setMessage("No records found");
        }
      })
      .catch(() => {
        setControls([]);
        setMessage("No records found");
      });
  }, [value]);

  return (
    <div className="v3-tab">
      <div className="v3-tab-head">
        <h3>Data quality rules</h3>
        <div className="dq-seg" role="group" aria-label="Filter data quality rules">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={value === f.value}
              onClick={() => setValue(f.value)}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      {controlData && controlData.length > 0 ? (
        <DataGridTable rows={controlData} columns={tableHeader} containerStyle={{ width: "100%", height: "560px" }} />
      ) : (
        <NoDataFound message={message} />
      )}
    </div>
  );
}
