// The most history entries KGE accepts on one question (kge/semantics/planhistory.py; the server keeps its own copy).
// A conversation whose history has reached it is not followed up: the next question is asked afresh.
export const MAX_HISTORY_TURNS = 8;
