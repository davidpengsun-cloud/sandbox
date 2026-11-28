import { openBigQueryRepository } from "./repository/bigquery.js";
import { openSqliteRepository } from "./repository/sqlite.js";

export function openRepository(env = process.env) {
  const factories = {
    bigquery: openBigQueryRepository,
    sqlite: openSqliteRepository,
  };
  const name = env.PORTAL_BACKEND || "sqlite";
  const factory = factories[name];
  if (!factory) throw new Error(`PORTAL_BACKEND must be one of: ${Object.keys(factories).join(", ")}`);
  return factory(env);
}
