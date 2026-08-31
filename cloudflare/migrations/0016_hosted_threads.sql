ALTER TABLE hosted_workspace_accounts ADD COLUMN threads_auto_publish_enabled INTEGER NOT NULL
    DEFAULT 0 CHECK (threads_auto_publish_enabled IN (0, 1));

ALTER TABLE hosted_workspace_accounts ADD COLUMN default_threads_profile_id TEXT;

ALTER TABLE hosted_workspace_candidates ADD COLUMN threads_profile_id TEXT;

CREATE TABLE hosted_threads_oauth_states (
    oauth_state_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    state_sha256 TEXT NOT NULL UNIQUE CHECK (length(state_sha256) = 64),
    reconnect_profile_id TEXT,
    redirect_uri TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY (account_id, reconnect_profile_id)
        REFERENCES hosted_threads_profiles(account_id, profile_id) ON DELETE CASCADE
);

CREATE INDEX hosted_threads_oauth_states_expiry
ON hosted_threads_oauth_states (consumed_at, expires_at);

CREATE TABLE hosted_threads_profiles (
    profile_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    threads_user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT,
    scopes_json TEXT NOT NULL,
    token_ciphertext BLOB,
    token_nonce BLOB,
    token_key_version TEXT CHECK (
        token_key_version IS NULL OR (
            length(token_key_version) >= 2
            AND substr(token_key_version, 1, 1) = 'v'
            AND substr(token_key_version, 2, 1) GLOB '[1-9]'
            AND substr(token_key_version, 2) NOT GLOB '*[^0-9]*'
        )
    ),
    token_expires_at TEXT,
    state TEXT NOT NULL CHECK (state IN ('active', 'reauth_required', 'disconnected')),
    disconnected_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, threads_user_id),
    UNIQUE (account_id, profile_id),
    CHECK (
        (state = 'disconnected' AND token_ciphertext IS NULL AND token_nonce IS NULL
         AND token_key_version IS NULL)
        OR
        (state IN ('active', 'reauth_required') AND token_ciphertext IS NOT NULL
         AND token_nonce IS NOT NULL AND token_key_version IS NOT NULL)
    )
);

CREATE INDEX hosted_threads_profiles_token_expiry
ON hosted_threads_profiles (state, token_expires_at);

CREATE TABLE hosted_threads_publications (
    publication_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES hosted_workspace_accounts(account_id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES hosted_workspace_candidates(candidate_id) ON DELETE RESTRICT,
    candidate_revision INTEGER NOT NULL CHECK (candidate_revision >= 1),
    profile_id TEXT NOT NULL,
    threads_user_id_snapshot TEXT,
    username_snapshot TEXT,
    state TEXT NOT NULL CHECK (
        state IN (
            'scheduled', 'canceled', 'creating_container', 'container_ready', 'publishing',
            'published', 'unknown_side_effect', 'failed', 'rate_limited', 'auth_required',
            'unavailable'
        )
    ),
    caption_snapshot TEXT NOT NULL,
    image_key_snapshot TEXT NOT NULL,
    image_sha256_snapshot TEXT NOT NULL CHECK (length(image_sha256_snapshot) = 64),
    timezone_snapshot TEXT NOT NULL,
    posting_slot_snapshot TEXT NOT NULL CHECK (posting_slot_snapshot IN ('morning', 'evening')),
    wall_clock_snapshot TEXT,
    scheduled_at TEXT NOT NULL,
    container_id TEXT,
    container_created_at TEXT,
    publish_barrier_at TEXT,
    threads_post_id TEXT,
    permalink TEXT,
    published_at TEXT,
    canceled_at TEXT,
    failure_code TEXT,
    failure_detail TEXT,
    failed_at TEXT,
    retry_after_at TEXT,
    next_poll_at TEXT,
    metrics_cursor TEXT,
    replies_cursor TEXT,
    metrics_polled_at TEXT,
    replies_polled_at TEXT,
    poll_completed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (candidate_id, candidate_revision),
    FOREIGN KEY (account_id, profile_id)
        REFERENCES hosted_threads_profiles(account_id, profile_id) ON DELETE RESTRICT
);

CREATE INDEX hosted_threads_publications_scheduling
ON hosted_threads_publications (state, scheduled_at, created_at);

CREATE INDEX hosted_threads_publications_poll
ON hosted_threads_publications (state, next_poll_at, updated_at);

CREATE TABLE hosted_threads_metric_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    views INTEGER NOT NULL CHECK (views >= 0),
    likes INTEGER NOT NULL CHECK (likes >= 0),
    replies INTEGER NOT NULL CHECK (replies >= 0),
    reposts INTEGER NOT NULL CHECK (reposts >= 0),
    quotes INTEGER NOT NULL CHECK (quotes >= 0),
    shares INTEGER NOT NULL CHECK (shares >= 0),
    delete_after TEXT NOT NULL,
    UNIQUE (publication_id, observed_at),
    FOREIGN KEY (account_id, publication_id)
        REFERENCES hosted_threads_publications(account_id, publication_id) ON DELETE CASCADE
);

CREATE INDEX hosted_threads_metric_snapshots_cleanup
ON hosted_threads_metric_snapshots (delete_after, snapshot_id);

CREATE TABLE hosted_threads_replies (
    reply_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    threads_reply_id TEXT NOT NULL,
    root_threads_post_id TEXT NOT NULL,
    body TEXT NOT NULL,
    replied_at TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    delete_after TEXT NOT NULL,
    UNIQUE (account_id, threads_reply_id),
    FOREIGN KEY (account_id, publication_id)
        REFERENCES hosted_threads_publications(account_id, publication_id) ON DELETE CASCADE
);

CREATE INDEX hosted_threads_replies_cleanup
ON hosted_threads_replies (delete_after, reply_id);

CREATE UNIQUE INDEX hosted_threads_publications_account_publication
ON hosted_threads_publications (account_id, publication_id);

CREATE TRIGGER hosted_threads_profiles_bound_delete
BEFORE DELETE ON hosted_threads_profiles
WHEN EXISTS (
         SELECT 1 FROM hosted_workspace_accounts
         WHERE account_id = OLD.account_id AND default_threads_profile_id = OLD.profile_id
     )
     OR EXISTS (
         SELECT 1 FROM hosted_workspace_candidates
         WHERE account_id = OLD.account_id AND threads_profile_id = OLD.profile_id
     )
     OR EXISTS (
         SELECT 1 FROM hosted_threads_publications
         WHERE account_id = OLD.account_id AND profile_id = OLD.profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'bound Threads profile must be disconnected instead of deleted');
END;

CREATE TRIGGER hosted_threads_accounts_default_profile_update
BEFORE UPDATE OF default_threads_profile_id ON hosted_workspace_accounts
WHEN NEW.default_threads_profile_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = NEW.account_id AND profile_id = NEW.default_threads_profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'default Threads profile must belong to the account');
END;

CREATE TRIGGER hosted_threads_accounts_default_profile_insert
BEFORE INSERT ON hosted_workspace_accounts
WHEN NEW.default_threads_profile_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = NEW.account_id AND profile_id = NEW.default_threads_profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'default Threads profile must belong to the account');
END;

CREATE TRIGGER hosted_threads_candidates_profile_insert
BEFORE INSERT ON hosted_workspace_candidates
WHEN NEW.threads_profile_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = NEW.account_id AND profile_id = NEW.threads_profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'candidate Threads profile must belong to the account');
END;

CREATE TRIGGER hosted_threads_candidates_profile_update
BEFORE UPDATE OF threads_profile_id, account_id ON hosted_workspace_candidates
WHEN NEW.threads_profile_id IS NOT NULL
     AND NOT EXISTS (
         SELECT 1 FROM hosted_threads_profiles
         WHERE account_id = NEW.account_id AND profile_id = NEW.threads_profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'candidate Threads profile must belong to the account');
END;

CREATE TRIGGER hosted_threads_candidates_account_update
BEFORE UPDATE OF account_id ON hosted_workspace_candidates
WHEN EXISTS (
    SELECT 1 FROM hosted_threads_publications
    WHERE candidate_id = OLD.candidate_id AND account_id != NEW.account_id
)
BEGIN
    SELECT RAISE(ABORT, 'publication candidate must remain in the account');
END;

CREATE TRIGGER hosted_threads_publications_candidate_account_insert
BEFORE INSERT ON hosted_threads_publications
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_workspace_candidates
    WHERE account_id = NEW.account_id AND candidate_id = NEW.candidate_id
)
BEGIN
    SELECT RAISE(ABORT, 'publication candidate must belong to the account');
END;

CREATE TRIGGER hosted_threads_publications_binding_update
BEFORE UPDATE OF account_id, candidate_id, candidate_revision, profile_id
ON hosted_threads_publications
WHEN NEW.account_id != OLD.account_id
     OR NEW.candidate_id != OLD.candidate_id
     OR NEW.candidate_revision != OLD.candidate_revision
     OR NEW.profile_id != OLD.profile_id
BEGIN
    SELECT RAISE(ABORT, 'publication account, candidate revision, and profile are immutable');
END;

CREATE TRIGGER hosted_threads_publications_candidate_account_update
BEFORE UPDATE OF account_id, candidate_id ON hosted_threads_publications
WHEN NOT EXISTS (
    SELECT 1 FROM hosted_workspace_candidates
    WHERE account_id = NEW.account_id AND candidate_id = NEW.candidate_id
)
BEGIN
    SELECT RAISE(ABORT, 'publication candidate must belong to the account');
END;

CREATE TRIGGER hosted_threads_profiles_identity_update
BEFORE UPDATE OF account_id, profile_id ON hosted_threads_profiles
WHEN EXISTS (
         SELECT 1 FROM hosted_workspace_accounts
         WHERE account_id = OLD.account_id AND default_threads_profile_id = OLD.profile_id
     )
     OR EXISTS (
         SELECT 1 FROM hosted_workspace_candidates
         WHERE account_id = OLD.account_id AND threads_profile_id = OLD.profile_id
     )
     OR EXISTS (
         SELECT 1 FROM hosted_threads_publications
         WHERE account_id = OLD.account_id AND profile_id = OLD.profile_id
     )
BEGIN
    SELECT RAISE(ABORT, 'bound Threads profile must remain in the account');
END;
