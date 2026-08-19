import type { DataSourceOptions } from "typeorm";

import { environment } from "../config/environment/environment.js";

export const databaseOptions = {
  type: "postgres",
  url: environment.DATABASE_URL,
  entities: [],
  migrations: [],
  synchronize: false,
  logging: environment.NODE_ENV === "development" ? ["error", "warn"] : ["error"],
  extra: {
    max: 10,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 30_000,
  },
} satisfies DataSourceOptions;
