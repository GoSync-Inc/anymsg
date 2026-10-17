-- Encrypted cold copies of CAS originals in the private R2 bucket (ANYM-297).
-- Expand-only: deployed schema-34 code never reads this table. One row per
-- shared digest; only a read-back verified row authorizes managed eviction.
CREATE TABLE attachment_cold_copies (
 digest text PRIMARY KEY REFERENCES attachment_residency(digest) CHECK(digest ~ '^[a-f0-9]{64}$'),
 bytes bigint NOT NULL CHECK(bytes>=0),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','verified','failed')),
 object_key text NOT NULL CHECK(length(object_key)<=256
  AND object_key ~ '^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*/cold/[a-f0-9]{64}\.age$'
  AND right(object_key,74)='/cold/'||digest||'.age'),
 ciphertext_bytes bigint CHECK(ciphertext_bytes>=0),
 ciphertext_sha256 text CHECK(ciphertext_sha256 ~ '^[a-f0-9]{64}$'),
 recipient_fingerprints text[] NOT NULL DEFAULT '{}',
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 error_code text CHECK(error_code ~ '^[a-z_]{1,64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 verified_at timestamptz,
 -- Reserved for cold-first rehydration (ANYM-297 part 2); inert in schema 35.
 restore_state text NOT NULL DEFAULT 'idle' CHECK(restore_state IN ('idle','requested','failed')),
 restore_attachment text,
 restore_requested_at timestamptz,
 restore_finished_at timestamptz,
 restore_error text CHECK(restore_error ~ '^[a-z_]{1,64}$'),
 CHECK(state<>'verified' OR (ciphertext_bytes IS NOT NULL AND ciphertext_sha256 IS NOT NULL
  AND verified_at IS NOT NULL AND cardinality(recipient_fingerprints)=2
  AND array_position(recipient_fingerprints,NULL) IS NULL))
);
CREATE INDEX attachment_cold_copies_restore ON attachment_cold_copies(restore_requested_at)
 WHERE restore_state='requested';
