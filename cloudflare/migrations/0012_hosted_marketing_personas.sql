-- The persona layer the hosted control plane was missing.
--
-- `hosted_workspace_accounts` is the country's operating account: one row per country,
-- carrying posting times, time zone and the generation switch. A persona is a different
-- thing at a different cardinality — many people posting under one country — so it gets its
-- own table rather than columns bolted onto that one.
--
-- The shape mirrors the local `marketing_accounts` table on purpose. The browser shell is
-- one file serving both surfaces, so a persona has to read the same whether the row came
-- from the local SQLite file or from D1.
--
-- Purely additive: no existing table or row is touched.
CREATE TABLE IF NOT EXISTS hosted_marketing_personas (
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    country TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    schedule_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'observing', 'active', 'retired')),
    note TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (workspace_id, account_id)
);

CREATE INDEX IF NOT EXISTS hosted_marketing_personas_country
ON hosted_marketing_personas (workspace_id, country, created_at DESC);
