-- Scoped attempt budgets and worker controls. No provider is enabled here.
-- The legacy enrichment_budget remains the shared daily hard ceiling.
CREATE TABLE processing_budget (
    day date NOT NULL,
    account text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('audio','image','document')),
    profile_hash text NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
    attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
    PRIMARY KEY(day,account,kind,profile_hash)
);
CREATE TABLE processing_control (
    account text NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK(kind IN ('audio','image','document')),
    profile_hash text NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
    pause_reason text,
    paused_until timestamptz,
    last_started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(account,kind,profile_hash),
    CHECK(pause_reason IS NOT NULL OR paused_until IS NULL)
);
CREATE TABLE processing_resource_leases (
    resource text PRIMARY KEY CHECK(resource='local-heavy'),
    lease_token uuid NOT NULL,
    worker_kind text NOT NULL CHECK(worker_kind IN ('enrichment','extraction')),
    attachment text NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    lease_until timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
ALTER TABLE processing_profiles ADD COLUMN pause_reason text;
ALTER TABLE processing_profiles ADD COLUMN paused_until timestamptz;
