import { SearchOutlined } from "@material-ui/icons";
import './TypeAheadSearchBar.css'
import { useState } from "react";
import { getPublishSearchData, getPublishedSearchResult } from "../../../services/admin";
import { AutoComplete } from 'antd';
import { CloseSquareFilled } from '@ant-design/icons';

export default function AdminSearchBar(props) {
    const [searchValue, setSearchValue] = useState(null);
    const [options, setOptions] = useState([]);

    let triggerSearch = (event) => {
        setSearchValue(event.target.value);
        if (event.key === "Enter") {
            getPublishedSearchResult( event.target.value === '' ||  event.target.value === null ? '0' :  event.target.value).then((res) => {
                if (!res || !res.data) {
                    console.log("No records found");
                } else {
                    props.searchResult(res.data);             
                }
            }).catch((err)=>{
                console.log("Search Failed!", err);
            });
        }
    };

    let gotoSearch = () => {
        getPublishedSearchResult(searchValue === '' || searchValue === null ? '0' : searchValue).then((res) => {
            if (!res || !res.data) {
                console.log("No records found");
            } else {
                props.searchResult(res.data);             
            }
        }).catch((err)=>{
            console.log("Search Failed!", err);
        });
    };

    const getSearchResult = (param) => {
        if (param && param.length > 0) {
            getPublishSearchData(param).then((res) => {
                if (!res || !res.data) {
                    console.log('Error');
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
                placeholder="Search here..."
            />
            <SearchOutlined
                style={{ width: props.iconWidth }}
                onClick={gotoSearch}
            />
        </div>
    )
}