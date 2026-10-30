import React, { useState, useEffect } from "react";
import { useSelector } from "react-redux";
import { getLineage } from "../services/metadata";
import ReactFlow, { Controls, Background, MarkerType } from "reactflow";
import { saveViewedDataProduct } from "../services/user";
import "reactflow/dist/style.css";
import NoDataFound from "./shared/NoDataFound";

const DataLineage = (props) => {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
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
          if (res.data.length > 0) {
            const currentNodes = nodes;
            const currentEdges = edges;
            const outputId = res.data[0]["Data Product Application ID"];
            const outputNode = { id: outputId, data: { label: outputId }, position: { x: 200, y: 100 } };
            res.data.forEach((item) => {
              const lastNode = currentNodes.slice(-1)[0];
              const position = lastNode ? { x: lastNode.position.x, y: lastNode.position.y + 100 } : { x: 0, y: 0 };
              currentNodes.push({ id: item["Application ID"], data: { label: item["Application ID"] }, position });
              currentEdges.push({
                id: item["Application ID"],
                source: item["Application ID"],
                target: outputNode.id,
                selected: true,
                markerEnd: { type: MarkerType.ArrowClosed, width: 20, height: 20, color: "var(--v3-structure)" },
              });
            });
            currentNodes.push(outputNode);
            setNodes(currentNodes);
            setEdges(currentEdges);
          } else {
            setMessage("No records found");
          }
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
