-- Account operational observations are separate from archive coverage/conflicts.
ALTER TABLE collector_health ADD COLUMN operations jsonb NOT NULL DEFAULT '{}'
    CHECK(jsonb_typeof(operations)='object' AND octet_length(operations::text)<=16384);
CREATE TABLE processing_profiles (
    account text NOT NULL REFERENCES accounts(id),
    kind text NOT NULL CHECK(kind IN ('audio','image','document')),
    provider text NOT NULL CHECK(provider ~ '^[a-z_]{1,64}$'),
    model text NOT NULL CHECK(length(model)<=128),
    enabled boolean NOT NULL DEFAULT true,
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY(account,kind)
);
