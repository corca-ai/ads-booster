-- The background asset pool: wallpapers a person curated, owned by a persona.
--
-- The web-search path chose a background per capture, and what it chose was nobody's
-- fault and nobody's decision: a query written by a model against an index of stock
-- photography. This table replaces that with images a human picked (seeds saved to a
-- Pinterest board) and images machine-expanded from those picks (related pins), the
-- latter gated by a yes/no review before anything reaches a capture.
--
-- One row is one image in one persona's pool. The same image saved for two personas is
-- two rows on purpose: approval, usage and rotation are per-persona decisions, and a
-- pool must never shrink because another persona rejected the picture.
--
-- Purely additive: no existing table or row is touched.
CREATE TABLE IF NOT EXISTS persona_background_assets (
    asset_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    persona_id TEXT NOT NULL,
    -- Where the image came from, kept verbatim. `source_url` is the page a person can
    -- open (the pin); `image_url` is the file that was fetched. If an asset ever has to
    -- be pulled, these are what say which one and why.
    source_url TEXT NOT NULL,
    image_url TEXT NOT NULL,
    r2_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    -- 'seed' rows were saved to the board by a person, which is already a review, so they
    -- enter approved. 'related' rows were expanded by a machine and enter pending.
    origin TEXT NOT NULL CHECK (origin IN ('seed', 'related')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    review_note TEXT,
    -- Rotation state: assignment picks the approved asset unused the longest, so a pool
    -- cycles fully before any image repeats.
    used_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- The same picture collected twice for the same persona is one asset. Related-pin
-- expansion revisits the same images constantly, and without this the pool would fill
-- with duplicates that rotation would then show back to back.
CREATE UNIQUE INDEX IF NOT EXISTS persona_background_assets_dedup
ON persona_background_assets (workspace_id, persona_id, sha256);

-- The review queue: pending assets for one persona, oldest first.
CREATE INDEX IF NOT EXISTS persona_background_assets_review
ON persona_background_assets (workspace_id, persona_id, status, created_at);

-- Assignment: the approved asset that has waited longest since last use.
CREATE INDEX IF NOT EXISTS persona_background_assets_rotation
ON persona_background_assets (workspace_id, persona_id, status, last_used_at);
