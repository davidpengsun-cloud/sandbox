import { createContext } from "react";

/* What the conversation's cards read beside their message: the person's role and identity, the verdicts sent, the
   turn's cards and working blocks, Graph mode's opener, the answered questions back, and (#626) the card open in the
   side panel and how to open one. ChatView.js provides it around the conversation and its side panel. */
export const ChatContext = createContext({
  engineer: false, me: { id: "" }, feedback: {}, sendFeedback: () => {}, cards: new Map(), blocks: {}, openNode: undefined, answered: {},
  answerLocally: () => {}, live: "", opened: "", onOpen: undefined,
});
