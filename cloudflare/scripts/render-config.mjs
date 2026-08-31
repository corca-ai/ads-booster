import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const databaseId = process.env.CF_D1_DATABASE_ID;
if (!databaseId) {
  throw new Error("CF_D1_DATABASE_ID is required");
}
const deploySha = process.env.TRACE_DEPLOY_SHA ?? "local";
if (deploySha !== "local" && !/^[0-9a-f]{40}$/u.test(deploySha)) {
  throw new Error("TRACE_DEPLOY_SHA must be a full lowercase commit SHA");
}
const requiredVariables = Object.freeze({
  THREADS_APP_ID: process.env.THREADS_APP_ID,
  THREADS_GRAPH_API_VERSION: process.env.THREADS_GRAPH_API_VERSION,
  THREADS_PUBLIC_ORIGIN: process.env.THREADS_PUBLIC_ORIGIN,
  THREADS_REDIRECT_URI: process.env.THREADS_REDIRECT_URI,
});
for (const [name, value] of Object.entries(requiredVariables)) {
  if (!value) throw new Error(`${name} is required`);
}
if (!/^v[1-9][0-9]*\.[0-9]+$/u.test(requiredVariables.THREADS_GRAPH_API_VERSION)) {
  throw new Error("THREADS_GRAPH_API_VERSION must be pinned to vN.N");
}
for (const name of ["THREADS_PUBLIC_ORIGIN", "THREADS_REDIRECT_URI"]) {
  const url = new URL(requiredVariables[name]);
  if (url.protocol !== "https:") throw new Error(`${name} must use HTTPS`);
}
const template = await readFile(resolve(root, "wrangler.template.jsonc"), "utf8");
await writeFile(
  resolve(root, "wrangler.generated.jsonc"),
  template
    .replaceAll("__D1_DATABASE_ID__", databaseId)
    .replaceAll("__TRACE_DEPLOY_SHA__", deploySha)
    .replaceAll("__THREADS_APP_ID__", requiredVariables.THREADS_APP_ID)
    .replaceAll("__THREADS_GRAPH_API_VERSION__", requiredVariables.THREADS_GRAPH_API_VERSION)
    .replaceAll("__THREADS_PUBLIC_ORIGIN__", requiredVariables.THREADS_PUBLIC_ORIGIN)
    .replaceAll("__THREADS_REDIRECT_URI__", requiredVariables.THREADS_REDIRECT_URI),
  { encoding: "utf8", mode: 0o600 },
);
console.log("wrote cloudflare/wrangler.generated.jsonc");
