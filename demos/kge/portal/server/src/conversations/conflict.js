// #625: the errors a write of the conversation store is refused with (store.js has the port). A conflict: the object's
// generation is not the one the writer read, so another writer changed it, made it or removed it first. Busy: the
// object's lock was not free in time (another writer holds it), which says nothing about whether it changed, so the
// saver treats it as a failed save (not_saved), never as "changed in another tab".
export function conflict(key) {
  return Object.assign(new Error(`The conversation ${key} changed since it was read.`), { code: "conflict" });
}
export const isConflict = (error) => error?.code === "conflict";
export function busy(key) {
  return Object.assign(new Error(`The conversation ${key} is being written by another writer.`), { code: "busy", status: 503 });
}
