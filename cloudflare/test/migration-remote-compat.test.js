import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

const migrationsDirectory = new URL("../migrations/", import.meta.url);

test("D1 migrations avoid remote trigger splitter ambiguities", async () => {
  const migrationNames = (await readdir(migrationsDirectory))
    .filter((name) => name.endsWith(".sql"))
    .sort();

  for (const name of migrationNames) {
    const sql = await readFile(new URL(name, migrationsDirectory), "utf8");
    assert.equal(sql.includes("\r"), false, `${name} must use LF line endings`);
    assert.doesNotMatch(
      sql,
      /\bSELECT\s+CASE\b/u,
      `${name} must parenthesize CASE inside trigger SELECT statements`,
    );
  }
});
