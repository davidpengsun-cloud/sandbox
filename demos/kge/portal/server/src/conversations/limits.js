// History is trimmed at 8 MiB before a turn. The serialized checkpoint also holds
// current-turn state and pending writes, so bound that complete document separately.
export const CONVERSATION_MAX_BYTES = 8 * 1024 * 1024;
export const CHECKPOINT_MAX_BYTES = 32 * 1024 * 1024;
