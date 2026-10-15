-- Operational state is independent from archive receipts/conflict diagnostics.
-- A newly installed row cannot reconstruct an unrecorded pre-upgrade FloodWait.
CREATE TABLE collector_health (
    account text PRIMARY KEY REFERENCES accounts(id),
    status text NOT NULL DEFAULT 'not_started'
        CHECK(status IN ('not_started','starting','receiving','degraded','stopped')),
    detail_code text NOT NULL DEFAULT 'not_checked' CHECK(detail_code ~ '^[a-z_]{1,64}$'),
    cooldown_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    discovery_started_at timestamptz,
    discovery_completed_at timestamptz,
    discovery_limited boolean NOT NULL DEFAULT false,
    discovery_count bigint NOT NULL DEFAULT 0 CHECK(discovery_count>=0)
);
