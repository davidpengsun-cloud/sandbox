const DAY = 86400000;
export const daysAgo = (value, now = Date.now()) => {
  if (!value) return null;
  const time = Date.parse(String(value).length === 10 ? `${value}T00:00:00` : String(value));
  if (Number.isNaN(time)) return null;
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  return Math.max(0, Math.ceil((today.getTime() - time) / DAY));
};
export const freshness = (value, now) => {
  const days = daysAgo(value, now);
  return days === null ? "unknown" : days <= 7 ? "fresh" : days <= 30 ? "aging" : "stale";
};
export const relTime = (value, now) => {
  const days = daysAgo(value, now);
  if (days === null) return "";
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days}d ago`;
  if (days < 60) return `${Math.round(days / 7)}w ago`;
  if (days < 365) return `${Math.round(days / 30)}mo ago`;
  return `${Math.round(days / 365)}y ago`;
};
