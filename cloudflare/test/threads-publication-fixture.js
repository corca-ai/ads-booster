import { THREADS_REQUIRED_SCOPES } from "../src/threads/client.js";

export function createPublicationFixture(options = {}) {
  const row = {
    publication_id: "publication-a",
    account_id: "account-a",
    candidate_id: "candidate-a",
    candidate_revision: 2,
    profile_id: "profile-a",
    state: "scheduled",
    caption_snapshot: "caption",
    image_key_snapshot: "private/image.png",
    image_sha256_snapshot: options.imageDigest ?? "a".repeat(64),
    scheduled_at: "2026-08-31T00:00:00Z",
    container_id: null,
    threads_post_id: null,
    token_ciphertext: "ciphertext",
    token_nonce: "nonce",
    token_key_version: "v1",
    token_expires_at: "2027-08-31T00:00:00Z",
    scopes_json: JSON.stringify(THREADS_REQUIRED_SCOPES),
    profile_state: "active",
    threads_auto_publish_enabled: 1,
    ...options.row,
  };
  const calls = [];
  let readbackFailures = options.readbackFailures ?? 0;

  const DB = {
    prepare(sql) {
      return {
        bind(...values) {
          return {
            async first() {
              if (sql.includes("FROM hosted_threads_publications AS publication")) {
                return values[0] === row.publication_id ? { ...row } : null;
              }
              if (sql.includes("SELECT image_key_snapshot")) {
                return values[0] === row.account_id && values[1] === row.publication_id
                  ? {
                      image_key_snapshot: row.image_key_snapshot,
                      image_sha256_snapshot: row.image_sha256_snapshot,
                    }
                  : null;
              }
              throw new Error(`unexpected first SQL: ${sql}`);
            },
            async run() {
              if (sql.includes("SET state = 'creating_container'")) {
                const changes = row.state === "scheduled"
                  && row.threads_auto_publish_enabled === 1
                  && row.profile_state === "active" ? 1 : 0;
                if (changes) row.state = "creating_container";
                return { meta: { changes } };
              }
              if (sql.includes("SET state = 'container_ready'")) {
                const changes = row.state === "creating_container" ? 1 : 0;
                if (changes) {
                  row.state = "container_ready";
                  row.container_id = values[0];
                }
                return { meta: { changes } };
              }
              if (sql.includes("SET state = 'publishing'")) {
                const changes = row.state === "container_ready"
                  && row.threads_auto_publish_enabled === 1
                  && row.profile_state === "active" ? 1 : 0;
                if (changes) row.state = "publishing";
                return { meta: { changes } };
              }
              if (sql.includes("SET threads_post_id = ?")) {
                const changes = row.state === "publishing" && row.threads_post_id === null ? 1 : 0;
                if (changes) row.threads_post_id = values[0];
                return { meta: { changes } };
              }
              if (sql.includes("SET state = 'published'")) {
                const changes = row.state === "publishing" && row.threads_post_id === values[5] ? 1 : 0;
                if (changes) {
                  row.state = "published";
                  row.permalink = values[0];
                  row.next_poll_at = values[2];
                }
                return { meta: { changes } };
              }
              if (sql.includes("SET state = 'canceled'")) {
                const expected = values.at(-1);
                const changes = row.state === expected ? 1 : 0;
                if (changes) {
                  row.state = "canceled";
                  row.failure_code = values[1];
                }
                return { meta: { changes } };
              }
              if (sql.includes("SET state = ?, failure_code = ?")) {
                const expected = values.at(-1);
                const changes = row.state === expected ? 1 : 0;
                if (changes) {
                  row.state = values[0];
                  row.failure_code = values[1];
                }
                return { meta: { changes } };
              }
              if (sql.includes("SET failure_code = 'readback_pending'")) {
                if (row.state === "publishing" && row.threads_post_id) {
                  row.failure_code = "readback_pending";
                  row.retry_after_at = values[0];
                  return { meta: { changes: 1 } };
                }
                return { meta: { changes: 0 } };
              }
              throw new Error(`unexpected run SQL: ${sql}`);
            },
          };
        },
      };
    },
  };

  const graphClient = {
    async getPublishingLimit() {
      calls.push("quota");
      if (options.quotaError) throw options.quotaError;
      return { quotaUsage: 1, quotaTotal: 100 };
    },
    async createImageContainer(input) {
      calls.push("container");
      if (options.disableAtContainer) row.threads_auto_publish_enabled = 0;
      return { containerId: "container-a", input };
    },
    async publishContainer() {
      calls.push("publish");
      if (options.publishError) throw options.publishError;
      return { postId: "post-a" };
    },
    async getPost(postId) {
      calls.push("readback");
      if (readbackFailures > 0) {
        readbackFailures -= 1;
        throw new Error("readback fixture failure");
      }
      return {
        id: postId,
        permalink: "https://www.threads.net/@trace/post/a",
        mediaType: "IMAGE",
        timestamp: "2026-08-31T00:00:00Z",
      };
    },
  };

  return {
    row,
    calls,
    env: {
      DB,
      THREADS_MEDIA_SIGNING_KEY: "m".repeat(32),
      THREADS_PUBLIC_ORIGIN: "https://workspace.example",
    },
    graphClient,
    tokenVault: { async decrypt() { calls.push("decrypt"); return "access-placeholder"; } },
  };
}
