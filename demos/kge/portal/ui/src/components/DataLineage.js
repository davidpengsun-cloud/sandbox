import React, { useState, useEffect } from "react";
import { useSelector } from "react-redux";
import { getLineage } from "../services/metadata";
import { ReactFlow, Controls, Background, MarkerType } from "@xyflow/react";
import { saveViewedDataProduct } from "../services/user";
import "@xyflow/react/dist/style.css";
import NoDataFound from "./shared/NoDataFound";

const DataLineage = (props) => {
  const [nodesData, setNodesData] = useState([]);
  const [edgesData, setEdgesData] = useState([]);
  const [message, setMessage] = useState("Loading…");
  const productId = useSelector((state) => state.dataProduct.selectedProduct);

  useEffect(() => {
    saveViewedDataProduct({ productId: props.data.PRODUCTID, productName: props.data.TECHNICALNAME, searchWord: null, category: "Details", tab: "DETAILS-LINEAGE" });
    getLineage(productId)
      .then((res) => {
        if (!res || !res.data || !res.data.nodes || !res.data.edges) {
          setMessage("No records found");
        } else {
          const modifiedEdge = res.data.edges.map((e) => ({
            ...e,
            markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: "var(--v3-structure)" },
          }));
          setNodesData(res.data.nodes);
          setEdgesData(modifiedEdge);
          if (!res.data.nodes.length) setMessage("No records found");
        }
      })
      .catch(() => setMessage("No records found"));
  }, []);

  if (!nodesData || nodesData.length === 0) {
    return <NoDataFound message={message} />;
  }

  return (
    <div className="v3-tab">
      <div className="v3-tab-head">
        <h3>Lineage</h3>
      </div>
      <div className="v3-lineage">
        <ReactFlow nodes={nodesData} edges={edgesData} fitView>
          <Controls />
          <Background variant="dots" gap={12} size={1} color="var(--v3-line)" />
        </ReactFlow>
      </div>
    </div>
  );
};

export default DataLineage;
