import React, { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import "./DataGridTable.css";

export default function DataGridTable(props) {
    const { rows, columns, containerStyle } = props;

    const gridStyle = useMemo(() => ({ height: "100%", width: "100%", overflowX: "auto" }), []);

    const defaultColDef = useMemo(() => {
        return {
            resizable: false,
            suppressMovable: true,
            autoHeight: true,
            wrapText: true,
            wrapHeaderText: true,
            autoHeaderHeight: true,
        };
    }, []);

    const gridOptions = useMemo(() => {
        return {
            columnDefs: columns,
            rowHeight: 48,
            defaultColDef: defaultColDef,
            sideBar: {
                toolPanels: ["columns"],
                defaultToolPanel: "",
            },
            suppressColumnsToolPanel: true,
        };
    }, [columns, defaultColDef]);

    return (
        <div className="v3-grid-wrap" style={containerStyle}>
            <div style={gridStyle} className="ag-theme-quartz mktplce-grid">
                <AgGridReact
                    rowData={rows}
                    columnDefs={columns}
                    defaultColDef={defaultColDef}
                    gridOptions={gridOptions}
                />
            </div>
        </div>
    );
}
