import "reflect-metadata";

import type { INestApplication } from "@nestjs/common";
import { Test } from "@nestjs/testing";
import request from "supertest";

import { AppModule } from "../src/app.module.js";

describe("GET /health", () => {
  let app: INestApplication;

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({ imports: [AppModule] }).compile();
    app = moduleRef.createNestApplication();
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  it("reports a live PostgreSQL connection", async () => {
    // Given: the NestJS application is connected to the configured PostgreSQL database.
    // When: a client requests the health endpoint.
    const response = await request(app.getHttpServer()).get("/health").expect(200);

    // Then: the endpoint reports both application and database health.
    expect(response.body).toEqual({ status: "ok", database: "connected" });
  });
});
