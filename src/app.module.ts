import { Module } from "@nestjs/common";

import { HealthModule } from "./domain/health/health.module.js";
import { DatabaseModule } from "./global/database/database.module.js";

@Module({
  imports: [DatabaseModule, HealthModule],
})
export class AppModule {}
