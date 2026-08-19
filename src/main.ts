import "reflect-metadata";

import { NestFactory } from "@nestjs/core";

import { AppModule } from "./app.module.js";
import { environment } from "./global/config/environment/environment.js";

const app = await NestFactory.create(AppModule);

app.enableShutdownHooks();
await app.listen(environment.PORT, "0.0.0.0");
