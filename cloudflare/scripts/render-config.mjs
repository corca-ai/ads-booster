import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const databaseId = process.env.CF_D1_DATABASE_ID;
if (!databaseId) {
  throw new Error("CF_D1_DATABASE_ID is required");
}
const template = await readFile(resolve(root, "wrangler.template.jsonc"), "utf8");
await writeFile(
  resolve(root, "wrangler.generated.jsonc"),
  template.replaceAll("__D1_DATABASE_ID__", databaseId),
  { encoding: "utf8", mode: 0o600 },
);
console.log("wrote cloudflare/wrangler.generated.jsonc");
