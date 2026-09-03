import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

const migrationsDirectory = new URL("../migrations/", import.meta.url);
const ambiguousTriggerCase = /\bSELECT\s+CASE\b/u;

test("D1 migrations avoid known remote trigger splitter input ambiguities", async () => {
  // This is a maintenance guard for the exact remote-only provider failure observed in
  // issue #129. Local SQLite and Wrangler's local splitter accept both forms, so the
  // post-merge remote migration and exact-SHA health readback remain the e2e proof.
  assert.match("BEGIN SELECT CASE WHEN 1 THEN 1 END; END;", ambiguousTriggerCase);
  assert.doesNotMatch("BEGIN SELECT (CASE WHEN 1 THEN 1 END); END;", ambiguousTriggerCase);

  const migrationNames = (await readdir(migrationsDirectory))
    .filter((name) => name.endsWith(".sql"))
    .sort();

  for (const name of migrationNames) {
    const sql = await readFile(new URL(name, migrationsDirectory), "utf8");
    assert.equal(sql.includes("\r"), false, `${name} must use LF line endings`);
    assert.doesNotMatch(
      sql,
      ambiguousTriggerCase,
      `${name} must parenthesize CASE inside trigger SELECT statements`,
    );
  }
});
