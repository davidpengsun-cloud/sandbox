import React, { useEffect, useRef, useState } from "react";
import { useDispatch } from "react-redux";
import { useNavigate } from "react-router-dom";
import {
  searchResults,
  searchParam,
  domainFilter,
  communityFilter,
  sourceFilter,
  typeFilter,
  hierarchyList,
  groupOption,
  sortOption,
  filterOptions,
  domainSelected,
} from "../../features/search/searchSlice";
import { getSearchData, getSearchResults } from "../../services/search";
import { getFavouriteDataProducts, saveViewedDataProduct } from "../../services/user";
import { buildSearchUrl } from "../../features/search/searchUrl";

export default function PortalSearch({ className = "", showShortcut = true, onTermChange }) {
  const navigate = useNavigate();
  const dispatch = useDispatch();
  const inputRef = useRef(null);
  const boxRef = useRef(null);
  const debounceRef = useRef(null);
  const [term, setTerm] = useState("");
  const [options, setOptions] = useState([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(-1);

  const updateTerm = (value) => {
    setTerm(value);
    onTermChange?.(value);
  };

  const runSearch = (value) => {
    const val = value && value.trim() ? value.trim() : null;
    setOpen(false);
    setHighlighted(-1);
    dispatch(groupOption(""));
    dispatch(sortOption(""));
    dispatch(filterOptions([]));
    dispatch(domainSelected(false));
    dispatch(searchParam(val));
    Promise.all([getSearchResults({ searchParam: val }), getFavouriteDataProducts()])
      .then((data) => {
        if (!data || !data[0] || !data[1] || !data[0].data || !data[1].data) {
          console.log("Error", data);
        } else {
          saveViewedDataProduct({
            productId: 0,
            productName: null,
            searchWord: val,
            category: data[0].data.searchList.length === 0 ? "Invalid" : "Valid",
            tab: "SEARCH",
          });
          const result = data[0].data.searchList.map((item) => ({
            ...item,
            FAVOURITED: data[1].data.find((d) => d.DATA_PRODUCT_ID === item.PRODUCTID) ? true : false,
          }));
          dispatch(searchResults(result));
          dispatch(domainFilter(data[0].data.domainFilter));
          dispatch(communityFilter(data[0].data.communityFilter));
          dispatch(sourceFilter(data[0].data.sourceFilter));
          dispatch(typeFilter(data[0].data.typeFilter));
          dispatch(hierarchyList(data[0].data.hierarchy));
        }
        navigate(buildSearchUrl({ q: val }));
      })
      .catch((error) => console.log(error));
  };

  const optionValue = (option) => (typeof option === "string" ? option : option?.value ?? option?.label ?? "");

  const fetchSuggestions = (value) => {
    if (!value || value.length === 0) {
      setOptions([]);
      setOpen(false);
      return;
    }
    getSearchData(value)
      .then((res) => {
        if (res && res.data && Array.isArray(res.data)) {
          setOptions(res.data);
          setOpen(res.data.length > 0);
          setHighlighted(-1);
        } else {
          setOptions([]);
          setOpen(false);
        }
      })
      .catch(() => {
        setOptions([]);
        setOpen(false);
      });
  };

  const onChange = (event) => {
    const value = event.target.value;
    updateTerm(value);
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchSuggestions(value), 200);
  };

  const onKeyDown = (event) => {
    if (event.key === "ArrowDown" && open) {
      event.preventDefault();
      setHighlighted((index) => (index + 1) % options.length);
    } else if (event.key === "ArrowUp" && open) {
      event.preventDefault();
      setHighlighted((index) => (index <= 0 ? options.length - 1 : index - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (open && highlighted >= 0 && options[highlighted]) {
        const value = optionValue(options[highlighted]);
        updateTerm(value);
        runSearch(value);
      } else {
        runSearch(term);
      }
    } else if (event.key === "Escape") {
      setOpen(false);
      setHighlighted(-1);
    }
  };

  const pick = (option) => {
    const value = optionValue(option);
    updateTerm(value);
    runSearch(value);
  };

  useEffect(() => {
    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    const onClick = (event) => {
      if (boxRef.current && !boxRef.current.contains(event.target)) {
        setOpen(false);
        setHighlighted(-1);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
      clearTimeout(debounceRef.current);
    };
  }, []);

  return (
    <div
      className={`v3-search ${className}`.trim()}
      ref={boxRef}
      role="combobox"
      aria-expanded={open}
      aria-haspopup="listbox"
    >
      <svg className="v3-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" aria-hidden="true">
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
      <input
        ref={inputRef}
        type="search"
        value={term}
        onChange={onChange}
        onKeyDown={onKeyDown}
        onFocus={() => options.length > 0 && setOpen(true)}
        placeholder="Search tables, views, reports, columns…"
        aria-label="Search the portal"
        aria-autocomplete="list"
        autoComplete="off"
      />
      {showShortcut && <span className="v3-kbd">⌘K</span>}
      {open && (
        <ul className="v3-suggest" role="listbox" aria-label="Search suggestions">
          {options.slice(0, 8).map((option, index) => {
            const value = optionValue(option);
            return (
              <li
                key={`${value}-${index}`}
                role="option"
                aria-selected={highlighted === index}
                className={highlighted === index ? "hi" : ""}
                onMouseDown={(event) => {
                  event.preventDefault();
                  pick(option);
                }}
                onMouseEnter={() => setHighlighted(index)}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.5-3.5" />
                </svg>
                <span>{value}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
