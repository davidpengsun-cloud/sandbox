export const ASK_HISTORY_PREFIX = "askHistory:";

export function clearAskHistory() {
  try {
    for (const key of Object.keys(window.sessionStorage)) {
      if (key.startsWith(ASK_HISTORY_PREFIX)) window.sessionStorage.removeItem(key);
    }
  } catch {
    /* Authentication still works when browser storage is unavailable. */
  }
}
