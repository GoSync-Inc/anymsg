-- Preserve historical webjs observations and accept source-bound native evidence.
-- No identity, message ID, provenance, or existing observation is rewritten.
ALTER TABLE whatsapp_identity_observations
    DROP CONSTRAINT whatsapp_identity_observations_provenance_check;
ALTER TABLE whatsapp_identity_observations
    ADD CONSTRAINT whatsapp_identity_observations_provenance_check
    CHECK(provenance IN (
        'webjs_contact_cache_v1',
        'whatsmeow_message_alt_v1',
        'whatsmeow_lid_store_v1'
    ));
