-- Which persona wrote a hosted candidate.
--
-- Until now every hosted candidate belonged to the country's operating account and to
-- nothing finer, so two personas under one country shared a single pool: 김도현's screen
-- listed 이서진's drafts, and either could delete the other's. Locally the same rows have
-- been persona-scoped since accounts existed; this closes the gap.
--
-- Nullable, and existing rows keep NULL. A row written before personas existed genuinely
-- has no persona, and inventing one would be a worse record than the absence.
ALTER TABLE hosted_workspace_candidates ADD COLUMN persona_id TEXT;

CREATE INDEX IF NOT EXISTS hosted_workspace_candidates_persona
ON hosted_workspace_candidates (account_id, persona_id, created_at DESC);
