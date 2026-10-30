import { getApi } from "../api/api";

export const getValues = async (fieldName) => {
  let request = {
    api: `/dropdown-values/${fieldName}`,
  };
  return getApi(request);
};
