import { SearchOutlined } from "@material-ui/icons";
// import './SearchBar.css'
import { useState } from "react";
import { useDispatch } from "react-redux";
import { searchResults, searchParam, domainFilter, communityFilter, sourceFilter, typeFilter, hierarchyList, groupOption, sortOption, filterOptions, domainSelected } from "../../../features/search/searchSlice";
import { getSearchResults } from "../../../services/search";
import { getFavouriteDataProducts } from "../../../services/user";
import { useNavigate } from "react-router-dom";

export default function SearchBar(props) {
    const [searchValue, setSearchValue] = useState(null);
    const navigate = useNavigate();
    const dispatch = useDispatch();
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
            // getSearchResults({
            //     searchParam: event.target.value,
            // }).then((res) => {
            //     dispatch(searchResults(res.data));
            //     navigate("/search");
            // });
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
        // getSearchResults({
        //     searchParam: searchValue,
        // }).then((res) => {
        //     dispatch(searchResults(res.data));
        //     navigate("/search");
        // });
    };

    return (
        <div className={props.searchBarStyle[0]} id="searchBar">
            <input
                placeholder="Search the portal (eg. Table, View, Reports.. )"
                className={props.searchBarStyle[1]} id="searchBarInput"
                onKeyUp={(event) => triggerSearch(event)}
            />
            <SearchOutlined
                style={{ width: props.iconWidth }}
                onClick={gotoSearch}
            />
        </div>
    )
}
