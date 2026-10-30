import React, { useEffect, useState } from 'react';
import Box from '@mui/material/Box';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ChevronRightIcon from '@mui/icons-material/ChevronRight';
import { TreeView } from '@mui/x-tree-view/TreeView';
import { TreeItem } from '@mui/x-tree-view/TreeItem';
import { useNavigate } from "react-router-dom";
import './DataCommunity.css';
import { useDispatch } from "react-redux";
import { searchResults, searchParam, browseScope, domainFilter, communityFilter, sourceFilter, typeFilter, hierarchyList, domainSelected } from "../../../features/search/searchSlice";
import { selectedProduct, selectedProductName } from "../../../features/dataProduct/dataProductSlice";
import { getCommunityProducts } from "../../../services/search";
import { getFavouriteDataProducts, getDataCommunities, saveViewedDataProduct } from "../../../services/user";
import { Tooltip, Typography, withStyles } from '@material-ui/core';

const treeStyle = {
  overflow: "auto",
  flexGrow: 1,
  width: 'fit-content',
  "& .MuiTreeItem-label span div": {
    fontSize: "2.5rem !important",
    fontFamily: "Inter !important",
  },"& .MuiTreeItem-label span": {
    fontSize: "2.5rem !important",
    fontFamily: "Inter !important",
    // textDecoration: "underline",
    // color: "#00345F",
  },
  "& .MuiTreeItem-label": {
    fontSize: "2.5rem !important",
    fontFamily: "Inter !important",
  },
  "& .MuiTreeItem-label:hover": {
    textDecoration:"underline",
  },
  "& .MuiTreeItem-content.Mui-selected": {
    backgroundColor: "#FFFFFF !important",
  },
  "& .MuiTreeItem-content:hover": {
    backgroundColor: "#FFFFFF !important",
  },
  "& MuiTreeItem-content.Mui-expanded": {
    backgroundColor: "#FFFFFF !important",
  },
  "& .MuiTreeItem-label label": {
    fontSize: "2rem !important",
    fontFamily: "Inter !important",
  },
  "& .MuiTreeItem-iconContainer .MuiSvgIcon-root": {
    color: "#00345F",
    fontSize: "xx-large"
  },
  "& .MuiTreeItem-content": {
    display: "flex",
    flexDirection: "row",
    alignItems: "flex-start",
    paddingBottom: "1rem",

    ".product-leaf": {
      fontSize: "2.2rem !important",
     
    },
  },
  "& .MuiCollapse-wrapper": {
    paddingTop: "0.5rem",
    paddingBottom: "1rem"
  },
  // "& .MuiCollapse-wrapper .MuiTreeItem-content": {
  //   paddingBottom: "0.5rem",
  // },
  "& .MuiCollapse-root": {
    marginLeft: "3rem"
  }
}

const HtmlTooltip = withStyles((theme) => ({
  tooltip: {
    maxWidth: 500,
    width: 'max-content',
    padding: '1rem',
    position: 'absolute',
    backgroundColor: "#55585A",
    borderRadius: "0.8rem"
  }
}))(Tooltip);

export default function DataCommunityTree(props) {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const [treeData, setTreeData] = useState();
  // const [defaultExpanded, setDefaultExpanded] = useState([]);
  const [expandData, setExpandData] = useState([]);
  const [expandedItems, setExpandedItems] = useState([]);

  useEffect(() => {
    getTreeData();
  }, []);

  useEffect(() => {
    setExpandedItems((oldExpanded) =>
    oldExpanded?.length === 0
      ? expandData
      : [],
    );    
  },[props.expanded]);

  useEffect(() => {
    if (props.selectedNodes.length === 0) {
      setExpandedItems([]);
    } else if (props.selectedNodes.length === expandData.length) {
      setExpandedItems(expandData);
    }
  },[props.selectedNodes])

  const getTreeData = () => {
    getDataCommunities().then((res) => {
      if (!res || !res.data) {
        console.log('Error', res);
      } else {
        setTreeData(res.data.communityList);
        // setDefaultExpanded(res.data.treeExpandList);
        // setDefaultExpanded([]);
        setExpandData(res.data.treeExpandList);
        props.getAllNodes(res.data.treeExpandList);
        props.getTotalProductCount(res.data.totalCount);
      }
    }).catch((error) => {
      console.log(error);
    });
  };

  const handleSelectClick = (item, nodes, name) => {
    if (nodes.kind === "product" || !nodes.children) {
      navigate(`/productDetails/${nodes.productId || nodes.id}`);
      dispatch(selectedProductName(nodes.name)); 
      dispatch(selectedProduct(nodes.val));
    } else {
      const isDomainSelected = nodes.kind === "domain";
      // dispatch(searchParam(item));
      if (isDomainSelected) {
        dispatch(searchParam(name));
        dispatch(domainSelected(true));
      } else {
        dispatch(searchParam(item));
        dispatch(domainSelected(false));
        if (nodes.kind === "community") dispatch(browseScope({ community: item, domain: nodes.domain || "" }));
      }
      Promise.all([
        getCommunityProducts({ searchParam: item, isDomain: isDomainSelected, ...(nodes.domain ? { domain: nodes.domain } : {}) }),
        getFavouriteDataProducts()
      ]).then((data) => {
        if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
          console.log('Error', data);
        } else {
          if (data[0].data?.searchList?.length === 0) {
              saveViewedDataProduct({ productId: 0, productName: null, searchWord: item, category: 'Invalid', tab: 'SEARCH' });
          } else {
              saveViewedDataProduct({ productId: 0, productName: null, searchWord:item, category: 'Valid', tab: 'SEARCH' });
          }
          const result = data[0].data.searchList.map(item => ({
            ...item,
            FAVOURITED: data[1].data.find(d => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false
          }));
          dispatch(searchResults(result));
          dispatch(domainFilter(data[0].data.domainFilter));
          dispatch(communityFilter(data[0].data.communityFilter));
          dispatch(sourceFilter(data[0].data.sourceFilter));
          dispatch(typeFilter(data[0].data.typeFilter));
          dispatch(hierarchyList(data[0].data.hierarchy));
        }
        navigate("/search");
      }).catch((error) => {
        console.log(error);
      });
    }
  };

  const handleExpandedItemsChange = (event, nodeIds) => {
    props.getSelectedNodes(nodeIds);
    setExpandedItems(nodeIds);
  };

  const renderTree = (nodes) => (
    <TreeItem key={nodes.id} nodeId={nodes.id} label={
      <>
        <span onClick={(event) => {event.stopPropagation(); event.preventDefault(); handleSelectClick(nodes.val, nodes, nodes.name)}}>
          <div className={nodes.kind === "product" || !nodes.children ? 'product-leaf' : "domain-leaf"}>{nodes.name}</div></span>
      </>}>
      {Array.isArray(nodes.children)
        ? nodes.children.map((node) => renderTree(node))
        : null}
    </TreeItem>
    // <TreeItem key={nodes.id} nodeId={nodes.id} label={
    //   <>
    //     <span onClick={(event) => {event.stopPropagation(); event.preventDefault(); handleSelectClick(nodes.val, nodes, nodes.name)}}>
    //       {nodes.desc ?
    //         <HtmlTooltip
    //           title={
    //             <Typography variant='h5'>{nodes.desc}</Typography>
    //           }
    //           placement='top'
    //         ><div className={!nodes.count ? 'product-leaf' : "domain-leaf"}>{nodes.name}</div>
    //         </HtmlTooltip> : <><div className={!nodes.count ? 'product-leaf' : "domain-leaf"}>{nodes.name}</div></>}</span>

    //   </>}>
    //   {Array.isArray(nodes.children)
    //     ? nodes.children.map((node) => renderTree(node))
    //     : null}
    // </TreeItem>
  );

  const filterCommuinities = (data) => (
    data.map((item) => {
      return renderTree(item);
    })
  );

  return (
    <Box className="contentLeft">
      {treeData ?
        <TreeView
          sx={treeStyle}
          aria-label=""
          // defaultExpanded={defaultExpanded}
          expanded={expandedItems}
          onNodeToggle={handleExpandedItemsChange}
          defaultCollapseIcon={<ExpandMoreIcon />}
          defaultExpandIcon={<ChevronRightIcon />}
        >
          {filterCommuinities(treeData)}
        </TreeView> : <></>}
    </Box>
  );
}
