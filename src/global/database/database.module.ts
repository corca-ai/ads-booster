import { Global, Module } from "@nestjs/common";
import { TypeOrmModule } from "@nestjs/typeorm";

import { databaseOptions } from "./database.options.js";

@Global()
@Module({
  imports: [
    TypeOrmModule.forRoot({
      ...databaseOptions,
      autoLoadEntities: true,
      retryAttempts: 5,
      retryDelay: 1_000,
    }),
  ],
  exports: [TypeOrmModule],
})
export class DatabaseModule {}
