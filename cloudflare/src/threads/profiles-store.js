export class ThreadsProfilesStoreError extends Error {
  constructor(code, status, message) {
    super(message);
    this.name = "ThreadsProfilesStoreError";
    this.code = code;
    this.status = status;
  }
}

const conflict = (code, message) => new ThreadsProfilesStoreError(code, 409, message);

const parseProfile = (row, defaultProfileId = null) => ({
  profile_id: row.profile_id,
  threads_user_id: row.threads_user_id,
  username: row.username,
  display_name: row.display_name,
  scopes: JSON.parse(row.scopes_json),
  token_expires_at: row.token_expires_at,
  state: row.state,
  disconnected_at: row.disconnected_at,
  is_default: row.profile_id === defaultProfileId,
  created_at: row.created_at,
  updated_at: row.updated_at,
});

export function createThreadsProfilesStore(db, accountId) {
  const requireAccount = async () => {
    const account = await db.prepare(
      `SELECT account_id, threads_auto_publish_enabled, default_threads_profile_id, revision
       FROM hosted_workspace_accounts WHERE account_id = ? AND enabled = 1`,
    ).bind(accountId).first();
    if (!account) {
      throw new ThreadsProfilesStoreError(
        "THREADS_ACCOUNT_NOT_FOUND",
        404,
        "워크스페이스 계정을 찾을 수 없습니다.",
      );
    }
    return account;
  };

  const findProfile = (profileId) => db.prepare(
    "SELECT * FROM hosted_threads_profiles WHERE account_id = ? AND profile_id = ?",
  ).bind(accountId, profileId).first();

  const requireProfile = async (profileId) => {
    const row = await findProfile(profileId);
    if (!row) {
      throw new ThreadsProfilesStoreError(
        "THREADS_PROFILE_NOT_FOUND",
        404,
        "Threads 프로필을 찾을 수 없습니다.",
      );
    }
    return row;
  };

  return Object.freeze({
    async settings() {
      const account = await requireAccount();
      return {
        account_id: account.account_id,
        threads_auto_publish_enabled: account.threads_auto_publish_enabled === 1,
        default_threads_profile_id: account.default_threads_profile_id,
        revision: account.revision,
      };
    },

    async listProfiles() {
      const account = await requireAccount();
      const result = await db.prepare(
        `SELECT * FROM hosted_threads_profiles
         WHERE account_id = ? ORDER BY created_at, profile_id`,
      ).bind(accountId).all();
      return result.results.map((row) => parseProfile(row, account.default_threads_profile_id));
    },

    requireProfile,

    findProfileByUser(threadsUserId) {
      return db.prepare(
        "SELECT * FROM hosted_threads_profiles WHERE account_id = ? AND threads_user_id = ?",
      ).bind(accountId, threadsUserId).first();
    },

    async createOAuthState(state) {
      await requireAccount();
      if (state.reconnect_profile_id) await requireProfile(state.reconnect_profile_id);
      await db.prepare(
        `INSERT INTO hosted_threads_oauth_states
          (oauth_state_id, account_id, state_sha256, reconnect_profile_id, redirect_uri,
           created_at, expires_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        state.oauth_state_id,
        accountId,
        state.state_sha256,
        state.reconnect_profile_id,
        state.redirect_uri,
        state.created_at,
        state.expires_at,
      ).run();
    },

    async consumeOAuthState(stateSha256, now) {
      const row = await db.prepare(
        `SELECT * FROM hosted_threads_oauth_states
         WHERE account_id = ? AND state_sha256 = ? AND consumed_at IS NULL AND expires_at > ?`,
      ).bind(accountId, stateSha256, now).first();
      if (!row) throw conflict("THREADS_OAUTH_STATE_INVALID", "OAuth 인증 요청이 만료되었거나 이미 사용되었습니다.");
      const result = await db.prepare(
        `UPDATE hosted_threads_oauth_states SET consumed_at = ?
         WHERE oauth_state_id = ? AND consumed_at IS NULL`,
      ).bind(now, row.oauth_state_id).run();
      if (result.meta.changes !== 1) {
        throw conflict("THREADS_OAUTH_STATE_INVALID", "OAuth 인증 요청이 이미 사용되었습니다.");
      }
      return row;
    },

    async connectProfile(profile) {
      const existing = await this.findProfileByUser(profile.threads_user_id);
      if (existing) {
        throw conflict("THREADS_PROFILE_DUPLICATE", "이미 연결된 Threads 프로필입니다.");
      }
      await db.prepare(
        `INSERT INTO hosted_threads_profiles
          (profile_id, account_id, threads_user_id, username, display_name, scopes_json,
           token_ciphertext, token_nonce, token_key_version, token_expires_at,
           state, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)`,
      ).bind(
        profile.profile_id,
        accountId,
        profile.threads_user_id,
        profile.username,
        profile.display_name,
        JSON.stringify(profile.scopes),
        profile.token_ciphertext,
        profile.token_nonce,
        profile.token_key_version,
        profile.token_expires_at,
        profile.now,
        profile.now,
      ).run();
      return parseProfile(await requireProfile(profile.profile_id));
    },

    async reconnectProfile(profileId, profile) {
      const current = await requireProfile(profileId);
      if (current.threads_user_id !== profile.threads_user_id) {
        throw conflict("THREADS_RECONNECT_IDENTITY_MISMATCH", "기존 Threads 사용자와 같은 계정으로 다시 연결해 주세요.");
      }
      const statements = [
        db.prepare(
          `UPDATE hosted_threads_profiles
           SET username = ?, display_name = ?, scopes_json = ?, token_ciphertext = ?,
               token_nonce = ?, token_key_version = ?, token_expires_at = ?, state = 'active',
               disconnected_at = NULL, updated_at = ?
           WHERE account_id = ? AND profile_id = ?`,
        ).bind(
          profile.username,
          profile.display_name,
          JSON.stringify(profile.scopes),
          profile.token_ciphertext,
          profile.token_nonce,
          profile.token_key_version,
          profile.token_expires_at,
          profile.now,
          accountId,
          profileId,
        ),
        db.prepare(
          `UPDATE hosted_threads_publications SET next_poll_at = ?, updated_at = ?
           WHERE account_id = ? AND profile_id = ? AND state = 'published'`,
        ).bind(profile.now, profile.now, accountId, profileId),
      ];
      await db.batch(statements);
      return parseProfile(await requireProfile(profileId));
    },

    async setDefault(profileId, expectedRevision, now) {
      const profile = await requireProfile(profileId);
      if (profile.state !== "active") throw conflict("THREADS_PROFILE_INACTIVE", "활성 Threads 프로필만 기본값으로 선택할 수 있습니다.");
      const result = await db.prepare(
        `UPDATE hosted_workspace_accounts
         SET default_threads_profile_id = ?, revision = revision + 1, updated_at = ?
         WHERE account_id = ? AND enabled = 1 AND revision = ?`,
      ).bind(profileId, now, accountId, expectedRevision).run();
      if (result.meta.changes !== 1) throw conflict("THREADS_SETTINGS_STALE", "Threads 설정이 다른 요청에서 먼저 변경되었습니다.");
      return this.settings();
    },

    async updateSettings(enabled, expectedRevision, requiredScopes, now) {
      const account = await requireAccount();
      if (enabled) {
        if (!account.default_threads_profile_id) throw conflict("THREADS_DEFAULT_REQUIRED", "기본 Threads 프로필을 먼저 선택해 주세요.");
        const profile = await requireProfile(account.default_threads_profile_id);
        const scopes = new Set(JSON.parse(profile.scopes_json));
        if (profile.state !== "active" || requiredScopes.some((scope) => !scopes.has(scope))) {
          throw conflict("THREADS_PROFILE_SCOPE_REQUIRED", "필수 권한이 있는 활성 Threads 프로필이 필요합니다.");
        }
      }
      const result = await db.prepare(
        `UPDATE hosted_workspace_accounts
         SET threads_auto_publish_enabled = ?, revision = revision + 1, updated_at = ?
         WHERE account_id = ? AND enabled = 1 AND revision = ?`,
      ).bind(Number(enabled), now, accountId, expectedRevision).run();
      if (result.meta.changes !== 1) throw conflict("THREADS_SETTINGS_STALE", "Threads 설정이 다른 요청에서 먼저 변경되었습니다.");
      return this.settings();
    },

    async disconnectProfile(profileId, expectedRevision, now) {
      await requireProfile(profileId);
      const [profileUpdated, _publicationsCanceled, accountUpdated] = await db.batch([
        db.prepare(
          `UPDATE hosted_threads_profiles
           SET token_ciphertext = NULL, token_nonce = NULL, token_key_version = NULL,
               token_expires_at = NULL, state = 'disconnected', disconnected_at = ?, updated_at = ?
           WHERE account_id = ? AND profile_id = ?
             AND EXISTS (SELECT 1 FROM hosted_workspace_accounts
                         WHERE account_id = ? AND enabled = 1 AND revision = ?)`,
        ).bind(now, now, accountId, profileId, accountId, expectedRevision),
        db.prepare(
          `UPDATE hosted_threads_publications
           SET state = 'canceled', canceled_at = ?, failure_code = 'profile_disconnected', updated_at = ?
           WHERE account_id = ? AND profile_id = ?
             AND state IN ('scheduled', 'creating_container', 'container_ready')
             AND EXISTS (SELECT 1 FROM hosted_workspace_accounts
                         WHERE account_id = ? AND enabled = 1 AND revision = ?)`,
        ).bind(now, now, accountId, profileId, accountId, expectedRevision),
        db.prepare(
          `UPDATE hosted_workspace_accounts
           SET default_threads_profile_id = CASE WHEN default_threads_profile_id = ? THEN NULL ELSE default_threads_profile_id END,
               threads_auto_publish_enabled = CASE WHEN default_threads_profile_id = ? THEN 0 ELSE threads_auto_publish_enabled END,
               revision = revision + 1, updated_at = ?
           WHERE account_id = ? AND enabled = 1 AND revision = ?`,
        ).bind(profileId, profileId, now, accountId, expectedRevision),
      ]);
      if (profileUpdated.meta.changes !== 1 || accountUpdated.meta.changes !== 1) {
        throw conflict("THREADS_SETTINGS_STALE", "Threads 설정이 다른 요청에서 먼저 변경되었습니다.");
      }
      return parseProfile(await requireProfile(profileId));
    },
  });
}
