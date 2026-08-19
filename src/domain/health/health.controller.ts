import { Controller, Get, ServiceUnavailableException } from "@nestjs/common";
import { InjectDataSource } from "@nestjs/typeorm";
import type { DataSource } from "typeorm";

type DatabaseProbeRow = {
  readonly connected: number;
};

type HealthResponse = {
  readonly status: "ok";
  readonly database: "connected";
};

@Controller("health")
export class HealthController {
  constructor(@InjectDataSource() private readonly dataSource: DataSource) {}

  @Get()
  async check(): Promise<HealthResponse> {
    const rows = await this.dataSource.query<readonly DatabaseProbeRow[]>("SELECT 1 AS connected");

    if (rows[0]?.connected !== 1) {
      throw new ServiceUnavailableException("Database health check failed");
    }

    return { status: "ok", database: "connected" };
  }
}
