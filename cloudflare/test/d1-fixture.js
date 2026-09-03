import { readFileSync, readdirSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { resolve } from "node:path";

const MAX_D1_BOUND_PARAMETERS = 100;

export class D1Adapter {
  constructor() {
    this.sqlite = new DatabaseSync(":memory:");
    const root = resolve(import.meta.dirname, "../migrations");
    for (const filename of readdirSync(root).filter((name) => name.endsWith(".sql")).sort()) {
      this.sqlite.exec(readFileSync(resolve(root, filename), "utf8"));
    }
  }

  prepare(sql) {
    const database = this;
    const bound = (values) => ({
      sql,
      values,
      async first() {
        return database.sqlite.prepare(sql).get(...values) ?? null;
      },
      async all() {
        return { results: database.sqlite.prepare(sql).all(...values) };
      },
      async run() {
        const result = database.sqlite.prepare(sql).run(...values);
        return { meta: { changes: Number(result.changes) } };
      },
    });
    return {
      ...bound([]),
      bind(...values) {
        if (values.length > MAX_D1_BOUND_PARAMETERS) {
          throw new RangeError(
            `D1 supports at most ${MAX_D1_BOUND_PARAMETERS} bound parameters per query`,
          );
        }
        return bound(values);
      },
    };
  }

  async batch(statements) {
    this.sqlite.exec("BEGIN IMMEDIATE");
    try {
      const results = [];
      for (const statement of statements) results.push(await statement.run());
      this.sqlite.exec("COMMIT");
      return results;
    } catch (error) {
      this.sqlite.exec("ROLLBACK");
      throw error;
    }
  }
}
