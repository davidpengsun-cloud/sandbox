import casefold from "./casefold.json" with { type: "json" };
import unassigned from "./unassigned.json" with { type: "json" };

export const portalRoles = Object.freeze(["viewer", "admin", "publisher", "approver"]);
const refused = (message, status = 400) => Object.assign(new Error(message), { status });

function pythonUnassigned(char) {
  const point = char.codePointAt(0);
  let low = 0, high = unassigned.ranges.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    const [start, end] = unassigned.ranges[middle];
    if (point < start) high = middle;
    else if (point > end) low = middle + 1;
    else return true;
  }
  return false;
}

export function normalizeUsername(value) {
  if (typeof value !== "string") throw refused("A username is required.");
  if (/[\p{Cc}\p{Cf}\p{Cn}]/u.test(value) || [...value].some(pythonUnassigned)) throw refused("Invalid username.");
  const username = [...value.trim().normalize("NFKC")].map((char) => casefold[char] ?? char.toLowerCase()).join("").normalize("NFKC");
  if (!username || [...username].length > 254 || /[\p{Cc}\p{Cf}\p{Cn}\s]/u.test(username) || [...username].some(pythonUnassigned) || username === "access_admin" || username.startsWith("role:")) throw refused("Invalid username.");
  return username;
}

export function checkRole(role) {
  if (!portalRoles.includes(role)) throw refused("Unknown portal role.");
  return role;
}

export function portalAccess(repository) {
  const roles = async (identity) => {
    const held = await repository.roles(identity.principal);
    return [...new Set([...held, ...(identity.who?.admin === true && !identity.fallback ? ["admin"] : [])])];
  };
  const requireAdmin = async (identity) => {
    if (identity.fallback || !(await roles(identity)).includes("admin")) throw refused("Portal admin role required.", 403);
  };
  return {
    roles,
    async list(identity) {
      await requireAdmin(identity);
      return { users: (await repository.users()).map((user) => ({ principal: user.principal_id, roles: user.roles })), roles: portalRoles };
    },
    async change(identity, username, role, grant) {
      await requireAdmin(identity);
      return repository.changeRole(normalizeUsername(username), checkRole(role), grant, identity.principal);
    },
    async audit(identity) {
      await requireAdmin(identity);
      return { audit: await repository.roleAudit() };
    },
  };
}
