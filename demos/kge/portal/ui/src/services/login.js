import { postApi } from "../api/api";

export const login =  async (params) => {
  let request = {
    api: "/session",
    params: { username: params.email, password: params.password },
  };
  return postApi(request);
};
