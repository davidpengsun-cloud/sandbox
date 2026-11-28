import React, { memo } from "react";
import { Handle, Position, useStore } from "@xyflow/react";

/* One node of Cerebro's graph, within the drawing budget (cerebro.md §7.5): memoised, so a node
   whose data is unchanged is not drawn again, and drawn with less detail when zoomed out. The
   handles are kept only as the anchors xyflow ends a link on: nobody may connect nodes here, so
   they are not connectable and graph.css hides them. */

/* Below this zoom a node is drawn as a small mark without its name. Above it, the mark and the
   name keep their size on screen as the view zooms out (graph.css's --cerebro-label-scale), so a
   name stays legible when the view fits a large graph. */
export const LOW_DETAIL_ZOOM = 0.15;

/* A boolean, never the zoom itself, so a node is drawn again only when the zoom crosses the line,
   not on every frame of a zoom. */
const zoomedOut = (store) => store.transform[2] < LOW_DETAIL_ZOOM;

/* Counts the draws of each node, for the test that holds the memoisation; does nothing otherwise. */
export const drawCounter = { onDraw: null };

function GraphNode({ id, data }) {
  const far = useStore(zoomedOut);
  drawCounter.onDraw?.(id);
  return <>
    <Handle type="target" position={Position.Top} isConnectable={false} isConnectableStart={false} isConnectableEnd={false} className="cerebro-handle" />
    <span className="cerebro-node-glyph">
      {far
        ? <span className="cerebro-node-mark" title={data.label} aria-label={data.label} />
        : <><span className="cerebro-node-dot" aria-hidden="true" /><span className="cerebro-node-label">{data.label}</span></>}
    </span>
    <Handle type="source" position={Position.Bottom} isConnectable={false} isConnectableStart={false} isConnectableEnd={false} className="cerebro-handle" />
  </>;
}

export default memo(GraphNode);
