import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import { isAbsolute } from 'node:path';

// The KGE machine bearer is a service credential, not a model-provider credential. It is never selected as a
// fallback for a person's missing/expired session; only server-owned callers can request an explicit service context.
export function openModelServiceBearer(env = process.env) {
  const path = env.PORTAL_MODEL_SERVICE_BEARER_FILE;
  if (!path) return undefined;
  if (typeof path !== 'string' || !isAbsolute(path) || path.length > 4096 || path.includes('\0')) throw new Error('PORTAL_MODEL_SERVICE_BEARER_FILE must be an absolute path.');
  return async ({ signal } = {}) => {
    signal?.throwIfAborted();
    let file;
    try {
      file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
      const stat = await file.stat();
      if (!stat.isFile() || stat.size > 8192 || (stat.mode & 0o077) !== 0 || (typeof process.geteuid === 'function' && stat.uid !== process.geteuid())) {
        throw new Error('The model service credential file has unsafe permissions or size.');
      }
      const bearer = (await file.readFile({ encoding: 'utf8', signal })).trim();
      if (!/^[\x21-\x7e]{1,4096}$/.test(bearer)) throw new Error('The model service credential is invalid.');
      return bearer;
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error('The model service credential is unavailable or invalid.');
    } finally { await file?.close(); }
  };
}
