import { SearchOutlined } from "@material-ui/icons";
import './TypeAheadSearchBar.css'
import { useState } from "react";
import { useDispatch } from "react-redux";
import { searchResults, searchParam, domainFilter, communityFilter, sourceFilter, typeFilter, hierarchyList, groupOption, sortOption, filterOptions, domainSelected } from "../../../features/search/searchSlice";
import { getSearchResults, getSearchData } from "../../../services/search";
import { getFavouriteDataProducts, saveViewedDataProduct } from "../../../services/user";
import { useNavigate } from "react-router-dom";
import { AutoComplete } from 'antd';
import { CloseSquareFilled } from '@ant-design/icons';

export default function TypeAheadSearchBar(props) {
    const [searchValue, setSearchValue] = useState(null);
    const navigate = useNavigate();
    const dispatch = useDispatch();
    const [options, setOptions] = useState([]);

    let triggerSearch = (event) => {
        // clear filters
        dispatch(groupOption(''));
        dispatch(sortOption(''));
        dispatch(filterOptions([]));
        dispatch(domainSelected(false));
        setSearchValue(event.target.value);
        if (event.key === "Enter") {
            dispatch(searchParam(event.target.value));
            Promise.all([
                getSearchResults({ searchParam: event.target.value }),
                getFavouriteDataProducts()
            ]).then((data) => {
                if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
                    console.log('Error', data);
                } else {
                    if (data[0].data?.searchList?.length === 0) {
                        saveViewedDataProduct({ productId: 0, productName: null, searchWord: event.target.value, category: 'Invalid', tab: 'SEARCH' });
                    } else {
                        saveViewedDataProduct({ productId: 0, productName: null, searchWord: event.target.value, category: 'Valid', tab: 'SEARCH' });
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

    let gotoSearch = () => {
        // clear filters
        dispatch(groupOption(''));
        dispatch(sortOption(''));
        dispatch(filterOptions([]));
        dispatch(domainSelected(false));
        dispatch(searchParam(searchValue));
        Promise.all([
            getSearchResults({ searchParam: searchValue }),
            getFavouriteDataProducts()
        ]).then((data) => {
            if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
                console.log('Error', data);
            } else {
                if (data[0].data?.searchList?.length === 0) {
                    saveViewedDataProduct({ productId: 0, productName: null, searchWord: searchValue, category: 'Invalid', tab: 'SEARCH' });
                } else {
                    saveViewedDataProduct({ productId: 0, productName: null, searchWord: searchValue, category: 'Valid', tab: 'SEARCH' });
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
    };

    const getSearchResult = (param) => {
        if (param && param.length > 0) {
            getSearchData(param).then((res) => {
                if (!res || !res.data) {
                    console.log('Error', res);
                } else {
                    setOptions(res.data);
                }
            }).catch((error) => {
                console.log(error);
            });
        } else {
            setSearchValue('');
            setOptions([]);
        }
    };

    const onChange = (data) => {
        getSearchResult(data); 
    };

    const onSelect = (data) => {
        setSearchValue(data);
    }

    return (
        <div className={props.searchBarStyle[0]} id="searchBar">
            <AutoComplete className={props.searchBarStyle[1]} id="searchBarInput"
                options={options}
                allowClear={{ clearIcon: <CloseSquareFilled style={{fontSize: "3rem"}} /> }}
                onChange={onChange}
                onSelect={onSelect}
                onKeyUp={(event) => triggerSearch(event)}
                aria-autocomplete="none"
                placeholder="Search the portal (eg. Table, View, Reports.. )"
            />
            <SearchOutlined
                style={{ width: props.iconWidth }}
                onClick={gotoSearch}
            />
        </div>
    )
}
