-- Caption generation becomes a Mac worker job, like image capture already is.
--
-- The hosted surface wrote its own captions with Workers AI and a prompt of its own, which
-- made two generators for one product: the local one reads the reference corpus, assigns a
-- domain and a caption form per candidate and samples reference bodies, and the hosted one
-- read none of that. Publishing the work to a worker instead means one generator, and the
-- table that already carries "published work waiting for a Mac" is this one.
--
-- `kind` is what tells the two jobs apart on the way out and on the way back. Every row that
-- exists today is a capture, which is exactly what the default says, so no existing row
-- changes and no existing query has to learn a new column to keep working.
ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN kind TEXT NOT NULL
    DEFAULT 'capture'
    CHECK (kind IN ('capture', 'generate_candidates'));

-- Which persona asked for the batch, so the candidates the callback writes land in that
-- persona's list rather than in the country-wide pool. NULL is the country-wide request,
-- which is what the scheduled generation and any pre-persona surface still send.
ALTER TABLE hosted_workspace_capture_tasks ADD COLUMN persona_id TEXT;

-- What the generator recorded about itself: the context documents it read with their byte
-- sizes, the reference bodies this one call was shown, the instruction length, the assigned
-- domain and the caption form. The four `generation_*` columns on the candidate row describe
-- a Workers AI prompt and cannot hold any of it, and flattening it would throw away exactly
-- the evidence "which references produce candidates worth approving" needs later.
ALTER TABLE hosted_workspace_candidates ADD COLUMN generation_provenance_json TEXT;

-- A generation task has no candidate: the candidates are what it is going to produce. The
-- column is NOT NULL and SQLite cannot relax that without rebuilding a live table, so these
-- rows carry the empty string, and every capture query already filters on a real candidate
-- id. `candidate_revision` carries 1 for the same reason.
CREATE INDEX IF NOT EXISTS hosted_workspace_capture_tasks_generation
ON hosted_workspace_capture_tasks (account_id, kind, state, created_at DESC);
