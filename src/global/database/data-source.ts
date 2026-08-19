import { DataSource } from "typeorm";

import { databaseOptions } from "./database.options.js";

export const appDataSource = new DataSource(databaseOptions);
