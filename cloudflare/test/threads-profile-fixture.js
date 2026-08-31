import { THREADS_REQUIRED_SCOPES } from "../src/threads/client.js";
import { ThreadsProfilesStoreError } from "../src/threads/profiles-store.js";

const conflict = (code, message) => new ThreadsProfilesStoreError(code, 409, message);

export function createThreadsProfileFixture() {
  const account = {
    account_id: "account-a",
    threads_auto_publish_enabled: false,
    default_threads_profile_id: null,
    revision: 1,
  };
  const profiles = new Map();
  const states = new Map();
  const validatedProfiles = [];
  const encryptedInputs = [];
  const disconnects = [];

  const safeProfile = (profile) => ({
    profile_id: profile.profile_id,
    threads_user_id: profile.threads_user_id,
    username: profile.username,
    display_name: profile.display_name,
    scopes: [...profile.scopes],
    token_expires_at: profile.token_expires_at,
    state: profile.state,
    disconnected_at: profile.disconnected_at ?? null,
    is_default: profile.profile_id === account.default_threads_profile_id,
  });

  const requireProfile = (profileId) => {
    const profile = profiles.get(profileId);
    if (!profile) throw new ThreadsProfilesStoreError("THREADS_PROFILE_NOT_FOUND", 404, "missing");
    return profile;
  };

  const store = {
    async settings() {
      return { ...account };
    },
    async listProfiles() {
      return [...profiles.values()].map(safeProfile);
    },
    async createOAuthState(state) {
      if (state.reconnect_profile_id) requireProfile(state.reconnect_profile_id);
      states.set(state.state_sha256, { ...state, account_id: account.account_id, consumed_at: null });
    },
    async consumeOAuthState(stateSha256, now) {
      const state = states.get(stateSha256);
      if (!state || state.consumed_at || state.expires_at <= now) {
        throw conflict("THREADS_OAUTH_STATE_INVALID", "invalid state");
      }
      state.consumed_at = now;
      return { ...state };
    },
    async connectProfile(profile) {
      if ([...profiles.values()].some((current) => current.threads_user_id === profile.threads_user_id)) {
        throw conflict("THREADS_PROFILE_DUPLICATE", "duplicate");
      }
      profiles.set(profile.profile_id, { ...profile, state: "active" });
      return safeProfile(profiles.get(profile.profile_id));
    },
    async reconnectProfile(profileId, profile) {
      const current = requireProfile(profileId);
      if (current.threads_user_id !== profile.threads_user_id) {
        throw conflict("THREADS_RECONNECT_IDENTITY_MISMATCH", "wrong user");
      }
      profiles.set(profileId, { ...current, ...profile, state: "active", disconnected_at: null });
      return safeProfile(profiles.get(profileId));
    },
    async setDefault(profileId, revision) {
      const profile = requireProfile(profileId);
      if (revision !== account.revision) throw conflict("THREADS_SETTINGS_STALE", "stale");
      if (profile.state !== "active") throw conflict("THREADS_PROFILE_INACTIVE", "inactive");
      account.default_threads_profile_id = profileId;
      account.revision += 1;
      return this.settings();
    },
    async updateSettings(enabled, revision, requiredScopes) {
      if (revision !== account.revision) throw conflict("THREADS_SETTINGS_STALE", "stale");
      if (enabled) {
        const profile = requireProfile(account.default_threads_profile_id);
        if (requiredScopes.some((scope) => !profile.scopes.includes(scope))) {
          throw conflict("THREADS_PROFILE_SCOPE_REQUIRED", "scope");
        }
      }
      account.threads_auto_publish_enabled = enabled;
      account.revision += 1;
      return this.settings();
    },
    async disconnectProfile(profileId, revision, now) {
      const profile = requireProfile(profileId);
      if (revision !== account.revision) throw conflict("THREADS_SETTINGS_STALE", "stale");
      Object.assign(profile, {
        token_ciphertext: null,
        token_nonce: null,
        token_key_version: null,
        state: "disconnected",
        disconnected_at: now,
      });
      if (account.default_threads_profile_id === profileId) {
        account.default_threads_profile_id = null;
        account.threads_auto_publish_enabled = false;
      }
      account.revision += 1;
      disconnects.push(profileId);
      return safeProfile(profile);
    },
  };

  const graphClient = {
    async exchangeAuthorizationCode(code) {
      return { accessToken: `short:${code}` };
    },
    async exchangeLongLivedToken(accessToken) {
      return { accessToken: `long:${accessToken}`, expiresIn: 3600 };
    },
    async getValidatedProfile() {
      const profile = validatedProfiles.shift();
      if (!profile) throw new Error("validated profile fixture missing");
      return profile;
    },
  };

  const tokenVault = {
    async encrypt(token) {
      encryptedInputs.push(token);
      return { ciphertext: "ciphertext", nonce: "nonce", key_version: "v1" };
    },
  };

  const DB = {
    prepare(sql) {
      return {
        bind(stateSha256) {
          return {
            async first() {
              if (!sql.includes("hosted_threads_oauth_states")) return null;
              const state = states.get(stateSha256);
              return state ? { account_id: state.account_id } : null;
            },
          };
        },
      };
    },
  };

  return {
    account,
    DB,
    disconnects,
    encryptedInputs,
    profiles,
    states,
    store,
    graphClient,
    tokenVault,
    validatedProfiles,
    requiredScopes: [...THREADS_REQUIRED_SCOPES],
  };
}
