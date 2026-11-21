import axios from "axios";

const API_URL = process.env.REACT_APP_ASK_FEEDBACK_URL
const API = axios.create({
    baseURL: API_URL,
    timeout: 10000,
    withCredentials: true,
    headers: {
      "Content-Type": "application/json",
    },
  });


// function SaveChatFeedback(userFeedback) {
//     return axios.post(`${API_URL}/feedback-add/`, userFeedback)
// }

const SaveChatFeedback = async (request) => {
    return API.post(request.api, request.params,);
  };

export default SaveChatFeedback;
