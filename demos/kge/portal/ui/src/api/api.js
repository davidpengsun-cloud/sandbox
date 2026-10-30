import axios from "axios";
import { publishKnowledge, clearKnowledge, knowledgeEpoch } from "../components/KnowledgeStatus";
const BACKEND_URL = "";
const API = axios.create({
  baseURL: BACKEND_URL,
  // timeout: 50000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});
// const BACKEND_URL = "http://localhost:4000";

// API.interceptors.request.use(config => {
//   const accessToken = Cookies.get('token');
//   if(accessToken) {
//     config.headers["Authorization"] = accessToken;
//     return config;
//   } else {
//     window.location.href = "/";
//   }
// });

const errorHandler = (error, request) => {
  const statusCode = error.response?.status;
  // A 401 means the session is gone: back to the sign-in page. A 401 from the
  // sign-in call itself is a wrong password, which the form shows; reloading
  // here would wipe that message.
  if (statusCode && statusCode === 401 && request?.api !== "/session") {
    clearKnowledge();
    window.location.href = "/";
  }
  // if (statusCode && statusCode !== 401) {
    console.error(error);
  // }
  // return Promise.reject(error);
};

export const getApi = async (request) => {
  const epoch = knowledgeEpoch();
  try {
    const res = await API.get(request.api, {
      params: request.params ? request.params : null,
    });
    publishKnowledge(res, epoch);
    return res;
  } catch (err) {
    errorHandler(err);
    throw err;
  }
};

export const postApi = async (request) => {
  if (request.api === "/session") clearKnowledge();
  const epoch = knowledgeEpoch();
  try {
    const res = await API.post(request.api, request.params,);
    publishKnowledge(res, epoch);
    return res;
  } catch (err) {
    errorHandler(err, request);
    throw err;
  }
};

export const putApi = async (request) => {
  const epoch = knowledgeEpoch();
  try {
    const res = await API.put(request.api, request.data, {
      headers: {
        "Custom-Language": "en",
      },
    });
    publishKnowledge(res, epoch);
    return res;
  } catch (err) {
    errorHandler(err);
    throw err;
  }
};
