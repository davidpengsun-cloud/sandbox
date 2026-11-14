import { postApi } from "../api/api";
import { clearAskHistory } from "./askHistory";

export const login =  async (params) => {
  let request = {
    api: "/session",
    params: { username: params.email, password: params.password },
  };
  const response = await postApi(request);
  clearAskHistory();
  return response;
};
