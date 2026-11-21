import React, { useState } from "react";
import Select from "react-select";

const colourStyles = {
  control: (styles) => ({
    ...styles,
    backgroundColor: "#FFFFFF",
    maxWidth: "25rem",
  }),
  option: (styles, { data, isDisabled, isFocused, isSelected }) => {
    return {
      ...styles,
      backgroundColor: isDisabled
        ? "#CCCCCC"
        : isSelected
        ? "#E7E3DF"
        : isFocused
        ? "#E7E3DF"
        : "#FFFFFF",
      color: isDisabled ? "#CCCCCC" : isSelected ? "#000000" : "#000000",
      cursor: isDisabled ? "not-allowed" : "default",
      fontSize: "2rem",
      fontFamily: "Inter",
      fontWeight: 400,
      ":active": {
        ...styles[":active"],
        backgroundColor: !isDisabled
          ? isSelected
            ? "#E7E3DF"
            : "#FFFFFF"
          : undefined,
      },
    };
  },
  multiValue: (styles, { data }) => {
    return {
      ...styles,
      backgroundColor: "#E7E3DF",
    };
  },
  multiValueLabel: (styles, { data }) => ({
    ...styles,
    color: "#000000",
    fontSize: "2rem",
    fontFamily: "Inter",
    fontWeight: 400,
  }),
  multiValueRemove: (styles, { data }) => ({
    ...styles,
    color: "#000000",
    ":hover": {
      backgroundColor: "#E7E3DF",
      color: "#000000",
    },
  }),
  placeholder: (styles, { data }) => ({
    ...styles,
    fontSize: "2rem",
    fontFamily: "Inter",
    fontWeight: 400,
  }),
  input: (styles, { data }) => ({
    ...styles,
    fontSize: "2rem",
    fontFamily: "Inter",
    fontWeight: 400,
  }),
};

const DropdownWithSearch = (props) => {
  const [isClearable, setIsClearable] = useState(true);
  const [isSearchable, setIsSearchable] = useState(true);
  const [isDisabled, setIsDisabled] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [open, setOpen] = useState(props.open);
  const [value, setValue] = useState(props.value);

  let selectFilterOptions = (selectedOptions) => {
    setValue(selectedOptions);
    props.getSelectedOptions(selectedOptions, props.parent);
  };

  return (
    <Select
      className="basic-multi-select"
      classNamePrefix="select"
      name="optionsWithSearch"
      isMulti
      options={props.options}
      isDisabled={isDisabled}
      isLoading={isLoading}
      isClearable={isClearable}
      isSearchable={isSearchable}
      closeMenuOnSelect={false}
      styles={colourStyles}
      value={value}
      onChange={(selectedOptions) => {
        selectFilterOptions(selectedOptions);
      }}
      menuIsOpen={open}
    />
  );
};

export default DropdownWithSearch;
