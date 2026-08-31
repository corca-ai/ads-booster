import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { threadsPublicVariables } from "../src/threads/config.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const COMMIT_SHA = /^[0-9a-f]{40}$/u;

export const renderConfig = (template, env) => {
  const databaseId = env.CF_D1_DATABASE_ID;
  if (!databaseId) throw new Error("CF_D1_DATABASE_ID is required");
  const deploySha = env.TRACE_DEPLOY_SHA ?? "local";
  if (deploySha !== "local" && !COMMIT_SHA.test(deploySha)) {
    throw new Error("TRACE_DEPLOY_SHA must be a full lowercase commit SHA");
  }
  const threadsVariables = threadsPublicVariables(env);
  const threadsBlock = Object.entries(threadsVariables)
    .map(([name, value]) => `    ${JSON.stringify(name)}: ${JSON.stringify(value)},`)
    .join("\n");
  return template
    .replaceAll("__D1_DATABASE_ID__", databaseId)
    .replaceAll("__TRACE_DEPLOY_SHA__", deploySha)
    .replaceAll(
      '    "__THREADS_VARIABLES__": null,\n',
      threadsBlock ? `${threadsBlock}\n` : "",
    );
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const template = await readFile(resolve(root, "wrangler.template.jsonc"), "utf8");
  await writeFile(
    resolve(root, "wrangler.generated.jsonc"),
    renderConfig(template, process.env),
    { encoding: "utf8", mode: 0o600 },
  );
  console.log("wrote cloudflare/wrangler.generated.jsonc");
}
