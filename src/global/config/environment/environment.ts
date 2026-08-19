import "dotenv/config";

import { z } from "zod";

const environmentSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  PORT: z.coerce.number().int().min(1).max(65_535).default(3_000),
  DATABASE_URL: z.url().startsWith("postgresql://"),
});

type Environment = z.infer<typeof environmentSchema>;

export class EnvironmentConfigError extends Error {
  readonly name = "EnvironmentConfigError";

  constructor(readonly details: string) {
    super(`Invalid environment configuration: ${details}`);
  }
}

const parsedEnvironment = environmentSchema.safeParse(process.env);

if (!parsedEnvironment.success) {
  throw new EnvironmentConfigError(z.prettifyError(parsedEnvironment.error));
}

export const environment: Environment = parsedEnvironment.data;
