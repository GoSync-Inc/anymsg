-- Provider attempts are independent of local disk admission. Reservations are
-- conservative full-transfer upper bounds, not a second blob residency catalog.
CREATE TABLE media_reservations (
    job text PRIMARY KEY REFERENCES media_jobs(id) ON DELETE CASCADE,
    volume_key text NOT NULL CHECK(length(volume_key)<=128),
    lease_epoch bigint NOT NULL CHECK(lease_epoch>0),
    reserved_bytes bigint NOT NULL CHECK(reserved_bytes>=0),
    expires_at timestamptz NOT NULL
);
CREATE INDEX media_reservation_volume ON media_reservations(volume_key);
